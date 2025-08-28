#!/usr/bin/env python
"""Python script for generating the issues corresponding to a year's news."""
import argparse
import asyncio
import logging
import pathlib
import re
import sqlite3
from datetime import date, timedelta
from textwrap import dedent
from typing import Optional, Tuple

from pydantic import BaseModel, ValidationError
from ratelimit import limits, sleep_and_retry  # type: ignore
from tqdm import tqdm  # type: ignore

from _models import ModelContext, calculate_cost

# Initialize API limits
SERVICE = "OpenAI"
MODEL = "gpt-4"

# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

################################################################################

SYSTEM_MESSAGE_ISSUES = """
You summarize important issues people would think are important national problems based
on what was in the news. You respond with a JSON object as follows:
```json
{
    "issues": [{"title": str, "description": str}, ...],
    "revisions": [{"old_title": str, "new_title": str}, ...]
}
```
The string in the `title` field is a short title for the issue (e.g., "Stagflation" or
"The Iraq War") and the string in the `description` field should be a brief (but
accurate) one-sentence summary of the issue.

It is *very* important that the titles of issues maintain continuity across years (even
though the descriptions may change), so if they are available, you will be provided with
issues from the previous year to use as a reference. You should not, e.g., write "The
War in Iraq" in one year and "The Iraq War" in another year.

Sometimes the same issue still ends up with a different title in the current and
previous years. When that happens, you can submit a revision object with the old title
and the new title to ensure continuity. For example, if the issue was "The War in Iraq"
last year and "The Iraq War" this year, you would submit:
```json
{
    "old_title": "The War in Iraq",
    "new_title": "The Iraq War"
}
```
However, be careful—when you revise a title, you should make sure that the new title
would have been a reasonable title for the issue in the previous years as well.

If there are no revisions, do not include the `revisions` field in your response.
""".strip()

SYSTEM_MESSAGE_REVISIONS = """
You are helping us build a database of issues that were important in the news each year.
Sometimes, it is necessary to revise the titles of issues to maintain continuity across
years. You will be presented with a proposed issue merger and asked to approve or reject
it, based on whether you think the issues are the same or different. Your response
should take the form of a JSON object as follows:
```json
{
    "summary": str,
    "title": str,
    "approved": bool
}
```
The key here is that your job is to ensure that:
* The issue title makes sense for all years.
* The issues really are the "same." For example, "The Iraq War" and "The War in Iraq"
  are the same issue, but "The Iraq War" and "The Vietnam War" are not.

If you approve the merger, set `approved` to `true`. If you reject it, set `approved`
to `false`. If you approve the merger, the issue will be renamed to the title you
provide. Provide a short summary of your reasoning either way in the `summary` field.
""".strip()


def get_prompt_issues(
    top_stories: list[Tuple[str, str]],
    previous_issues: list[Tuple[str, str]],
    year: str,
) -> str:
    """Generate the "issues" prompt for the API."""
    # Create a dictionary where the keys are distinct issues and the values are the
    # years in which they appeared
    issue_years: dict[str, list[str]] = {}
    for title, year_ in previous_issues:
        if title not in issue_years:
            issue_years[title] = []
        issue_years[title].append(year_)

    top_stories_str = "\n".join(
        [f"{date}: {description}" for date, description in top_stories]
    )
    previous_issues_str = "\n".join(
        [
            f"{i:3}. {title}: {', '.join(map(str, years))}"
            for i, (title, years) in enumerate(issue_years.items(), start=1)
        ]
    )
    prompt = (
        f"{top_stories_str}\n"
        "Based on this list of events, what do you think were ten to fifteen most important "
        f"issues people would think are important national problems in {year}? Try to be specific "
        '("Inflation" rather than "The Economy"; "The Vietnam War" rather than '
        '"Foreign Policy"; "Gay Marriage" rather than "Social Issues").\n'
    )
    if previous_issues:
        prompt += (
            "You can come up with brand new issues or borrow from the most important "
            f"issues from previous years:\n{previous_issues_str}\n"
            "If you do think an issue was important for this year and a previous year, "
            "be sure to copy the *issue* exactly. It's important that the issues "
            "maintain continuity across years.\n"
        )
    prompt += (
        "NOTE: A small number of these stories may be 'hallucinated.' Please ignore "
        f"stories that are not relevant to events in {year}, or occurred at a "
        "different time."
    )

    return dedent(prompt)


def get_prompt_revisions(
    revisions: list[Tuple[str, str]],
    previous_issues: list[Tuple[str, str]],
    old_title: str,
    new_title: str,
) -> str:
    """Generate the "revisions" prompt for the API."""

    # Gather the issues with the old title
    old_title_issues = [
        {"title": title, "description": description}
        for title, description in previous_issues
        if title == old_title
    ]
    old_title_str = "\n".join(
        [
            f"{i:3}. {title}: {description}"
            for i, (title, description) in enumerate(old_title_issues, start=1)
        ]
    )

    # Gather the issues with the new title
    new_title_issues = [
        {"title": title, "description": description}
        for title, description in previous_issues
        if title == new_title
    ]
    new_title_str = "\n".join(
        [
            f"{i:3}. {title}: {description}"
            for i, (title, description) in enumerate(new_title_issues, start=1)
        ]
    )

    # If either the old or new title is not in the database, throw a warning
    if not old_title_issues or not new_title_issues:
        logging.warning(
            "Old title %s or new title %s not in database", old_title, new_title
        )
        ValueError(f"Old title {old_title} or new title {new_title} not in database")

    prompt = (
        "Should the following issues be merged? If so, what should the new title be?\n"
        f"{old_title}:\n{old_title_str}\n"
        f"{new_title}:\n{new_title_str}\n"
        "If you think the issues are the same, set `approved` to `true` and provide a new title. "
        "If you think the issues are different, set `approved` to `false`.\n"
        "If you approve the merger, the issue will be renamed to the title you provide."
    )

    return dedent(prompt)


################################################################################
# Pydantic response objects


class Issue(BaseModel):
    """Pydantic model for a single issue."""

    title: str
    description: str


class Revision(BaseModel):
    """Pydantic model for a single revision."""

    old_title: str
    new_title: str


class Response(BaseModel):
    """Pydantic model for the response from the API."""

    issues: list[Issue]
    revisions: list[Revision] = []


class RevisionResponse(BaseModel):
    """Pydantic model for the response to a revision."""

    summary: str
    title: Optional[str]
    approved: bool


################################################################################


def get_top_stories(year: str) -> list[Tuple[str, str]]:
    """Get up to the top three stories from the database for each date in a given
    year."""
    start_date = date(int(year), 1, 1)
    end_date = date(int(year), 12, 31)

    delta = timedelta(days=1)
    top_stories = []

    with sqlite3.connect(DATABASE) as conn:
        while start_date <= end_date:
            query = """
            SELECT date, description
            FROM events
            WHERE date = ?
            ORDER BY top_story DESC, id
            LIMIT 3
            """
            cur = conn.cursor()
            cur.execute(query, (start_date.isoformat(),))
            for date_str, description in cur.fetchall():
                top_stories.append((date_str, description))

            start_date += delta

    return top_stories


def get_previous_issues(year: str) -> list[Tuple[str, str]]:
    """Get the previous year's issues from the database."""
    with sqlite3.connect(DATABASE) as conn:
        query = """
        SELECT title, year
        FROM issues
        WHERE year < ?
        ORDER BY title, year
        """
        cur = conn.cursor()
        cur.execute(query, (year,))
        return cur.fetchall()


def check_year(year: str) -> bool:
    """Checks if the year is already in the database."""
    with sqlite3.connect(DATABASE) as conn:
        query = """
        SELECT year
        FROM issues
        WHERE year = ?
        """
        cur = conn.cursor()
        cur.execute(query, (year,))
        return cur.fetchone() is not None


################################################################################


@sleep_and_retry
@limits(calls=1, period=30)
async def generate_issues(
    year: str, m_context: ModelContext, initialize: bool, close: bool
) -> None:
    """Generate the issues for a given year."""
    # If it's the first year, initialize the model context
    if initialize:
        await m_context.initialize()

    # Check if the year is already in the database
    if check_year(year):
        logging.warning("Year %s already in database", year)
        if close:
            await m_context.close()
        return

    # Generate the prompt
    top_stories = get_top_stories(year)
    previous_issues = get_previous_issues(year)
    prompt = get_prompt_issues(top_stories, previous_issues, year)

    # Call the API
    async with m_context as m:
        response = await m.chat(
            system=SYSTEM_MESSAGE_ISSUES,
            prompt=prompt,
            json_start='{"issues": [',
            temperature=1,
        )

    # Parse the response
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()
        if response is None:
            logging.warning("Error getting response for %s", year)
            if close:
                await m_context.close()
            raise ValueError(f"Error getting response for {year}")
        try:
            # Extract JSON from surounding text in case the model adds extra text
            response_str = re.search(
                r"\{.*\}", response, re.DOTALL | re.MULTILINE
            ).group()

            response = Response.parse_raw(response_str)

            # Log the issues
            logging.info("Issues for %s: %s", year, response.issues)

            # Save the issues to the database
            for issue in response.issues:
                cur.execute(
                    """
                    INSERT INTO issues (year, title, description)
                    VALUES (?, ?, ?)
                    """,
                    (year, issue.title, issue.description),
                )
        except ValidationError as e:
            logging.error("Error parsing response: %s", response_str)
            logging.error(e)
            raise ValueError(f"Error parsing response: {response_str}") from e

            # Close the model context if it's the last year
            if close:
                await m_context.close()

        # If there are revisions, check them
        for revision in response.revisions:
            # Log the revision
            logging.info("Revision for %s: %s", year, revision)

            # Get the prompt for the revision
            try:
                prompt = get_prompt_revisions(
                    previous_issues,
                    previous_issues,
                    revision.old_title,
                    revision.new_title,
                )
            # If the old or new title is not in the database, skip the revision
            except ValueError as e:
                logging.warning("Skipping revision for %s: %s, %s", year, revision, e)
                continue

            # Call the API
            async with m_context as m:
                revision_response = await m.chat(
                    system=SYSTEM_MESSAGE_REVISIONS,
                    prompt=prompt,
                    json_start='{"summary": "',
                    temperature=1,
                )

            # Parse the response
            if revision_response is None:
                logging.warning("Error getting revision response for %s", year)
                if close:
                    await m_context.close()
                raise ValueError(f"Error getting revision response for {year}")
            try:
                # Extract JSON from surounding text in case the model adds extra text
                revision_response_str = re.search(
                    r"\{.*\}", revision_response, re.DOTALL | re.MULTILINE
                ).group()

                revision_response = RevisionResponse.parse_raw(revision_response_str)

                # Log the revision response
                logging.info("Revision response for %s: %s", year, revision_response)

                # If the revision is approved, update the database
                if revision_response.approved:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE issues
                        SET title = ?
                        WHERE title = ?
                        """,
                        (revision_response.title, revision.old_title),
                    )
            except ValidationError as e:
                logging.error(
                    "Error parsing revision response: %s", revision_response_str
                )
                logging.error(e)
                raise ValueError(
                    f"Error parsing revision response: {revision_response_str}"
                ) from e

        # Commit the changes to the database
        conn.commit()

    # If it's the last year, close the model context
    if close:
        await m_context.close()


################################################################################
# Main function


def main() -> None:
    """Main function to generate issues for all years."""
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    parser.add_argument("--log", default="INFO")
    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        filename=pathlib.Path("logs", "issues.log"),
        level=args.log,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Generate all years between start and end, not inclusive
    start = args.start
    end = args.end
    years = [str(year) for year in range(start, end)]
    logging.info(
        "Generating issues for %i years, %s to %s", len(years), years[0], years[-1]
    )

    # Initialize the model context
    m_context = ModelContext(SERVICE, MODEL)

    # Generate the issues for all years
    for year in tqdm(years):
        initialize = year == years[0]
        close = year == years[-1]
        asyncio.run(generate_issues(year, m_context, initialize, close))

    # Print usage statistics
    print(calculate_cost(SERVICE, MODEL))


if __name__ == "__main__":
    main()
