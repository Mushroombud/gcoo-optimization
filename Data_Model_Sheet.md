# Data and Model Sheet: Sejong GBIKE 04:00 Deployment Optimization

이 문서는 현재 저장소의 **Sejong TAGO 공유 PM 데이터**를 이용해, GCOO/GBIKE의 매일 04:00 최적 배치를 어떤 **Optimization Model**로 표현할 수 있는지 정리한 Model Sheet이다.

핵심 질문은 다음과 같다.

```text
세종특별시에서 매일 04:00에 각 500m grid zone에 GBIKE PM을 몇 대 배치해야
하루 기대 profit이 최대가 되는가?
```

이 문서의 초점은 solver algorithm이 아니다. 핵심은 Solver 또는 Gurobi/Excel Solver에 넣을 수 있는 **decision variable, objective function, constraints, non-linear demand function, data-derived parameters**를 명확히 정의하는 것이다.

---

## 1. 현재 데이터 구조

## 1.1 Raw Data

원천 데이터는 TAGO Personal Mobility API에서 수집한 세종특별시 PM 스냅샷이다.

```text
data/raw/tago_pm_snapshots_sejong_*.csv
data/raw/api/tago_pm/*
```

raw snapshot 한 행은 특정 시각에 관측된 PM 1대를 의미한다.

| Column | 의미 |
| --- | --- |
| `timestamp` | 스냅샷 수집 시각 |
| `operator_name` | 운영사명. 현재 주요 값은 `GBIKE`, `ALPACA` |
| `device_id` | PM 기기 ID |
| `battery_level` | 배터리 잔량 |
| `latitude`, `longitude` | 기기 위치 |
| `city_code`, `city_name` | TAGO 도시 코드와 도시명 |

현재 전처리 최신 요약은 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| raw snapshot files | 587 |
| device-snapshot rows | 2,443,752 |
| latest raw timestamp | 2026-06-14T16:10:04+0900 |
| latest devices | 4,173 |
| latest GBIKE devices | 2,805 |
| latest ALPACA devices | 1,368 |
| grid size | 500m |

이 데이터의 장점은 외부 proxy가 아니라, 실제 공유 PM의 위치, 운영사, 배터리 상태를 직접 관측한다는 점이다.

---

## 1.2 Spatial Unit: 500m Grid Zone

모델의 공간 단위는 500m grid zone이다.

```text
i ∈ I = 세종특별시 500m grid zone 집합
```

생성 컬럼은 다음과 같다.

| Column | 의미 |
| --- | --- |
| `zone_id` | 500m grid zone ID |
| `zone_lat_index`, `zone_lon_index` | 위도/경도 방향 grid index |
| `zone_center_latitude`, `zone_center_longitude` | zone 중심 좌표 |

500m grid를 쓰는 이유는 다음과 같다.

- PM 이용자는 보통 가까운 거리 안의 기기를 찾는다.
- 행정동보다 운영 의사결정 단위가 세밀하다.
- GPS 단위의 sidewalk-level 배치보다 데이터 요구량이 낮다.
- 04:00 배치 문제에서 "어느 생활권 블록에 몇 대를 둘 것인가"를 표현하기 적절하다.

---

## 1.3 Processed Tables

전처리 결과는 다음 파일에 저장된다.

| File | 모델에서의 역할 |
| --- | --- |
| `sejong_pm_snapshots_accumulated.csv` | 모든 수집 시점의 device-level PM 상태 |
| `sejong_pm_latest_snapshot.csv` | 최신 시점의 PM 배치 상태 |
| `sejong_pm_operator_snapshot_counts.csv` | 시점별 운영사별 총 공급량 |
| `sejong_pm_zone_snapshot_counts.csv` | 시점별 zone/operator별 공급량 |
| `sejong_pm_device_intervals.csv` | 같은 device의 연속 스냅샷 간 이동 |
| `sejong_pm_activity_by_zone.csv` | zone/operator별 이동 activity summary |
| `sejong_pm_inferred_rides.csv` | 이동 interval에서 추정한 ride segment. 운영자 이동 의심 flag 포함 |
| `sejong_pm_operator_move_candidates.csv` | 운영자가 차량/정비 과정에서 이동시킨 것으로 보이는 excluded segment |
| `sejong_pm_od_flows.csv` | 운영자 이동 의심 segment를 제외한 clean inferred ride의 origin-destination flow |

현재 주요 processed data 규모는 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| device intervals | 2,439,579 |
| moved >= 50m intervals | 10,161 |
| moved >= 200m intervals | 9,600 |
| inferred ride segments | 8,893 |
| inferred ride avg distance | 1,034.9m |
| inferred ride avg speed | 12.4km/h |
| OD pairs | 2,505 |

---

## 2. Snapshot에서 Demand Signal을 만드는 방식

## 2.1 Device Interval

같은 `operator_name`과 같은 `device_id`에 대해 시간순으로 정렬하고, 직전 스냅샷과 현재 스냅샷을 비교한다.

```text
interval = same operator_name + same device_id
           between consecutive timestamps
```

주요 생성값은 다음과 같다.

| Column | 의미 |
| --- | --- |
| `prev_zone_id` | 직전 스냅샷의 zone |
| `zone_id` | 현재 스냅샷의 zone |
| `interval_minutes` | 두 스냅샷 사이 시간 |
| `distance_m` | 이동거리 |
| `speed_kmph` | 추정 이동속도 |
| `battery_delta` | 배터리 변화량 |
| `same_zone` | 같은 zone에 머물렀는지 여부 |

해석상 주의할 점은, 이동 interval이 모두 실제 ride는 아니라는 것이다. 일부는 수거, 재배치, GPS noise일 수 있다. 그래서 모델에서는 이것을 확정된 ride log가 아니라 **observed activity signal**로 사용한다.

---

## 2.2 Inferred Ride Segment

현재 inferred ride는 다음 조건으로 추정한다.

```text
4 min <= interval_minutes <= 6 min
distance_m >= 100m
```

이 조건을 쓰는 이유는 다음과 같다.

- 약 5분 간격의 snapshot 사이에서 100m 이상 이동했다면 단순 GPS jitter보다 실제 이동일 가능성이 높다.
- 평균 속도 약 12.4km/h는 PM의 도시 내 단거리 이동 속도와 맞는다.
- 너무 긴 interval은 중간 경로를 알 수 없고, 너무 짧은 이동은 GPS 오차일 수 있다.

추정 ride 중 운영자가 차량으로 이동시켰거나 배터리 교환/재배치 중인 것으로 보이는 segment는 별도 flag를 세우고 수요 계산에서 제외한다.

| Column | 의미 |
| --- | --- |
| `operator_move_speed_rule` | `speed_kmph > 28`인 비정상 고속 이동 |
| `operator_move_repeat_rule` | `speed_kmph > 25`인 고속 이동이 같은 기기에서 30분 내 2회 이상 반복 |
| `operator_move_cluster_rule` | `speed_kmph > 25`인 이동이 같은 시간/OD에서 2대 이상 군집 |
| `operator_move_battery_rule` | `speed_kmph > 25`이면서 배터리 변화량 절댓값이 20pp 이상 |
| `operator_move_flag` | 위 rule 중 하나라도 참인 운영자 이동 의심 segment |
| `operator_move_reason` | 적용된 rule code 목록 |
| `excluded_from_demand` | `true`이면 `D_i`, OD flow, 최적화 수요 계산에서 제외 |

zone별 기본 demand signal은 다음처럼 만든다.

```text
D_i = inferred ride segments starting from zone i
      where excluded_from_demand = false
```

여기서 `D_i`는 실제 전체 수요가 아니라, snapshot 기반으로 관측된 수요의 proxy이다. 보고서에서는 필요하면 scaling parameter `η`를 둬서 다음처럼 확장할 수 있다.

```text
D_i = η R_i
```

`R_i`는 관측된 inferred ride origin count이고, `η`는 snapshot이 포착하지 못한 실제 수요를 보정하는 scale parameter이다.

---

## 2.3 Competition Signal

최신 snapshot에서 zone별 운영사 공급량을 계산한다.

```text
C_i = latest ALPACA devices in zone i
G_i = latest GBIKE devices in zone i
```

GBIKE 관점에서는 `C_i`가 경쟁사 공급량이다. ALPACA 또는 신규 진입자 관점으로 바꾸면 같은 구조에서 경쟁사 변수를 GBIKE 공급량으로 바꾸면 된다.

경쟁사 공급량은 모델에서 두 가지 의미를 가진다.

1. **Market validation**: ALPACA가 많이 놓인 zone은 PM 시장이 실제로 존재한다는 신호일 수 있다.
2. **Competition pressure**: ALPACA가 많으면 같은 GBIKE 배치량으로 잡을 수 있는 수요가 줄어든다.

---

## 3. Solver에 넣는 Main Model

## 3.1 Decision Variable

Solver가 직접 고르는 값은 각 zone의 배치량이다.

```math
x_i = \text{04:00에 zone } i \text{에 배치할 GBIKE PM 수}
```

정수 모델에서는:

```math
x_i \in \mathbb{Z}_+
```

수업에서 배운 Non-linear Optimization 관점으로 설명하거나 Excel Solver로 연속 근사할 때는:

```math
x_i \ge 0
```

로 완화할 수 있다. 실제 PM 대수는 정수이므로 최종 배치에서는 반올림 또는 정수 solver가 필요하다.

---

## 3.2 Objective Function

GBIKE의 하루 기대 profit을 최대화한다.

```math
\max_x \sum_i \left[(p_i-v)Q_i(x_i)-c_i x_i-r_i(x_i)\right]
```

각 항의 의미는 다음과 같다.

| Term | 의미 |
| --- | --- |
| `(p_i-v)` | ride 1건당 순수 운행마진 |
| `p_i` | zone `i`에서 ride 1건이 만드는 평균 매출 |
| `v` | ride 1건당 변동비 |
| `Q_i(x_i)` | zone `i`에서 실제로 잡을 수 있는 기대 ride 수 |
| `c_i x_i` | 배치한 PM 대수에 비례하는 일 운영비 |
| `r_i(x_i)` | 이용 후 흩어진 PM을 회수/재배치하는 기대 비용 |

`(p_i-v)Q_i(x_i)`는 운행으로 벌어들이는 기대 이익이다. `p_i`에서 `v`를 빼는 이유는 ride 1건이 발생할 때 매출만 생기는 것이 아니라 결제 수수료, 정비, 소모품, 고객지원 등 ride 수에 비례하는 비용도 같이 발생하기 때문이다.

`-c_i x_i`는 ride가 발생하지 않아도 PM을 현장에 배치해두는 순간 발생하는 비용이다. 예를 들어 충전 관리, 보험/감가, 현장 관리, 민원 대응 등이 여기에 해당한다.

`-r_i(x_i)`는 PM이 이용 후 다른 zone으로 흩어졌을 때 다음 운영 시작 전에 다시 회수하거나 재배치하는 비용이다. OD flow가 불균형한 zone일수록 이 값이 커질 수 있다.

---

## 3.3 Non-linear Demand Capture Function

GBIKE가 zone `i`에서 처리할 기대 ride 수는 다음처럼 둔다.

```math
Q_i(x_i)
= \min \left\{
A_i\left(1-e^{-\frac{\beta x_i}{1+\theta C_i}}\right),
Ux_i
\right\}
```

이 식이 이 모델의 핵심이다.

| Symbol | 의미 |
| --- | --- |
| `Q_i(x_i)` | 배치량 `x_i`일 때 zone `i`에서 GBIKE가 capture하는 기대 ride 수 |
| `A_i` | 보정된 잠재수요 |
| `x_i` | 04:00에 zone `i`에 배치할 GBIKE PM 수 |
| `C_i` | zone `i`의 ALPACA 경쟁 공급량 |
| `β` | 배치량 증가가 수요 capture로 전환되는 속도 |
| `θ` | 경쟁 공급량이 GBIKE capture를 약화시키는 강도 |
| `U` | PM 1대가 하루 처리할 수 있는 최대 ride 수 |

### 왜 `1-exp(-...)`인가?

PM을 더 많이 배치하면 사용자가 가까운 기기를 찾을 확률이 올라간다. 하지만 이미 충분히 많은 PM이 있는 zone에서는 1대를 추가해도 접근성 개선폭이 작다. 즉, 추가 1대의 marginal benefit은 체감한다.

`1-exp(-z)`는 이런 현상을 표현하기 좋은 함수다.

- `x_i = 0`이면 `Q_i(x_i) = 0`
- `x_i`가 증가하면 기대 ride가 증가한다.
- 처음에는 빠르게 증가하고, 이후에는 완만해진다.
- 잠재수요 `A_i`를 넘지 않는다.

따라서 이 함수는 "PM을 많이 두면 무조건 같은 폭으로 수요가 늘어난다"는 선형 가정을 피한다.

### 왜 경쟁사 `C_i`가 분모에 들어가는가?

```math
\frac{\beta x_i}{1+\theta C_i}
```

ALPACA 공급량이 많을수록 같은 GBIKE 배치량 `x_i`의 수요 capture 효과가 약해진다는 가정이다.

예를 들어 GBIKE 10대를 놓았을 때:

- ALPACA가 거의 없는 zone에서는 사용자가 GBIKE를 선택할 가능성이 높다.
- ALPACA가 이미 많은 zone에서는 사용자가 ALPACA를 선택할 수도 있으므로 GBIKE 10대의 효과가 줄어든다.

`θ`가 클수록 경쟁사의 압력이 더 강하게 반영된다.

### 왜 `min(..., Ux_i)`인가?

아무리 수요가 많아도 PM 1대가 하루에 처리할 수 있는 ride 수는 제한된다.

```math
Q_i(x_i) \le Ux_i
```

이 제약은 배터리, 이동시간, 회전율, 고장, 대기시간 등 물리적 운영 한계를 반영한다.

---

## 3.4 Adjusted Demand Potential

기본 demand는 inferred ride origin count에서 출발한다.

```math
D_i = \text{inferred rides starting from zone } i
```

하지만 경쟁사 공급량은 단순히 나쁜 신호만은 아니다. ALPACA가 많이 놓인 곳은 경쟁이 심한 곳이기도 하지만, 동시에 PM 수요가 검증된 시장일 수도 있다. 그래서 보정된 잠재수요를 다음처럼 둔다.

```math
A_i
=D_i\left(
1+\lambda
\frac{\log(1+C_i)}{\log(1+C_{\max})}
\right)
```

| Symbol | 의미 |
| --- | --- |
| `D_i` | inferred ride에서 얻은 기본 수요 |
| `C_i` | ALPACA 공급량 |
| `C_max` | 전체 zone 중 ALPACA 공급량의 최댓값 |
| `λ` | 경쟁사 존재를 market validation으로 반영하는 강도 |

`log(1+C_i)`를 쓰는 이유는 competitor signal도 체감한다고 보기 때문이다. ALPACA가 0대에서 10대로 늘어나는 것은 강한 시장 신호이지만, 100대에서 110대로 늘어나는 것은 추가 정보가 상대적으로 작다.

중요한 점은 경쟁사 공급량이 모델에서 두 번 등장한다는 것이다.

| 위치 | 역할 |
| --- | --- |
| `A_i` 안의 `log(1+C_i)` | ALPACA가 많은 곳은 PM 시장이 검증된 곳일 수 있다는 market validation |
| `Q_i(x_i)` 분모의 `1+θC_i` | ALPACA가 많으면 GBIKE가 같은 수요를 capture하기 어렵다는 competition pressure |

---

## 4. Constraints

## 4.1 Fleet Constraint

```math
\sum_i x_i = F
```

또는 fleet을 모두 쓰지 않아도 되는 모델이면:

```math
\sum_i x_i \le F
```

현재 dashboard run에서는 `F=500`으로 두고, 500대를 반드시 배치하는 planning problem으로 계산한다.

---

## 4.2 Zone Capacity Constraint

```math
0 \le x_i \le K_i
```

현재 구현에서는 다음처럼 둔다.

```math
K_i = \lceil \kappa \cdot \text{current total PM supply}_i \rceil
```

여기서 `current total PM supply_i`는 최신 snapshot에서 zone `i`에 관측된 GBIKE+ALPACA 공급량이다. PM이 이미 많이 모여 있는 zone은 물리적/운영적으로 더 큰 수용 가능성이 있다고 보는 보수적 proxy이다.

왜 필요한가:

- 500m zone 안에 PM을 무한히 둘 수 없다.
- 보행 공간, 민원, 주차 가능성, 안전 문제가 있다.
- Solver가 모든 PM을 몇 개 고수요 zone에 몰아넣는 비현실적 해를 방지한다.

---

## 4.3 Demand Capture Constraint

비선형 수요를 constraint 형태로 쓰면 다음과 같다.

```math
Q_i \le A_i\left(1-e^{-\frac{\beta x_i}{1+\theta C_i}}\right)
```

이 제약은 배치량 증가의 체감효과와 경쟁 압력을 함께 반영한다.

---

## 4.4 Device Throughput Constraint

```math
Q_i \le Ux_i
```

PM 1대가 하루에 처리할 수 있는 ride 수의 상한을 둔다.

---

## 4.5 Non-negativity And Integer Constraint

```math
x_i \ge 0,\quad Q_i \ge 0,\quad r_i(x_i) \ge 0
```

실제 PM 대수는 정수이므로 엄밀히는:

```math
x_i \in \mathbb{Z}_+
```

정수 제약을 포함하면 문제는 MINLP가 된다. 수업 범위에서 Non-linear Optimization으로 설명하려면 `x_i`를 연속변수로 완화한 뒤, 최종 결과를 정수화할 수 있다.

---

## 5. Static Parameters: 현재 Dashboard 설정값

현재 `src/visualize_optimization_model.py`에서 쓰는 static parameter는 다음과 같다.

| Parameter | 현재 값 | 의미 |
| --- | ---: | --- |
| `F` | 500 | 이번 run에서 배치할 전체 GBIKE PM 수 |
| `λ` | 0.30 | 경쟁사 존재를 market validation으로 반영하는 강도 |
| `β` | 0.08 | GBIKE 배치량이 수요 capture로 전환되는 속도 |
| `θ` | 1.00 | ALPACA 공급량이 GBIKE capture를 약화시키는 정도 |
| `U` | 6.0 rides/device/day | PM 1대가 하루 처리 가능한 최대 ride 수 |
| `p_i` | 2,200 KRW | 현재 dashboard에서는 zone 공통 ride 1건 평균 매출 |
| `v` | 300 KRW | ride 1건당 변동비 |
| `c_i` | 2,500 KRW/day | PM 1대당 일 운영비 |
| `ρ` | 900 KRW/km | 재배치 거리 1km당 비용 |
| `κ` | 1.25 | zone capacity `K_i` 계산에 쓰는 multiplier |

이 값들은 최종 정답이라기보다, 모델을 실행하고 시각화하기 위한 baseline assumption이다. 보고서에서는 sensitivity analysis 대상으로 둘 수 있다.

---

## 6. Data-derived Parameters

현재 dashboard model에서 데이터로부터 계산되는 주요 값은 다음과 같다.

| Parameter | 계산 방식 | 의미 |
| --- | --- | --- |
| `D_i` | `sejong_pm_inferred_rides.csv` 중 `excluded_from_demand=false`인 origin zone별 count | zone별 기본 demand signal |
| `C_i` | latest snapshot의 zone별 ALPACA device count | 경쟁사 공급량 |
| `G_i` | latest snapshot의 zone별 GBIKE device count | 현재 GBIKE 공급량 |
| `A_i` | `D_i(1 + λ log(1+C_i)/log(1+C_max))` | 보정된 잠재수요 |
| `K_i` | `ceil(κ * current total PM supply_i)` | zone별 최대 배치 가능량 |
| `L_i` | clean OD flow 기반 expected rebalancing km | 이용 후 PM 회수/재배치 거리 proxy |
| `r_i(x_i)` | `ρ * L_i * Q_i(x_i)` | 기대 재배치비 |

현재 구현의 rebalancing cost는 다음 구조다.

```math
r_i(x_i)=\rho L_i Q_i(x_i)
```

`L_i`는 `sejong_pm_od_flows.csv`에서 origin zone `i`에서 출발한 clean ride들의 평균 이동거리로부터 추정한다. 즉, 운영자 이동 의심 segment를 제거한 뒤 어떤 zone에서 출발한 PM이 이용 후 얼마나 흩어질 가능성이 있는지를 비용으로 반영한다.

---

## 7. Linear Optimization Baseline

수업의 Linear Optimization과 연결하기 위해 baseline model을 둘 수 있다.

```math
\max_x \sum_i a_i x_i
```

여기서:

```math
a_i = (p_i-v)u_i-c_i-\rho_i
```

`a_i`는 zone `i`에 PM 1대를 추가했을 때의 고정된 기대 순이익이다.

제약조건:

```math
\sum_i x_i \le F
```

```math
0 \le x_i \le K_i
```

이 모델은 해석이 쉽지만, PM 1대의 수익성이 배치량과 무관하게 항상 같다고 가정한다. 실제로는 같은 zone에 PM이 많아질수록 추가 1대의 효과가 줄어드므로 main model은 Non-linear Optimization으로 잡는 것이 더 설득력 있다.

---

## 8. 왜 Non-linear Optimization인가?

이 모델이 non-linear인 이유는 목적함수 안의 `Q_i(x_i)`가 비선형이기 때문이다.

```math
1-e^{-\frac{\beta x_i}{1+\theta C_i}}
```

이 함수는 다음 현실을 반영한다.

- PM을 더 많이 놓으면 접근성이 좋아진다.
- 하지만 접근성 개선효과는 포화된다.
- 경쟁사 PM이 많으면 같은 GBIKE 배치량의 효과가 줄어든다.
- PM 1대의 하루 처리량에는 물리적 상한이 있다.

따라서 이 문제는 단순히 "수요가 큰 zone부터 많이 배치"하는 문제가 아니다. 각 zone에서 추가 1대가 만드는 기대 ride 증가분과, 그 PM을 유지/재배치하는 비용을 동시에 비교해야 한다.

---

## 9. Simulation: 불확실성 아래에서 모델 검증

Optimization은 특정 parameter와 demand assumption 아래에서 최적 배치 `x*`를 찾는다. 하지만 실제 운영일에는 수요와 비용이 예측과 다를 수 있다. 그래서 Simulation은 선택된 배치 `x*`가 얼마나 robust한지 확인하는 검증 단계로 사용한다.

현재 visualization의 Simulation은 다음 방식으로 읽는다.

```text
1. Solver가 찾은 배치 x*는 고정한다.
2. demand shock와 cost shock를 여러 scenario로 만든다.
3. 각 scenario에서 Objective value를 다시 계산한다.
4. Objective value의 분포, P10, P50, P90을 본다.
```

scenario별 profit은 다음처럼 계산한다.

```math
Profit_s(x^*)
=\sum_i (p_i-v)Q_{is}(x_i^*)
-\sum_i c_i x_i^*
-\sum_i r_{is}(x_i^*)
```

### demand shock

실제 ride 수요가 예측보다 높거나 낮아지는 효과다. 수요가 커지면 운행매출과 ride당 변동비가 함께 증가하고, 수요가 작아지면 둘 다 줄어든다.

### cost shock

재배치비가 평소보다 비싸지거나 싸지는 효과다. 예를 들어 회수 동선이 길어지거나 인력/차량 비용이 올라가면 같은 ride 수에서도 rebalancing cost가 커진다.

### P10 / P50 / P90

| 지표 | 의미 |
| --- | --- |
| `P10` | 나쁜 쪽 10% scenario에서의 Objective value 기준. downside risk로 해석 |
| `P50` | scenario들의 중앙값 |
| `P90` | 좋은 쪽 10% scenario에서의 Objective value 기준 |
| `P90-P10` | 같은 배치안의 profit 변동폭 |

Simulation은 solver를 대체하는 algorithm이 아니다. 선택된 model과 배치안이 불확실한 운영 환경에서 얼마나 안정적인지 검증하는 단계다.

---

## 10. 현재 Dashboard Run 결과

`outputs/visualizations/optimization_model.html`은 위 모델을 시각화한다. 현재 생성된 dashboard run의 기준은 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| dashboard latest timestamp | 2026-06-14T15:05:04+0900 |
| model zones | 372 |
| GBIKE devices in snapshot | 2,804 |
| ALPACA devices in snapshot | 1,368 |
| optimization fleet `F` | 500 |
| allocated devices | 500 |
| active zones | 71 |
| expected rides | 1,703.6 |
| expected revenue | 3,747,930 KRW |
| expected variable cost | 511,081 KRW |
| expected fixed cost | 1,250,000 KRW |
| expected rebalancing cost | 1,543,638 KRW |
| expected profit / Objective value | 443,210 KRW |

전처리 최신 시각과 dashboard run 시각이 다를 수 있다. 이는 데이터 수집이 계속 진행되는 동안 visualization이 특정 snapshot 기준으로 생성되기 때문이다. 보고서에는 반드시 "전처리 최신 데이터 기준"과 "dashboard run 기준"을 구분해서 적는 것이 좋다.

---

## 11. Competitor 또는 신규 진입자 모델 확장

ALPACA 또는 신규 진입자는 GBIKE 배치를 외생 변수로 관찰하고 자기 배치를 최적화한다고 볼 수 있다.

운영사 집합:

```math
k \in \{\text{GBIKE}, \text{ALPACA}, \text{NEW}\}
```

각 운영사의 배치:

```math
x_{ik} = \text{operator } k \text{가 zone } i \text{에 배치하는 PM 수}
```

사용자 선택확률까지 모델링하려면 discrete choice 구조를 붙일 수 있다.

```math
V_{ik}
=\alpha_{\text{access}}\log(1+x_{ik})
-\alpha_{\text{price}}price_k
+\alpha_{\text{brand}}brand_{ik}
+\alpha_{\text{quality}}quality_{ik}
```

```math
P_{ik}=\frac{\exp(V_{ik})}{\sum_l \exp(V_{il})}
```

이 식의 underlying hypothesis는 다음이다.

- 사용자는 가까운 PM이 많을수록 그 운영사를 선택할 가능성이 높다.
- 가격이 높을수록 선택확률은 낮아진다.
- 브랜드 선호와 기기 품질이 높을수록 선택확률은 올라간다.
- 접근성 효과는 `log(1+x)`로 체감한다.

신규 진입자의 기대 ride는 다음처럼 둘 수 있다.

```math
Q_{i,\text{NEW}}
=D_i P_{i,\text{NEW}}
\left(1-e^{-\beta_{\text{NEW}}x_{i,\text{NEW}}}\right)
```

진입 여부까지 모델링하려면 binary variable을 둔다.

```math
y_i \in \{0,1\}
```

```math
x_{i,\text{NEW}} \le M y_i
```

이 확장은 "GBIKE의 방어적 최적 배치"와 "ALPACA/신규 진입자의 공격적 최적 배치"를 같은 framework에서 비교하게 해준다.

---

## 12. 보고서에 쓰기 좋은 구조

과제 보고서에서는 다음 순서가 가장 설득력 있다.

1. **Data**

   Sejong TAGO PM snapshot은 운영사별 device 위치, 배터리, 이동 흔적을 제공한다.

2. **Demand Construction**

   연속 snapshot 간 이동을 이용해 inferred ride를 만들고, origin zone별 count를 기본 수요 `D_i`로 사용한다.

3. **Linear Baseline**

   먼저 zone별 PM 1대당 순이익이 고정된 Linear Optimization benchmark를 제시한다.

4. **Non-linear Main Model**

   실제 배치 문제에서는 접근성 효과가 포화되므로 `1-exp(-...)` 형태의 demand capture function을 사용한다.

5. **Competition**

   ALPACA 공급량은 market validation과 competition pressure를 동시에 반영한다.

6. **Constraints**

   fleet size, zone capacity, device throughput, non-negativity, integer feasibility를 둔다.

7. **Simulation**

   demand/cost shock를 주어 최적 배치 `x*`의 Objective value 분포와 downside risk를 평가한다.

8. **Business Interpretation**

   최적 배치는 단순 수요 순위가 아니라, demand potential, marginal capture, competitor density, capacity, rebalancing cost를 함께 균형화한 결과라고 해석한다.

---

## 13. Summary

이 프로젝트의 모델 흐름은 다음과 같다.

```text
TAGO device snapshots
-> 500m grid zone supply
-> device interval movement
-> inferred ride demand and OD flow
-> Linear Optimization baseline
-> Non-linear profit maximization
-> Simulation-based robustness evaluation
```

가장 중요한 모델링 선택은 다음 식이다.

```math
Q_i(x_i)
= \min \left\{
A_i\left(1-e^{-\frac{\beta x_i}{1+\theta C_i}}\right),
Ux_i
\right\}
```

이 식은 다음을 동시에 반영한다.

- GBIKE가 많을수록 접근성이 높아진다.
- 접근성 효과는 체감한다.
- ALPACA가 많을수록 GBIKE의 수요 capture가 약해진다.
- PM 1대가 처리할 수 있는 하루 ride 수는 제한된다.

따라서 이 모델은 단순한 지도 시각화나 수요 랭킹이 아니라, 실제 Sejong PM snapshot을 기반으로 한 **profit-maximizing non-linear deployment model**이다.
