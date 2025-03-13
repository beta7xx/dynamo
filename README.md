<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Dynamo

<h4> A Datacenter Scale Distributed Inference Serving Framework </h4>

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![GitHub Release](https://img.shields.io/github/v/release/ai-dynamo/dynamo)](https://github.com/ai-dynamo/dynamo/releases/latest)


Dynamo is a flexible, component based, data center scale inference
serving framework designed to meet the demands of complex use cases
including those of Generative AI. It is designed to enable developers
to implement and customize routing, load balancing, scaling and
workflow definitions at the data center scale without sacrificing
performance or ease of use.

> [!NOTE]
> This project is currently in the alpha / experimental /
> rapid-prototyping stage and we are actively looking for feedback and
> collaborators.

## Building Dynamo

### Requirements
Dynamo development and examples are container based.

* [Docker](https://docs.docker.com/get-started/get-docker/)
* [buildx](https://github.com/docker/buildx)

### Development

You can build the Dynamo container using the build scripts
in `container/` (or directly with `docker build`).

We provide 2 types of environments:

2. `TENSORRTLLM` which includes our TRT-LLM backend
3. `VLLM` which includes our VLLM backend using NIXL communication library.

For example, if you want to build a container with `VLLM` support you can use the default:

<!--pytest.mark.skip-->
```bash
./container/build.sh
```

Please see the instructions in the corresponding example for specific build instructions.

## Running Dynamo for Local Testing and Development

You can run the Dynamo container using the run scripts in
`container/` (or directly with `docker run`).

The run script offers a few common workflows:

1. Running a command in a container and exiting.

<!--pytest.mark.skip-->
```bash
./container/run.sh -- python3 -c "import dynamo.runtime; help(dynamo.runtime)"
```
<!--

# This tests the above the line but from within the container
# using pytest-codeblocks

```bash
python3 -c "import dynamo.runtime; help(dynamo.runtime)"
```
-- >

2. Starting an interactive shell.

<!--pytest.mark.skip-->
```bash
./container/run.sh -it
```

3. Mounting the local workspace and Starting an interactive shell.

<!--pytest.mark.skip-->
```bash
./container/run.sh -it --mount-workspace
```

The last command also passes common environment variables ( `-e
HF_TOKEN` ) and mounts common directories such as `/tmp:/tmp`,
`/mnt:/mnt`.

Please see the instructions in the corresponding example for specific
deployment instructions.

## LLM Serving Quick Start

You can quickly spin up a local model for quick testing in the development container.

<!--pytest.mark.skip-->
```bash
./container/run.sh -it

# Download Model

# Run

dynamo run deepseek-ai/DeepSeek-R1-Distill-Llama-8B
```
<!

## Tests above command line
```bash
echo "Testing Dynamo Run"

echo "hello" | timeout 60s dynamo run deepseek-ai/DeepSeek-R1-Distill-Llama-8B >out.txt 2>&1
grep -q "Hello" out.txt
```
->

#### Example Output

```
INFO 03-12 17:38:27 __init__.py:190] Automatically detected platform cuda.
INFO 03-12 17:38:27 nixl.py:16] NIXL is available
? User › how are you doing today?
✔ User · how are you doing today?

<think>
...
In summary, after considering all these factors, I think the best response is a positive, open-ended statement that invites the other person to share. So, "I'm doing well, thank you. How about you?" seems like the most appropriate and friendly way to respond.
</think>

I'm doing well, thank you. How about you?
```

## Rust Based Runtime

Dynamo has a new rust based distributed runtime with
implementation under development. The rust based runtime enables
serving arbitrary python code as well as native rust. Please note the
APIs are subject to change.

### Hello World

[Hello World](./lib/bindings/python/examples/hello_world)

A basic example demonstrating the rust based runtime and python
bindings.

### LLM

[VLLM](./examples/python_rs/llm/vllm)

An intermediate example expanding further on the concepts introduced
in the Hello World example. In this example, we demonstrate
[Disaggregated Serving](https://arxiv.org/abs/2401.09670) as an
application of the components defined in Dynamo.

# Disclaimers

> [!NOTE]
> This project is currently in the alpha / experimental /
> rapid-prototyping stage and we will be adding new features incrementally.

1. The `TENSORRTLLM` and `VLLM` containers are WIP and not expected to
   work out of the box.

2. Testing has primarily been on single node systems with processes
   launched within a single container.
