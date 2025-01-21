"""Connection pool classes for SQLite and AWS clients."""

import asyncio
import logging
import os

from aiobotocore.session import get_session  # type: ignore
from botocore.config import Config  # type: ignore

from _utils import adapt_date, convert_date

# Initialize the AWS session
try:
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
except KeyError:
    raise KeyError(
        "Please set the environment variables AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY."
    )

CONFIG = Config(retries={"max_attempts": 1, "mode": "standard"})
SESSION = get_session()

################################################################################


class AWSClientConnectionManager:
    def __init__(self, pool):
        self.pool = pool
        self.client = None

    async def __aenter__(self):
        self.client = await self.pool.acquire()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.pool.release(self.client)
        self.client = None


class AWSClientConnectionPool:
    """Connection pool for AWS clients."""

    def __init__(
        self,
        maxsize=10,
        service_name="bedrock-runtime",
        region_name="us-east-1",
    ):
        self.service_name = service_name
        self.region_name = region_name
        self.maxsize = maxsize
        self.pool = asyncio.Queue(maxsize=maxsize)
        self.lock = asyncio.Lock()

    async def initialize(self):
        async with self.lock:
            # Pre-populate the pool with connections
            for _ in range(self.maxsize):
                client = await SESSION._create_client(
                    service_name=self.service_name,
                    region_name=self.region_name,
                    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                    aws_access_key_id=AWS_ACCESS_KEY_ID,
                    config=CONFIG,
                )
                await self.pool.put(client)
                logging.info("Created client %s", client)

    async def close(self):
        async with self.lock:
            while not self.pool.empty():
                client = await self.pool.get()
                await client.close()
                logging.info("Closed client %s", client)

    async def acquire(self):
        return await self.pool.get()

    async def release(self, client):
        await self.pool.put(client)

    def client(self):
        return AWSClientConnectionManager(self)
