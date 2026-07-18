# Point-process diagnostics

This module provides two exploratory diagnostics for cleaned bike departures:

- `hawkes_diagnostics.py` measures station-level inter-event timing and
  dispersion before and after an empirical hour-of-week adjustment.
- `cross_excitation_check.py` screens for directional short-lag correlation
  among busy stations after a simple seasonal residualization.
- `run_pp_diagnostics.py` is a launcher for either script or both.

Neither diagnostic fits or validates a full multivariate Hawkes model. Treat
their results as hypotheses for further modelling, not as causal conclusions.

## Run

From the repository root:

```bash
python modeling/pp_fitting/scripts/run_pp_diagnostics.py \
  --task all \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/pp_diagnostics
```

`--task hawkes` and `--task cross` run the individual diagnostics. The input
must contain `start_station_id` and either `started_at` or `start_time`; the
cross-excitation probe also requires start-station latitude and longitude.

## Outputs

The launcher writes a CSV/PNG pair for the univariate diagnostic and a PNG for
the cross-excitation probe. Outputs are intentionally local-only so that
experiments can be rerun against data acquired under the relevant provider
terms.
