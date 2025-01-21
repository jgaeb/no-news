#!/usr/bin/env python
"""Python script for generating fine-tuning data for an events model."""
import argparse
import datetime
import json
import pathlib
import sqlite3
from collections import defaultdict
from textwrap import dedent

from _utils import adapt_date, convert_date

DATABASE = pathlib.Path("data", "no-news.db")

# Register the converters and adapters for the DATE type
sqlite3.register_converter("DATE", convert_date)
sqlite3.register_adapter(datetime.date, adapt_date)

################################################################################
# Get the topics and issues by year


ISSUES = {}
with sqlite3.connect(DATABASE) as conn:
    conn.row_factory = sqlite3.Row
    TOPICS = conn.execute("SELECT id, title, description FROM topics").fetchall()
    for year in range(1968, 2020):
        ISSUES[year] = conn.execute(
            "SELECT id, title, description FROM issues WHERE year = ?",
            (year,),
        ).fetchall()


################################################################################

SYSTEM_MESSAGE = """
You categorize news items by the issues they cover and the topics they discuss.
You should respond with a JSON object as follows:
```json
{
    "results:[
        {"id": int, "issue": int | null, "topic": int | null, "hard_news": bool}
    ]
}
```
Here `id` is the id of the corresponding news item, `issue` is the issue number, and
`topic` is the topic number in the list of issues and topics provided. If more than one
listed issue or topic could fit a given news item, choose the one that is most relevant
or most important. If no listed issue or topic fits, set the value to `null`. If the
news item is hard news (e.g., politics, economics, crime), set `hard_news` to `true`;
otherwise, set it to `false`.
""".strip()


def get_prompt(segments: list[dict], year: int) -> str:
    """Generate the whole prompt for the news classification task."""
    topics_str = "\n".join(
        f"{topic['id']}: {topic['title']}: {topic['description']}" for topic in TOPICS
    )
    issues_str = "\n".join(
        f"{issue['id']}: {issue['title']}: {issue['description']}"
        for issue in ISSUES[year]
    )

    segment_prompt = "\n\n".join(
        [
            f"({s['id']}) {s['outlet']}\n{s['title']}:\n{s['abstract']}\n====================\n"
            for s in segments
        ]
    )

    return (
        "Topics:\n"
        f"{topics_str}\n\n"
        "Issues:\n"
        f"{issues_str}\n\n"
        "News Segments:\n"
        f"{segment_prompt}"
    )


################################################################################


async def get_segments(segment_ids: list[int]) -> list[dict]:
    """Get a given list of segments."""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row

        cur = conn.cursor()

        # SQL query to select required segments
        query = """
        SELECT outlet, id, title, abstract
        FROM segments
        WHERE id IN ({ids})
        AND NOT empty
        AND NOT commercial
        AND outlet IN ("ABC", "CBS", "NBC")
        AND program IN ("ABC Evening News", "CBS Evening News", "NBC Evening News")
        ORDER BY outlet, id
        """.format(
            ids=", ".join(str(id) for id in segment_ids)
        )

        # Execute the query asynchronously
        cur.execute(query)
        # Fetch all the rows asynchronously
        rows = cur.fetchall()

        return rows


def generate_jsonl(segment_ids: list[int], year: int) -> dict:
    """Generate lines of JSON data for the given segments."""
    # Get the segment
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM segments WHERE id IN ({})".format(
                ", ".join(str(id) for id in segment_ids)
            )
        )
        segments = cur.fetchall()

    # Get the prompt
    prompt = get_prompt(segments=segments, year=year)

    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
            {
                "role": "assistant",
                "content": json.dumps(
                    [
                        {
                            "id": s["id"],
                            "issue": s["issue_id"],
                            "topic": s["topic_id"],
                            "hard_news": s["hard_news"],
                        }
                        for s in segments
                    ]
                ),
            },
        ]
    }

    return payload


def main():
    """Main function."""
    # Set up the argument parser
    parser = argparse.ArgumentParser(
        description="Generate fine-tuning data for the classification model."
    )
    parser.add_argument(
        "split", type=float, help="Proportion of training data to generate."
    )
    parser.add_argument(
        "ids", type=str, help="File containing the segment ids to use as JSONL data."
    )
    args = parser.parse_args()

    # Validate the split
    if not 0 < args.split <= 1:
        parser.error("The split must be a number between 0 and 1.")

    # Get the segment IDs
    segment_ids = []
    with open(args.ids) as f:
        lines = f.readlines()
    for line in lines:
        obj = json.loads(line)
        segment_ids.append(obj)

    # Split the segment IDs
    split = int(args.split * len(segment_ids))
    training_segment_ids = segment_ids[:split]
    testing_segment_ids = segment_ids[split:]

    # Print the number of training and testing dates
    print(f"Training segment_ids: {len(training_segment_ids)}")
    print(f"Testing segment_ids: {len(testing_segment_ids)}")

    # Generate the training data
    with open(pathlib.Path("data", "classify-training.jsonl"), "w") as f:
        for obj in training_segment_ids:
            json.dump(generate_jsonl(obj["ids"], obj["year"]), f)
            f.write("\n")

    # Generate the testing data
    with open(pathlib.Path("data", "classify-testing.jsonl"), "w") as f:
        for segment_id in testing_segment_ids:
            json.dump(generate_jsonl(segment_id["ids"], segment_id["year"]), f)
            f.write("\n")


if __name__ == "__main__":
    main()
