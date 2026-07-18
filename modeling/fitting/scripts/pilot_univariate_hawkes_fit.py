"""Fit a small exploratory univariate Hawkes-model pilot.

The script compares in-sample AIC of an exponential-kernel Hawkes model with
a homogeneous Poisson baseline after empirical hour-of-week time rescaling.
It is a reproducible experiment scaffold, not a causal test or a production
multivariate model.
"""

import os
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize


SEC_PER_HOUR = 3600.0
MAX_BRANCHING = 0.995


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pilot univariate Hawkes fitting on deseasonalized station event streams."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a cleaned trip CSV or CSV.GZ file.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for pilot fitting outputs.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of busiest stations to fit in the pilot run.",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=5000,
        help="Minimum events required for a station to be eligible.",
    )
    return parser.parse_args()


def hour_of_week(ts):
    return ts.dt.dayofweek * 24 + ts.dt.hour


def datetime_to_ns(values):
    return values.to_numpy(dtype="datetime64[ns]").astype("int64")


def seasonal_rate_table(times):
    grid_start = times.min().floor("h")
    grid_end = times.max().ceil("h")
    grid = pd.date_range(grid_start, grid_end, freq="h")[:-1]
    how_grid = hour_of_week(pd.Series(grid))

    exposure = np.bincount(how_grid, minlength=168) * SEC_PER_HOUR
    counts = np.bincount(hour_of_week(times), minlength=168)
    rate = np.divide(counts, exposure, out=np.zeros(168), where=exposure > 0)
    return rate, grid, how_grid


def rescaled_event_times(times):
    rate, grid, how_grid = seasonal_rate_table(times)
    hour_rate = rate[how_grid]
    cum_at_bin_start = np.concatenate([[0.0], np.cumsum(hour_rate * SEC_PER_HOUR)])

    grid_start_ns = grid[0].value
    t_sec = (datetime_to_ns(times) - grid_start_ns) / 1e9
    bin_idx = np.minimum((t_sec // SEC_PER_HOUR).astype(int), len(hour_rate) - 1)
    bin_start_sec = bin_idx * SEC_PER_HOUR
    tau = cum_at_bin_start[bin_idx] + hour_rate[bin_idx] * (t_sec - bin_start_sec)
    return np.sort(tau)


def poisson_loglik(event_times):
    horizon = float(event_times[-1])
    n_events = len(event_times)
    mu_hat = n_events / horizon
    loglik = n_events * np.log(mu_hat) - mu_hat * horizon
    return {
        "mu": mu_hat,
        "loglik": loglik,
        "aic": 2 * 1 - 2 * loglik,
    }


def hawkes_neg_loglik(theta, event_times):
    log_mu, log_beta, logit_eta = theta
    mu = np.exp(log_mu)
    beta = np.exp(log_beta)
    eta = MAX_BRANCHING / (1.0 + np.exp(-logit_eta))
    alpha = eta * beta

    n_events = len(event_times)
    horizon = float(event_times[-1])

    g = np.zeros(n_events)
    if n_events > 1:
        deltas = np.diff(event_times)
        for i in range(1, n_events):
            g[i] = np.exp(-beta * deltas[i - 1]) * (1.0 + g[i - 1])

    intensity = mu + alpha * g
    if np.any(intensity <= 0.0) or not np.all(np.isfinite(intensity)):
        return 1e100

    compensator = mu * horizon + eta * np.sum(1.0 - np.exp(-beta * (horizon - event_times)))
    loglik = np.sum(np.log(intensity)) - compensator
    if not np.isfinite(loglik):
        return 1e100
    return -loglik


def fit_hawkes(event_times):
    horizon = float(event_times[-1])
    rate = len(event_times) / horizon
    init_mu = max(rate * 0.7, 1e-4)
    init_beta = 1.0
    init_eta = 0.3
    x0 = np.array([
        np.log(init_mu),
        np.log(init_beta),
        np.log(init_eta / (MAX_BRANCHING - init_eta)),
    ])

    result = minimize(
        hawkes_neg_loglik,
        x0=x0,
        args=(event_times,),
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(result.message)

    log_mu, log_beta, logit_eta = result.x
    mu = float(np.exp(log_mu))
    beta = float(np.exp(log_beta))
    eta = float(MAX_BRANCHING / (1.0 + np.exp(-logit_eta)))
    alpha = eta * beta
    loglik = -float(result.fun)
    return {
        "mu": mu,
        "beta": beta,
        "alpha": alpha,
        "eta": eta,
        "loglik": loglik,
        "aic": 2 * 3 - 2 * loglik,
        "converged": bool(result.success),
        "iterations": int(result.nit),
    }


def load_station_series(input_path, min_events, top_n):
    df = pd.read_csv(input_path, usecols=["started_at", "start_station_id"])
    df["started_at"] = pd.to_datetime(df["started_at"])
    df = df.dropna(subset=["start_station_id"]).sort_values("started_at")

    counts = df["start_station_id"].value_counts()
    eligible = counts[counts >= min_events].head(top_n)
    station_ids = eligible.index.tolist()

    grouped = {sid: g.reset_index(drop=True) for sid, g in df.groupby("start_station_id")["started_at"]}
    return station_ids, grouped


def create_summary_plot(summary, output_path):
    stations = summary["station"].tolist()
    x = np.arange(len(stations))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))

    axes[0].bar(x, summary["eta_hat"], color="#2a9d8f")
    axes[0].axhline(0.0, color="black", linewidth=0.8)
    axes[0].set_xticks(x, stations, rotation=30, ha="right")
    axes[0].set_title("Pilot Hawkes branching ratio")
    axes[0].set_ylabel("eta_hat")

    axes[1].bar(x, summary["delta_aic_poisson_minus_hawkes"], color="#264653")
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_xticks(x, stations, rotation=30, ha="right")
    axes[1].set_title("AIC improvement over Poisson")
    axes[1].set_ylabel("delta AIC")

    plt.suptitle("Pilot univariate Hawkes fit on deseasonalized station streams")
    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")


def main():
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    station_ids, grouped = load_station_series(args.input, args.min_events, args.top_n)
    records = []

    print(f"Running pilot fit for {len(station_ids)} stations")
    for idx, station_id in enumerate(station_ids, start=1):
        print(f"  [{idx}/{len(station_ids)}] {station_id}")
        times = grouped[station_id]
        tau = rescaled_event_times(times)
        if len(tau) < 2 or tau[-1] <= 0:
            continue

        poisson = poisson_loglik(tau)
        hawkes = fit_hawkes(tau)
        records.append({
            "station": station_id,
            "n_events": len(tau),
            "rescaled_horizon": round(float(tau[-1]), 3),
            "poisson_mu_hat": round(poisson["mu"], 6),
            "poisson_loglik": round(poisson["loglik"], 3),
            "poisson_aic": round(poisson["aic"], 3),
            "hawkes_mu_hat": round(hawkes["mu"], 6),
            "hawkes_beta_hat": round(hawkes["beta"], 6),
            "hawkes_alpha_hat": round(hawkes["alpha"], 6),
            "eta_hat": round(hawkes["eta"], 6),
            "hawkes_loglik": round(hawkes["loglik"], 3),
            "hawkes_aic": round(hawkes["aic"], 3),
            "delta_aic_poisson_minus_hawkes": round(poisson["aic"] - hawkes["aic"], 3),
            "converged": hawkes["converged"],
            "iterations": hawkes["iterations"],
        })

    summary = pd.DataFrame(records).sort_values("delta_aic_poisson_minus_hawkes", ascending=False)
    summary_path = output_dir / "pilot_univariate_hawkes_summary.csv"
    figure_path = output_dir / "pilot_univariate_hawkes_overview.png"
    summary.to_csv(summary_path, index=False)
    create_summary_plot(summary, figure_path)

    print("\nTop pilot results:")
    print(summary[[
        "station",
        "n_events",
        "poisson_aic",
        "hawkes_aic",
        "delta_aic_poisson_minus_hawkes",
        "eta_hat",
    ]].to_string(index=False))
    print(f"\nSaved:\n  - {summary_path}\n  - {figure_path}")


if __name__ == "__main__":
    main()
