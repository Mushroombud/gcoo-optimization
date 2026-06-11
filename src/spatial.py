from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


def _rings_from_geometry(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geom_type == "Polygon":
        return coordinates
    if geom_type == "MultiPolygon":
        rings: list[list[list[float]]] = []
        for polygon in coordinates:
            rings.extend(polygon)
        return rings
    return []


def _bbox_for_geometry(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    points = []
    for ring in _rings_from_geometry(geometry):
        points.extend(ring)
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    lons = [float(point[0]) for point in points]
    lats = [float(point[1]) for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def load_dong_features(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    features = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        features.append(
            {
                "dong_id": str(props.get("dong_id") or props.get("adm_cd") or props.get("adm_cd2")),
                "dong_name": props.get("dong_name") or props.get("adm_nm"),
                "gu_name": props.get("gu_name") or props.get("sggnm"),
                "geometry": geometry,
                "bbox": _bbox_for_geometry(geometry),
            }
        )
    return features


def dong_master_from_geojson(path: str | Path) -> pd.DataFrame:
    features = load_dong_features(path)
    return pd.DataFrame(
        [
            {
                "dong_id": feature["dong_id"],
                "dong_name": feature["dong_name"],
                "gu_name": feature["gu_name"],
                "area_km2": pd.NA,
            }
            for feature in features
        ]
    )


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    x1, y1 = float(ring[-1][0]), float(ring[-1][1])
    for point in ring:
        x2, y2 = float(point[0]), float(point[1])
        crosses = (y1 > lat) != (y2 > lat)
        if crosses:
            x_at_lat = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1
            if lon < x_at_lat:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    if not polygon or not _point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:])


def geometry_contains_point(geometry: dict[str, Any], lon: float, lat: float) -> bool:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geom_type == "Polygon":
        return _point_in_polygon(lon, lat, coordinates)
    if geom_type == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, polygon) for polygon in coordinates)
    return False


def assign_point_to_dong(
    lat: Any,
    lon: Any,
    features: list[dict[str, Any]],
) -> str | None:
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return None

    for feature in features:
        min_lon, min_lat, max_lon, max_lat = feature["bbox"]
        if not (min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat):
            continue
        if geometry_contains_point(feature["geometry"], longitude, latitude):
            return feature["dong_id"]
    return None


def attach_points_to_dongs(
    df: pd.DataFrame,
    features: list[dict[str, Any]],
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    dong_col: str = "dong_id",
) -> pd.DataFrame:
    mapped = df.copy()
    mapped[dong_col] = [
        assign_point_to_dong(lat, lon, features)
        for lat, lon in zip(mapped[lat_col], mapped[lon_col], strict=False)
    ]
    return mapped


def guess_dong_from_address(address: Any, dong_master: pd.DataFrame) -> str | None:
    if not isinstance(address, str) or not address.strip():
        return None
    text = re.sub(r"\s+", " ", address)
    gu_match = re.search(r"([가-힣]+구)", text)
    gu_name = gu_match.group(1) if gu_match else None
    candidates = dong_master
    if gu_name:
        candidates = candidates[candidates["gu_name"] == gu_name]
    for row in candidates.sort_values("dong_name", key=lambda s: s.str.len(), ascending=False).to_dict("records"):
        dong_name = str(row["dong_name"])
        if dong_name and dong_name in text:
            return str(row["dong_id"])
    return None
