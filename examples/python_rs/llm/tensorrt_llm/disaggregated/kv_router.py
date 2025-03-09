# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import copy
import json
import traceback
from enum import Enum
from typing import AsyncIterator

import uvloop
from common.base_engine import ChatProcessorMixin
from common.parser import LLMAPIConfig, parse_tensorrt_llm_args
from common.protocol import (
    DisaggChatCompletionRequest,
    DisaggChatCompletionStreamResponse,
    DisaggCompletionStreamResponse,
    RoutingStrategy,
    Tokens,
)
from tensorrt_llm.logger import logger
from tensorrt_llm.serve.openai_protocol import CompletionRequest, DisaggregatedParams
from transformers import AutoTokenizer

from dynamo.llm import KvRouter
from dynamo.runtime import DistributedRuntime, dynamo_endpoint, dynamo_worker

logger.set_level("debug")


class EndpointType(Enum):
    COMPLETIONS = "completions"
    CHAT = "chat"


class KVRouterWrapper:
    def __init__(self, kv_router: KvRouter, routing_strategy: RoutingStrategy):
        self.kv_router = kv_router
        self.routing_strategy = routing_strategy

    @dynamo_endpoint(Tokens, str)
    async def generate(self, request) -> AsyncIterator[str]:
        lora_id = 0
        worker_id = None
        if self.routing_strategy == RoutingStrategy.PREFIX:
            try:
                worker_id = await self.kv_router.schedule(request.tokens, lora_id)
            except Exception:
                logger.warning(
                    f"Error during worker selection: {traceback.format_exc()}"
                )
                worker_id = ""

            logger.info(f"Scheduling to worker_id: {worker_id}")

            yield str(worker_id)

        else:
            raise NotImplementedError(
                f"Routing strategy {self.routing_strategy} not implemented"
            )


class Router(ChatProcessorMixin):
    def __init__(
        self,
        ctx_chat_client,
        gen_chat_client,
        ctx_completion_client,
        gen_completion_client,
        kv_router_wrapper: KVRouterWrapper,
        engine_config: LLMAPIConfig,
    ):
        self.ctx_chat_client = ctx_chat_client
        self.gen_chat_client = gen_chat_client
        self.ctx_completion_client = ctx_completion_client
        self.gen_completion_client = gen_completion_client
        self.kv_router_wrapper = kv_router_wrapper

        self.engine_config = engine_config
        if self.engine_config.model_path:
            self.model = self.engine_config.model_path
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.engine_config.model_path
            )
            logger.info(f"Using model from path: {self.engine_config.model_path}")
        else:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.engine_config.model_name
            )
        logger.info("INITIALIZED ROUTER")

        # allows to use tokenizer
        super().__init__(engine_config)

    async def _get_ctx_resp(self, request, ctx_client, type):
        logger.debug(f"Received request {request}")

        # NOTE: this will increase TTFT since we are encoding the prompt here
        # prompt is also encoded in the worker.
        # TODO: we need to implement our own request processing and protocols to send only token ids to llmapi worker.
        token_ids = self.tokenizer.encode(request.prompt)
        worker_id_generator: AsyncIterator = self.kv_router_wrapper.generate(
            Tokens(tokens=token_ids).model_dump_json()
        )

        worker_id = (
            await worker_id_generator.__anext__()
        )  # only one worker id is returned

        request.max_tokens = 1
        request.disaggregated_params = DisaggregatedParams(request_type="context_only")
        logger.debug(f"[router] Sending request to context server: {request}")

        if worker_id == "":
            ctx_resp = [
                resp
                async for resp in await ctx_client.random(request.model_dump_json())
            ]
        else:
            ctx_resp = [
                resp
                async for resp in await ctx_client.direct(
                    request.model_dump_json(), int(worker_id)
                )
            ]

        if len(ctx_resp) > 1:
            raise ValueError(
                "Context server returned more than one response. This is currently not supported in disaggregated server."
            )
        logger.debug(
            f"[router] received response from context server: {ctx_resp[0].data()}"
        )
        return ctx_resp[0].data()

    # TODO (shreyasm): The only reason we cant further combine the two methods below is
    # because the disagg params are in different locations.
    # Disagg params should be in under the choices field in the response object.
    # This is the case for completions but not for chat.

    @dynamo_endpoint(CompletionRequest, DisaggCompletionStreamResponse)
    async def generate_completion(self, request):
        # These settings are needed to satisfy request checks.
        request.skip_special_tokens = False
        request.add_special_tokens = False
        request.spaces_between_special_tokens = False

        gen_req = copy.deepcopy(request)

        ctx_resp = await self._get_ctx_resp(
            request, self.ctx_completion_client, EndpointType.COMPLETIONS
        )
        ctx_resp_obj = DisaggCompletionStreamResponse.model_validate(ctx_resp)

        gen_req.disaggregated_params = DisaggregatedParams.model_validate(
            ctx_resp_obj.choices[0].disaggregated_params
        )
        gen_req.disaggregated_params.request_type = "generation_only"

        if request.stream:
            yield json.loads(
                ctx_resp_obj.model_dump_json(
                    exclude_unset=True, exclude={"disaggregated_params"}
                )
            )

        logger.debug(f"[router] Sending request to generation server: {gen_req}")
        async for response in await self.gen_completion_client.round_robin(
            gen_req.model_dump_json()
        ):
            gen_resp_obj = DisaggCompletionStreamResponse.model_validate(
                response.data()
            )
            yield json.loads(gen_resp_obj.model_dump_json(exclude_unset=True))

    @dynamo_endpoint(DisaggChatCompletionRequest, DisaggChatCompletionStreamResponse)
    async def generate_chat(self, request):
        # These settings are needed to satisfy request checks.
        request.skip_special_tokens = False
        request.add_special_tokens = False
        request.spaces_between_special_tokens = False

        gen_req = copy.deepcopy(request)

        ctx_resp = await self._get_ctx_resp(
            request, self.ctx_chat_client, EndpointType.CHAT
        )
        ctx_resp_obj = DisaggChatCompletionStreamResponse.model_validate_json(ctx_resp)

        gen_req.disaggregated_params = DisaggregatedParams.model_validate(
            ctx_resp_obj.disaggregated_params
        )
        gen_req.disaggregated_params.request_type = "generation_only"

        if request.stream:
            yield json.loads(
                ctx_resp_obj.model_dump_json(
                    exclude_unset=True, exclude={"disaggregated_params"}
                )
            )

        logger.debug(f"[router] Sending request to generation server: {gen_req}")
        async for response in await self.gen_chat_client.round_robin(
            gen_req.model_dump_json()
        ):
            gen_resp_obj = DisaggChatCompletionStreamResponse.model_validate(
                response.data()
            )
            yield json.loads(gen_resp_obj.model_dump_json(exclude_unset=True))


@dynamo_worker()
async def worker(runtime: DistributedRuntime, args, engine_config):
    """
    Instantiate a `backend` component and serve the `generate` endpoint
    A `Component` can serve multiple endpoints
    """
    component = runtime.namespace("dynamo").component("router")
    await component.create_service()

    ctx_completion_client = (
        await runtime.namespace("dynamo")
        .component("tensorrt-llm-ctx")
        .endpoint("completions")
        .client()
    )
    gen_completion_client = (
        await runtime.namespace("dynamo")
        .component("tensorrt-llm-gen")
        .endpoint("completions")
        .client()
    )
    ctx_chat_client = (
        await runtime.namespace("dynamo")
        .component("tensorrt-llm-ctx")
        .endpoint("chat/completions")
        .client()
    )
    gen_chat_client = (
        await runtime.namespace("dynamo")
        .component("tensorrt-llm-gen")
        .endpoint("chat/completions")
        .client()
    )

    # Only listen to context server for now
    kv_listener = runtime.namespace("dynamo").component("tensorrt-llm-ctx")
    await kv_listener.create_service()

    kv_router = KvRouter(runtime, kv_listener)

    completions_endpoint = component.endpoint("completions")
    # chat_endpoint = component.endpoint("chat/completions")

    kv_router_wrapper = KVRouterWrapper(kv_router, args.routing_strategy)
    router = Router(
        ctx_chat_client,
        gen_chat_client,
        ctx_completion_client,
        gen_completion_client,
        kv_router_wrapper,
        engine_config,
    )

    await asyncio.gather(
        completions_endpoint.serve_endpoint(router.generate_completion),
        # chat_endpoint.serve_endpoint(router.generate_chat),
    )


if __name__ == "__main__":
    uvloop.install()
    args, engine_config = parse_tensorrt_llm_args()

    asyncio.run(worker(args, engine_config))
