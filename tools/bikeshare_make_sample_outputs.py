#!/usr/bin/env python3
"""Build a time-windowed sample_outputs folder from cleaned full outputs.

This keeps the exact cleaning logic from the full run, but avoids mixing too
many years when you want a smaller, prediction-friendly subset such as a
two-year window.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from bikeshare_cleaning import (
    DEFAULT_THRESHOLDS,
    build_station_outputs,
    plot_duration_histograms,
    plot_top_pairs,
    summarize_thresholds,
    update_station_registry,
    write_chunk_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create sample_outputs from cleaned full outputs.")
    parser.add_argument("--input-dir", required=True, help="Path to city full_outputs or full_outputs_v2 directory.")
    parser.add_argument("--output-dir", required=True, help="Where the filtered sample outputs should be written.")
    parser.add_argument("--start-year", type=int, help="Inclusive sample start year, for example 2022.")
    parser.add_argument("--end-year", type=int, help="Inclusive sample end year, for example 2023.")
    parser.add_argument(
        "--latest-n-years",
        type=int,
        default=None,
        help="If start/end year are not given, automatically keep the latest N observed years.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=200_000,
        help="Rows per chunk while filtering large cleaned CSVs.",
    )
    parser.add_argument(
        "--duration-thresholds",
        type=int,
        nargs="+",
        default=list(DEFAULT_THRESHOLDS),
        help="Threshold summary table to compare 90/120/180/240 min style cutoffs.",
    )
    parser.add_argument(
        "--skip-plots",
        action="store_true",
        help="Skip plotting sample histograms and top-pairs plots.",
    )
    return parser.parse_args()


def cleaned_inputs(input_dir: Path) -> List[Tuple[str, Path]]:
    paths = [
        ("Customer", input_dir / "cleaned_customer.csv"),
        ("Subscriber", input_dir / "cleaned_subscriber.csv"),
    ]
    existing = [(user_type, path) for user_type, path in paths if path.exists()]
    if not existing:
        raise FileNotFoundError(f"No cleaned_customer.csv or cleaned_subscriber.csv found under {input_dir}")
    return existing


def discover_years(paths: Iterable[Path], chunksize: int) -> List[int]:
    years = set()
    for path in paths:
        for chunk in pd.read_csv(path, usecols=["started_at"], chunksize=chunksize):
            started = pd.to_datetime(chunk["started_at"], errors="coerce")
            years.update(int(year) for year in started.dt.year.dropna().unique())
    return sorted(years)


def resolve_year_window(args: argparse.Namespace, paths: Iterable[Path]) -> Tuple[int, int]:
    if args.start_year is not None or args.end_year is not None:
        if args.start_year is None or args.end_year is None:
            raise ValueError("Please provide both --start-year and --end-year together.")
        if args.start_year > args.end_year:
            raise ValueError("--start-year must be <= --end-year.")
        return args.start_year, args.end_year

    latest_n = args.latest_n_years or 2
    years = discover_years(paths, args.chunksize)
    if not years:
        raise ValueError("Could not detect any years from cleaned inputs.")
    if len(years) < latest_n:
        return years[0], years[-1]
    selected = years[-latest_n:]
    return selected[0], selected[-1]


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = cleaned_inputs(input_dir)
    start_year, end_year = resolve_year_window(args, [path for _, path in inputs])

    station_registry = defaultdict(lambda: {"name": Counter(), "coord": Counter()})
    pair_counts = Counter()
    start_totals = Counter()
    duration_threshold_rows: List[dict] = []
    duration_samples: Dict[str, List[float]] = defaultdict(list)
    user_type_counts = Counter()
    wrote_cleaned = set()

    total_rows_seen = 0
    removed_outside_window = 0
    city_name: Optional[str] = None

    for expected_user_type, path in inputs:
        for chunk in pd.read_csv(path, chunksize=args.chunksize, low_memory=False):
            total_rows_seen += len(chunk)
            chunk["started_at"] = pd.to_datetime(chunk["started_at"], errors="coerce")
            if "ended_at" in chunk.columns:
                chunk["ended_at"] = pd.to_datetime(chunk["ended_at"], errors="coerce")
            year_mask = chunk["started_at"].dt.year.between(start_year, end_year, inclusive="both")
            removed_outside_window += int((~year_mask).sum())
            sample = chunk.loc[year_mask].copy()
            if sample.empty:
                continue

            if city_name is None and "city" in sample.columns and not sample["city"].dropna().empty:
                city_name = str(sample["city"].dropna().iloc[0])

            for user_type, group in sample.groupby("user_type"):
                duration_threshold_rows.extend(summarize_thresholds(group, args.duration_thresholds, user_type))
                user_type_counts[user_type] += len(group)
                if len(duration_samples[user_type]) < 250_000:
                    remaining = 250_000 - len(duration_samples[user_type])
                    duration_samples[user_type].extend(group["duration_min"].head(remaining).tolist())

                pair_frame = (
                    group.groupby(["start_station_id", "end_station_id"]).size().reset_index(name="trip_count")
                )
                for row in pair_frame.itertuples(index=False):
                    pair_counts[(user_type, row.start_station_id, row.end_station_id)] += int(row.trip_count)
                    start_totals[(user_type, row.start_station_id)] += int(row.trip_count)

                out_path = output_dir / f"cleaned_{user_type.lower()}.csv"
                append = out_path in wrote_cleaned
                write_chunk_csv(out_path, group, append=append)
                wrote_cleaned.add(out_path)

            update_station_registry(station_registry, sample)

    pair_rows = []
    for (user_type, start_id, end_id), trip_count in pair_counts.items():
        total_from_start = start_totals[(user_type, start_id)]
        pair_rows.append(
            {
                "user_type": user_type,
                "start_station_id": start_id,
                "end_station_id": end_id,
                "trip_count": trip_count,
                "total_from_start": total_from_start,
                "normalized_count": trip_count / total_from_start if total_from_start else 0.0,
            }
        )
    pair_df = pd.DataFrame(pair_rows)
    if not pair_df.empty:
        pair_df = pair_df.sort_values(
            ["user_type", "normalized_count", "trip_count"], ascending=[True, False, False]
        )

    station_lookup, station_conflicts = build_station_outputs(station_registry)

    overall_summary = pd.DataFrame(
        [
            {
                "city": city_name or input_dir.parent.name,
                "source_output_dir": str(input_dir),
                "sample_start_year": start_year,
                "sample_end_year": end_year,
                "rows_seen_in_full_outputs": total_rows_seen,
                "removed_outside_window": removed_outside_window,
                "kept_rows": int(sum(user_type_counts.values())),
            }
        ]
    )
    by_user_type = pd.DataFrame(
        [{"user_type": user_type, "kept_rows": count} for user_type, count in sorted(user_type_counts.items())]
    )
    thresholds_df = pd.DataFrame(duration_threshold_rows)
    if not thresholds_df.empty:
        thresholds_df = (
            thresholds_df.groupby(["user_type", "threshold_min"], as_index=False)
            .agg(
                rows_before_threshold=("rows_before_threshold", "sum"),
                rows_removed=("rows_removed", "sum"),
                rows_kept=("rows_kept", "sum"),
            )
        )
        thresholds_df["removed_share"] = (
            thresholds_df["rows_removed"] / thresholds_df["rows_before_threshold"]
        ).fillna(0.0)

    overall_summary.to_csv(output_dir / "summary_overall.csv", index=False)
    by_user_type.to_csv(output_dir / "summary_by_user_type.csv", index=False)
    thresholds_df.to_csv(output_dir / "duration_threshold_comparison.csv", index=False)
    pair_df.to_csv(output_dir / "station_pairs_normalized.csv", index=False)
    station_lookup.to_csv(output_dir / "station_lookup_canonical.csv", index=False)
    station_conflicts.to_csv(output_dir / "station_id_conflicts.csv", index=False)

    if not args.skip_plots:
        plot_duration_histograms(duration_samples, output_dir, cutoff=max(args.duration_thresholds))
        if not pair_df.empty:
            plot_top_pairs(pair_df, output_dir)

    print("\n[DONE] Sample outputs written to:", output_dir.resolve())
    print(overall_summary.to_string(index=False))


if __name__ == "__main__":
    main()
