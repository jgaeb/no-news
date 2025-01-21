#!/usr/bin/env python
"""Python script for generating the events corresponding to a day's news."""
import argparse
import asyncio
import datetime
import logging
import pathlib
import random
import re
import sqlite3
from textwrap import dedent

import aiosqlite  # type: ignore
from pydantic import BaseModel, ValidationError
from tqdm.asyncio import tqdm_asyncio as tqdm  # type: ignore

from _models import ModelContext, calculate_cost
from _utils import handle_exceptions

# Initialize API limits
SERVICE = "OpenAI"
MODEL = "ft-events-3"

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

################################################################################

SYSTEM_MESSAGE = """
You summarize the day's news by giving a list of all the events that were
covered by the media, and by saying what event was the most important. You
respond with a JSON object as follows:
```json
{"events": [{"description": str, "segments": [int]}, ...]}
```
The JSON should be well-formatted, so remember to use double quotes for the keys and to
escape quotes in the strings. The string in the `description` field should be a brief
(but accurate) one-sentence summary of the event that occurred. The `segments` field
should be the ID numbers of the segments that covered the event.

In a typical news broadcast, each segment will cover a different event, so you should
expect to have anywhere from 12 to 20 events in total, unless it's an unusual day (e.g.,
slow news or one extremely important event).

It's very important that you list the most important event *FIRST*, and that you
don't list the same event more than once.
""".strip()


def get_prompt(segments: list[aiosqlite.Row], date: datetime.date) -> str:
    """Generate the prompt for the given segments."""
    # Gather the first two abstracts from ABC, CBS, and NBC and print them
    # with the titles
    abstracts = ""
    for outlet in ["ABC", "CBS", "NBC"]:
        abstracts += "\n".join(
            [
                (
                    f"({s['id']}) {s['outlet']}\n"
                    f"{s['title']}:\n{s['abstract']}"
                    "\n====================\n"
                )
                for s in segments
                if s["outlet"] == outlet
            ]
        )

    prompt = (
        "These are the news stories that appeared on ABC, CBS, and NBC on "
        f"{date}:\n\n"
        f"{abstracts}\n\n"
        "What *specific* events happend that day that were reported on by the media? "
        "Be sure that your answer is a complete sentence (i.e., not just a noun or "
        "list of nouns) and specifc to the date (i.e., not 'the war in Iraq' or 'the "
        "economy', but 'Secretary Rumsfeld announced...'). Most segments will "
        "probably report at least one event, unless it's news analysis or human "
        "interest, but some events will be reported in multiple segments. "
        "You should have almost as many events as segments.\n"
        "Don't include the same event more than once, and list the most important "
        "event first."
    )

    return dedent(prompt).strip()


################################################################################
# Pydantic response object


class Event(BaseModel):
    """Pydantic model for a single event."""

    description: str
    segments: list[int]


class Response(BaseModel):
    """Pydantic model for the response."""

    events: list[Event]


################################################################################


async def get_segments(
    date: datetime.date, conn: aiosqlite.Connection
) -> list[aiosqlite.Row]:
    """Get the segments for a given date."""
    logging.debug("Getting segments for %s", date)
    async with conn.cursor() as cur:
        # SQL query to select required segments
        query = """
        SELECT outlet, id, title, abstract
        FROM segments
        WHERE date = ?
        AND NOT empty
        AND NOT commercial
        AND outlet IN ("ABC", "CBS", "NBC")
        AND program IN ("ABC Evening News", "CBS Evening News", "NBC Evening News")
        ORDER BY outlet, id
        """

        # Execute the query asynchronously
        await cur.execute(query, (date,))

        # Fetch all the rows asynchronously
        rows = await cur.fetchall()

        logging.debug("Got %i segments for %s", len(rows), date)
        return rows


async def unprocessed_date(date: datetime.date, conn: aiosqlite.Connection) -> bool:
    """Checks if the date is not in the database."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE date = ?
            """,
            (date,),
        )
        count = await cur.fetchone()
        if count[0] > 0:
            logging.info("Date %s already in database", date)
            return False
        return True


################################################################################


def clean_events(segments: list[aiosqlite.Row], events: list[Event]) -> list[Event]:
    """Check if the events are already in the database."""
    # Check if any of the events list segments that don't exist
    segment_ids = {segment["id"] for segment in segments}
    for event in events:
        if any(segment_id not in segment_ids for segment_id in event.segments):
            raise ValueError(
                f"Event contains segments that don't exist: {event}, {segment_ids}"
            )

    # Drop any events from the list that don't have segments
    ret_events = [event for event in events if event.segments]
    if len(ret_events) < len(events):
        logging.warning(
            "Dropped events with no segments: %s",
            [e.description for e in events if not e.segments],
        )

    return ret_events


@handle_exceptions
async def generate_events(
    date: datetime.date,
    conn: aiosqlite.Connection,
    m_context: ModelContext,
) -> None:
    """Generate the events for a given date."""

    # Generate the prompt
    segments = await get_segments(date, conn)
    if not segments:
        logging.info("No segments for %s", date)
        return
    prompt = get_prompt(segments, date)

    # Call the API
    async with m_context as m:
        response = await m.chat(
            system=SYSTEM_MESSAGE,
            prompt=prompt,
            json_start='{"events":',
            temperature=0.9,
        )

    # Parse the response
    if response is None:
        logging.error("Error getting response for %s", date)
        return
    try:
        # Extract JSON from surounding text in case the model adds extra text
        response_str = re.search(r"\{.*\}", response, re.DOTALL | re.MULTILINE).group()

        response = Response.parse_raw(response_str)

        # Clean the events
        events = clean_events(segments, response.events)

        # Save the events to the database
        async with conn.cursor() as cur:
            for i, event in enumerate(events):
                await cur.execute(
                    """
                    INSERT INTO events (model, date, description, top_story)
                    VALUES (?, ?, ?, ?);
                    """,
                    (MODEL, date, event.description, i == 0),
                )
                event_id = cur.lastrowid
                # Update the segments with the event ID
                for segment_id in event.segments:
                    await cur.execute(
                        """
                        UPDATE segments
                        SET event_id = ?
                        WHERE id = ?;
                        """,
                        (event_id, segment_id),
                    )
            await conn.commit()
    except ValidationError as e:
        logging.error("Error parsing response: %s, %s", response, e)


################################################################################
# Main function


async def main() -> None:
    """Main function to generate the events for all dates."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("start_date", type=datetime.date.fromisoformat)
    parser.add_argument("end_date", type=datetime.date.fromisoformat)
    parser.add_argument("--log", default="WARN")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--randomize", action="store_true")
    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "events.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Setup the SQLite connection
    conn = aiosqlite.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)

    # Initialize the model context
    m_context = ModelContext(SERVICE, MODEL)
    await m_context.initialize()

    # Generate all dates between the start and end date, not inclusive
    start_date = args.start_date
    end_date = args.end_date
    dates = [
        start_date + datetime.timedelta(days=i)
        for i in range((end_date - start_date).days)
        if await unprocessed_date(start_date + datetime.timedelta(days=i), conn)
    ]
    logging.info(
        "Generating events for %i dates, %s to %s", len(dates), start_date, end_date
    )
    if args.randomize:
        random.shuffle(dates)
    if len(dates) > args.limit:
        logging.info("Limiting to %i dates", args.limit)
        dates = dates[: args.limit]

    # Generate the events for all dates
    tasks = [generate_events(date, conn, m_context) for date in dates]
    await tqdm.gather(*tasks)

    # Close the connections
    await conn.close()
    await m_context.close()

    # Print usage statistics
    print(calculate_cost(SERVICE, MODEL))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.exception("Error in main")
        print(calculate_cost(SERVICE, MODEL))
        raise e
