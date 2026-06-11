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

The official TAGO website describes a public transport integrated information service and exposes Open API application flow. The currently visible TAGO categories are public transport modes, not shared PM device snapshots.

A known TAGO public-data gateway endpoint was probed without a service key:

```text
http://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList
```

Observed unauthenticated response:

```text
Unauthorized
```

This confirms the data.go.kr/TAGO gateway requires a service key. It does not confirm that the spec-required shared PM fields are publicly available.

The spec-required TAGO PM data requires per-device or per-snapshot fields:

```text
timestamp, operator_name, device_id, battery_level, latitude, longitude
```

At this point, the public TAGO page and unauthenticated gateway probe do not expose a verified API that returns those fields. If the actual shared PM endpoint exists behind data.go.kr authorization, we need the subscribed API URL and a `DATA_GO_KR_SERVICE_KEY` to validate it.

## Data Feasibility Against `Spec.md`

| Required input | API feasibility | Current status |
| --- | --- | --- |
| Seoul administrative dong boundaries | Possible via separate boundary dataset, not TAGO/bikeList | Need `data/raw/seoul_admin_dong.geojson` or a confirmed boundary API |
| Seoul Bike station coordinates | API-solvable | Confirmed via Seoul `bikeList` |
| Seoul Bike rental history | Not solved by `bikeList` | Need historical trip CSV/file API |
| TAGO shared PM device snapshots | Unconfirmed | Need real PM endpoint + data.go.kr service key |
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

To start accumulating real shared-PM snapshots, also provide the actual TAGO/shared-PM endpoint:

```bash
TAGO_PM_API_URL="https://..."
```

`OPEN_DATA_PORTAL_API_KEY` is injected as `serviceKey` automatically.

## Data Input Part

The data input layer only collects and normalizes raw inputs. It does not run the optimization model.

```bash
python3 src/data_input.py --snapshot-label now
```

This writes:

```text
data/raw/seoul_bike_stations.csv
data/raw/seoul_private_pm_operator_summary.csv
data/raw/tago_pm_snapshots_<label>.csv    # only when TAGO_PM_API_URL is configured
data/raw/api/
data/raw/snapshot_manifest.jsonl
```

For the next week, run the same command repeatedly near the target decision window, ideally 03:30-04:30:

```bash
python3 src/data_input.py --snapshot-label "$(date +%Y%m%dT%H%M%S)"
```

Each successful TAGO call appends one normalized PM snapshot file. As files accumulate, the model gets better estimates for:

- `x_obs_i`: average observed GCOO placement
- `competitor_count_is`: competitor density by scenario day
- `K_i`: capacity from observed PM supply percentiles
- scenario variation across days

## Model Part

The model layer does not call external APIs. It reads accumulated files from `data/raw` and produces processed model artifacts.

```bash
python3 src/model.py --out outputs/model
```

If no real TAGO PM snapshot file exists yet, the model writes a readiness report instead of pretending the data exists:

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

The prototype uses a tiny fixture for TAGO PM snapshots because the real shared PM API is not verified yet. That fixture is isolated in `src/prototype_pipeline.py` and should be replaced by `data/raw/tago_pm_snapshots_*.csv` once the real API is available.
