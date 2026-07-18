#!/usr/bin/env python3
"""Independent analysis script for cleaned bikeshare datasets.

This script is meant to collect the "information analysis" parts that were
previously scattered in notebooks. It does not run automatically as part of the
cleaning pipeline, so large batch cleaning remains lightweight.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    if not os.environ.get("MPLBACKEND"):
        os.environ["MPLBACKEND"] = "Agg"
    import matplotlib.pyplot as plt
except ModuleNotFoundError:  # pragma: no cover
    plt = None

try:
    from scipy.stats import kruskal
except ModuleNotFoundError:  # pragma: no cover
    kruskal = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run notebook-style bikeshare analysis on cleaned outputs.")
    parser.add_argument(
        "--cleaned",
        required=True,
        help="Path to a cleaned CSV, e.g. cleaned_subscriber.csv or cleaned_customer.csv.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for analysis outputs.",
    )
    parser.add_argument(
        "--user-type-label",
        help="Optional label written into outputs when the cleaned file already contains a single user type.",
    )
    parser.add_argument(
        "--run-time-tests",
        action="store_true",
        help="Run heavier time-dependence tests such as Kruskal-Wallis per pair.",
    )
    parser.add_argument(
        "--min-pair-trips",
        type=int,
        default=30,
        help="Minimum trips per pair for pair-level analyses. Default: 30.",
    )
    parser.add_argument(
        "--top-n-pairs",
        type=int,
        default=20,
        help="Number of top pairs or top stations to export in summary tables. Default: 20.",
    )
    parser.add_argument(
        "--time-test-min-days",
        type=int,
        default=3,
        help="Minimum number of distinct days required before a pair enters time-dependence tests.",
    )
    return parser.parse_args()


def ensure_plotting() -> None:
    if plt is None:
        raise SystemExit("matplotlib is required for analysis plots.")


def ensure_kruskal() -> None:
    if kruskal is None:
        raise SystemExit("scipy is required for --run-time-tests.")


def load_cleaned(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    for col in ["start_station_id", "end_station_id"]:
        df[col] = df[col].astype("string")

    for col in ["started_at", "ended_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if "trip_date" in df.columns:
        df["trip_date"] = pd.to_datetime(df["trip_date"], errors="coerce")
    elif "started_at" in df.columns:
        df["trip_date"] = df["started_at"].dt.normalize()

    if "duration_min" not in df.columns and "duration_sec" in df.columns:
        df["duration_min"] = pd.to_numeric(df["duration_sec"], errors="coerce") / 60.0
    elif "duration_min" in df.columns:
        df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce")

    if "started_at" in df.columns:
        df["hour"] = df["started_at"].dt.hour

    return df


def write_pair_summary(df: pd.DataFrame, output_dir: Path, min_pair_trips: int) -> pd.DataFrame:
    pair_stats = (
        df.groupby(["start_station_id", "end_station_id"])["duration_min"]
        .agg(
            trip_count="count",
            mean_min="mean",
            std_min="std",
            min_min="min",
            q25_min=lambda x: x.quantile(0.25),
            median_min="median",
            q75_min=lambda x: x.quantile(0.75),
            q95_min=lambda x: x.quantile(0.95),
            max_min="max",
        )
        .reset_index()
    )
    pair_stats = pair_stats[pair_stats["trip_count"] >= min_pair_trips].copy()
    pair_stats.to_csv(output_dir / "pair_duration_summary.csv", index=False)
    return pair_stats


def write_station_consistency(df: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    start_info = df[["start_station_id", "start_station_name", "start_lat", "start_lng"]].copy()
    start_info.columns = ["station_id", "name", "latitude", "longitude"]

    end_info = df[["end_station_id", "end_station_name", "end_lat", "end_lng"]].copy()
    end_info.columns = ["station_id", "name", "latitude", "longitude"]

    all_stations = pd.concat([start_info, end_info], ignore_index=True)
    all_stations = all_stations.dropna(subset=["station_id"]).copy()

    canonical = (
        all_stations.groupby("station_id")
        .agg(
            canonical_name=("name", lambda x: x.mode().iloc[0] if not x.mode().empty else pd.NA),
            canonical_lat=("latitude", lambda x: x.mode().iloc[0] if not x.mode().empty else pd.NA),
            canonical_lng=("longitude", lambda x: x.mode().iloc[0] if not x.mode().empty else pd.NA),
            observation_count=("name", "size"),
        )
        .reset_index()
    )

    conflicts = (
        all_stations.groupby("station_id")
        .agg(
            n_name_versions=("name", "nunique"),
            n_lat_versions=("latitude", "nunique"),
            n_lng_versions=("longitude", "nunique"),
        )
        .reset_index()
    )
    conflicts = conflicts[
        (conflicts["n_name_versions"] > 1)
        | (conflicts["n_lat_versions"] > 1)
        | (conflicts["n_lng_versions"] > 1)
    ].copy()

    canonical.to_csv(output_dir / "station_canonical_analysis.csv", index=False)
    conflicts.to_csv(output_dir / "station_consistency_conflicts.csv", index=False)
    return canonical, conflicts


def haversine(lat1, lon1, lat2, lon2) -> float:
    if any(pd.isna(v) for v in [lat1, lon1, lat2, lon2]):
        return math.nan
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def add_distance_metrics(pair_stats: pd.DataFrame, station_canonical: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    station_lookup = station_canonical[["station_id", "canonical_lat", "canonical_lng"]].copy()

    enriched = pair_stats.merge(
        station_lookup,
        left_on="start_station_id",
        right_on="station_id",
        how="left",
    ).rename(columns={"canonical_lat": "start_lat", "canonical_lng": "start_lng"}).drop(columns="station_id")

    enriched = enriched.merge(
        station_lookup,
        left_on="end_station_id",
        right_on="station_id",
        how="left",
    ).rename(columns={"canonical_lat": "end_lat", "canonical_lng": "end_lng"}).drop(columns="station_id")

    enriched["distance_km"] = enriched.apply(
        lambda row: haversine(row["start_lat"], row["start_lng"], row["end_lat"], row["end_lng"]),
        axis=1,
    )
    enriched.to_csv(output_dir / "pair_duration_distance_summary.csv", index=False)
    return enriched


def write_most_visited_outputs(df: pd.DataFrame, pair_stats: pd.DataFrame, output_dir: Path, top_n: int) -> None:
    top_end = (
        pair_stats.groupby("end_station_id")["trip_count"]
        .sum()
        .reset_index(name="total_arrivals")
        .sort_values("total_arrivals", ascending=False)
    )
    top_end.to_csv(output_dir / "most_visited_end_stations.csv", index=False)

    if top_end.empty:
        return

    top_end_id = str(top_end.iloc[0]["end_station_id"])
    top_end_pairs = (
        pair_stats[pair_stats["end_station_id"] == top_end_id]
        .sort_values("trip_count", ascending=False)
        .head(top_n)
        .copy()
    )
    top_end_pairs.to_csv(output_dir / "most_visited_end_station_origins.csv", index=False)

    top_pairs = pair_stats.sort_values("trip_count", ascending=False).head(top_n).copy()
    top_pairs.to_csv(output_dir / "top_pairs_by_trip_count.csv", index=False)

    top_pair_keys = top_pairs[["start_station_id", "end_station_id"]].drop_duplicates()
    top_pair_trips = df.merge(top_pair_keys, on=["start_station_id", "end_station_id"], how="inner")
    top_pair_trips.to_csv(output_dir / "top_pair_trip_level_sample.csv", index=False)


def plot_distance_vs_duration(pair_stats: pd.DataFrame, output_dir: Path) -> None:
    ensure_plotting()
    subset = pair_stats.dropna(subset=["distance_km", "mean_min"]).copy()
    if subset.empty:
        return

    plt.figure(figsize=(8, 6))
    plt.scatter(subset["distance_km"], subset["mean_min"], alpha=0.35, s=18, color="#4C78A8")
    plt.xlabel("Distance (km)")
    plt.ylabel("Mean duration (min)")
    plt.title("Pair distance vs. mean duration")
    plt.tight_layout()
    plt.savefig(output_dir / "distance_vs_mean_duration.png", dpi=180)
    plt.close()


def plot_hourly_duration(df: pd.DataFrame, output_dir: Path) -> None:
    ensure_plotting()
    if "hour" not in df.columns:
        return

    hourly = (
        df.groupby("hour")["duration_min"]
        .agg(mean_duration="mean", median_duration="median", trip_count="count")
        .reset_index()
    )
    hourly.to_csv(output_dir / "hourly_duration_summary.csv", index=False)

    plt.figure(figsize=(9, 5))
    plt.plot(hourly["hour"], hourly["mean_duration"], marker="o", color="#F58518", label="Mean")
    plt.plot(hourly["hour"], hourly["median_duration"], marker="s", color="#54A24B", label="Median")
    plt.xlabel("Hour of day")
    plt.ylabel("Duration (min)")
    plt.title("Trip duration by hour of day")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "hourly_duration_profile.png", dpi=180)
    plt.close()


def run_time_dependence_tests(
    df: pd.DataFrame,
    output_dir: Path,
    min_pair_trips: int,
    time_test_min_days: int,
) -> None:
    ensure_kruskal()
    working = df.copy()
    working = working[working["start_station_id"] != working["end_station_id"]].copy()

    candidates = working.groupby(["start_station_id", "end_station_id"]).filter(
        lambda x: len(x) >= min_pair_trips and x["trip_date"].nunique() >= time_test_min_days
    )

    rows = []
    for (start_id, end_id), group in candidates.groupby(["start_station_id", "end_station_id"]):
        daily_groups = [g["duration_min"].dropna().values for _, g in group.groupby("trip_date") if len(g) >= 2]
        if len(daily_groups) < time_test_min_days:
            continue
        stat, p_value = kruskal(*daily_groups)
        rows.append(
            {
                "start_station_id": start_id,
                "end_station_id": end_id,
                "n_days": len(daily_groups),
                "trip_count": len(group),
                "kruskal_stat": stat,
                "p_value": p_value,
                "reject_h0_0_05": p_value < 0.05,
            }
        )

    pd.DataFrame(rows).to_csv(output_dir / "pair_time_dependence_kruskal.csv", index=False)


def write_overall_summary(
    df: pd.DataFrame,
    pair_stats: pd.DataFrame,
    station_conflicts: pd.DataFrame,
    output_dir: Path,
    user_type_label: Optional[str],
) -> None:
    summary = pd.DataFrame(
        [
            {
                "user_type_label": user_type_label or (
                    df["user_type"].dropna().iloc[0] if "user_type" in df.columns and not df.empty else pd.NA
                ),
                "rows": len(df),
                "unique_start_stations": df["start_station_id"].nunique(),
                "unique_end_stations": df["end_station_id"].nunique(),
                "unique_pairs_after_min_filter": len(pair_stats),
                "station_conflict_count": len(station_conflicts),
                "duration_mean_min": df["duration_min"].mean(),
                "duration_median_min": df["duration_min"].median(),
            }
        ]
    )
    summary.to_csv(output_dir / "analysis_summary.csv", index=False)


def main() -> None:
    args = parse_args()

    cleaned_path = Path(args.cleaned)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_cleaned(cleaned_path)
    pair_stats = write_pair_summary(df, output_dir, min_pair_trips=args.min_pair_trips)
    station_canonical, station_conflicts = write_station_consistency(df, output_dir)
    pair_stats = add_distance_metrics(pair_stats, station_canonical, output_dir)
    write_most_visited_outputs(df, pair_stats, output_dir, top_n=args.top_n_pairs)
    plot_distance_vs_duration(pair_stats, output_dir)
    plot_hourly_duration(df, output_dir)
    write_overall_summary(df, pair_stats, station_conflicts, output_dir, args.user_type_label)

    if args.run_time_tests:
        run_time_dependence_tests(
            df,
            output_dir,
            min_pair_trips=args.min_pair_trips,
            time_test_min_days=args.time_test_min_days,
        )

    print(f"[DONE] Analysis outputs written to {output_dir.resolve()}")


if __name__ == "__main__":
    main()
