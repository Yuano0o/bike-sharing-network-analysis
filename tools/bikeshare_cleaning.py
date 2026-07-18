#!/usr/bin/env python3
"""Batch cleaning pipeline for large bikeshare trip datasets.

This script turns different city CSV formats into a shared schema, removes
pandemic-era trips, applies the cleaning rules discussed in the notebooks,
splits outputs by user type, and exports normalized origin-destination counts
plus a few lightweight plots.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import pandas as pd

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    plt = None


PANDEMIC_START = pd.Timestamp("2020-03-01")
PANDEMIC_END = pd.Timestamp("2021-12-31 23:59:59")
DEFAULT_THRESHOLDS = (90, 120, 180, 240)
DEFAULT_USER_TYPES = ("Customer", "Subscriber")


@dataclass
class DatasetStats:
    raw_rows: int = 0
    removed_pandemic: int = 0
    removed_missing_core: int = 0
    removed_short_self_loop: int = 0
    removed_over_duration: int = 0
    kept_rows: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean large bikeshare CSV datasets.")
    parser.add_argument(
        "inputs",
        nargs="+",
        help="CSV/ZIP files or glob patterns, e.g. 'baywheels/*.csv' 'citibike/*.zip'",
    )
    parser.add_argument("--city", default="unknown_city", help="City label written to outputs.")
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory for cleaned data, summaries, and plots.",
    )
    parser.add_argument(
        "--max-duration-min",
        type=int,
        default=240,
        help="Final trip duration cutoff in minutes. Default: 240.",
    )
    parser.add_argument(
        "--duration-thresholds",
        type=int,
        nargs="+",
        default=list(DEFAULT_THRESHOLDS),
        help="Threshold summary table to compare 90/120/180/240 min style cutoffs.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=200_000,
        help="Rows per chunk while streaming large files.",
    )
    parser.add_argument(
        "--skip-cleaned-csv",
        action="store_true",
        help="Skip writing cleaned row-level CSVs and only export summaries.",
    )
    return parser.parse_args()


def expand_inputs(patterns: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.exists():
            files.append(path)
            continue
        matches = sorted(Path.cwd().glob(pattern))
        files.extend(p for p in matches if p.is_file())
    unique_files = sorted(dict.fromkeys(files))
    if not unique_files:
        raise FileNotFoundError("No input files matched the provided paths/patterns.")
    return unique_files


def detect_schema(columns: List[str]) -> str:
    cols = set(columns)
    lowered = {c.lower() for c in columns}
    if {"id", "name", "latitude", "longitude", "dpcapacity"}.issubset(lowered) and (
        "landmark" in lowered or "online_date" in lowered
    ):
        return "station_metadata"
    if {"duration_sec", "start_time", "end_time"}.issubset(cols):
        return "baywheels_legacy"
    if {"routeid", "paymentplan", "starthub", "startdate", "starttime", "enddate", "endtime", "duration"}.issubset(
        lowered
    ):
        return "portland_hub_export"
    if {"duration", "start date", "end date"}.issubset(lowered):
        return "capitalbikeshare_legacy"
    if {
        "01 - rental details local start time",
        "01 - rental details local end time",
        "01 - rental details duration in seconds uncapped",
        "03 - rental start station id",
        "02 - rental end station id",
    }.issubset(lowered):
        return "divvy_prefixed_legacy"
    if {"tripduration", "start_time", "end_time", "from_station_id", "to_station_id"}.issubset(lowered):
        return "divvy_legacy"
    if {"tripduration", "starttime", "stoptime"}.issubset(lowered):
        return "classic_bikeshare"
    if {"started_at", "ended_at"}.issubset(cols):
        return "gbfs_modern"
    raise ValueError(f"Unsupported schema columns: {columns[:12]}")


def normalize_user_type(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip().str.lower()
    mapped = values.replace(
        {
            "subscriber": "Subscriber",
            "member": "Subscriber",
            "registered": "Subscriber",
            "customer": "Customer",
            "casual": "Customer",
        }
    )
    mapped = mapped.fillna("Unknown")
    return mapped


def to_string_id(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    text = text.where(~text.isin(["", "<NA>", "nan", "None"]), pd.NA)
    # Some systems export numeric station IDs as floats (for example "30200.0").
    # Collapse those back to a single canonical string so one station_id stays unique.
    text = text.str.replace(r"^(-?\d+)\.0+$", r"\1", regex=True)
    return text


def maybe_round(series: pd.Series, decimals: int = 6) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.round(decimals)


def combine_datetime(date_series: pd.Series, time_series: pd.Series) -> pd.Series:
    date_text = date_series.astype("string").str.strip()
    time_text = time_series.astype("string").str.strip()
    combined = (date_text + " " + time_text).where(date_text.notna() & time_text.notna(), pd.NA)
    return pd.to_datetime(combined, errors="coerce")


def first_present_column(rename_map: Dict[str, str], *candidates: str) -> Optional[str]:
    for candidate in candidates:
        if candidate in rename_map:
            return rename_map[candidate]
    return None


def standardize_chunk(chunk: pd.DataFrame, schema: str) -> pd.DataFrame:
    if schema == "baywheels_legacy":
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(chunk["start_time"], errors="coerce"),
                "ended_at": pd.to_datetime(chunk["end_time"], errors="coerce"),
                "duration_min": pd.to_numeric(chunk["duration_sec"], errors="coerce") / 60.0,
                "start_station_id": to_string_id(chunk["start_station_id"]),
                "start_station_name": chunk["start_station_name"].astype("string"),
                "start_lat": maybe_round(chunk["start_station_latitude"]),
                "start_lng": maybe_round(chunk["start_station_longitude"]),
                "end_station_id": to_string_id(chunk["end_station_id"]),
                "end_station_name": chunk["end_station_name"].astype("string"),
                "end_lat": maybe_round(chunk["end_station_latitude"]),
                "end_lng": maybe_round(chunk["end_station_longitude"]),
                "user_type": normalize_user_type(chunk["user_type"]),
            }
        )
    elif schema == "capitalbikeshare_legacy":
        rename_map = {col.lower(): col for col in chunk.columns}
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(chunk[rename_map["start date"]], errors="coerce"),
                "ended_at": pd.to_datetime(chunk[rename_map["end date"]], errors="coerce"),
                "duration_min": pd.to_numeric(chunk[rename_map["duration"]], errors="coerce") / 60.0,
                "start_station_id": to_string_id(chunk[rename_map.get("start station number")]),
                "start_station_name": chunk[rename_map.get("start station")].astype("string"),
                "start_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "start_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_station_id": to_string_id(chunk[rename_map.get("end station number")]),
                "end_station_name": chunk[rename_map.get("end station")].astype("string"),
                "end_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "user_type": normalize_user_type(chunk[rename_map.get("member type")]),
            }
        )
    elif schema == "portland_hub_export":
        rename_map = {col.lower(): col for col in chunk.columns}
        start_lat_col = first_present_column(rename_map, "startlatitude", "start_latitude")
        start_lng_col = first_present_column(rename_map, "startlongitude", "start_longitude")
        end_lat_col = first_present_column(rename_map, "endlatitude", "end_latitude")
        end_lng_col = first_present_column(rename_map, "endlongitude", "end_longitude")
        df = pd.DataFrame(
            {
                "started_at": combine_datetime(chunk[rename_map["startdate"]], chunk[rename_map["starttime"]]),
                "ended_at": combine_datetime(chunk[rename_map["enddate"]], chunk[rename_map["endtime"]]),
                "duration_min": pd.to_timedelta(chunk[rename_map["duration"]], errors="coerce").dt.total_seconds()
                / 60.0,
                "start_station_id": to_string_id(chunk[rename_map["starthub"]]),
                "start_station_name": chunk[rename_map["starthub"]].astype("string"),
                "start_lat": maybe_round(chunk[start_lat_col]) if start_lat_col else pd.Series([math.nan] * len(chunk)),
                "start_lng": maybe_round(chunk[start_lng_col]) if start_lng_col else pd.Series([math.nan] * len(chunk)),
                "end_station_id": to_string_id(chunk[rename_map["endhub"]]),
                "end_station_name": chunk[rename_map["endhub"]].astype("string"),
                "end_lat": maybe_round(chunk[end_lat_col]) if end_lat_col else pd.Series([math.nan] * len(chunk)),
                "end_lng": maybe_round(chunk[end_lng_col]) if end_lng_col else pd.Series([math.nan] * len(chunk)),
                "user_type": normalize_user_type(chunk[rename_map["paymentplan"]]),
            }
        )
    elif schema == "classic_bikeshare":
        rename_map = {col.lower(): col for col in chunk.columns}
        start_id_col = first_present_column(rename_map, "start station id", "from_station_id")
        start_name_col = first_present_column(rename_map, "start station name", "from_station_name")
        start_lat_col = first_present_column(rename_map, "start station latitude")
        start_lng_col = first_present_column(rename_map, "start station longitude")
        end_id_col = first_present_column(rename_map, "end station id", "to_station_id")
        end_name_col = first_present_column(rename_map, "end station name", "to_station_name")
        end_lat_col = first_present_column(rename_map, "end station latitude")
        end_lng_col = first_present_column(rename_map, "end station longitude")
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(chunk[rename_map["starttime"]], errors="coerce"),
                "ended_at": pd.to_datetime(chunk[rename_map["stoptime"]], errors="coerce"),
                "duration_min": pd.to_numeric(chunk[rename_map["tripduration"]], errors="coerce") / 60.0,
                "start_station_id": to_string_id(chunk[start_id_col]),
                "start_station_name": chunk[start_name_col].astype("string"),
                "start_lat": maybe_round(chunk[start_lat_col]) if start_lat_col else pd.Series([math.nan] * len(chunk)),
                "start_lng": maybe_round(chunk[start_lng_col]) if start_lng_col else pd.Series([math.nan] * len(chunk)),
                "end_station_id": to_string_id(chunk[end_id_col]),
                "end_station_name": chunk[end_name_col].astype("string"),
                "end_lat": maybe_round(chunk[end_lat_col]) if end_lat_col else pd.Series([math.nan] * len(chunk)),
                "end_lng": maybe_round(chunk[end_lng_col]) if end_lng_col else pd.Series([math.nan] * len(chunk)),
                "user_type": normalize_user_type(chunk[rename_map.get("usertype")]),
            }
        )
    elif schema == "divvy_legacy":
        rename_map = {col.lower(): col for col in chunk.columns}
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(chunk[rename_map["start_time"]], errors="coerce"),
                "ended_at": pd.to_datetime(chunk[rename_map["end_time"]], errors="coerce"),
                "duration_min": pd.to_numeric(chunk[rename_map["tripduration"]], errors="coerce") / 60.0,
                "start_station_id": to_string_id(chunk[rename_map.get("from_station_id")]),
                "start_station_name": chunk[rename_map.get("from_station_name")].astype("string"),
                "start_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "start_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_station_id": to_string_id(chunk[rename_map.get("to_station_id")]),
                "end_station_name": chunk[rename_map.get("to_station_name")].astype("string"),
                "end_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "user_type": normalize_user_type(chunk[rename_map.get("usertype")]),
            }
        )
    elif schema == "divvy_prefixed_legacy":
        rename_map = {col.lower(): col for col in chunk.columns}
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(
                    chunk[rename_map["01 - rental details local start time"]], errors="coerce"
                ),
                "ended_at": pd.to_datetime(
                    chunk[rename_map["01 - rental details local end time"]], errors="coerce"
                ),
                "duration_min": pd.to_numeric(
                    chunk[rename_map["01 - rental details duration in seconds uncapped"]], errors="coerce"
                )
                / 60.0,
                "start_station_id": to_string_id(chunk[rename_map["03 - rental start station id"]]),
                "start_station_name": chunk[rename_map["03 - rental start station name"]].astype("string"),
                "start_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "start_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_station_id": to_string_id(chunk[rename_map["02 - rental end station id"]]),
                "end_station_name": chunk[rename_map["02 - rental end station name"]].astype("string"),
                "end_lat": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "end_lng": pd.Series([math.nan] * len(chunk), dtype="float64"),
                "user_type": normalize_user_type(chunk[rename_map["user type"]]),
            }
        )
    elif schema == "gbfs_modern":
        df = pd.DataFrame(
            {
                "started_at": pd.to_datetime(chunk["started_at"], errors="coerce"),
                "ended_at": pd.to_datetime(chunk["ended_at"], errors="coerce"),
                "duration_min": (
                    pd.to_datetime(chunk["ended_at"], errors="coerce")
                    - pd.to_datetime(chunk["started_at"], errors="coerce")
                ).dt.total_seconds()
                / 60.0,
                "start_station_id": to_string_id(chunk.get("start_station_id")),
                "start_station_name": chunk.get("start_station_name", pd.Series(dtype="string")).astype("string"),
                "start_lat": maybe_round(chunk.get("start_lat")),
                "start_lng": maybe_round(chunk.get("start_lng")),
                "end_station_id": to_string_id(chunk.get("end_station_id")),
                "end_station_name": chunk.get("end_station_name", pd.Series(dtype="string")).astype("string"),
                "end_lat": maybe_round(chunk.get("end_lat")),
                "end_lng": maybe_round(chunk.get("end_lng")),
                "user_type": normalize_user_type(chunk.get("member_casual")),
            }
        )
    else:
        raise ValueError(f"Unknown schema: {schema}")

    df["trip_date"] = df["started_at"].dt.date.astype("string")
    return df


def init_station_registry() -> defaultdict[str, Dict[str, Counter]]:
    return defaultdict(lambda: {"name": Counter(), "coord": Counter()})


def update_station_registry(registry: defaultdict, cleaned: pd.DataFrame) -> None:
    station_views = [
        cleaned[["start_station_id", "start_station_name", "start_lat", "start_lng"]].rename(
            columns={
                "start_station_id": "station_id",
                "start_station_name": "name",
                "start_lat": "lat",
                "start_lng": "lng",
            }
        ),
        cleaned[["end_station_id", "end_station_name", "end_lat", "end_lng"]].rename(
            columns={
                "end_station_id": "station_id",
                "end_station_name": "name",
                "end_lat": "lat",
                "end_lng": "lng",
            }
        ),
    ]
    all_stations = pd.concat(station_views, ignore_index=True).dropna(subset=["station_id"])
    for row in all_stations.itertuples(index=False):
        station_id = row.station_id
        if pd.notna(row.name):
            registry[station_id]["name"][str(row.name)] += 1
        if pd.notna(row.lat) and pd.notna(row.lng):
            registry[station_id]["coord"][(float(row.lat), float(row.lng))] += 1


def write_chunk_csv(path: Path, frame: pd.DataFrame, append: bool) -> None:
    mode = "a" if append else "w"
    frame.to_csv(path, index=False, mode=mode, header=not append, quoting=csv.QUOTE_MINIMAL)


def build_station_outputs(registry: defaultdict) -> Tuple[pd.DataFrame, pd.DataFrame]:
    canonical_rows = []
    conflict_rows = []
    for station_id, info in registry.items():
        top_name, top_name_count = (None, 0)
        top_coord, top_coord_count = (None, 0)
        if info["name"]:
            top_name, top_name_count = info["name"].most_common(1)[0]
        if info["coord"]:
            top_coord, top_coord_count = info["coord"].most_common(1)[0]

        lat = top_coord[0] if top_coord else math.nan
        lng = top_coord[1] if top_coord else math.nan
        canonical_rows.append(
            {
                "station_id": station_id,
                "canonical_name": top_name,
                "canonical_lat": lat,
                "canonical_lng": lng,
                "n_name_versions": len(info["name"]),
                "n_coord_versions": len(info["coord"]),
                "top_name_count": top_name_count,
                "top_coord_count": top_coord_count,
            }
        )
        if len(info["name"]) > 1 or len(info["coord"]) > 1:
            conflict_rows.append(
                {
                    "station_id": station_id,
                    "name_versions": " | ".join(f"{name} ({count})" for name, count in info["name"].most_common()),
                    "coord_versions": " | ".join(
                        f"{coord[0]:.6f},{coord[1]:.6f} ({count})"
                        for coord, count in info["coord"].most_common()
                    ),
                }
            )
    station_lookup = pd.DataFrame(canonical_rows)
    if not station_lookup.empty:
        station_lookup = station_lookup.sort_values("station_id")
    else:
        station_lookup = pd.DataFrame(
            columns=[
                "station_id",
                "canonical_name",
                "canonical_lat",
                "canonical_lng",
                "n_name_versions",
                "n_coord_versions",
                "top_name_count",
                "top_coord_count",
            ]
        )

    station_conflicts = pd.DataFrame(conflict_rows)
    if not station_conflicts.empty:
        station_conflicts = station_conflicts.sort_values("station_id")
    else:
        station_conflicts = pd.DataFrame(columns=["station_id", "name_versions", "coord_versions"])
    return station_lookup, station_conflicts


def apply_canonical_station_names(
    cleaned_paths: Iterable[Path], station_lookup: pd.DataFrame, chunksize: int
) -> None:
    if station_lookup.empty:
        return

    canonical_map = (
        station_lookup.dropna(subset=["station_id", "canonical_name"])
        .drop_duplicates(subset=["station_id"], keep="last")
        .set_index("station_id")["canonical_name"]
        .to_dict()
    )
    if not canonical_map:
        return

    for path in cleaned_paths:
        if not path.exists():
            continue
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        first_chunk = True
        for chunk in pd.read_csv(
            path,
            chunksize=chunksize,
            low_memory=False,
            dtype={
                "start_station_id": "string",
                "end_station_id": "string",
                "start_station_name": "string",
                "end_station_name": "string",
            },
        ):
            chunk["start_station_id"] = to_string_id(chunk["start_station_id"])
            chunk["end_station_id"] = to_string_id(chunk["end_station_id"])
            chunk["start_station_name"] = chunk["start_station_id"].map(canonical_map).fillna(
                chunk["start_station_name"]
            )
            chunk["end_station_name"] = chunk["end_station_id"].map(canonical_map).fillna(
                chunk["end_station_name"]
            )
            write_chunk_csv(tmp_path, chunk, append=not first_chunk)
            first_chunk = False
        tmp_path.replace(path)


def plot_duration_histograms(duration_lists: Dict[str, List[float]], output_dir: Path, cutoff: int) -> None:
    if plt is None:
        print("[WARN] matplotlib is not installed; skipping duration plots.")
        return
    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    bins = list(range(0, cutoff + 5, 5))
    for user_type, values in duration_lists.items():
        if not values:
            continue
        plt.figure(figsize=(8, 5))
        plt.hist(values, bins=bins, edgecolor="black", alpha=0.75, color="#4C78A8")
        plt.axvline(pd.Series(values).median(), color="#E45756", linestyle="--", linewidth=2)
        plt.title(f"{user_type} trip duration distribution")
        plt.xlabel("Duration (minutes)")
        plt.ylabel("Trip count")
        plt.tight_layout()
        plt.savefig(plot_dir / f"duration_hist_{user_type.lower()}.png", dpi=160)
        plt.close()


def plot_top_pairs(pair_df: pd.DataFrame, output_dir: Path) -> None:
    if plt is None:
        print("[WARN] matplotlib is not installed; skipping pair plots.")
        return
    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    for user_type, group in pair_df.groupby("user_type"):
        top = group.sort_values("normalized_count", ascending=False).head(15).copy()
        if top.empty:
            continue
        top["pair_label"] = top["start_station_id"].astype("string") + " -> " + top["end_station_id"].astype(
            "string"
        )
        plt.figure(figsize=(10, 6))
        plt.barh(top["pair_label"][::-1], top["normalized_count"][::-1], color="#72B7B2")
        plt.title(f"Top normalized origin-destination pairs: {user_type}")
        plt.xlabel("Normalized count")
        plt.tight_layout()
        plt.savefig(output_dir / "plots" / f"top_pairs_{user_type.lower()}.png", dpi=160)
        plt.close()


def summarize_thresholds(base_df: pd.DataFrame, thresholds: List[int], user_type: str) -> List[dict]:
    rows = []
    for threshold in sorted(set(thresholds)):
        removed = int((base_df["duration_min"] > threshold).sum())
        rows.append(
            {
                "user_type": user_type,
                "threshold_min": threshold,
                "rows_before_threshold": len(base_df),
                "rows_removed": removed,
                "rows_kept": len(base_df) - removed,
                "removed_share": removed / len(base_df) if len(base_df) else 0.0,
            }
        )
    return rows


def iter_csv_chunks(path: Path, chunksize: int) -> Iterator[pd.DataFrame]:
    yield from pd.read_csv(path, chunksize=chunksize, low_memory=False)


def main() -> None:
    args = parse_args()
    input_files = expand_inputs(args.inputs)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = DatasetStats()
    user_type_counts = Counter()
    duration_threshold_rows: List[dict] = []
    pair_counts = Counter()
    start_totals = Counter()
    duration_samples: Dict[str, List[float]] = defaultdict(list)
    station_registry = init_station_registry()
    wrote_cleaned = set()

    for path in input_files:
        sample = pd.read_csv(path, nrows=5)
        schema = detect_schema(list(sample.columns))
        if schema == "station_metadata":
            print(f"[INFO] Skipping non-trip station file {path.name}")
            continue
        print(f"[INFO] Processing {path.name} with schema={schema}")

        for chunk in iter_csv_chunks(path, args.chunksize):
            stats.raw_rows += len(chunk)
            std = standardize_chunk(chunk, schema)

            core_mask = (
                std["started_at"].notna()
                & std["ended_at"].notna()
                & std["duration_min"].notna()
                & std["start_station_id"].notna()
                & std["end_station_id"].notna()
                & std["user_type"].isin(DEFAULT_USER_TYPES)
            )
            stats.removed_missing_core += int((~core_mask).sum())
            std = std.loc[core_mask].copy()
            if std.empty:
                continue

            pandemic_mask = std["started_at"].between(PANDEMIC_START, PANDEMIC_END)
            stats.removed_pandemic += int(pandemic_mask.sum())
            std = std.loc[~pandemic_mask].copy()
            if std.empty:
                continue

            short_self_loop_mask = (
                (std["start_station_id"] == std["end_station_id"]) & (std["duration_min"] < 2)
            )
            stats.removed_short_self_loop += int(short_self_loop_mask.sum())
            std = std.loc[~short_self_loop_mask].copy()
            if std.empty:
                continue

            for user_type, group in std.groupby("user_type"):
                duration_threshold_rows.extend(summarize_thresholds(group, args.duration_thresholds, user_type))

            over_duration_mask = std["duration_min"] > args.max_duration_min
            stats.removed_over_duration += int(over_duration_mask.sum())
            cleaned = std.loc[~over_duration_mask].copy()
            if cleaned.empty:
                continue

            cleaned["city"] = args.city
            cleaned["source_file"] = path.name
            cleaned["is_round_trip"] = cleaned["start_station_id"] == cleaned["end_station_id"]
            cleaned["year_month"] = cleaned["started_at"].dt.to_period("M").astype(str)
            stats.kept_rows += len(cleaned)

            update_station_registry(station_registry, cleaned)

            for user_type, group in cleaned.groupby("user_type"):
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

                if not args.skip_cleaned_csv:
                    out_path = output_dir / f"cleaned_{user_type.lower()}.csv"
                    append = out_path in wrote_cleaned
                    write_chunk_csv(out_path, group, append=append)
                    wrote_cleaned.add(out_path)

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
                "normalized_count": trip_count / total_from_start if total_from_start else math.nan,
            }
        )
    pair_df = pd.DataFrame(pair_rows).sort_values(
        ["user_type", "normalized_count", "trip_count"], ascending=[True, False, False]
    )

    station_lookup, station_conflicts = build_station_outputs(station_registry)

    overall_summary = pd.DataFrame(
        [
            {
                "city": args.city,
                "input_files": len(input_files),
                "raw_rows": stats.raw_rows,
                "removed_missing_core": stats.removed_missing_core,
                "removed_pandemic": stats.removed_pandemic,
                "removed_short_self_loop_lt_2min": stats.removed_short_self_loop,
                "removed_over_duration": stats.removed_over_duration,
                "kept_rows": stats.kept_rows,
                "final_max_duration_min": args.max_duration_min,
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

    if not args.skip_cleaned_csv:
        apply_canonical_station_names(
            [output_dir / "cleaned_customer.csv", output_dir / "cleaned_subscriber.csv"],
            station_lookup,
            chunksize=args.chunksize,
        )

    plot_duration_histograms(duration_samples, output_dir, args.max_duration_min)
    if not pair_df.empty:
        plot_top_pairs(pair_df, output_dir)

    print("\n[DONE] Outputs written to:", output_dir.resolve())
    print(overall_summary.to_string(index=False))


if __name__ == "__main__":
    main()
