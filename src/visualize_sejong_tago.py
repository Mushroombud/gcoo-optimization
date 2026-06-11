from __future__ import annotations

import argparse
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


def render_map(tables: dict[str, pd.DataFrame], out_path: Path, max_markers: int) -> dict[str, Any]:
    latest = tables["latest"]
    m = folium.Map(location=map_center(latest), zoom_start=12, tiles=None)
    folium.TileLayer("CartoDB positron", name="CartoDB positron").add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

    add_latest_heatmap(m, latest)
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
    }


def render(processed_dir: Path, out_dir: Path, max_markers: int = 1500) -> dict[str, Any]:
    out_dir = ensure_dir(out_dir)
    tables = load_processed(processed_dir)
    charts_path = out_dir / "sejong_charts_dashboard.html"
    map_path = out_dir / "sejong_map.html"
    render_chart_dashboard(tables, charts_path)
    map_result = render_map(tables, map_path, max_markers)
    manifest = {
        "processed_dir": str(processed_dir),
        "charts_dashboard": str(charts_path),
        "map": str(map_path),
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
    args = parser.parse_args()
    manifest = render(Path(args.processed_dir), Path(args.out), args.max_markers)
    print(f"charts={manifest['charts_dashboard']}")
    print(f"map={manifest['map']}")
    print(f"latest_rows={manifest['map_result']['latest_rows']}")
    print(f"device_markers={manifest['map_result']['device_markers']}")
    print(f"zone_markers={manifest['map_result']['zone_markers']}")


if __name__ == "__main__":
    main()
