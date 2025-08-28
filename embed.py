#!/usr/bin/env python
"""Python script for embedding news segments, events, and issues using OpenAI API."""
import argparse
import asyncio
import datetime
import logging
import os
import pathlib
from textwrap import dedent

import aiosqlite
import openai
import tiktoken
from aiolimiter import AsyncLimiter
from pydantic import BaseModel, ValidationError
from tqdm.asyncio import tqdm_asyncio as tqdm

# Initialize OpenAI API
client = openai.AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"], organization=os.environ["OPENAI_API_ORG"]
)

encoding = tiktoken.encoding_for_model("gpt-3.5-turbo-0125")

# Initialize API rate limits
connetions_limiter = asyncio.Semaphore(200)
request_limiter = AsyncLimiter(5e3)
token_limiter = AsyncLimiter(9e6)

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

TOTAL_TOKENS = 0

################################################################################


def get_prompt_segment(title: str, abstract: str) -> str:
    prompt = f"""
    {title}
    ================
    {abstract}
    """
    return dedent(prompt).strip()


def get_prompt_event(description: str) -> str:
    return description


def get_prompt_issue(title: str, description: str) -> str:
    prompt = f"{title}: {description}"
    return dedent(prompt).strip()


################################################################################


async def get_segments(
    start_date: datetime.date, end_date: datetime.date, conn: aiosqlite.Connection
) -> list[tuple]:
    segments = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT s.id, s.title, s.abstract
            FROM segments AS s
            LEFT JOIN embeddings AS e
            ON s.id = e.segment_id
            WHERE s.date BETWEEN ? AND ?
            AND NOT s.commercial
            AND NOT s.empty
            AND s.outlet IN ("ABC", "CBS", "NBC")
            AND s.program IN ("ABC Evening News", "CBS Evening News", "NBC Evening News")
            AND e.segment_id IS NULL
            """,
            (start_date, end_date),
        )
        async for row in cur:
            segments.append(row)
    return segments


async def get_events(
    start_date: datetime.date, end_date: datetime.date, conn: aiosqlite.Connection
) -> list[tuple]:
    events = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT ev.id, ev.description
            FROM events AS ev
            LEFT JOIN embeddings AS em
            ON ev.id = em.event_id
            WHERE ev.date BETWEEN ? AND ?
            AND em.event_id IS NULL
            """,
            (start_date, end_date),
        )
        async for row in cur:
            events.append(row)
    return events


async def get_issues(
    start_year: int, end_year: int, conn: aiosqlite.Connection
) -> list[tuple]:
    issues = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT i.id, i.title, i.description
            FROM issues AS i
            LEFT JOIN embeddings AS e
            ON i.id = e.issue_id
            WHERE year BETWEEN ? AND ?
            AND e.issue_id IS NULL
            """,
            (start_year, end_year),
        )
        async for row in cur:
            issues.append(row)
    return issues


# Check if the embedding already exists in the database
async def check(type: str, type_id: int, conn: aiosqlite.Connection) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT COUNT(id) FROM embeddings WHERE {type}_id = ?",
            (type_id,),
        )
        row = await cur.fetchone()
        return row[0] > 0


################################################################################


async def embed_segment(
    segment_id: int,
    title: str,
    abstract: str,
    conn: aiosqlite.Connection,
) -> None:
    """Embed a single segment using the OpenAI API."""
    async with connetions_limiter:
        # Check if the segment has already been embedded
        if await check("segment", segment_id, conn):
            logging.warning("Segment %i already embedded", segment_id)
            return

        # Get the prompt
        prompt = get_prompt_segment(title, abstract)
        logging.debug("Prompt for segment %i: %s", segment_id, prompt)

        # Call the API
        await token_limiter.acquire(len(encoding.encode(prompt)))
        global TOTAL_TOKENS
        TOTAL_TOKENS += len(encoding.encode(prompt))
        async with request_limiter:
            response_embedding = await client.embeddings.create(
                input=[prompt],
                model="text-embedding-3-small",
                dimensions=256,
            )

        # Insert the embedding into the database
        async with conn.cursor() as cur:
            dims = ", ".join("X_" + str(i) for i in range(256))
            vals = ", ".join("?" for _ in range(256))
            await cur.execute(
                f"""
                INSERT INTO embeddings (segment_id, {dims})
                VALUES (?, {vals})
                """,
                (segment_id, *response_embedding.data[0].embedding),
            )
            await conn.commit()


async def embed_event(
    event_id: int,
    description: str,
    conn: aiosqlite.Connection,
) -> None:
    """Embed a single event using the OpenAI API."""
    async with connetions_limiter:
        # Check if the event has already been embedded
        if await check("event", event_id, conn):
            logging.warning("Event %i already embedded", event_id)
            return

        # Get the prompt
        prompt = get_prompt_event(description)
        logging.debug("Prompt for event %i: %s", event_id, prompt)

        # Call the API
        await token_limiter.acquire(len(encoding.encode(prompt)))
        global TOTAL_TOKENS
        TOTAL_TOKENS += len(encoding.encode(prompt))
        async with request_limiter:
            response_embedding = await client.embeddings.create(
                input=[prompt],
                model="text-embedding-3-small",
                dimensions=256,
            )

        # Insert the embedding into the database
        async with conn.cursor() as cur:
            dims = ", ".join("X_" + str(i) for i in range(256))
            vals = ", ".join("?" for _ in range(256))
            await cur.execute(
                f"""
                INSERT INTO embeddings (event_id, {dims})
                VALUES (?, {vals})
                """,
                (event_id, *response_embedding.data[0].embedding),
            )
            await conn.commit()


async def embed_issue(
    issue_id: int,
    title: str,
    description: str,
    conn: aiosqlite.Connection,
) -> None:
    """Embed a single issue using the OpenAI API."""
    async with connetions_limiter:
        # Check if the issue has already been embedded
        if await check("issue", issue_id, conn):
            logging.warning("Issue %s already embedded", title)
            return

        # Get the prompt
        prompt = get_prompt_issue(title, description)
        logging.debug("Prompt for issue %s: %s", title, prompt)

        # Call the API
        await token_limiter.acquire(len(encoding.encode(prompt)))
        global TOTAL_TOKENS
        TOTAL_TOKENS += len(encoding.encode(prompt))
        async with request_limiter:
            response_embedding = await client.embeddings.create(
                input=[prompt],
                model="text-embedding-3-small",
                dimensions=256,
            )

        # Insert the embedding into the database
        async with conn.cursor() as cur:
            dims = ", ".join("X_" + str(i) for i in range(256))
            vals = ", ".join("?" for _ in range(256))
            await cur.execute(
                f"""
                INSERT INTO embeddings (issue_id, {dims})
                VALUES (?, {vals})
                """,
                (issue_id, *response_embedding.data[0].embedding),
            )
            await conn.commit()


################################################################################
# Main function


async def main() -> None:
    """Embed all the segments, events, and issues."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("type", choices=["segments", "events", "issues"])
    parser.add_argument("--start-date", type=datetime.date.fromisoformat)
    parser.add_argument("--end-date", type=datetime.date.fromisoformat)
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--log", default="INFO")
    args = parser.parse_args()
    if args.type == "segments":
        if not args.start_date or not args.end_date:
            parser.error(
                "The --start-date and --end-date arguments are required for segments"
            )
        start_date = args.start_date
        end_date = args.end_date
    elif args.type == "events":
        if not args.start_date or not args.end_date:
            parser.error(
                "The --start-date and --end-date arguments are required for events"
            )
        start_date = args.start_date
        end_date = args.end_date
    elif args.type == "issues":
        if not args.start_year or not args.end_year:
            parser.error(
                "The --start-year and --end-year arguments are required for issues"
            )
        start_year = args.start_year
        end_year = args.end_year

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "embed.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Connect to the database
    conn = await aiosqlite.connect(DATABASE)

    # Fetch the data to embed
    if args.type == "segments":
        data = await get_segments(start_date, end_date, conn)
    elif args.type == "events":
        data = await get_events(start_date, end_date, conn)
    elif args.type == "issues":
        data = await get_issues(start_year, end_year, conn)

    # Create the tasks to embed the data
    if args.type == "segments":
        tasks = [
            embed_segment(segment_id, title, abstract, conn)
            for segment_id, title, abstract in data
        ]
    elif args.type == "events":
        tasks = [
            embed_event(event_id, description, conn) for event_id, description in data
        ]
    elif args.type == "issues":
        tasks = [
            embed_issue(issue_id, title, description, conn)
            for issue_id, title, description in data
        ]

    # Embed the data
    await tqdm.gather(*tasks)

    # Close the database connection
    await conn.close()

    print(f"Total tokens used: {TOTAL_TOKENS}")


if __name__ == "__main__":
    asyncio.run(main())
