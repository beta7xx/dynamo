// SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::sync::{Arc, Mutex};

pub use crate::kv_router::protocols::ForwardPassMetrics;

use crate::kv_router::scheduler::{Endpoint, Service};
use crate::kv_router::ProcessedEndpoints;
use dynemo_runtime::component::Component;
use std::time::Duration;
use tokio_util::sync::CancellationToken;

pub struct KvMetricsAggregator {
    pub service_name: String,
    pub endpoints: Arc<Mutex<ProcessedEndpoints>>,
}

impl KvMetricsAggregator {
    pub async fn new(component: Component, cancellation_token: CancellationToken) -> Self {
        let (ep_tx, mut ep_rx) = tokio::sync::mpsc::channel(128);

        tokio::spawn(collect_endpoints(
            component.drt().nats_client().clone(),
            component.service_name(),
            ep_tx,
            cancellation_token.clone(),
        ));

        tracing::trace!("awaiting the start of the background endpoint subscriber");
        let endpoints = Arc::new(Mutex::new(ProcessedEndpoints::default()));
        let endpoints_clone = endpoints.clone();
        tokio::spawn(async move {
            tracing::debug!("scheduler background task started");
            loop {
                match ep_rx.recv().await {
                    Some(endpoints) => match endpoints_clone.lock() {
                        Ok(mut shared_endpoint) => {
                            *shared_endpoint = endpoints;
                        }
                        Err(e) => {
                            tracing::error!("Failed to acquire lock on endpoints: {:?}", e);
                        }
                    },
                    None => {
                        tracing::warn!("endpoint subscriber shutdown");
                        break;
                    }
                };
            }

            tracing::trace!("background endpoint subscriber shutting down");
        });
        Self {
            service_name: component.service_name(),
            endpoints,
        }
    }

    pub fn get_endpoints(&self) -> ProcessedEndpoints {
        match self.endpoints.lock() {
            Ok(endpoints) => endpoints.clone(),
            Err(e) => {
                tracing::error!("Failed to acquire lock on endpoints: {:?}", e);
                ProcessedEndpoints::default()
            }
        }
    }
}

async fn collect_endpoints(
    nats_client: dynemo_runtime::transports::nats::Client,
    service_name: String,
    ep_tx: tokio::sync::mpsc::Sender<ProcessedEndpoints>,
    cancel: CancellationToken,
) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                tracing::debug!("cancellation token triggered");
                break;
            }
            _ = tokio::time::sleep(Duration::from_secs(1)) => {
                tracing::trace!("collecting endpoints for service: {}", service_name);
            }
        }

        let values = match nats_client
            .get_endpoints(&service_name, Duration::from_millis(100))
            .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("Failed to retrieve endpoints for {}: {:?}", service_name, e);
                continue;
            }
        };

        tracing::debug!("values: {:?}", values);
        let services: Vec<Service> = values
            .into_iter()
            .filter(|v| !v.is_empty())
            .filter_map(|v| match serde_json::from_slice::<Service>(&v) {
                Ok(service) => Some(service),
                Err(e) => {
                    tracing::warn!("For value: {:?} \nFailed to parse service: {:?}", v, e);
                    None
                }
            })
            .collect();
        tracing::debug!("services: {:?}", services);

        let endpoints: Vec<Endpoint> = services
            .into_iter()
            .flat_map(|s| s.endpoints)
            .filter(|s| s.data.is_some())
            .map(|s| Endpoint {
                name: s.name,
                subject: s.subject,
                data: s.data.unwrap(),
            })
            .collect();
        tracing::debug!("endpoints: {:?}", endpoints);

        tracing::trace!(
            "found {} endpoints for service: {}",
            endpoints.len(),
            service_name
        );

        let processed = ProcessedEndpoints::new(endpoints);
        if ep_tx.send(processed).await.is_err() {
            tracing::trace!("failed to send processed endpoints; shutting down");
            break;
        }
    }
}
