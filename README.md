# Multi-city bike-sharing network analysis

An end-to-end Python workflow for turning heterogeneous bike-share trip files
into auditable, analysis-ready origin-destination (OD) networks. The project
supports data acquisition, schema-aware preprocessing, descriptive analysis,
network visualisation, and exploratory point-process diagnostics for datasets
from San Francisco, Washington, DC, Portland, and Chicago. The output is
designed to support later point-process and Hawkes-process modelling work.

## Why this project

This repository demonstrates practical data-engineering and analytical work on
large, messy public datasets:

- Downloads and organises city-level trip-history data from provider endpoints.
- Detects several legacy and modern trip schemas, then standardises them into a
  common representation.
- Streams large CSV files in chunks, applies explicit cleaning rules, and
  records an audit-friendly summary of rows removed and retained.
- Builds normalized directed OD networks, station lookup tables, conflict
  reports, descriptive summaries, and optional NetworkX visualisations.
- Keeps modelling work clearly separated: diagnostic probes and a small
  univariate Hawkes pilot are exploratory, not a production forecasting model
  or causal claim.

## Local research-run scale

The local research run that informed this repository processed **113.3 million
raw rows** and retained **94.2 million cleaned trips** across four fully
processed cities, using the documented 240-minute duration cutoff. A separate
two-year New York City sample retained **89.5 million trips**. These figures
are reported as a workload snapshot rather than bundled results: the underlying
data and generated outputs are deliberately not versioned, and future runs may
vary with provider files and selected inputs.

| City | Raw rows processed | Cleaned trips retained |
| --- | ---: | ---: |
| San Francisco | 20,824,610 | 17,516,045 |
| Washington, DC | 49,350,687 | 43,775,863 |
| Portland | 1,226,107 | 670,026 |
| Chicago | 41,885,578 | 32,266,778 |

## Tech stack

Python 3.10+ · pandas · NumPy · SciPy · statsmodels · Matplotlib · NetworkX

## Repository layout

```text
.
├── data/                         # local-only data guide; downloads are ignored
├── tools/                        # acquisition, cleaning, analysis, and graph scripts
├── modeling/
│   ├── pp_fitting/               # exploratory point-process diagnostics
│   └── fitting/                  # pilot univariate Hawkes fit
├── requirements.txt              # reproducible Python dependencies
└── README.md
```

The repository deliberately excludes raw and row-level processed trip data,
local virtual environments, temporary files, personal report material, and
generated experiment artefacts. See [data/README.md](data/README.md) for the
configured public data endpoints and local data layout.

## Quick start

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# Discover and download selected provider files (inspect first with --dry-run).
.venv/bin/python tools/download_bikeshare_data.py \
  --cities san_francisco \
  --years 2022 2023 \
  --output-root data

# Standardise trips and construct OD-network inputs.
.venv/bin/python tools/bikeshare_cleaning.py \
  "data/san_francisco/*.csv" "data/san_francisco/*.zip" \
  --city san_francisco \
  --output-dir outputs/san_francisco
```

The cleaner automatically skips non-trip station metadata files and processes
matching trip files in chunks. On a first use, run the downloader with
`--dry-run` to review provider links, and verify that downloading and use are
permitted by the relevant provider.

## Workflow

1. **Acquire** — `tools/download_bikeshare_data.py` discovers historical files
   from configured city endpoints and records local download manifests.
2. **Standardise and clean** — `tools/bikeshare_cleaning.py` maps recognised
   source schemas to a common trip representation, removes incomplete records,
   excludes the configured pandemic period, filters short self-loops, applies a
   maximum-duration cutoff, and separates customer/subscriber trips.
3. **Analyse the OD network** — `tools/bikeshare_graph_viz.py` uses normalized
   station-pair counts and canonical station metadata to draw directed graphs,
   identify high-arrival stations, and optionally run community detection.
4. **Explore trip behaviour** — `tools/bikeshare_analysis.py` produces
   pair-level, station-level, temporal, and distance-versus-duration summaries.
5. **Explore event dynamics** — scripts in `modeling/` run deseasonalized
   diagnostics and a limited univariate Hawkes fitting pilot on cleaned trips.

## Main outputs

For each cleaned city, the pipeline writes a compact, inspectable set of
tables. The row-level `cleaned_*.csv` files are optional and can be disabled
with `--skip-cleaned-csv` when disk space is limited.

| File | Purpose |
| --- | --- |
| `summary_overall.csv` | row counts and each cleaning-stage removal count |
| `summary_by_user_type.csv` | retained trips by customer/subscriber category |
| `duration_threshold_comparison.csv` | effect of candidate duration cutoffs |
| `station_pairs_normalized.csv` | directed OD edge counts and origin-normalized weights |
| `station_lookup_canonical.csv` | canonical station ID, name, and coordinates |
| `station_id_conflicts.csv` | station-name/coordinate inconsistencies for review |

## Useful commands

Build an OD graph from cleaning outputs:

```bash
.venv/bin/python tools/bikeshare_graph_viz.py \
  --pairs outputs/san_francisco/station_pairs_normalized.csv \
  --stations outputs/san_francisco/station_lookup_canonical.csv \
  --output-dir outputs/san_francisco/graph_subscriber \
  --user-type Subscriber \
  --min-normalized 0.03 \
  --detect-communities
```

Run descriptive analysis:

```bash
.venv/bin/python tools/bikeshare_analysis.py \
  --cleaned outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/analysis_subscriber \
  --user-type-label Subscriber
```

Run exploratory point-process diagnostics:

```bash
.venv/bin/python modeling/pp_fitting/scripts/run_pp_diagnostics.py \
  --task all \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/pp_diagnostics
```

Run the small univariate Hawkes fitting pilot:

```bash
.venv/bin/python modeling/fitting/scripts/pilot_univariate_hawkes_fit.py \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/hawkes_pilot
```

Further command-level detail is available in [tools/README.md](tools/README.md)
and [modeling/README.md](modeling/README.md).

## Modelling scope and limitations

The point-process scripts are exploratory tools. Deseasonalized residual
clustering can be consistent with a self-exciting process, but it does not by
itself establish causal self-excitation or identify a true multivariate
interaction network. The included Hawkes fit is a small univariate pilot and
uses AIC only for the stated Poisson comparison. Results should be validated
with held-out data and more complete covariates before operational use.

Source schemas and provider endpoints evolve. The cleaner recognises several
common formats, but every newly downloaded dataset should be inspected before
being treated as compatible.

## License

Released under the [MIT License](LICENSE). The code license does not grant any
rights to third-party bike-share datasets; follow each provider's current terms
when downloading, using, or sharing data.
