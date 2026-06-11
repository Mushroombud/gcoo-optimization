from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from common import ensure_dir, write_json
from prototype_pipeline import (
    aggregate_demand,
    aggregate_tago_scenario,
    attach_dong_id,
    build_model_inputs,
    dong_master_from_boxes,
    filter_pm_like_trips,
    make_fixture_bike_trips,
    make_fixture_dongs,
    make_fixture_tago_pm,
    optimize_allocation,
)


def load_config(path: str = "config/model_config.yaml") -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_tago_snapshots(config: dict[str, Any], allow_fixtures: bool) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    pattern = config["model"]["inputs"]["tago_pm_snapshots_glob"]
    files = sorted(Path().glob(pattern))
    if files:
        frames = [pd.read_csv(path) for path in files]
        raw = pd.concat(frames, ignore_index=True)
        tago_cfg = config.get("data_input", {}).get("apis", {}).get("tago_pm", {})
        city_filter = tago_cfg.get("target_city_name") or tago_cfg.get("city_name")
        if city_filter and "city_name" in raw.columns:
            before = len(raw)
            raw = raw[
                raw["city_name"].astype(str).map(
                    lambda actual: str(city_filter) in actual or actual in str(city_filter)
                )
            ].copy()
            notes.append(f"Filtered TAGO PM rows by city_name={city_filter}: {before} -> {len(raw)}.")
        notes.append(f"Loaded {len(files)} TAGO PM snapshot file(s).")
        return raw, notes
    if allow_fixtures:
        notes.append("No real TAGO PM snapshot files found; using fixture data because --allow-fixtures was set.")
        return make_fixture_tago_pm(), notes
    notes.append("No TAGO PM snapshot files found. Run data input with TAGO_PM_API_URL configured.")
    return pd.DataFrame(), notes


def load_bike_stations(config: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    path = Path(config["model"]["inputs"]["seoul_bike_stations_csv"])
    if not path.exists():
        return pd.DataFrame(), [f"Missing Seoul Bike station file: {path}"]
    return pd.read_csv(path), [f"Loaded Seoul Bike station file: {path}"]


def build_readiness(
    bike_stations: pd.DataFrame,
    tago_raw: pd.DataFrame,
    notes: list[str],
    tago_scenario_rows: int | None = None,
) -> dict[str, Any]:
    can_optimize = bool(not bike_stations.empty and not tago_raw.empty)
    if tago_scenario_rows is not None:
        can_optimize = can_optimize and tago_scenario_rows > 0
    return {
        "bike_station_rows": int(len(bike_stations)),
        "tago_pm_raw_rows": int(len(tago_raw)),
        "tago_scenario_rows": tago_scenario_rows,
        "can_optimize": can_optimize,
        "notes": notes,
    }


def write_not_ready(out_dir: Path, readiness: dict[str, Any]) -> None:
    write_json(out_dir / "model_readiness.json", readiness)
    lines = [
        "# Model Readiness",
        "",
        f"- Seoul Bike station rows: {readiness['bike_station_rows']}",
        f"- TAGO PM raw rows: {readiness['tago_pm_raw_rows']}",
        f"- TAGO scenario rows: {readiness.get('tago_scenario_rows')}",
        "- Can optimize: false",
        "",
        "Notes:",
        *[f"- {note}" for note in readiness["notes"]],
    ]
    (out_dir / "model_readiness.md").write_text("\n".join(lines), encoding="utf-8")


def run_model(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    out_dir = ensure_dir(Path(args.out))
    notes: list[str] = []

    bike_stations, bike_notes = load_bike_stations(config)
    notes.extend(bike_notes)
    tago_raw, tago_notes = load_tago_snapshots(config, args.allow_fixtures)
    notes.extend(tago_notes)

    readiness = build_readiness(bike_stations, tago_raw, notes)

    if not readiness["can_optimize"]:
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    dongs = make_fixture_dongs()
    dong_master = dong_master_from_boxes(dongs)
    bike_stations = attach_dong_id(bike_stations, dongs)
    mapped_station_count = int(bike_stations["dong_id"].notna().sum())
    notes.append(
        f"Mapped {mapped_station_count}/{len(bike_stations)} bike stations to the current fixture dong boxes."
    )

    tago_scenario = aggregate_tago_scenario(
        tago_raw,
        dongs,
        int(config["spatial"]["battery_threshold"]),
    )
    if tago_scenario.empty:
        notes.append(
            "TAGO PM raw snapshots exist, but no effective PM devices mapped to the current Seoul dong geometry."
        )
        readiness = build_readiness(
            bike_stations,
            tago_raw,
            notes,
            tago_scenario_rows=0,
        )
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    bike_trips = make_fixture_bike_trips(bike_stations[bike_stations["dong_id"].notna()].head(5))
    pm_like = filter_pm_like_trips(bike_trips, bike_stations, config)
    demand_scenario = aggregate_demand(pm_like)
    model_inputs = build_model_inputs(
        dong_master,
        demand_scenario,
        tago_scenario,
        config,
    )
    allocation = optimize_allocation(
        model_inputs,
        demand_scenario,
        tago_scenario,
        config,
    )

    dong_master.to_csv(out_dir / "dong_master.csv", index=False)
    bike_stations.to_csv(out_dir / "bike_stations_with_dong.csv", index=False)
    pm_like.to_csv(out_dir / "bike_trip_pm_like.csv", index=False)
    demand_scenario.to_csv(out_dir / "demand_scenario.csv", index=False)
    tago_scenario.to_csv(out_dir / "tago_scenario.csv", index=False)
    model_inputs.to_csv(out_dir / "model_inputs.csv", index=False)
    allocation.to_csv(out_dir / "allocation_optimized.csv", index=False)

    write_json(
        out_dir / "model_readiness.json",
        {
            **build_readiness(
                bike_stations,
                tago_raw,
                notes,
                tago_scenario_rows=int(len(tago_scenario)),
            ),
            "tago_scenario_rows": int(len(tago_scenario)),
            "allocated_scooters": int(allocation["x_star_i"].sum()),
        },
    )
    print(f"model outputs={out_dir}")
    print(f"tago_scenario_rows={len(tago_scenario)}")
    print(f"allocated_scooters={int(allocation['x_star_i'].sum())}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build model inputs and allocation from accumulated raw data.")
    parser.add_argument("--config", default="config/model_config.yaml")
    parser.add_argument("--out", default="outputs/model")
    parser.add_argument("--allow-fixtures", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_model(args))


if __name__ == "__main__":
    main()
