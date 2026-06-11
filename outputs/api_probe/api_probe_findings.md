# API Probe Findings

## Summary

### seoul_bike_realtime_station

- URL: `http://openapi.seoul.go.kr:8088/***/json/bikeList/1/5/`
- HTTP status: `None`
- Response format: `json`
- Usable response: `True`
- Raw file: `outputs/api_probe/seoul_bike_sample.json`

Notes:
- requests failed: HTTPConnectionPool(host='openapi.seoul.go.kr', port=8088): Max retries exceeded with url: /sample/json/bikeList/1/5/ (Caused by NameResolutionError("HTTPConnection(host='openapi.seoul.go.kr', port=8088): Failed to resolve 'openapi.seoul.go.kr' ([Errno 8] nodename nor servname provided, or not known)"))
- curl fallback exited with code 6: 
- Used cached raw API response from seoul_bike_sample.json.
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

### tago_data_go_kr_gateway

- URL: `http://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList?gpsLati=37.5665&gpsLong=126.9780&numOfRows=5&pageNo=1&_type=json`
- HTTP status: `None`
- Response format: `text`
- Usable response: `False`
- Raw file: `outputs/api_probe/tago_gateway_probe.txt`

Notes:
- This probes a known TAGO/data.go.kr gateway shape, not a shared PM endpoint.
- The spec-required shared PM endpoint is still unverified.
- DATA_GO_KR_SERVICE_KEY is not set; unauthenticated calls are expected to fail.
- requests failed: HTTPConnectionPool(host='apis.data.go.kr', port=80): Max retries exceeded with url: /1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList?gpsLati=37.5665&gpsLong=126.9780&numOfRows=5&pageNo=1&_type=json (Caused by NameResolutionError("HTTPConnection(host='apis.data.go.kr', port=80): Failed to resolve 'apis.data.go.kr' ([Errno 8] nodename nor servname provided, or not known)"))
- curl fallback exited with code 6: 
- Used cached raw API response from tago_gateway_probe.txt.
- Raw response body starts with: 'Unauthorized\n'

## Planning Impact

- Seoul Bike station coordinates are API-solvable through `bikeList`.
- Seoul Bike trip history is not returned by `bikeList`; historical rental files are still required.
- TAGO/data.go.kr requires `DATA_GO_KR_SERVICE_KEY` for usable responses.
- A TAGO shared PM per-device snapshot endpoint was not verified from public unauthenticated sources.