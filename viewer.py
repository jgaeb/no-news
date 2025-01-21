import argparse
import os
import pathlib
import random
import sqlite3
from datetime import datetime

# Create the argument parser
parser = argparse.ArgumentParser(
    description="Generate a viewer for news segments from a SQLite database"
)
parser.add_argument("year", type=int, help="The year to fetch news segments from")
args = parser.parse_args()

# Connect to the SQLite database
conn = sqlite3.connect(pathlib.Path("data", "no-news.db"))
cursor = conn.cursor()

# Fetch 100 random non-commercial, non-empty segments from the year
cursor.execute(
    f"""
    SELECT id, outlet, program, date, title, abstract, reporter, duration, event_id
    FROM segments
    WHERE strftime('%Y', date) = '{args.year}'
    AND outlet IN ('ABC', 'CBS', 'NBC')
    AND program LIKE '%Evening News%'
    AND commercial = 0
    AND empty = 0
    AND hard_news = 1
    AND issue_id = -1
    ORDER BY RANDOM()
    LIMIT 100
"""
)
segments = cursor.fetchall()

# Fetch all issues from the specified year
cursor.execute(
    f"""
    SELECT title, description
    FROM issues
    WHERE year = {args.year}
"""
)
issues = cursor.fetchall()

# Close the database connection
conn.close()

# Generate the HTML content
html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>News Segments Viewer</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            display: flex;
        }}
        #sidebar {{
            width: 300px;
            height: 100vh;
            overflow-y: auto;
            background-color: #f0f0f0;
            padding: 20px;
            box-sizing: border-box;
            position: fixed;
        }}
        #content {{
            margin-left: 300px;
            padding: 20px;
            flex-grow: 1;
        }}
        .segment {{
            background-color: #ffffff;
            border: 1px solid #ddd;
            padding: 15px;
            margin-bottom: 20px;
            border-radius: 5px;
        }}
        .segment-with-event {{
            background-color: #e6f2ff;
            border-color: #4d94ff;
        }}
        h1, h2 {{
            color: #333;
        }}
        .issue {{
            margin-bottom: 15px;
        }}
    </style>
</head>
<body>
    <div id="sidebar">
        <h2>Issues from {args.year}</h2>
        {"".join(f'<div class="issue"><h3>{issue[0]}</h3><p>{issue[1]}</p></div>' for issue in issues)}
    </div>
    <div id="content">
        <h1>News Segments from {args.year}</h1>
        {"".join(f'''
        <div class="segment {'segment-with-event' if segment[8] is not None else ''}">
            <h2>{i}: {segment[4]}</h2>
            <p><strong>Outlet:</strong> {segment[1]}</p>
            <p><strong>Program:</strong> {segment[2]}</p>
            <p><strong>Date:</strong> {segment[3]}</p>
            <p><strong>Reporter:</strong> {segment[6]}</p>
            <p><strong>Duration:</strong> {segment[7]} seconds</p>
            <p><strong>Abstract:</strong> {segment[5]}</p>
            <p><strong>Event ID:</strong> {segment[8] if segment[8] is not None else 'N/A'}</p>
        </div>
        ''' for i, segment in enumerate(segments))}
    </div>
</body>
</html>
"""

# Write the HTML content to a file
with open(os.path.join("viewer", f"{args.year}.html"), "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"HTML file 'viewer/{args.year}.html' generated successfully.")
