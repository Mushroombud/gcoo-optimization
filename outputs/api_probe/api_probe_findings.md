# API Probe Findings

## Summary

### seoul_bike_realtime_station

- URL: `http://openapi.seoul.go.kr:8088/***/json/bikeList/1/5/`
- HTTP status: `200`
- Response format: `json`
- Usable response: `True`
- Raw file: `outputs/api_probe/seoul_bike_sample.json`

Notes:
- Rows returned: 5
- Usable for station_id, station_name, latitude, longitude.

Observed field paths:
- `rentBikeStatus.list_total_count`
- `rentBikeStatus.RESULT.CODE`
- `rentBikeStatus.RESULT.MESSAGE`
- `rentBikeStatus.row[].rackTotCnt`
- `rentBikeStatus.row[].stationName`
- `rentBikeStatus.row[].parkingBikeTotCnt`
- `rentBikeStatus.row[].shared`
- `rentBikeStatus.row[].stationLatitude`
- `rentBikeStatus.row[].stationLongitude`
- `rentBikeStatus.row[].stationId`

### tago_personal_mobility

- URL: `https://apis.data.go.kr/1613000/PersonalMobilityInfo/GetPMProvider?numOfRows=1000&pageNo=1&_type=json&serviceKey=***`
- HTTP status: `200`
- Response format: `json`
- Usable response: `True`
- Raw file: `outputs/api_probe/tago_pm_provider_probe.json`

Notes:
- This probes TAGO PersonalMobilityInfo/GetPMProvider from the official guide.
- Provider rows returned: 2
- Provider cities returned: 세종특별시
- Target city provider rows for 서울: 0
- Selected provider from API: 세종특별시/12 ALPACA
- No 서울 provider was found in the all-city provider response.

Observed field paths:
- `response.header.resultCode`
- `response.header.resultMsg`
- `response.body.items.item[].citycode`
- `response.body.items.item[].cityname`
- `response.body.items.item[].providername`
- `response.body.numOfRows`
- `response.body.pageNo`
- `response.body.totalCount`

## Planning Impact

- Seoul Bike station coordinates are API-solvable through `bikeList`.
- Seoul Bike trip history is not returned by `bikeList`; historical rental files are still required.
- TAGO PersonalMobilityInfo can provide provider, vehicleID, battery, latitude, and longitude fields.
- The current provider API response must still be checked against the configured target city before using it for Seoul modeling.