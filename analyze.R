#!/usr/bin/env Rscript
library(groundhog)
groundhog.library("
  xtable
  patchwork
  RSQLite
  DBI
  dbplyr
  fs
  glue
  scales
  lubridate
  tidyverse
", "2025-01-01")

# Set the seed for reproducibility
set.seed(7924823)

# Set the ggplot2 theme
theme_set(theme_bw())

################################################################################
# Load the data

# Connect to the sqlite database
con <- dbConnect(RSQLite::SQLite(), path("data", "no-news.db"))

# Load the topics
topics <- tbl(con, "topics") %>%
  collect()

# Print the topics as a LaTeX table with no row names
topics %>%
  select(Topic = title, Description = description) %>%
  xtable(
    caption = "Model-generated topics.",
    label = "tab:topics",
    align = "lp{0.2\\textwidth}p{0.74\\textwidth}"
  ) %>%
  print(
    type = "latex",
    file = path("tables", "topics.tex"),
    include.rownames = FALSE,
    tabular.environment = "longtable",
    caption.placement = "top",
    floating = FALSE,
    sanitize.text.function = \(x) str_replace_all(x, c("&" = "\\&", "\\%" = "\\\\%"))
  )

# Load the issues
issues <- tbl(con, "issues") %>%
  collect()

# Print a random selection of 50 issues, along with their descriptions and years
issues %>%
  sample_n(50) %>%
  select(Year = year, Issue = title, Description = description) %>%
  arrange(Year) %>%
  xtable(
    caption = "A representative selection of 50 out of 766 model-generated issues in given years.",
    label = "tab:issues",
    align = "lp{0.03\\textwidth}p{0.2\\textwidth}p{0.69\\textwidth}"
  ) %>%
  print(
    type = "latex",
    file = path("tables", "issues.tex"),
    include.rownames = FALSE,
    tabular.environment = "longtable",
    caption.placement = "top",
    floating = FALSE,
    sanitize.text.function = \(x) str_replace_all(x, c("&" = "\\&", "\\%" = "\\\\%"))
  )

# Load the events
events <- tbl(con, "events") %>%
  collect()

# Print a random selection of 50 events, along with their dates and descriptions
events %>%
  sample_n(50) %>%
  select(Date = date, Description = description) %>%
  arrange(Date) %>%
  xtable(
    caption = "A representative selection of 50 out of 275,985 model-generated events.",
    label = "tab:events",
    align = "lp{0.1\\textwidth}p{0.84\\textwidth}"
  ) %>%
  print(
    type = "latex",
    file = path("tables", "events.tex"),
    tabular.environment = "longtable",
    include.rownames = FALSE,
    caption.placement = "top",
    floating = FALSE,
    sanitize.text.function = \(x) str_replace_all(x, c("&" = "\\&", "\\%" = "\\\\%"))
  )

# Load the segments
segments <- tbl(con, "segments") %>%
  filter(
    outlet %in% c("ABC", "CBS", "NBC"),
    program %in% c("ABC Evening News", "CBS Evening News", "NBC Evening News"),
    duration <= 1800
  ) %>%
  collect() %>%
  replace_na(list(event_id = -1)) %>%
  mutate(
    date = ymd(date),
    hard_news = hard_news == 1,
    commercial = commercial == 1,
    empty = empty == 1,
    # NOTE: Added to coerce computed columns to logical
    in_news = in_news == 1,
    intl_red = intl_red == 1,
    intl_full = intl_full == 1
  )

################################################################################
# For each outlet and date, determine when the news starts and ends

# NOTE: Not run directly because titles are not provided in the public data
#
# starts <- segments %>%
#   filter(str_detect(title, "(?i)^preview|^introduction")) %>%
#   group_by(date, outlet) %>%
#   summarize(
#     start = min(id),
#     .groups = "drop"
#   )
#
# ends <- segments %>%
#   filter(str_detect(title, "(?i)^good ?night")) %>%
#   group_by(date, outlet) %>%
#   summarize(
#     end = max(id),
#     .groups = "drop"
#   )
#
# # Join the start and end segments onto the main data
# segments <- segments %>%
#   left_join(starts, by = c("date", "outlet")) %>%
#   left_join(ends, by = c("date", "outlet")) %>%
#   group_by(date, outlet) %>%
#   mutate(
#     start = if_else(is.na(start), min(id), start),
#     end = if_else(is.na(end), max(id), end)
#   ) %>%
#   ungroup() %>%
#   mutate(in_news = id >= start & id <= end) %>%
#   filter(in_news) %>%
#   select(-start, -end, -in_news)
segments <- segments %>%
  filter(in_news)

################################################################################
# Calculate how the breakdown among news topics changes over time

# First, calculate across all topics
p_full <- segments %>%
  filter(!empty, !commercial) %>%
  select(id, outlet, date, duration, topic_id) %>%
  left_join(topics, by = join_by(topic_id == id)) %>%
  replace_na(list(title = "Unclassified")) %>%
  group_by(year = year(date), topic = title) %>%
  summarize(dur = sum(duration, na.rm = TRUE), .groups = "drop") %>%
  mutate(topic = factor(topic)) %>%
  group_by(year) %>%
  mutate(p = dur / sum(dur, na.rm = TRUE)) %>%
  ggplot(aes(x = year, y = p, color = topic)) +
  geom_line(show.legend = FALSE) +
  geom_point(show.legend = FALSE) +
  scale_y_continuous(
    labels = scales::percent_format(),
    limits = c(0, NA),
    expand = expansion(mult = c(0, 0.2))
  ) +
  labs(
    title = "News Topics",
    x = "Year",
    y = "Proportion of News Time"
  ) +
  facet_wrap(vars(topic), ncol = 3, scales = "free_y") +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1),
    strip.text = element_text(size = 5)
  )

ggsave(
  path("plots", "topics-full.pdf"),
  p_full,
  width = 6.5,
  height = 8
)

# Next, restrict to International Relations (#1), National Security (#2),
# Politics (#4), technology (#11), crime (#13), and human interest (#20)
p_highlighted <- segments %>%
  filter(!empty, !commercial) %>%
  select(id, outlet, date, duration, topic_id) %>%
  left_join(topics, by = join_by(topic_id == id)) %>%
  replace_na(list(title = "Unclassified")) %>%
  group_by(year = year(date), topic = title) %>%
  summarize(dur = sum(duration, na.rm = TRUE), .groups = "drop") %>%
  group_by(year) %>%
  mutate(p = dur / sum(dur, na.rm = TRUE)) %>%
  mutate(topic = factor(
    topic,
    levels = c(
      "International Relations and Global Policy",
      "Natural Disasters and Emergency Management",
      "Political Campaigns and Elections",
      "Civil Rights and Social Movements",
      "Culture and Entertainment",
      "Technology, Science, and Innovation"
    )
  )) %>%
  drop_na(topic) %>%
  ggplot(aes(x = year, y = p, color = topic)) +
  geom_line(show.legend = FALSE) +
  geom_point(show.legend = FALSE) +
  scale_y_continuous(
    labels = scales::percent_format(),
    limits = c(0, NA),
    expand = expansion(mult = c(0, 0.2))
  ) +
  labs(
    title = "Changes in Coverage of Selected News Topics",
    x = "Year",
    y = "Proportion of News Time"
  ) +
  facet_wrap(vars(topic), ncol = 3, scales = "free_y") +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1),
    strip.text = element_text(size = 5),
    plot.margin = margin(t = 0, r = 5.5, b = 0, l = 5.5)
  )

ggsave(
  path("plots", "topics-highlighted.pdf"),
  p_highlighted,
  width = 6,
  height = 2.5
)

################################################################################
# Calculate how the news is split between hard news and other content

high_quality_indices <- c(1:5, 8, 9, 12)

p_composition <- segments %>%
  group_by(date, outlet) %>%
  summarize(
    dur_commercial = sum(
      commercial * duration,
      na.rm = TRUE
    ),
    dur_empty = sum(
      (empty & !commercial) * duration,
      na.rm = TRUE
    ),
    dur_soft_news = sum(
      (!hard_news & !empty & !commercial) * duration,
      na.rm = TRUE
    ),
    dur_other_high = sum(
      (
        hard_news
        & !empty
        & !commercial
        & issue_id == -1
        & other_id %in% high_quality_indices
      ) * duration,
      na.rm = TRUE
    ),
    dur_other_low = sum(
      (
        hard_news & !empty & !commercial & issue_id == -1
        & !(other_id %in% high_quality_indices)
      ) * duration,
      na.rm = TRUE
    ),
    dur_rest = sum(
      (hard_news & !empty & !commercial & issue_id != -1) * duration,
      na.rm = TRUE
    ),
    dur_total = sum(duration, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  group_by(year = year(date)) %>%
  summarize(
    dur_commercial = mean(dur_commercial),
    dur_soft_news = mean(dur_soft_news),
    dur_other_low = mean(dur_other_low),
    dur_other_high = mean(dur_other_high),
    dur_empty = mean(dur_empty),
    dur_rest = mean(dur_rest),
    dur_total = mean(dur_total),
    .groups = "drop"
  ) %>%
  mutate(dur_other = 30 * 60 - dur_total) %>%
  select(-dur_total) %>%
  pivot_longer(
    cols = starts_with("dur_"),
    names_to = "type",
    values_to = "duration"
  ) %>%
  mutate(
    type = factor(
      type,
      levels = c(
        "dur_other",
        "dur_commercial",
        "dur_empty",
        "dur_soft_news",
        "dur_other_low",
        "dur_other_high",
        "dur_rest"
      ),
      labels = c(
        "Other",
        "Commercial",
        "Empty",
        "Soft News",
        "Low Quality\nNon-Issue",
        "High Quality\nNon-Issue",
        "Issue-Based News"
      )
    ),
    duration = duration / 60
  ) %>%
  ggplot(aes(x = year, y = duration, fill = type)) +
  geom_area(position = "stack", color = "black", alpha = 0.5) +
  scale_fill_manual(
    values = set_names(
      c("#A0A0A0", scales::pal_viridis()(6)),
      c(
        "Other",
        "Commercial",
        "Empty",
        "Soft News",
        "Low Quality\nNon-Issue",
        "High Quality\nNon-Issue",
        "Issue-Based News"
      )
    )
  ) +
  scale_y_continuous(expand = c(0, 0)) +
  scale_x_continuous(expand = c(0, 0)) +
  labs(
    title = "News Content",
    x = "Year",
    y = "Duration (minutes)",
    fill = "Type of Content"
  ) +
  guides(fill = guide_legend(ncol = 1)) +
  theme_bw() +
  theme(
    legend.text = element_text(size = 7),
    plot.margin = margin(t = 0, r = 5.5, b = 0, l = 5.5)
  )

ggsave(
  path("plots", "composition.pdf"),
  p_composition,
  width = 6,
  height = 2.5
)

# Create appendix plot showing the breakdown among `other_id` topics over the
# years
p_other <- segments %>%
  filter(!empty, !commercial, hard_news, issue_id == -1) %>%
  select(id, date, outlet, duration, other_id) %>%
  group_by(decade = year(date) %/% 5, other_id) %>%
  summarize(dur = sum(duration, na.rm = TRUE), .groups = "drop") %>%
  mutate(
    other_id = factor(
      other_id,
      levels = c(-1, seq(17)),
      labels = c(
        "Other",
        "Business news",
        "Government procedure",
        "Foreign politics",
        "Corruption",
        "Foreign turmoil",
        "Natural disasters",
        "Notices",
        "Trials",
        "Crime",
        "Weather",
        "Transportation disasters",
        "Medical and health news",
        "Manmade disasters",
        "Animal attacks",
        "The Pope",
        "The Queen / British royal family",
        "Space program"
      )
    ),
    # Use the ordered factor levels for `other_id`
    other_id = fct_reorder(other_id, dur, .fun = sum, .desc = TRUE)
  ) %>%
  group_by(decade) %>%
  mutate(p = dur / sum(dur)) %>%
  ungroup() %>%
  ggplot(aes(x = decade * 5, y = p, fill = other_id)) +
  geom_area(position = "stack", color = "black") +
  scale_x_continuous(
    breaks = seq(1960, 2020, by = 5),
    expand = c(0, 0)
  ) +
  scale_y_continuous(
    labels = scales::percent_format(),
    limits = c(0, 1),
    expand = expansion(mult = c(0, 0)),
    oob = oob_keep
  ) +
  labs(
    title = "Non-Issue Hard News",
    x = "Year",
    y = "Proportion of News Time",
    fill = NULL
  ) +
  guides(fill = guide_legend(ncol = 3)) +
  theme_bw() +
  theme(
    legend.position = "bottom",
    plot.margin = margin(t = 0, r = 11, b = 0, l = 5.5)
  )

ggsave(
  path("plots", "other.pdf"),
  p_other,
  width = 6.5,
  height = 6
)

################################################################################
# Calculate how much time is spent on the top ten issues in 1969 and 2024

top_ten <- segments %>%
  select(id, date, outlet, duration, issue_id) %>%
  filter(year(date) %in% c(1969, 2024)) %>%
  left_join(issues, by = join_by(issue_id == id)) %>%
  replace_na(list(title = "Unclassified")) %>%
  group_by(date, outlet, title) %>%
  summarize(dur = sum(duration, na.rm = TRUE), .groups = "drop") %>%
  group_by(year(date)) %>%
  complete(nesting(date, outlet), title, fill = list(dur = 0)) %>%
  group_by(year = year(date), title) %>%
  summarize(dur = mean(dur) / 60, .groups = "drop") %>%
  filter(title != "Unclassified") %>%
  group_by(year) %>%
  arrange(year, desc(dur)) %>%
  slice_head(n = 10) %>%
  mutate(title = factor(title, levels = title)) %>%
  ungroup()

# Plot for 1969
p_1969 <- top_ten %>%
  filter(year == 1969) %>%
  ggplot(aes(x = dur, y = title, fill = title)) +
  geom_bar(stat = "identity", show.legend = FALSE) +
  scale_fill_viridis_d() +
  scale_x_reverse(
    limits = c(6, 0),
    expand = c(0, 0),
  ) +
  labs(x = "Duration (minutes)", y = NULL) +
  facet_wrap(vars(year)) +
  theme_bw() +
  theme(
    axis.text.y = element_text(hjust = 1, size = 7),
    axis.text.x = element_text(hjust = 0.7),
    plot.margin = margin(t = 0, r = 5.5, b = 0, l = 0)
  )

# Plot for 2024 with text on right
p_2024 <- top_ten %>%
  filter(year == 2024) %>%
  ggplot(aes(x = dur, y = title, fill = title)) +
  geom_bar(stat = "identity", show.legend = FALSE) +
  scale_fill_viridis_d() +
  scale_y_discrete(position = "right") +
  scale_x_continuous(
    limits = c(0, 6),
    expand = c(0, 0),
  ) +
  labs(x = "Duration (minutes)", y = NULL) +
  facet_wrap(vars(year)) +
  theme_bw() +
  theme(
    axis.text.y = element_text(hjust = 0, size = 7),
    axis.text.x = element_text(hjust = 0.3),
    plot.margin = margin(t = 0, r = 0, b = 0, l = 5.5)
  )

# Combine plots with a shared bottom title
p_top_ten <- p_1969 + p_2024 +
  plot_layout(widths = c(1, 1), axis = 'collect') +
  plot_annotation(
    title = "Coverage of Top Ten Issues in 1969 and 2024",
    theme = theme(
      plot.title = element_text(hjust = 0.5),
      plot.margin = margin(t = 1, r = 0, b = 0, l = 0)
    )
  )

ggsave(
  path("plots", "top-ten.pdf"),
  p_top_ten,
  width = 6.5,
  height = 2.3
)

################################################################################
# Calculate how mentions of international news changes over time

# NOTE: Not run directly because titles and abstracts are not provided in the
# public data
#
# intl_full_re <- read_csv("_country_list.csv", show_col_types = FALSE) %>%
#   mutate_all(str_to_lower) %>%
#   with(c(country, adjective)) %>%
#   str_c(collapse = "|") %>%
#   str_c("(?i)\\b(", ., ")\\b")
#
# intl_red_re <- read_csv("_country_list.csv", show_col_types = FALSE) %>%
#   filter(!str_detect(country, "Iraq|Afghanistan")) %>%
#   mutate_all(str_to_lower) %>%
#   with(c(country, adjective)) %>%
#   str_c(collapse = "|") %>%
#   str_c("(?i)\\b(", ., ")\\b")
#
# # Determine whether each segment is international
# intl <- segments %>%
#   filter(!empty, !commercial, hard_news) %>%
#   mutate(
#     intl_full = str_detect(abstract, intl_full_re)
#       | str_detect(title, intl_full_re),
#     intl_red = str_detect(abstract, intl_red_re)
#       | str_detect(title, intl_red_re)
#   ) %>%
#   group_by(year = year(date)) %>%
#   summarize(
#     intl_full = sum(intl_full * duration, na.rm = TRUE)
#       / sum(duration, na.rm = TRUE),
#     intl_red = sum(intl_red * duration, na.rm = TRUE)
#       / sum(duration, na.rm = TRUE),
#     .groups = "drop"
#   ) %>%
#   pivot_longer(
#     cols = starts_with("intl_"),
#     names_to = "type"
#   )
intl <- segments %>%
  filter(!empty, !commercial, hard_news) %>%
  group_by(year = year(date)) %>%
  summarize(
    intl_full = sum(intl_full * duration, na.rm = TRUE)
      / sum(duration, na.rm = TRUE),
    intl_red = sum(intl_red * duration, na.rm = TRUE)
      / sum(duration, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  pivot_longer(
    cols = starts_with("intl_"),
    names_to = "type"
  )

# Plot the proportion of international news (weighted by duration) per year
p_intl <- intl %>%
  ggplot(aes(x = year, y = value, color = type)) +
  geom_line() +
  geom_point(size = 1) +
  scale_y_continuous(
    labels = percent_format(),
    limits = c(0, 0.75),
    expand = expansion(mult = c(0, 0))
  ) +
  scale_color_discrete(
    labels = c(
      "intl_full" = "All Countries",
      "intl_red"  = "Excluding Iraq and Afghanistan"
    ),
  ) +
  labs(
    x     = "Year",
    y     = "Prop. international news\n(out of hard news)",
    color = NULL
  ) +
  guides(color = guide_legend(ncol = 1)) +
  theme(
    legend.position = "inside",
    legend.position.inside = c(0.5, 0.1),
    plot.margin = margin(0, 5.5, 0, 5.5),
    legend.text = element_text(size = 6),
    legend.background = element_blank(),
    legend.key = element_blank(),
    axis.text.x = element_text(angle = 45, hjust = 1)
  )

ggsave(
  path("plots", "intl.pdf"),
  p_intl,
  width = 3,
  height = 2.5
)

################################################################################

# Load the response data
responses <- read_csv(
    path("data", "responses.csv"),
    col_types = cols(.default = "c")
  ) %>%
  pivot_longer(
    cols = starts_with("segment_"),
    names_to = c("segment", ".value"),
    names_pattern = glue(
      "segment_(\\d+)_(news_type|issue_primary|issue_secondary|topic_primary|",
      "topic_secondary)"
    ),
    values_drop_na = TRUE
  ) %>%
  pivot_longer(
    cols = c(news_type, issue_primary, issue_secondary, topic_primary, topic_secondary),
    names_to = "variable",
    values_to = "value",
    values_drop_na = TRUE
  ) %>%
  mutate(segment = as.integer(segment))

# Load model data
model_label <- tbl(con, "segments") %>%
  filter(id %in% responses$segment) %>%
  collect() %>%
  left_join(rename(topics, topic = title), by = c("topic_id" = "id")) %>%
  left_join(rename(issues, issue = title), by = c("issue_id" = "id")) %>%
  transmute(
    segment = id,
    news_type = if_else(
      hard_news == 1,
      "Hard News (e.g., politics, economics, crime)",
      "Soft News (e.g., entertainment, sports, human interest)"
    ),
    topic = topic,
    issue = issue
  ) %>%
  replace_na(
    list(
      topic = "No topic matches this abstract.",
      issue = "This abstract does not match any of the issues."
    )
  ) %>%
  pivot_longer(
    cols = c(news_type, issue, topic),
    names_to = "variable",
    values_to = "value",
    values_drop_na = TRUE
  ) %>%
  mutate(
    variable = factor(variable, levels = c("news_type", "topic", "issue"))
  ) %>%
  arrange(segment, variable)

# Calculate the majority vote for each segment
gold_label <- responses %>%
  count(segment, variable, value) %>%
  # Add half points for secondary issues/topics
  mutate(
    n = if_else(str_detect(variable, "secondary"), n / 2, n),
    type = str_extract(variable, "news_type|issue|topic"),
    type = factor(type, levels = c("news_type", "topic", "issue"))
  ) %>%
  group_by(segment, type, value) %>%
  summarise(n = sum(n), .groups = "drop") %>%
  group_by(segment, type) %>%
  slice_max(n, n = 1, with_ties = TRUE) %>%
  arrange(segment, type) %>%
  mutate(
    variable_primary = if_else(type == "news_type", type, str_c(type, "_primary")),
    variable_secondary = if_else(type == "news_type", type, str_c(type, "_secondary"))
  ) %>%
  ungroup()

# Calculate the average accuracy of the human responses
accuracy_human <- responses %>%
  filter(! variable %in% c("issue_secondary", "topic_secondary")) %>%
  left_join(
    select(gold_label, -variable_secondary),
    by = join_by(
      segment == segment,
      variable == variable_primary,
      value == value
    )
  ) %>%
  mutate(correct = if_else(is.na(n), FALSE, TRUE)) %>%
  filter(! variable %in% c("issue_secondary", "topic_secondary")) %>%
  mutate(
    variable = factor(
      variable,
      levels = c("news_type", "topic_primary", "issue_primary"),
      labels = c("news_type", "topic", "issue")
    )
  ) %>%
  group_by(variable) %>%
  summarize(
    std.err = sd(correct) / sqrt(n()),
    mean = mean(correct),
    .groups = "drop"
  ) %>%
  mutate(
    rater = "human",
    outcome = "correct"
  )

# Calculate the average accuracy of the model responses
accuracy_model <- model_label %>%
  left_join(
    select(gold_label, -variable_secondary),
    by = join_by(
      segment == segment,
      variable == type,
      value == value
    )
  ) %>%
  mutate(correct = if_else(is.na(n), FALSE, TRUE)) %>%
  group_by(variable) %>%
  summarize(
    std.err = sd(correct) / sqrt(n()),
    mean = mean(correct),
    .groups = "drop"
  ) %>%
  mutate(
    rater = "model",
    outcome = "correct"
  )

# Calculate the probability that two random human responses are the same
rand_human <- responses %>%
  filter(! variable %in% c("issue_secondary", "topic_secondary")) %>%
  mutate(
    variable = factor(
      variable,
      levels = c("news_type", "topic_primary", "issue_primary"),
      labels = c("news_type", "topic", "issue")
    )
  ) %>%
  count(variable, segment, value) %>%
  group_by(variable, segment) %>%
  summarize(
    num = sum(n * (n - 1) / 2),
    tot = sum(n) * (sum(n) - 1) / 2,
    .groups = "drop_last"
  ) %>%
  summarize(
    mean = sum(num) / sum(tot),
    std.err = sqrt(mean * (1 - mean) / sum(tot))
  ) %>%
  mutate(
    rater = "human",
    outcome = "random"
  )

# Calculate the probability that a random human response is the same as the
# model response
rand_model <- responses %>%
  filter(! variable %in% c("issue_secondary", "topic_secondary")) %>%
  mutate(
    variable = factor(
      variable,
      levels = c("news_type", "topic_primary", "issue_primary"),
      labels = c("news_type", "topic", "issue")
    )
  ) %>%
  left_join(
    select(model_label, segment, variable, value),
    by = join_by(segment == segment, variable == variable),
    suffix = c("_human", "_model")
  ) %>%
  group_by(variable) %>%
  summarize(
    mean = mean(value_human == value_model, na.rm = TRUE),
    std.err = sd(value_human == value_model, na.rm = TRUE) / sqrt(n())
  ) %>%
  mutate(
    rater = "model",
    outcome = "random"
  )

# Plot the results
p_agreement <- bind_rows(
    accuracy_human,
    accuracy_model,
    rand_human,
    rand_model
  ) %>%
  mutate(
    rater = factor(
      rater,
      levels = c("human", "model"),
      labels = c("Human Experts", "Model")
    ),
    outcome = factor(
      outcome,
      levels = c("correct", "random"),
      labels = c(
        "Proportion \"correct\"",
        "Probability of agreement"
      )
    ),
    variable = factor(
      variable,
      levels = c("news_type", "topic", "issue"),
      labels = c("Hard vs. soft news", "Topic", "Issue")
    )
  ) %>%
  ggplot(aes(x = variable, y = mean, fill = rater)) +
  geom_col(position = position_dodge(width = 0.9), width = 0.8) +
  geom_errorbar(
    aes(ymin = mean - 1.96 * std.err, ymax = mean + 1.96 * std.err),
    position = position_dodge(width = 0.9),
    width = 0.2
  ) +
  scale_y_continuous(
    limits = c(0, 1),
    breaks = seq(0, 1, by = 0.1),
    labels = scales::percent_format(accuracy = 1),
    expand = c(0, 0)
  ) +
  facet_wrap(~ outcome) +
  labs(
    x = "Aspect",
    y = "Proportion Correct",
    fill = "Rater"
  ) +
  theme_bw() +
  theme(legend.position = "bottom")

ggsave(
  path("plots", "validation.pdf"),
  plot = p_agreement,
  width = 6,
  height = 4,
  device = cairo_pdf
)
