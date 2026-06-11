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


SEOUL_BIKE_URL_TEMPLATE = "http://openapi.seoul.go.kr:8088/{key}/json/bikeList/1/5/"
TAGO_BUS_STOP_URL = (
    "http://apis.data.go.kr/1613000/BusSttnInfoInqireService/"
    "getCrdntPrxmtSttnList"
)


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


def http_get(url: str) -> tuple[int | None, str, list[str]]:
    notes: list[str] = []
    try:
        response = requests.get(url, timeout=20)
        return response.status_code, response.text, notes
    except requests.RequestException as exc:
        notes.append(f"requests failed: {exc}")

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


def probe_seoul_bike(out_dir: Path) -> ProbeResult:
    api_key = os.getenv("SEOUL_OPEN_API_KEY", "sample")
    url = SEOUL_BIKE_URL_TEMPLATE.format(key=api_key)
    notes: list[str] = []

    status_code, text, transport_notes = http_get(url)
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


def probe_tago_gateway(out_dir: Path) -> ProbeResult:
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY")
    params = {
        "gpsLati": "37.5665",
        "gpsLong": "126.9780",
        "numOfRows": "5",
        "pageNo": "1",
        "_type": "json",
    }
    notes = [
        "This probes a known TAGO/data.go.kr gateway shape, not a shared PM endpoint.",
        "The spec-required shared PM endpoint is still unverified.",
    ]
    if service_key:
        params["serviceKey"] = service_key
    else:
        notes.append("DATA_GO_KR_SERVICE_KEY is not set; unauthenticated calls are expected to fail.")

    query = urlencode(params)
    url = f"{TAGO_BUS_STOP_URL}?{query}"
    redacted_url = redact_url(url, [service_key or ""])

    status_code, text, transport_notes = http_get(url)
    notes.extend(transport_notes)
    cached_text_path = out_dir / "tago_gateway_probe.txt"
    if not text.strip() and cached_text_path.exists():
        text = cached_text_path.read_text(encoding="utf-8")
        if status_code == 0:
            status_code = None
        notes.append("Used cached raw API response from tago_gateway_probe.txt.")
    if status_code is None and not text:
        return ProbeResult(
            name="tago_data_go_kr_gateway",
            url=redacted_url,
            status_code=None,
            response_format="request_error",
            ok=False,
            field_paths=[],
            notes=notes or ["Request failed before a response body was received."],
        )

    response_format = detect_response_format(text)
    raw_path = out_dir / (
        "tago_gateway_probe.json" if response_format == "json" else "tago_gateway_probe.txt"
    )
    field_paths: list[str] = []
    ok = False

    if response_format == "json":
        payload = json.loads(text)
        write_json(raw_path, payload)
        field_paths = flatten_field_paths(payload)
        ok = "response" in payload
        notes.append("Authenticated TAGO-like JSON response received.")
    else:
        write_text(raw_path, text)
        notes.append(f"Raw response body starts with: {text[:80]!r}")

    return ProbeResult(
        name="tago_data_go_kr_gateway",
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
            "- TAGO/data.go.kr requires `DATA_GO_KR_SERVICE_KEY` for usable responses.",
            "- A TAGO shared PM per-device snapshot endpoint was not verified from public unauthenticated sources.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/api_probe")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [
        probe_seoul_bike(out_dir),
        probe_tago_gateway(out_dir),
    ]

    write_json(out_dir / "api_probe_summary.json", [asdict(result) for result in results])
    write_text(out_dir / "api_probe_findings.md", build_findings(results))

    for result in results:
        status = "ok" if result.ok else "not usable"
        print(f"{result.name}: {status} ({result.response_format}, status={result.status_code})")
    print(f"Wrote {out_dir / 'api_probe_summary.json'}")


if __name__ == "__main__":
    main()
