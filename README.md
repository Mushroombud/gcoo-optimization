# GCOO Seoul Placement Prototype

This repository starts from `Spec.md` and provides a minimal prototype for:

1. probing the currently reachable public APIs,
2. documenting which required planning data can and cannot be obtained by API,
3. converting API-like inputs into the tables required by the optimization spec,
4. running a tiny end-to-end allocation example.

## Current API Findings

### Seoul Bike / Seoul Open Data

Confirmed reachable with the Seoul Open Data sample key:

```text
http://openapi.seoul.go.kr:8088/sample/json/bikeList/1/5/
```

Observed response shape:

```json
{
  "rentBikeStatus": {
    "list_total_count": 5,
    "RESULT": { "CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다." },
    "row": [
      {
        "rackTotCnt": "15",
        "stationName": "102. 망원역 1번출구 앞",
        "parkingBikeTotCnt": "16",
        "shared": "107",
        "stationLatitude": "37.55564880",
        "stationLongitude": "126.91062927",
        "stationId": "ST-4"
      }
    ]
  }
}
```

This can provide Seoul Bike station IDs, names, latitude, and longitude. It is useful for `data/raw/seoul_bike_stations.csv`.

It does not provide trip history. The spec needs rental timestamp, return timestamp, origin station, destination station, distance, and duration. That must come from Seoul Bike rental history file data or another historical-trip source.

### TAGO / data.go.kr

The bundled Word guide `오픈API활용가이드_국토교통부(TAGO)_퍼스널모빌리티정보v1.1.docx` documents the actual PM API:

```text
Service: http://apis.data.go.kr/1613000/PersonalMobilityInfo
Provider list: /GetPMProvider
PM list: /GetPMListByProvider
```

Confirmed response fields from the guide and live probe:

```text
providerName, vehicleID, battery, cityCode, cityName, latitude, longitude
```

The implemented flow is:

```text
GetPMProvider with cityName omitted -> all providerName/cityCode pairs
Filter provider rows where cityName matches target_city_name=서울
GetPMListByProvider(providerName, cityCode) -> live rideable PM devices
```

Current live behavior: even with `cityName` omitted as the guide specifies for all-city lookup, the API currently returns only `세종특별시` providers (`ALPACA`, `GBIKE`). The data input code writes both the all-provider CSV and the target-city provider CSV, then only calls PM list endpoints for providers whose `cityName` matches Seoul. As of the latest run, TAGO API works, but no Seoul PM provider row is exposed by this endpoint.

## Data Feasibility Against `Spec.md`

| Required input | API feasibility | Current status |
| --- | --- | --- |
| Seoul administrative dong boundaries | Possible via separate boundary dataset, not TAGO/bikeList | Need `data/raw/seoul_admin_dong.geojson` or a confirmed boundary API |
| Seoul Bike station coordinates | API-solvable | Confirmed via Seoul `bikeList` |
| Seoul Bike rental history | Not solved by `bikeList` | Need historical trip CSV/file API |
| TAGO shared PM device snapshots | API-solvable where city data is exposed | `PersonalMobilityInfo` works; all-city provider lookup currently exposes Sejong, not Seoul |
| GCOO pricing/cost | Not public API data | Config assumptions |

## Required Keys

`.env` supports these keys:

```bash
SEOUL_API_KEY="..."
OPEN_DATA_PORTAL_API_KEY="..."
```

Legacy aliases are still accepted:

```bash
SEOUL_OPEN_API_KEY="..."
DATA_GO_KR_SERVICE_KEY="..."
```

`OPEN_DATA_PORTAL_API_KEY` is injected as `serviceKey` automatically for TAGO `PersonalMobilityInfo`.

## Data Input Part

The data input layer only collects and normalizes raw inputs. It does not run the optimization model.

```bash
python3 src/data_input.py --snapshot-label now
```

This writes:

```text
data/raw/seoul_bike_stations.csv
data/raw/seoul_private_pm_operator_summary.csv
data/raw/api/tago_pm/providers_all_<label>.csv
data/raw/api/tago_pm/providers_<label>.csv
data/raw/tago_pm_snapshots_<label>.csv    # only when TAGO returns providers matching target_city_name
data/raw/api/
data/raw/snapshot_manifest.jsonl
```

For the next week, run the same command repeatedly near the target decision window, ideally 03:30-04:30:

```bash
python3 src/data_input.py --snapshot-label "$(date +%Y%m%dT%H%M%S)"
```

Each successful TAGO call appends one normalized PM snapshot file after city validation. As files accumulate, the model gets better estimates for:

- `x_obs_i`: average observed GCOO placement
- `competitor_count_is`: competitor density by scenario day
- `K_i`: capacity from observed PM supply percentiles
- scenario variation across days

## Model Part

The model layer does not call external APIs. It reads accumulated files from `data/raw` and produces processed model artifacts.

```bash
python3 src/model.py --out outputs/model
```

If no real Seoul-matching TAGO PM snapshot file exists yet, the model writes a readiness report instead of pretending the data exists:

```text
outputs/model/model_readiness.json
```

For smoke-testing only, use:

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
```

Combined one-shot command:

```bash
python3 src/run_pipeline.py --snapshot-label now --out outputs/latest_run
```

## Visualization Setup

Visualization is split into two interactive HTML outputs:

1. numeric charts from optimization/scenario CSVs,
2. Seoul map visualizations with dong overlays, heatmaps, and bike station markers.

The setup uses Python packages instead of adding a React app:

- `pyecharts`: Python wrapper for Apache ECharts, used for interactive chart dashboards.
- `folium`: Python wrapper for Leaflet, used for Seoul map overlay and heatmap HTML.

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Generate fixture-backed prototype outputs and then render both visualization surfaces:

```bash
python3 src/prototype_pipeline.py --out outputs/prototype
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations
```

Generated files:

```text
outputs/visualizations/charts_dashboard.html
outputs/visualizations/seoul_map.html
outputs/visualizations/visualization_manifest.json
```

For real Seoul administrative dong boundaries, place a GeoJSON at:

```text
data/raw/seoul_admin_dong.geojson
```

You can generate it from the bundled downloader:

```bash
python3 src/fetch_seoul_boundary.py --out data/raw/seoul_admin_dong.geojson
```

The visualizer will use that file automatically. Until it exists, `seoul_map.html` uses the current fixture dong bounding boxes from `src/prototype_pipeline.py`, so map generation still works for smoke testing. The map metric defaults to `auto`; override it when needed:

```bash
python3 src/visualize.py --input outputs/model --map-metric x_star_i
python3 src/visualize.py --input outputs/model --map-metric mean_H
python3 src/visualize.py --input outputs/model --map-metric mean_competitor_count
```

## Run

Probe reachable APIs:

```bash
python3 src/api_probe.py --out outputs/api_probe
```

Run the prototype transformation and tiny allocation:

```bash
python3 src/prototype_pipeline.py --out outputs/prototype
```

Generated files include:

```text
outputs/api_probe/api_probe_summary.json
outputs/api_probe/api_probe_findings.md
outputs/prototype/dong_master.csv
outputs/prototype/bike_stations_normalized.csv
outputs/prototype/bike_trip_pm_like.csv
outputs/prototype/demand_scenario.csv
outputs/prototype/tago_scenario.csv
outputs/prototype/model_inputs.csv
outputs/prototype/allocation_optimized.csv
outputs/prototype/prototype_report.md
```

The prototype uses a tiny fixture for smoke testing. The real pipeline uses `src/data_input.py` and city-validated `data/raw/tago_pm_snapshots_*.csv`.
