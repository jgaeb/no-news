#!/usr/bin/env python
"""Python script for generating the events corresponding to a day's news."""
import argparse
import asyncio
import datetime
import json
import logging
import os
import pathlib
import re
import sqlite3
from typing import Dict, List, Optional

import aiosqlite  # type: ignore
import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError
from tqdm.asyncio import tqdm_asyncio as tqdm  # type: ignore

from _aws import format_payload
from _models import MODELS, ModelContext, calculate_cost
from _utils import adapt_date, convert_date, handle_exceptions

# Initialize API limits
SERVICE = "AWS"
MODEL = "haiku"

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

# Register the SQLite date adapter
aiosqlite.register_adapter(datetime.date, adapt_date)
aiosqlite.register_converter("DATE", convert_date)
sqlite3.register_adapter(datetime.date, adapt_date)
sqlite3.register_converter("DATE", convert_date)

# Create a semaphore for really big jobs to limit the number of concurrent tasks
SEMAPHORE = asyncio.Semaphore(1000)

# Initialize the AWS session
try:
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    BUCKET = os.environ.get("AWS_BUCKET")
    SERVICE_ROLE_ARN = os.environ.get("AWS_SERVICE_ROLE_ARN")
except KeyError as exc:
    raise KeyError(
        "Please set the environment variables AWS_ACCESS_KEY_ID, "
        "AWS_SECRET_ACCESS_KEY, AWS_BUCKET, and AWS_SERVICE_ROLE_ARN"
    ) from exc

################################################################################
# Get the topics and issues by year


ISSUES = {}
with sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
    conn.row_factory = sqlite3.Row
    TOPICS = conn.execute("SELECT id, title, description FROM topics").fetchall()
    for year in range(1968, 2020):
        ISSUES[year] = conn.execute(
            "SELECT id, title, description FROM issues WHERE year = ?",
            (year,),
        ).fetchall()


################################################################################

SYSTEM_MESSAGE_ISSUES = """
You categorize what issues different news segments cover. You should respond with a JSON
object as follows:
```json
{
    "explanation": str,
    "issue": int,
}
```
The `explanation` field should be a brief explanation of which issue is the best fit for
for the news segment, as well as an explanation of whether that issue is a good fit for
the particular news segment or not. If the news segment does not fit any of the issues
you have been provided, you should respond with `null`.
""".strip()

SYSTEM_MESSAGE_TOPICS = """
You categorize what topics different news segments cover. You should respond with a JSON
object as follows:
```json
{
    "explanation": str,
    "topic": int,
    "hard_news": bool
}
```
The `explanation` field should be a brief explanation of which topic is the best fit for
for the news segment, as well as an explanation of whether that topic is a good fit for
the particular news segment or not. If the news segment does not fit any of the topics
you have been provided, you should respond with `null`.

Finally, the `hard_news` field should be set to `true` if the news segment is hard news
(e.g., politics, economics, crime), and `false` otherwise (e.g., entertainment, sports,
human interest).
""".strip()


def get_prompt(level: str, outlet: str, title: str, abstract: str, date: datetime.date):
    """Generate the whole prompt for the news classification task."""
    topics_str = "\n".join(
        f"{topic['id']}: {topic['title']}: {topic['description']}" for topic in TOPICS
    )
    issues_str = "\n".join(
        f"{issue['id']}: {issue['title']}: {issue['description']}"
        for issue in ISSUES[date.year]
    )
    segment_prompt = f"({date}) {outlet}: {title}\n{abstract}"

    if level == "issues":
        return f"Issues:\n{issues_str}\n\nNews Segment:\n{segment_prompt}"
    elif level == "topics":
        return f"Topics:\n{topics_str}\n\nNews Segment:\n{segment_prompt}"
    else:
        raise ValueError(f"Invalid level: {level}")


################################################################################
# Pydantic response object


class IssueResponse(BaseModel):
    """Pydantic model for the response to the news classification task."""

    issue: Optional[int]


class TopicResponse(BaseModel):
    """Pydantic model for the response to the news classification task."""

    topic: Optional[int]
    hard_news: bool


################################################################################


async def get_segment(segment_id: int, conn: aiosqlite.Connection) -> aiosqlite.Row:
    """Get a news segment by its ID."""
    logging.debug("Getting segment %i", segment_id)
    async with conn.cursor() as cur:
        cur.row_factory = aiosqlite.Row
        await cur.execute(
            """
            SELECT *
            FROM segments
            WHERE id = ?
            """,
            (segment_id,),
        )
        return await cur.fetchone()


################################################################################


def check_issue(response: IssueResponse, year: int) -> bool:
    """Check if the response is valid."""
    # Check if the issue is valid
    if response.issue not in {issue["id"] for issue in ISSUES[year]}:
        if response.issue is not None:
            return False
    return True


def check_topic(response: TopicResponse, year: int) -> bool:
    """Check if the response is valid."""
    # Check if the topic is valid
    if response.topic not in {topic["id"] for topic in TOPICS}:
        if response.topic is not None:
            return False
    return True


@handle_exceptions
async def classify_issue(
    segment_id: int, year: int, conn: aiosqlite.Connection, m_context: ModelContext
) -> None:
    """Classify what issue the news segment covers."""
    async with SEMAPHORE:
        # Get the prompt
        segment = await get_segment(segment_id, conn)
        prompt = get_prompt(
            "issues",
            segment["outlet"],
            segment["title"],
            segment["abstract"],
            segment["date"],
        )
        logging.debug("Prompt for %i: %s", segment_id, prompt)

        # Call the API
        async with m_context as m:
            response = await m.chat(
                system=SYSTEM_MESSAGE_ISSUES,
                prompt=prompt,
                json_start='{"explanation": "',
                temperature=1.0,
            )

        # Parse the response
        if response is None:
            logging.error("Error getting response for %i", segment_id)
            return
        try:
            # Extract JSON from surounding text in case the model adds extra text
            response_str = re.search(
                r"\{.*\}", response, re.DOTALL | re.MULTILINE
            ).group()

            response = IssueResponse.parse_raw(response_str)

            # Check if the response is valid
            if not check_issue(response, year):
                logging.error("Invalid response for %i: %s", segment_id, response_str)
                return

            # Save the response to the database
            async with conn.cursor() as cur:
                # NOTE: We use the sentinel value -1 when the issue is not set
                if response.issue is None:
                    response.issue = -1
                await cur.execute(
                    """
                    UPDATE segments
                    SET issue_id = ?
                    WHERE id = ?
                    """,
                    (response.issue, segment_id),
                )
                await conn.commit()

        except ValidationError as e:
            logging.error("Error parsing response for %i: %s", segment_id, response_str)
            logging.error(e)


@handle_exceptions
async def classify_topic(
    segment_id: int, year: int, conn: aiosqlite.Connection, m_context: ModelContext
) -> None:
    """Classify the news segment."""
    async with SEMAPHORE:
        # Get the prompt
        segment = await get_segment(segment_id, conn)
        prompt = get_prompt(
            "topics",
            segment["outlet"],
            segment["title"],
            segment["abstract"],
            segment["date"],
        )
        logging.debug("Prompt for %i: %s", segment_id, prompt)

        # Call the API
        async with m_context as m:
            response = await m.chat(
                system=SYSTEM_MESSAGE_TOPICS,
                prompt=prompt,
                json_start='{"explanation": "',
                temperature=1.0,
            )

        # Parse the response
        if response is None:
            logging.error("Error getting response for segment %i", segment_id)
            return
        try:
            # Extract JSON from surounding text in case the model adds extra text
            response_str = re.search(
                r"\{.*\}", response, re.DOTALL | re.MULTILINE
            ).group()

            response = TopicResponse.parse_raw(response_str)

            # Check if the response is valid
            if not check_topic(response, year):
                logging.error(
                    "Invalid topic response for %i: %s", segment_id, response_str
                )
                return

            # Save the response to the database
            async with conn.cursor() as cur:
                # NOTE: We use the sentinel value -1 when the topic is not set
                if response.topic is None:
                    response.topic = -1
                await cur.execute(
                    """
                    UPDATE segments
                    SET topic_id = ?,
                    hard_news = ?
                    WHERE id = ?
                    """,
                    (response.topic, response.hard_news, segment_id),
                )
                await conn.commit()

        except ValidationError as e:
            logging.error("Error parsing response for %i: %s", segment_id, response_str)
            logging.error(e)


################################################################################
##################################### AWS ######################################
################################################################################


class ModelOutput(BaseModel):
    explanation: Optional[str] = None
    topic: Optional[int] = None
    issue: Optional[int] = None
    hard_news: Optional[bool] = None


class ModelResult:
    raw_input: Dict
    record_id: int
    level: str  # 'topics' or 'issues'
    parsed_output: ModelOutput

    def __init__(self, raw_input: str, level: str):
        self.raw_input = json.loads(raw_input)
        self.record_id = int(self.raw_input["recordId"].lstrip("0"))
        self.level = level
        try:
            self.parsed_output = ModelOutput.parse_raw(
                '{"explanation": "'
                + self.raw_input["modelOutput"]["content"][0]["text"]
            )
        except KeyError as e:
            raise ValueError(f"Missing key in model output: {raw_input}") from e


################################################################################


def generate_prompts(segments: List[sqlite3.Row], level: str) -> None:
    """Generate prompts for the AWS Bedrock API."""
    # Initialize the s3 client
    s3 = boto3.client("s3")

    # Create a JSON object for each segment
    tokens = 0
    prompts = []
    for segment in segments:
        # Generate the appropriate prompt based on level
        segment_prompt = (
            f"({segment['date']}) {segment['outlet']}: "
            f"{segment['title']}\n{segment['abstract']}"
        )
        if level == "issues":
            system_message = SYSTEM_MESSAGE_ISSUES
            issues_str = "\n".join(
                f"{issue['id']}: {issue['title']}: {issue['description']}"
                for issue in ISSUES[int(segment["year"])]
            )
            prompt = f"Issues:\n{issues_str}\n\nNews Segment:\n{segment_prompt}"
        else:  # topics
            system_message = SYSTEM_MESSAGE_TOPICS
            topics_str = "\n".join(
                f"{topic['id']}: {topic['title']}: {topic['description']}"
                for topic in TOPICS
            )
            prompt = f"Topics:\n{topics_str}\n\nNews Segment:\n{segment_prompt}"

        payload = json.dumps(
            {
                "recordId": f"{segment['id']:011}",
                "modelInput": format_payload(
                    system=system_message,
                    prompt=prompt,
                    temperature=1.0,
                    json_start='{"explanation": "',
                ),
            }
        )
        prompts.append(payload)
        tokens += (len(system_message) + len(prompt) + len("{") + 4) // 5

    # Print the total number of tokens
    logging.info("%s prompts with a total of %s tokens", len(segments), tokens)

    # Break into chunks of at most 50,000 and upload to S3
    i = 0
    while prompts:
        chunk = prompts[:50000]
        prompts = prompts[50000:]

        # Upload the chunk to S3
        try:
            s3.put_object(
                Body="\n".join(chunk),
                Bucket=BUCKET,
                Key=f"input/{level}_prompts_{i:03}.jsonl",
            )
            logging.info("Uploaded prompt chunk %s to S3", i)
            i += 1
        except ClientError as e:
            logging.error("Failed to upload prompt chunk %s to S3: %s", i, e)
            break


def start_bedrock_batch_job(job_name: str, level: str) -> None:
    """Start a batch job on AWS Bedrock."""
    try:
        # Initialize the s3 client
        s3 = boto3.client("s3")

        # Get all of the json lines files in the input directory for this level
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"input/{level}_")
        input_files = [
            obj["Key"]
            for obj in response.get("Contents", [])
            if obj["Key"].endswith(".jsonl")
        ]

        # Initialize the Bedrock client
        bedrock = boto3.client("bedrock")

        # For each input file, create a job
        for i, input_file in enumerate(input_files):
            # Define the job configuration
            job_config = {
                "jobName": f"{job_name}-{level}-{i:03}",
                "inputDataConfig": {
                    "s3InputDataConfig": {"s3Uri": f"s3://{BUCKET}/{input_file}"}
                },
                "outputDataConfig": {
                    "s3OutputDataConfig": {"s3Uri": f"s3://{BUCKET}/output/"}
                },
                "modelId": MODELS["AWS"]["haiku"],
                "roleArn": SERVICE_ROLE_ARN,
            }

            # Start the batch job
            response = bedrock.create_model_invocation_job(**job_config)

            # Get the job ID
            job_arn = response["jobArn"]
            logging.info("Started Bedrock batch job: %s", job_arn)

    except ClientError as e:
        logging.error("Failed to start Bedrock batch job: %s", e)
        raise


def update_database(cur: sqlite3.Cursor, result: ModelResult) -> None:
    """Update the database with the parsed result."""
    try:
        if result.level == "issues":
            # For issues, we only update the issue_id
            cur.execute(
                """
                UPDATE segments
                SET issue_id = ?
                WHERE id = ?
                """,
                (
                    result.parsed_output.issue
                    if result.parsed_output.issue is not None
                    else -1,
                    result.record_id,
                ),
            )
        else:  # topics
            # For topics, we update both topic_id and hard_news
            cur.execute(
                """
                UPDATE segments
                SET topic_id = ?,
                    hard_news = ?
                WHERE id = ?
                """,
                (
                    result.parsed_output.topic
                    if result.parsed_output.topic is not None
                    else -1,
                    result.parsed_output.hard_news,
                    result.record_id,
                ),
            )

        if cur.rowcount == 0:
            logging.warning("No segment found for ID %s", result.record_id)

    except sqlite3.Error as e:
        logging.error("Database error for record ID %s: %s", result.record_id, e)


def process_results(level: str) -> None:
    """Download, parse, and update the database with batch job results."""
    try:
        # Initialize the S3 client
        s3 = boto3.client("s3")

        # List all objects in the output directory
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix="output/")
        output_files = [
            obj["Key"]
            for obj in response.get("Contents", [])
            if obj["Key"].endswith(".jsonl.out")
        ]

        # Connect to the SQLite database
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()

        for file_key in output_files:
            # Download the file
            response = s3.get_object(Bucket=BUCKET, Key=file_key)
            content = response["Body"].read().decode("utf-8")

            # Process each line in the file
            for line in content.split("\n"):
                if line.strip():
                    try:
                        result = ModelResult(line, level)
                        update_database(cur, result)
                    except json.JSONDecodeError:
                        logging.error("Failed to parse JSON: %s", line)
                    except ValueError as e:
                        logging.error("Validation error: %s", e)

        # Commit changes and close the connection
        conn.commit()
        conn.close()

        logging.info("Processed all results and updated the database")

    except ClientError as e:
        logging.error("Error processing results: %s", e)
        raise


################################################################################
# Main function


async def main() -> None:
    """Main function to classify news segments."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["chat", "upload", "start_job", "process"])
    parser.add_argument("level", choices=["issues", "topics"])
    parser.add_argument("--start-date", type=datetime.date.fromisoformat)
    parser.add_argument("--end-date", type=datetime.date.fromisoformat)
    parser.add_argument("--log", default="WARNING")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--randomize", action="store_true")
    parser.add_argument("--ids-file", type=str)
    parser.add_argument("--job-name", type=str)
    args = parser.parse_args()

    # If starting a job, check for job name
    if args.action == "start_job" and not args.job_name:
        parser.error("Please provide a job name")

    # If using chat mode and IDs file is not provided, start and end dates must be provided
    if (
        args.action == "chat"
        and not args.ids_file
        and not args.start_date
        and not args.end_date
    ):
        parser.error(
            "For chat mode, either --ids-file or --start_date and --end_date must be provided"
        )

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "classify.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if args.action == "upload":
        # Setup the SQLite connection
        conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row

        # Get segments to classify
        with conn:
            cur = conn.cursor()
            if args.level == "issues":
                query = """
                SELECT id, strftime('%Y', date) AS year, date, outlet, title, abstract
                FROM segments
                WHERE date BETWEEN ? AND ?
                AND NOT empty
                AND NOT commercial
                AND program LIKE '%Evening News'
                """
            else:  # topics
                query = """
                SELECT id, strftime('%Y', date) AS year, date, outlet, title, abstract
                FROM segments
                WHERE topic_id IS NULL
                AND date BETWEEN ? AND ?
                AND NOT empty
                AND NOT commercial
                AND program LIKE '%Evening News'
                """
            cur.execute(query, (args.start_date, args.end_date))
            segments = cur.fetchall()

        # Generate and upload prompts
        generate_prompts(segments, args.level)
        conn.close()

    elif args.action == "start_job":
        # Start the Bedrock batch job
        start_bedrock_batch_job(args.job_name, args.level)

    elif args.action == "process":
        # Process the results and update the database
        process_results(args.level)

    elif args.action == "chat":
        # Setup the async SQLite connection
        conn = await aiosqlite.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)

        # Get segments to classify
        if args.ids_file:
            with open(args.ids_file) as f:
                segments = [
                    {"id": int(s1), "year": int(s2)}
                    for s1, s2 in map(
                        lambda x: tuple(x.strip().split(",")), f.readlines()
                    )
                ]
        else:
            async with conn.cursor() as cur:
                cur.row_factory = aiosqlite.Row
                if args.level == "issues":
                    query = """
                    SELECT id, strftime('%Y', date) AS year
                    FROM segments
                    WHERE date BETWEEN ? AND ?
                    AND NOT empty
                    AND NOT commercial
                    AND program LIKE '%Evening News'
                    """
                else:
                    query = """
                    SELECT id, strftime('%Y', date) AS year
                    FROM segments
                    WHERE topic_id IS NULL
                    AND date BETWEEN ? AND ?
                    AND NOT empty
                    AND NOT commercial
                    AND program LIKE '%Evening News'
                    """
                if args.randomize:
                    query += " ORDER BY RANDOM()"
                query += " LIMIT ?"
                await cur.execute(query, (args.start_date, args.end_date, args.limit))
                segments = await cur.fetchall()

        # Initialize the model context
        m_context = ModelContext(SERVICE, MODEL)
        await m_context.initialize()

        # Generate the classification tasks
        if args.level == "issues":
            tasks = [
                classify_issue(segment["id"], int(segment["year"]), conn, m_context)
                for segment in segments
            ]
        else:
            tasks = [
                classify_topic(segment["id"], int(segment["year"]), conn, m_context)
                for segment in segments
            ]
        await tqdm.gather(*tasks)

        # Close connections
        await conn.close()
        await m_context.close()

        # Print usage statistics
        print(calculate_cost(SERVICE, MODEL))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.exception(e)
        print(calculate_cost(SERVICE, MODEL))
        raise e
