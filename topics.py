#!/usr/bin/env python
"""Python script for generating the events corresponding to a day's news."""
import argparse
import asyncio
import logging
import pathlib
import pickle
import random
import re
import sqlite3
import sys
import time
from textwrap import dedent, wrap

from pydantic import BaseModel, ValidationError
from ratelimit import limits, sleep_and_retry

from _models import ModelContext, calculate_cost

# Initialize API limits
SERVICE = "OpenAI"
MODEL = "gpt-4"

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

# Define the warmup number
N_WARMUP = 35

# Define the number of samples
N_SAMPLES = 10

# Define the total number samples
N_TOTAL = N_WARMUP + N_SAMPLES + 1

################################################################################

TOPICS = [
    [
        {
            "title": "International Relations",
            "description": "Coverage of global diplomacy, treaties, and conflicts.",
        },
        {
            "title": "National Security",
            "description": "Discussions about terrorism, defense policies, and homeland security.",
        },
        {
            "title": "Economics and Business",
            "description": "Reports on the stock market, corporate news, and economic indicators.",
        },
        {
            "title": "Politics",
            "description": "Coverage of political parties, campaigns, and elections.",
        },
        {
            "title": "Congress",
            "description": "Legislative actions, hearings, and debates.",
        },
        {
            "title": "The Presidency",
            "description": "Activities and policies of the sitting president.",
        },
        {
            "title": "Judiciary",
            "description": "Supreme Court decisions and significant legal battles.",
        },
        {
            "title": "Health Care",
            "description": "Debates on health policies, insurance issues, and public health crises.",
        },
        {
            "title": "Education",
            "description": "News on policy changes, school events, and educational reforms.",
        },
        {
            "title": "Environment",
            "description": "Issues like climate change, conservation efforts, and natural disasters.",
        },
        {
            "title": "Technology",
            "description": "Innovations, cybersecurity, and the impact of tech on society.",
        },
        {
            "title": "Sports",
            "description": "Major sporting events, updates, and athlete news.",
        },
        {
            "title": "Crime and Law Enforcement",
            "description": "Coverage of major crimes, law enforcement activities, and public safety issues.",
        },
        {
            "title": "Transportation",
            "description": "News about public transport, infrastructure projects, and automotive industry updates.",
        },
        {
            "title": "Arts and Culture",
            "description": "Highlights from the worlds of art, music, and culture.",
        },
        {
            "title": "Social Issues",
            "description": "Discussions on civil rights, social justice, and community movements.",
        },
        {
            "title": "Science and Research",
            "description": "Discoveries, research developments, and space exploration.",
        },
        {
            "title": "Health and Wellness",
            "description": "News on medical advancements, wellness tips, and health advisories.",
        },
        {
            "title": "Consumer Affairs",
            "description": "Consumer protection, product recalls, and shopping advice.",
        },
        {
            "title": "Human Interest Stories",
            "description": "Feature stories on individuals or events that have a unique or emotional appeal.",
        },
    ]
]

################################################################################
# Pydantic response objects


class Removal(BaseModel):
    """Pydantic object for a topic removal."""

    title: str
    id: int


class Addition(BaseModel):
    """Pydantic object for a topic addition."""

    title: str
    description: str


class Response(BaseModel):
    """Pydantic response object for the API."""

    explanation: str
    removals: list[Removal]
    additions: list[Addition]


################################################################################

SYSTEM_MESSAGE = """
You are helping me to create a list of very high-level news topics for events from the
last fifty years of news. I will present you with a list of events covered in the news
and the current working list of topics. Your job is to examine how well the topics cover
the news articles and to suggest new topics that do a better job of capturing the
various kinds of coverage. Ideally, most events should be able to be categorized under
one of the topics, and no topic should be too broad or too narrow. You should be careful
of suggesting topics that are too specific—the corpus of news articles spans more than
five decades and a wide range of sources.

I'm aiming for a list of around *twenty* topics by the end, so you should add, remove,
or merge topics as needed to reach that number. (Although anywhere between fifteen and
twenty-five topics would be acceptable.)

You suggest new topics by responding with a JSON object of the following form:
```json
{
    "explanation": str,
    "removals", [{"title": str, "id": int}],
    "additions": [{"title": str, "description": str}]
}
```
The `explanation` field should be a brief explanation (up to a paragraph) explaining
your proposed changes. The `removals` field should be a list of the indices of the
topics you think should be removed. The `additions` field should be a list of objects,
each containing a `title` and a `description` field. The `title` field should be a
short, descriptive title for the topic. The `description` field should be a longer
description of the topic. Note that any topic you remove should be (at least roughly)
covered by one of the topics you add.

Note that you don't *have* to suggest changes to the topics. If you think the current
list is good, just leave the `removals` and `additions` fields empty.
""".strip()

SYSTEM_MESSAGE_FINAL = """
You are helping me to create a list of very high-level news topics for events from the
last fifty years of news. I will present you with a list of events covered in the news
and a variety of working lists of topics. Your job is to examine how well the various
lists of topics cover the news articles and to suggest a final list of topics that do
the best job of capturing the various kinds of coverage.

You should aim for the list that best meets the following criteria:
1. Most events should be able to be categorized under one of the topics.
2. Topics should be distinct from each other, and not overlap too much.
3. Topics should be general enough to cover a wide range of news articles over the last
   fifty years.
4. Topics should be natural and feel like they are roughly at the same level of
   generality.

Respond with a JSON object of the following form:
```json
{"choice": int}
```
The `choice` field should be the index of the list of topics you think is the best
final list.
""".strip()


def get_prompt(iteration: int, events: list[sqlite3.Row]) -> str:
    """Get the current prompt."""
    events_str = "Here are 500 events from the news:\n" + "\n".join(
        f"• {event['date']}: {event['description']}" for event in events
    )
    topics_str = "Here are the current topics:\n" + "\n".join(
        f"{i}: {topic['title']} — {topic['description']}"
        for i, topic in enumerate(TOPICS[iteration - 1], start=1)
    )
    return f"{events_str}\n\n{topics_str}"


def get_final_prompt(
    events: list[sqlite3.Row], topics_list: list[list[dict[str, str]]]
) -> str:
    """Get the final prompt to choose the final topics."""
    events_str = "Here are 500 events from the news:\n" + "\n".join(
        f"• {event['date']}: {event['description']}" for event in events
    )
    topics_str = "\n\n".join(
        f"Topics {i}:\n"
        + "\n".join(
            f"{j}: {topic['title']} — {topic['description']}"
            for j, topic in enumerate(topics, start=1)
        )
        for i, topics in enumerate(topics_list, start=1)
    )
    return f"{events_str}\n\n{topics_str}"


################################################################################
# Database functions


def get_events() -> list[sqlite3.Row]:
    """Get 500 random events from the database."""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, description FROM events ORDER BY RANDOM() LIMIT 500"
        )
        return cursor.fetchall()


################################################################################


def apply_response(response: Response, iteration: int) -> None:
    """Check if the response is valid and update the topics accordingly."""
    global TOPICS

    # Check that the length of TOPICS is correct
    if len(TOPICS) != iteration:
        logging.error("Length of TOPICS is incorrect.")
        raise ValueError("Length of TOPICS is incorrect")

    # Get current topics list
    current_topics = TOPICS[iteration - 1]

    # Create new topics list starting with existing topics not marked for removal
    topics: list[dict[str, str]] = []
    removal_ids = {r.id for r in response.removals}

    # Use 1-based indexing as specified in the protocol
    for i, topic in enumerate(current_topics, start=1):
        if i not in removal_ids:
            topics.append(topic)
            logging.info(f"Keeping topic {i}: {topic['title']}")
        else:
            logging.info(f"Removing topic {i}: {topic['title']}")

    # Add new topics
    for addition in response.additions:
        topics.append({"title": addition.title, "description": addition.description})
        logging.info(f"Adding topic: {addition.title}")

    # Randomize the order of the topics
    random.shuffle(topics)

    # Update the topics
    TOPICS.append(topics)


def print_response(response: Response) -> str:
    """Pretty print the response."""
    removals_str = "\n".join(
        f"• (index {removal.id}) {removal.title}" for removal in response.removals
    )
    additions_str = "\n".join(
        f"• {addition.title}: {addition.description}" for addition in response.additions
    )
    return dedent(
        f"Explanation: {response.explanation}\n"
        f"Removals:\n{removals_str}\n"
        f"Additions:\n{additions_str}"
    )


def print_topics(iteration: int) -> str:
    """Pretty print the current list of topics."""
    return dedent(
        "\n".join(
            f"{i}: {topic['title']} — {topic['description']}"
            for i, topic in enumerate(TOPICS[iteration], start=1)
        ).strip()
    )


@sleep_and_retry
async def get_topics(iteration: int, m_context: ModelContext) -> Response:
    """Get the topics from the API."""

    # Get the prompt
    prompt = get_prompt(iteration, get_events())

    # Call the API
    async with m_context as m:
        response = await m.chat(
            system=SYSTEM_MESSAGE,
            prompt=prompt,
            json_start='{"explanation": "',
            temperature=1.0,
        )

    # Parse the response
    if response is None:
        logging.warning("Error getting response.")
        raise ValueError("Error getting response.")
    try:
        return Response.parse_raw(response)
    except ValidationError as e:
        logging.error("Error parsing response: %s", e)
        raise e


@sleep_and_retry
@limits(calls=1, period=30)
async def get_final_topics(
    m_context: ModelContext, topics_list: list[list[dict[str, str]]]
) -> int:
    """Get the final topics from the API."""

    # Get the prompt
    prompt = get_final_prompt(get_events(), topics_list)

    # Call the API
    async with m_context as m:
        response = await m.chat(
            system=SYSTEM_MESSAGE_FINAL,
            prompt=prompt,
            json_start='{"choice": ',
            temperature=1.0,
        )

    # Parse the response
    if response is None:
        logging.warning("Error getting response.")
        raise ValueError("Error getting response.")
    try:
        return int(re.search(r"\d+", response).group())
    except ValueError as e:
        logging.error("Error parsing response: %s", e)
        raise e


################################################################################


async def main():
    """Main function for the script."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="INFO")
    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "topics.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Set up the model context
    m_context = ModelContext(SERVICE, MODEL)
    await m_context.initialize()

    # Iterate on the topic list until the user says to halt
    iteration = 1
    start_time = time.time()
    while iteration < N_TOTAL:
        # Get the topics
        try:
            response = await get_topics(iteration, m_context)

            # Apply the response
            apply_response(response, iteration)

            # Pretty print the current list of topics and the response
            logging.info(print_topics(iteration))
            logging.info(print_response(response))
        except ValueError as e:
            logging.error("Error getting topics: %s", e)
            print("Error getting topics:", e)
        finally:
            # Increment the iteration
            iteration += 1

            # Print the curent iteration, elapsed time, time per iteration, and total time
            elapsed_time = time.time() - start_time
            time_per_iteration = elapsed_time / iteration
            total_time = time_per_iteration * (N_SAMPLES + N_WARMUP)
            print(
                f"[{iteration}/{N_TOTAL}]\t[{elapsed_time:.2f}s/{time_per_iteration:.2f}s/{total_time:.2f}s]",
                end="\r",
            )

    # Get the final topics
    try:
        choice = await get_final_topics(m_context, TOPICS[-N_SAMPLES:])
        # Log the final choice
        logging.info(f"Final choice: {choice}")

        # Add the topics to the database
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            for topic in TOPICS[N_WARMUP + choice - 1]:
                cursor.execute(
                    "INSERT INTO topics (title, description) VALUES (?, ?)",
                    (topic["title"], topic["description"]),
                )

    except ValueError as e:
        logging.error("Error getting final topics: %s", e)
        print("Error getting final topics:", e)
        # Pickle the topics
        with open("topics.pkl", "wb") as f:
            pickle.dump(TOPICS, f)

    # Close the model context
    await m_context.close()

    # Print the cost
    print(calculate_cost(SERVICE, MODEL))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error("Error in main: %s", e)
        print(calculate_cost(SERVICE, MODEL))
        raise e
    except KeyboardInterrupt:
        print(calculate_cost(SERVICE, MODEL))
        print("Exiting...")
        sys.exit(1)
