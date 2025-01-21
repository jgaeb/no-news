#!/usr/bin/env Rscript
suppressPackageStartupMessages(library(RSQLite))
suppressPackageStartupMessages(library(DBI))
suppressPackageStartupMessages(library(fs))
suppressPackageStartupMessages(library(lubridate))
suppressPackageStartupMessages(library(tidyverse))

# If media-bias.db does not exist, abort
if (!file_exists(path("data", "no-news.db"))) {
  message("no-news.db does not exist. Create it with `schema.sql`.")
  quit()
}

con <- DBI::dbConnect(RSQLite::SQLite(), path("data", "no-news.db"))

col_types <- cols_only(
  `News Outlet` = col_character(),
  `Program Name` = col_character(),
  Date = col_date(format = "%m/%d/%Y"),
  `Vanderbilt ID` = col_character(),
  Title = col_character(),
  Abstract = col_character(),
  Reporter = col_character(),
  Duration = col_time(format = "%H:%M:%S")
)

# Read in the data
data <- dir_ls("data") %>%
  str_subset("ALLDATA.csv") %>%
  map(read_csv, col_types = col_types, num_threads = 8, progress = FALSE) %>%
  bind_rows() %>%
  # Make column names more ergonomic
  rename(
    outlet = `News Outlet`,
    program = `Program Name`,
    date = Date,
    id = `Vanderbilt ID`,
    title = `Title`,
    abstract = `Abstract`,
    reporter = `Reporter`,
    duration = `Duration`
  ) %>%
  # Label commercials and empty segments, and fill missing columns
  mutate(
    program = if_else(is.na(program), "(UNKNOWN)", program),
    commercial = str_detect(
      title,
      r"((?i)^(\d\d?:\d\d:\d\d\s+)?(\(?.?Commercial:?\)?|^Upcoming Items.+COMMERCIALS:))"
    ),
    commercial = if_else(is.na(commercial), FALSE, commercial),
    empty = str_detect(abstract, r"(^\s*$)"),
    empty = if_else(is.na(empty), FALSE, empty),
    classified = FALSE,
    hard_news = NA,
    event_id = NA_integer_,
    issue_id = NA_integer_,
    topic_id = NA_integer_
  ) %>%
  # Convert duration to seconds using lubridate
  mutate(duration = as.integer(duration / dseconds(1))) %>%
  # Convert id to integer
  mutate(
    id = str_extract(id, r"(#\d+$)"),
    id = as.integer(str_remove(id, "^#"))
  ) %>%
  # Convert date to ISO format using lubridate
  mutate(date = format(date, "%Y-%m-%d"))


# Write to sqlite database
DBI::dbWriteTable(con, "segments", data, append = TRUE, overwrite = FALSE)
