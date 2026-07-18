# Modelling scope

The modelling directory contains exploratory station-event diagnostics and a
small univariate Hawkes fitting pilot. It follows the preprocessing pipeline
and consumes local cleaned trip files at runtime.

## Inputs

The scripts use departure timestamps grouped by `start_station_id`. The
cross-station probe also uses station coordinates. Customer and subscriber
streams can be analysed independently because preprocessing preserves the user
type split.

## Point-process diagnostics

`modeling/pp_fitting/` contains two complementary analyses:

- **Univariate diagnostics:** stationarity checks, inter-event gap statistics,
  dispersion measures, and residual checks before and after an empirical
  hour-of-week adjustment.
- **Cross-station probe:** directional short-lag correlation among a selected
  set of busy stations after seasonal residualisation.

These diagnostics identify patterns worth modelling. They do not fit a full
multivariate Hawkes process or recover a direct causal interaction network.
Shared demand drivers, indirect paths, and omitted covariates can also produce
residual dependence.

## Univariate fitting pilot

`modeling/fitting/` fits an exponential-kernel univariate Hawkes model to a
small set of high-volume station streams after empirical time rescaling. Each
fit is compared with a homogeneous Poisson baseline using in-sample AIC.

The pilot establishes a reproducible fitting path and parameter summary. It is
not a city-scale forecasting system, and in-sample AIC improvement alone is not
model validation.

## Next modelling steps

A fuller modelling stage would add held-out evaluation, stronger seasonal and
weather covariates, regularized multivariate estimation, residual diagnostics,
and comparisons with non-Hawkes baselines. OD weights or station communities
can provide a structured way to limit the candidate interaction set.

See [modeling/README.md](../modeling/README.md) for runnable commands and the
subdirectory READMEs for script-specific inputs and outputs.
