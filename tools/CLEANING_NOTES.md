# Bikeshare Cleaning Notes

## What the script does

`bikeshare_cleaning.py` is a chunked cleaning pipeline for large bike-share trip files.

It recognizes several common legacy and modern schemas, including:

- Bay Wheels / old Ford GoBike style:
  `duration_sec`, `start_time`, `end_time`, `start_station_id`, `end_station_id`, `user_type`
- Classic CitiBike / Divvy style:
  `tripduration`, `starttime`, `stoptime`, `start station id`, `end station id`, `usertype`
- Modern GBFS style:
  `started_at`, `ended_at`, `start_station_id`, `end_station_id`, `member_casual`

## Cleaning rules mapped to advisor requirements

1. Split `Customer` and `Subscriber`
2. Remove A-A trips shorter than 2 minutes
3. Remove pandemic period trips:
   March 1, 2020 to December 31, 2021
4. Compare duration cutoffs:
   90, 120, 180, 240 minutes
5. Export normalized OD counts:
   `trip_count / total trips from same start station`
6. Build canonical station table by `station_id`
7. Export station name / coordinate conflict table

## Main outputs

When you run the script, it writes:

- `summary_overall.csv`
- `summary_by_user_type.csv`
- `duration_threshold_comparison.csv`
- `station_pairs_normalized.csv`
- `station_lookup_canonical.csv`
- `station_id_conflicts.csv`
- `cleaned_customer.csv`
- `cleaned_subscriber.csv`

If `matplotlib` is installed, it also writes plots into `plots/`.

## Example commands

San Francisco sample:

```bash
python3 tools/bikeshare_cleaning.py 2017-fordgobike-tripdata.csv 201801-fordgobike-tripdata.csv \
  --city san_francisco \
  --output-dir outputs_sf
```

All monthly files in one folder:

```bash
python3 tools/bikeshare_cleaning.py "citibike_2022/*.csv" \
  --city nyc \
  --output-dir outputs_nyc
```

Use a stricter final cutoff:

```bash
python3 tools/bikeshare_cleaning.py "divvy/*.csv" \
  --city chicago \
  --output-dir outputs_chicago \
  --max-duration-min 120
```

## Suggested workflow for the full project

1. Download each city's yearly or monthly CSV files into separate folders.
2. Run the script city by city.
3. Compare `duration_threshold_comparison.csv` before deciding whether the final cutoff should be 90, 120, 180, or 240 minutes.
4. Use `station_pairs_normalized.csv` for OD-network analysis and plotting.
5. Use `station_id_conflicts.csv` to audit station naming mistakes.

## Recommended folder layout for very large datasets

```text
research_project/
  data/
    san_francisco/
    nyc/
    washington/
    chicago/
    columbus/
    portland/
  outputs/
  tools/
    bikeshare_cleaning.py
    run_bikeshare_batch.py
    bikeshare_batch_config.json
```

## Batch run for many cities

1. Copy `bikeshare_batch_config.example.json` to `bikeshare_batch_config.json`
2. Edit each city's input patterns
3. Run:

```bash
python3 tools/run_bikeshare_batch.py --config tools/bikeshare_batch_config.json
```

Preview commands first:

```bash
python3 tools/run_bikeshare_batch.py --config tools/bikeshare_batch_config.json --dry-run
```

## Partitioned run for very large cities

Use `bikeshare_partitioned_run.py` when one city is too large to comfortably
process in a single run.

Example: split automatically by year

```bash
python3 tools/bikeshare_partitioned_run.py \
  --inputs "data/san_francisco/extracted_csv/*.csv" \
  --city san_francisco \
  --output-dir data/san_francisco/partitioned_outputs
```

This creates:

- `partitions/year_2017/`
- `partitions/year_2018/`
- ...
- `merged/`

The merged directory includes:

- combined partition summaries
- combined user-type summaries
- merged duration threshold comparison
- merged station pairs
- merged station lookup
- partition manifest

Why use this:

- safer on large datasets
- easier to restart if one partition fails
- slightly slower overall, but usually more robust than one giant run

## Official downloader

Use `download_bikeshare_data.py` to discover and download historical trip files from the official city system-data pages.

Download one city for selected years:

```bash
python3 tools/download_bikeshare_data.py --cities san_francisco --years 2019 2022 2023
```

Preview discovered links without downloading:

```bash
python3 tools/download_bikeshare_data.py --cities nyc chicago --years 2022 --dry-run
```

Download all configured cities:

```bash
python3 tools/download_bikeshare_data.py
```

Then clean the downloaded files:

```bash
python3 tools/bikeshare_cleaning.py "data/san_francisco/*.csv" "data/san_francisco/*.zip" \
  --city san_francisco \
  --output-dir outputs_sf
```

## Graph visualization

Use `bikeshare_graph_viz.py` after cleaning to build station-network graphs from
`station_pairs_normalized.csv` and `station_lookup_canonical.csv`.

Example for one user type:

```bash
python3 tools/bikeshare_graph_viz.py \
  --pairs outputs_sf/station_pairs_normalized.csv \
  --stations outputs_sf/station_lookup_canonical.csv \
  --output-dir outputs_sf/graph_customer \
  --user-type Customer \
  --min-normalized 0.03 \
  --highlight-most-visited \
  --detect-communities
```

What it does:

- Builds a directed graph from `(start_station_id -> end_station_id)`
- Uses `normalized_count` or `trip_count` as edge weight
- Filters weak edges by threshold
- Highlights the most visited end station if requested
- Optionally runs community detection
- Optionally exports JSON for interactive visualization

Typical outputs:

- `graph_customer.png`
- `graph_summary_customer.csv`
- `most_visited_end_stations_customer.csv`
- `graph_communities_customer.png`
- `community_assignments_customer.csv`
- `graph_customer.json`

## Independent analysis script

Use `bikeshare_analysis.py` to collect notebook-style information analysis into a
reusable script. This is intentionally separate from cleaning and graph export.

Example:

```bash
python3 tools/bikeshare_analysis.py \
  --cleaned test_outputs_sf/cleaned_subscriber.csv \
  --output-dir test_outputs_sf/analysis_subscriber \
  --user-type-label Subscriber
```

What it covers by default:

- Pair-level duration summary
- Station ID consistency / coordinate conflict analysis
- Pair distance calculation using station coordinates
- Most visited end station summary
- Top pairs summary
- Hour-of-day duration summary and plot
- Distance vs. duration plot

Heavier optional analysis:

```bash
python3 tools/bikeshare_analysis.py \
  --cleaned test_outputs_sf/cleaned_subscriber.csv \
  --output-dir test_outputs_sf/analysis_subscriber \
  --user-type-label Subscriber \
  --run-time-tests
```

That optional mode runs pair-level time-dependence tests such as
Kruskal-Wallis, which can become expensive on multi-city multi-year data.

## Practical advice for huge files

- Run one city at a time first, not all cities at once
- Keep raw data separated by city
- Start with `--skip-cleaned-csv` if disk space becomes a problem
- Save only summaries first, then export row-level cleaned data only for the city you are actively analyzing
- If one city has mixed schemas across years, that is fine; the script detects common legacy and modern formats automatically
