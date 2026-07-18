# Data pipeline tools

Run these commands from the repository root after installing
[`requirements.txt`](../requirements.txt).

| Script | Role |
| --- | --- |
| `download_bikeshare_data.py` | discovers and downloads configured public trip-history files |
| `bikeshare_cleaning.py` | streams, standardises, cleans, and summarizes raw trip files |
| `run_bikeshare_batch.py` | runs the cleaner for multiple cities using a JSON config |
| `bikeshare_partitioned_run.py` | partitions a large run and merges its summaries |
| `bikeshare_make_sample_outputs.py` | creates a time-windowed subset from prior clean outputs |
| `bikeshare_graph_viz.py` | builds optional directed OD-network plots and exports |
| `bikeshare_analysis.py` | runs descriptive trip and station analysis |
| `bikeshare_presentation_figures.py` | creates local presentation figures from cleaned outputs |

## Typical single-city run

```bash
python tools/download_bikeshare_data.py \
  --cities chicago --years 2022 2023 --output-root data --dry-run

python tools/bikeshare_cleaning.py \
  "data/chicago/*.csv" "data/chicago/*.zip" \
  --city chicago --output-dir outputs/chicago
```

Read [CLEANING_NOTES.md](CLEANING_NOTES.md) for the cleaning rules, output
schema, large-file guidance, and additional examples.

## Multi-city run

The example configuration is designed to be used from the repository root:

```bash
python tools/run_bikeshare_batch.py \
  --config tools/bikeshare_batch_config.example.json \
  --dry-run
```

Copy the example to a local, untracked configuration before modifying it. The
example uses glob patterns, so ensure its input paths match the files you have
downloaded.
