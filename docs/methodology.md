# Methodology and reproducibility

This document records the assumptions behind the processed data snapshot and
the conditions needed to reproduce the pipeline without committing large trip
files to Git.

## Processing scale

The full local run processed 113,286,982 raw rows and retained 94,228,712 trips
across four cities. A separate recent-two-year New York City sample retained
89,474,239 trips.

| City | Input files | Raw rows | Cleaned trips |
| --- | ---: | ---: | ---: |
| San Francisco | 80 | 20,824,610 | 17,516,045 |
| Washington, DC | 105 | 49,350,687 | 43,775,863 |
| Portland | 44 | 1,226,107 | 670,026 |
| Chicago | 78 | 41,885,578 | 32,266,778 |

Counts describe the source files used for that run. They can change when a
provider revises historical files or a different year range is selected.

## Schema standardisation

The cleaner recognises several legacy and modern layouts, including Bay Wheels
or Ford GoBike, Citi Bike or Divvy, and GBFS-style trip records. Supported
columns are mapped to a common representation before filtering. The detailed
schema examples and output fields are listed in
[tools/CLEANING_NOTES.md](../tools/CLEANING_NOTES.md).

Core assumptions:

- trip start and end times must parse successfully;
- duration, start station, end station, and user type must be present;
- user labels are normalized to `Customer` and `Subscriber`;
- station identifiers are handled consistently as string-like keys;
- station metadata conflicts are exported for review rather than silently
  discarded.

## Cleaning assumptions

The published processing snapshot used these rules:

1. Remove records missing required timestamps, duration, station IDs, or a
   recognized user type.
2. Exclude trips from 1 March 2020 through 31 December 2021.
3. Remove self-loop trips shorter than two minutes.
4. Compare 90, 120, 180, and 240-minute duration thresholds, using 240 minutes
   as the final cutoff for the reported run.
5. Write customer and subscriber event streams separately.

The duration cutoff is configurable. Each run writes a threshold-comparison
table and a summary of rows removed at every stage so alternative choices can
be audited.

## OD normalization and station resolution

For a directed pair from station `i` to station `j`, the pipeline records both
the trip count and the share of all retained departures from station `i`:

```text
normalized_count(i, j) = trips(i → j) / all departures from i
```

This preserves absolute demand while making routes from smaller origins
comparable with routes from major hubs. Canonical station records consolidate
observed names and coordinates by station ID; unresolved variation is written
to `station_id_conflicts.csv`.

## Reproducibility notes

- Python dependencies and minimum versions are listed in
  [`requirements.txt`](../requirements.txt).
- Raw downloads, row-level cleaned data, local environments, and generated
  results are excluded through `.gitignore`.
- The downloader writes per-city manifests containing source URLs and local
  filenames.
- `tools/bikeshare_batch_config.example.json` provides a versioned template for
  repeatable multi-city runs; local path changes belong in an untracked copy.
- Processing is chunk-based, with a configurable chunk size for memory control.
- `--skip-cleaned-csv` can produce summaries and OD outputs without writing
  large row-level event files.
- Provider schemas and download endpoints can change, so newly downloaded
  files should be checked against the supported schema list before a full run.

Command examples are kept in [tools/README.md](../tools/README.md) and
[modeling/README.md](../modeling/README.md) rather than duplicated here.
