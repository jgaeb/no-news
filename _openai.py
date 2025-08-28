"""Interface for chatting with OpenAI's GPT models."""

import asyncio
import logging
import os
import random
from functools import lru_cache

import openai
import tiktoken  # type: ignore
from aiolimiter import AsyncLimiter

MAX_RETRIES = 3
MAX_TOKENS = 1000

PROMPT_TOKENS = 0
RESPONSE_TOKENS = 0

# Initialize the OpenAI client
client = openai.AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"], organization=os.environ["OPENAI_API_ORG"]
)

################################################################################
# Chat


@lru_cache(maxsize=1)
def get_encoding(model: str) -> tiktoken.Encoding:
    """Get the encoding for the GPT model."""
    return tiktoken.encoding_for_model(model)


# pylint: disable=too-many-arguments
# pylint: disable=too-many-locals
async def chat(
    model: str,
    system: str,
    prompt: str,
    limiter: AsyncLimiter,
    pool: asyncio.Semaphore,
    temperature: float = 1.0,
    # pylint: disable=unused-argument
    **kwargs,
    # pylint: enable=unused-argument
) -> str:
    """Chat with the GPT model."""

    # Calculate the number of tokens in the input
    encoding = get_encoding(model)
    tokens = len(encoding.encode(system)) + len(encoding.encode(prompt))

    # Create the request
    jitter_factor = 0.8
    wait_time = random.uniform(1 / jitter_factor, jitter_factor)
    retries = 0
    async with pool:
        while retries < MAX_RETRIES:
            await limiter.acquire(tokens)
            try:
                # Send the request
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "system", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=MAX_TOKENS,
                )

                if response is not None:
                    break

            except openai.OpenAIError as e:
                logging.error("Error in OpenAI request: %s", e)
                retries += 1
                await asyncio.sleep(wait_time)
                wait_time *= 2

            except Exception as e:
                raise RuntimeError(f"Failed to connect to {model}: {e}") from e

        else:
            raise RuntimeError(f"Failed to connect to {model} after {retries} retries")

    # Calculate the number of tokens in the response
    try:
        prompt_tokens = response.usage.prompt_tokens  # type: ignore
        completion_tokens = response.usage.completion_tokens  # type: ignore
        # pylint: enable=no-member
        if not isinstance(prompt_tokens, int):
            prompt_tokens = 0
            logging.warning("Prompt tokens not found in response %s", response)
        if not isinstance(completion_tokens, int):
            completion_tokens = 0
            logging.warning("Completion tokens not found in response %s", response)
        # pylint: disable=global-statement
        global PROMPT_TOKENS, RESPONSE_TOKENS
        # pylint: enable=global-statement
        PROMPT_TOKENS += response.usage.prompt_tokens  # type: ignore
        RESPONSE_TOKENS += response.usage.completion_tokens  # type: ignore
    except AttributeError:
        logging.warning("Failed to get token usage from response")

    return response.choices[0].message.content  # type: ignore


# pylint: enable=too-many-arguments
# pylint: enable=too-many-locals
