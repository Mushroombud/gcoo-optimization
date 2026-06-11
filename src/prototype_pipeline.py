from __future__ import annotations

import argparse
import glob
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True)
class DongBox:
    dong_id: str
    dong_name: str
    gu_name: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


EMBEDDED_SEOUL_BIKE_SAMPLE = {
    "rentBikeStatus": {
        "row": [
            {
                "rackTotCnt": "15",
                "stationName": "102. 망원역 1번출구 앞",
                "parkingBikeTotCnt": "16",
                "shared": "107",
                "stationLatitude": "37.55564880",
                "stationLongitude": "126.91062927",
                "stationId": "ST-4",
            },
            {
                "rackTotCnt": "14",
                "stationName": "103. 망원역 2번출구 앞",
                "parkingBikeTotCnt": "13",
                "shared": "93",
                "stationLatitude": "37.55495071",
                "stationLongitude": "126.91083527",
                "stationId": "ST-5",
            },
            {
                "rackTotCnt": "13",
                "stationName": "104. 합정역 1번출구 앞",
                "parkingBikeTotCnt": "0",
                "shared": "0",
                "stationLatitude": "37.55073929",
                "stationLongitude": "126.91508484",
                "stationId": "ST-6",
            },
            {
                "rackTotCnt": "5",
                "stationName": "105. 합정역 5번출구 앞",
                "parkingBikeTotCnt": "1",
                "shared": "20",
                "stationLatitude": "37.55000687",
                "stationLongitude": "126.91482544",
                "stationId": "ST-7",
            },
            {
                "rackTotCnt": "12",
                "stationName": "106. 합정역 7번출구 앞",
                "parkingBikeTotCnt": "3",
                "shared": "25",
                "stationLatitude": "37.54864502",
                "stationLongitude": "126.91282654",
                "stationId": "ST-8",
            },
        ]
    }
}


def load_config(path: str = "config/model_config.yaml") -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_seoul_bike_probe(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return EMBEDDED_SEOUL_BIKE_SAMPLE


def normalize_bike_stations(payload: dict[str, Any]) -> pd.DataFrame:
    rows = payload.get("rentBikeStatus", {}).get("row", [])
    records = []
    for row in rows:
        records.append(
            {
                "station_id": row.get("stationId"),
                "station_name": row.get("stationName"),
                "latitude": float(row.get("stationLatitude")),
                "longitude": float(row.get("stationLongitude")),
                "parking_bike_count": int(row.get("parkingBikeTotCnt", 0)),
                "rack_count": int(row.get("rackTotCnt", 0)),
            }
        )
    return pd.DataFrame.from_records(records)


def make_fixture_dongs() -> list[DongBox]:
    return [
        DongBox("11140101", "망원1동", "마포구", 126.9050, 37.5520, 126.9135, 37.5595),
        DongBox("11140102", "합정동", "마포구", 126.9110, 37.5460, 126.9180, 37.5530),
        DongBox("11140103", "서교동", "마포구", 126.9180, 37.5480, 126.9285, 37.5580),
    ]


def dong_master_from_boxes(dongs: list[DongBox]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dong_id": dong.dong_id,
                "dong_name": dong.dong_name,
                "gu_name": dong.gu_name,
                "area_km2": np.nan,
            }
            for dong in dongs
        ]
    )


def assign_bbox_dong(latitude: float, longitude: float, dongs: list[DongBox]) -> str | None:
    for dong in dongs:
        if (
            dong.min_lat <= latitude <= dong.max_lat
            and dong.min_lon <= longitude <= dong.max_lon
        ):
            return dong.dong_id
    return None


def attach_dong_id(
    df: pd.DataFrame,
    dongs: list[DongBox],
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> pd.DataFrame:
    mapped = df.copy()
    mapped["dong_id"] = [
        assign_bbox_dong(lat, lon, dongs)
        for lat, lon in zip(mapped[lat_col], mapped[lon_col], strict=False)
    ]
    return mapped


def make_fixture_tago_pm() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": "2026-06-01T04:00:00+09:00",
                "operator_name": "GCOO",
                "device_id": "gcoo-001",
                "battery_level": 82,
                "latitude": 37.5558,
                "longitude": 126.9107,
            },
            {
                "timestamp": "2026-06-01T04:00:00+09:00",
                "operator_name": "GCOO",
                "device_id": "gcoo-002",
                "battery_level": 47,
                "latitude": 37.5502,
                "longitude": 126.9149,
            },
            {
                "timestamp": "2026-06-01T04:00:00+09:00",
                "operator_name": "CompetitorA",
                "device_id": "comp-a-001",
                "battery_level": 66,
                "latitude": 37.5549,
                "longitude": 126.9106,
            },
            {
                "timestamp": "2026-06-01T04:00:00+09:00",
                "operator_name": "CompetitorB",
                "device_id": "comp-b-001",
                "battery_level": 15,
                "latitude": 37.5501,
                "longitude": 126.9148,
            },
            {
                "timestamp": "2026-06-02T04:00:00+09:00",
                "operator_name": "GCOO",
                "device_id": "gcoo-003",
                "battery_level": 74,
                "latitude": 37.5557,
                "longitude": 126.9109,
            },
            {
                "timestamp": "2026-06-02T04:00:00+09:00",
                "operator_name": "CompetitorA",
                "device_id": "comp-a-002",
                "battery_level": None,
                "latitude": 37.5500,
                "longitude": 126.9147,
            },
            {
                "timestamp": "2026-06-02T04:00:00+09:00",
                "operator_name": "CompetitorA",
                "device_id": "comp-a-003",
                "battery_level": 55,
                "latitude": 37.5487,
                "longitude": 126.9128,
            },
        ]
    )


def load_tago_pm_raw() -> pd.DataFrame:
    files = sorted(glob.glob("data/raw/tago_pm_snapshots_*.csv"))
    if not files:
        return make_fixture_tago_pm()
    return pd.concat((pd.read_csv(file) for file in files), ignore_index=True)


def is_gcoo_operator(name: str) -> bool:
    normalized = str(name).lower()
    return "gcoo" in normalized or "지쿠" in normalized


def aggregate_tago_scenario(
    tago_df: pd.DataFrame,
    dongs: list[DongBox],
    battery_threshold: int,
) -> pd.DataFrame:
    mapped = attach_dong_id(tago_df, dongs)
    mapped["battery_level"] = pd.to_numeric(mapped["battery_level"], errors="coerce")
    mapped["effective"] = mapped["battery_level"].isna() | (
        mapped["battery_level"] >= battery_threshold
    )
    mapped["scenario_day"] = pd.to_datetime(mapped["timestamp"]).dt.date.astype(str)
    mapped["is_gcoo"] = mapped["operator_name"].map(is_gcoo_operator)
    mapped = mapped[mapped["effective"] & mapped["dong_id"].notna()].copy()

    grouped = (
        mapped.groupby(["scenario_day", "dong_id"])
        .agg(
            gcoo_count_is=("is_gcoo", "sum"),
            total_pm_count_is=("device_id", "nunique"),
        )
        .reset_index()
    )
    grouped["competitor_count_is"] = (
        grouped["total_pm_count_is"] - grouped["gcoo_count_is"]
    )
    return grouped[
        [
            "scenario_day",
            "dong_id",
            "gcoo_count_is",
            "competitor_count_is",
            "total_pm_count_is",
        ]
    ]


def make_fixture_bike_trips(stations: pd.DataFrame) -> pd.DataFrame:
    station_ids = stations["station_id"].tolist()
    if len(station_ids) < 4:
        raise ValueError("Need at least four station rows to build the fixture trips.")
    return pd.DataFrame(
        [
            {
                "rental_datetime": "2026-06-01 08:15:00",
                "return_datetime": "2026-06-01 08:25:00",
                "rental_station_id": station_ids[0],
                "return_station_id": station_ids[2],
                "distance_m": 1400,
                "duration_min": 10,
            },
            {
                "rental_datetime": "2026-06-01 18:20:00",
                "return_datetime": "2026-06-01 18:29:00",
                "rental_station_id": station_ids[1],
                "return_station_id": station_ids[3],
                "distance_m": 1100,
                "duration_min": 9,
            },
            {
                "rental_datetime": "2026-06-02 01:10:00",
                "return_datetime": "2026-06-02 01:19:00",
                "rental_station_id": station_ids[2],
                "return_station_id": station_ids[0],
                "distance_m": 1300,
                "duration_min": 9,
            },
            {
                "rental_datetime": "2026-06-02 09:00:00",
                "return_datetime": "2026-06-02 09:12:00",
                "rental_station_id": station_ids[3],
                "return_station_id": station_ids[0],
                "distance_m": 1800,
                "duration_min": 12,
            },
            {
                "rental_datetime": "2026-06-02 11:00:00",
                "return_datetime": "2026-06-02 11:45:00",
                "rental_station_id": station_ids[0],
                "return_station_id": station_ids[1],
                "distance_m": 5500,
                "duration_min": 45,
            },
        ]
    )


def make_operating_day(timestamp: pd.Timestamp) -> str:
    if timestamp.hour < 4:
        return (timestamp.date() - pd.Timedelta(days=1)).isoformat()
    return timestamp.date().isoformat()


def filter_pm_like_trips(
    trips: pd.DataFrame,
    station_dongs: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    demand_cfg = config["demand"]
    station_to_dong = station_dongs.set_index("station_id")["dong_id"].to_dict()

    prepared = trips.copy()
    prepared["rental_datetime"] = pd.to_datetime(prepared["rental_datetime"])
    prepared["operating_day"] = prepared["rental_datetime"].map(make_operating_day)
    prepared["origin_dong_id"] = prepared["rental_station_id"].map(station_to_dong)
    prepared["destination_dong_id"] = prepared["return_station_id"].map(station_to_dong)
    prepared["distance_km"] = prepared["distance_m"] / 1000.0
    prepared["duration_min"] = pd.to_numeric(prepared["duration_min"], errors="coerce")
    prepared["speed_kmph"] = prepared["distance_km"] / (prepared["duration_min"] / 60.0)

    mask = (
        prepared["origin_dong_id"].notna()
        & prepared["destination_dong_id"].notna()
        & (prepared["origin_dong_id"] != prepared["destination_dong_id"])
        & prepared["distance_km"].between(
            demand_cfg["min_distance_km"], demand_cfg["max_distance_km"]
        )
        & prepared["duration_min"].between(
            demand_cfg["min_duration_min"], demand_cfg["max_duration_min"]
        )
        & prepared["speed_kmph"].between(
            demand_cfg["min_speed_kmph"], demand_cfg["max_speed_kmph"]
        )
    )
    return prepared.loc[
        mask,
        [
            "operating_day",
            "origin_dong_id",
            "destination_dong_id",
            "distance_km",
            "duration_min",
            "speed_kmph",
        ],
    ].copy()


def aggregate_demand(pm_like: pd.DataFrame) -> pd.DataFrame:
    departures = (
        pm_like.groupby(["operating_day", "origin_dong_id"])
        .agg(
            H_is=("origin_dong_id", "size"),
            departures_is=("origin_dong_id", "size"),
            avg_distance_km_i=("distance_km", "mean"),
        )
        .reset_index()
        .rename(columns={"operating_day": "scenario_day", "origin_dong_id": "dong_id"})
    )
    arrivals = (
        pm_like.groupby(["operating_day", "destination_dong_id"])
        .agg(arrivals_is=("destination_dong_id", "size"))
        .reset_index()
        .rename(
            columns={
                "operating_day": "scenario_day",
                "destination_dong_id": "dong_id",
            }
        )
    )
    demand = departures.merge(arrivals, on=["scenario_day", "dong_id"], how="outer")
    demand[["H_is", "departures_is", "arrivals_is"]] = demand[
        ["H_is", "departures_is", "arrivals_is"]
    ].fillna(0)
    city_avg_distance = pm_like["distance_km"].mean()
    demand["avg_distance_km_i"] = demand["avg_distance_km_i"].fillna(city_avg_distance)
    return demand.sort_values(["scenario_day", "dong_id"])


def build_model_inputs(
    dong_master: pd.DataFrame,
    demand: pd.DataFrame,
    tago: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    spatial_cfg = config["spatial"]
    revenue_cfg = config["revenue"]
    cost_cfg = config["cost"]

    demand_by_dong = (
        demand.groupby("dong_id")
        .agg(
            mean_H=("H_is", "mean"),
            mean_arrivals=("arrivals_is", "mean"),
            mean_departures=("departures_is", "mean"),
            avg_distance_km_i=("avg_distance_km_i", "mean"),
        )
        .reset_index()
    )
    tago_by_dong = (
        tago.groupby("dong_id")
        .agg(
            x_obs_i=("gcoo_count_is", "mean"),
            total_pm_p95=("total_pm_count_is", lambda s: float(np.percentile(s, 95))),
        )
        .reset_index()
    )

    model = dong_master[["dong_id"]].merge(demand_by_dong, on="dong_id", how="left")
    model = model.merge(tago_by_dong, on="dong_id", how="left")
    numeric_cols = [
        "mean_H",
        "mean_arrivals",
        "mean_departures",
        "x_obs_i",
        "total_pm_p95",
    ]
    model[numeric_cols] = model[numeric_cols].fillna(0)
    city_avg_distance = demand["avg_distance_km_i"].mean()
    model["avg_distance_km_i"] = model["avg_distance_km_i"].fillna(city_avg_distance)

    model["K_i"] = np.ceil(
        spatial_cfg["capacity_multiplier"] * model["total_pm_p95"]
    ).astype(int)
    min_capacity = spatial_cfg["min_capacity_if_demand_positive"]
    model.loc[(model["K_i"] == 0) & (model["mean_H"] > 0), "K_i"] = min_capacity

    trip_minutes = (
        model["avg_distance_km_i"] / revenue_cfg["avg_scooter_speed_kmph"] * 60.0
    )
    model["p_i"] = revenue_cfg["unlock_fee"] + revenue_cfg["per_minute_fee"] * trip_minutes
    model["B_i"] = (
        (model["mean_arrivals"] - model["mean_departures"]).abs()
        / (model["mean_arrivals"] + model["mean_departures"] + 1)
    )
    model["c_i"] = cost_cfg["base_fixed_cost_per_scooter_day"] * (
        1 + cost_cfg["mu_imbalance_cost"] * model["B_i"]
    )
    return model[["dong_id", "p_i", "c_i", "K_i", "x_obs_i", "B_i"]]


def compute_q(a_is: float, x_i: int, c_is: float, config: dict[str, Any]) -> float:
    model_cfg = config["nonlinear_model"]
    beta = model_cfg["beta_default_if_uncalibrated"]
    theta = model_cfg["theta_competition_pressure"]
    upper_u = model_cfg["U_max_rides_per_scooter_day"]
    capture = a_is * (1 - math.exp(-beta * x_i / (1 + theta * c_is)))
    return min(capture, upper_u * x_i)


def build_od_rebalancing_tables(
    demand: pd.DataFrame,
    fallback_distance_km: float,
) -> tuple[dict[str, list[tuple[str, float]]], dict[tuple[str, str], float]]:
    if demand.empty:
        return {}, {}

    od_source = demand.copy()
    if {"origin_dong_id", "destination_dong_id", "distance_km"}.issubset(od_source.columns):
        grouped = (
            od_source.groupby(["origin_dong_id", "destination_dong_id"])
            .agg(trips=("origin_dong_id", "size"), distance_km=("distance_km", "mean"))
            .reset_index()
            .rename(columns={"origin_dong_id": "origin", "destination_dong_id": "destination"})
        )
    else:
        return {}, {}

    transitions: dict[str, list[tuple[str, float]]] = {}
    distance_lookup: dict[tuple[str, str], float] = {}
    for origin, group in grouped.groupby("origin"):
        total = float(group["trips"].sum())
        if total <= 0:
            continue
        transitions[str(origin)] = [
            (str(row.destination), float(row.trips) / total)
            for row in group.itertuples(index=False)
        ]
        for row in group.itertuples(index=False):
            origin_id = str(row.origin)
            destination_id = str(row.destination)
            distance = float(row.distance_km) if not pd.isna(row.distance_km) else fallback_distance_km
            distance_lookup[(origin_id, destination_id)] = distance
            distance_lookup[(destination_id, origin_id)] = distance

    return transitions, distance_lookup


def estimate_end_distribution(
    allocation: dict[str, int],
    transitions: dict[str, list[tuple[str, float]]],
    utilization: dict[str, float],
) -> dict[str, float]:
    end_counts = {dong_id: float(count) for dong_id, count in allocation.items()}
    for origin, count in allocation.items():
        if count <= 0:
            continue
        moved = min(float(count), max(0.0, utilization.get(origin, 0.0)))
        if moved <= 0:
            continue
        end_counts[origin] = end_counts.get(origin, 0.0) - moved
        destination_mix = transitions.get(origin)
        if not destination_mix:
            end_counts[origin] = end_counts.get(origin, 0.0) + moved
            continue
        for destination, probability in destination_mix:
            end_counts[destination] = end_counts.get(destination, 0.0) + moved * probability
    return end_counts


def distance_between_dongs(
    origin: str,
    destination: str,
    distance_lookup: dict[tuple[str, str], float],
    fallback_distance_km: float,
) -> float:
    if origin == destination:
        return 0.0
    return float(distance_lookup.get((origin, destination), fallback_distance_km))


def greedy_rebalancing_km(
    target: dict[str, int],
    end_counts: dict[str, float],
    distance_lookup: dict[tuple[str, str], float],
    fallback_distance_km: float,
) -> float:
    zones = sorted(set(target).union(end_counts))
    surplus = {zone: end_counts.get(zone, 0.0) - float(target.get(zone, 0)) for zone in zones}
    deficits = {zone: -value for zone, value in surplus.items() if value < -1e-9}
    supplies = {zone: value for zone, value in surplus.items() if value > 1e-9}
    total_km = 0.0

    for deficit_zone, deficit_amount in sorted(deficits.items()):
        remaining = deficit_amount
        while remaining > 1e-9 and supplies:
            supply_zone = min(
                supplies,
                key=lambda zone: distance_between_dongs(
                    zone, deficit_zone, distance_lookup, fallback_distance_km
                ),
            )
            moved = min(remaining, supplies[supply_zone])
            total_km += moved * distance_between_dongs(
                supply_zone, deficit_zone, distance_lookup, fallback_distance_km
            )
            remaining -= moved
            supplies[supply_zone] -= moved
            if supplies[supply_zone] <= 1e-9:
                del supplies[supply_zone]
    return total_km


def estimate_rebalancing(
    allocation: dict[str, int],
    utilization: dict[str, float],
    transitions: dict[str, list[tuple[str, float]]],
    distance_lookup: dict[tuple[str, str], float],
    fallback_distance_km: float,
    cost_per_scooter_km: float,
) -> tuple[float, float]:
    if cost_per_scooter_km <= 0 or not allocation:
        return 0.0, 0.0
    end_counts = estimate_end_distribution(allocation, transitions, utilization)
    relocation_km = greedy_rebalancing_km(
        allocation, end_counts, distance_lookup, fallback_distance_km
    )
    return relocation_km, relocation_km * cost_per_scooter_km


def optimize_allocation(
    model_inputs: pd.DataFrame,
    demand: pd.DataFrame,
    tago: pd.DataFrame,
    config: dict[str, Any],
    od_trips: pd.DataFrame | None = None,
) -> pd.DataFrame:
    demand_cfg = config["demand"]
    cost_cfg = config["cost"]
    model_cfg = config["nonlinear_model"]

    x_obs_total = int(round(model_inputs["x_obs_i"].sum()))
    fallback_supply = max(1, int(model_inputs["K_i"].sum()))
    total_supply = x_obs_total or min(10, fallback_supply)

    mean_h_total = demand.groupby("dong_id")["H_is"].mean().sum()
    alpha = (
        (total_supply * demand_cfg["u0_avg_rides_per_scooter_day"]) / mean_h_total
        if mean_h_total > 0
        else 0
    )
    comp_max = max(float(tago["competitor_count_is"].max()), 0.0)
    comp_denominator = math.log1p(comp_max) if comp_max > 0 else 1.0

    demand = demand.copy()
    tago = tago.copy()
    model_inputs = model_inputs.copy()
    demand["dong_id"] = demand["dong_id"].astype(str)
    tago["dong_id"] = tago["dong_id"].astype(str)
    model_inputs["dong_id"] = model_inputs["dong_id"].astype(str)

    scenarios = sorted(set(demand["scenario_day"]).union(set(tago["scenario_day"])))
    demand_lookup = demand.set_index(["scenario_day", "dong_id"])["H_is"].to_dict()
    competitor_lookup = (
        tago.set_index(["scenario_day", "dong_id"])["competitor_count_is"].to_dict()
    )
    input_lookup = model_inputs.set_index("dong_id").to_dict("index")
    operating_profit_by_k: dict[str, dict[int, float]] = {}
    used_scooters_by_k: dict[str, dict[int, float]] = {}
    marginal_items = []

    for dong_id, row in input_lookup.items():
        operating_profit_by_k[dong_id] = {0: 0.0}
        used_scooters_by_k[dong_id] = {0: 0.0}
        prev_profit = 0.0
        for k in range(1, int(row["K_i"]) + 1):
            profit_sum = 0.0
            used_sum = 0.0
            for scenario_day in scenarios:
                h_is = float(demand_lookup.get((scenario_day, dong_id), 0.0))
                c_is = float(competitor_lookup.get((scenario_day, dong_id), 0.0))
                d_is = alpha * h_is
                c_tilde = math.log1p(c_is) / comp_denominator if comp_max > 0 else 0.0
                a_is = d_is * (1 + model_cfg["lambda_market_validation"] * c_tilde)
                q_is = compute_q(a_is, k, c_is, config)
                profit_sum += (row["p_i"] - cost_cfg["variable_cost_per_ride"]) * q_is
                profit_sum -= row["c_i"] * k
                used_sum += min(float(k), q_is)
            current_profit = profit_sum / max(len(scenarios), 1)
            operating_profit_by_k[dong_id][k] = current_profit
            used_scooters_by_k[dong_id][k] = used_sum / max(len(scenarios), 1)
            marginal_items.append(
                {
                    "dong_id": dong_id,
                    "k": k,
                    "delta_profit": current_profit - prev_profit,
                }
            )
            prev_profit = current_profit

    fallback_distance_km = float(cost_cfg.get("rebalancing_fallback_distance_km", 2.0))
    rebalancing_cost_per_km = float(cost_cfg.get("rebalancing_cost_per_scooter_km", 0.0))
    rebalancing_candidate_pool_size = int(cost_cfg.get("rebalancing_candidate_pool_size", 80))
    transitions, distance_lookup = build_od_rebalancing_tables(
        od_trips if od_trips is not None else pd.DataFrame(), fallback_distance_km
    )

    allocation = {dong_id: 0 for dong_id in model_inputs["dong_id"]}
    selected = 0
    operating_profit = 0.0
    rebalancing_km = 0.0
    rebalancing_cost = 0.0

    if rebalancing_cost_per_km > 0 and transitions:
        ranked_marginal_items = [item for item in marginal_items if item["delta_profit"] > 0]
        ranked_marginal_items.sort(key=lambda item: item["delta_profit"], reverse=True)
        while selected < total_supply:
            best_item: dict[str, Any] | None = None
            evaluated_candidates = 0
            for item in ranked_marginal_items:
                if evaluated_candidates >= rebalancing_candidate_pool_size:
                    break
                dong_id = item["dong_id"]
                next_k = allocation[dong_id] + 1
                if item["k"] != next_k:
                    continue
                operating_delta = float(item["delta_profit"])
                candidate_allocation = dict(allocation)
                candidate_allocation[dong_id] = next_k
                candidate_utilization = {
                    zone: used_scooters_by_k[zone].get(count, 0.0)
                    for zone, count in candidate_allocation.items()
                }
                candidate_km, candidate_cost = estimate_rebalancing(
                    candidate_allocation,
                    candidate_utilization,
                    transitions,
                    distance_lookup,
                    fallback_distance_km,
                    rebalancing_cost_per_km,
                )
                evaluated_candidates += 1
                adjusted_delta = operating_delta - (candidate_cost - rebalancing_cost)
                if adjusted_delta <= 0:
                    continue
                if best_item is None or adjusted_delta > best_item["adjusted_delta"]:
                    best_item = {
                        "dong_id": dong_id,
                        "adjusted_delta": adjusted_delta,
                        "operating_delta": operating_delta,
                        "rebalancing_km": candidate_km,
                        "rebalancing_cost": candidate_cost,
                    }
            if best_item is None:
                break
            best_dong = best_item["dong_id"]
            allocation[best_dong] += 1
            selected += 1
            operating_profit += float(best_item["operating_delta"])
            rebalancing_km = float(best_item["rebalancing_km"])
            rebalancing_cost = float(best_item["rebalancing_cost"])
    else:
        marginal_items = [item for item in marginal_items if item["delta_profit"] > 0]
        marginal_items.sort(key=lambda item: item["delta_profit"], reverse=True)
        for item in marginal_items:
            if selected >= total_supply:
                break
            dong_id = item["dong_id"]
            if allocation[dong_id] == item["k"] - 1:
                allocation[dong_id] += 1
                selected += 1
        operating_profit = sum(
            operating_profit_by_k[dong_id].get(count, 0.0)
            for dong_id, count in allocation.items()
        )
        if transitions:
            final_utilization = {
                zone: used_scooters_by_k[zone].get(count, 0.0)
                for zone, count in allocation.items()
            }
            rebalancing_km, rebalancing_cost = estimate_rebalancing(
                allocation,
                final_utilization,
                transitions,
                distance_lookup,
                fallback_distance_km,
                rebalancing_cost_per_km,
            )

    objective_profit = operating_profit - rebalancing_cost
    return pd.DataFrame(
        [
            {
                "dong_id": dong_id,
                "x_star_i": count,
                "total_supply_F": total_supply,
                "expected_operating_profit": operating_profit,
                "expected_rebalancing_km": rebalancing_km,
                "expected_rebalancing_cost": rebalancing_cost,
                "expected_profit_after_rebalancing": objective_profit,
                "rebalancing_cost_per_scooter_km": rebalancing_cost_per_km,
            }
            for dong_id, count in allocation.items()
        ]
    )


def write_report(
    out_dir: Path,
    pm_like: pd.DataFrame,
    demand: pd.DataFrame,
    tago: pd.DataFrame,
    allocation: pd.DataFrame,
) -> None:
    active_alloc = allocation[allocation["x_star_i"] > 0]
    lines = [
        "# Prototype Report",
        "",
        "This is a tiny end-to-end prototype, not a final analysis run.",
        "",
        "## Inputs",
        "",
        f"- PM-like fixture trips retained: {len(pm_like)}",
        f"- Demand scenario rows: {len(demand)}",
        f"- TAGO-like scenario rows: {len(tago)}",
        "",
        "## Allocation",
        "",
        f"- Total supply used: {int(allocation['total_supply_F'].max())}",
        f"- Active dongs: {len(active_alloc)}",
        f"- Expected rebalancing km: {float(allocation['expected_rebalancing_km'].max()):.2f}",
        f"- Expected rebalancing cost: {float(allocation['expected_rebalancing_cost'].max()):.0f}",
        f"- Expected profit after rebalancing: {float(allocation['expected_profit_after_rebalancing'].max()):.0f}",
        "",
        "Top allocated dongs:",
    ]
    if active_alloc.empty:
        lines.append(
            "- No allocation selected because all marginal-profit candidates were non-positive under the placeholder economics."
        )
    else:
        for row in active_alloc.sort_values("x_star_i", ascending=False).to_dict("records"):
            lines.append(f"- `{row['dong_id']}`: {row['x_star_i']}")
    lines.extend(
        [
            "",
            "## Data Gap",
            "",
            "The TAGO PM raw input in this run is a fixture with the expected spec schema.",
            "Replace it with `data/raw/tago_pm_snapshots_*.csv` once the real API is available.",
        ]
    )
    (out_dir / "prototype_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/prototype")
    parser.add_argument(
        "--seoul-bike-probe",
        default="outputs/api_probe/seoul_bike_sample.json",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_config()
    dongs = make_fixture_dongs()
    dong_master = dong_master_from_boxes(dongs)

    bike_payload = load_seoul_bike_probe(Path(args.seoul_bike_probe))
    bike_stations = normalize_bike_stations(bike_payload)
    bike_stations = attach_dong_id(bike_stations, dongs)

    tago_raw = load_tago_pm_raw()
    tago_scenario = aggregate_tago_scenario(
        tago_raw,
        dongs,
        config["spatial"]["battery_threshold"],
    )

    bike_trips = make_fixture_bike_trips(bike_stations)
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
        pm_like,
    )

    dong_master.to_csv(out_dir / "dong_master.csv", index=False)
    bike_stations.to_csv(out_dir / "bike_stations_normalized.csv", index=False)
    pm_like.to_csv(out_dir / "bike_trip_pm_like.csv", index=False)
    demand_scenario.to_csv(out_dir / "demand_scenario.csv", index=False)
    tago_scenario.to_csv(out_dir / "tago_scenario.csv", index=False)
    model_inputs.to_csv(out_dir / "model_inputs.csv", index=False)
    allocation.to_csv(out_dir / "allocation_optimized.csv", index=False)
    write_report(out_dir, pm_like, demand_scenario, tago_scenario, allocation)

    print(f"Wrote prototype outputs to {out_dir}")
    print(f"PM-like trips retained: {len(pm_like)}")
    print(f"Allocated scooters: {int(allocation['x_star_i'].sum())}")


if __name__ == "__main__":
    main()
