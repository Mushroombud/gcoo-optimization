# Model Readiness

- Can optimize: true
- Used surrogate PM: true
- PM-like trip rows: 5682
- Demand scenario rows: 400
- PM scenario rows: 426
- Allocated scooters: 5
- Active dongs: 2

Notes:
- Loaded Seoul administrative dong boundary file: data/raw/seoul_admin_dong.geojson
- Loaded Seoul Bike station file: data/raw/seoul_bike_stations.csv
- Mapped 2718/2721 bike stations to real dong polygons.
- Loaded 1 Seoul Bike trip file(s), rows=11989.
- Filtered TAGO PM rows by city_name=서울: 4165 -> 0.
- Loaded 1 TAGO PM snapshot file(s).
- Mapped 70236/283998 tow events to dong by address text.
- Mapped 46/329 parking zones to dong by address text.
- Built surrogate PM scenario from Seoul Bike demand, tow events, parking zones, and private PM operator summary.
- Surrogate counts are planning priors, not live per-device TAGO observations.