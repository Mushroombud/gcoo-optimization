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
from spatial import (
    attach_points_to_dongs,
    dong_master_from_geojson,
    guess_dong_from_address,
    load_dong_features,
)


def load_config(path: str = "config/model_config.yaml") -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_dong_context(
    config: dict[str, Any],
    allow_fixtures: bool,
) -> tuple[pd.DataFrame, list[dict[str, Any]] | None, Any, list[str]]:
    notes: list[str] = []
    geojson_path = Path(config["model"]["inputs"].get("seoul_admin_dong_geojson", ""))
    if geojson_path.exists():
        notes.append(f"Loaded Seoul administrative dong boundary file: {geojson_path}")
        return dong_master_from_geojson(geojson_path), load_dong_features(geojson_path), None, notes
    if allow_fixtures:
        dongs = make_fixture_dongs()
        notes.append("Missing Seoul dong boundary file; using fixture dong boxes because --allow-fixtures was set.")
        return dong_master_from_boxes(dongs), None, dongs, notes
    notes.append(f"Missing Seoul dong boundary file: {geojson_path}")
    return pd.DataFrame(), None, None, notes


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
    notes.append("No TAGO PM snapshot files found.")
    return pd.DataFrame(), notes


def load_bike_stations(config: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    path = Path(config["model"]["inputs"]["seoul_bike_stations_csv"])
    if not path.exists():
        return pd.DataFrame(), [f"Missing Seoul Bike station file: {path}"]
    return pd.read_csv(path), [f"Loaded Seoul Bike station file: {path}"]


def map_bike_stations(
    bike_stations: pd.DataFrame,
    features: list[dict[str, Any]] | None,
    fixture_dongs: Any,
) -> tuple[pd.DataFrame, list[str]]:
    if bike_stations.empty:
        return bike_stations, []
    if features is not None:
        mapped = attach_points_to_dongs(bike_stations, features)
        return mapped, [f"Mapped {int(mapped['dong_id'].notna().sum())}/{len(mapped)} bike stations to real dong polygons."]
    mapped = attach_dong_id(bike_stations, fixture_dongs)
    return mapped, [f"Mapped {int(mapped['dong_id'].notna().sum())}/{len(mapped)} bike stations to fixture dong boxes."]


def load_bike_trips(
    config: dict[str, Any],
    allow_fixtures: bool,
    bike_stations: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    pattern = config["model"]["inputs"].get("seoul_bike_trips_glob", "data/raw/seoul_bike_trips_*.csv")
    files = sorted(Path().glob(pattern))
    if files:
        frames = [pd.read_csv(path) for path in files]
        trips = pd.concat(frames, ignore_index=True)
        notes.append(f"Loaded {len(files)} Seoul Bike trip file(s), rows={len(trips)}.")
        return trips, notes
    if allow_fixtures and not bike_stations.empty:
        notes.append("No Seoul Bike trip files found; using fixture trips because --allow-fixtures was set.")
        return make_fixture_bike_trips(bike_stations[bike_stations["dong_id"].notna()].head(5)), notes
    notes.append(f"Missing Seoul Bike trip files matching: {pattern}")
    return pd.DataFrame(), notes


def aggregate_tago_scenario_with_real_dongs(
    tago_raw: pd.DataFrame,
    features: list[dict[str, Any]] | None,
    fixture_dongs: Any,
    battery_threshold: int,
) -> pd.DataFrame:
    if tago_raw.empty:
        return pd.DataFrame()
    if features is None:
        return aggregate_tago_scenario(tago_raw, fixture_dongs, battery_threshold)

    mapped = attach_points_to_dongs(tago_raw, features)
    mapped["battery_level"] = pd.to_numeric(mapped["battery_level"], errors="coerce")
    mapped["effective"] = mapped["battery_level"].isna() | (mapped["battery_level"] >= battery_threshold)
    mapped["scenario_day"] = pd.to_datetime(mapped["timestamp"]).dt.date.astype(str)
    mapped["is_gcoo"] = mapped["operator_name"].astype(str).str.contains("GCOO|지쿠|gcoo", case=False, na=False)
    mapped = mapped[mapped["effective"] & mapped["dong_id"].notna()].copy()
    if mapped.empty:
        return pd.DataFrame()
    grouped = (
        mapped.groupby(["scenario_day", "dong_id"])
        .agg(
            gcoo_count_is=("is_gcoo", "sum"),
            total_pm_count_is=("device_id", "nunique"),
        )
        .reset_index()
    )
    grouped["competitor_count_is"] = grouped["total_pm_count_is"] - grouped["gcoo_count_is"]
    return grouped[["scenario_day", "dong_id", "gcoo_count_is", "competitor_count_is", "total_pm_count_is"]]


def load_optional_csv(path_text: str | None) -> pd.DataFrame:
    if not path_text:
        return pd.DataFrame()
    path = Path(path_text)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _minmax(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    max_value = float(numeric.max()) if not numeric.empty else 0.0
    if max_value <= 0:
        return numeric
    return numeric / max_value


def private_pm_gu_prior(private_pm: pd.DataFrame, fallback_total: float, dong_master: pd.DataFrame) -> pd.Series:
    if private_pm.empty or not {"brand", "gu_name", "device_count_total"}.issubset(private_pm.columns):
        gu_counts = dong_master["gu_name"].value_counts().sort_index()
        return gu_counts / gu_counts.sum() * fallback_total

    table = private_pm.copy()
    table["brand_key"] = table["operator_name"].astype(str) + "::" + table["brand"].astype(str)
    table["device_count_total"] = pd.to_numeric(table["device_count_total"], errors="coerce").fillna(0.0)
    service_gu_counts = table.groupby("brand_key")["gu_name"].transform("nunique").replace(0, 1)
    table["gu_allocated_devices"] = table["device_count_total"] / service_gu_counts
    gu_prior = table.groupby("gu_name")["gu_allocated_devices"].sum()
    if gu_prior.sum() <= 0:
        gu_counts = dong_master["gu_name"].value_counts().sort_index()
        return gu_counts / gu_counts.sum() * fallback_total
    return gu_prior


def build_surrogate_pm_scenario(
    dong_master: pd.DataFrame,
    demand: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    cfg = config["model"].get("surrogate_pm", {})
    input_cfg = config["model"]["inputs"]
    tow = load_optional_csv(input_cfg.get("seoul_pm_tow_events_csv"))
    parking = load_optional_csv(input_cfg.get("seoul_kickboard_parking_zones_csv"))
    private_pm = load_optional_csv(input_cfg.get("private_pm_operator_summary_csv"))

    base = dong_master[["dong_id", "dong_name", "gu_name"]].copy()
    demand_score = demand.groupby("dong_id")["H_is"].mean() if not demand.empty else pd.Series(dtype=float)
    base["demand_score"] = base["dong_id"].map(demand_score).fillna(0.0)

    if not tow.empty and "address" in tow.columns:
        tow = tow.copy()
        tow["dong_id"] = tow["address"].map(lambda value: guess_dong_from_address(value, dong_master))
        tow_score = tow.dropna(subset=["dong_id"]).groupby("dong_id").size()
        base["tow_score"] = base["dong_id"].map(tow_score).fillna(0.0)
        notes.append(f"Mapped {int(tow['dong_id'].notna().sum())}/{len(tow)} tow events to dong by address text.")
    else:
        base["tow_score"] = 0.0
        notes.append("No Seoul PM tow-event CSV available for surrogate PM activity.")

    if not parking.empty and "address" in parking.columns:
        parking = parking.copy()
        parking["full_address"] = parking[["gu_name", "address", "detail_location"]].fillna("").agg(" ".join, axis=1)
        parking["dong_id"] = parking["full_address"].map(lambda value: guess_dong_from_address(value, dong_master))
        parking_score = parking.dropna(subset=["dong_id"]).groupby("dong_id").size()
        base["parking_score"] = base["dong_id"].map(parking_score).fillna(0.0)
        notes.append(f"Mapped {int(parking['dong_id'].notna().sum())}/{len(parking)} parking zones to dong by address text.")
    else:
        base["parking_score"] = 0.0
        notes.append("No Seoul kickboard parking-zone CSV available for surrogate PM activity.")

    base["activity_weight"] = (
        float(cfg.get("demand_weight", 1.0)) * _minmax(base["demand_score"])
        + float(cfg.get("tow_weight", 2.0)) * _minmax(base["tow_score"])
        + float(cfg.get("parking_weight", 0.5)) * _minmax(base["parking_score"])
    )
    if base["activity_weight"].sum() <= 0:
        base["activity_weight"] = 1.0

    gcoo_total = float(cfg.get("fallback_gcoo_total_supply", 500))
    base["gcoo_count_is"] = base["activity_weight"] / base["activity_weight"].sum() * gcoo_total

    fallback_competitor_total = float(cfg.get("fallback_competitor_total_supply", 5000))
    gu_prior = private_pm_gu_prior(private_pm, fallback_competitor_total, dong_master)
    base["competitor_count_is"] = 0.0
    for gu_name, gu_total in gu_prior.items():
        gu_mask = base["gu_name"] == gu_name
        gu_weights = base.loc[gu_mask, "activity_weight"]
        if gu_weights.sum() <= 0:
            gu_weights = pd.Series(1.0, index=gu_weights.index)
        base.loc[gu_mask, "competitor_count_is"] = gu_weights / gu_weights.sum() * float(gu_total)

    if base["competitor_count_is"].sum() <= 0:
        base["competitor_count_is"] = base["activity_weight"] / base["activity_weight"].sum() * fallback_competitor_total

    base["total_pm_count_is"] = base["gcoo_count_is"] + base["competitor_count_is"]
    scenario_days = sorted(demand["scenario_day"].dropna().astype(str).unique().tolist()) if not demand.empty else ["surrogate"]
    frames = []
    for scenario_day in scenario_days:
        frame = base[["dong_id", "gcoo_count_is", "competitor_count_is", "total_pm_count_is"]].copy()
        frame.insert(0, "scenario_day", scenario_day)
        frames.append(frame)
    notes.append(
        "Built surrogate PM scenario from Seoul Bike demand, tow events, parking zones, and private PM operator summary."
    )
    notes.append("Surrogate counts are planning priors, not live per-device TAGO observations.")
    return pd.concat(frames, ignore_index=True), notes


def build_readiness(
    bike_stations: pd.DataFrame,
    bike_trips: pd.DataFrame,
    tago_raw: pd.DataFrame,
    notes: list[str],
    demand_rows: int | None = None,
    tago_scenario_rows: int | None = None,
    used_surrogate_pm: bool = False,
) -> dict[str, Any]:
    can_optimize = bool(not bike_stations.empty and not bike_trips.empty)
    if tago_scenario_rows is not None:
        can_optimize = can_optimize and tago_scenario_rows > 0
    return {
        "bike_station_rows": int(len(bike_stations)),
        "bike_trip_rows": int(len(bike_trips)),
        "demand_scenario_rows": demand_rows,
        "tago_pm_raw_rows": int(len(tago_raw)),
        "tago_scenario_rows": tago_scenario_rows,
        "used_surrogate_pm": used_surrogate_pm,
        "can_optimize": can_optimize,
        "notes": notes,
    }


def write_not_ready(out_dir: Path, readiness: dict[str, Any]) -> None:
    write_json(out_dir / "model_readiness.json", readiness)
    lines = [
        "# Model Readiness",
        "",
        f"- Seoul Bike station rows: {readiness['bike_station_rows']}",
        f"- Seoul Bike trip rows: {readiness['bike_trip_rows']}",
        f"- Demand scenario rows: {readiness.get('demand_scenario_rows')}",
        f"- TAGO PM raw rows: {readiness['tago_pm_raw_rows']}",
        f"- PM scenario rows: {readiness.get('tago_scenario_rows')}",
        f"- Used surrogate PM: {str(readiness.get('used_surrogate_pm')).lower()}",
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

    dong_master, features, fixture_dongs, dong_notes = load_dong_context(config, args.allow_fixtures)
    notes.extend(dong_notes)
    bike_stations, bike_notes = load_bike_stations(config)
    notes.extend(bike_notes)
    if dong_master.empty or bike_stations.empty:
        readiness = build_readiness(bike_stations, pd.DataFrame(), pd.DataFrame(), notes)
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    bike_stations, map_notes = map_bike_stations(bike_stations, features, fixture_dongs)
    notes.extend(map_notes)
    bike_trips, trip_notes = load_bike_trips(config, args.allow_fixtures, bike_stations)
    notes.extend(trip_notes)
    if bike_trips.empty:
        readiness = build_readiness(bike_stations, bike_trips, pd.DataFrame(), notes)
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    pm_like = filter_pm_like_trips(bike_trips, bike_stations, config)
    demand_scenario = aggregate_demand(pm_like) if not pm_like.empty else pd.DataFrame()
    if demand_scenario.empty:
        notes.append("No PM-like Seoul Bike trips remained after the configured filter.")
        readiness = build_readiness(
            bike_stations,
            bike_trips,
            pd.DataFrame(),
            notes,
            demand_rows=0,
        )
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    tago_raw, tago_notes = load_tago_snapshots(config, args.allow_fixtures)
    notes.extend(tago_notes)
    tago_scenario = aggregate_tago_scenario_with_real_dongs(
        tago_raw,
        features,
        fixture_dongs,
        int(config["spatial"]["battery_threshold"]),
    )
    used_surrogate_pm = False
    if tago_scenario.empty and config["model"].get("surrogate_pm", {}).get("enabled_when_tago_missing", True):
        tago_scenario, surrogate_notes = build_surrogate_pm_scenario(dong_master, demand_scenario, config)
        notes.extend(surrogate_notes)
        used_surrogate_pm = True

    if tago_scenario.empty:
        notes.append("No real or surrogate PM scenario could be built.")
        readiness = build_readiness(
            bike_stations,
            bike_trips,
            tago_raw,
            notes,
            demand_rows=len(demand_scenario),
            tago_scenario_rows=0,
            used_surrogate_pm=used_surrogate_pm,
        )
        write_not_ready(out_dir, readiness)
        print(f"model not ready; wrote {out_dir / 'model_readiness.json'}")
        return 0

    model_inputs = build_model_inputs(dong_master, demand_scenario, tago_scenario, config)
    allocation = optimize_allocation(model_inputs, demand_scenario, tago_scenario, config, pm_like)

    dong_master.to_csv(out_dir / "dong_master.csv", index=False)
    bike_stations.to_csv(out_dir / "bike_stations_with_dong.csv", index=False)
    pm_like.to_csv(out_dir / "bike_trip_pm_like.csv", index=False)
    demand_scenario.to_csv(out_dir / "demand_scenario.csv", index=False)
    tago_scenario.to_csv(out_dir / "tago_scenario.csv", index=False)
    model_inputs.to_csv(out_dir / "model_inputs.csv", index=False)
    allocation.to_csv(out_dir / "allocation_optimized.csv", index=False)

    readiness = {
        **build_readiness(
            bike_stations,
            bike_trips,
            tago_raw,
            notes,
            demand_rows=int(len(demand_scenario)),
            tago_scenario_rows=int(len(tago_scenario)),
            used_surrogate_pm=used_surrogate_pm,
        ),
        "allocated_scooters": int(allocation["x_star_i"].sum()),
        "active_dongs": int((allocation["x_star_i"] > 0).sum()),
        "expected_rebalancing_km": float(allocation["expected_rebalancing_km"].max()),
        "expected_rebalancing_cost": float(allocation["expected_rebalancing_cost"].max()),
        "expected_profit_after_rebalancing": float(allocation["expected_profit_after_rebalancing"].max()),
    }
    write_json(out_dir / "model_readiness.json", readiness)
    lines = [
        "# Model Readiness",
        "",
        f"- Can optimize: {str(readiness['can_optimize']).lower()}",
        f"- Used surrogate PM: {str(used_surrogate_pm).lower()}",
        f"- PM-like trip rows: {len(pm_like)}",
        f"- Demand scenario rows: {len(demand_scenario)}",
        f"- PM scenario rows: {len(tago_scenario)}",
        f"- Allocated scooters: {readiness['allocated_scooters']}",
        f"- Active dongs: {readiness['active_dongs']}",
        f"- Expected rebalancing km: {readiness['expected_rebalancing_km']:.2f}",
        f"- Expected rebalancing cost: {readiness['expected_rebalancing_cost']:.0f}",
        f"- Expected profit after rebalancing: {readiness['expected_profit_after_rebalancing']:.0f}",
        "",
        "Notes:",
        *[f"- {note}" for note in notes],
    ]
    (out_dir / "model_readiness.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"model outputs={out_dir}")
    print(f"demand_scenario_rows={len(demand_scenario)}")
    print(f"pm_scenario_rows={len(tago_scenario)}")
    print(f"used_surrogate_pm={used_surrogate_pm}")
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
