"""Convenience interface for generating the context needed to run a model."""

import logging
from asyncio import Semaphore

from aiolimiter import AsyncLimiter

import _anthropic
import _aws
import _openai
from _connpool import AWSClientConnectionPool

MODELS = {
    "OpenAI": {
        "gpt-4": "gpt-4-turbo-2024-04-09",
        "gpt-3.5": "gpt-3.5-turbo-0125",
        "ft-events-1": "ft:gpt-3.5-turbo-0125:computational-policy-lab:events:9A33J3DL",
        "ft-events-2": "ft:gpt-3.5-turbo-0125:computational-policy-lab:events2:9A6G5JOs",
        "ft-events-3": "ft:gpt-3.5-turbo-0125:computational-policy-lab:events3:9AAUeVtD",
        "ft-classify-1": "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify:9AWOhGqH",
        "ft-classify-2": "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify2:9Aazgks8",
        "ft-classify-3": "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify3:9AdamZ7x",
    },
    "AWS": {
        "sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
        "haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    },
    "Anthropic": {
        "opus": "claude-3-opus-20240229",
        "sonnet": "claude-3-sonnet-20240229",
        "haiku": "claude-3-haiku-20240307",
    },
}

################################################################################
# Rate limiters

gpt_3_5_turbo_rl = AsyncLimiter(500, 3)

RATE_LIMITERS = {
    "OpenAI": {
        "gpt-4": AsyncLimiter(250, 3),
        "gpt-3.5": gpt_3_5_turbo_rl,
        "ft-events-1": gpt_3_5_turbo_rl,
        "ft-events-2": gpt_3_5_turbo_rl,
        "ft-events-3": gpt_3_5_turbo_rl,
        "ft-classify-1": gpt_3_5_turbo_rl,
        "ft-classify-2": gpt_3_5_turbo_rl,
        "ft-classify-3": gpt_3_5_turbo_rl,
    },
    "AWS": {
        "sonnet": AsyncLimiter(50, 6),
        "haiku": AsyncLimiter(100, 6),
    },
    "Anthropic": {
        "opus": AsyncLimiter(200, 6),
        "sonnet": AsyncLimiter(200, 6),
        "haiku": AsyncLimiter(200, 6),
    },
}

################################################################################
# Token limiters

gpt_3_5_turbo = AsyncLimiter(500_000, 15)

TOKEN_LIMITERS = {
    "OpenAI": {
        "gpt-4": AsyncLimiter(150_000, 15),
        "gpt-3.5": gpt_3_5_turbo,
        "ft-events-1": gpt_3_5_turbo,
        "ft-events-2": gpt_3_5_turbo,
        "ft-events-3": gpt_3_5_turbo,
        "ft-classify-1": gpt_3_5_turbo,
        "ft-classify-2": gpt_3_5_turbo,
        "ft-classify-3": gpt_3_5_turbo,
    },
    "AWS": {
        "sonnet": AsyncLimiter(100_000, 6),
        "haiku": AsyncLimiter(200_000, 6),
    },
    "Anthropic": {
        "opus": AsyncLimiter(200_000),
        "sonnet": AsyncLimiter(160_000),
        "haiku": AsyncLimiter(80_000),
    },
}

################################################################################
# Connection limiters

CONNECTION_LIMITERS = {
    "AWS": AWSClientConnectionPool(maxsize=100),
    "OpenAI": Semaphore(100),
    "Anthropic": Semaphore(100),
}

################################################################################
# Cost calculators (ppm)

MODEL_COSTS = {
    "OpenAI": {
        "gpt-4": {
            "prompt": 10,
            "response": 30,
        },
        "gpt-3.5": {
            "prompt": 0.5,
            "response": 1.5,
        },
        "ft-events-1": {
            "prompt": 3,
            "response": 6,
        },
        "ft-events-2": {
            "prompt": 3,
            "response": 6,
        },
        "ft-events-3": {
            "prompt": 3,
            "response": 6,
        },
        "ft-classify-1": {
            "prompt": 3,
            "response": 6,
        },
        "ft-classify-2": {
            "prompt": 3,
            "response": 6,
        },
        "ft-classify-3": {
            "prompt": 3,
            "response": 6,
        },
    },
    "AWS": {
        "sonnet": {
            "prompt": 3,
            "response": 15,
        },
        "haiku": {
            "prompt": 0.25,
            "response": 1.25,
        },
    },
    "Anthropic": {
        "opus": {"prompt": 15, "response": 75},
        "sonnet": {
            "prompt": 3,
            "response": 15,
        },
        "haiku": {
            "prompt": 0.25,
            "response": 1.25,
        },
    },
}


def calculate_cost(service, model):
    """Calculate the cost of running a model."""

    if service not in MODELS:
        raise ValueError(f"Unknown service: {service}")

    if service == "OpenAI":
        prompt_tokens = _openai.PROMPT_TOKENS
        response_tokens = _openai.RESPONSE_TOKENS
    elif service == "AWS":
        prompt_tokens = _aws.PROMPT_TOKENS
        response_tokens = _aws.RESPONSE_TOKENS
    elif service == "Anthropic":
        prompt_tokens = _anthropic.PROMPT_TOKENS
        response_tokens = _anthropic.RESPONSE_TOKENS
    else:
        raise ValueError(f"This code should be unreachable: {service}")

    # Calculate the cost
    cost = (
        MODEL_COSTS[service][model]["prompt"] * prompt_tokens
        + MODEL_COSTS[service][model]["response"] * response_tokens
    ) / 1_000_000

    return (
        f"prompt_tokens: {prompt_tokens}\t"
        f"response_tokens: {response_tokens}\t"
        f"cost: {cost:.2f}"
    )


################################################################################
# Model context


class ModelContext:
    """Async context manager for running a model."""

    def __init__(self, service: str, model: str):
        if service not in MODELS:
            raise ValueError(f"Unknown service: {service}")
        if model not in MODELS[service]:
            raise ValueError(f"Unknown model: {model}")

        self.service: str = service
        self.model: str = model
        self.full_model: str = MODELS[service][model]
        self.rate_limiter = RATE_LIMITERS[self.service][self.model]
        self.token_limiter = TOKEN_LIMITERS[self.service][self.model]
        self.connection_limiter = CONNECTION_LIMITERS[self.service]

    async def initialize(self):
        if self.service == "AWS":
            await self.connection_limiter.initialize()

    async def close(self):
        if self.service == "AWS":
            await self.connection_limiter.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type: Exception, exc_value: str, traceback: str):
        return False

    async def chat(
        self,
        system: str,
        prompt: str,
        temperature: float = 1.0,
        json_start: str = "",
        # pylint: disable=unused-argument
        **kwargs,
        # pylint: enable=unused-argument
    ) -> str:
        async with self.rate_limiter:
            if self.service == "OpenAI":
                if json_start:
                    logging.warning(
                        "json_start not supported for OpenAI: %s", json_start
                    )
                return await _openai.chat(
                    model=self.full_model,
                    system=system,
                    prompt=prompt,
                    limiter=self.token_limiter,
                    pool=self.connection_limiter,
                    temperature=temperature,
                    **kwargs,
                )
            if self.service == "AWS":
                return await _aws.chat(
                    model=self.full_model,
                    system=system,
                    prompt=prompt,
                    limiter=self.token_limiter,
                    pool=self.connection_limiter,
                    temperature=temperature,
                    json_start=json_start,
                    **kwargs,
                )
            if self.service == "Anthropic":
                return await _anthropic.chat(
                    model=self.full_model,
                    system=system,
                    prompt=prompt,
                    limiter=self.token_limiter,
                    pool=self.connection_limiter,
                    temperature=temperature,
                    json_start=json_start,
                    **kwargs,
                )
