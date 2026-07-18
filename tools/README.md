# Data pipeline commands

Run commands from the repository root after installing
[`requirements.txt`](../requirements.txt).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The end-to-end stage boundaries are documented in
[`docs/workflow.md`](../docs/workflow.md); cleaning assumptions are kept in
[`docs/methodology.md`](../docs/methodology.md).

## Script index

| Script | Role |
| --- | --- |
| `download_bikeshare_data.py` | discover and download configured public trip-history files |
| `bikeshare_cleaning.py` | stream, standardise, clean, and summarize raw trip files |
| `run_bikeshare_batch.py` | run the cleaner for multiple cities from JSON config |
| `bikeshare_partitioned_run.py` | process large inputs in partitions and merge outputs |
| `bikeshare_make_sample_outputs.py` | create a time-windowed subset from cleaned outputs |
| `bikeshare_graph_viz.py` | build directed OD graphs, communities, and exports |
| `bikeshare_analysis.py` | generate descriptive trip and station analysis |
| `bikeshare_presentation_figures.py` | create local summary figures from cleaned outputs |

## Download data

Inspect links before downloading:

```bash
.venv/bin/python tools/download_bikeshare_data.py \
  --cities san_francisco chicago \
  --years 2022 2023 \
  --output-root data \
  --dry-run
```

Remove `--dry-run` to download the selected files. Without `--cities`, the
downloader uses every configured city.

## Clean one city

```bash
.venv/bin/python tools/bikeshare_cleaning.py \
  "data/san_francisco/*.csv" "data/san_francisco/*.zip" \
  --city san_francisco \
  --output-dir outputs/san_francisco \
  --chunksize 200000 \
  --max-duration-min 240
```

Add `--skip-cleaned-csv` when only summaries, station tables, and OD outputs
are needed. Supported source schemas and output columns are listed in
[CLEANING_NOTES.md](CLEANING_NOTES.md).

## Run multiple cities

Preview commands from the versioned example configuration:

```bash
.venv/bin/python tools/run_bikeshare_batch.py \
  --config tools/bikeshare_batch_config.example.json \
  --dry-run
```

Copy the example to a local configuration before changing paths or city
selection, then rerun without `--dry-run`.

## Partition a large run

```bash
.venv/bin/python tools/bikeshare_partitioned_run.py \
  --inputs "data/san_francisco/extracted_csv/*.csv" \
  --city san_francisco \
  --output-dir outputs/san_francisco_partitioned \
  --partition-mode year
```

The command creates per-partition outputs and a merged directory containing
combined summaries, OD pairs, station metadata, and a partition manifest.

## Create a time-windowed sample

```bash
.venv/bin/python tools/bikeshare_make_sample_outputs.py \
  --input-dir outputs/san_francisco \
  --output-dir outputs/san_francisco_sample \
  --start-year 2022 \
  --end-year 2023
```

## Run descriptive analysis

```bash
.venv/bin/python tools/bikeshare_analysis.py \
  --cleaned outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/analysis_subscriber \
  --user-type-label Subscriber
```

Add `--run-time-tests` to enable the more expensive pair-level temporal tests.

## Build a station graph

```bash
.venv/bin/python tools/bikeshare_graph_viz.py \
  --pairs outputs/san_francisco/station_pairs_normalized.csv \
  --stations outputs/san_francisco/station_lookup_canonical.csv \
  --output-dir outputs/san_francisco/graph_subscriber \
  --user-type Subscriber \
  --min-normalized 0.03 \
  --detect-communities \
  --export-json
```

Use `--highlight-most-visited` to emphasize the station with the highest total
arrivals and `--top-edges` to limit the graph to the strongest edges.
