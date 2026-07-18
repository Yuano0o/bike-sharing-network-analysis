#!/usr/bin/env python3
"""Build graph visualizations from cleaned bikeshare outputs.

This script is intentionally separated from the cleaning pipeline so that
large-scale batch cleaning can stay lightweight while graph exploration remains
optional and city-specific.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    if not os.environ.get("MPLBACKEND"):
        os.environ["MPLBACKEND"] = "Agg"
    import matplotlib.pyplot as plt
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    plt = None

try:
    import networkx as nx
    from networkx.algorithms.community import greedy_modularity_communities
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    nx = None
    greedy_modularity_communities = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create graph visualizations from bikeshare OD outputs.")
    parser.add_argument(
        "--pairs",
        required=True,
        help="Path to station_pairs_normalized.csv from the cleaning step.",
    )
    parser.add_argument(
        "--stations",
        required=True,
        help="Path to station_lookup_canonical.csv from the cleaning step.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for graph outputs.",
    )
    parser.add_argument(
        "--user-type",
        choices=["Customer", "Subscriber"],
        help="Filter to one user_type. If omitted, all user types are combined in separate outputs.",
    )
    parser.add_argument(
        "--weight-column",
        choices=["trip_count", "normalized_count"],
        default="normalized_count",
        help="Edge weight used to build the graph.",
    )
    parser.add_argument(
        "--min-normalized",
        type=float,
        default=0.03,
        help="Minimum normalized_count threshold used to keep edges. Default: 0.03.",
    )
    parser.add_argument(
        "--min-trip-count",
        type=int,
        default=1,
        help="Minimum trip_count threshold used to keep edges. Default: 1.",
    )
    parser.add_argument(
        "--top-edges",
        type=int,
        default=None,
        help="Optional limit to keep only the strongest N edges after thresholding.",
    )
    parser.add_argument(
        "--highlight-most-visited",
        action="store_true",
        help="Highlight the station with the highest total arrivals.",
    )
    parser.add_argument(
        "--detect-communities",
        action="store_true",
        help="Run community detection and write assignments/plots.",
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Export nodes and edges as JSON for downstream interactive visualization.",
    )
    return parser.parse_args()


def ensure_dependencies() -> None:
    missing = []
    if plt is None:
        missing.append("matplotlib")
    if nx is None:
        missing.append("networkx")
    if missing:
        raise SystemExit(
            "Missing required visualization dependencies: "
            + ", ".join(missing)
            + ". Install them before running graph visualization."
        )


def load_inputs(pairs_path: Path, stations_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    pairs = pd.read_csv(pairs_path)
    stations = pd.read_csv(stations_path)

    for col in ["start_station_id", "end_station_id"]:
        pairs[col] = pairs[col].astype("string")
    stations["station_id"] = stations["station_id"].astype("string")
    stations = stations.drop_duplicates(subset=["station_id"], keep="last").copy()
    return pairs, stations


def filter_pairs(
    pairs: pd.DataFrame,
    user_type: Optional[str],
    min_normalized: float,
    min_trip_count: int,
    top_edges: Optional[int],
    weight_column: str,
) -> pd.DataFrame:
    df = pairs.copy()
    if user_type is not None:
        df = df[df["user_type"] == user_type].copy()

    df = df[(df["normalized_count"] >= min_normalized) & (df["trip_count"] >= min_trip_count)].copy()
    df = df.sort_values(weight_column, ascending=False)
    if top_edges is not None:
        df = df.head(top_edges).copy()
    return df


def build_graph(df: pd.DataFrame, stations: pd.DataFrame, weight_column: str):
    graph = nx.DiGraph()
    station_info = stations.set_index("station_id").to_dict("index")

    for row in df.itertuples(index=False):
        graph.add_edge(
            row.start_station_id,
            row.end_station_id,
            weight=float(getattr(row, weight_column)),
            trip_count=int(row.trip_count),
            normalized_count=float(row.normalized_count),
        )

    for node in graph.nodes():
        info = station_info.get(node, {})
        graph.nodes[node]["name"] = info.get("canonical_name", node)
        graph.nodes[node]["lat"] = info.get("canonical_lat")
        graph.nodes[node]["lng"] = info.get("canonical_lng")

    return graph


def compute_positions(graph) -> dict:
    positions = {}
    missing = []
    for node, data in graph.nodes(data=True):
        lat = data.get("lat")
        lng = data.get("lng")
        if pd.notna(lat) and pd.notna(lng):
            positions[node] = (float(lng), float(lat))
        else:
            missing.append(node)

    if missing:
        fallback = nx.spring_layout(graph.subgraph(missing), seed=42)
        for node, coords in fallback.items():
            positions[node] = coords
    return positions


def most_visited_station(df: pd.DataFrame) -> Optional[str]:
    if df.empty:
        return None
    totals = df.groupby("end_station_id")["trip_count"].sum().sort_values(ascending=False)
    return str(totals.index[0]) if not totals.empty else None


def draw_graph(graph, positions: dict, output_path: Path, title: str, highlighted_node: Optional[str]) -> None:
    plt.figure(figsize=(13, 10))

    node_sizes = []
    node_colors = []
    for node in graph.nodes():
        in_weight = sum(d["trip_count"] for _, _, d in graph.in_edges(node, data=True))
        node_sizes.append(max(120, min(1200, 120 + in_weight * 0.1)))
        node_colors.append("#d62728" if node == highlighted_node else "#4C78A8")

    edge_widths = [max(0.5, min(5.0, data["weight"] * 20)) for _, _, data in graph.edges(data=True)]

    nx.draw_networkx_edges(
        graph,
        positions,
        arrows=True,
        arrowstyle="-|>",
        arrowsize=10,
        width=edge_widths,
        alpha=0.35,
        edge_color="#7f7f7f",
    )
    nx.draw_networkx_nodes(graph, positions, node_size=node_sizes, node_color=node_colors, alpha=0.9)

    label_map = {}
    for node, data in graph.nodes(data=True):
        if node == highlighted_node:
            label_map[node] = data.get("name", node)
    if label_map:
        nx.draw_networkx_labels(graph, positions, labels=label_map, font_size=8)

    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close()


def draw_communities(graph, positions: dict, output_path: Path, title: str) -> pd.DataFrame:
    communities = list(greedy_modularity_communities(graph.to_undirected()))
    node_rows = []
    palette = [
        "#4C78A8",
        "#F58518",
        "#E45756",
        "#72B7B2",
        "#54A24B",
        "#EECA3B",
        "#B279A2",
        "#FF9DA6",
        "#9D755D",
        "#BAB0AC",
    ]

    plt.figure(figsize=(13, 10))
    for idx, community in enumerate(communities):
        color = palette[idx % len(palette)]
        sub_nodes = list(community)
        nx.draw_networkx_nodes(
            graph,
            positions,
            nodelist=sub_nodes,
            node_color=color,
            node_size=180,
            alpha=0.9,
        )
        for node in sub_nodes:
            node_rows.append({"station_id": node, "community_id": idx})

    nx.draw_networkx_edges(graph, positions, alpha=0.25, arrows=False, edge_color="#777777")
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close()

    return pd.DataFrame(node_rows)


def export_graph_json(graph, output_path: Path) -> None:
    payload = {
        "nodes": [
            {
                "id": node,
                "name": data.get("name"),
                "lat": data.get("lat"),
                "lng": data.get("lng"),
            }
            for node, data in graph.nodes(data=True)
        ],
        "edges": [
            {
                "source": source,
                "target": target,
                "weight": data.get("weight"),
                "trip_count": data.get("trip_count"),
                "normalized_count": data.get("normalized_count"),
            }
            for source, target, data in graph.edges(data=True)
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2))


def run_for_subset(
    df: pd.DataFrame,
    stations: pd.DataFrame,
    output_dir: Path,
    subset_name: str,
    weight_column: str,
    highlight_most_visited: bool,
    detect_communities: bool,
    export_json_flag: bool,
) -> None:
    if df.empty:
        print(f"[WARN] No edges left for subset={subset_name}.")
        return

    graph = build_graph(df, stations, weight_column)
    positions = compute_positions(graph)
    highlight_node = most_visited_station(df) if highlight_most_visited else None

    summary = pd.DataFrame(
        [
            {
                "subset": subset_name,
                "nodes": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "weight_column": weight_column,
                "highlighted_station_id": highlight_node,
            }
        ]
    )
    summary.to_csv(output_dir / f"graph_summary_{subset_name}.csv", index=False)

    draw_graph(
        graph,
        positions,
        output_dir / f"graph_{subset_name}.png",
        title=f"Bikeshare graph: {subset_name}",
        highlighted_node=highlight_node,
    )

    top_end = (
        df.groupby("end_station_id")["trip_count"].sum().sort_values(ascending=False).reset_index(name="total_arrivals")
    )
    top_end.to_csv(output_dir / f"most_visited_end_stations_{subset_name}.csv", index=False)

    if detect_communities:
        assignments = draw_communities(
            graph,
            positions,
            output_dir / f"graph_communities_{subset_name}.png",
            title=f"Bikeshare communities: {subset_name}",
        )
        assignments.to_csv(output_dir / f"community_assignments_{subset_name}.csv", index=False)

    if export_json_flag:
        export_graph_json(graph, output_dir / f"graph_{subset_name}.json")


def main() -> None:
    args = parse_args()
    ensure_dependencies()

    pairs_path = Path(args.pairs)
    stations_path = Path(args.stations)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs, stations = load_inputs(pairs_path, stations_path)

    subsets = [args.user_type] if args.user_type else sorted(pairs["user_type"].dropna().unique())
    for subset in subsets:
        filtered = filter_pairs(
            pairs,
            user_type=subset,
            min_normalized=args.min_normalized,
            min_trip_count=args.min_trip_count,
            top_edges=args.top_edges,
            weight_column=args.weight_column,
        )
        run_for_subset(
            filtered,
            stations,
            output_dir,
            subset_name=subset.lower(),
            weight_column=args.weight_column,
            highlight_most_visited=args.highlight_most_visited,
            detect_communities=args.detect_communities,
            export_json_flag=args.export_json,
        )

    print(f"[DONE] Graph outputs written to {output_dir.resolve()}")


if __name__ == "__main__":
    main()
