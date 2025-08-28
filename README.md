# No News Is Good News? The Declining Information Value of Broadcast News in America
---
Data and replication materials for Gaebler, Westwood, Iyengar, and Goel (2025)
"No News Is Good News? The Declining Information Value of Broadcast News in
America".

To reproduce the analyses in our paper:
1. Make sure you have `R` version 4.4.0 and the `groundhog` package installed.
2. Unzip `data/no-news.db.gz`:
```bash
gunzip data/no-news.db.gz
```
3. Run the `R` script
```bash
Rscript analyze.R
```

## Additional Scripts

The following scripts, used to process the full data set, are also provided:
* `load_segments.R`: Loads CSV data into the sqlite database.
* `events.py`: Identifies events in the data.
* `fine_tune_events.py`: Generates training data for fine-tuning the event
  classifier.
* `issues.py`: Identifies issues in the data.
* `topics.py`: Identifies topics in the data.
* `embed.py`: Generates word embeddings of events, issues, and topics.
* `classify.py`: Classifies segments into issues and topics.
* `other.py`: Classifies non-issue hard news.
* `viewer.py`: Generates HTML visualizations of news segments with their
  classifications for manual inspection.
* `qualtrics.py`: Creates and manages Qualtrics surveys for human validation of
  model classifications.

The following environment variables are required for various scripts:
* `OPENAI_API_KEY`: OpenAI API key for GPT models
* `OPENAI_API_ORG`: OpenAI organization ID
* `ANTHROPIC_API_KEY`: Anthropic API key for Claude models
* `AWS_ACCESS_KEY_ID`: AWS access key for Bedrock
* `AWS_SECRET_ACCESS_KEY`: AWS secret key for Bedrock
* `AWS_BUCKET`: S3 bucket for batch processing
* `AWS_SERVICE_ROLE_ARN`: AWS service role ARN for Bedrock batch jobs
* `QUALTRICS_API_TOKEN`: Qualtrics API token (optional, for validation surveys)
* `QUALTRICS_DATACENTER`: Qualtrics datacenter URL (optional)

**NOTE:** Titles and abstracts are not included in the public data; features
calculated using these fields have been precomputed and are included in the
database. The `id` field of the `segments` table in the database references
the segments' unique identifiers in the
[Vanderbilt Television News Archive (VTVNA)](https://tvnews.vanderbilt.edu/).
