"""
Cross-excitation probe for bike departures  (NO model fitting)
==============================================================
A MULTIVARIATE Hawkes process lets an event at station j raise the future
departure rate at station i:

    lambda_i(t) = mu_i(t) + sum_j sum_{t_k^j < t} phi_ij(t - t_k^j)

The cross kernels phi_ij (i != j) are the "cross-excitation". Detecting them
model-free is much harder than the univariate case, for three reasons:

  (1) COMBINATORICS : N stations -> N*(N-1) ordered pairs. Here N=347 -> ~120k
      directed pairs. We restrict to the K busiest stations for a demo.
  (2) COMMON DRIVER : every station peaks at 8am, so ALL pairs are strongly
      correlated at lag 0 from shared seasonality / weather -- this is
      common-cause, NOT cross-excitation. We deseasonalize, then look at the
      ASYMMETRY between positive and negative lags (excitation is directional
      and time-lagged; a common driver is symmetric).
  (3) IDENTIFIABILITY : even after that, j->i correlation can be indirect
      (j->m->i) or driven by an unobserved common cause. Separating DIRECT
      cross-excitation needs a fitted, regularized multivariate model.

So this script DETECTS a directional short-lag signal (feasible); it does NOT
claim to recover the true direct network (not feasible without fitting).

Statistic per ordered pair (j -> i), on deseasonalized z-scored bin counts:
    c_ij(tau) = (1/T) sum_t R_i[t] R_j[t - tau]      (j leads i for tau>0)
    short-lag directed strength : S_ij = sum_{tau=1..L} c_ij(tau)
    directional asymmetry       : A_ij = S_ij - S_ji   (>0 => j excites i more)
"""

import argparse
import os
from pathlib import Path
import numpy as np
import pandas as pd

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
import warnings

warnings.filterwarnings("ignore")

K_TOP     = 25          # number of busiest stations to probe (K*(K-1) pairs)
BIN_MIN   = 30          # time-bin resolution (minutes)
MAX_LAG   = 4           # lags 1..MAX_LAG bins are "short lag" (here up to 2 h)


def datetime_to_ns(values):
    """Convert pandas datetime Series to integer nanoseconds reliably."""
    return values.to_numpy(dtype="datetime64[ns]").astype("int64")


def resolve_columns(df):
    time_col = "started_at" if "started_at" in df.columns else "start_time"
    station_col = "start_station_id"
    lat_col = "start_lat" if "start_lat" in df.columns else "start_station_latitude"
    lng_col = "start_lng" if "start_lng" in df.columns else "start_station_longitude"

    required = [station_col, time_col, lat_col, lng_col]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    return time_col, station_col, lat_col, lng_col


def parse_args():
    parser = argparse.ArgumentParser(
        description="Probe directional cross-excitation signals across busy stations."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a cleaned trip CSV or CSV.GZ file.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for generated figures. Defaults to the current directory.",
    )
    return parser.parse_args()


# ── load ────────────────────────────────────────────────────────────────
def xcorr(R, tau):
    """K x K matrix; entry [i,j] = corr of R_i now with R_j tau bins earlier."""
    if tau == 0:
        return (R @ R.T) / R.shape[1]
    return (R[:, tau:] @ R[:, :-tau].T) / (R.shape[1] - tau)

def dist_km(a, b):
    la1, lo1 = np.radians(a)
    la2, lo2 = np.radians(b)
    h = np.sin((la2-la1)/2)**2 + np.cos(la1)*np.cos(la2)*np.sin((lo2-lo1)/2)**2
    return 6371 * 2 * np.arcsin(np.sqrt(h))


def main():
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading ...")
    df = pd.read_csv(args.input)
    time_col, station_col, lat_col, lng_col = resolve_columns(df)
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.dropna(subset=[station_col, lat_col, lng_col])

    top = df[station_col].value_counts().head(K_TOP).index.tolist()
    df = df[df[station_col].isin(top)].copy()
    coords = df.groupby(station_col)[[lat_col, lng_col]].first()

    # ── bin into a common K x T count matrix ──────────────────────────────
    bin_ns = BIN_MIN * 60 * 1_000_000_000
    t0 = df[time_col].min().floor("h")
    df["bin"] = ((datetime_to_ns(df[time_col]) - t0.value) // bin_ns).astype(int)
    T = df["bin"].max() + 1
    print(f"{len(top)} stations, {T:,} bins of {BIN_MIN} min "
          f"({T*BIN_MIN/60/24:.0f} days)")

    counts = np.zeros((len(top), T))
    sid_index = {s: k for k, s in enumerate(top)}
    for s, g in df.groupby(station_col):
        counts[sid_index[s]] = np.bincount(g["bin"].values, minlength=T)

    # ── deseasonalize: subtract hour-of-week profile, then z-score ────────
    bins_per_week = 7 * 24 * 60 // BIN_MIN
    slot = (np.arange(T) + (t0.dayofweek * 24 * 60 + t0.hour * 60) // BIN_MIN) % bins_per_week

    R = np.zeros_like(counts)
    for k in range(len(top)):
        profile = np.zeros(bins_per_week)
        np.add.at(profile, slot, counts[k])
        occ = np.bincount(slot, minlength=bins_per_week)
        profile = profile / np.maximum(occ, 1)
        resid = counts[k] - profile[slot]
        R[k] = (resid - resid.mean()) / (resid.std() + 1e-12)

    # ── lagged cross-correlation  c_ij(tau) = <R_i(t) R_j(t-tau)> ─────────
    C = np.stack([xcorr(R, tau) for tau in range(MAX_LAG + 1)])

    lag0 = C[0]
    S = C[1:].sum(axis=0)
    A = S - S.T
    sig = 2.0 / np.sqrt(T)

    # ── report ────────────────────────────────────────────────────────────
    iu = np.triu_indices(len(top), 1)
    print("\n" + "=" * 70)
    print("  HOW MUCH IS COMMON-DRIVER vs DIRECTIONAL?")
    print("=" * 70)
    print(f"  mean lag-0 corr (common seasonality residual): {lag0[iu].mean():.3f}")
    print(f"  mean |short-lag directed strength S_ij|      : {np.abs(S).mean():.3f}")
    print(f"  mean |asymmetry A_ij|                        : {np.abs(A).mean():.3f}")
    print(f"  noise band (|corr| < {sig:.3f} ~ not significant)")
    frac = (np.abs(A) > sig).mean()
    print(f"  fraction of directed pairs with |A| > noise  : {100*frac:.0f}%")

    print("\n  Strongest DIRECTIONAL pairs  (j -> i, by asymmetry A_ij):")
    order = np.argsort(A.ravel())[::-1]
    shown = 0
    print(f"  {'j':>12} {'->':^4} {'i':>12}   {'A_ij':>7} {'S_ij':>7} {'S_ji':>7} "
          f"{'lag0':>6}  {'dist_km':>7}")
    for idx in order:
        i, j = divmod(idx, len(top))
        if i == j:
            continue
        print(f"  {str(top[j]):>12} {'-->':^4} {str(top[i]):>12}   "
              f"{A[i,j]:>7.3f} {S[i,j]:>7.3f} {S[j,i]:>7.3f} {lag0[i,j]:>6.3f}  "
              f"{dist_km(coords.loc[top[i]], coords.loc[top[j]]):>7.2f}")
        shown += 1
        if shown >= 12:
            break

    # ── heatmaps ───────────────────────────────────────────────────────────
    fig_path = output_dir / "cross_excitation_check.png"
    fig, ax = plt.subplots(1, 3, figsize=(18, 5.2))
    for a, M, ttl in zip(
            ax, [lag0.copy(), S.copy(), A.copy()],
            ["lag-0 corr  (common driver,\nsymmetric -> NOT cross-excitation)",
             "short-lag directed strength S_ij\n(j leads i)",
             "asymmetry A_ij = S_ij - S_ji\n(directional cross-excitation signal)"]):
        np.fill_diagonal(M, np.nan)
        v = np.nanmax(np.abs(M))
        im = a.imshow(M, cmap="RdBu_r", vmin=-v, vmax=v)
        a.set_title(ttl, fontsize=10)
        a.set_xlabel("j (source)")
        a.set_ylabel("i (target)")
        plt.colorbar(im, ax=a, fraction=0.046)
    plt.suptitle(f"Cross-excitation probe — top {len(top)} stations, "
                 f"{BIN_MIN}-min bins, deseasonalized residuals", fontsize=13)
    plt.tight_layout()
    plt.savefig(fig_path, dpi=140, bbox_inches="tight")
    print(f"\nSaved: {fig_path}")


if __name__ == "__main__":
    main()
