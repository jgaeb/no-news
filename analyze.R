#!/usr/bin/env Rscript
library(groundhog)
groundhog.library("
  xtable
  patchwork
  RSQLite
  DBI
  dbplyr
  fs
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
        & !other_id %in% high_quality_indices
      ) * duration,
      na.rm = TRUE
    ),
    dur_rest = sum(
      (hard_news & !empty & !commercial & issue_id != -1) * duration,
      na.rm = TRUE),
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

################################################################################
# Calculate how much time is spent on the top ten issues in 1969 and 2019

top_ten <- segments %>%
  select(id, date, outlet, duration, issue_id) %>%
  filter(year(date) %in% c(1969, 2019)) %>%
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

# Plot for 2019 with text on right
p_2019 <- top_ten %>%
  filter(year == 2019) %>%
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
p_top_ten <- p_1969 + p_2019 +
  plot_layout(widths = c(1, 1), axis = 'collect') +
  plot_annotation(
    title = "Coverage of Top Ten Issues in 1969 and 2019",
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
    legend.position = c(0.5, 0.1),
    plot.margin = margin(0, 5.5, 0, 5.5),
    legend.text = element_text(size = 6),
    legend.background = element_blank(),
    legend.key = element_blank(),
    axis.text.x = element_text(angle = 45, hjust = 1)
  )

ggsave(
  "plots/intl.pdf",
  p_intl,
  width = 2.5,
  height = 2.5
)
