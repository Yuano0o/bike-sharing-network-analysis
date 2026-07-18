#!/usr/bin/env python3
"""Partition large bikeshare runs into year/month ranges, then merge results.

This script keeps `bikeshare_cleaning.py` as the single-batch worker and adds a
separate orchestration layer for large cities or long time ranges.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Partition a large bikeshare cleaning job and merge results.")
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="CSV glob patterns or paths, e.g. 'data/san_francisco/extracted_csv/*.csv'.",
    )
    parser.add_argument("--city", required=True, help="City label used in outputs.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Parent directory for partition outputs and merged outputs.",
    )
    parser.add_argument(
        "--cleaning-script",
        default="tools/bikeshare_cleaning.py",
        help="Path to the existing single-run cleaning script.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to call the cleaning script.",
    )
    parser.add_argument(
        "--partition-mode",
        choices=["year", "range"],
        default="year",
        help="Split one partition per year, or use explicit ranges from a config file.",
    )
    parser.add_argument(
        "--range-config",
        help="JSON file describing explicit partitions when --partition-mode range is used.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=200_000,
        help="Chunksize passed through to the cleaning script.",
    )
    parser.add_argument(
        "--max-duration-min",
        type=int,
        default=240,
        help="Final duration cutoff passed through to the cleaning script.",
    )
    parser.add_argument(
        "--skip-cleaned-csv",
        action="store_true",
        help="Pass --skip-cleaned-csv into each partition run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned partition commands without executing them.",
    )
    return parser.parse_args()


def expand_inputs(patterns: List[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        p = Path(pattern)
        if p.exists():
            files.append(p)
            continue
        files.extend(sorted(Path.cwd().glob(pattern)))
    unique = sorted(dict.fromkeys(path for path in files if path.is_file()))
    if not unique:
        raise FileNotFoundError("No input files matched the provided patterns.")
    return unique


def infer_year_month(path: Path) -> Optional[str]:
    name = path.name
    import re

    match = re.search(r"((?:19|20)\d{2})(0[1-9]|1[0-2])", name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.search(r"((?:19|20)\d{2})", name)
    if match:
        return f"{match.group(1)}-00"
    return None


def build_year_partitions(files: List[Path]) -> List[dict]:
    buckets = {}
    for path in files:
        token = infer_year_month(path)
        if token is None:
            key = "unknown"
        else:
            key = token[:4]
        buckets.setdefault(key, []).append(path)
    return [{"name": f"year_{key}", "files": sorted(paths)} for key, paths in sorted(buckets.items())]


def build_range_partitions(files: List[Path], config_path: Path) -> List[dict]:
    config = json.loads(config_path.read_text())
    partitions = []
    for item in config.get("partitions", []):
        name = item["name"]
        start = item["from"]
        end = item["to"]
        selected = []
        for path in files:
            token = infer_year_month(path)
            if token is None:
                continue
            if start <= token <= end:
                selected.append(path)
        partitions.append({"name": name, "files": sorted(selected)})
    return partitions


def run_partition(
    partition: dict,
    args: argparse.Namespace,
    output_dir: Path,
) -> None:
    part_dir = output_dir / "partitions" / partition["name"]
    part_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        args.python,
        args.cleaning_script,
        *[str(path) for path in partition["files"]],
        "--city",
        args.city,
        "--output-dir",
        str(part_dir),
        "--chunksize",
        str(args.chunksize),
        "--max-duration-min",
        str(args.max_duration_min),
    ]
    if args.skip_cleaned_csv:
        cmd.append("--skip-cleaned-csv")

    print("\n[RUN]", " ".join(cmd))
    if args.dry_run:
        return
    subprocess.run(cmd, check=True)


def combine_summaries(partition_dirs: List[Path], merged_dir: Path) -> None:
    frames = []
    for pdir in partition_dirs:
        path = pdir / "summary_overall.csv"
        if path.exists():
            df = pd.read_csv(path)
            df["partition"] = pdir.name
            frames.append(df)
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.to_csv(merged_dir / "combined_partition_summaries.csv", index=False)


def combine_user_type_summaries(partition_dirs: List[Path], merged_dir: Path) -> None:
    frames = []
    for pdir in partition_dirs:
        path = pdir / "summary_by_user_type.csv"
        if path.exists():
            df = pd.read_csv(path)
            df["partition"] = pdir.name
            frames.append(df)
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.to_csv(merged_dir / "combined_user_type_summaries.csv", index=False)


def combine_threshold_summaries(partition_dirs: List[Path], merged_dir: Path) -> None:
    frames = []
    for pdir in partition_dirs:
        path = pdir / "duration_threshold_comparison.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined = (
            combined.groupby(["user_type", "threshold_min"], as_index=False)
            .agg(
                rows_before_threshold=("rows_before_threshold", "sum"),
                rows_removed=("rows_removed", "sum"),
                rows_kept=("rows_kept", "sum"),
            )
        )
        combined["removed_share"] = combined["rows_removed"] / combined["rows_before_threshold"]
        combined.to_csv(merged_dir / "merged_duration_threshold_comparison.csv", index=False)


def combine_station_pairs(partition_dirs: List[Path], merged_dir: Path) -> None:
    frames = []
    for pdir in partition_dirs:
        path = pdir / "station_pairs_normalized.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
    if not frames:
        return

    combined = pd.concat(frames, ignore_index=True)
    combined = (
        combined.groupby(["user_type", "start_station_id", "end_station_id"], as_index=False)
        .agg(trip_count=("trip_count", "sum"))
    )
    combined["total_from_start"] = combined.groupby(["user_type", "start_station_id"])["trip_count"].transform("sum")
    combined["normalized_count"] = combined["trip_count"] / combined["total_from_start"]
    combined.to_csv(merged_dir / "merged_station_pairs_normalized.csv", index=False)


def combine_station_lookup(partition_dirs: List[Path], merged_dir: Path) -> None:
    frames = []
    for pdir in partition_dirs:
        path = pdir / "station_lookup_canonical.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
    if not frames:
        return

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["station_id", "observation_count"] if "observation_count" in combined.columns else ["station_id"])
    combined = combined.drop_duplicates(subset=["station_id"], keep="last")
    combined.to_csv(merged_dir / "merged_station_lookup_canonical.csv", index=False)


def write_partition_manifest(partitions: List[dict], merged_dir: Path) -> None:
    payload = {
        "partitions": [
            {"name": part["name"], "file_count": len(part["files"]), "files": [str(p) for p in part["files"]]}
            for part in partitions
        ]
    }
    (merged_dir / "partition_manifest.json").write_text(json.dumps(payload, indent=2))


def main() -> None:
    args = parse_args()
    files = expand_inputs(args.inputs)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.partition_mode == "year":
        partitions = build_year_partitions(files)
    else:
        if not args.range_config:
            raise SystemExit("--range-config is required when --partition-mode range is used.")
        partitions = build_range_partitions(files, Path(args.range_config))

    partitions = [part for part in partitions if part["files"]]
    if not partitions:
        raise SystemExit("No partitions contain files after filtering.")

    for part in partitions:
        run_partition(part, args, output_dir)

    merged_dir = output_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    partition_dirs = [output_dir / "partitions" / part["name"] for part in partitions]

    write_partition_manifest(partitions, merged_dir)
    if not args.dry_run:
        combine_summaries(partition_dirs, merged_dir)
        combine_user_type_summaries(partition_dirs, merged_dir)
        combine_threshold_summaries(partition_dirs, merged_dir)
        combine_station_pairs(partition_dirs, merged_dir)
        combine_station_lookup(partition_dirs, merged_dir)
        print(f"[DONE] Partitioned outputs written to {output_dir.resolve()}")


if __name__ == "__main__":
    main()
