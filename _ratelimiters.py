from asyncio import Semaphore

from aiolimiter import AsyncLimiter

CONNECTION_LIMITER = Semaphore(100)

REQUEST_LIMITER = {
    "gpt-3.5-turbo-0125": AsyncLimiter(500, 3),
    "gpt-4-0125-preview": AsyncLimiter(250, 3),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events:9A33J3DL": AsyncLimiter(
        500, 3
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events2:9A6G5JOs": AsyncLimiter(
        500, 3
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events3:9AAUeVtD": AsyncLimiter(
        500, 3
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify:9AWOhGqH": AsyncLimiter(
        500, 3
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify2:9Aazgks8": AsyncLimiter(
        500, 3
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify3:9AdamZ7x": AsyncLimiter(
        500, 3
    ),
    "anthropic.claude-3-sonnet-20240229-v1:0": AsyncLimiter(5, 4),
    "anthropic.claude-3-haiku-20240307-v1:0": AsyncLimiter(15, 5),
}

TOKEN_LIMITER = {
    "gpt-3.5-turbo-0125": AsyncLimiter(500_000, 15),
    "gpt-4-0125-preview": AsyncLimiter(150_000, 15),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events:9A33J3DL": AsyncLimiter(
        500_000, 15
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events2:9A6G5JOs": AsyncLimiter(
        500_000, 15
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:events3:9AAUeVtD": AsyncLimiter(
        500_000, 15
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify:9AWOhGqH": AsyncLimiter(
        500_000, 15
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify2:9Aazgks8": AsyncLimiter(
        500_000, 15
    ),
    "ft:gpt-3.5-turbo-0125:computational-policy-lab:classify3:9AdamZ7x": AsyncLimiter(
        500_000, 15
    ),
    "anthropic.claude-3-sonnet-20240229-v1:0": AsyncLimiter(12_500, 5),
    "anthropic.claude-3-haiku-20240307-v1:0": AsyncLimiter(18_750, 5),
}
