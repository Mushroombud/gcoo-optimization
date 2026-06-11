from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from common import load_dotenv


SEOUL_BIKE_URL_TEMPLATE = "http://openapi.seoul.go.kr:8088/{key}/json/bikeList/1/5/"
TAGO_PM_PROVIDER_URL = "http://apis.data.go.kr/1613000/PersonalMobilityInfo/GetPMProvider"
TAGO_PM_LIST_URL = "http://apis.data.go.kr/1613000/PersonalMobilityInfo/GetPMListByProvider"


@dataclass
class ProbeResult:
    name: str
    url: str
    status_code: int | None
    response_format: str
    ok: bool
    field_paths: list[str]
    notes: list[str]
    raw_file: str | None = None


def redact_url(url: str, secret_values: list[str]) -> str:
    redacted = url
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def detect_response_format(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<"):
        return "xml"
    return "text"


def http_get(url: str, secret_values: list[str] | None = None) -> tuple[int | None, str, list[str]]:
    secrets = secret_values or []
    notes: list[str] = []
    try:
        response = requests.get(url, timeout=20)
        return response.status_code, response.text, notes
    except requests.RequestException as exc:
        notes.append(f"requests failed: {redact_url(str(exc), secrets)}")

    curl_cmd = [
        "curl",
        "-sL",
        "-A",
        "Mozilla/5.0",
        "-w",
        "\n__HTTP_STATUS__:%{http_code}",
        url,
    ]
    try:
        completed = subprocess.run(
            curl_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        notes.append(f"curl fallback failed: {exc}")
        return None, "", notes

    text = completed.stdout
    status_code: int | None = None
    marker = "\n__HTTP_STATUS__:"
    if marker in text:
        text, status_text = text.rsplit(marker, 1)
        try:
            status_code = int(status_text.strip())
        except ValueError:
            notes.append(f"curl returned an unparsable HTTP status: {status_text!r}")
    if completed.returncode != 0:
        notes.append(f"curl fallback exited with code {completed.returncode}: {completed.stderr[:160]}")
    else:
        notes.append("Used curl fallback after requests failed in this environment.")
    return status_code, text, notes


def flatten_field_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            paths.extend(flatten_field_paths(child, child_prefix))
    elif isinstance(value, list):
        if value:
            paths.extend(flatten_field_paths(value[0], f"{prefix}[]"))
        else:
            paths.append(f"{prefix}[]")
    else:
        paths.append(prefix)
    return paths


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def city_matches(city_name: Any, target_city: str) -> bool:
    if not city_name:
        return False
    actual = str(city_name).strip()
    target = target_city.strip()
    return target in actual or actual in target


def probe_seoul_bike(out_dir: Path) -> ProbeResult:
    api_key = os.getenv("SEOUL_API_KEY") or os.getenv("SEOUL_OPEN_API_KEY") or "sample"
    url = SEOUL_BIKE_URL_TEMPLATE.format(key=api_key)
    notes: list[str] = []

    status_code, text, transport_notes = http_get(url, [api_key])
    notes.extend(transport_notes)
    cached_json_path = out_dir / "seoul_bike_sample.json"
    if not text.strip() and cached_json_path.exists():
        text = cached_json_path.read_text(encoding="utf-8")
        if status_code == 0:
            status_code = None
        notes.append("Used cached raw API response from seoul_bike_sample.json.")
    if status_code is None and not text:
        return ProbeResult(
            name="seoul_bike_realtime_station",
            url=redact_url(url, [api_key]),
            status_code=None,
            response_format="request_error",
            ok=False,
            field_paths=[],
            notes=notes or ["Request failed before a response body was received."],
        )

    response_format = detect_response_format(text)
    raw_path = out_dir / "seoul_bike_sample.json"
    field_paths: list[str] = []
    ok = False

    if response_format == "json":
        payload = json.loads(text)
        write_json(raw_path, payload)
        field_paths = flatten_field_paths(payload)
        result_code = (
            payload.get("rentBikeStatus", {})
            .get("RESULT", {})
            .get("CODE")
        )
        rows = payload.get("rentBikeStatus", {}).get("row", [])
        ok = result_code == "INFO-000" and isinstance(rows, list)
        notes.append(f"Rows returned: {len(rows)}")
        notes.append("Usable for station_id, station_name, latitude, longitude.")
    else:
        raw_path = out_dir / "seoul_bike_sample.txt"
        write_text(raw_path, text)
        notes.append("Expected JSON but received a non-JSON response.")

    return ProbeResult(
        name="seoul_bike_realtime_station",
        url=redact_url(url, [api_key]),
        status_code=status_code,
        response_format=response_format,
        ok=ok,
        field_paths=field_paths,
        notes=notes,
        raw_file=str(raw_path),
    )


def probe_tago_personal_mobility(out_dir: Path) -> ProbeResult:
    service_key = os.getenv("OPEN_DATA_PORTAL_API_KEY") or os.getenv("DATA_GO_KR_SERVICE_KEY")
    target_city = "서울"
    provider_params = {"numOfRows": "1000", "pageNo": "1", "_type": "json"}
    notes = [
        "This probes TAGO PersonalMobilityInfo/GetPMProvider from the official guide.",
    ]
    if service_key:
        provider_params["serviceKey"] = service_key
    else:
        notes.append("OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY is not set.")

    query = urlencode(provider_params)
    url = f"{TAGO_PM_PROVIDER_URL}?{query}"
    redacted_url = redact_url(url, [service_key or ""])

    status_code, text, transport_notes = http_get(url, [service_key or ""])
    notes.extend(transport_notes)
    cached_text_path = out_dir / "tago_gateway_probe.txt"
    if not text.strip() and cached_text_path.exists():
        text = cached_text_path.read_text(encoding="utf-8")
        if status_code == 0:
            status_code = None
        notes.append("Used cached raw API response from tago_gateway_probe.txt.")
    if status_code is None and not text:
        return ProbeResult(
            name="tago_personal_mobility",
            url=redacted_url,
            status_code=None,
            response_format="request_error",
            ok=False,
            field_paths=[],
            notes=notes or ["Request failed before a response body was received."],
        )

    response_format = detect_response_format(text)
    raw_path = out_dir / (
        "tago_pm_provider_probe.json" if response_format == "json" else "tago_pm_provider_probe.txt"
    )
    field_paths: list[str] = []
    ok = False

    if response_format == "json":
        payload = json.loads(text)
        write_json(raw_path, payload)
        field_paths = flatten_field_paths(payload)
        providers = response_items(payload)
        notes.append(f"Provider rows returned: {len(providers)}")
        if providers:
            cities = sorted({str(row.get("cityname") or row.get("cityName")) for row in providers})
            notes.append(f"Provider cities returned: {', '.join(cities[:20])}")
            target_providers = [
                row for row in providers if city_matches(row.get("cityname") or row.get("cityName"), target_city)
            ]
            notes.append(f"Target city provider rows for {target_city}: {len(target_providers)}")
            first = target_providers[0] if target_providers else providers[0]
            notes.append(
                f"Selected provider from API: {first.get('cityname')}/{first.get('citycode')} {first.get('typename') or first.get('providerName') or first.get('providername')}"
            )
            if service_key and target_providers:
                list_params = {
                    "serviceKey": service_key,
                    "providerName": first.get("kprovidername") or first.get("providerName") or first.get("providername"),
                    "cityCode": first.get("citycode") or first.get("cityCode"),
                    "numOfRows": "5",
                    "pageNo": "1",
                    "_type": "json",
                }
                list_url = f"{TAGO_PM_LIST_URL}?{urlencode(list_params)}"
                list_status, list_text, list_notes = http_get(list_url, [service_key])
                notes.extend(list_notes)
                list_format = detect_response_format(list_text)
                list_raw_path = out_dir / (
                    "tago_pm_list_probe.json" if list_format == "json" else "tago_pm_list_probe.txt"
                )
                if list_format == "json":
                    list_payload = json.loads(list_text)
                    write_json(list_raw_path, list_payload)
                    list_rows = response_items(list_payload)
                    notes.append(f"PM list rows returned for first provider: {len(list_rows)}")
                    field_paths.extend(flatten_field_paths(list_payload)[:80])
                    ok = list_status == 200 and len(list_rows) > 0
                else:
                    write_text(list_raw_path, list_text)
                    notes.append(f"PM list response was {list_format}, status={list_status}.")
            elif providers:
                ok = status_code == 200
                notes.append(f"No {target_city} provider was found in the all-city provider response.")
            else:
                ok = False
    else:
        write_text(raw_path, text)
        notes.append(f"Raw response body starts with: {text[:80]!r}")

    return ProbeResult(
        name="tago_personal_mobility",
        url=redacted_url,
        status_code=status_code,
        response_format=response_format,
        ok=ok,
        field_paths=field_paths,
        notes=notes,
        raw_file=str(raw_path),
    )


def build_findings(results: list[ProbeResult]) -> str:
    lines = [
        "# API Probe Findings",
        "",
        "## Summary",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"### {result.name}",
                "",
                f"- URL: `{result.url}`",
                f"- HTTP status: `{result.status_code}`",
                f"- Response format: `{result.response_format}`",
                f"- Usable response: `{result.ok}`",
                f"- Raw file: `{result.raw_file}`",
                "",
                "Notes:",
            ]
        )
        lines.extend(f"- {note}" for note in result.notes)
        if result.field_paths:
            lines.extend(["", "Observed field paths:"])
            lines.extend(f"- `{path}`" for path in result.field_paths[:80])
        lines.append("")

    lines.extend(
        [
            "## Planning Impact",
            "",
            "- Seoul Bike station coordinates are API-solvable through `bikeList`.",
            "- Seoul Bike trip history is not returned by `bikeList`; historical rental files are still required.",
            "- TAGO PersonalMobilityInfo can provide provider, vehicleID, battery, latitude, and longitude fields.",
            "- The current provider API response must still be checked against the configured target city before using it for Seoul modeling.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/api_probe")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()
    load_dotenv(args.env)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [
        probe_seoul_bike(out_dir),
        probe_tago_personal_mobility(out_dir),
    ]

    write_json(out_dir / "api_probe_summary.json", [asdict(result) for result in results])
    write_text(out_dir / "api_probe_findings.md", build_findings(results))

    for result in results:
        status = "ok" if result.ok else "not usable"
        print(f"{result.name}: {status} ({result.response_format}, status={result.status_code})")
    print(f"Wrote {out_dir / 'api_probe_summary.json'}")


if __name__ == "__main__":
    main()
