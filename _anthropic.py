"""Interface for chatting with Anthropic's Claude models using Anthropic's API."""

import asyncio
import logging
import os
import random

import anthropic
from aiolimiter import AsyncLimiter

MAX_RETRIES = 3
MAX_TOKENS = 1000

PROMPT_TOKENS = 0
RESPONSE_TOKENS = 0

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

################################################################################
# Chat


# pylint: disable=too-many-arguments
# pylint: disable=too-many-locals
async def chat(
    model: str,
    system: str,
    prompt: str,
    limiter: AsyncLimiter,
    pool: asyncio.Semaphore,
    temperature: float = 1.0,
    json_start: str = "",
    # pylint: disable=unused-argument
    **kwargs,
    # pylint: enable=unused-argument
) -> str:
    """Chat with the Claude model."""

    # Calculate the number of tokens in the input
    tokens = (len(system) + len(prompt) + 4) // 5

    # Create the messages
    messages = [
        {"role": "user", "content": prompt},
    ]
    if json_start:
        messages.append({"role": "assistant", "content": json_start})

    # Create the request
    jitter_factor = 0.8
    wait_time = random.uniform(1 / jitter_factor, jitter_factor)
    retries = 0
    async with pool:
        while retries < MAX_RETRIES:
            await limiter.acquire(tokens)
            try:
                # Send the request
                response = await client.messages.create(
                    model=model,
                    messages=messages,
                    system=system,
                    temperature=temperature,
                    max_tokens=MAX_TOKENS,
                )

                if response:
                    break

            except anthropic.APIError as error:
                logging.error("Error in Anthropic request: %s", error)
                retries += 1
                await asyncio.sleep(wait_time)
                wait_time *= 2

            except Exception as e:
                raise RuntimeError(f"Failed to connect to {model}: {e}") from e

        else:
            raise RuntimeError(f"Failed to connect to {model} after {retries} retries")

    # Calculate the number of tokens in the response
    global PROMPT_TOKENS, RESPONSE_TOKENS
    PROMPT_TOKENS += response.usage.input_tokens
    RESPONSE_TOKENS += response.usage.output_tokens

    return json_start + response.content[0].text
