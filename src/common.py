from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests


KST = ZoneInfo("Asia/Seoul")


@dataclass
class HttpResult:
    url: str
    redacted_url: str
    status_code: int | None
    text: str
    notes: list[str]


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def env_first(names: list[str], default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def now_kst_label() -> str:
    return datetime.now(KST).strftime("%Y%m%dT%H%M%S%z")


def now_kst_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def redact(text: str, secrets: list[str | None]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, value: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def build_url(url: str, params: dict[str, Any] | None = None) -> str:
    clean_params = {
        key: value
        for key, value in (params or {}).items()
        if value is not None and value != ""
    }
    if not clean_params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(clean_params)}"


def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    secrets: list[str | None] | None = None,
    timeout_seconds: int = 30,
) -> HttpResult:
    full_url = build_url(url, params)
    secret_values = secrets or []
    notes: list[str] = []

    redacted_url = redact(full_url, secret_values)

    try:
        response = requests.get(full_url, timeout=timeout_seconds)
        return HttpResult(
            url=full_url,
            redacted_url=redacted_url,
            status_code=response.status_code,
            text=response.text,
            notes=notes,
        )
    except requests.RequestException as exc:
        notes.append(f"requests failed: {redact(str(exc), secret_values)}")

    curl_cmd = [
        "curl",
        "-sL",
        "-A",
        "Mozilla/5.0",
        "-w",
        "\n__HTTP_STATUS__:%{http_code}",
        full_url,
    ]
    try:
        completed = subprocess.run(
            curl_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        notes.append(f"curl fallback failed: {exc}")
        return HttpResult(
            url=full_url,
            redacted_url=redacted_url,
            status_code=None,
            text="",
            notes=notes,
        )

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
        notes.append(f"curl fallback exited with code {completed.returncode}")
    else:
        notes.append("Used curl fallback after requests failed.")

    return HttpResult(
        url=full_url,
        redacted_url=redacted_url,
        status_code=status_code,
        text=text,
        notes=notes,
    )


def detect_response_format(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<"):
        return "xml"
    return "text"


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    candidate_paths = [
        ("response", "body", "items", "item"),
        ("response", "body", "item"),
        ("body", "items", "item"),
        ("items", "item"),
        ("row",),
    ]
    for path in candidate_paths:
        value: Any = payload
        for part in path:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]

    for value in payload.values():
        if isinstance(value, dict) and isinstance(value.get("row"), list):
            return [row for row in value["row"] if isinstance(row, dict)]
    return []


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
