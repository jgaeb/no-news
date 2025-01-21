# No News Is Good News? The Declining Information Value of Broadcast News in America 
---
Data and replication materials for Gaebler, Westwood, Iyengar, and Goel (2025)
"No News Is Good News? The Declining Information Value of Broadcast News in
America"

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

The following scripts are also provided:
* `load_segments.R`: Loads CSV data into the sqlite database.
* `events.py`: Identifies events in the data.
* `fine_tune_events.py`: Generates training data for fine-tuning the event
  classifier.
* `issues.py`: Identifies issues in the data.
* `topics.py`: Identifies topics in the data.
* `embed.py`: Generates word embeddings of events, issues, and topics.
* `classify.py`: Classifies segments into issues and topics.
* `other.py`: Classifies non-issue hard news.
Python scripts were run using `python` version 3.12.8 and the packages listed
in `requirements.txt`.

**NOTE:** Titles and abstracts are not included in the public data; features
calculated using these fields have been precomputed and are included in the
database. . The `id` field of the `segments` table in the database references
the segments' unique identifiers in the
[Vanderbilt Television News Archive (VTVNA)](https://tvnews.vanderbilt.edu/).
