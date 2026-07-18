# End-to-end workflow

The project separates data acquisition, preparation, analysis, and modelling so
that each stage can be rerun independently. Generated files are passed between
stages through explicit local paths rather than hidden notebook state.

## 1. Acquire public trip files

`tools/download_bikeshare_data.py` discovers historical files from configured
city endpoints, filters links by city and year, and stores downloads under
`data/<city>/`. A local manifest records source URLs and filenames so repeated
runs can skip files already present.

Provider endpoints and the expected local data layout are documented in
[data/README.md](../data/README.md).

## 2. Standardise and clean trips

`tools/bikeshare_cleaning.py` detects supported source schemas and maps each
input chunk to a common event representation. The shared fields include trip
timestamps, duration, station identifiers and coordinates, user type, city,
and source filename.

Cleaning produces separate customer and subscriber event streams together with
row-count summaries for every removal stage. Assumptions and thresholds are
centralized in [methodology.md](methodology.md).

## 3. Construct OD and station data

Each retained trip contributes to a directed `(start station → end station)`
pair. The pipeline writes raw pair counts and origin-normalized weights, plus a
canonical station lookup and a conflict table for inconsistent names or
coordinates.

These outputs form the interface between preprocessing and network analysis:

```text
cleaned trips
├── summary tables
├── station_pairs_normalized.csv
├── station_lookup_canonical.csv
└── station_id_conflicts.csv
```

## 4. Analyse trips and station networks

`tools/bikeshare_analysis.py` generates pair-level, station-level, temporal,
and distance-versus-duration summaries. `tools/bikeshare_graph_viz.py` converts
OD tables into directed NetworkX graphs, supports edge filtering and community
detection, and can export graph data for later visualisation.

Runnable examples are collected in [tools/README.md](../tools/README.md).

## 5. Prepare event-process experiments

Cleaned departure times become station event streams for the scripts under
`modeling/`. The OD network and detected communities can also be used to reduce
or prioritize candidate interactions in later multivariate work.

The current diagnostics and fitting pilot are described in
[modeling-scope.md](modeling-scope.md), with commands in
[modeling/README.md](../modeling/README.md).

## Execution modes

- **Single city:** run the cleaner directly on one collection of source files.
- **Multi-city batch:** use `run_bikeshare_batch.py` with a JSON configuration.
- **Partitioned run:** split a large city by year or configured date range,
  process each partition, and merge the summaries.
- **Time-windowed sample:** derive smaller modelling inputs from a previously
  cleaned city output.

These modes share the same cleaning worker and output schema, so scaling a run
does not change the downstream interface.
