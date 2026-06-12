from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

import folium
import pandas as pd
from folium.plugins import Fullscreen, HeatMap, MarkerCluster, MiniMap
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Page, Scatter

from common import ensure_dir, write_json


OPERATOR_COLORS = {
    "ALPACA": "#2563eb",
    "GBIKE": "#0f766e",
}
RIDE_INTERVAL_MINUTES = 5.0
RIDE_INTERVAL_TOLERANCE_MINUTES = 1.0
RIDE_DISTANCE_THRESHOLD_M = 100.0
MAX_OD_LINES = 120
MAX_RIDE_SEGMENTS = 1000


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_processed(processed_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        "accumulated": read_csv(processed_dir / "sejong_pm_snapshots_accumulated.csv"),
        "latest": read_csv(processed_dir / "sejong_pm_latest_snapshot.csv"),
        "operator_counts": read_csv(processed_dir / "sejong_pm_operator_snapshot_counts.csv"),
        "zone_counts": read_csv(processed_dir / "sejong_pm_zone_snapshot_counts.csv"),
        "device_intervals": read_csv(processed_dir / "sejong_pm_device_intervals.csv"),
        "activity_by_zone": read_csv(processed_dir / "sejong_pm_activity_by_zone.csv"),
    }


def chart_init(page_title: str) -> opts.InitOpts:
    return opts.InitOpts(
        width="1180px",
        height="460px",
        page_title=page_title,
        bg_color="#ffffff",
    )


def empty_bar(title: str, subtitle: str) -> Bar:
    return (
        Bar(init_opts=chart_init(title))
        .add_xaxis(["no data"])
        .add_yaxis(subtitle, [0])
        .set_global_opts(title_opts=opts.TitleOpts(title=title, subtitle=subtitle))
    )


def empty_line(title: str, subtitle: str) -> Line:
    return (
        Line(init_opts=chart_init(title))
        .add_xaxis(["no data"])
        .add_yaxis(subtitle, [0])
        .set_global_opts(title_opts=opts.TitleOpts(title=title, subtitle=subtitle))
    )


def make_latest_operator_bar(latest: pd.DataFrame) -> Bar:
    if latest.empty:
        return empty_bar("Latest PM Supply", "Missing latest snapshot")
    rows = (
        latest.groupby("operator_name", dropna=False)
        .agg(
            device_count=("device_id", "nunique"),
            avg_battery=("battery_level", "mean"),
        )
        .reset_index()
        .sort_values("device_count", ascending=False)
    )
    chart = (
        Bar(init_opts=chart_init("Latest PM Supply"))
        .add_xaxis(rows["operator_name"].astype(str).tolist())
        .add_yaxis("Devices", rows["device_count"].astype(int).tolist())
        .add_yaxis("Avg battery", rows["avg_battery"].round(1).tolist())
    )
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Latest Sejong TAGO PM supply",
            subtitle="Device count and average battery by provider",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="shadow"),
        yaxis_opts=opts.AxisOpts(name="Count / battery %"),
    )


def make_operator_trend(operator_counts: pd.DataFrame) -> Line:
    if operator_counts.empty:
        return empty_line("Provider Trend", "Missing operator counts")
    data = operator_counts.copy()
    data["timestamp"] = data["timestamp"].astype(str)
    pivot = (
        data.pivot_table(
            index="timestamp",
            columns="operator_name",
            values="device_count",
            aggfunc="sum",
        )
        .fillna(0)
        .sort_index()
    )
    chart = Line(init_opts=chart_init("Provider Trend")).add_xaxis(pivot.index.astype(str).tolist())
    for operator in pivot.columns:
        chart.add_yaxis(str(operator), pivot[operator].astype(float).round(0).tolist(), is_smooth=True)
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Observed PM supply over time",
            subtitle="Each cron snapshot appends one point per provider",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
        yaxis_opts=opts.AxisOpts(name="Devices"),
    )


def make_activity_trend(intervals: pd.DataFrame) -> Line:
    if intervals.empty:
        return empty_line("Movement Activity", "Needs at least two snapshots")
    data = intervals.copy()
    data["timestamp"] = data["timestamp"].astype(str)
    grouped = (
        data.groupby("timestamp")
        .agg(
            moved_50m=("moved_50m", "sum"),
            moved_200m=("moved_200m", "sum"),
            avg_distance_m=("distance_m", "mean"),
        )
        .reset_index()
        .sort_values("timestamp")
    )
    chart = Line(init_opts=chart_init("Movement Activity")).add_xaxis(grouped["timestamp"].tolist())
    chart.add_yaxis("Moved >= 50m", grouped["moved_50m"].astype(int).tolist(), is_smooth=True)
    chart.add_yaxis("Moved >= 200m", grouped["moved_200m"].astype(int).tolist(), is_smooth=True)
    chart.add_yaxis("Avg interval distance m", grouped["avg_distance_m"].round(1).tolist(), is_smooth=True)
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Snapshot-to-snapshot activity proxy",
            subtitle="Movement is inferred from device location deltas",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
        yaxis_opts=opts.AxisOpts(name="Intervals / meters"),
    )


def make_battery_scatter(latest: pd.DataFrame) -> Scatter:
    if latest.empty or not {"battery_level", "operator_name"}.issubset(latest.columns):
        return (
            Scatter(init_opts=chart_init("Battery Distribution"))
            .add_xaxis([0])
            .add_yaxis("missing latest snapshot", [0])
            .set_global_opts(title_opts=opts.TitleOpts(title="Battery distribution"))
        )
    rows = latest.copy()
    rows["battery_level"] = pd.to_numeric(rows["battery_level"], errors="coerce")
    rows = rows.dropna(subset=["battery_level"]).sort_values(["operator_name", "battery_level"])
    rows["rank"] = range(1, len(rows) + 1)
    chart = Scatter(init_opts=chart_init("Battery Distribution")).add_xaxis(rows["rank"].tolist())
    for operator, group in rows.groupby("operator_name", dropna=False):
        chart.add_yaxis(str(operator), group["battery_level"].round(1).tolist(), symbol_size=6)
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Latest battery distribution",
            subtitle="Sorted by provider and battery level",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="item"),
        xaxis_opts=opts.AxisOpts(name="Device rank"),
        yaxis_opts=opts.AxisOpts(name="Battery %", min_=0, max_=100),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
    )


def render_chart_dashboard(tables: dict[str, pd.DataFrame], out_path: Path) -> None:
    page = Page(layout=Page.SimplePageLayout, page_title="Sejong TAGO PM Dashboard")
    page.add(
        make_latest_operator_bar(tables["latest"]),
        make_operator_trend(tables["operator_counts"]),
        make_activity_trend(tables["device_intervals"]),
        make_battery_scatter(tables["latest"]),
    )
    page.render(str(out_path))


def operator_color(operator_name: Any) -> str:
    text = str(operator_name or "UNKNOWN")
    if text in OPERATOR_COLORS:
        return OPERATOR_COLORS[text]
    palette = ["#7c3aed", "#db2777", "#ea580c", "#0891b2", "#16a34a"]
    return palette[sum(ord(char) for char in text) % len(palette)]


def map_center(points: pd.DataFrame) -> list[float]:
    if points.empty or not {"latitude", "longitude"}.issubset(points.columns):
        return [36.4801, 127.2890]
    return [
        float(pd.to_numeric(points["latitude"], errors="coerce").median()),
        float(pd.to_numeric(points["longitude"], errors="coerce").median()),
    ]


def add_latest_heatmap(m: folium.Map, latest: pd.DataFrame) -> None:
    if latest.empty:
        return
    heat = []
    for row in latest.dropna(subset=["latitude", "longitude"]).itertuples():
        battery = pd.to_numeric(getattr(row, "battery_level", None), errors="coerce")
        weight = 1.0 if pd.isna(battery) else max(float(battery), 1.0) / 100.0
        heat.append([float(row.latitude), float(row.longitude), weight])
    if heat:
        HeatMap(
            heat,
            name="Latest PM heatmap",
            radius=20,
            blur=16,
            min_opacity=0.25,
        ).add_to(m)


def add_zone_markers(m: folium.Map, zone_counts: pd.DataFrame) -> int:
    if zone_counts.empty:
        return 0
    latest_ts = str(zone_counts["timestamp"].max())
    latest = zone_counts[zone_counts["timestamp"].astype(str) == latest_ts].copy()
    if latest.empty:
        return 0
    grouped = (
        latest.groupby("zone_id")
        .agg(
            device_count=("device_count", "sum"),
            effective_device_count=("effective_device_count", "sum"),
            avg_battery=("avg_battery", "mean"),
            latitude=("zone_center_latitude", "first"),
            longitude=("zone_center_longitude", "first"),
        )
        .reset_index()
    )
    zone_layer = folium.FeatureGroup(name="500m zone supply", show=True).add_to(m)
    for row in grouped.dropna(subset=["latitude", "longitude"]).itertuples():
        count = int(getattr(row, "device_count", 0))
        radius = max(5, min(26, 4 + count ** 0.5))
        folium.CircleMarker(
            location=[float(row.latitude), float(row.longitude)],
            radius=radius,
            color="#0f172a",
            weight=1,
            fill=True,
            fill_color="#38bdf8",
            fill_opacity=0.35,
            popup=folium.Popup(
                html=(
                    f"<b>{row.zone_id}</b><br>"
                    f"devices={count}<br>"
                    f"effective={int(getattr(row, 'effective_device_count', 0))}<br>"
                    f"avg_battery={float(getattr(row, 'avg_battery', 0)):.1f}%"
                ),
                max_width=260,
            ),
        ).add_to(zone_layer)
    return len(grouped)


def add_device_markers(m: folium.Map, latest: pd.DataFrame, max_markers: int) -> int:
    if latest.empty or max_markers == 0:
        return 0
    points = latest.dropna(subset=["latitude", "longitude"]).copy()
    points["battery_level"] = pd.to_numeric(points["battery_level"], errors="coerce")
    points = points.sort_values(["battery_level", "operator_name", "device_id"])
    if max_markers > 0:
        points = points.head(max_markers)
    cluster = MarkerCluster(name=f"Latest PM devices ({len(points):,})").add_to(m)
    for row in points.itertuples():
        operator_name = str(getattr(row, "operator_name", "UNKNOWN"))
        battery = pd.to_numeric(getattr(row, "battery_level", None), errors="coerce")
        color = operator_color(operator_name)
        icon_color = "red" if pd.notna(battery) and float(battery) < 20 else "blue"
        folium.Marker(
            location=[float(row.latitude), float(row.longitude)],
            icon=folium.Icon(color=icon_color, icon="bolt", prefix="fa"),
            popup=folium.Popup(
                html=(
                    f"<b>{operator_name}</b><br>"
                    f"device={getattr(row, 'device_id', '')}<br>"
                    f"battery={float(battery):.0f}%<br>"
                    f"zone={getattr(row, 'zone_id', '')}<br>"
                    f"timestamp={getattr(row, 'timestamp', '')}<br>"
                    f"<span style='color:{color}'>provider color</span>"
                ),
                max_width=300,
            ),
        ).add_to(cluster)
    return len(points)


def ride_segment_candidates(intervals: pd.DataFrame) -> pd.DataFrame:
    required = {
        "timestamp",
        "prev_timestamp",
        "operator_name",
        "device_id",
        "prev_latitude",
        "prev_longitude",
        "latitude",
        "longitude",
        "interval_minutes",
        "distance_m",
    }
    if intervals.empty or not required.issubset(intervals.columns):
        return pd.DataFrame()
    rows = intervals.copy()
    numeric_cols = [
        "prev_latitude",
        "prev_longitude",
        "latitude",
        "longitude",
        "interval_minutes",
        "distance_m",
        "speed_kmph",
    ]
    for col in numeric_cols:
        if col in rows.columns:
            rows[col] = pd.to_numeric(rows[col], errors="coerce")
    rows = rows.dropna(
        subset=[
            "prev_latitude",
            "prev_longitude",
            "latitude",
            "longitude",
            "interval_minutes",
            "distance_m",
        ]
    )
    min_minutes = RIDE_INTERVAL_MINUTES - RIDE_INTERVAL_TOLERANCE_MINUTES
    max_minutes = RIDE_INTERVAL_MINUTES + RIDE_INTERVAL_TOLERANCE_MINUTES
    segments = rows[
        rows["interval_minutes"].between(min_minutes, max_minutes)
        & (rows["distance_m"] >= RIDE_DISTANCE_THRESHOLD_M)
    ].copy()
    if segments.empty:
        return segments
    segments = segments.sort_values(["timestamp", "operator_name", "device_id"]).reset_index(drop=True)
    segments.insert(0, "ride_segment_id", segments.index + 1)
    return segments


def build_od_flows(segments: pd.DataFrame) -> pd.DataFrame:
    required = {"prev_zone_id", "zone_id", "prev_latitude", "prev_longitude", "latitude", "longitude"}
    if segments.empty or not required.issubset(segments.columns):
        return pd.DataFrame()
    return (
        segments.groupby(["prev_zone_id", "zone_id"], dropna=False)
        .agg(
            trip_count=("device_id", "count"),
            device_count=("device_id", "nunique"),
            operator_count=("operator_name", "nunique"),
            avg_distance_m=("distance_m", "mean"),
            median_distance_m=("distance_m", "median"),
            avg_speed_kmph=("speed_kmph", "mean"),
            first_timestamp=("timestamp", "min"),
            last_timestamp=("timestamp", "max"),
            origin_latitude=("prev_latitude", "mean"),
            origin_longitude=("prev_longitude", "mean"),
            dest_latitude=("latitude", "mean"),
            dest_longitude=("longitude", "mean"),
        )
        .reset_index()
        .sort_values(["trip_count", "avg_distance_m"], ascending=[False, False])
    )


def write_ride_outputs(processed_dir: Path, segments: pd.DataFrame, od_flows: pd.DataFrame) -> dict[str, str]:
    paths = {
        "ride_segments": processed_dir / "sejong_pm_inferred_rides.csv",
        "od_flows": processed_dir / "sejong_pm_od_flows.csv",
    }
    segments.to_csv(paths["ride_segments"], index=False)
    od_flows.to_csv(paths["od_flows"], index=False)
    return {name: str(path) for name, path in paths.items()}


def summarize_ride_segments(segments: pd.DataFrame, od_flows: pd.DataFrame) -> dict[str, Any]:
    if segments.empty:
        return {
            "ride_segments": 0,
            "ride_devices": 0,
            "ride_operators": 0,
            "avg_distance_m": 0.0,
            "median_distance_m": 0.0,
            "max_distance_m": 0.0,
            "avg_speed_kmph": 0.0,
            "first_timestamp": None,
            "last_timestamp": None,
            "od_pairs": 0,
        }
    speed = pd.to_numeric(segments.get("speed_kmph"), errors="coerce")
    return {
        "ride_segments": int(len(segments)),
        "ride_devices": int(segments["device_id"].nunique()),
        "ride_operators": int(segments["operator_name"].nunique()),
        "avg_distance_m": float(segments["distance_m"].mean()),
        "median_distance_m": float(segments["distance_m"].median()),
        "max_distance_m": float(segments["distance_m"].max()),
        "avg_speed_kmph": float(speed.mean()) if speed.notna().any() else 0.0,
        "first_timestamp": str(segments["timestamp"].min()),
        "last_timestamp": str(segments["timestamp"].max()),
        "od_pairs": int(len(od_flows)),
    }


def add_ride_summary_panel(m: folium.Map, summary: dict[str, Any], rendered_segments: int, rendered_od: int) -> None:
    html_panel = f"""
    <div style="
        position: fixed;
        left: 18px;
        bottom: 28px;
        z-index: 9999;
        width: 318px;
        padding: 12px 14px;
        background: rgba(255,255,255,0.94);
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(15,23,42,0.18);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        color: #0f172a;
        line-height: 1.45;
    ">
      <div style="font-size: 14px; font-weight: 700; margin-bottom: 6px;">Sejong inferred ride movement</div>
      <div style="font-size: 12px;">Rule: {RIDE_INTERVAL_MINUTES:.0f}min +/- {RIDE_INTERVAL_TOLERANCE_MINUTES:.0f}min, distance >= {RIDE_DISTANCE_THRESHOLD_M:.0f}m</div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; margin-top: 8px; font-size: 12px;">
        <span>total segments</span><b>{summary['ride_segments']:,}</b>
        <span>rendered samples</span><b>{rendered_segments:,}</b>
        <span>devices</span><b>{summary['ride_devices']:,}</b>
        <span>OD pairs</span><b>{summary['od_pairs']:,}</b>
        <span>rendered OD</span><b>{rendered_od:,}</b>
        <span>avg distance</span><b>{summary['avg_distance_m']:.0f}m</b>
        <span>median</span><b>{summary['median_distance_m']:.0f}m</b>
        <span>avg speed</span><b>{summary['avg_speed_kmph']:.1f}km/h</b>
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(html_panel))


def add_ride_segments(
    m: folium.Map,
    segments: pd.DataFrame,
    od_flows: pd.DataFrame,
    max_ride_segments: int,
    max_od_lines: int,
) -> dict[str, Any]:
    max_ride_segments = max(0, int(max_ride_segments))
    max_od_lines = max(0, int(max_od_lines))
    summary = summarize_ride_segments(segments, od_flows)
    sampled_segments = segments.sort_values("distance_m", ascending=False).head(max_ride_segments)
    rendered_od = od_flows.head(max_od_lines)
    add_ride_summary_panel(m, summary, len(sampled_segments), len(rendered_od))

    if not rendered_od.empty:
        od_layer = folium.FeatureGroup(
            name=f"Top OD flows by 500m grid ({len(rendered_od):,}/{len(od_flows):,})",
            show=True,
        ).add_to(m)
        for row in rendered_od.itertuples():
            trip_count = int(getattr(row, "trip_count", 0))
            popup = (
                f"<b>{html.escape(str(row.prev_zone_id))} -> {html.escape(str(row.zone_id))}</b><br>"
                f"rides={trip_count:,}<br>"
                f"devices={int(getattr(row, 'device_count', 0)):,}<br>"
                f"operators={int(getattr(row, 'operator_count', 0)):,}<br>"
                f"avg_distance={float(getattr(row, 'avg_distance_m', 0.0)):.0f}m<br>"
                f"avg_speed={float(getattr(row, 'avg_speed_kmph', 0.0)):.1f}km/h"
            )
            folium.PolyLine(
                locations=[
                    [float(row.origin_latitude), float(row.origin_longitude)],
                    [float(row.dest_latitude), float(row.dest_longitude)],
                ],
                color="#e11d48",
                weight=max(2.0, min(10.0, 1.4 + trip_count ** 0.65)),
                opacity=0.76,
                popup=folium.Popup(popup, max_width=320),
                tooltip=f"{row.prev_zone_id} -> {row.zone_id}: {trip_count:,}",
            ).add_to(od_layer)
            folium.CircleMarker(
                location=[float(row.dest_latitude), float(row.dest_longitude)],
                radius=max(3, min(9, 2 + trip_count ** 0.45)),
                color="#be123c",
                weight=1,
                fill=True,
                fill_color="#fb7185",
                fill_opacity=0.85,
                popup=folium.Popup(popup, max_width=320),
            ).add_to(od_layer)

    if not sampled_segments.empty:
        raw_layer = folium.FeatureGroup(
            name=f"Sampled ride segments ({len(sampled_segments):,}/{len(segments):,})",
            show=False,
        ).add_to(m)
        for row in sampled_segments.itertuples():
            operator_name = str(getattr(row, "operator_name", "UNKNOWN"))
            color = operator_color(operator_name)
            distance_m = float(getattr(row, "distance_m", 0.0))
            speed_kmph = pd.to_numeric(getattr(row, "speed_kmph", None), errors="coerce")
            speed_text = "n/a" if pd.isna(speed_kmph) else f"{float(speed_kmph):.1f}km/h"
            popup = (
                f"<b>{html.escape(operator_name)}</b><br>"
                f"device={html.escape(str(getattr(row, 'device_id', '')))}<br>"
                f"from={html.escape(str(getattr(row, 'prev_timestamp', '')))}<br>"
                f"to={html.escape(str(getattr(row, 'timestamp', '')))}<br>"
                f"distance={distance_m:.0f}m<br>"
                f"interval={float(getattr(row, 'interval_minutes', 0.0)):.1f}min<br>"
                f"speed={speed_text}<br>"
                f"OD={html.escape(str(getattr(row, 'prev_zone_id', '')))}"
                f" -> {html.escape(str(getattr(row, 'zone_id', '')))}"
            )
            folium.PolyLine(
                locations=[
                    [float(row.prev_latitude), float(row.prev_longitude)],
                    [float(row.latitude), float(row.longitude)],
                ],
                color=color,
                weight=max(1.2, min(4.0, distance_m / 700.0)),
                opacity=0.28,
                popup=folium.Popup(popup, max_width=340),
                tooltip=f"{operator_name}: {distance_m:.0f}m",
            ).add_to(raw_layer)
    summary["rendered_ride_segments"] = int(len(sampled_segments))
    summary["rendered_od_flows"] = int(len(rendered_od))
    summary["max_ride_segments"] = int(max_ride_segments)
    summary["max_od_lines"] = int(max_od_lines)
    return summary


def render_map(
    tables: dict[str, pd.DataFrame],
    out_path: Path,
    max_markers: int,
    max_ride_segments: int,
    max_od_lines: int,
) -> dict[str, Any]:
    latest = tables["latest"]
    m = folium.Map(location=map_center(latest), zoom_start=12, tiles=None)
    folium.TileLayer("CartoDB positron", name="CartoDB positron").add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

    add_latest_heatmap(m, latest)
    ride_summary = add_ride_segments(
        m,
        tables["ride_segments"],
        tables["od_flows"],
        max_ride_segments,
        max_od_lines,
    )
    zone_count = add_zone_markers(m, tables["zone_counts"])
    marker_count = add_device_markers(m, latest, max_markers)
    MiniMap(toggle_display=True).add_to(m)
    Fullscreen(position="topright").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(out_path))
    return {
        "latest_rows": int(len(latest)),
        "zone_markers": int(zone_count),
        "device_markers": int(marker_count),
        "center": map_center(latest),
        "ride_summary": ride_summary,
    }


def render(
    processed_dir: Path,
    out_dir: Path,
    max_markers: int = 1500,
    max_ride_segments: int = MAX_RIDE_SEGMENTS,
    max_od_lines: int = MAX_OD_LINES,
) -> dict[str, Any]:
    out_dir = ensure_dir(out_dir)
    tables = load_processed(processed_dir)
    ride_segments = ride_segment_candidates(tables["device_intervals"])
    od_flows = build_od_flows(ride_segments)
    ride_output_paths = write_ride_outputs(processed_dir, ride_segments, od_flows)
    tables["ride_segments"] = ride_segments
    tables["od_flows"] = od_flows

    charts_path = out_dir / "sejong_charts_dashboard.html"
    map_path = out_dir / "sejong_map.html"
    render_chart_dashboard(tables, charts_path)
    map_result = render_map(tables, map_path, max_markers, max_ride_segments, max_od_lines)
    manifest = {
        "processed_dir": str(processed_dir),
        "charts_dashboard": str(charts_path),
        "map": str(map_path),
        "ride_outputs": ride_output_paths,
        "render_limits": {
            "max_device_markers": int(max_markers),
            "max_ride_segments": int(max_ride_segments),
            "max_od_lines": int(max_od_lines),
        },
        "map_result": map_result,
        "tables": {
            name: {"rows": int(len(df)), "columns": list(df.columns)}
            for name, df in tables.items()
        },
    }
    write_json(out_dir / "sejong_visualization_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Sejong TAGO PM visualization outputs.")
    parser.add_argument("--processed-dir", default="data/processed/sejong_tago")
    parser.add_argument("--out", default="outputs/visualizations")
    parser.add_argument("--max-markers", type=int, default=1500)
    parser.add_argument("--max-ride-segments", type=int, default=MAX_RIDE_SEGMENTS)
    parser.add_argument("--max-od-lines", type=int, default=MAX_OD_LINES)
    args = parser.parse_args()
    manifest = render(
        Path(args.processed_dir),
        Path(args.out),
        args.max_markers,
        args.max_ride_segments,
        args.max_od_lines,
    )
    print(f"charts={manifest['charts_dashboard']}")
    print(f"map={manifest['map']}")
    print(f"latest_rows={manifest['map_result']['latest_rows']}")
    print(f"device_markers={manifest['map_result']['device_markers']}")
    print(f"zone_markers={manifest['map_result']['zone_markers']}")
    print(f"ride_segments={manifest['map_result']['ride_summary']['ride_segments']}")
    print(f"rendered_ride_segments={manifest['map_result']['ride_summary']['rendered_ride_segments']}")
    print(f"rendered_od_flows={manifest['map_result']['ride_summary']['rendered_od_flows']}")


if __name__ == "__main__":
    main()
