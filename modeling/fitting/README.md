# Univariate Hawkes fitting pilot

`scripts/pilot_univariate_hawkes_fit.py` fits exponential-kernel univariate
Hawkes models to a small set of high-volume station departure streams after an
empirical hour-of-week time rescaling. It compares each fitted model with a
homogeneous Poisson baseline using in-sample AIC.

This is a pilot workflow, not a final multivariate city-scale model or a
causal analysis. In-sample AIC improvement alone does not establish that a
Hawkes process is the appropriate operational model.

## Run

From the repository root:

```bash
python modeling/fitting/scripts/pilot_univariate_hawkes_fit.py \
  --input outputs/san_francisco/cleaned_subscriber.csv \
  --output-dir outputs/san_francisco/hawkes_pilot \
  --top-n 5 \
  --min-events 5000
```

The script writes `pilot_univariate_hawkes_summary.csv` and a compact PNG
comparison chart to the specified output directory.
