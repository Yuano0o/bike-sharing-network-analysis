"""
Hawkes-Process Diagnostics for Bike Departures  (NO model fitting)
==================================================================
For each `start_station_id`, take departure times and ask whether the observed
timing has features that are *consistent with* a self-exciting process.

We never fit a Hawkes model. We only compute model-free signatures of
self-excitation / clustering, using five diagnostics:

    1. Stationarity check           (ADF + KPSS on hourly counts)
    2. Inter-event time distribution(CV, KS vs Exponential)
    3. Dispersion test              (Fano factor / index of dispersion)
    4. Branching-ratio heuristic    (n_hat = 1 - 1/sqrt(Fano_large))
    5. Residual analysis            (time-rescaling theorem -> Exp(1))

THE CONFOUNDER: bike departures have strong diurnal/weekly seasonality.
Seasonality alone makes a process look non-stationary, overdispersed and
non-exponential -- i.e. it fakes every Hawkes signature even with NO
self-excitation. So every diagnostic is run twice:

    RAW            : on the raw event stream  (sees seasonality + excitation)
    DESEASONALIZED : after rescaling time by an empirical baseline rate
                     lambda0(hour-of-week) estimated directly from the data.

If clustering survives this adjustment, it is a signal for further model
checking; it does not by itself establish genuine self-excitation. If it
disappears, seasonality is a plausible explanation for the raw clustering.

Estimating lambda0 from hour-of-week averages is NOT fitting a Hawkes model;
it is the standard non-parametric baseline used by the time-rescaling theorem.
"""

import argparse
import os
from pathlib import Path
import numpy as np
import pandas as pd

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
from scipy import stats
from statsmodels.tsa.stattools import adfuller, kpss
import warnings

warnings.filterwarnings("ignore")

MIN_EVENTS  = 2000      # only analyse stations with at least this many departures
TOP_N_PLOTS = 6         # number of busiest stations to draw diagnostic plots for
SEC_PER_HOUR = 3600.0


def datetime_to_ns(values):
    """Convert pandas datetime Series to integer nanoseconds reliably."""
    return values.to_numpy(dtype="datetime64[ns]").astype("int64")


# ──────────────────────────────────────────────────────────────────────────
# 1. EMPIRICAL HOUR-OF-WEEK BASELINE  lambda0(dow, hour)
#    (shared seasonal profile used to deseasonalize every station)
# ──────────────────────────────────────────────────────────────────────────
def hour_of_week(ts):
    """Map a DatetimeIndex/Series to an integer 0..167 (Mon 00h ... Sun 23h)."""
    return ts.dt.dayofweek * 24 + ts.dt.hour


def seasonal_rate_table(times):
    """
    Estimate a piecewise-constant baseline rate (events / second) for each of
    the 168 hour-of-week slots, using the station's own observed exposure.
    Returns array rate[0..167] in events per second.
    """
    grid_start = times.min().floor("h")
    grid_end   = times.max().ceil("h")
    grid       = pd.date_range(grid_start, grid_end, freq="h")[:-1]   # hour bins
    how_grid   = hour_of_week(pd.Series(grid))

    # exposure: how many seconds of observation fall in each hour-of-week slot
    exposure = np.bincount(how_grid, minlength=168) * SEC_PER_HOUR
    # counts: how many events fall in each hour-of-week slot
    counts   = np.bincount(hour_of_week(times), minlength=168)

    rate = np.divide(counts, exposure, out=np.zeros(168), where=exposure > 0)
    return rate, grid, how_grid


def rescaled_times(times, rate, grid, how_grid):
    """
    Time-rescaling theorem: tau_i = Lambda0(t_i) = integral of lambda0 up to t_i.
    Under an inhomogeneous Poisson process with intensity lambda0, the rescaled
    inter-event gaps  d_tau = diff(tau)  are i.i.d. Exp(1).

    lambda0 is piecewise-constant on hourly bins, so the compensator is exact:
    cumulative integral at hour boundaries + linear interpolation within an hour.
    """
    hour_rate = rate[how_grid]                       # rate active in each grid hour
    cum_at_bin_start = np.concatenate([[0.0], np.cumsum(hour_rate * SEC_PER_HOUR)])

    grid_start_ns = grid[0].value
    # which hour bin each event falls in
    t_sec   = (datetime_to_ns(times) - grid_start_ns) / 1e9
    bin_idx = np.minimum((t_sec // SEC_PER_HOUR).astype(int), len(hour_rate) - 1)
    bin_start_sec = bin_idx * SEC_PER_HOUR

    tau = cum_at_bin_start[bin_idx] + hour_rate[bin_idx] * (t_sec - bin_start_sec)
    return np.diff(np.sort(tau))


# ──────────────────────────────────────────────────────────────────────────
# 3. DIAGNOSTIC PRIMITIVES
# ──────────────────────────────────────────────────────────────────────────
def cv(gaps):
    """Coefficient of variation. 1 = Poisson, >1 = clustered/overdispersed."""
    m = gaps.mean()
    return gaps.std() / m if m > 0 else np.nan


def ks_exponential(gaps):
    """KS test of gaps vs an Exponential with the same mean. Returns p-value."""
    m = gaps.mean()
    if m <= 0 or len(gaps) < 20:
        return np.nan
    return stats.kstest(gaps, "expon", args=(0, m)).pvalue


def fano_factor(event_sec, window_sec, span):
    """
    Index of dispersion: Var(N)/E(N) for counts in fixed windows.
    1 = Poisson, >1 = overdispersed (clustering / self-excitation).
    """
    n_bins = int(span // window_sec)
    if n_bins < 10:
        return np.nan
    edges  = np.arange(n_bins + 1) * window_sec
    counts = np.histogram(event_sec, bins=edges)[0]
    m = counts.mean()
    return counts.var() / m if m > 0 else np.nan


def branching_ratio(fano_large):
    """
    Hardiman-Bouchaud heuristic. For a stationary Hawkes process the asymptotic
    Fano factor F -> 1/(1-n)^2, hence  n_hat = 1 - 1/sqrt(F).
    Only meaningful AFTER deseasonalizing (else seasonality inflates F -> n~1).
    """
    if not np.isfinite(fano_large) or fano_large < 1:
        return 0.0
    return float(np.clip(1.0 - 1.0 / np.sqrt(fano_large), 0.0, 0.999))


def stationarity(hourly_counts):
    """ADF (null=unit root) + KPSS (null=stationary) on hourly count series."""
    try:
        adf_p  = adfuller(hourly_counts, autolag="AIC")[1]
        kpss_p = kpss(hourly_counts, regression="c", nlags="auto")[1]
    except Exception:
        return np.nan, np.nan
    return adf_p, kpss_p


# ──────────────────────────────────────────────────────────────────────────
# 4. PER-STATION ANALYSIS
# ──────────────────────────────────────────────────────────────────────────
def analyse_station(times):
    """times: sorted pd.Series[datetime] for one station. Returns a dict."""
    n = len(times)
    span = (times.max() - times.min()).total_seconds()

    # ---- raw inter-event gaps (seconds) -------------------------------------
    event_sec = (datetime_to_ns(times) - times.iloc[0].value) / 1e9
    raw_gaps  = np.diff(event_sec)
    raw_gaps  = raw_gaps[raw_gaps >= 0]

    # ---- hourly counts for stationarity -------------------------------------
    hourly = (times.dt.floor("h").value_counts().sort_index())
    full   = pd.date_range(hourly.index.min(), hourly.index.max(), freq="h")
    hourly = hourly.reindex(full, fill_value=0)
    adf_p, kpss_p = stationarity(hourly.values)

    # ---- deseasonalize via time-rescaling -----------------------------------
    rate, grid, how_grid = seasonal_rate_table(times)
    des_gaps = rescaled_times(times, rate, grid, how_grid)   # ~Exp(1) if inhom-Poisson

    # ---- dispersion (raw vs deseasonalized) ---------------------------------
    fano_raw_24h = fano_factor(event_sec, 24 * SEC_PER_HOUR, span)
    # deseasonalized "time" has unit rate; total rescaled span ~ n events.
    des_sec   = np.concatenate([[0.0], np.cumsum(des_gaps)])
    des_span  = des_sec[-1]
    fano_des  = fano_factor(des_sec, des_span / 200.0, des_span)  # ~200 windows

    return {
        "station":            times.name,
        "n_events":           n,
        "days":               round(span / 86400, 1),
        # 1. stationarity
        "adf_p":              round(adf_p, 4)  if np.isfinite(adf_p)  else np.nan,
        "kpss_p":             round(kpss_p, 4) if np.isfinite(kpss_p) else np.nan,
        # 2. inter-event distribution
        "cv_raw":             round(cv(raw_gaps), 3),
        "cv_deseason":        round(cv(des_gaps), 3),
        "ks_exp_p_raw":       round(ks_exponential(raw_gaps), 4),
        "ks_exp_p_deseason":  round(ks_exponential(des_gaps), 4),
        # 3. dispersion
        "fano_raw_24h":       round(fano_raw_24h, 2) if np.isfinite(fano_raw_24h) else np.nan,
        "fano_deseason":      round(fano_des, 2)     if np.isfinite(fano_des)     else np.nan,
        # 4. branching ratio heuristic (deseasonalized)
        "branch_ratio_hat":   round(branching_ratio(fano_des), 3),
        # store gaps for plotting/verdict (not written to CSV)
        "_raw_gaps":          raw_gaps,
        "_des_gaps":          des_gaps,
    }


def verdict(r):
    """
    Combine deseasonalized signals into a coarse label. The deseasonalized
    diagnostics provide a coarse screening label; they do not discriminate a
    Hawkes process from all other clustered or misspecified processes.
    """
    excited = 0
    if np.isfinite(r["cv_deseason"]) and r["cv_deseason"] > 1.15:           excited += 1
    if np.isfinite(r["fano_deseason"]) and r["fano_deseason"] > 1.5:        excited += 1
    if np.isfinite(r["ks_exp_p_deseason"]) and r["ks_exp_p_deseason"] < 0.05: excited += 1
    if r["branch_ratio_hat"] > 0.2:                                         excited += 1
    if excited >= 3:
        return "Residual clustering signal after seasonal adjustment"
    if excited == 2:
        return "Mixed diagnostic signal"
    return "No strong residual clustering signal"


def resolve_columns(df):
    """Map project-local cleaned output columns to the names used below."""
    time_col = "started_at" if "started_at" in df.columns else "start_time"
    station_col = "start_station_id"

    if station_col not in df.columns:
        raise ValueError(f"Missing required column: {station_col}")
    if time_col not in df.columns:
        raise ValueError("Missing required time column: expected started_at or start_time")

    return time_col, station_col


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run Hawkes-style univariate diagnostics on cleaned bikeshare trips."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a cleaned trip CSV or CSV.GZ file.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for CSV and PNG outputs. Defaults to the current directory.",
    )
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────
# 5. RUN OVER ALL STATIONS
# ──────────────────────────────────────────────────────────────────────────
def main():
  args = parse_args()
  output_dir = Path(args.output_dir).resolve()
  output_dir.mkdir(parents=True, exist_ok=True)

  print("Loading trip times + start_station_id ...")
  df = pd.read_csv(args.input)
  time_col, station_col = resolve_columns(df)
  df[time_col] = pd.to_datetime(df[time_col])
  df = df.dropna(subset=[station_col]).sort_values(time_col).reset_index(drop=True)
  print(f"  {len(df):,} departures, {df[station_col].nunique()} stations, "
        f"{df[time_col].min()} -> {df[time_col].max()}")

  counts_per_station = df[station_col].value_counts()
  stations = counts_per_station[counts_per_station >= MIN_EVENTS].index.tolist()
  print(f"Analysing {len(stations)} stations with >= {MIN_EVENTS} departures ...\n")

  records = []
  groups = {sid: g for sid, g in df.groupby(station_col)[time_col]}
  for i, sid in enumerate(stations, 1):
      times = groups[sid].sort_values().reset_index(drop=True)
      times.name = sid
      rec = analyse_station(times)
      rec["verdict"] = verdict(rec)
      records.append(rec)
      if i % 25 == 0:
          print(f"  ...{i}/{len(stations)}")

  res = pd.DataFrame(records)
  csv_cols = [c for c in res.columns if not c.startswith("_")]
  csv_path = output_dir / "hawkes_diagnostics_results.csv"
  fig_path = output_dir / "hawkes_diagnostics.png"
  res[csv_cols].to_csv(csv_path, index=False)

  # ── 6. SUMMARY ──────────────────────────────────────────────────────────
  print("\n" + "=" * 70)
  print("  SUMMARY  (exploratory deseasonalized diagnostic labels)")
  print("=" * 70)
  for v, n in res["verdict"].value_counts().items():
      print(f"  {v:55s}: {n:4d}  ({100*n/len(res):5.1f}%)")

  print("\n  Median across stations:")
  print(f"    CV  raw / deseasonalized        : "
        f"{res.cv_raw.median():.2f} / {res.cv_deseason.median():.2f}   (1.0 = Poisson)")
  print(f"    Fano raw(24h) / deseasonalized  : "
        f"{res.fano_raw_24h.median():.1f} / {res.fano_deseason.median():.2f}   (1.0 = Poisson)")
  print(f"    Branching ratio (deseasonalized): {res.branch_ratio_hat.median():.2f}")
  print(f"    KS-exp p (deseason) < 0.05      : "
        f"{(res.ks_exp_p_deseason < 0.05).mean()*100:.0f}% of stations reject Exp(1)")

  print("\n  Top stations by departures:")
  show = ["station","n_events","cv_raw","cv_deseason","fano_raw_24h",
          "fano_deseason","branch_ratio_hat","verdict"]
  print(res.sort_values("n_events", ascending=False)[show].head(12).to_string(index=False))

  # ── 7. DIAGNOSTIC PLOTS for the busiest stations ─────────────────────────
  top = res.sort_values("n_events", ascending=False).head(TOP_N_PLOTS)
  fig, axes = plt.subplots(TOP_N_PLOTS, 3, figsize=(16, 3.2 * TOP_N_PLOTS))

  for i, (_, r) in enumerate(top.iterrows()):
      raw_gaps, des_gaps = r["_raw_gaps"], r["_des_gaps"]

      # (a) raw inter-event histogram vs exponential
      ax = axes[i, 0]
      g = raw_gaps[raw_gaps < np.quantile(raw_gaps, 0.99)]
      ax.hist(g, bins=60, density=True, alpha=0.6, color="steelblue")
      xs = np.linspace(0, g.max(), 200)
      ax.plot(xs, stats.expon.pdf(xs, scale=raw_gaps.mean()), "r--", lw=2, label="Exp (Poisson)")
      ax.set_title(f"St.{r['station']}  RAW gaps  (CV={r['cv_raw']})", fontsize=10)
      ax.legend(fontsize=8); ax.set_xlabel("seconds")

      # (b) deseasonalized rescaled-gap QQ plot vs Exp(1)
      ax = axes[i, 1]
      q = np.linspace(0.01, 0.99, 100)
      ax.plot(stats.expon.ppf(q), np.quantile(des_gaps, q), ".", color="darkorange")
      lim = stats.expon.ppf(0.99)
      ax.plot([0, lim], [0, lim], "k--", lw=1)
      ax.set_title(f"DESEASON QQ vs Exp(1)  (CV={r['cv_deseason']})", fontsize=10)
      ax.set_xlabel("theoretical"); ax.set_ylabel("empirical")

      # (c) Fano factor vs window size (raw)
      ax = axes[i, 2]
      span = r["days"] * 86400
      ev = np.cumsum(np.concatenate([[0.0], raw_gaps]))
      Ws = np.array([0.5, 1, 2, 4, 8, 12, 24, 48, 96, 168]) * SEC_PER_HOUR
      Fs = [fano_factor(ev, w, span) for w in Ws]
      ax.plot(Ws / SEC_PER_HOUR, Fs, "o-", color="seagreen")
      ax.axhline(1, color="r", ls="--", lw=1, label="Poisson")
      ax.set_xscale("log"); ax.set_title(f"Fano vs window  (n_hat={r['branch_ratio_hat']})", fontsize=10)
      ax.set_xlabel("window (hours)"); ax.set_ylabel("Var/Mean"); ax.legend(fontsize=8)

  plt.suptitle("Hawkes diagnostics — busiest stations "
               "(col1: raw gaps, col2: deseasonalized residuals, col3: dispersion)",
               fontsize=13, y=1.005)
  plt.tight_layout()
  plt.savefig(fig_path, dpi=140, bbox_inches="tight")
  print(f"\nSaved:\n  - {csv_path}\n  - {fig_path}")
  return res


if __name__ == "__main__":
    main()
