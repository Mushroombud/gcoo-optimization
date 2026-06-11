from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import data_input
import visualize_sejong_tago
from common import append_jsonl, ensure_dir, now_kst_iso, now_kst_label, write_json


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def safe_label(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_+-]", "_", value)


def configure_sejong_tago(config: dict[str, Any], city_name: str) -> dict[str, Any]:
    tago_cfg = config["data_input"]["apis"]["tago_pm"]
    tago_cfg["target_city_name"] = city_name
    tago_cfg["provider_lookup_city_name"] = ""
    tago_cfg["provider_name"] = ""
    tago_cfg["response_type"] = "json"
    tago_cfg["base_url"] = str(tago_cfg["base_url"]).replace("http://", "https://")
    return config


def load_snapshot_files(pattern: str) -> tuple[pd.DataFrame, list[Path]]:
    files = sorted(Path().glob(pattern))
    frames = []
    for path in files:
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        frame["source_file"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame(), files
    return pd.concat(frames, ignore_index=True), files


def haversine_m(lat1: pd.Series, lon1: pd.Series, lat2: pd.Series, lon2: pd.Series) -> pd.Series:
    radius_m = 6_371_000.0
    lat1_rad = np.radians(pd.to_numeric(lat1, errors="coerce"))
    lon1_rad = np.radians(pd.to_numeric(lon1, errors="coerce"))
    lat2_rad = np.radians(pd.to_numeric(lat2, errors="coerce"))
    lon2_rad = np.radians(pd.to_numeric(lon2, errors="coerce"))
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    return pd.Series(2 * radius_m * np.arcsin(np.sqrt(a)), index=lat1.index)


def add_grid_zone(df: pd.DataFrame, zone_size_m: int) -> tuple[pd.DataFrame, dict[str, float]]:
    if df.empty:
        return df, {"lat_step": 0.0, "lon_step": 0.0, "reference_latitude": 0.0}
    result = df.copy()
    ref_lat = float(result["latitude"].median())
    lat_step = zone_size_m / 111_320.0
    lon_step = zone_size_m / (111_320.0 * max(math.cos(math.radians(ref_lat)), 0.01))
    result["zone_lat_index"] = np.floor(result["latitude"] / lat_step).astype("int64")
    result["zone_lon_index"] = np.floor(result["longitude"] / lon_step).astype("int64")
    result["zone_id"] = (
        "grid_"
        + result["zone_lat_index"].astype(str)
        + "_"
        + result["zone_lon_index"].astype(str)
    )
    result["zone_center_latitude"] = (result["zone_lat_index"] + 0.5) * lat_step
    result["zone_center_longitude"] = (result["zone_lon_index"] + 0.5) * lon_step
    return result, {
        "lat_step": lat_step,
        "lon_step": lon_step,
        "reference_latitude": ref_lat,
    }


def normalize_snapshots(raw: pd.DataFrame, battery_threshold: int, zone_size_m: int) -> tuple[pd.DataFrame, dict[str, float]]:
    if raw.empty:
        return raw, {"lat_step": 0.0, "lon_step": 0.0, "reference_latitude": 0.0}

    df = raw.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["operator_name"] = df["operator_name"].astype(str)
    df["device_id"] = df["device_id"].astype(str)
    for column in ["battery_level", "latitude", "longitude"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["timestamp", "operator_name", "device_id", "latitude", "longitude"])
    df = df.drop_duplicates(["timestamp", "operator_name", "device_id"], keep="last")
    df["battery_effective"] = df["battery_level"].isna() | (df["battery_level"] >= battery_threshold)
    df["snapshot_date"] = df["timestamp"].dt.date.astype(str)
    df["snapshot_hour"] = df["timestamp"].dt.hour
    df, grid_meta = add_grid_zone(df, zone_size_m)
    return df.sort_values(["timestamp", "operator_name", "device_id"]).reset_index(drop=True), grid_meta


def build_operator_counts(df: pd.DataFrame, battery_threshold: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    grouped = (
        df.groupby(["timestamp", "operator_name"], dropna=False)
        .agg(
            device_count=("device_id", "nunique"),
            effective_device_count=("battery_effective", "sum"),
            avg_battery=("battery_level", "mean"),
            p10_battery=("battery_level", lambda series: float(np.nanpercentile(series, 10))),
            median_battery=("battery_level", "median"),
            low_battery_count=("battery_level", lambda series: int((series < battery_threshold).sum())),
            zone_count=("zone_id", "nunique"),
        )
        .reset_index()
    )
    grouped["timestamp"] = grouped["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return grouped


def build_zone_counts(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    grouped = (
        df.groupby(["timestamp", "zone_id", "operator_name"], dropna=False)
        .agg(
            device_count=("device_id", "nunique"),
            effective_device_count=("battery_effective", "sum"),
            avg_battery=("battery_level", "mean"),
            zone_center_latitude=("zone_center_latitude", "first"),
            zone_center_longitude=("zone_center_longitude", "first"),
        )
        .reset_index()
    )
    grouped["timestamp"] = grouped["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return grouped


def build_device_intervals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    sorted_df = df.sort_values(["operator_name", "device_id", "timestamp"]).copy()
    grouped = sorted_df.groupby(["operator_name", "device_id"], dropna=False)
    sorted_df["prev_timestamp"] = grouped["timestamp"].shift(1)
    sorted_df["prev_latitude"] = grouped["latitude"].shift(1)
    sorted_df["prev_longitude"] = grouped["longitude"].shift(1)
    sorted_df["prev_battery_level"] = grouped["battery_level"].shift(1)
    sorted_df["prev_zone_id"] = grouped["zone_id"].shift(1)
    intervals = sorted_df.dropna(subset=["prev_timestamp", "prev_latitude", "prev_longitude"]).copy()
    if intervals.empty:
        return intervals

    intervals["interval_minutes"] = (
        intervals["timestamp"] - intervals["prev_timestamp"]
    ).dt.total_seconds() / 60.0
    intervals["distance_m"] = haversine_m(
        intervals["prev_latitude"],
        intervals["prev_longitude"],
        intervals["latitude"],
        intervals["longitude"],
    )
    intervals["speed_kmph"] = intervals["distance_m"] / 1000.0 / (intervals["interval_minutes"] / 60.0)
    intervals.loc[~np.isfinite(intervals["speed_kmph"]), "speed_kmph"] = np.nan
    intervals["battery_delta"] = intervals["battery_level"] - intervals["prev_battery_level"]
    intervals["moved_50m"] = intervals["distance_m"] >= 50
    intervals["moved_200m"] = intervals["distance_m"] >= 200
    intervals["same_zone"] = intervals["zone_id"] == intervals["prev_zone_id"]
    output_columns = [
        "timestamp",
        "prev_timestamp",
        "operator_name",
        "device_id",
        "prev_zone_id",
        "zone_id",
        "prev_latitude",
        "prev_longitude",
        "latitude",
        "longitude",
        "interval_minutes",
        "distance_m",
        "speed_kmph",
        "prev_battery_level",
        "battery_level",
        "battery_delta",
        "moved_50m",
        "moved_200m",
        "same_zone",
    ]
    intervals = intervals[output_columns]
    for column in ["timestamp", "prev_timestamp"]:
        intervals[column] = intervals[column].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return intervals.reset_index(drop=True)


def build_activity_by_zone(intervals: pd.DataFrame) -> pd.DataFrame:
    if intervals.empty:
        return pd.DataFrame()
    activity = intervals.copy()
    activity["origin_zone_id"] = activity["prev_zone_id"].fillna(activity["zone_id"])
    grouped = (
        activity.groupby(["timestamp", "origin_zone_id", "operator_name"], dropna=False)
        .agg(
            interval_count=("device_id", "count"),
            moved_50m_count=("moved_50m", "sum"),
            moved_200m_count=("moved_200m", "sum"),
            avg_distance_m=("distance_m", "mean"),
            median_distance_m=("distance_m", "median"),
            avg_speed_kmph=("speed_kmph", "mean"),
            avg_battery_delta=("battery_delta", "mean"),
        )
        .reset_index()
        .rename(columns={"origin_zone_id": "zone_id"})
    )
    return grouped


def write_processed_outputs(
    df: pd.DataFrame,
    files: list[Path],
    processed_dir: Path,
    battery_threshold: int,
    zone_size_m: int,
    grid_meta: dict[str, float],
) -> dict[str, Any]:
    ensure_dir(processed_dir)
    if df.empty:
        summary = {
            "ok": False,
            "created_at": now_kst_iso(),
            "raw_file_count": len(files),
            "notes": ["No Sejong TAGO snapshot CSV rows were available for preprocessing."],
        }
        write_json(processed_dir / "sejong_pm_preprocess_summary.json", summary)
        return summary

    latest_timestamp = df["timestamp"].max()
    latest = df[df["timestamp"] == latest_timestamp].copy()
    operator_counts = build_operator_counts(df, battery_threshold)
    zone_counts = build_zone_counts(df)
    intervals = build_device_intervals(df)
    activity_by_zone = build_activity_by_zone(intervals)

    accumulated = df.copy()
    accumulated["timestamp"] = accumulated["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    accumulated.to_csv(processed_dir / "sejong_pm_snapshots_accumulated.csv", index=False)

    latest["timestamp"] = latest["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    latest.to_csv(processed_dir / "sejong_pm_latest_snapshot.csv", index=False)
    operator_counts.to_csv(processed_dir / "sejong_pm_operator_snapshot_counts.csv", index=False)
    zone_counts.to_csv(processed_dir / "sejong_pm_zone_snapshot_counts.csv", index=False)
    intervals.to_csv(processed_dir / "sejong_pm_device_intervals.csv", index=False)
    activity_by_zone.to_csv(processed_dir / "sejong_pm_activity_by_zone.csv", index=False)

    summary = {
        "ok": True,
        "created_at": now_kst_iso(),
        "raw_file_count": len(files),
        "snapshot_count": int(df["timestamp"].nunique()),
        "row_count": int(len(df)),
        "latest_timestamp": latest_timestamp.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "latest_device_count": int(latest["device_id"].nunique()),
        "latest_effective_device_count": int(latest["battery_effective"].sum()),
        "operator_counts_latest": {
            str(operator): int(count)
            for operator, count in latest.groupby("operator_name")["device_id"].nunique().items()
        },
        "device_interval_count": int(len(intervals)),
        "moved_50m_interval_count": int(intervals["moved_50m"].sum()) if not intervals.empty else 0,
        "moved_200m_interval_count": int(intervals["moved_200m"].sum()) if not intervals.empty else 0,
        "zone_size_m": int(zone_size_m),
        "grid": grid_meta,
        "outputs": {
            "accumulated": str(processed_dir / "sejong_pm_snapshots_accumulated.csv"),
            "latest": str(processed_dir / "sejong_pm_latest_snapshot.csv"),
            "operator_counts": str(processed_dir / "sejong_pm_operator_snapshot_counts.csv"),
            "zone_counts": str(processed_dir / "sejong_pm_zone_snapshot_counts.csv"),
            "device_intervals": str(processed_dir / "sejong_pm_device_intervals.csv"),
            "activity_by_zone": str(processed_dir / "sejong_pm_activity_by_zone.csv"),
        },
    }
    write_json(processed_dir / "sejong_pm_preprocess_summary.json", summary)
    return summary


def acquire_lock(lock_path: Path):
    ensure_dir(lock_path.parent)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"another collector run is active; lock={lock_path}")
        raise SystemExit(0)
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def run(args: argparse.Namespace) -> int:
    os.chdir(repo_root())
    lock_file = acquire_lock(Path(args.lock_file))
    _ = lock_file

    data_input.load_dotenv(args.env)
    config = configure_sejong_tago(data_input.load_config(args.config), args.city_name)
    battery_threshold = int(config["spatial"].get("battery_threshold", 20))
    label = safe_label(args.snapshot_label or f"sejong_{now_kst_label()}")

    fetched_rows = None
    if not args.skip_fetch:
        snapshot = data_input.fetch_tago_pm_snapshot(config, label)
        fetched_rows = int(len(snapshot))
        if snapshot.empty:
            print(f"tago fetch returned no rows for city={args.city_name} label={label}")

    raw_pattern = config["model"]["inputs"]["tago_pm_snapshots_glob"]
    sejong_pattern = raw_pattern.replace("*", "sejong*")
    raw, files = load_snapshot_files(sejong_pattern)
    normalized, grid_meta = normalize_snapshots(raw, battery_threshold, args.zone_size_m)
    summary = write_processed_outputs(
        normalized,
        files,
        Path(args.processed_dir),
        battery_threshold,
        args.zone_size_m,
        grid_meta,
    )
    summary.update(
        {
            "collector_label": label,
            "city_name": args.city_name,
            "fetched_rows": fetched_rows,
            "skip_fetch": bool(args.skip_fetch),
        }
    )
    if not args.skip_visualization:
        visualization = visualize_sejong_tago.render(
            Path(args.processed_dir),
            Path(args.visualization_dir),
            args.max_visualization_markers,
        )
        summary["visualization"] = {
            "charts_dashboard": visualization["charts_dashboard"],
            "map": visualization["map"],
            "manifest": str(Path(args.visualization_dir) / "sejong_visualization_manifest.json"),
        }
    append_jsonl(Path(args.processed_dir) / "collector_runs.jsonl", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Sejong TAGO PM snapshots and rebuild preprocessed rolling datasets."
    )
    parser.add_argument("--config", default="config/model_config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--city-name", default="세종")
    parser.add_argument("--snapshot-label")
    parser.add_argument("--processed-dir", default="data/processed/sejong_tago")
    parser.add_argument("--visualization-dir", default="outputs/visualizations")
    parser.add_argument("--max-visualization-markers", type=int, default=1500)
    parser.add_argument("--zone-size-m", type=int, default=500)
    parser.add_argument("--lock-file", default="data/raw/sejong_tago_collect.lock")
    parser.add_argument("--skip-fetch", action="store_true", help="Only rebuild processed outputs from existing raw CSVs.")
    parser.add_argument("--skip-visualization", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
