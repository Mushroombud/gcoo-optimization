from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from common import ensure_dir, write_json


DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20250401/HangJeongDong_ver20250401.geojson"
)
DEFAULT_OUT = "data/raw/seoul_admin_dong.geojson"


CODE_KEYS = [
    "dong_id",
    "adm_cd",
    "ADM_CD",
    "adm_cd2",
    "ADSTRD_CD",
    "HCODE",
    "code",
]
NAME_KEYS = [
    "dong_name",
    "adm_nm",
    "ADM_NM",
    "adm_nm2",
    "emd_nm",
    "name",
]


def first_property(properties: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = properties.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def is_seoul_feature(feature: dict[str, Any]) -> bool:
    properties = feature.get("properties", {})
    values = [str(value) for value in properties.values() if value is not None]
    if any("서울" in value for value in values):
        return True

    code = first_property(properties, CODE_KEYS)
    return bool(code and code.startswith("11"))


def normalize_properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = dict(feature.get("properties", {}))
    code = first_property(properties, CODE_KEYS)
    name = first_property(properties, NAME_KEYS)

    if code:
        properties["dong_id"] = code
    if name:
        parts = str(name).replace("서울특별시", "").strip().split()
        properties["dong_name"] = parts[-1] if parts else str(name)
        if len(parts) >= 2:
            properties["gu_name"] = parts[-2]
    properties["source"] = "HangJeongDong_ver20250401"
    return properties


def build_seoul_geojson(source: dict[str, Any]) -> dict[str, Any]:
    features = []
    for feature in source.get("features", []):
        if not is_seoul_feature(feature):
            continue
        copied = {
            "type": "Feature",
            "properties": normalize_properties(feature),
            "geometry": feature.get("geometry"),
        }
        features.append(copied)

    if not features:
        raise RuntimeError("No Seoul features were found in the source GeoJSON.")
    return {"type": "FeatureCollection", "features": features}


def fetch_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and filter Seoul administrative dong boundaries."
    )
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    out_path = Path(args.out)
    ensure_dir(out_path.parent)
    source = fetch_json(args.source_url)
    seoul_geojson = build_seoul_geojson(source)
    write_json(out_path, seoul_geojson)

    print(f"boundary={out_path}")
    print(f"features={len(seoul_geojson['features'])}")
    print(f"source={args.source_url}")


if __name__ == "__main__":
    main()
