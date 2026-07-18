# Point-process modelling commands

The modelling scripts consume local cleaned trip files created by the data
pipeline. Install the shared dependencies from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Interpretation and limitations are documented once in
[`docs/modeling-scope.md`](../docs/modeling-scope.md).

## Components

| Directory | Scope |
| --- | --- |
| [`pp_fitting/`](pp_fitting/README.md) | deseasonalized univariate diagnostics and a directional cross-station probe |
| [`fitting/`](fitting/README.md) | pilot exponential-kernel univariate Hawkes fits |

## Run point-process diagnostics

Run both diagnostic tasks:

```bash
.venv/bin/python modeling/pp_fitting/scripts/run_pp_diagnostics.py \
  --task all \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/pp_diagnostics
```

Use `--task hawkes` for the station-level diagnostics or `--task cross` for the
directional cross-station probe. The input must contain `start_station_id` and
either `started_at` or `start_time`; the cross task also requires start-station
latitude and longitude.

The diagnostic launcher writes:

- `hawkes_diagnostics_results.csv`
- `hawkes_diagnostics.png`
- `cross_excitation_check.png`

## Run the univariate Hawkes pilot

```bash
.venv/bin/python modeling/fitting/scripts/pilot_univariate_hawkes_fit.py \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/hawkes_pilot \
  --top-n 5 \
  --min-events 5000
```

The pilot writes `pilot_univariate_hawkes_summary.csv` and
`pilot_univariate_hawkes_overview.png`. Inputs and generated results remain
local and are excluded from Git.
