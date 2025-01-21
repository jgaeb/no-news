"""Interface for chatting with Anthropic's Claude models using AWS Bedrock."""

import asyncio
import json
import logging
import random

from aiolimiter import AsyncLimiter
from botocore.exceptions import ClientError  # type: ignore

from _connpool import AWSClientConnectionPool

MAX_RETRIES = 3
MAX_TOKENS = 1000

PROMPT_TOKENS = 0
RESPONSE_TOKENS = 0

################################################################################
# Chat


def format_payload(
    system: str, prompt: str, temperature: float, json_start: str = ""
) -> dict:
    """Format the payload for the Claude model."""
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        ],
    }

    if json_start:
        payload["messages"].append(  # type: ignore
            {"role": "assistant", "content": [{"type": "text", "text": json_start}]}
        )

    return payload


# pylint: disable=too-many-arguments
# pylint: disable=too-many-locals
async def chat(
    model: str,
    system: str,
    prompt: str,
    limiter: AsyncLimiter,
    pool: AWSClientConnectionPool,
    temperature: float = 1.0,
    json_start: str = "",
    # pylint: disable=unused-argument
    **kwargs,
    # pylint: enable=unused-argument
) -> str:
    """Chat with the Claude model."""

    # Format the payload
    payload = format_payload(system, prompt, temperature, json_start)

    # Calculate the number of tokens in the input
    tokens = (len(system) + len(prompt) + len(json_start) + 4) // 5

    # Create the client and request
    jitter_factor = 0.8
    wait_time = random.uniform(1 / jitter_factor, jitter_factor)
    retries = 0
    async with pool.client() as client:
        while retries < MAX_RETRIES:
            await limiter.acquire(tokens)
            try:
                # Send the request
                body = json.dumps(payload)
                logging.debug("Request to Claude model %s: %s", model, body)
                raw_response = await client.invoke_model(body=body, modelId=model)

                # Read the response as a string
                async with raw_response["body"] as stream:
                    response = await stream.read()

                break

            except ClientError as e:
                logging.warning("Error in AWS request: %s", e)
                retries += 1
                await asyncio.sleep(wait_time)
                wait_time *= 2

            except Exception as e:
                raise RuntimeError(f"Failed to connect to {model}: {e}") from e

        else:
            raise RuntimeError(f"Failed to connect to {model} after {retries} retries")

    # Parse the response
    try:
        response = json.loads(response)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ill-formatted Claude response: {e}") from e

    # Calculate the number of tokens in the response
    try:
        global PROMPT_TOKENS, RESPONSE_TOKENS
        PROMPT_TOKENS += response["usage"]["input_tokens"]
        RESPONSE_TOKENS += response["usage"]["output_tokens"]
    except KeyError:
        logging.warning("Token usage not found in response: %s", response)

    contents = response.get("content", [])
    return (
        json_start
        + " ".join(
            [
                content.get("text", "")
                for content in contents
                if content.get("type", "") == "text"
            ]
        ).strip()
    )


# pylint: enable=too-many-arguments
# pylint: enable=too-many-locals
