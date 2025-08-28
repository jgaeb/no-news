#!/usr/bin/env python3
"""Python for generating qualtrics surveys for validation studies."""

import argparse
import os
import pathlib
import random
import sqlite3
import string
from datetime import datetime

import requests
from jinja2 import Environment, FileSystemLoader
from markdown import markdown

################################################################################
# Database and date handling


def convert_date(s):
    """Convert a string to a date."""
    return datetime.strptime(s.decode("ascii"), "%Y-%m-%d").date()


def adapt_date(date):
    """Adapt a date to a string."""
    return date.isoformat()


# Define the database
DATABASE = pathlib.Path("data", "no-news.db")

# Register the SQLite date adapter
sqlite3.register_adapter(datetime.date, adapt_date)
sqlite3.register_converter("DATE", convert_date)

# Study Configuration
CLASSIFICATIONS_PER_SEGMENT = 10  # How many people classify each segment
SEGMENTS_TO_SAMPLE = 100  # How many segments to include in the study
SEGMENTS_PER_PARTICIPANT = 3  # How many segments each participant sees
MINUTES_PER_TASK = 8  # Estimated time per task in minutes
PAYMENT_IN_DOLLARS = 1.60  # Payment per participant in dollars

ISSUES = {}
with sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
    conn.row_factory = sqlite3.Row
    TOPICS = conn.execute("SELECT id, title, description FROM topics").fetchall()
    for year in range(1968, 2025):
        ISSUES[str(year)] = conn.execute(
            "SELECT id, title, description FROM issues WHERE year = ?",
            (year,),
        ).fetchall()

################################################################################
# Load the Jinja2 environment for HTML templates

ENV = Environment(
    loader=FileSystemLoader(pathlib.Path("templates")),
    trim_blocks=True,
    lstrip_blocks=True,
)


class QualtricsValidationSurvey:
    def __init__(self, api_token, datacenter):
        """
        Initialize Qualtrics API client

        Args:
            api_token: Your Qualtrics API token
            datacenter: Your datacenter (e.g., 'harvard.pdx1.qualtrics.com')
        """
        self.api_token = api_token
        self.base_url = f"https://{datacenter}/API/v3"
        self.headers = {"X-API-TOKEN": api_token, "Content-Type": "application/json"}

    def list_surveys(self, limit=50):
        """
        List existing surveys

        Args:
            limit: Maximum number of surveys to return
        """
        url = f"{self.base_url}/surveys"
        params = {"limit": limit}

        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()

        surveys = response.json()["result"]["elements"]

        if not surveys:
            print("No surveys found.")
            return

        print(f"\nFound {len(surveys)} surveys:")
        print("-" * 80)
        print(f"{'Survey ID':<20} {'Name':<40} {'Status':<10} {'Created':<10}")
        print("-" * 80)

        for survey in surveys:
            created_date = survey.get("creationDate", "Unknown")[
                :10
            ]  # Just the date part
            status = survey.get("isActive", "Unknown")
            status_text = "Active" if status else "Inactive"

            print(
                f"{survey['id']:<20} {survey['name'][:39]:<40} {status_text:<10} {created_date:<10}"
            )

        print("-" * 80)

    def delete_survey(self, survey_id):
        """
        Delete a survey with confirmation

        Args:
            survey_id: The survey ID to delete
            confirmation_string: Required confirmation string
        """
        # Generate random confirmation string
        required_confirmation = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=8)
        )

        print(f"\n⚠️  WARNING: You are about to permanently delete survey: {survey_id}")
        print(f"To confirm deletion, type exactly: {required_confirmation}")

        user_input = input("Confirmation: ").strip()

        if user_input != required_confirmation:
            print("❌ Confirmation string did not match. Deletion cancelled.")
            return False

        url = f"{self.base_url}/survey-definitions/{survey_id}"

        try:
            response = requests.delete(url, headers=self.headers)
            response.raise_for_status()
            print(f"✅ Survey {survey_id} deleted successfully!")
            return True
        except requests.exceptions.HTTPError as e:
            print(f"❌ Error deleting survey: {e}")
            if response.status_code == 404:
                print("Survey not found. It may have already been deleted.")
            return False

    def create_survey(self, survey_name, segments_data):
        """
        Create a complete validation survey with dual classification

        Args:
            survey_name: Name for the survey
            segments_data: List of segment dictionaries (will be sampled)

        Returns:
            survey_id: The created survey ID
        """

        # Use provided options or defaults
        topics = TOPICS
        issues = ISSUES

        # Sample segments if we have more than requested
        if len(segments_data) > SEGMENTS_TO_SAMPLE:
            segments_data = random.sample(segments_data, SEGMENTS_TO_SAMPLE)
            print(
                f"Sampled {SEGMENTS_TO_SAMPLE} segments from {len(segments_data)} available"
            )

        print(f"Creating survey '{survey_name}'...")

        # Step 1: Create the survey
        survey_id = self._create_base_survey(survey_name)

        # Step 2: Create consent form
        consent_block_id, consent_question_id = self._add_consent_form(survey_id)

        # Step 3: Add intro block
        intro_block_id = self._add_instructions_block(survey_id)

        # Step 4: Create a quota group
        quota_group_id = self._create_quota_group(survey_id)

        # Step 5: Create blocks for each segment
        block_ids = [
            self._add_segment_block(survey_id, segment, topics, issues, quota_group_id)
            for segment in segments_data
        ]

        # Step 6: Set up survey flow with randomizer
        self._setup_survey_flow(
            survey_id, block_ids, consent_block_id, consent_question_id, intro_block_id
        )

        print("\n🎉 Survey created successfully!")
        print(f"Survey ID: {survey_id}")
        print("Configuration:")
        print(f"  - {len(segments_data)} segments")
        print(f"  - {SEGMENTS_PER_PARTICIPANT} segments per participant")
        print(f"  - {CLASSIFICATIONS_PER_SEGMENT} classifications per segment")
        print(f"  - {len(topics)} topic options")
        print(f"  - {len(issues)} issue options")
        print(
            f"  - Expected participants needed: ~{(len(segments_data) * CLASSIFICATIONS_PER_SEGMENT) // SEGMENTS_PER_PARTICIPANT + 10}"
        )

        return survey_id

    def _create_base_survey(self, survey_name):
        """Create the base survey"""
        url = f"{self.base_url}/survey-definitions"

        data = {"SurveyName": survey_name, "Language": "EN", "ProjectCategory": "CORE"}

        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()

        survey_id = response.json()["result"]["SurveyID"]
        print(f"✓ Created base survey: {survey_id}")
        return survey_id

    def _add_consent_form(self, survey_id):
        """Add a consent form block to the survey"""

        # Create consent form block
        url = f"{self.base_url}/survey-definitions/{survey_id}/blocks"

        block_data = {
            "Type": "Standard",
            "Description": "Consent Form",
            "BlockElements": [],
        }

        response = requests.post(url, headers=self.headers, json=block_data)
        response.raise_for_status()

        block_id = response.json()["result"]["BlockID"]

        # Load the consent form from `consent_form.md` and convert to HTML
        consent_md = ENV.get_template("consent_form.md")
        consent_html = markdown(
            consent_md.render(
                segments_per_participant=SEGMENTS_PER_PARTICIPANT,
                minutes_per_task=MINUTES_PER_TASK,
                payment_in_dollars=PAYMENT_IN_DOLLARS,
            )
        )

        # Add consenting and non-consenting options
        choices = []
        choices.append({"Display": "I consent to take part in this study."})
        choices.append({"Display": "I do not consent to take part in this study."})
        choice_order = ["0", "1"]

        # Add consent question
        url = f"{self.base_url}/survey-definitions/{survey_id}/questions?blockId={block_id}"
        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "BELOW",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": "consent",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": "Consent",
            "QuestionText": consent_html,
            "QuestionType": "MC",
            "Selector": "SAVR",
            "SubSelector": "TX",
            "Choices": choices,
            "ChoiceOrder": choice_order,
            "Validation": {"Settings": {"ForceResponse": "ON"}},
        }
        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        question_id = response.json()["result"]["QuestionID"]

        print(f"✓ Created consent form block with ID: {block_id}")

        return block_id, question_id

    def _add_instructions_block(self, survey_id):
        """Add introduction block with instructions"""

        # Create intro block
        url = f"{self.base_url}/survey-definitions/{survey_id}/blocks"

        block_data = {
            "Type": "Standard",
            "Description": "Instructions",
            "BlockElements": [],
        }

        response = requests.post(url, headers=self.headers, json=block_data)
        response.raise_for_status()

        block_id = response.json()["result"]["BlockID"]

        # Load the instructions from `data/instructions.md` and convert to HTML
        instructions_md = ENV.get_template("instructions.md")
        instructions_html = markdown(instructions_md.render())

        # Add intro question
        url = f"{self.base_url}/survey-definitions/{survey_id}/questions?blockId={block_id}"

        question_data = {
            "Configuration": {"QuestionDescriptionOption": "UseText"},
            "DataExportTag": "instructions",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": "Instructions",
            "QuestionText": instructions_html,
            "QuestionType": "DB",
            "Selector": "TB",
            "Validation": {"Settings": {}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        print("✓ Created instructions block")

        return block_id

    def _create_quota_group(self, survey_id):
        """Create a quota group for the survey"""

        url = f"{self.base_url}/survey-definitions/{survey_id}/quotagroups"
        quota_group_data = {
            "Name": f"Validation Quota Group for {survey_id}",
            "MultipleMatch": "PlaceInAll",
            "Public": False,
            "Selected": True,
        }

        response = requests.post(url, headers=self.headers, json=quota_group_data)
        response.raise_for_status()

        quota_group_id = response.json()["result"]["QuotaGroupID"]

        print(f"✓ Created quota group with ID: {quota_group_id}")

        return quota_group_id

    def _add_segment_block(self, survey_id, segment, topics, issues, quota_group_id):
        """Create a block for each segment, populating it with the abstract and
        questions for the topic and issue classification"""

        # Create the block
        url = f"{self.base_url}/survey-definitions/{survey_id}/blocks"
        block_data = {
            "Type": "Standard",
            "Description": f"Segment {segment['id']}",
            "BlockElements": [],
        }

        response = requests.post(url, headers=self.headers, json=block_data)
        response.raise_for_status()

        block_id = response.json()["result"]["BlockID"]

        # Load the segment abstract template from `data/segment.md` and convert
        # to HTML
        segment_md = ENV.get_template("segment.md")
        segment_html = markdown(
            segment_md.render(
                segment=segment, date=segment["date"].strftime("%b %d, %Y")
            )
        )

        # Add the abstract as a "descriptive text" question
        url = f"{self.base_url}/survey-definitions/{survey_id}/questions?blockId={block_id}"
        question_data = {
            "Configuration": {"QuestionDescriptionOption": "UseText"},
            "DataExportTag": f"segment_{segment['id']}_abstract",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Abstract",
            "QuestionText": segment_html,
            "QuestionType": "DB",
            "Selector": "TB",
            "Validation": {"Settings": {}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Inquire whether the segment is soft or hard news
        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "SIDE",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": f"segment_{segment['id']}_news_type",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} News Type",
            "QuestionText": ("Is this segment primarily hard news or soft news?"),
            "QuestionType": "MC",
            "Selector": "SAHR",
            "SubSelector": "TX",
            "Choices": {
                "1": {"Display": "Hard News (e.g., politics, economics, crime)"},
                "2": {
                    "Display": "Soft News (e.g., entertainment, sports, human interest)"
                },
            },
            "ChoiceOrder": ["1", "2"],
            "Validation": {"Settings": {"ForceResponse": "ON"}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Load the topic display template from `data/topics.md` and convert to HTML
        topics_md = ENV.get_template("topics.md")
        topics_html = markdown(topics_md.render(topics=topics))

        # Add the topics display as a "descriptive text" question
        question_data = {
            "Configuration": {"QuestionDescriptionOption": "UseText"},
            "DataExportTag": f"segment_{segment['id']}_topics",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Topics",
            "QuestionText": topics_html,
            "QuestionType": "DB",
            "Selector": "TB",
            "Validation": {"Settings": {}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Build choices from topic options
        choices = {}
        choice_order = []
        for i, topic in enumerate(topics, 1):
            choice_id = str(i)
            choices[choice_id] = {"Display": f"{topic['title']}"}
            choice_order.append(choice_id)

        # Add "Other" option
        other_id = str(len(topics) + 1)
        choices[other_id] = {"Display": "No topic matches this abstract."}
        choice_order.append(other_id)

        # Add topic classification question
        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "SIDE",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": f"segment_{segment['id']}_topic_primary",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Topic Classification (Primary)",
            "QuestionText": "What is the primary topic of this segment?",
            "QuestionType": "MC",
            "Selector": "DL",
            "SubSelector": "TX",
            "Choices": choices,
            "ChoiceOrder": choice_order,
            "Validation": {"Settings": {"ForceResponse": "ON"}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "SIDE",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": f"segment_{segment['id']}_topic_secondary",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Topic Classification (Secondary)",
            "QuestionText": "<strong>(Optional)</strong> What is the secondary topic of this segment?",
            "QuestionType": "MC",
            "Selector": "DL",
            "SubSelector": "TX",
            "Choices": choices,
            "ChoiceOrder": choice_order,
            "Validation": {"Settings": {"ForceResponse": "OFF"}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Load the issues display template from `templates/issues.md` and convert to HTML
        issues_md = ENV.get_template("issues.md")
        issues_html = markdown(
            issues_md.render(issues=issues[segment["year"]], year=segment["year"])
        )

        # Add the issues display as a "descriptive text" question
        question_data = {
            "Configuration": {"QuestionDescriptionOption": "UseText"},
            "DataExportTag": f"segment_{segment['id']}_issues",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Issues",
            "QuestionText": issues_html,
            "QuestionType": "DB",
            "Selector": "TB",
            "Validation": {"Settings": {}},
        }
        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Build choices from issue options
        choices = {}
        choice_order = []
        for i, issue in enumerate(issues[segment["year"]], 1):
            choice_id = str(i)
            choices[choice_id] = {"Display": f"{issue['title']}"}
            choice_order.append(choice_id)

        # Add "Other" option
        other_id = str(len(issues[segment["year"]]) + 1)
        choices[other_id] = {
            "Display": "This abstract does not match any of the issues."
        }
        choice_order.append(other_id)

        # Add issue classification question
        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "BELOW",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": f"segment_{segment['id']}_issue_primary",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Issue Classification (Primary)",
            "QuestionText": "What is the primary issue of this segment?",
            "QuestionType": "MC",
            "Selector": "DL",
            "SubSelector": "TX",
            "Choices": choices,
            "ChoiceOrder": choice_order,
            "Validation": {"Settings": {"ForceResponse": "ON"}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        question_id = response.json()["result"]["QuestionID"]

        question_data = {
            "Configuration": {
                "QuestionDescriptionOption": "UseText",
                "TextPosition": "inline",
                "ChoiceColumnWidth": 25,
                "RepeatHeaders": "none",
                "WhiteSpace": "ON",
                "LabelPosition": "BELOW",
                "NumColumns": 1,
                "MobileFirst": True,
            },
            "DataExportTag": f"segment_{segment['id']}_issue_secondary",
            "DefaultChoices": False,
            "Language": [],
            "QuestionDescription": f"Segment {segment['id']} Issue Classification (Secondary)",
            "QuestionText": "<strong>(Optional)</strong> What is the secondary issue of this segment?",
            "QuestionType": "MC",
            "Selector": "DL",
            "SubSelector": "TX",
            "Choices": choices,
            "ChoiceOrder": choice_order,
            "Validation": {"Settings": {"ForceResponse": "OFF"}},
        }

        response = requests.post(url, headers=self.headers, json=question_data)
        response.raise_for_status()

        # Add a quota for issue question completion
        url = f"{self.base_url}/survey-definitions/{survey_id}/quotas?quotaGroupId={quota_group_id}"
        quota_data = {
            "Name": f"Segment {segment['id']} Topic Quota",
            "LogicType": "Simple",
            "Occurrences": CLASSIFICATIONS_PER_SEGMENT,
            "QuotaAction": "DontDisplayBlock",
            "OverQuotaAction": "Record",
            "ActionElement": block_id,
            "ActionInfo": {
                "0": {
                    "0": {
                        "ActionType": "DontDisplayBlock",
                        "HideBlock": block_id,
                        "Type": "Expression",
                        "LogicType": "QuotaAction",
                    },
                    "Type": "If",
                },
                "Type": "BooleanExpression",
            },
            "QuotaRealm": "Survey",
            "QuotaSchedule": None,
            "EndSurveyOptions": {
                "EndingType": "Default",
                "ResponseFlag": "QuotaMet",
                "SurveyTermination": "DefaultMessage",
                "EmailThankYou": "false",
                "ResponseSummary": "No",
                "ConfirmResponseSummary": "",
                "CountQuotas": "Yes",
                "Screenout": "No",
                "AnonymizeResponse": "No",
                "IgnoreResponse": "No",
            },
            "Logic": {
                "0": {
                    "0": {
                        "LogicType": "Question",
                        "QuestionID": question_id,
                        "QuestionIsInLoop": "no",
                        "ChoiceLocator": f"q://{question_id}/SelectableChoice/1",
                        "Operator": "Displayed",
                        "QuestionIDFromLocator": question_id,
                        "LeftOperand": f"q://{question_id}/ChoiceDisplayed/1",
                        "Type": "Expression",
                        "Description": f"Segment {segment['id']} issue classification",
                    },
                    "Type": "If",
                },
                "Type": "BooleanExpression",
            },
        }

        response = requests.post(url, headers=self.headers, json=quota_data)
        response.raise_for_status()

        print(f"✓ Created block for segment {segment['id']}")
        return block_id

    def _setup_survey_flow(
        self,
        survey_id,
        block_ids,
        consent_block_id,
        consent_question_id,
        intro_block_id,
    ):
        """Set up survey flow with randomizer"""
        url = f"{self.base_url}/survey-definitions/{survey_id}/flow"

        # Create randomizer flow
        randomizer_flow = []
        for block_id in block_ids:
            randomizer_flow.append(
                {
                    "Type": "Block",
                    "ID": block_id,
                    "FlowID": f"FL_{block_id.replace('BL_', '')}",
                    "Autofill": [],
                }
            )

        flow_data = {
            "Type": "Root",
            "FlowID": "FL_1",
            "Flow": [
                {
                    "ID": consent_block_id,
                    "Type": "Block",
                    "FlowID": "FL_consent",
                    "Autofill": [],
                },
                {
                    "Type": "Branch",
                    "FlowID": "FL_consent_branch",
                    "Description": "End survey if consent is not given",
                    "BranchLogic": {
                        "0": {
                            "0": {
                                "LogicType": "Question",
                                "QuestionID": consent_question_id,
                                "QuestionIsInLoop": "no",
                                "ChoiceLocator": f"q://{consent_question_id}/SelectableChoice/1",
                                "Operator": "Selected",
                                "QuestionIDFromLocator": consent_question_id,
                                "LeftOperand": f"q://{consent_question_id}/SelectableChoice/1",
                                "Type": "Expression",
                                "Description": "Consent not given",
                            },
                            "Type": "If",
                        },
                        "Type": "BooleanExpression",
                    },
                    "Flow": [{"Type": "EndSurvey", "FlowID": "FL_end_survey"}],
                },
                {
                    "ID": intro_block_id,
                    "Type": "Block",
                    "FlowID": "FL_intro",
                    "Autofill": [],
                },
                {
                    "Type": "BlockRandomizer",
                    "FlowID": "FL_rand",
                    "SubSet": SEGMENTS_PER_PARTICIPANT,
                    "EvenPresentation": True,
                    "Flow": randomizer_flow,
                },
            ],
            "Properties": {
                "Count": 5 + len(block_ids),
            },
        }

        response = requests.put(url, headers=self.headers, json=flow_data)
        response.raise_for_status()

        print(
            f"✓ Survey flow configured: showing {SEGMENTS_PER_PARTICIPANT} segments per participant"
        )


def get_sample_segments():
    """Generate sample segments for testing"""
    with sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
        conn.row_factory = sqlite3.Row
        query = """
        SELECT id, title, outlet, date, STRFTIME("%Y", date) AS year, abstract
        FROM segments
        WHERE NOT empty
        AND NOT commercial
        AND program LIKE '%Evening News'
        AND outlet IN ('ABC', 'CBS', 'NBC')
        ORDER BY RANDOM()
        LIMIT ?;
        """
        return conn.execute(query, (SEGMENTS_TO_SAMPLE,)).fetchall()


def cmd_create(args):
    """Handle the create subcommand - creates anonymous surveys suitable for Prolific"""
    client = QualtricsValidationSurvey(args.token, args.datacenter)

    # Get sample segments (replace with your actual data loading)
    segments = get_sample_segments()

    client.create_survey(
        survey_name=args.name,
        segments_data=segments,
    )

    print("\n🔗 Next steps:")
    print("1. Preview your survey in Qualtrics")
    print("2. Publish the survey")
    print("3. Get the anonymous link for Prolific distribution")
    print(
        f"4. Recruit ~{(len(segments) * CLASSIFICATIONS_PER_SEGMENT) // SEGMENTS_PER_PARTICIPANT + 10} participants on Prolific"
    )


def cmd_list(args):
    """Handle the list subcommand"""
    client = QualtricsValidationSurvey(args.token, args.datacenter)
    client.list_surveys(limit=args.limit)


def cmd_delete(args):
    """Handle the delete subcommand"""
    client = QualtricsValidationSurvey(args.token, args.datacenter)
    client.delete_survey(args.survey_id, args.confirm)


def main():
    parser = argparse.ArgumentParser(
        description="Create, list, and manage Qualtrics validation surveys for LLM classification studies (anonymous surveys for Prolific distribution)"
    )

    # Global arguments
    parser.add_argument(
        "--token",
        default=os.environ.get("QUALTRICS_API_TOKEN"),
        help="Qualtrics API token (default: QUALTRICS_API_TOKEN env var)",
    )
    parser.add_argument(
        "--datacenter",
        default=os.environ.get("QUALTRICS_DATACENTER"),
        help="Qualtrics datacenter URL (default: QUALTRICS_DATACENTER env var)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Create command
    create_parser = subparsers.add_parser(
        "create", help="Create a new validation survey"
    )
    create_parser.add_argument("name", help="Name for the survey")
    create_parser.set_defaults(func=cmd_create)

    # List command
    list_parser = subparsers.add_parser("list", help="List existing surveys")
    list_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum surveys to list"
    )
    list_parser.set_defaults(func=cmd_list)

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a survey")
    delete_parser.add_argument("survey_id", help="Survey ID to delete")
    delete_parser.add_argument(
        "--confirm", help="Confirmation string (will be prompted if not provided)"
    )
    delete_parser.set_defaults(func=cmd_delete)

    args = parser.parse_args()

    # Validate required arguments
    if not args.token:
        parser.error(
            "--token is required (or set QUALTRICS_API_TOKEN environment variable)"
        )
    if not args.datacenter:
        parser.error(
            "--datacenter is required (or set QUALTRICS_DATACENTER environment variable)"
        )

    if not args.command:
        parser.print_help()
        return

    # Run the appropriate command
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise


if __name__ == "__main__":
    main()
