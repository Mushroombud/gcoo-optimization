# Seoul PM Data Catalog

This catalog records the data sources found for making the GCOO Seoul PM placement project feasible when TAGO Seoul PM snapshots are unavailable.

## Current Run Summary

Generated at the repository level with:

```bash
python3 src/data_input.py --source seoul-kickboard-tow --snapshot-label feasible_full
python3 src/model.py --out outputs/model
```

Actual local inputs now available:

| Local file | Rows | Role |
| --- | ---: | --- |
| `data/raw/seoul_admin_dong.geojson` | 426 dongs | Spatial unit and point-in-polygon join target |
| `data/raw/seoul_bike_stations.csv` | 2,721 | Station coordinates for Seoul Bike OD mapping |
| `data/raw/seoul_bike_trips_2025-10-01.csv` | 11,989 | Sample Seoul Bike trip history for PM-like demand |
| `data/raw/seoul_pm_tow_events.csv` | 283,998 | PM activity/friction proxy from kickboard towing events |
| `data/raw/seoul_kickboard_parking_zones.csv` | 329 | PM parking/placement constraint and activity prior |
| `data/raw/seoul_private_pm_operator_summary.csv` | 28 | Operator/brand/gu aggregate supply prior |

Generated model outputs:

| Output | Rows | Meaning |
| --- | ---: | --- |
| `outputs/model/dong_master.csv` | 426 | Administrative dong master |
| `outputs/model/bike_trip_pm_like.csv` | 5,682 | PM-like Seoul Bike trips after distance/duration/speed filters |
| `outputs/model/demand_scenario.csv` | 400 | Dong-level demand scenario |
| `outputs/model/tago_scenario.csv` | 426 | Surrogate PM supply scenario |
| `outputs/model/model_inputs.csv` | 426 | Optimization input table |
| `outputs/model/allocation_optimized.csv` | 426 | Prototype allocation result |

`outputs/model/model_readiness.json` reports `can_optimize=true` and `used_surrogate_pm=true`.

## Directly Usable Public Data

### Seoul Open Data: Kickboard Towing Events

- Service: `tbAutoKickboard`
- Local output: `data/raw/seoul_pm_tow_events.csv`
- Fields used: report date, district, address, towing type, action date.
- Use: PM activity/friction proxy. High towing volume is not usage count, but it is a strong signal of where shared PMs are present and creating operational incidents.
- Limitation: address text is not always dong-resolvable; current run mapped 70,236 of 283,998 events to a dong by address text.

### Seoul Open Data: Kickboard Parking Zones

- Service: `parkingKickboard`
- Local output: `data/raw/seoul_kickboard_parking_zones.csv`
- Fields used: district, address, detailed location, stand availability.
- Use: placement constraint/prior and weak PM activity signal.
- Limitation: Seoul city page notes that district-funded parking zones may exist separately on data.go.kr, so this should be supplemented with district files.

### Seoul Open Data: Seoul Bike Trip History

- Service: `tbCycleRentData`
- Local output: `data/raw/seoul_bike_trips_2025-10-01.csv`
- Fields used: rental/return timestamps, rental/return station IDs, distance, duration.
- Use: demand proxy. PM-like trips are filtered by distance, duration, and speed, then aggregated to origin dong.
- Limitation: current run intentionally sampled hours 8, 9, 18, 19 and 3 pages per hour. Full-day collection is possible by increasing `max_trip_pages_per_hour` and clearing/expanding `bike_trip_history_hours`.

### Seoul Open Data / Data.go.kr: Private PM Operator Summary

- Source file: `서울시 민간대여 공유 전동킥보드 기기 현황_25.12월기준.csv`
- Local output: `data/raw/seoul_private_pm_operator_summary.csv`
- Fields used: operator, brand, total device count, service district.
- Use: competitor supply prior by district, then distributed to dongs by the activity weight.
- Limitation: not a device snapshot and not usage data. It cannot identify GCOO placement by dong or time.

## Relevant Data Found But Not Integrated Yet

These are useful next-layer proxies when the first optimization loop needs stronger explanatory variables:

| Source | Use |
| --- | --- |
| `서울특별시_공공자전거 대여 이력` recent OpenAPI | Recent 7-day station/hour availability, useful for freshness checks but not full OD trips |
| `서울특별시_교통량 이력 정보` | Road segment traffic volume by time, possible safety/accessibility/friction feature |
| `서울특별시_실시간 도로 소통 정보` | Road speed and travel time, possible congestion/safety feature |
| Seoul Big Data `서울 생활인구` | Hourly floating population by area, demand normalization |
| Seoul Big Data `수도권 생활이동` | OD movement proxy, useful for commute/short-trip demand validation |
| District kickboard parking files on data.go.kr | Supplement city parking zones, especially district-funded zones |

The data.go.kr discovery result is saved in:

- `outputs/data_discovery/data_go_kr_pm_related_catalog.csv`
- `outputs/data_discovery/data_go_kr_pm_related_catalog.json`

The broad search includes false positives. Curate by title/provider before integrating.

## Currently Not Solvable From Public APIs

The following are not publicly available enough to satisfy the original TAGO-only design:

| Needed data | Status | Practical replacement |
| --- | --- | --- |
| Seoul TAGO live PM device snapshots | TAGO provider lookup currently returns no Seoul provider rows | Use surrogate PM scenario until TAGO or operator feed becomes available |
| GCOO per-device 04:00 placement | Not available from public data | Use configured fallback GCOO supply and internal GCOO logs later |
| Competitor per-device placement by dong/time | Not available from public data | Use private PM operator summary + towing + parking + demand prior |
| PM ride OD/usage records | Not available publicly | Use PM-like Seoul Bike OD trips plus optional population/movement proxies |

## Prototype Modeling Strategy

The model now separates data input from modeling:

1. `src/data_input.py` collects public sources and writes normalized raw CSVs.
2. `src/model.py` loads accumulated files and builds demand/PM/model input tables.
3. Real TAGO snapshots are used first if Seoul rows exist.
4. If TAGO has no Seoul rows, `model.surrogate_pm` builds `tago_scenario.csv` from:
   - Seoul Bike PM-like demand score,
   - kickboard towing score,
   - kickboard parking-zone score,
   - private PM operator district supply prior,
   - configured GCOO fallback supply.

This makes the project executable now while keeping a clean path for replacing the surrogate with real TAGO or GCOO internal data later.
