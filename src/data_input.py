from __future__ import annotations

import argparse
import json
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import yaml

from common import (
    append_jsonl,
    detect_response_format,
    env_first,
    ensure_dir,
    extract_records,
    flatten_field_paths,
    http_get,
    load_dotenv,
    now_kst_iso,
    now_kst_label,
    stable_hash,
)


SEOUL_BIKE_URL_TEMPLATE = "http://openapi.seoul.go.kr:8088/{key}/json/bikeList/{start}/{end}/"
SEOUL_OPENAPI_URL_TEMPLATE = "http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/{suffix}"
PRIVATE_PM_SUMMARY_FILE = "서울시 민간대여 공유 전동킥보드 기기 현황_25.12월기준.csv"


def load_config(path: str = "config/model_config.yaml") -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def manifest_path(config: dict[str, Any]) -> Path:
    return Path(config["data_input"]["paths"]["manifest_jsonl"])


def record_manifest(config: dict[str, Any], event: dict[str, Any]) -> None:
    event.setdefault("created_at", now_kst_iso())
    append_jsonl(manifest_path(config), event)


def normalize_bike_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append(
            {
                "station_id": row.get("stationId"),
                "station_name": row.get("stationName"),
                "latitude": pd.to_numeric(row.get("stationLatitude"), errors="coerce"),
                "longitude": pd.to_numeric(row.get("stationLongitude"), errors="coerce"),
                "parking_bike_count": pd.to_numeric(row.get("parkingBikeTotCnt"), errors="coerce"),
                "rack_count": pd.to_numeric(row.get("rackTotCnt"), errors="coerce"),
                "shared_percent": pd.to_numeric(row.get("shared"), errors="coerce"),
            }
        )
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    return df.dropna(subset=["station_id", "latitude", "longitude"]).drop_duplicates("station_id")


def seoul_api_key() -> str:
    key = env_first(["SEOUL_API_KEY", "SEOUL_OPEN_API_KEY"])
    if not key:
        raise RuntimeError("SEOUL_API_KEY or SEOUL_OPEN_API_KEY is required.")
    return key


def extract_seoul_rows(payload: dict[str, Any], service: str) -> tuple[list[dict[str, Any]], int | None, str | None]:
    body = payload.get(service)
    if not isinstance(body, dict):
        body = next((value for value in payload.values() if isinstance(value, dict) and "row" in value), {})
    rows = body.get("row", []) if isinstance(body, dict) else []
    total = body.get("list_total_count") if isinstance(body, dict) else None
    result = body.get("RESULT", {}) if isinstance(body, dict) else {}
    code = result.get("CODE") if isinstance(result, dict) else None
    try:
        total_count = int(total) if total is not None else None
    except ValueError:
        total_count = None
    return rows if isinstance(rows, list) else [], total_count, code


def seoul_openapi_url(key: str, service: str, start: int, end: int, suffix_parts: list[str] | None = None) -> str:
    suffix = "/".join(str(part).strip("/") for part in (suffix_parts or []) if str(part) != "")
    if suffix:
        suffix += "/"
    return SEOUL_OPENAPI_URL_TEMPLATE.format(
        key=quote(key, safe=""),
        service=service,
        start=start,
        end=end,
        suffix=suffix,
    )


def fetch_seoul_openapi_rows(
    config: dict[str, Any],
    service: str,
    raw_subdir: str,
    snapshot_label: str,
    suffix_parts: list[str] | None = None,
    max_pages: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    key = seoul_api_key()
    api_cfg = config["data_input"]["apis"].get("seoul_openapi", {})
    rows_per_page = int(api_cfg.get("rows_per_page", 1000))
    retry_attempts = int(api_cfg.get("retry_attempts", 3))
    retry_sleep_seconds = float(api_cfg.get("retry_sleep_seconds", 1.0))
    raw_dir = ensure_dir(Path(config["data_input"]["paths"]["raw_api_dir"]) / raw_subdir)
    page_limit = max_pages or 10_000
    rows_all: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    known_total_count: int | None = None

    for page in range(page_limit):
        start = page * rows_per_page + 1
        end = (page + 1) * rows_per_page
        rows: list[dict[str, Any]] = []
        total_count: int | None = None
        code: str | None = None

        for attempt in range(1, max(retry_attempts, 1) + 1):
            url = seoul_openapi_url(key, service, start, end, suffix_parts)
            result = http_get(url, secrets=[key])
            response_format = detect_response_format(result.text)
            suffix_label = "_".join(str(part) for part in (suffix_parts or [])) or "all"
            attempt_label = f"_attempt{attempt}" if attempt > 1 else ""
            raw_path = raw_dir / (
                f"{service}_{snapshot_label}_{suffix_label}_{start}_{end}{attempt_label}.{response_format}"
            )
            raw_path.write_text(result.text, encoding="utf-8")
            event = {
                "service": service,
                "attempt": attempt,
                "redacted_url": result.redacted_url,
                "raw_path": str(raw_path),
                "response_format": response_format,
                "notes": result.notes,
            }

            if response_format != "json":
                event["ok"] = False
                event["error"] = f"Expected JSON, got {response_format}."
                events.append(event)
                if attempt < retry_attempts:
                    time.sleep(retry_sleep_seconds)
                    continue
                break

            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError as exc:
                event["ok"] = False
                event["error"] = f"JSON decode failed: {exc}"
                events.append(event)
                if attempt < retry_attempts:
                    time.sleep(retry_sleep_seconds)
                    continue
                break

            rows, total_count, code = extract_seoul_rows(payload, service)
            if total_count is not None:
                known_total_count = total_count
            event.update({"ok": code == "INFO-000", "rows": len(rows), "total_count": total_count, "code": code})
            events.append(event)

            retry_empty_page = code == "INFO-000" and not rows and (
                known_total_count is None or len(rows_all) < known_total_count
            )
            retry_bad_code = code != "INFO-000"
            if (retry_empty_page or retry_bad_code) and attempt < retry_attempts:
                time.sleep(retry_sleep_seconds)
                continue
            break

        rows_all.extend(rows)

        if code != "INFO-000" or not rows:
            break
        if len(rows) < rows_per_page:
            break
        if known_total_count is not None and len(rows_all) >= known_total_count:
            break

    return rows_all, events


def fetch_seoul_bike_stations(
    config: dict[str, Any],
    snapshot_label: str,
    max_pages: int | None = None,
) -> pd.DataFrame:
    key = seoul_api_key()

    api_cfg = config["data_input"]["apis"]["seoul_bike"]
    rows_per_page = int(api_cfg.get("rows_per_page", 1000))
    configured_max_pages = int(api_cfg.get("max_pages", 10))
    page_limit = max_pages or configured_max_pages
    raw_dir = ensure_dir(Path(config["data_input"]["paths"]["raw_api_dir"]) / "seoul_bike")

    all_rows: list[dict[str, Any]] = []
    for page in range(page_limit):
        start = page * rows_per_page + 1
        end = (page + 1) * rows_per_page
        url = SEOUL_BIKE_URL_TEMPLATE.format(key=key, start=start, end=end)
        result = http_get(url, secrets=[key])
        response_format = detect_response_format(result.text)
        raw_path = raw_dir / f"bikeList_{snapshot_label}_{start}_{end}.{response_format}"
        raw_path.write_text(result.text, encoding="utf-8")

        if response_format != "json":
            record_manifest(
                config,
                {
                    "source": "seoul_bike",
                    "ok": False,
                    "redacted_url": result.redacted_url,
                    "raw_path": str(raw_path),
                    "notes": [*result.notes, f"Expected JSON, got {response_format}."],
                },
            )
            break

        payload = json.loads(result.text)
        body = payload.get("rentBikeStatus", {})
        rows = body.get("row", [])
        all_rows.extend(rows)

        if not rows or len(rows) < rows_per_page:
            break

    stations = normalize_bike_rows(all_rows)
    if stations.empty:
        raise RuntimeError("No Seoul Bike station rows were fetched; refusing to overwrite the station CSV.")
    station_path = Path(config["data_input"]["paths"]["seoul_bike_stations_csv"])
    ensure_dir(station_path.parent)
    stations.to_csv(station_path, index=False)

    record_manifest(
        config,
        {
            "source": "seoul_bike",
            "ok": not stations.empty,
            "rows": len(stations),
            "normalized_path": str(station_path),
            "snapshot_label": snapshot_label,
            "notes": [f"Fetched {len(all_rows)} raw station rows."],
        },
    )
    return stations


def normalize_kickboard_tow_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append(
            {
                "event_date": row.get("DCLR_DT"),
                "gu_name": row.get("GU_NM"),
                "address": row.get("ADDR"),
                "tow_type": row.get("TYPE"),
                "action_date": row.get("ACTN_DAY"),
                "source_service": "tbAutoKickboard",
            }
        )
    return pd.DataFrame.from_records(records)


def fetch_seoul_kickboard_tow(
    config: dict[str, Any],
    snapshot_label: str,
    max_pages: int | None = None,
) -> pd.DataFrame:
    service = config["data_input"]["apis"]["seoul_openapi"]["kickboard_tow_service"]
    rows, events = fetch_seoul_openapi_rows(
        config,
        service,
        "seoul_kickboard_tow",
        snapshot_label,
        max_pages=max_pages,
    )
    normalized = normalize_kickboard_tow_rows(rows)
    out_path = Path(config["data_input"]["paths"]["seoul_pm_tow_events_csv"])
    ensure_dir(out_path.parent)
    normalized.to_csv(out_path, index=False)
    record_manifest(
        config,
        {
            "source": "seoul_kickboard_tow",
            "ok": not normalized.empty,
            "rows": len(normalized),
            "normalized_path": str(out_path),
            "snapshot_label": snapshot_label,
            "page_event_count": len(events),
            "page_events_sample": events[:20],
            "notes": [
                "Actual Seoul Open Data tbAutoKickboard rows. "
                "Use as PM activity/friction proxy, not as live device inventory."
            ],
        },
    )
    return normalized


def normalize_kickboard_parking_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append(
            {
                "parking_id": row.get("SN"),
                "gu_name": row.get("SGG_NM"),
                "address": row.get("PSTN"),
                "detail_location": row.get("DTL_PSTN"),
                "has_stand": row.get("STAND_YN"),
                "source_service": "parkingKickboard",
            }
        )
    return pd.DataFrame.from_records(records)


def fetch_seoul_kickboard_parking(
    config: dict[str, Any],
    snapshot_label: str,
    max_pages: int | None = None,
) -> pd.DataFrame:
    service = config["data_input"]["apis"]["seoul_openapi"]["kickboard_parking_service"]
    rows, events = fetch_seoul_openapi_rows(
        config,
        service,
        "seoul_kickboard_parking",
        snapshot_label,
        max_pages=max_pages,
    )
    normalized = normalize_kickboard_parking_rows(rows)
    out_path = Path(config["data_input"]["paths"]["seoul_kickboard_parking_zones_csv"])
    ensure_dir(out_path.parent)
    normalized.to_csv(out_path, index=False)
    record_manifest(
        config,
        {
            "source": "seoul_kickboard_parking",
            "ok": not normalized.empty,
            "rows": len(normalized),
            "normalized_path": str(out_path),
            "snapshot_label": snapshot_label,
            "page_event_count": len(events),
            "page_events_sample": events[:20],
            "notes": ["Actual Seoul Open Data parkingKickboard rows. Use as placement constraint/prior."],
        },
    )
    return normalized


def operating_day_hours(operating_day: str, configured_hours: list[int] | None = None) -> list[tuple[str, int]]:
    if configured_hours:
        return [(operating_day, int(hour)) for hour in configured_hours]
    day = datetime.strptime(operating_day, "%Y-%m-%d").date()
    next_day = day + timedelta(days=1)
    return [(day.isoformat(), hour) for hour in range(4, 24)] + [
        (next_day.isoformat(), hour) for hour in range(0, 4)
    ]


def normalize_bike_trip_history_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append(
            {
                "rental_datetime": row.get("RENT_DT"),
                "return_datetime": row.get("RTN_DT"),
                "rental_station_id": row.get("RENT_STATION_ID") or row.get("RENT_ID"),
                "return_station_id": row.get("RETURN_STATION_ID") or row.get("RTN_ID"),
                "distance_m": pd.to_numeric(row.get("USE_DST"), errors="coerce"),
                "duration_min": pd.to_numeric(row.get("USE_MIN"), errors="coerce"),
                "bike_type": row.get("BIKE_SE_CD"),
                "source_service": "tbCycleRentData",
            }
        )
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    return df.dropna(
        subset=[
            "rental_datetime",
            "return_datetime",
            "rental_station_id",
            "return_station_id",
            "distance_m",
            "duration_min",
        ]
    )


def fetch_seoul_bike_trip_history(
    config: dict[str, Any],
    snapshot_label: str,
    operating_day: str | None = None,
    max_pages_per_hour: int | None = None,
) -> pd.DataFrame:
    api_cfg = config["data_input"]["apis"]["seoul_openapi"]
    service = api_cfg["bike_trip_history_service"]
    target_day = operating_day or api_cfg["bike_trip_history_default_operating_day"]
    configured_hours = api_cfg.get("bike_trip_history_hours")
    pages_per_hour = max_pages_per_hour or int(api_cfg.get("max_trip_pages_per_hour", 3))

    rows_all: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for date_text, hour in operating_day_hours(target_day, configured_hours):
        rows, hour_events = fetch_seoul_openapi_rows(
            config,
            service,
            "seoul_bike_trip_history",
            snapshot_label,
            suffix_parts=[date_text, str(hour)],
            max_pages=pages_per_hour,
        )
        rows_all.extend(rows)
        events.extend(hour_events)

    normalized = normalize_bike_trip_history_rows(rows_all)
    out_path = Path(config["data_input"]["paths"]["seoul_bike_trips_pattern"].format(operating_day=target_day))
    ensure_dir(out_path.parent)
    normalized.to_csv(out_path, index=False)
    record_manifest(
        config,
        {
            "source": "seoul_bike_trip_history",
            "ok": not normalized.empty,
            "rows": len(normalized),
            "normalized_path": str(out_path),
            "snapshot_label": snapshot_label,
            "operating_day": target_day,
            "hours": operating_day_hours(target_day, configured_hours),
            "max_pages_per_hour": pages_per_hour,
            "page_event_count": len(events),
            "page_events_sample": events[:20],
            "notes": [
                "Actual Seoul Bike tbCycleRentData rows. "
                "Configured hours may be a sample; raise max_trip_pages_per_hour or clear bike_trip_history_hours for full-day collection."
            ],
        },
    )
    return normalized


def first_value(row: dict[str, Any], names: list[str]) -> Any:
    lower_map = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        key = name.strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_pm_records(rows: list[dict[str, Any]], snapshot_ts: str) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    records = []
    for row in rows:
        operator_name = first_value(
            row,
            [
                "operator_name",
                "operatorName",
                "providerName",
                "providername",
                "oprtNm",
                "companyName",
                "업체명",
                "대여업체",
                "brand",
                "브랜드",
            ],
        )
        device_id = first_value(
            row,
            [
                "device_id",
                "deviceId",
                "vehicleID",
                "vehicleid",
                "pmId",
                "kickboardId",
                "vhclId",
                "기기ID",
                "기기아이디",
                "id",
            ],
        )
        battery_level = first_value(
            row,
            ["battery_level", "batteryLevel", "battery", "btry", "배터리", "배터리잔량"],
        )
        latitude = first_value(
            row,
            ["latitude", "lat", "gpsLati", "lati", "y", "위도", "위도값"],
        )
        longitude = first_value(
            row,
            ["longitude", "lng", "lon", "gpsLong", "long", "x", "경도", "경도값"],
        )

        lat = to_float(latitude)
        lon = to_float(longitude)
        if lat is None or lon is None:
            continue
        if not device_id:
            device_id = f"generated-{stable_hash(row)}"
        records.append(
            {
                "timestamp": snapshot_ts,
                "operator_name": operator_name or "UNKNOWN",
                "device_id": device_id,
                "battery_level": to_float(battery_level),
                "latitude": lat,
                "longitude": lon,
                "city_code": first_value(row, ["cityCode", "citycode"]),
                "city_name": first_value(row, ["cityName", "cityname"]),
            }
        )

    if rows and not records:
        sample_paths = flatten_field_paths(rows[0])
        notes.append(
            "No PM rows had recognizable latitude/longitude fields. "
            f"Sample fields: {', '.join(sample_paths[:30])}"
        )
    return pd.DataFrame.from_records(records), notes


def tago_service_key() -> str:
    service_key = env_first(["OPEN_DATA_PORTAL_API_KEY", "DATA_GO_KR_SERVICE_KEY"])
    if not service_key:
        raise RuntimeError("OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY is required.")
    return service_key


def tago_operation_url(api_cfg: dict[str, Any], operation_key: str) -> str:
    base_url = str(api_cfg["base_url"]).rstrip("/")
    operation = str(api_cfg[operation_key]).strip("/")
    return f"{base_url}/{operation}"


def extract_xml_records(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text.encode("utf-8"))
    records = []
    for item in root.findall(".//item"):
        row: dict[str, Any] = {}
        for child in list(item):
            tag = child.tag.split("}", 1)[-1]
            row[tag] = child.text
        records.append(row)
    return records


def extract_response_records(text: str, response_format: str) -> tuple[list[dict[str, Any]], list[str]]:
    if response_format == "json":
        payload = json.loads(text)
        return extract_records(payload), flatten_field_paths(payload)
    if response_format == "xml":
        return extract_xml_records(text), []
    return [], []


def total_count_from_response(text: str, response_format: str, observed_count: int) -> int | None:
    try:
        if response_format == "json":
            payload = json.loads(text)
            value = payload.get("response", {}).get("body", {}).get("totalCount")
            return int(value) if value is not None else None
        if response_format == "xml":
            root = ET.fromstring(text.encode("utf-8"))
            value = root.findtext(".//totalCount")
            return int(value) if value is not None else None
    except (ValueError, ET.ParseError, json.JSONDecodeError):
        return None
    return observed_count if observed_count else None


def fetch_tago_provider_page(
    config: dict[str, Any],
    page_no: int,
    snapshot_label: str,
) -> tuple[list[dict[str, Any]], int | None, dict[str, Any]]:
    api_cfg = config["data_input"]["apis"]["tago_pm"]
    service_key = tago_service_key()
    raw_dir = ensure_dir(Path(config["data_input"]["paths"]["raw_api_dir"]) / "tago_pm")
    params = {
        "serviceKey": service_key,
        "numOfRows": int(api_cfg.get("rows_per_page", 1000)),
        "pageNo": page_no,
        "_type": api_cfg.get("response_type", "json"),
        "cityName": api_cfg.get("provider_lookup_city_name"),
        "providerName": api_cfg.get("provider_name") or None,
    }
    result = http_get(tago_operation_url(api_cfg, "provider_operation"), params=params, secrets=[service_key])
    response_format = detect_response_format(result.text)
    raw_path = raw_dir / f"providers_{snapshot_label}_{page_no}.{response_format}"
    raw_path.write_text(result.text, encoding="utf-8")
    rows, field_paths = extract_response_records(result.text, response_format)
    return rows, total_count_from_response(result.text, response_format, len(rows)), {
        "redacted_url": result.redacted_url,
        "raw_path": str(raw_path),
        "response_format": response_format,
        "field_paths": field_paths[:80],
        "notes": result.notes,
    }


def city_matches_filter(city_name: Any, city_filter: Any) -> bool:
    if not city_filter:
        return True
    if not city_name:
        return False
    actual = str(city_name).strip()
    expected = str(city_filter).strip()
    return expected in actual or actual in expected


def normalize_tago_providers(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    seen = set()
    for row in rows:
        provider_name = first_value(row, ["providerName", "providername"])
        city_code = first_value(row, ["cityCode", "citycode"])
        city_name = first_value(row, ["cityName", "cityname"])
        if not provider_name or not city_code:
            continue
        key = (str(provider_name), str(city_code))
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "provider_name": provider_name,
                "city_code": city_code,
                "city_name": city_name,
            }
        )
    columns = ["provider_name", "city_code", "city_name"]
    return pd.DataFrame.from_records(records, columns=columns)


def fetch_tago_providers(
    config: dict[str, Any],
    snapshot_label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    api_cfg = config["data_input"]["apis"]["tago_pm"]
    rows_per_page = int(api_cfg.get("rows_per_page", 1000))
    max_pages = int(api_cfg.get("max_provider_pages", 20))
    all_rows: list[dict[str, Any]] = []
    page_events: list[dict[str, Any]] = []
    for page_no in range(1, max_pages + 1):
        rows, total_count, event = fetch_tago_provider_page(config, page_no, snapshot_label)
        page_events.append(event)
        all_rows.extend(rows)
        if not rows or len(rows) < rows_per_page:
            break
        if total_count is not None and len(all_rows) >= total_count:
            break

    all_providers = normalize_tago_providers(all_rows)
    raw_dir = Path(config["data_input"]["paths"]["raw_api_dir"]) / "tago_pm"
    all_providers_path = raw_dir / f"providers_all_{snapshot_label}.csv"
    ensure_dir(all_providers_path.parent)
    all_providers.to_csv(all_providers_path, index=False)

    target_city_name = api_cfg.get("target_city_name") or api_cfg.get("city_name")
    if target_city_name and not all_providers.empty:
        providers = all_providers[
            all_providers["city_name"].map(lambda city_name: city_matches_filter(city_name, target_city_name))
        ].copy()
    else:
        providers = all_providers

    providers_path = raw_dir / f"providers_{snapshot_label}.csv"
    ensure_dir(providers_path.parent)
    providers.to_csv(providers_path, index=False)
    return providers, all_providers, page_events


def fetch_tago_pm_list_page(
    config: dict[str, Any],
    provider_name: str,
    city_code: str,
    page_no: int,
    snapshot_label: str,
) -> tuple[list[dict[str, Any]], int | None, dict[str, Any]]:
    api_cfg = config["data_input"]["apis"]["tago_pm"]
    service_key = tago_service_key()
    raw_dir = ensure_dir(Path(config["data_input"]["paths"]["raw_api_dir"]) / "tago_pm")
    params = {
        "serviceKey": service_key,
        "numOfRows": int(api_cfg.get("rows_per_page", 1000)),
        "pageNo": page_no,
        "_type": api_cfg.get("response_type", "json"),
        "providerName": provider_name,
        "cityCode": city_code,
    }
    result = http_get(tago_operation_url(api_cfg, "list_operation"), params=params, secrets=[service_key])
    response_format = detect_response_format(result.text)
    safe_provider = "".join(ch if ch.isalnum() else "_" for ch in provider_name)[:60]
    raw_path = raw_dir / f"pm_list_{snapshot_label}_{city_code}_{safe_provider}_{page_no}.{response_format}"
    raw_path.write_text(result.text, encoding="utf-8")
    rows, field_paths = extract_response_records(result.text, response_format)
    return rows, total_count_from_response(result.text, response_format, len(rows)), {
        "provider_name": provider_name,
        "city_code": city_code,
        "redacted_url": result.redacted_url,
        "raw_path": str(raw_path),
        "response_format": response_format,
        "field_paths": field_paths[:80],
        "notes": result.notes,
    }


def fetch_tago_pm_snapshot(config: dict[str, Any], snapshot_label: str) -> pd.DataFrame:
    api_cfg = config["data_input"]["apis"]["tago_pm"]
    rows_per_page = int(api_cfg.get("rows_per_page", 1000))
    max_pages = int(api_cfg.get("max_list_pages", 50))
    notes: list[str] = []
    providers, all_providers, provider_events = fetch_tago_providers(config, snapshot_label)
    target_city_name = api_cfg.get("target_city_name") or api_cfg.get("city_name")
    if providers.empty:
        returned_cities = []
        if not all_providers.empty:
            returned_cities = sorted(all_providers["city_name"].dropna().astype(str).unique().tolist())
        record_manifest(
            config,
            {
                "source": "tago_pm",
                "ok": False,
                "snapshot_label": snapshot_label,
                "all_provider_rows": len(all_providers),
                "target_city_name": target_city_name,
                "returned_cities": returned_cities,
                "notes": [
                    "No TAGO PM providers matched the configured target city after all-city provider lookup."
                ],
                "provider_events": provider_events,
            },
        )
        return pd.DataFrame()

    all_rows: list[dict[str, Any]] = []
    list_events: list[dict[str, Any]] = []
    for provider in providers.to_dict("records"):
        provider_name = str(provider["provider_name"])
        city_code = str(provider["city_code"])
        provider_rows: list[dict[str, Any]] = []
        for page_no in range(1, max_pages + 1):
            rows, total_count, event = fetch_tago_pm_list_page(
                config,
                provider_name,
                city_code,
                page_no,
                snapshot_label,
            )
            list_events.append(event)
            provider_rows.extend(rows)
            if not rows or len(rows) < rows_per_page:
                break
            if total_count is not None and len(provider_rows) >= total_count:
                break
        all_rows.extend(provider_rows)

    snapshot_df, normalize_notes = normalize_pm_records(all_rows, now_kst_iso())
    notes.extend(normalize_notes)
    normalized_path: str | None = None
    if not snapshot_df.empty:
        out_path = Path(config["data_input"]["paths"]["tago_pm_snapshot_pattern"].format(label=snapshot_label))
        ensure_dir(out_path.parent)
        snapshot_df.to_csv(out_path, index=False)
        normalized_path = str(out_path)

    record_manifest(
        config,
        {
            "source": "tago_pm",
            "ok": not snapshot_df.empty,
            "rows": len(snapshot_df),
            "raw_rows": len(all_rows),
            "provider_rows": len(providers),
            "all_provider_rows": len(all_providers),
            "target_city_name": target_city_name,
            "normalized_path": normalized_path,
            "snapshot_label": snapshot_label,
            "provider_events": provider_events,
            "list_event_count": len(list_events),
            "list_events_sample": list_events[:20],
            "notes": notes,
        },
    )
    return snapshot_df


def normalize_private_pm_operator_summary(config: dict[str, Any]) -> pd.DataFrame:
    path = Path(PRIVATE_PM_SUMMARY_FILE)
    if not path.exists():
        return pd.DataFrame()

    raw = pd.read_csv(path, encoding="cp949", header=None)
    table = raw.iloc[3:].copy()
    table.columns = ["row_no", "operator_name", "brand", "device_count", "service_area"]
    table["operator_name"] = table["operator_name"].ffill()
    table["device_count"] = (
        table["device_count"].astype(str).str.replace(",", "", regex=False).pipe(pd.to_numeric, errors="coerce")
    )
    table = table.dropna(subset=["brand", "device_count", "service_area"])

    records = []
    for row in table.to_dict("records"):
        for gu in str(row["service_area"]).split(","):
            gu_name = gu.strip()
            if not gu_name:
                continue
            if not gu_name.endswith("구"):
                gu_name = f"{gu_name}구"
            records.append(
                {
                    "operator_name": row["operator_name"],
                    "brand": row["brand"],
                    "device_count_total": int(row["device_count"]),
                    "gu_name": gu_name,
                    "source_file": str(path),
                }
            )

    summary = pd.DataFrame.from_records(records)
    out_path = Path(config["data_input"]["paths"]["private_pm_operator_summary_csv"])
    ensure_dir(out_path.parent)
    summary.to_csv(out_path, index=False)
    record_manifest(
        config,
        {
            "source": "seoul_private_pm_operator_summary",
            "ok": not summary.empty,
            "rows": len(summary),
            "normalized_path": str(out_path),
            "notes": [
                "This is an operator/gu aggregate, not a per-device live snapshot. "
                "Use it as a prior or sanity check, not as TAGO scenario data."
            ],
        },
    )
    return summary


def run_data_input(args: argparse.Namespace) -> None:
    load_dotenv(args.env)
    config = load_config(args.config)
    snapshot_label = args.snapshot_label or now_kst_label()

    if args.source in {"all", "seoul-bike"}:
        stations = fetch_seoul_bike_stations(config, snapshot_label, args.max_pages)
        print(f"seoul-bike rows={len(stations)}")

    if args.source in {"all", "tago-pm"}:
        tago = fetch_tago_pm_snapshot(config, snapshot_label)
        print(f"tago-pm normalized_rows={len(tago)}")

    if args.source in {"all", "private-pm-summary"}:
        summary = normalize_private_pm_operator_summary(config)
        print(f"private-pm-summary rows={len(summary)}")

    if args.source in {"all", "seoul-kickboard-tow", "pm-surrogates"}:
        tow = fetch_seoul_kickboard_tow(config, snapshot_label, args.max_pages)
        print(f"seoul-kickboard-tow rows={len(tow)}")

    if args.source in {"all", "seoul-kickboard-parking", "pm-surrogates"}:
        parking = fetch_seoul_kickboard_parking(config, snapshot_label, args.max_pages)
        print(f"seoul-kickboard-parking rows={len(parking)}")

    if args.source in {"all", "seoul-bike-trips", "pm-surrogates"}:
        trips = fetch_seoul_bike_trip_history(
            config,
            snapshot_label,
            args.operating_day,
            args.max_pages_per_hour,
        )
        print(f"seoul-bike-trips rows={len(trips)}")

    print(f"manifest={manifest_path(config)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and normalize raw data inputs.")
    parser.add_argument("--config", default="config/model_config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--snapshot-label")
    parser.add_argument(
        "--source",
        choices=[
            "all",
            "seoul-bike",
            "tago-pm",
            "private-pm-summary",
            "seoul-kickboard-tow",
            "seoul-kickboard-parking",
            "seoul-bike-trips",
            "pm-surrogates",
        ],
        default="all",
    )
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--operating-day", help="YYYY-MM-DD operating day for Seoul Bike trip history.")
    parser.add_argument("--max-pages-per-hour", type=int)
    args = parser.parse_args()
    run_data_input(args)


if __name__ == "__main__":
    main()
