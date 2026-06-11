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

Visualization is the main inspection surface for this project. The current setup generates two standalone interactive HTML files:

1. `charts_dashboard.html`: numeric charts from optimization/scenario CSVs.
2. `seoul_map.html`: Seoul map overlay with administrative dong boundaries, heatmaps, and bike station markers.

The setup intentionally uses Python packages instead of adding a React app:

- `pyecharts`: Apache ECharts-backed interactive chart dashboard.
- `folium`: Leaflet-backed map overlay and heatmap rendering.
- `branca`: color scale support for map overlays.

### 1. Install visualization dependencies

Run this once from the repository root:

```bash
python3 -m pip install -r requirements.txt
```

This installs the normal data/model dependencies plus:

```text
pyecharts
folium
branca
```

### 2. Prepare Seoul administrative dong boundaries

The map visualizer looks for this file:

```text
data/raw/seoul_admin_dong.geojson
```

Generate it with:

```bash
python3 src/fetch_seoul_boundary.py --out data/raw/seoul_admin_dong.geojson
```

Expected output:

```text
boundary=data/raw/seoul_admin_dong.geojson
features=426
source=https://raw.githubusercontent.com/vuski/admdongkor/master/ver20250401/HangJeongDong_ver20250401.geojson
```

If this file is missing, `src/visualize.py` still runs by falling back to the tiny fixture bounding boxes from `src/prototype_pipeline.py`. That fallback is only for smoke testing. Use the real GeoJSON for any serious visual inspection.

### 3. Generate data to visualize

For a quick local smoke test, generate fixture-backed prototype outputs:

```bash
python3 src/prototype_pipeline.py --out outputs/prototype
```

For real model outputs, run the model pipeline instead:

```bash
python3 src/model.py --out outputs/model
```

If real Seoul-matching TAGO PM snapshots are not available yet, the model may only write a readiness report. In that case, use `outputs/prototype` or `outputs/model_fixture` for visual smoke testing:

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
```

### 4. Render the visualization HTML

Render both chart and map outputs from a model/prototype output directory:

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations
```

or:

```bash
python3 src/visualize.py --input outputs/model --out outputs/visualizations
```

Expected output:

```text
charts=outputs/visualizations/charts_dashboard.html
map=outputs/visualizations/seoul_map.html
map_metric=<selected_metric>
boundary_source=geojson
```

Generated files:

```text
outputs/visualizations/charts_dashboard.html
outputs/visualizations/seoul_map.html
outputs/visualizations/visualization_manifest.json
```

`visualization_manifest.json` records which input directory was used, which tables were loaded, which map metric was selected, and whether the map used real GeoJSON boundaries or fixture bounding boxes.

### 5. Choose the map overlay metric

By default, the visualizer uses `--map-metric auto`. Auto mode picks the first useful metric with non-zero values in this order:

```text
x_star_i
mean_H
mean_total_pm_count
mean_competitor_count
x_obs_i
K_i
```

Override the metric explicitly when inspecting a specific question:

```bash
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric x_star_i
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric mean_H
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric mean_competitor_count
```

Common metric meanings:

| Metric | Meaning |
| --- | --- |
| `x_star_i` | optimized GCOO scooter placement by dong |
| `mean_H` | average PM-like demand by dong |
| `mean_competitor_count` | average competitor PM count by dong |
| `mean_gcoo_count` | average observed GCOO PM count by dong |
| `K_i` | dong capacity used by the optimization model |
| `B_i` | imbalance score from arrivals/departures |

### 6. Open the visualization in a browser

The output files are static HTML. The most reliable way to inspect them is to serve the repo locally:

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:8765/outputs/visualizations/charts_dashboard.html
http://127.0.0.1:8765/outputs/visualizations/seoul_map.html
```

If port `8765` is already in use, choose another port:

```bash
python3 -m http.server 8770 --bind 127.0.0.1
```

You can stop the server with `Ctrl+C`.

### 7. Visualization input contract

`src/visualize.py` expects the selected `--input` directory to contain the CSV outputs written by `src/model.py` or `src/prototype_pipeline.py`.

| File | Required | Used for |
| --- | --- | --- |
| `allocation_optimized.csv` | yes for allocation charts | optimized placement chart and `x_star_i` map layer |
| `model_inputs.csv` | recommended | capacity, observed supply, imbalance diagnostics |
| `demand_scenario.csv` | recommended | demand charts and `mean_H` map layer |
| `tago_scenario.csv` | recommended | GCOO/competitor count charts |
| `bike_stations_with_dong.csv` or `bike_stations_normalized.csv` | recommended | station markers and station heatmap |
| `dong_master.csv` | recommended | readable dong labels |

Optional files:

| File | Used for |
| --- | --- |
| `data/raw/seoul_admin_dong.geojson` | real Seoul administrative dong polygon overlay |
| `data/raw/tago_pm_snapshots_*.csv` | raw PM point heatmap when available |

### 8. Troubleshooting

If the map prints `boundary_source=fixture_bbox`, the real Seoul boundary file was not found. Run:

```bash
python3 src/fetch_seoul_boundary.py --out data/raw/seoul_admin_dong.geojson
```

If the map renders but the overlay values are all zero, the selected `--map-metric` is probably missing or zero in the input CSVs. Try:

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations --map-metric mean_H
```

If `outputs/model` only contains `model_readiness.json`, the model did not have enough real input data to optimize. Use fixture mode for visualization smoke testing:

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
python3 src/visualize.py --input outputs/model_fixture --out outputs/visualizations
```

If browser loading looks stale, rerun the visualizer and refresh the browser page:

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations
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
