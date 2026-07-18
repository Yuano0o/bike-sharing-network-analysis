#!/usr/bin/env python3
"""Run bikeshare_cleaning.py for multiple cities from one config file."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch runner for multiple bikeshare cities.")
    parser.add_argument(
        "--config",
        default="bikeshare_batch_config.json",
        help="Path to batch config JSON.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to launch bikeshare_cleaning.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    config = json.loads(config_path.read_text())

    root = Path(config.get("data_root", ".")).resolve()
    script_path = Path(config.get("cleaning_script", "bikeshare_cleaning.py")).resolve()
    output_root = Path(config.get("output_root", "batch_outputs")).resolve()
    default_chunksize = int(config.get("chunksize", 200_000))
    default_max_duration = int(config.get("max_duration_min", 240))

    cities = config.get("cities", [])
    if not cities:
        raise ValueError("No cities found in config.")

    for city in cities:
        city_name = city["city"]
        patterns = city["inputs"]
        city_output = output_root / city_name
        cmd = [
            args.python,
            str(script_path),
            *[str(root / pattern) for pattern in patterns],
            "--city",
            city_name,
            "--output-dir",
            str(city_output),
            "--chunksize",
            str(city.get("chunksize", default_chunksize)),
            "--max-duration-min",
            str(city.get("max_duration_min", default_max_duration)),
        ]

        if city.get("skip_cleaned_csv", False):
            cmd.append("--skip-cleaned-csv")

        print("\n[RUN]", " ".join(cmd))
        if args.dry_run:
            continue

        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
