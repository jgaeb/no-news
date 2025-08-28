#!/usr/bin/env python
"""Python script for classifying "other" news."""
import argparse
import asyncio
import json
import logging
import os
import pathlib
import re
import sqlite3
from typing import Dict, List, Optional

import aiosqlite  # type: ignore
import boto3
import tqdm
from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError
from tqdm.asyncio import tqdm_asyncio

from _aws import format_payload
from _models import MODELS, ModelContext, calculate_cost
from _utils import handle_exceptions

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

# Initialize API limits
SERVICE = "AWS"
MODEL = "haiku"

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

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

########################################################################################

SYSTEM_MESSAGE = """
Here is a list of topics:
1.  Business news: Stock market reports, mergers and acquisitions, SEC investigations,
    stock buybacks, strikes and labor issues, etc.
2.  Government procedure: Presidential, congressional, and judicial appointments,
    recesses, vacations, resumptions, etc.
3.  Foreign politics: Foreign elections, diplomatic events, and other “peaceful”
    political news from foreign countries.
4.  Corruption: Reports of government and private corruption, financial scams and
    racketeering, and bribery.
5.  Foreign turmoil: Riots, terror attacks, crises, and disorder in foreign countries.
6.  Natural Disasters: Extreme weather (hurricanes, floods, tornadoes, typhoons,
    blizzards, etc.), earthquakes, volcanic eruptions, wildfires, landslides and
    avalanches, etc.
7.  Notices: Memorials, anniversaries, dedications, especially military; deaths, health
    status, and retirements of elder statesmen, celebrities, etc.
8.  Trials: High profile criminal and occasionally civil trials.
9.  Crime: Reports of murders and other violent crimes, shootouts, prison breaks,
    kidnappings, whereabouts of serial killers, etc.
10. Weather: Conventional weather reports for different parts of the US.
11. Transportation Disasters: Plane crashes, train derailments, barges crashing, ferries
    sinking, etc.
12. Medical and Health News: New drugs and medical technology, medical and public health
    research, disease outbreaks, etc.
13. Manmade Disasters: Oil spills, toxic dumping, industrial accidents, fires.
14. Animal attacks: Shark attacks, sting ray attacks, bear attacks, etc.
15. The Pope: Papal visits, encyclicals, etc.
16. The Queen / British Royal Family: Royal weddings, births, deaths, scandals, etc.
17. Space Program: Shuttle launches, new space technology, reports on probes, landers,
    etc.

Your job is to categorize what topics different news segments cover. You should respond
with a JSON object as follows:
```json
{
    "explanation": str,
    "topic": int,
}
```
The `explanation` field should be a brief explanation of which topic is the best fit for
the news segment, as well as an explanation of whether that topic is a good fit for the
particular news segment or not. If the news segment does not fit any of the topics you
have been provided, you should respond with `null`. The `topic` field should be the
number of the issue that best fits the news segment.
"""

########################################################################################


class ModelOutput(BaseModel):
    explanation: Optional[str] = None
    topic: Optional[int] = None


class ModelResult:
    raw_input: Dict
    record_id: int
    parsed_output: ModelOutput

    def __init__(self, raw_input: str):
        self.raw_input = json.loads(raw_input)
        self.record_id = int(self.raw_input["recordId"].lstrip("0"))
        try:
            self.parsed_output = ModelOutput.parse_raw(
                "{" + self.raw_input["modelOutput"]["content"][0]["text"]
            )
        except KeyError as e:
            raise ValueError(f"Missing key in model output: {raw_input}") from e


########################################################################################


def generate_prompts(segments: List[sqlite3.Row]) -> None:
    """Generate prompts for the OpenAI API."""
    # Initialize the s3 client
    s3 = boto3.client("s3")

    # Create a JSON object for each segment
    tokens = 0
    prompts = []
    for segment in segments:
        prompt = f"{(segment['date'])} {segment['title']}\n\n{segment['abstract']}"
        payload = json.dumps(
            {
                "recordId": f"{segment['id']:011}",
                "modelInput": format_payload(
                    system=SYSTEM_MESSAGE,
                    prompt=prompt,
                    temperature=1.0,
                    json_start='{"explanation": "',
                ),
            }
        )
        prompts.append(payload)
        tokens += (len(SYSTEM_MESSAGE) + len(prompt) + len("{") + 4) // 5

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
                Key=f"input/prompts_{i:03}.jsonl",
            )
            logging.info("Uploaded prompt chunk %s to S3", i)
            i += 1
        except ClientError as e:
            logging.error("Failed to upload prompt chunk %s to S3: %s", i, e)
            break


########################################################################################


def start_bedrock_batch_job(job_name: str) -> None:
    """Start a batch job on AWS Bedrock."""
    try:
        # Initialize the s3 client
        s3 = boto3.client("s3")

        # Get all of the json lines files in the input directory
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix="input/")
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
                "jobName": f"{job_name}-{i:03}",
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


########################################################################################


def process_results() -> None:
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
                        result = ModelResult(line)
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


def update_database(cur: sqlite3.Cursor, result: ModelResult) -> None:
    """Update the database with the parsed result."""
    try:
        cur.execute(
            """
            UPDATE segments
            SET other_id = ?
            WHERE id = ?
        """,
            (result.parsed_output.topic, result.record_id),
        )

        if cur.rowcount == 0:
            logging.warning("No segment found for ID %s", result.record_id)

    except sqlite3.Error as e:
        logging.error("Database error for record ID %s: %s", result.record_id, e)


########################################################################################


@handle_exceptions
async def chat(
    segment: sqlite3.Row, conn: aiosqlite.Connection, m_context: ModelContext
) -> None:
    """Chat with the model and update the database."""
    async with SEMAPHORE:
        # Create the prompt
        prompt = f"{segment['date']} {segment['title']}\n\n{segment['abstract']}"

        # Call the model
        async with m_context as m:
            response = await m.chat(
                system=SYSTEM_MESSAGE,
                prompt=prompt,
                json_start='{"explanation": "',
                temperature=1.0,
            )

        # Parse the response
        if response is None:
            logging.error("Empty response for segment ID %i", segment["id"])
            return
        try:
            # Extract JSON from surrounding text in case the model adds extra text
            response_str = re.search(
                r"\{.*\}", response, re.DOTALL | re.MULTILINE
            ).group()

            response = ModelOutput.parse_raw(response_str)

            # Check if the response is valid
            if response.topic is not None and not (1 <= response.topic <= 17):
                logging.error(
                    "Invalid topic %s for segment ID %i: %s",
                    response.topic,
                    segment["id"],
                    response,
                )
                return

            # Save the response to the database
            async with conn.cursor() as cur:
                # NOTE: We use the sentinel value -1 when the issue is not set
                if response.topic is None:
                    response.topic = -1
                await cur.execute(
                    """
                    UPDATE segments
                    SET other_id = ?
                    WHERE id = ?
                    """,
                    (response.topic, segment["id"]),
                )
                await conn.commit()

        except ValidationError as e:
            logging.error(
                "Error parsing response for %i: %s. Error: %s",
                segment["id"],
                response,
                e,
            )


########################################################################################


async def main() -> None:
    """Main function to classify news segments."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["upload", "start_job", "process", "chat"])
    parser.add_argument("--log", default="INFO")
    parser.add_argument("--job-name", type=str)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    # If starting a job, check for job name
    if args.action == "start_job" and not args.job_name:
        parser.error("Please provide a job name")

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "other.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Setup the SQLite connection
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    if args.action == "upload":
        # Get all of the segments that need to be classified
        with conn:
            cur = conn.cursor()
            query = """
            SELECT id, strftime('%Y', date) AS year, date, title, abstract
            FROM segments
            WHERE NOT empty
            AND NOT commercial
            AND hard_news
            AND issue_id = -1
            AND other_id IS NULL
            AND program LIKE '%Evening News'
            LIMIT ?
            """
            cur.execute(query, (args.limit,))

            segments = cur.fetchall()

        # Generate and upload prompts
        generate_prompts(segments)

    elif args.action == "start_job":
        # Start the Bedrock batch job
        start_bedrock_batch_job(args.job_name)

    elif args.action == "process":
        # Process the results and update the database
        process_results()

    elif args.action == "chat":
        # Setup the async SQLite connection
        aconn = await aiosqlite.connect(DATABASE)
        async with aconn.cursor() as cur:
            cur.row_factory = aiosqlite.Row
            query = """
            SELECT id, strftime('%Y', date) AS year, date, title, abstract
            FROM segments
            WHERE NOT empty
            AND NOT commercial
            AND hard_news
            AND issue_id = -1
            AND other_id IS NULL
            AND program LIKE '%Evening News'
            LIMIT ?
            """
            await cur.execute(query, (args.limit,))

            segments = await cur.fetchall()

        # Initialize the model context
        m_context = ModelContext(SERVICE, MODEL)
        await m_context.initialize()

        # Generate the classification tasks
        tasks = [chat(segment, aconn, m_context) for segment in segments]
        await tqdm_asyncio.gather(*tasks)

        # Close the async connection and model context
        await aconn.close()
        await m_context.close()

        # Print usage statistics
        print(calculate_cost(SERVICE, MODEL))

    # Close the connection
    conn.close()

    logging.info("Script completed successfully")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        logging.exception("An error occurred during script execution:")
        raise exc
