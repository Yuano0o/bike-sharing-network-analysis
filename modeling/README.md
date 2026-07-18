# Exploratory point-process modelling

This directory follows, rather than replaces, the data pipeline in `tools/`.
All scripts accept a local cleaned-trip file at runtime; no model inputs or
generated results are committed to the repository.

## Components

| Directory | Scope |
| --- | --- |
| `pp_fitting/` | deseasonalized univariate diagnostics and a directional cross-correlation probe |
| `fitting/` | limited pilot fit of univariate exponential-kernel Hawkes models |

Install the project dependencies from the repository root:

```bash
python -m pip install -r requirements.txt
```

## Important interpretation note

These are exploratory analyses. A residual clustering signal after the
implemented seasonal adjustment is evidence to investigate, not proof of a
causal self-excitation mechanism. The cross-excitation script is explicitly a
screening probe; it does not identify a direct multivariate interaction
network. The fitting pilot is not a city-scale production model.

See [pp_fitting/README.md](pp_fitting/README.md) and
[fitting/README.md](fitting/README.md) for runnable commands and assumptions.
