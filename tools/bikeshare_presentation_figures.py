#!/usr/bin/env python3
"""Generate presentation-ready figures from cleaned bikeshare outputs."""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

import pandas as pd

os.environ.setdefault("MPLCONFIGDIR", str(Path(".matplotlib-cache").resolve()))
if not os.environ.get("MPLBACKEND"):
    os.environ["MPLBACKEND"] = "Agg"
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.patches import FancyArrowPatch


USER_COLORS = {
    "Customer": "#d95f02",
    "Subscriber": "#1b9e77",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create figures for the bikeshare M2 presentation."
    )
    parser.add_argument(
        "--input-dir",
        default="data/san_francisco/sample_outputs_latest2y",
        help="Directory containing cleaned_customer.csv.gz, cleaned_subscriber.csv.gz, and station_pairs_normalized.csv.gz.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for saved figures. Default: <input-dir>/analysis.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=500_000,
        help="Rows per chunk for cleaned trip files. Default: 500000.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=30,
        help="Number of stations for the OD heatmap. Default: 30.",
    )
    parser.add_argument(
        "--top-origin-n",
        type=int,
        default=15,
        help="Number of stations for top-origin bar charts. Default: 15.",
    )
    parser.add_argument(
        "--network-top-edges",
        type=int,
        default=80,
        help="Number of strongest Subscriber OD edges for the presentation network. Default: 80.",
    )
    return parser.parse_args()


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")


def save_figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=300, bbox_inches="tight")
    plt.close()
    print(path)


def collect_hourly_counts(cleaned_path: Path, chunksize: int) -> pd.Series:
    require_file(cleaned_path)
    counts = pd.Series(0, index=range(24), dtype="int64")
    for chunk in pd.read_csv(
        cleaned_path,
        usecols=["started_at"],
        chunksize=chunksize,
        low_memory=False,
    ):
        started_at = pd.to_datetime(chunk["started_at"], errors="coerce")
        hourly = started_at.dt.hour.value_counts().sort_index()
        counts = counts.add(hourly, fill_value=0).astype("int64")
    return counts.reindex(range(24), fill_value=0)


def plot_hourly_demand(input_dir: Path, output_dir: Path, chunksize: int) -> Path:
    customer_path = input_dir / "cleaned_customer.csv.gz"
    subscriber_path = input_dir / "cleaned_subscriber.csv.gz"
    customer_counts = collect_hourly_counts(customer_path, chunksize)
    subscriber_counts = collect_hourly_counts(subscriber_path, chunksize)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(
        customer_counts.index,
        customer_counts.values,
        marker="o",
        linewidth=2.5,
        color=USER_COLORS["Customer"],
        label="Customer",
    )
    ax.plot(
        subscriber_counts.index,
        subscriber_counts.values,
        marker="o",
        linewidth=2.5,
        color=USER_COLORS["Subscriber"],
        label="Subscriber",
    )
    ax.set_title("San Francisco hourly trip demand by user type", fontsize=14, weight="bold")
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Trip count")
    ax.set_xticks(range(0, 24, 2))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    out = output_dir / "hourly_trip_demand_customer_vs_subscriber.png"
    save_figure(out)
    return out


def plot_inter_event_distribution(input_dir: Path, output_dir: Path) -> Path:
    subscriber_path = input_dir / "cleaned_subscriber.csv.gz"
    require_file(subscriber_path)
    started_at = pd.read_csv(
        subscriber_path,
        usecols=["started_at"],
        low_memory=False,
    )["started_at"]
    times = pd.to_datetime(started_at, errors="coerce").dropna().sort_values()
    inter_event_min = times.diff().dt.total_seconds().div(60).dropna()
    inter_event_min = inter_event_min[inter_event_min >= 0]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.hist(
        inter_event_min.clip(upper=60),
        bins=60,
        color=USER_COLORS["Subscriber"],
        alpha=0.86,
        edgecolor="white",
        linewidth=0.4,
    )
    ax.set_title("Subscriber inter-event time distribution", fontsize=14, weight="bold")
    ax.set_xlabel("Minutes between consecutive subscriber trips, clipped at 60")
    ax.set_ylabel("Frequency")
    ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    out = output_dir / "inter_event_time_distribution_subscriber.png"
    save_figure(out)
    return out


def plot_od_heatmap(input_dir: Path, output_dir: Path, top_n: int) -> Path:
    pairs_path = input_dir / "station_pairs_normalized.csv.gz"
    require_file(pairs_path)
    pairs = pd.read_csv(pairs_path, low_memory=False)
    pairs = pairs[pairs["user_type"].astype(str).str.lower() == "subscriber"].copy()
    if pairs.empty:
        raise ValueError(f"No Subscriber rows found in {pairs_path}")

    for col in ["start_station_id", "end_station_id"]:
        pairs[col] = pairs[col].astype(str)
    pairs["trip_count"] = pd.to_numeric(pairs["trip_count"], errors="coerce").fillna(0)

    origin_volume = pairs.groupby("start_station_id")["trip_count"].sum()
    destination_volume = pairs.groupby("end_station_id")["trip_count"].sum()
    station_volume = origin_volume.add(destination_volume, fill_value=0).sort_values(ascending=False)
    top_stations = station_volume.head(top_n).index.tolist()

    filtered = pairs[
        pairs["start_station_id"].isin(top_stations)
        & pairs["end_station_id"].isin(top_stations)
    ]
    matrix = (
        filtered.pivot_table(
            index="start_station_id",
            columns="end_station_id",
            values="trip_count",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(index=top_stations, columns=top_stations, fill_value=0)
    )

    fig, ax = plt.subplots(figsize=(11.5, 9.5))
    image = ax.imshow(matrix.values, cmap="YlOrRd", aspect="auto")
    ax.set_title(f"Subscriber OD flow heatmap: top {len(top_stations)} stations", fontsize=14, weight="bold")
    ax.set_xlabel("Destination station ID")
    ax.set_ylabel("Origin station ID")
    ax.set_xticks(range(len(top_stations)))
    ax.set_yticks(range(len(top_stations)))
    ax.set_xticklabels(top_stations, rotation=90, fontsize=6)
    ax.set_yticklabels(top_stations, fontsize=6)
    cbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Trip count")

    out = output_dir / "od_flow_heatmap_top30_subscriber.png"
    save_figure(out)
    return out


def plot_filtered_station_network(input_dir: Path, output_dir: Path, top_edges: int) -> Path:
    pairs_path = input_dir / "station_pairs_normalized.csv.gz"
    stations_path = input_dir / "station_lookup_canonical.csv.gz"
    require_file(pairs_path)
    require_file(stations_path)

    pairs = pd.read_csv(pairs_path, low_memory=False)
    stations = pd.read_csv(stations_path, low_memory=False)
    pairs = pairs[pairs["user_type"].astype(str).str.lower() == "subscriber"].copy()
    pairs["trip_count"] = pd.to_numeric(pairs["trip_count"], errors="coerce").fillna(0)

    stations["station_id"] = stations["station_id"].astype(str)
    station_lookup = stations.drop_duplicates("station_id").set_index("station_id")
    for col in ["start_station_id", "end_station_id"]:
        pairs[col] = pairs[col].astype(str)

    # Keep the main San Francisco station network for a readable presentation map.
    pairs = pairs[
        pairs["start_station_id"].str.startswith("SF-")
        & pairs["end_station_id"].str.startswith("SF-")
    ].copy()
    pairs = pairs.sort_values("trip_count", ascending=False).head(top_edges).copy()

    involved = sorted(set(pairs["start_station_id"]) | set(pairs["end_station_id"]))
    coords = station_lookup.reindex(involved)[["canonical_lng", "canonical_lat"]].dropna()
    if coords.empty:
        raise ValueError("No station coordinates available for filtered network plot.")

    pairs = pairs[
        pairs["start_station_id"].isin(coords.index)
        & pairs["end_station_id"].isin(coords.index)
    ].copy()
    if pairs.empty:
        raise ValueError("No OD pairs with usable station coordinates for filtered network plot.")

    node_flow = Counter()
    for row in pairs.itertuples(index=False):
        node_flow[row.start_station_id] += int(row.trip_count)
        node_flow[row.end_station_id] += int(row.trip_count)

    top_label_nodes = {station_id for station_id, _ in node_flow.most_common(8)}
    max_flow = max(node_flow.values()) if node_flow else 1
    max_edge = pairs["trip_count"].max()

    fig, ax = plt.subplots(figsize=(12.8, 9.2))
    ax.set_facecolor("#fbfaf6")

    for row in pairs.sort_values("trip_count").itertuples(index=False):
        sx, sy = coords.loc[row.start_station_id, ["canonical_lng", "canonical_lat"]]
        ex, ey = coords.loc[row.end_station_id, ["canonical_lng", "canonical_lat"]]
        width = 0.55 + 4.2 * (row.trip_count / max_edge)
        alpha = 0.16 + 0.42 * (row.trip_count / max_edge)
        arrow = FancyArrowPatch(
            (sx, sy),
            (ex, ey),
            arrowstyle="-|>",
            mutation_scale=7.5 + 8.5 * (row.trip_count / max_edge),
            linewidth=width,
            color="#546a76",
            alpha=alpha,
            shrinkA=6,
            shrinkB=6,
            connectionstyle="arc3,rad=0.08",
        )
        ax.add_patch(arrow)

    xs = coords.loc[list(node_flow.keys()), "canonical_lng"]
    ys = coords.loc[list(node_flow.keys()), "canonical_lat"]
    sizes = [90 + 720 * (node_flow[node] / max_flow) for node in node_flow.keys()]
    ax.scatter(
        xs,
        ys,
        s=sizes,
        c="#1b9e77",
        edgecolors="white",
        linewidths=1.1,
        alpha=0.92,
        zorder=3,
    )

    label_offsets = [
        (0.004, 0.004),
        (0.004, -0.004),
        (-0.004, 0.004),
        (-0.004, -0.004),
        (0.006, 0.0),
        (-0.006, 0.0),
        (0.0, 0.006),
        (0.0, -0.006),
    ]
    for idx, node in enumerate(top_label_nodes):
        x = coords.loc[node, "canonical_lng"]
        y = coords.loc[node, "canonical_lat"]
        dx, dy = label_offsets[idx % len(label_offsets)]
        ax.text(
            x + dx,
            y + dy,
            node,
            fontsize=8.5,
            ha="center",
            va="center",
            color="#102a33",
            weight="bold",
            zorder=4,
            bbox=dict(boxstyle="round,pad=0.14", facecolor="white", edgecolor="none", alpha=0.75),
        )

    ax.set_title(
        f"San Francisco Subscriber station network: top {len(pairs)} SF OD flows",
        fontsize=17,
        weight="bold",
        color="#183642",
        pad=16,
    )
    ax.text(
        0.01,
        0.02,
        "Filtered for presentation readability; edge width reflects trip count.",
        transform=ax.transAxes,
        fontsize=10.5,
        color="#50666d",
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(color="#d9d4c8", alpha=0.35, linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    x_pad = max((x_max - x_min) * 0.18, 0.01)
    y_pad = max((y_max - y_min) * 0.18, 0.01)
    ax.set_xlim(x_min - x_pad, x_max + x_pad)
    ax.set_ylim(y_min - y_pad, y_max + y_pad)

    out = output_dir / f"station_network_top{top_edges}_subscriber.png"
    save_figure(out)
    return out


def collect_top_origins(cleaned_path: Path, chunksize: int, top_n: int) -> pd.DataFrame:
    require_file(cleaned_path)
    station_counts: Counter[str] = Counter()
    station_names: dict[str, str] = {}

    for chunk in pd.read_csv(
        cleaned_path,
        usecols=["start_station_id", "start_station_name"],
        chunksize=chunksize,
        low_memory=False,
    ):
        chunk = chunk.dropna(subset=["start_station_id"])
        chunk["start_station_id"] = chunk["start_station_id"].astype(str)
        station_counts.update(chunk["start_station_id"])
        names = chunk.dropna(subset=["start_station_name"]).drop_duplicates("start_station_id")
        station_names.update(
            dict(zip(names["start_station_id"], names["start_station_name"].astype(str)))
        )

    rows = []
    for station_id, count in station_counts.most_common(top_n):
        name = station_names.get(station_id, station_id)
        rows.append({"station_id": station_id, "station_name": name, "trip_count": count})
    return pd.DataFrame(rows)


def plot_top_origins(input_dir: Path, output_dir: Path, user_type: str, chunksize: int, top_n: int) -> Path:
    cleaned_path = input_dir / f"cleaned_{user_type.lower()}.csv.gz"
    top = collect_top_origins(cleaned_path, chunksize, top_n)
    if top.empty:
        raise ValueError(f"No origin station rows found in {cleaned_path}")

    top = top.sort_values("trip_count", ascending=True)
    labels = top["station_name"].str.slice(0, 38) + " (" + top["station_id"].astype(str) + ")"

    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.barh(labels, top["trip_count"], color=USER_COLORS[user_type])
    ax.set_title(f"Top origin stations: {user_type}", fontsize=14, weight="bold")
    ax.set_xlabel("Trip count")
    ax.set_ylabel("")
    ax.grid(axis="x", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    out = output_dir / f"top_origin_stations_{user_type.lower()}.png"
    save_figure(out)
    return out


def plot_workflow(output_dir: Path) -> Path:
    steps = [
        ("Raw public\ntrip files", "#eff6f7"),
        ("Schema\nstandardization", "#f4f0df"),
        ("Cleaning +\nuser split", "#eef3e0"),
        ("Event files +\nOD tables", "#f7eadf"),
        ("Network +\nEDA figures", "#e9eef8"),
        ("Model-ready\nsamples", "#eef4ec"),
    ]
    fig, ax = plt.subplots(figsize=(15.5, 4.8))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    title_color = "#183642"
    edge_color = "#2f4858"

    ax.text(
        0.5,
        0.88,
        "From raw trip records to modeling-ready event data",
        ha="center",
        va="center",
        fontsize=19,
        weight="bold",
        color=title_color,
    )

    x0 = 0.035
    box_w = 0.13
    gap = 0.032
    y = 0.39
    box_h = 0.31

    for idx, (label, fill) in enumerate(steps):
        x = x0 + idx * (box_w + gap)
        patch = FancyBboxPatch(
            (x, y),
            box_w,
            box_h,
            boxstyle="round,pad=0.018,rounding_size=0.025",
            linewidth=1.7,
            edgecolor=edge_color,
            facecolor=fill,
        )
        ax.add_patch(patch)
        ax.text(
            x + box_w / 2,
            y + box_h / 2,
            label,
            ha="center",
            va="center",
            fontsize=11.2,
            weight="bold",
            color=title_color,
            linespacing=1.15,
        )
        if idx < len(steps) - 1:
            start = x + box_w + 0.006
            end = x + box_w + gap - 0.006
            ax.annotate(
                "",
                xy=(end, y + box_h / 2),
                xytext=(start, y + box_h / 2),
                arrowprops=dict(arrowstyle="-|>", lw=1.7, color=edge_color, shrinkA=0, shrinkB=0),
            )

    ax.text(
        0.5,
        0.18,
        "Prepared across five city datasets; San Francisco figures are used as examples in this deck.",
        ha="center",
        va="center",
        fontsize=11,
        color="#50666d",
        )

    out = output_dir / "presentation_workflow_diagram.png"
    save_figure(out)
    return out


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = [
        plot_workflow(output_dir),
        plot_hourly_demand(input_dir, output_dir, args.chunksize),
        plot_inter_event_distribution(input_dir, output_dir),
        plot_od_heatmap(input_dir, output_dir, args.top_n),
        plot_filtered_station_network(input_dir, output_dir, args.network_top_edges),
        plot_top_origins(input_dir, output_dir, "Subscriber", args.chunksize, args.top_origin_n),
        plot_top_origins(input_dir, output_dir, "Customer", args.chunksize, args.top_origin_n),
    ]

    print("\nSaved presentation figures:")
    for path in saved_paths:
        print(f"- {path}")


if __name__ == "__main__":
    main()
