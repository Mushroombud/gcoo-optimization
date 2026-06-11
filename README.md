# GCOO 서울 배치 프로토타입

이 저장소는 `Spec.md`에서 출발해 다음을 수행하는 최소 프로토타입을 제공합니다.

1. 현재 접근 가능한 공공 API 확인
2. 최적화 계획에 필요한 데이터 중 API로 확보 가능한 항목과 불가능한 항목 문서화
3. API 형태의 입력을 최적화 명세가 요구하는 테이블로 변환
4. 작은 end-to-end 배치 예제 실행

## 현재 API 확인 결과

### 서울 공공자전거 / 서울 열린데이터광장

서울 열린데이터광장 샘플 키로 접근 가능함을 확인했습니다.

```text
http://openapi.seoul.go.kr:8088/sample/json/bikeList/1/5/
```

관측된 응답 형태:

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

이 API는 서울 공공자전거 대여소 ID, 이름, 위도, 경도를 제공할 수 있습니다. `data/raw/seoul_bike_stations.csv` 생성에 유용합니다.

하지만 이동 이력은 제공하지 않습니다. 명세에는 대여 시각, 반납 시각, 출발 대여소, 도착 대여소, 거리, 이용 시간이 필요합니다. 이 데이터는 서울 공공자전거 대여 이력 파일 데이터나 다른 과거 이동 이력 소스에서 확보해야 합니다.

### TAGO / data.go.kr

동봉된 Word 가이드 `오픈API활용가이드_국토교통부(TAGO)_퍼스널모빌리티정보v1.1.docx`에는 실제 PM API가 다음과 같이 문서화되어 있습니다.

```text
Service: http://apis.data.go.kr/1613000/PersonalMobilityInfo
Provider list: /GetPMProvider
PM list: /GetPMListByProvider
```

가이드와 실제 호출에서 확인한 응답 필드:

```text
providerName, vehicleID, battery, cityCode, cityName, latitude, longitude
```

구현된 흐름:

```text
GetPMProvider with cityName omitted -> all providerName/cityCode pairs
Filter provider rows where cityName matches target_city_name=서울
GetPMListByProvider(providerName, cityCode) -> live rideable PM devices
```

현재 실제 동작: 가이드에 따르면 전체 도시 조회를 위해 `cityName`을 생략해야 하지만, API는 현재 `세종특별시` 공급자(`ALPACA`, `GBIKE`)만 반환합니다. 데이터 입력 코드는 전체 공급자 CSV와 대상 도시 공급자 CSV를 모두 쓰고, `cityName`이 서울과 일치하는 공급자에 대해서만 PM 목록 엔드포인트를 호출합니다. 최신 실행 기준으로 TAGO API 자체는 동작하지만, 이 엔드포인트는 서울 PM 공급자 행을 노출하지 않습니다.

## `Spec.md` 기준 데이터 확보 가능성

| 필요 입력 | API 확보 가능성 | 현재 상태 |
| --- | --- | --- |
| 서울 행정동 경계 | 별도 경계 데이터셋으로 가능, TAGO/bikeList는 아님 | `data/raw/seoul_admin_dong.geojson` 또는 확인된 경계 API 필요 |
| 서울 공공자전거 대여소 좌표 | API로 해결 가능 | 서울 `bikeList`로 확인됨 |
| 서울 공공자전거 대여 이력 | `bikeList`로는 해결 불가 | 과거 이동 이력 CSV 또는 파일 API 필요 |
| TAGO 공유 PM 장치 스냅샷 | 도시 데이터가 노출되는 경우 API로 해결 가능 | `PersonalMobilityInfo`는 동작하지만 전체 도시 공급자 조회가 현재 세종만 노출하고 서울은 노출하지 않음 |
| GCOO 가격/비용 | 공공 API 데이터 아님 | 설정값 가정 |

## 필요한 키

`.env`는 다음 키를 지원합니다.

```bash
SEOUL_API_KEY="..."
OPEN_DATA_PORTAL_API_KEY="..."
```

기존 별칭도 계속 허용됩니다.

```bash
SEOUL_OPEN_API_KEY="..."
DATA_GO_KR_SERVICE_KEY="..."
```

`OPEN_DATA_PORTAL_API_KEY`는 TAGO `PersonalMobilityInfo` 호출 시 자동으로 `serviceKey`로 주입됩니다.

## 데이터 입력 파트

데이터 입력 계층은 원시 입력 수집과 정규화만 수행합니다. 최적화 모델은 실행하지 않습니다.

```bash
python3 src/data_input.py --snapshot-label now
```

이 명령은 다음 파일을 씁니다.

```text
data/raw/seoul_bike_stations.csv
data/raw/seoul_private_pm_operator_summary.csv
data/raw/api/tago_pm/providers_all_<label>.csv
data/raw/api/tago_pm/providers_<label>.csv
data/raw/tago_pm_snapshots_<label>.csv    # TAGO가 target_city_name과 일치하는 공급자를 반환할 때만 생성
data/raw/api/
data/raw/snapshot_manifest.jsonl
```

다음 1주일 동안 목표 의사결정 시간대에 가깝게, 이상적으로는 03:30-04:30 사이에 같은 명령을 반복 실행합니다.

```bash
python3 src/data_input.py --snapshot-label "$(date +%Y%m%dT%H%M%S)"
```

도시 검증을 통과한 TAGO 호출이 성공할 때마다 정규화된 PM 스냅샷 파일이 하나씩 추가됩니다. 파일이 누적될수록 모델은 다음 항목을 더 잘 추정할 수 있습니다.

- `x_obs_i`: 평균 관측 GCOO 배치
- `competitor_count_is`: 시나리오 날짜별 경쟁사 밀도
- `K_i`: 관측 PM 공급량 백분위 기반 용량
- 날짜별 시나리오 변동

## 모델 파트

모델 계층은 외부 API를 호출하지 않습니다. `data/raw`에 누적된 파일을 읽고 처리된 모델 산출물을 만듭니다.

```bash
python3 src/model.py --out outputs/model
```

실제 서울 매칭 TAGO PM 스냅샷 파일이 아직 없으면, 모델은 데이터가 있는 척하지 않고 준비 상태 보고서를 씁니다.

```text
outputs/model/model_readiness.json
```

스모크 테스트 전용으로는 다음을 사용합니다.

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
```

수집과 모델 실행을 한 번에 수행하는 명령:

```bash
python3 src/run_pipeline.py --snapshot-label now --out outputs/latest_run
```

## 세종 TAGO Cron 수집기

서울 TAGO 엔드포인트는 현재 서울 PM 공급자를 노출하지 않습니다. 세종 전환 작업에는 전용 cron 수집기를 사용합니다.

```bash
scripts/setup_sejong_tago_cron.sh --interval-minutes 5
```

서버 사전 조건:

```bash
OPEN_DATA_PORTAL_API_KEY="..."
```

위 값은 `.env` 또는 `--env-file`로 전달한 파일에 있어야 합니다.

설정 스크립트가 수행하는 작업:

1. `.venv` 생성
2. `requirements.txt` 설치
3. 세종 TAGO 수집 1회 즉시 실행
4. rolling 전처리 CSV 및 standalone visualization HTML 생성
5. 현재 Unix 사용자에 대해 idempotent한 crontab 항목 등록

Cron이 호출하는 명령:

```bash
python src/collect_sejong_tago.py --processed-dir data/processed/sejong_tago --visualization-dir outputs/visualizations
```

원시 및 정규화 스냅샷 파일은 다음 위치에 기록됩니다.

```text
data/raw/api/tago_pm/
data/raw/tago_pm_snapshots_sejong_*.csv
data/raw/snapshot_manifest.jsonl
```

각 스냅샷 이후 rolling 전처리 산출물이 다시 생성됩니다.

```text
data/processed/sejong_tago/sejong_pm_snapshots_accumulated.csv
data/processed/sejong_tago/sejong_pm_latest_snapshot.csv
data/processed/sejong_tago/sejong_pm_operator_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_zone_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_device_intervals.csv
data/processed/sejong_tago/sejong_pm_activity_by_zone.csv
data/processed/sejong_tago/sejong_pm_preprocess_summary.json
```

각 스냅샷 이후 visualization도 다시 생성됩니다.

```text
outputs/visualizations/sejong_charts_dashboard.html
outputs/visualizations/sejong_map.html
outputs/visualizations/sejong_visualization_manifest.json
```

cron 작업과 로그 확인:

```bash
crontab -l | grep gcoo-sejong-tago-cron
tail -f logs/sejong_tago_cron.log
```

## Pivot 검토: 세종 TAGO 기반 또는 서울 따릉이 기반

현재 확인된 제약은 명확합니다. 서울 TAGO PM 엔드포인트는 동작하지만 서울 공급자 행을 노출하지 않고, 반대로 세종은 TAGO에서 실제 PM 공급자와 장치 위치 스냅샷을 받을 수 있습니다. 따라서 주제는 크게 두 방향으로 pivot할 수 있습니다.

1. 세종시로 pivot: 실제 공유 PM 장치 스냅샷을 반복 수집해 공급/활동/경쟁 밀도 기반 배치 최적화 문제로 전환
2. 서울 따릉이 데이터 기반으로 pivot: 실제 PM 관측 대신 따릉이 OD 이동 이력을 PM 유사 수요 proxy로 사용해 서울 내 latent demand 기반 배치 최적화 문제로 전환

### Pivot A. 세종 TAGO 기반 PM 배치 최적화

세종 pivot의 장점은 실제 PM 장치 단위 관측값을 확보할 수 있다는 점입니다. TAGO `PersonalMobilityInfo`에서 세종 공급자와 장치 목록이 노출되므로, 서울보다 데이터 수집 가능성이 높고 5분 단위 cron 수집으로 시간축이 있는 패널 데이터를 만들 수 있습니다.

수집 가능한 데이터:

| 데이터 | 수집/생성 경로 | 최적화에서의 용도 |
| --- | --- | --- |
| 공급자 목록 | `GetPMProvider`, `cityName=세종` 검증 | operator 집합, 사업자별 경쟁 밀도 |
| 장치 스냅샷 | `GetPMListByProvider(providerName, cityCode)` | `operator_name`, `device_id`, `battery_level`, `latitude`, `longitude`, `timestamp` |
| 유효 장치 수 | `battery_threshold` 이상 장치 집계 | 실제 서비스 가능한 공급량 추정 |
| 500m grid zone | `src/collect_sejong_tago.py`의 `add_grid_zone` | 행정동 경계가 없어도 사용할 기본 공간 단위 |
| zone별 장치 수 | `sejong_pm_zone_snapshot_counts.csv` | zone별 공급/경쟁 밀도, capacity 산정 |
| 사업자별 장치 수 | `sejong_pm_operator_snapshot_counts.csv` | GCOO와 경쟁사 비중, 시장 검증 신호 |
| 장치 interval | `sejong_pm_device_intervals.csv` | 반복 스냅샷 사이 이동 여부, 이동 거리, 속도, 배터리 변화 |
| zone별 활동량 | `sejong_pm_activity_by_zone.csv` | 대여 이벤트가 없을 때의 수요 proxy |

세종 데이터의 핵심 한계는 TAGO가 대여 시작/종료 이벤트를 직접 주지 않는다는 점입니다. 따라서 수요는 실제 trip count가 아니라 반복 스냅샷에서 관측되는 장치 이동과 위치 밀도로 추정해야 합니다.

수요 도출 방식:

1. 동일 `operator_name`, `device_id`를 시간순으로 정렬합니다.
2. 직전 스냅샷과 현재 스냅샷 사이의 거리, 시간, 속도를 계산합니다.
3. `moved_50m`, `moved_200m` 같은 이동 flag를 pickup/drop-off의 proxy로 사용합니다.
4. 직전 zone을 `origin_zone_id`로 보고 `moved_200m_count`, `moved_50m_count`, `interval_count`를 zone별 활동량으로 집계합니다.
5. 너무 짧은 노이즈 이동, GPS 튐, 너무 긴 interval은 필터링하거나 가중치를 낮춥니다.
6. zone별 수요 점수 `H_is`는 다음처럼 둘 수 있습니다.

```text
H_is = moved_200m_count
     + gamma_50 * moved_50m_count
     + gamma_density * effective_device_count
```

여기서 `s`는 날짜/시간대 시나리오입니다. `effective_device_count`는 배터리 기준을 통과한 장치 수이며, 이동량이 적은 시간대에도 반복적으로 PM이 놓이는 zone을 시장 검증 신호로 반영합니다. 최종 모델에서는 `H_is`를 총 공급량과 평균 회전율(`u0_avg_rides_per_scooter_day`)에 맞춰 scale factor `alpha`로 보정합니다.

기본 decision variable:

| 변수 | 의미 |
| --- | --- |
| `x_i` | zone `i`에 배치할 GCOO scooter 수 |
| `K_i` | zone `i`의 최대 수용량 |
| `c_is` | 시나리오 `s`에서 zone `i`의 경쟁사 PM 수 |
| `a_is` | 수요와 경쟁 밀도를 합친 유효 시장 크기 |
| `q_is(x_i, c_is)` | 배치 `x_i`에서 기대되는 GCOO 이용량 |

현재 프로토타입의 수요 포착 함수는 다음 형태입니다.

```text
q_is = min(
  a_is * (1 - exp(-beta * x_i / (1 + theta * c_is))),
  U_max * x_i
)
```

해석은 다음과 같습니다.

- `x_i`가 늘면 기대 이용량은 증가하지만 포화됩니다.
- `c_is`가 높으면 같은 `x_i`에서도 경쟁 압력이 커져 포착률이 낮아집니다.
- `U_max`는 scooter 1대가 하루에 처리할 수 있는 최대 이용 횟수입니다.
- `lambda_market_validation`을 통해 경쟁사 장치가 많은 zone을 "이미 시장이 검증된 곳"으로 일부 가산할 수 있습니다.

목적함수는 기대 이익 극대화로 잡는 것이 자연스럽습니다.

```text
maximize over x:
  E_s [ sum_i ((p_i - variable_cost) * q_is(x_i, c_is) - c_i * x_i) ]
```

세종 pivot에서는 `p_i`를 실제 결제 데이터 없이 직접 관측할 수 없으므로, 평균 이동 거리 또는 평균 이동 시간 proxy로 추정합니다. 초기값은 현재 설정처럼 unlock fee와 분당 요금 가정을 사용하고, 이후 실제 GCOO 요금/정산 데이터가 생기면 `p_i`를 교체합니다.

필수 제약식:

```text
0 <= x_i <= K_i
sum_i x_i <= F
x_i integer
```

권장 제약식:

| 제약 | 설명 |
| --- | --- |
| zone capacity | `K_i = ceil(capacity_multiplier * p95(total_pm_count_is))` 또는 zone 면적/주차 가능 공간 기반 상한 |
| total fleet | 전체 투입 가능 scooter 수 `F`를 넘지 않음 |
| serviceable battery | 배터리 기준 미달 장치는 공급량/수요 추정에서 제외 |
| activity threshold | 반복 관측 기간 동안 활동량이 너무 낮은 zone에는 `x_i=0` 또는 낮은 `K_i` 적용 |
| rebalancing budget | 확장 모델에서 `r_ij`를 도입하면 총 재배치 거리/비용 제한 |
| operator exposure | 특정 경쟁사가 과점한 zone에 대한 최대 노출 또는 risk penalty |

확장 decision variable로는 `r_ij`를 둘 수 있습니다. `r_ij`는 zone `i`에서 zone `j`로 재배치할 scooter 수입니다. 이 경우 목적함수는 재배치 비용을 차감합니다.

```text
maximize:
  expected_profit(x) - sum_i_j relocation_cost_ij * r_ij
```

이 확장 모델은 "아침 배치 위치"뿐 아니라 "수요가 이동한 뒤 어디로 회수/재배치할지"까지 다룰 수 있습니다. 다만 현재 TAGO만으로는 실제 대여 이벤트를 보지 못하므로, 초기에는 `x_i`만 최적화하고 interval 기반 활동량으로 수요 proxy를 안정화하는 것이 우선입니다.

세종 pivot이 적합한 경우:

- 실제 PM 장치 위치와 경쟁 밀도를 기반으로 한 주제를 원할 때
- 단기간에 반복 수집 가능한 데이터가 필요할 때
- 서울이라는 지역 제약보다 "공유 PM 배치 최적화" 자체가 더 중요할 때

### Pivot B. 서울 따릉이 데이터 기반 PM 유사 수요 최적화

서울 따릉이 pivot은 지역을 서울로 유지할 수 있다는 장점이 있습니다. 대신 이 방향은 "실제 PM 관측 기반 최적화"가 아니라 "따릉이 이동 이력을 PM 유사 단거리 이동 수요 proxy로 사용하는 서울 배치 최적화"로 정의해야 합니다.

수집 가능한 데이터:

| 데이터 | 수집/생성 경로 | 최적화에서의 용도 |
| --- | --- | --- |
| 따릉이 대여소 위치 | 서울 `bikeList` | station ID, 이름, 위도/경도, 행정동 mapping |
| 따릉이 이동 이력 | 서울 열린데이터 `tbCycleRentData` | 대여/반납 시각, 출발/도착 대여소, 거리, 이용 시간 |
| 서울 행정동 경계 | `src/fetch_seoul_boundary.py` | station과 trip을 행정동으로 변환 |
| PM 견인 이벤트 | 서울 열린데이터 `tbAutoKickboard` | PM 마찰/불법주차가 나타난 지역 proxy |
| PM 주차구역 | 서울 열린데이터 `parkingKickboard` | PM 주차 가능성 또는 제도적 수용성 proxy |
| 민간 PM 사업자 요약 | `seoul_private_pm_operator_summary.csv` | 경쟁사 총량 prior |

이 pivot에서 직접 수집하기 어려운 데이터는 서울의 실시간 PM 장치 위치, GCOO 실제 대여/매출, 경쟁사별 실제 장치 배치입니다. 따라서 경쟁 PM 수는 실제 관측이 아니라 수요, 견인 이벤트, PM 주차구역, 사업자 요약을 합친 surrogate로 만들어야 합니다.

수요 도출 방식:

1. `bikeList`로 대여소 좌표를 가져오고, 행정동 경계와 spatial join하여 `station_id -> dong_id`를 만듭니다.
2. `tbCycleRentData`에서 날짜/시간대별 따릉이 이동 이력을 가져옵니다.
3. PM과 유사한 이동만 남깁니다. 현재 설정 기준은 다음과 같습니다.

```text
0.5km <= distance <= 3.0km
3min <= duration <= 20min
5km/h <= speed <= 25km/h
origin_dong_id != destination_dong_id
```

4. 새벽 4시 이전 이동은 전날 operating day로 묶어 야간 이동을 같은 영업일에 포함합니다.
5. 출발 행정동 기준으로 `H_is`와 `departures_is`를 집계하고, 도착 행정동 기준으로 `arrivals_is`를 집계합니다.
6. 평균 이동 거리 `avg_distance_km_i`는 예상 요금 `p_i` 계산에 사용합니다.
7. 전체 수요 규모는 총 fleet `F`와 평균 회전율 `u0_avg_rides_per_scooter_day`를 이용해 scooter 이용 수요로 scaling합니다.

서울 따릉이 pivot의 decision variable은 세종과 동일하게 시작할 수 있습니다.

| 변수 | 의미 |
| --- | --- |
| `x_i` | 행정동 `i`에 배치할 GCOO scooter 수 |
| `H_is` | 시나리오 `s`에서 행정동 `i`의 PM 유사 따릉이 출발 수요 |
| `arrivals_is`, `departures_is` | 행정동별 유입/유출 불균형 |
| `B_i` | `abs(arrivals - departures) / (arrivals + departures + 1)`로 계산한 imbalance score |
| `K_i` | 행정동별 배치 capacity |
| `c_is` | surrogate 경쟁 PM 수 |

목적함수는 세종과 같은 기대 이익 극대화 구조를 사용할 수 있습니다.

```text
maximize over x:
  E_s [ sum_i ((p_i - variable_cost) * q_is(x_i, c_is) - c_i * x_i) ]
```

서울 pivot에서는 각 항목을 다음처럼 정의합니다.

- `H_is`: PM 유사 조건을 통과한 따릉이 출발 trip 수
- `p_i`: `avg_distance_km_i / avg_scooter_speed_kmph * 60`으로 계산한 예상 이용 시간에 unlock fee와 분당 요금을 적용
- `c_i`: 기본 일별 운영비에 imbalance penalty를 더한 값
- `K_i`: 실제 TAGO PM이 없으면 수요, 견인 이벤트, 주차구역, 사업자 총량 prior로 만든 surrogate capacity
- `c_is`: PM 견인/주차/사업자 요약과 수요 가중치로 만든 surrogate competitor count

필수 제약식:

```text
0 <= x_i <= K_i
sum_i x_i <= F
x_i integer
```

권장 제약식:

| 제약 | 설명 |
| --- | --- |
| demand support | PM 유사 따릉이 수요가 거의 없는 행정동은 배치 후보에서 제외 |
| capacity | 실제 PM 관측이 없으므로 `K_i`를 수요 분위수, PM 주차구역, 견인 이벤트, 도로/상권 proxy로 보수적으로 제한 |
| imbalance penalty | 유출입 차이가 큰 곳은 회수/재배치 비용이 커지므로 `c_i` 증가 |
| district fairness | 특정 구에만 모든 fleet이 몰리지 않도록 구별 최소/최대 비중 설정 가능 |
| scenario robustness | 특정 날짜 하루가 아니라 여러 operating day 평균 또는 하위 분위 수익에도 견디는 배치 선택 |

서울 pivot에서 확장할 수 있는 형태는 unmet demand 또는 service level 변수를 추가하는 것입니다.

| 변수 | 의미 |
| --- | --- |
| `u_is` | 행정동 `i`, 시나리오 `s`에서 충족하지 못한 PM 유사 수요 |
| `z_i` | 행정동 `i`를 서비스 후보로 열지 여부 |
| `r_ij` | 행정동 `i`에서 `j`로 재배치하는 scooter 수 |

이 경우 목적함수는 수익 극대화와 미충족 수요 penalty를 함께 둘 수 있습니다.

```text
maximize:
  expected_profit(x)
  - eta_unmet * sum_i_s u_is
  - relocation_cost
```

서울 따릉이 pivot이 적합한 경우:

- 분석 대상 지역을 반드시 서울로 유지해야 할 때
- 공유 PM의 직접 관측보다 단거리 이동 수요 추정과 공간 최적화가 핵심일 때
- 논문/보고서 주제를 "따릉이 OD 기반 PM 잠재 수요 추정 및 배치 최적화"로 명확히 재정의할 수 있을 때

### 두 Pivot의 선택 기준

| 기준 | 세종 TAGO pivot | 서울 따릉이 pivot |
| --- | --- | --- |
| 실제 PM 장치 관측 | 가능 | 현재 불가 |
| 수요 관측 | 직접 trip은 불가, 스냅샷 이동으로 추정 | 따릉이 OD trip으로 proxy 구성 가능 |
| 지역 일관성 | 서울에서 세종으로 변경 | 서울 유지 |
| 경쟁 밀도 | 실제 PM 장치 수 기반 | surrogate 기반 |
| 모델 신뢰도 | 공급/경쟁 밀도는 강함, 수요는 추정 | 수요 proxy는 강함, PM 공급/경쟁은 약함 |
| 추천 주제명 | "세종시 공유 PM 스냅샷 기반 배치 최적화" | "서울 따릉이 OD 기반 PM 유사 수요 추정 및 배치 최적화" |

실제 PM 운영 최적화에 가까운 결과가 필요하면 세종 pivot이 더 낫습니다. 서울이라는 정책/공간 맥락이 중요하고 PM을 "따릉이 단거리 이동의 대체 수단"으로 모델링해도 되는 주제라면 서울 따릉이 pivot이 더 적합합니다.

## 시각화 설정

시각화는 이 프로젝트의 주된 검사 화면입니다. 현재 설정은 두 개의 독립 실행형 interactive HTML 파일을 생성합니다.

1. `charts_dashboard.html`: 최적화/시나리오 CSV 기반 수치 차트
2. `seoul_map.html`: 행정동 경계, heatmap, 자전거 대여소 marker를 포함한 서울 지도 overlay

이 설정은 React 앱을 추가하지 않고 Python 패키지를 의도적으로 사용합니다.

- `pyecharts`: Apache ECharts 기반 interactive chart dashboard
- `folium`: Leaflet 기반 map overlay 및 heatmap 렌더링
- `branca`: map overlay 색상 스케일 지원

### 1. 시각화 의존성 설치

저장소 루트에서 한 번 실행합니다.

```bash
python3 -m pip install -r requirements.txt
```

일반 데이터/모델 의존성과 함께 다음 패키지가 설치됩니다.

```text
pyecharts
folium
branca
```

### 2. 서울 행정동 경계 준비

지도 시각화 코드는 다음 파일을 찾습니다.

```text
data/raw/seoul_admin_dong.geojson
```

다음 명령으로 생성합니다.

```bash
python3 src/fetch_seoul_boundary.py --out data/raw/seoul_admin_dong.geojson
```

예상 출력:

```text
boundary=data/raw/seoul_admin_dong.geojson
features=426
source=https://raw.githubusercontent.com/vuski/admdongkor/master/ver20250401/HangJeongDong_ver20250401.geojson
```

이 파일이 없으면 `src/visualize.py`는 `src/prototype_pipeline.py`의 작은 fixture bounding box로 fallback하여 계속 실행됩니다. 이 fallback은 스모크 테스트 전용입니다. 실제 시각 검토에는 실제 GeoJSON을 사용하세요.

### 3. 시각화할 데이터 생성

빠른 로컬 스모크 테스트를 위해 fixture 기반 프로토타입 산출물을 생성합니다.

```bash
python3 src/prototype_pipeline.py --out outputs/prototype
```

실제 모델 산출물은 대신 모델 파이프라인을 실행합니다.

```bash
python3 src/model.py --out outputs/model
```

실제 서울 매칭 TAGO PM 스냅샷이 아직 없으면 모델은 준비 상태 보고서만 쓸 수 있습니다. 이 경우 시각화 스모크 테스트에는 `outputs/prototype` 또는 `outputs/model_fixture`를 사용합니다.

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
```

### 4. 시각화 HTML 렌더링

모델/프로토타입 출력 디렉터리에서 차트와 지도 산출물을 모두 렌더링합니다.

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations
```

또는:

```bash
python3 src/visualize.py --input outputs/model --out outputs/visualizations
```

예상 출력:

```text
charts=outputs/visualizations/charts_dashboard.html
map=outputs/visualizations/seoul_map.html
map_metric=<selected_metric>
boundary_source=geojson
```

생성 파일:

```text
outputs/visualizations/charts_dashboard.html
outputs/visualizations/seoul_map.html
outputs/visualizations/visualization_manifest.json
```

`visualization_manifest.json`은 사용된 입력 디렉터리, 로드된 테이블, 선택된 지도 지표, 지도가 실제 GeoJSON 경계를 사용했는지 fixture bounding box를 사용했는지를 기록합니다.

### 5. 지도 overlay 지표 선택

기본적으로 시각화 코드는 `--map-metric auto`를 사용합니다. Auto 모드는 다음 순서로 0이 아닌 값을 가진 첫 번째 유용한 지표를 선택합니다.

```text
x_star_i
mean_H
mean_total_pm_count
mean_competitor_count
x_obs_i
K_i
```

특정 질문을 검사할 때는 지표를 명시적으로 override합니다.

```bash
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric x_star_i
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric mean_H
python3 src/visualize.py --input outputs/model --out outputs/visualizations --map-metric mean_competitor_count
```

주요 지표 의미:

| Metric | Meaning |
| --- | --- |
| `x_star_i` | 행정동별 최적화된 GCOO 킥보드 배치 |
| `mean_H` | 행정동별 평균 PM 유사 수요 |
| `mean_competitor_count` | 행정동별 평균 경쟁사 PM 수 |
| `mean_gcoo_count` | 행정동별 평균 관측 GCOO PM 수 |
| `K_i` | 최적화 모델에서 사용한 행정동 용량 |
| `B_i` | 도착/출발 기반 불균형 점수 |

### 6. 브라우저에서 시각화 열기

출력 파일은 정적 HTML입니다. 가장 안정적인 확인 방법은 저장소를 로컬에서 serve하는 것입니다.

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

그다음 다음 주소를 엽니다.

```text
http://127.0.0.1:8765/outputs/visualizations/charts_dashboard.html
http://127.0.0.1:8765/outputs/visualizations/seoul_map.html
```

포트 `8765`를 이미 사용 중이면 다른 포트를 선택합니다.

```bash
python3 -m http.server 8770 --bind 127.0.0.1
```

서버는 `Ctrl+C`로 중지할 수 있습니다.

### 7. 시각화 입력 계약

`src/visualize.py`는 선택된 `--input` 디렉터리에 `src/model.py` 또는 `src/prototype_pipeline.py`가 쓴 CSV 산출물이 있다고 가정합니다.

| File | Required | Used for |
| --- | --- | --- |
| `allocation_optimized.csv` | allocation chart에는 yes | 최적화 배치 차트 및 `x_star_i` map layer |
| `model_inputs.csv` | recommended | 용량, 관측 공급량, 불균형 진단 |
| `demand_scenario.csv` | recommended | 수요 차트 및 `mean_H` map layer |
| `tago_scenario.csv` | recommended | GCOO/경쟁사 수 차트 |
| `bike_stations_with_dong.csv` 또는 `bike_stations_normalized.csv` | recommended | 대여소 marker 및 대여소 heatmap |
| `dong_master.csv` | recommended | 읽기 쉬운 행정동 label |

선택 파일:

| File | Used for |
| --- | --- |
| `data/raw/seoul_admin_dong.geojson` | 실제 서울 행정동 polygon overlay |
| `data/raw/tago_pm_snapshots_*.csv` | 사용 가능한 경우 원시 PM point heatmap |

### 8. 문제 해결

지도에서 `boundary_source=fixture_bbox`가 출력되면 실제 서울 경계 파일을 찾지 못한 것입니다. 다음을 실행하세요.

```bash
python3 src/fetch_seoul_boundary.py --out data/raw/seoul_admin_dong.geojson
```

지도가 렌더링되지만 overlay 값이 모두 0이면 선택한 `--map-metric`이 입력 CSV에 없거나 0일 가능성이 큽니다. 다음을 시도하세요.

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations --map-metric mean_H
```

`outputs/model`에 `model_readiness.json`만 있으면 모델이 최적화할 만큼 충분한 실제 입력 데이터를 확보하지 못한 것입니다. 시각화 스모크 테스트에는 fixture 모드를 사용합니다.

```bash
python3 src/model.py --out outputs/model_fixture --allow-fixtures
python3 src/visualize.py --input outputs/model_fixture --out outputs/visualizations
```

브라우저 로딩이 오래된 것처럼 보이면 시각화 코드를 다시 실행하고 브라우저 페이지를 새로고침합니다.

```bash
python3 src/visualize.py --input outputs/prototype --out outputs/visualizations
```

## 실행

접근 가능한 API 확인:

```bash
python3 src/api_probe.py --out outputs/api_probe
```

프로토타입 변환과 작은 배치 예제 실행:

```bash
python3 src/prototype_pipeline.py --out outputs/prototype
```

생성되는 파일:

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

프로토타입은 스모크 테스트용 작은 fixture를 사용합니다. 실제 파이프라인은 `src/data_input.py`와 도시 검증을 통과한 `data/raw/tago_pm_snapshots_*.csv`를 사용합니다.
