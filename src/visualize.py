from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any

import folium
import pandas as pd
from branca.element import Element
from branca.colormap import LinearColormap
from folium.plugins import Fullscreen, HeatMap, MiniMap
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Page, Scatter

from common import ensure_dir, write_json
from prototype_pipeline import make_fixture_dongs


SEOUL_CENTER = [37.5665, 126.9780]
DONG_ID_COLUMNS = ["dong_id", "origin_dong_id", "destination_dong_id"]
NUMERIC_COLUMNS = [
    "x_star_i",
    "total_supply_F",
    "K_i",
    "x_obs_i",
    "B_i",
    "p_i",
    "c_i",
    "H_is",
    "departures_is",
    "arrivals_is",
    "avg_distance_km_i",
    "gcoo_count_is",
    "competitor_count_is",
    "total_pm_count_is",
    "latitude",
    "longitude",
    "parking_bike_count",
    "rack_count",
]


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    for column in DONG_ID_COLUMNS:
        if column in df.columns:
            df[column] = df[column].astype("string")
    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def load_output_tables(input_dir: Path) -> dict[str, pd.DataFrame]:
    bike_path = input_dir / "bike_stations_with_dong.csv"
    if not bike_path.exists():
        bike_path = input_dir / "bike_stations_normalized.csv"

    return {
        "dong_master": read_csv_if_exists(input_dir / "dong_master.csv"),
        "model_inputs": read_csv_if_exists(input_dir / "model_inputs.csv"),
        "allocation": read_csv_if_exists(input_dir / "allocation_optimized.csv"),
        "demand": read_csv_if_exists(input_dir / "demand_scenario.csv"),
        "tago": read_csv_if_exists(input_dir / "tago_scenario.csv"),
        "bike_stations": read_csv_if_exists(bike_path),
    }


def build_dong_metrics(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    dong_ids: set[str] = set()
    for df in tables.values():
        if "dong_id" in df.columns:
            dong_ids.update(str(value) for value in df["dong_id"].dropna().tolist())

    metrics = tables["dong_master"].copy()
    if metrics.empty:
        metrics = pd.DataFrame({"dong_id": sorted(dong_ids)})
    metrics["dong_id"] = metrics["dong_id"].astype("string")

    allocation = tables["allocation"]
    if not allocation.empty:
        metrics = metrics.merge(allocation, on="dong_id", how="outer")

    model_inputs = tables["model_inputs"]
    if not model_inputs.empty:
        metrics = metrics.merge(model_inputs, on="dong_id", how="outer")

    demand = tables["demand"]
    if not demand.empty:
        demand_by_dong = (
            demand.groupby("dong_id", dropna=True)
            .agg(
                mean_H=("H_is", "mean"),
                mean_departures=("departures_is", "mean"),
                mean_arrivals=("arrivals_is", "mean"),
                avg_distance_km=("avg_distance_km_i", "mean"),
            )
            .reset_index()
        )
        metrics = metrics.merge(demand_by_dong, on="dong_id", how="outer")

    tago = tables["tago"]
    if not tago.empty:
        tago_by_dong = (
            tago.groupby("dong_id", dropna=True)
            .agg(
                mean_gcoo_count=("gcoo_count_is", "mean"),
                mean_competitor_count=("competitor_count_is", "mean"),
                mean_total_pm_count=("total_pm_count_is", "mean"),
            )
            .reset_index()
        )
        metrics = metrics.merge(tago_by_dong, on="dong_id", how="outer")

    for column in metrics.columns:
        if column not in {"dong_id", "dong_name", "gu_name"}:
            metrics[column] = pd.to_numeric(metrics[column], errors="coerce")

    numeric_cols = metrics.select_dtypes(include="number").columns
    metrics[numeric_cols] = metrics[numeric_cols].fillna(0)
    metrics["label"] = metrics.apply(format_dong_label, axis=1)
    return metrics.sort_values("label")


def format_dong_label(row: pd.Series) -> str:
    dong_name = row.get("dong_name")
    if pd.notna(dong_name) and str(dong_name).strip():
        return str(dong_name)
    return str(row.get("dong_id", "unknown"))


def choose_map_metric(metrics: pd.DataFrame, requested: str) -> str:
    if requested != "auto" and requested in metrics.columns:
        return requested

    for candidate in [
        "x_star_i",
        "mean_H",
        "mean_total_pm_count",
        "mean_competitor_count",
        "x_obs_i",
        "K_i",
    ]:
        if candidate in metrics.columns and float(metrics[candidate].fillna(0).sum()) > 0:
            return candidate
    if "x_star_i" in metrics.columns:
        return "x_star_i"
    numeric_cols = metrics.select_dtypes(include="number").columns
    return str(numeric_cols[0]) if len(numeric_cols) else "value"


def top_rows(metrics: pd.DataFrame, metric: str, limit: int = 30) -> pd.DataFrame:
    if metric not in metrics.columns:
        return metrics.head(limit)
    return metrics.sort_values(metric, ascending=False).head(limit)


def make_empty_bar(title: str, subtitle: str) -> Bar:
    return (
        Bar(init_opts=chart_init(title))
        .add_xaxis(["no data"])
        .add_yaxis(subtitle, [0])
        .set_global_opts(title_opts=opts.TitleOpts(title=title, subtitle=subtitle))
    )


def chart_init(page_title: str) -> opts.InitOpts:
    return opts.InitOpts(
        width="1180px",
        height="520px",
        page_title=page_title,
        bg_color="#ffffff",
    )


def make_allocation_chart(metrics: pd.DataFrame) -> Bar:
    if metrics.empty or "x_star_i" not in metrics.columns:
        return make_empty_bar("Allocation", "Missing allocation_optimized.csv")

    rows = top_rows(metrics, "x_star_i")
    chart = (
        Bar(init_opts=chart_init("GCOO Allocation"))
        .add_xaxis(rows["label"].astype(str).tolist())
        .add_yaxis("Optimized placement", rows["x_star_i"].round(2).tolist())
    )
    if "x_obs_i" in rows.columns:
        chart.add_yaxis("Observed GCOO", rows["x_obs_i"].round(2).tolist())
    if "K_i" in rows.columns:
        chart.add_yaxis("Capacity", rows["K_i"].round(2).tolist())
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Optimization output by dong",
            subtitle="x_star_i compared with observed supply and capacity",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="shadow"),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
        xaxis_opts=opts.AxisOpts(axislabel_opts=opts.LabelOpts(rotate=35)),
        yaxis_opts=opts.AxisOpts(name="Scooters"),
    )


def make_demand_competition_chart(metrics: pd.DataFrame) -> Bar:
    required = {"mean_H", "mean_competitor_count", "mean_gcoo_count"}
    if metrics.empty or not required.intersection(metrics.columns):
        return make_empty_bar("Demand and competition", "Missing scenario tables")

    sort_metric = "mean_H" if "mean_H" in metrics.columns else list(required)[0]
    rows = top_rows(metrics, sort_metric)
    chart = Bar(init_opts=chart_init("Demand and Competition")).add_xaxis(
        rows["label"].astype(str).tolist()
    )
    if "mean_H" in rows.columns:
        chart.add_yaxis("Mean PM-like demand", rows["mean_H"].round(2).tolist())
    if "mean_competitor_count" in rows.columns:
        chart.add_yaxis(
            "Mean competitor PM",
            rows["mean_competitor_count"].round(2).tolist(),
        )
    if "mean_gcoo_count" in rows.columns:
        chart.add_yaxis("Mean GCOO PM", rows["mean_gcoo_count"].round(2).tolist())
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title="Demand and competitive pressure",
            subtitle="Dong-level averages across available scenario days",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="shadow"),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
        xaxis_opts=opts.AxisOpts(axislabel_opts=opts.LabelOpts(rotate=35)),
        yaxis_opts=opts.AxisOpts(name="Average count"),
    )


def make_scenario_line_chart(tables: dict[str, pd.DataFrame]) -> Line:
    demand = tables["demand"]
    tago = tables["tago"]
    if demand.empty and tago.empty:
        return make_empty_line("Scenario trend", "Missing demand/tago scenarios")

    series = pd.DataFrame()
    if not demand.empty:
        series = demand.groupby("scenario_day", dropna=True)["H_is"].sum().reset_index()
        series = series.rename(columns={"H_is": "total_demand"})
    if not tago.empty:
        tago_series = (
            tago.groupby("scenario_day", dropna=True)
            .agg(
                competitor_pm=("competitor_count_is", "sum"),
                gcoo_pm=("gcoo_count_is", "sum"),
            )
            .reset_index()
        )
        series = tago_series if series.empty else series.merge(tago_series, on="scenario_day", how="outer")

    series = series.sort_values("scenario_day").fillna(0)
    chart = Line(init_opts=chart_init("Scenario Trend")).add_xaxis(
        series["scenario_day"].astype(str).tolist()
    )
    if "total_demand" in series.columns:
        chart.add_yaxis("PM-like demand", series["total_demand"].round(2).tolist(), is_smooth=True)
    if "competitor_pm" in series.columns:
        chart.add_yaxis("Competitor PM", series["competitor_pm"].round(2).tolist(), is_smooth=True)
    if "gcoo_pm" in series.columns:
        chart.add_yaxis("GCOO PM", series["gcoo_pm"].round(2).tolist(), is_smooth=True)
    return chart.set_global_opts(
        title_opts=opts.TitleOpts(title="Scenario trend by operating day"),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        datazoom_opts=[opts.DataZoomOpts(), opts.DataZoomOpts(type_="inside")],
        yaxis_opts=opts.AxisOpts(name="Count"),
    )


def make_empty_line(title: str, subtitle: str) -> Line:
    return (
        Line(init_opts=chart_init(title))
        .add_xaxis(["no data"])
        .add_yaxis(subtitle, [0])
        .set_global_opts(title_opts=opts.TitleOpts(title=title, subtitle=subtitle))
    )


def make_model_scatter(metrics: pd.DataFrame) -> Scatter:
    if metrics.empty or not {"B_i", "x_star_i"}.issubset(metrics.columns):
        return (
            Scatter(init_opts=chart_init("Model Diagnostics"))
            .add_xaxis([0])
            .add_yaxis("missing model_inputs.csv", [0])
            .set_global_opts(
                title_opts=opts.TitleOpts(
                    title="Model diagnostics",
                    subtitle="Requires model_inputs.csv and allocation_optimized.csv",
                )
            )
        )

    rows = metrics.sort_values("label")
    return (
        Scatter(init_opts=chart_init("Model Diagnostics"))
        .add_xaxis(rows["B_i"].round(4).tolist())
        .add_yaxis("Optimized placement", rows["x_star_i"].round(2).tolist())
        .set_global_opts(
            title_opts=opts.TitleOpts(
                title="Imbalance vs optimized placement",
                subtitle="B_i on x-axis, x_star_i on y-axis",
            ),
            tooltip_opts=opts.TooltipOpts(trigger="item"),
            xaxis_opts=opts.AxisOpts(name="B_i"),
            yaxis_opts=opts.AxisOpts(name="x_star_i"),
        )
    )


def render_chart_dashboard(
    tables: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    out_path: Path,
) -> None:
    page = Page(layout=Page.SimplePageLayout, page_title="GCOO Seoul Charts")
    page.add(
        make_allocation_chart(metrics),
        make_demand_competition_chart(metrics),
        make_scenario_line_chart(tables),
        make_model_scatter(metrics),
    )
    page.render(str(out_path))


def load_geojson(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return fixture_dong_geojson()


def fixture_dong_geojson() -> dict[str, Any]:
    features = []
    for dong in make_fixture_dongs():
        coordinates = [
            [
                [dong.min_lon, dong.min_lat],
                [dong.max_lon, dong.min_lat],
                [dong.max_lon, dong.max_lat],
                [dong.min_lon, dong.max_lat],
                [dong.min_lon, dong.min_lat],
            ]
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "dong_id": dong.dong_id,
                    "dong_name": dong.dong_name,
                    "gu_name": dong.gu_name,
                    "source": "fixture_bbox",
                },
                "geometry": {"type": "Polygon", "coordinates": coordinates},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def candidate_property_values(properties: dict[str, Any]) -> list[str]:
    keys = [
        "dong_id",
        "adm_cd",
        "ADM_CD",
        "adm_cd2",
        "ADSTRD_CD",
        "HCODE",
        "code",
        "dong_name",
        "adm_nm",
        "ADM_NM",
        "adm_nm2",
        "emd_nm",
        "name",
    ]
    values = []
    for key in keys:
        value = properties.get(key)
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return values


def attach_metric_properties(
    geojson: dict[str, Any],
    metrics: pd.DataFrame,
    metric: str,
) -> dict[str, Any]:
    by_id = {str(row["dong_id"]): row for row in metrics.to_dict("records") if pd.notna(row.get("dong_id"))}
    by_name = {
        str(row["dong_name"]): row
        for row in metrics.to_dict("records")
        if row.get("dong_name") is not None and pd.notna(row.get("dong_name"))
    }

    for feature in geojson.get("features", []):
        properties = feature.setdefault("properties", {})
        matched = None
        for value in candidate_property_values(properties):
            if value in by_id:
                matched = by_id[value]
                break
            if value in by_name:
                matched = by_name[value]
                break

        metric_value = float(matched.get(metric, 0) or 0) if matched else 0.0
        properties["_gcoo_metric"] = metric
        properties["_gcoo_value"] = round(metric_value, 4)
        properties["_gcoo_dong"] = (
            str(matched.get("label") or matched.get("dong_name") or matched.get("dong_id"))
            if matched
            else str(properties.get("dong_name") or properties.get("adm_nm") or "unmatched")
        )
        properties["_gcoo_source"] = properties.get("source", "geojson")
    return geojson


def iter_coordinate_pairs(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []

    def visit(node: Any) -> None:
        if (
            isinstance(node, list)
            and len(node) >= 2
            and all(isinstance(value, (int, float)) for value in node[:2])
        ):
            lon, lat = float(node[0]), float(node[1])
            pairs.append((lat, lon))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)

    visit(geometry.get("coordinates", []))
    return pairs


def centroid_by_feature(geojson: dict[str, Any]) -> dict[str, tuple[float, float]]:
    centroids = {}
    for feature in geojson.get("features", []):
        pairs = iter_coordinate_pairs(feature.get("geometry", {}))
        if not pairs:
            continue
        lat = sum(pair[0] for pair in pairs) / len(pairs)
        lon = sum(pair[1] for pair in pairs) / len(pairs)
        key = str(feature.get("properties", {}).get("_gcoo_dong", ""))
        if key:
            centroids[key] = (lat, lon)
    return centroids


def map_center(tables: dict[str, pd.DataFrame], geojson: dict[str, Any]) -> list[float]:
    points: list[tuple[float, float]] = []
    bike = tables["bike_stations"]
    if {"latitude", "longitude"}.issubset(bike.columns):
        valid = bike.dropna(subset=["latitude", "longitude"])
        points.extend((float(row.latitude), float(row.longitude)) for row in valid.itertuples())
    for feature in geojson.get("features", []):
        points.extend(iter_coordinate_pairs(feature.get("geometry", {})))
    if not points:
        return SEOUL_CENTER
    return [
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ]


def color_scale(values: list[float]) -> LinearColormap:
    clean = [float(value) for value in values if pd.notna(value)]
    if not clean:
        clean = [0.0]
    vmin = min(0.0, min(clean))
    vmax = max(clean)
    if vmax <= vmin:
        vmax = vmin + 1.0
    return LinearColormap(
        colors=["#f7fbff", "#9ecae1", "#41ab5d", "#fdae61", "#d7191c"],
        vmin=vmin,
        vmax=vmax,
        caption="GCOO map metric",
    )


def add_dong_overlay(
    m: folium.Map,
    geojson: dict[str, Any],
    metric: str,
) -> str:
    values = [
        feature.get("properties", {}).get("_gcoo_value", 0)
        for feature in geojson.get("features", [])
    ]
    scale = color_scale(values)

    def style(feature: dict[str, Any]) -> dict[str, Any]:
        value = feature.get("properties", {}).get("_gcoo_value", 0)
        return {
            "fillColor": scale(value),
            "color": "#142236",
            "weight": 1.8,
            "opacity": 0.95,
            "fillOpacity": 0.42,
        }

    layer = folium.GeoJson(
        geojson,
        name=f"Dong overlay: {metric}",
        style_function=style,
    ).add_to(m)
    add_dong_hover_styles(m)
    scale.add_to(m)
    return layer.get_name()


def add_dong_hover_styles(m: folium.Map) -> None:
    css = """
<style>
.leaflet-container .leaflet-interactive:focus {
  outline: none;
}
.leaflet-overlay-pane svg path.leaflet-interactive {
  transition: stroke-width 150ms ease, stroke-opacity 150ms ease, fill-opacity 150ms ease;
}
.gcoo-dong-tooltip.leaflet-tooltip {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(15, 23, 42, 0.14);
  border-radius: 8px;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.2);
  color: #111827;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 13px;
  line-height: 1.45;
  padding: 10px 12px;
  pointer-events: none;
  animation: gcooTooltipIn 170ms cubic-bezier(0.2, 0.8, 0.2, 1);
}
.gcoo-dong-tooltip.leaflet-tooltip-right::before {
  border-right-color: rgba(255, 255, 255, 0.94);
}
.gcoo-tooltip-title {
  font-size: 14px;
  font-weight: 800;
  margin-bottom: 6px;
}
.gcoo-tooltip-row {
  display: grid;
  grid-template-columns: 56px minmax(90px, auto);
  gap: 10px;
}
.gcoo-tooltip-key {
  color: #4b5563;
  font-weight: 700;
}
.gcoo-tooltip-value {
  color: #111827;
  font-weight: 500;
}
@keyframes gcooTooltipIn {
  from {
    opacity: 0;
    transform: translate3d(-4px, 6px, 0) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translate3d(0, 0, 0) scale(1);
  }
}
</style>
"""
    m.get_root().header.add_child(Element(css))


def dong_hover_script(layer_name: str) -> str:
    return f"""
(function() {{
  const geojsonLayer = {layer_name};
  let activeTooltip = null;

  function escapeHtml(value) {{
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }}

  function tooltipHtml(properties) {{
    return `
      <div class="gcoo-tooltip-title">${{escapeHtml(properties._gcoo_dong)}}</div>
      <div class="gcoo-tooltip-row">
        <span class="gcoo-tooltip-key">Metric</span>
        <span class="gcoo-tooltip-value">${{escapeHtml(properties._gcoo_metric)}}</span>
        <span class="gcoo-tooltip-key">Value</span>
        <span class="gcoo-tooltip-value">${{escapeHtml(properties._gcoo_value)}}</span>
      </div>
    `;
  }}

  function collectLatLngs(node, out) {{
    if (!Array.isArray(node)) {{
      return;
    }}
    if (node.length && node[0] && typeof node[0].lat === "number") {{
      for (const latlng of node) {{
        out.push(latlng);
      }}
      return;
    }}
    for (const child of node) {{
      collectLatLngs(child, out);
    }}
  }}

  function edgeAnchor(featureLayer) {{
    const latlngs = [];
    collectLatLngs(featureLayer.getLatLngs ? featureLayer.getLatLngs() : [], latlngs);
    if (!latlngs.length || !featureLayer.getBounds) {{
      return featureLayer.getCenter ? featureLayer.getCenter() : null;
    }}

    const bounds = featureLayer.getBounds();
    const center = bounds.getCenter();
    const east = bounds.getEast();
    let best = latlngs[0];
    let bestScore = Number.POSITIVE_INFINITY;

    for (const point of latlngs) {{
      const score = Math.abs(point.lng - east) * 2 + Math.abs(point.lat - center.lat);
      if (score < bestScore) {{
        best = point;
        bestScore = score;
      }}
    }}
    return best;
  }}

  function closeActiveTooltip() {{
    const map = geojsonLayer._map;
    if (activeTooltip && map) {{
      map.removeLayer(activeTooltip);
    }}
    activeTooltip = null;
  }}

  function openAnchoredTooltip(featureLayer, properties) {{
    const map = geojsonLayer._map;
    const anchor = edgeAnchor(featureLayer);
    if (!map || !anchor) {{
      return;
    }}

    closeActiveTooltip();
    activeTooltip = L.tooltip({{
      className: "gcoo-dong-tooltip",
      direction: "right",
      offset: [14, 0],
      opacity: 1,
      permanent: true,
      interactive: false,
    }})
      .setLatLng(anchor)
      .setContent(tooltipHtml(properties))
      .addTo(map);
  }}

  geojsonLayer.eachLayer(function(featureLayer) {{
    const properties = featureLayer.feature && featureLayer.feature.properties;
    if (!properties) {{
      return;
    }}

    featureLayer.on("mouseover", function() {{
      this.setStyle({{
        color: "#0b1726",
        weight: 4.2,
        opacity: 1,
        fillOpacity: 0.58,
      }});
      if (this.bringToFront) {{
        this.bringToFront();
      }}
      openAnchoredTooltip(this, properties);
    }});

    featureLayer.on("mouseout", function() {{
      closeActiveTooltip();
      geojsonLayer.resetStyle(this);
    }});
  }});
}})();
"""


def inject_script_at_html_end(path: Path, script: str) -> None:
    html = path.read_text(encoding="utf-8")
    insertion = f"\n<script>\n{script}\n</script>\n"
    if "</html>" in html:
        html = html.replace("</html>", f"{insertion}</html>", 1)
    else:
        html = f"{html}{insertion}"
    path.write_text(html, encoding="utf-8")


def load_tago_points(pattern: str) -> pd.DataFrame:
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = [read_csv_if_exists(Path(path)) for path in files]
    points = pd.concat(frames, ignore_index=True)
    if {"latitude", "longitude"}.issubset(points.columns):
        return points.dropna(subset=["latitude", "longitude"])
    return pd.DataFrame()


def add_bike_station_layers(m: folium.Map, bike: pd.DataFrame) -> None:
    if bike.empty or not {"latitude", "longitude"}.issubset(bike.columns):
        return

    valid = bike.dropna(subset=["latitude", "longitude"])
    heat = []
    for row in valid.itertuples():
        parking = getattr(row, "parking_bike_count", 1)
        weight = max(float(parking or 1), 1.0)
        heat.append([float(row.latitude), float(row.longitude), weight])
        folium.CircleMarker(
            location=[float(row.latitude), float(row.longitude)],
            radius=min(9, 3 + weight ** 0.5),
            color="#175c90",
            fill=True,
            fill_color="#2b8cbe",
            fill_opacity=0.75,
            popup=folium.Popup(
                html=(
                    f"<b>{getattr(row, 'station_name', 'station')}</b><br>"
                    f"station_id={getattr(row, 'station_id', '')}<br>"
                    f"parking={parking}"
                ),
                max_width=280,
            ),
        ).add_to(m)

    if heat:
        HeatMap(
            heat,
            name="Bike station parking heatmap",
            radius=22,
            blur=18,
            min_opacity=0.25,
        ).add_to(m)


def add_tago_heatmap(m: folium.Map, tago_points: pd.DataFrame) -> None:
    if tago_points.empty:
        return
    heat = []
    for row in tago_points.itertuples():
        battery = getattr(row, "battery_level", None)
        weight = 1.0 if pd.isna(battery) else max(float(battery), 1.0) / 100.0
        heat.append([float(row.latitude), float(row.longitude), weight])
    if heat:
        HeatMap(
            heat,
            name="Raw PM snapshot heatmap",
            radius=20,
            blur=16,
            min_opacity=0.25,
        ).add_to(m)


def add_dong_metric_heatmap(
    m: folium.Map,
    geojson: dict[str, Any],
    metrics: pd.DataFrame,
    metric: str,
) -> None:
    centroids = centroid_by_feature(geojson)
    if not centroids or metric not in metrics.columns:
        return

    heat = []
    for row in metrics.to_dict("records"):
        label = str(row.get("label") or row.get("dong_name") or row.get("dong_id"))
        centroid = centroids.get(label)
        value = float(row.get(metric, 0) or 0)
        if centroid and value > 0:
            heat.append([centroid[0], centroid[1], value])

    if heat:
        HeatMap(
            heat,
            name=f"Dong metric heatmap: {metric}",
            radius=35,
            blur=22,
            min_opacity=0.25,
        ).add_to(m)


def render_map(
    tables: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    geojson_path: Path | None,
    out_path: Path,
    metric: str,
    tago_glob: str,
) -> str:
    geojson = attach_metric_properties(load_geojson(geojson_path), metrics, metric)
    m = folium.Map(location=map_center(tables, geojson), zoom_start=13, tiles=None)
    folium.TileLayer("CartoDB positron", name="CartoDB positron").add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

    dong_layer_name = add_dong_overlay(m, geojson, metric)
    add_dong_metric_heatmap(m, geojson, metrics, metric)
    add_bike_station_layers(m, tables["bike_stations"])
    add_tago_heatmap(m, load_tago_points(tago_glob))

    MiniMap(toggle_display=True).add_to(m)
    Fullscreen(position="topright").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(out_path))
    inject_script_at_html_end(out_path, dong_hover_script(dong_layer_name))
    return "geojson" if geojson_path and geojson_path.exists() else "fixture_bbox"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render interactive GCOO charts and Seoul map visualizations."
    )
    parser.add_argument("--input", default="outputs/model", help="Model/prototype output directory.")
    parser.add_argument("--out", default="outputs/visualizations", help="Visualization output directory.")
    parser.add_argument(
        "--seoul-geojson",
        default="data/raw/seoul_admin_dong.geojson",
        help="Optional Seoul administrative dong GeoJSON boundary file.",
    )
    parser.add_argument(
        "--map-metric",
        default="auto",
        help="Dong metric for map overlay. Use auto, x_star_i, mean_H, mean_competitor_count, etc.",
    )
    parser.add_argument(
        "--tago-glob",
        default="data/raw/tago_pm_snapshots_*.csv",
        help="Raw PM snapshot glob used for point heatmaps when available.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    out_dir = ensure_dir(Path(args.out))
    tables = load_output_tables(input_dir)
    metrics = build_dong_metrics(tables)
    selected_metric = choose_map_metric(metrics, args.map_metric)

    charts_path = out_dir / "charts_dashboard.html"
    map_path = out_dir / "seoul_map.html"
    render_chart_dashboard(tables, metrics, charts_path)
    boundary_source = render_map(
        tables=tables,
        metrics=metrics,
        geojson_path=Path(args.seoul_geojson),
        out_path=map_path,
        metric=selected_metric,
        tago_glob=args.tago_glob,
    )

    manifest = {
        "input_dir": str(input_dir),
        "charts_dashboard": str(charts_path),
        "seoul_map": str(map_path),
        "selected_map_metric": selected_metric,
        "boundary_source": boundary_source,
        "tables": {
            name: {"rows": int(len(df)), "columns": list(df.columns)}
            for name, df in tables.items()
        },
    }
    write_json(out_dir / "visualization_manifest.json", manifest)
    print(f"charts={charts_path}")
    print(f"map={map_path}")
    print(f"map_metric={selected_metric}")
    print(f"boundary_source={boundary_source}")


if __name__ == "__main__":
    main()
