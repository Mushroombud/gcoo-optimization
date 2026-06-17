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
| raw snapshot files | 1,329 |
| device-snapshot rows | 5,539,886 |
| latest raw timestamp | 2026-06-18T02:15:18+0900 |
| latest devices | 4,191 |
| latest GBIKE devices | 2,823 |
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
| device intervals | 5,535,695 |
| moved >= 50m intervals | 23,858 |
| moved >= 200m intervals | 22,643 |
| inferred ride segments | 22,097 |
| clean demand segments | 21,022 |
| inferred ride avg distance | 898.7m |
| inferred ride avg speed | 8.8km/h |
| Origin-Destination Pairs | 3,219 |

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
| `excluded_from_demand` | `true`이면 `D_i`, Origin-Destination Pair flow, 최적화 수요 계산에서 제외 |

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
\max_x \sum_i \left[(p_i-v)Q_i(x_i)-r_i(x_i)\right]
```

각 항의 의미는 다음과 같다.

| Term | 의미 |
| --- | --- |
| `(p_i-v)` | ride 1건당 순수 운행마진 |
| `p_i` | zone `i`에서 ride 1건이 만드는 평균 매출 |
| `v` | ride 1건당 변동비 |
| `Q_i(x_i)` | zone `i`에서 실제로 잡을 수 있는 기대 ride 수 |
| `r_i(x_i)` | 이용 후 흩어진 PM을 회수/재배치하는 기대 비용 |

`(p_i-v)Q_i(x_i)`는 운행으로 벌어들이는 기대 이익이다. `p_i`에서 `v`를 빼는 이유는 ride 1건이 발생할 때 매출만 생기는 것이 아니라 결제 수수료, 정비, 소모품, 고객지원 등 ride 수에 비례하는 비용도 같이 발생하기 때문이다.

`-r_i(x_i)`는 PM이 이용 후 다른 zone으로 흩어졌을 때 다음 운영 시작 전에 다시 회수하거나 재배치하는 비용이다. 다만 여러 대를 한 번에 회수할 수 있는 묶음 이동 효과를 반영하기 위해 기대 ride 수는 `log(1+Q_i(x_i))`로 완만하게 보정한다.

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

현재 dashboard run에서는 `F=2800`으로 두고, 2,800대를 반드시 배치하는 planning problem으로 계산한다.

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
| `F` | 2,800 | 이번 run에서 배치할 전체 GBIKE PM 수 |
| `λ` | 0.30 | 경쟁사 존재를 market validation으로 반영하는 강도 |
| `β` | 0.08 | GBIKE 배치량이 수요 capture로 전환되는 속도 |
| `θ` | 1.00 | ALPACA 공급량이 GBIKE capture를 약화시키는 정도 |
| `U` | 6.0 rides/device/day | PM 1대가 하루 처리 가능한 최대 ride 수 |
| `p_i` | 2,200 KRW | 현재 dashboard에서는 zone 공통 ride 1건 평균 매출 |
| `v` | 300 KRW | ride 1건당 변동비 |
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
| `L_i` | clean Origin-Destination Pair flow 기반 expected rebalancing km | 이용 후 PM 회수/재배치 거리 proxy |
| `r_i(x_i)` | `ρ * L_i * log(1 + Q_i(x_i))` | 묶음 이동 효과를 반영한 기대 재배치비 |

현재 구현의 rebalancing cost는 다음 구조다.

```math
r_i(x_i)=\rho L_i \log(1+Q_i(x_i))
```

`L_i`는 `sejong_pm_od_flows.csv`에서 origin zone `i`에서 출발한 clean ride들의 평균 이동거리로부터 추정한다. 재배치비는 이 거리 proxy에 `log(1+Q_i(x_i))`를 곱해 계산하므로, OD flow가 큰 zone도 대수만큼 선형으로 비용이 늘지 않고 묶음 회수의 규모 효과를 반영한다.

---

## 7. Linear Optimization Baseline

수업의 Linear Optimization과 연결하기 위해 baseline model을 둘 수 있다.

```math
\max_x \sum_i a_i x_i
```

여기서:

```math
a_i = (p_i-v)u_i-\rho_i
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
-\sum_i r_{is}(x_i^*)
```

### demand shock

실제 ride 수요가 예측보다 높거나 낮아지는 효과다. 수요가 커지면 운행매출과 ride당 변동비가 함께 증가하고, 수요가 작아지면 둘 다 줄어든다.

### cost shock

재배치비가 평소보다 비싸지거나 싸지는 효과다. 예를 들어 회수 동선이 길어지거나 인력/차량 비용이 올라가면 log 보정된 이동량 기준의 rebalancing cost가 커진다.

### P10 / P50 / P90

| 지표 | 의미 |
| --- | --- |
| `P10` | 나쁜 쪽 10% scenario에서의 Objective value 기준. downside risk로 해석 |
| `P50` | scenario들의 중앙값 |
| `P90` | 좋은 쪽 10% scenario에서의 Objective value 기준 |
| `P90-P10` | 같은 배치안의 profit 변동폭 |

Simulation은 solver를 대체하는 algorithm이 아니다. 선택된 model과 배치안이 불확실한 운영 환경에서 얼마나 안정적인지 검증하는 단계다.

---

## 10. Origin-Destination Pair based Temporal Inventory Simulation

위 main model은 하루 전체 기대수요를 `Q_i(x_i)`로 압축하는 static deployment model이다. 이 구조는 04:00 초기 배치를 설명하기 좋지만, 사용자가 실제로 PM을 타고 이동하면 공급도 zone 사이에서 이동한다. 따라서 다음 질문이 남는다.

```text
처음에는 A zone에 PM이 많더라도,
A -> B 이동이 많으면 시간이 지나며 B zone 공급이 늘어나는 효과가 반영되는가?
```

이를 보완하기 위해 `outputs/visualizations/optimization_model.html`의 맨 아래에 **Origin-Destination Pair 기반 하루 재고 simulation**을 추가했다. 이 simulation은 `x*`를 다시 최적화하지 않는다. 대신 최적화 결과 `P* = {x_i*}`를 GCOO의 04:00 초기 배치로 고정하고, 관측된 Origin-Destination Pair flow를 이용해 하루 동안 PM 재고가 어떻게 이동하는지 사후 검증한다.

### 10.1 입력 데이터

사용하는 데이터는 다음이다.

| Input | 역할 |
| --- | --- |
| `sejong_pm_inferred_rides.csv` | 시간대별 clean ride segment 생성 |
| `excluded_from_demand=false` | 운영자 이동 의심 segment 제거 |
| `prev_zone_id` | origin 500m grid |
| `zone_id` | destination 500m grid |
| `timestamp` | 1시간 단위 simulation hour 계산 |
| `x_i*` | GCOO의 04:00 최적 초기 배치 |
| latest ALPACA supply `C_i` | ALPACA의 초기 공급량 |

GCOO와 ALPACA를 합친 market demand를 simulation하되, 초기 GCOO 배치는 반드시 main model의 `P*=x*`를 따른다. ALPACA는 latest snapshot의 zone별 공급량에서 시작한다.

### 10.2 시간대별 Origin-Destination Pair 회귀

관측된 clean ride segment를 04:00 기준 operating day, 시간대, Origin-Destination Pair로 집계한다.

```text
y_{d,h,o,r}
= day d, hour h에 origin o에서 destination r로 이동한 clean ride count
```

그 다음 로그 빈도 회귀를 추정한다.

```math
\log(1+y_{d,h,o,r})
= \alpha + \gamma_h + \delta_{o,r} + \epsilon_{d,h,o,r}
```

| Term | 의미 |
| --- | --- |
| `α` | baseline 이동 빈도 |
| `γ_h` | 시간대 효과 |
| `δ_{o,r}` | 특정 Origin-Destination Pair의 평균적 강도 |
| `ε` | 관측 noise |

현재 구현은 외부 통계 패키지를 쓰지 않고 balanced fixed-effect closed form을 sparse Origin-Destination Pair/hour count 집계로 계산한다. 실제 count 평균만 쓰면 sparse Origin-Destination Pair의 시간대별 rate가 불안정할 수 있으므로, 최종 rate는 관측 평균과 회귀 예측값을 섞어 만든다.

```math
\hat{\lambda}_{h,o,r}
=(1-w)\bar{y}_{h,o,r}
+w\left(\exp(\hat{\alpha}+\hat{\gamma}_h+\hat{\delta}_{o,r})-1\right)
```

현재 dashboard에서는 `w = 0.35`를 사용한다.

이때 위 식의 `\hat{\lambda}_{h,o,r}`는 Origin-Destination Pair의 시간대별 상대 패턴을 설명하는 base rate다. Simulation은 `P*`의 사후 검증이므로, 하루 총 수요 규모는 main optimization section의 기대 ride와 같은 기준으로 맞춘다.

```math
c_Q
=
\frac{\sum_i Q_i(x_i^*)}
{\sum_{h,o,r}\hat{\lambda}_{h,o,r}}
```

```math
\lambda^{cal}_{h,o,r}
=c_Q\hat{\lambda}_{h,o,r}
```

즉, Origin-Destination Pair flow의 방향과 시간대별 shape는 관측 inferred ride에서 오고, 하루 총량은 `sum_i Q_i(x_i*)`에 맞춘다. 따라서 dashboard의 `Target Q(x*) rides`는 optimization model의 기대 ride이고, `Simulated combined demand`는 correlated shock와 Poisson draw를 거친 특정 synthetic day의 실현 demand다. 두 값은 random draw 때문에 완전히 같지는 않지만, simulation의 기대 총량은 `Target Q(x*) rides`와 일치한다.

### 10.3 Correlated Random Value 생성

시간대별 Origin-Destination Pair 수요는 완전히 독립이라고 보기 어렵다. 예를 들어 특정 시간대에 전체 수요가 많거나, 어떤 origin 주변 수요가 같이 올라갈 수 있다. 그래서 common shock와 origin shock를 섞은 correlated normal value를 만든다.

```math
Z_{h,o,r}
=\rho_c Z_h+\rho_o Z_{h,o}
+\sqrt{1-\rho_c^2-\rho_o^2}\epsilon_{h,o,r}
```

이 `Z`는 standard normal random value다. Excel로 표현하면 correlated uniform 분위수 `U`에 `NORM.INV(U,0,1)`를 적용해 얻는 값과 같은 역할을 한다. 구현에서는 `numpy` normal draw를 직접 사용한다.

해당 shock를 Origin-Destination Pair rate에 곱해 stochastic demand rate를 만든다.

```math
\lambda'_{h,o,r}
=\lambda^{cal}_{h,o,r}
\cdot
\exp\left(\sigma Z_{h,o,r}-\frac{1}{2}\sigma^2\right)
```

그리고 최종 Origin-Destination Pair demand count는 Poisson draw로 만든다.

```math
Y_{h,o,r}^{sim}\sim\text{Poisson}(\lambda'_{h,o,r})
```

### 10.4 시간대별 재고 전이

각 zone의 PM 재고를 `S_{i,t}`라고 두면, simulation은 다음 순서로 진행된다.

```text
1. 04:00 초기 재고를 만든다.
   S_i,04:00 = x_i* + latest ALPACA supply_i

2. 각 시간대 h에서 origin별 총 demand를 계산한다.

3. origin 재고가 충분하면 demand를 처리한다.
   served_i,h = demand_i,h

4. origin 재고가 부족하면 가능한 만큼만 처리한다.
   served_i,h = S_i,h
   unmet_i,h = demand_i,h - S_i,h

5. 처리된 ride는 destination 재고로 더한다.
```

수식으로는 다음과 같다.

```math
S_{i,t+1}
=S_{i,t}
-\sum_j served_{i,j,t}
+\sum_k served_{k,i,t}
```

부족량은 다음처럼 기록한다.

```math
unmet_{i,t}
=\max\left(0,\sum_j demand_{i,j,t}-S_{i,t}\right)
```

`temporal_inventory_shortages.csv`에는 shortage가 발생한 모든 시간대와 zone이 다음 컬럼으로 저장된다.

| Column | 의미 |
| --- | --- |
| `hour_label` | simulation 시간대 |
| `zone_id` | PM이 부족했던 origin zone |
| `demand` | 해당 시간대 origin 총 수요 |
| `served` | 실제 처리된 ride 수 |
| `shortage` | 수요가 있었지만 PM이 부족해 처리하지 못한 ride 수 |
| `available_at_hour_start` | 시간대 시작 시 origin 재고 |
| `inventory_after_departures` | 출발 처리 후 origin 잔여 재고 |

또한 `temporal_inventory_od_movements.csv`에는 모든 simulated Origin-Destination Pair movement가 저장된다. 이 파일은 animation에 표시되는 top flow뿐 아니라, simulation에 사용된 전체 Origin-Destination Pair/hour record를 검산하기 위한 audit log다.

| Column | 의미 |
| --- | --- |
| `hour_label` | simulation 시간대 |
| `origin`, `destination` | Origin-Destination Pair grid |
| `demand` | correlated random value로 생성된 Origin-Destination Pair 수요 |
| `served` | origin 재고로 실제 처리된 이동 |
| `unmet` | 해당 Origin-Destination Pair에서 처리하지 못한 수요 |
| `rate` | `sum_i Q_i(x_i*)` 총량에 맞게 calibration된 최종 Origin-Destination Pair/hour rate |
| `base_rate` | calibration 전 empirical/regression blended base rate |
| `regression_rate` | 로그 빈도 회귀에서 나온 예측 rate |
| `empirical_rate` | 관측일 평균 Origin-Destination Pair/hour 빈도 |
| `normal_z`, `normal_quantile` | NORM.INV 원리의 correlated random shock와 그 분위수 |

### 10.5 Geo-based HTML Animation

`optimization_model.html` 하단의 simulation은 별도 지도 파일 `temporal_inventory_map.html`로 렌더링된다. 이 지도는 기존 Sejong folium/Leaflet 기반 geo-visualization과 같은 방식으로 CartoDB/OpenStreetMap tile을 깔고, 실제 위경도 좌표 위에 500m grid cell과 Origin-Destination Pair movement를 올린다.

- 시간대별 Origin-Destination Pair 이동: 초록 선
- Origin-Destination Pair 이동 방향: 초록 화살표
- 500m grid cell별 재고: 파란/청록 사각형
- 해당 시간대 shortage origin: 붉은 grid cell과 붉은 원
- destination inflow: 파란 점
- 시간대별 demand, served, unmet, service rate control
- shortage table과 전체 shortage/movement CSV 링크

첫 화면은 전체 Origin-Destination Pair bounding box가 아니라 peak shortage hour의 shortage cluster에 맞춰 열린다. 그래야 dashboard를 처음 열었을 때 어느 500m grid에서 공급 부족이 발생했는지 바로 읽을 수 있다.

이 geo-animation은 main solver 결과를 대체하지 않는다. 오히려 `P*`가 실제 Origin-Destination Pair flow를 통과했을 때 어느 지점에서 잘 버티고, 어느 500m grid에서 막히는지 보여주는 validation layer다. `unmet_rate`가 작으면 `P*`가 실제 최적 배치에 가깝다는 근거가 된다. 반대로 특정 zone과 시간대에서 shortage가 반복되면, static objective가 시간대별 공급 부족을 충분히 반영하지 못한다는 신호다.

---

## 11. λ, β, θ Calibration Simulation

`λ`, `β`, `θ`는 non-linear demand capture 구조의 핵심 상수다. 기존 dashboard baseline은 `λ=0.30`, `β=0.08`, `θ=1.00`을 사용하지만, 이 값들은 외부 운영 데이터로 직접 추정된 값이 아니므로 arbitrary해 보일 수 있다.

이를 보완하기 위해 `optimization_model.html` 하단에 **상수 Calibration Simulation** section을 추가했다. 이 section은 버튼을 누를 때만 local visualization server의 `POST /api/parameter-search` endpoint를 호출한다. Full-grid run은 허용하되, CPU-bound Python 계산에서 실제 core를 쓰기 위해 `ProcessPoolExecutor` 기반 worker pool로 parameter 조합을 병렬 평가한다.

### 11.1 목적함수

parameter calibration의 목적은 profit을 다시 최대화하는 것이 아니라, 같은 synthetic operating day set에서 공급 부족을 최소화하는 것이다.

```math
\min_{\lambda,\beta,\theta} \sum_t \sum_i unmet_{i,t}
```

여기서 `unmet_{i,t}`는 시간대 `t`, origin zone `i`에서 수요는 있었지만 재고 부족으로 처리하지 못한 ride 수다.

### 11.2 실행 방식

`λ`, `β`, `θ`를 작은 step 단위 grid로 열거한다. 현재 기본 grid는 `λ` 0.05 단위, `β` 0.005 단위, `θ` 0.05 단위다. 그 결과 28,675개 parameter 조합이 만들어지고, 각 조합마다 100개 demand trial을 실행하면 총 2,867,500개 case가 된다.

현재 local benchmark가 100 case / 4초라면 2,867,500개 case는 단일 worker 기준 약 114,700초, 즉 약 31.9시간이 필요하다. 현재 12 CPU 환경에서는 cron scheduler가 사용할 2개 core를 남기고 10 worker를 사용하므로, 단순 병렬 추정은 약 3.2시간이다. 실제 runtime은 process spawn, worker별 pandas copy, OS scheduling, candidate별 계산 편차 때문에 달라질 수 있다.

Python `threading`은 GIL 때문에 CPU-bound loop를 여러 core에 잘 분산하지 못한다. 따라서 구현상으로는 thread가 아니라 process worker pool을 사용한다. dashboard에서는 multi-core full-run으로 표현하며, 내부적으로는 worker process가 parameter candidate를 나눠 처리한다.

| Parameter | Search range | Step | Grid count | 의미 |
| --- | ---: | ---: | ---: | --- |
| `λ` | 0.00 - 1.20 | 0.05 | 25 | 경쟁사 존재를 market validation으로 반영하는 강도 |
| `β` | 0.02 - 0.20 | 0.005 | 37 | GBIKE 배치량이 수요 capture로 전환되는 속도 |
| `θ` | 0.00 - 1.50 | 0.05 | 31 | 경쟁 공급량이 GBIKE capture를 약화시키는 정도 |

각 trial은 다음 순서로 실행된다.

```text
1. λ, β, θ grid candidate를 하나 선택한다.
2. A_i = D_i(1 + λ competition_index_i)를 다시 계산한다.
3. Q_i(x_i)의 β, θ를 해당 candidate 값으로 바꾼다.
4. fleet F=2,800 제약 아래 x*를 다시 계산한다.
5. 모든 candidate에 동일한 100개 Origin-Destination Pair demand scenario set을 적용한다.
6. 각 demand trial마다 시간대별 재고 simulation을 돌려 unmet rides를 계산한다.
7. 100개 trial의 평균 unmet rides가 가장 작은 candidate를 best parameter로 선택한다.
```

중요한 점은 모든 parameter 조합이 **같은 100개 demand scenario set**으로 평가된다는 것이다. 그렇지 않으면 낮은 `λ` 또는 낮은 `β`가 demand 자체를 작게 만들어 unmet rides를 줄이는 것처럼 보일 수 있다. 현재 구현은 demand scenario set을 고정하고, parameter가 만드는 배치안 `x*`만 바꿔서 비교한다.

### 11.3 Frontend/Backend Control

Frontend의 `Full-run 시뮬레이션 시작하기` 버튼을 누르면 loading indicator가 켜지고 버튼이 disabled 된다. 완료 전까지 같은 page에서 추가 request를 보낼 수 없다. 같은 화면은 `GET /api/parameter-search-progress`를 1초 간격으로 polling해서 실제 완료된 parameter 조합 수와 case 수를 읽고, `completed_cases / elapsed_seconds` 기준의 실제 처리속도로 ETA를 갱신한다.

Backend도 `threading.Lock`으로 동일 endpoint의 동시 실행을 막는다. 이미 실행 중인 request가 있으면 `409`를 반환한다. Full-run은 `os.cpu_count() - 2` worker로 실행되며, 요청에서 `max_workers`를 넘기면 해당 값을 상한 내에서 사용할 수 있다. Worker process는 OS nice `+10`으로 낮은 priority에서 실행해, CPU-bound full-run 중에도 server, browser, editor 응답성이 먼저 확보되도록 한다.

OOM 방지를 위해 2,867,500개 case를 한 번에 메모리에 쌓지 않는다. 100개 Origin-Destination Pair demand scenario는 compact representation으로 변환한 뒤 worker 초기화 시 전달하고, 각 worker는 parameter 조합을 맡아 summary만 반환한다. case-level movement log는 calibration 중 저장하지 않는다. 완료 후 다음 항목을 `parameter_search_results.json`에 저장한다.

Full-run 완료 후 `이 최적값 반영하기` 버튼을 누르면 best `λ/β/θ`가 `optimization_model_constants.json`에 승인값으로 저장된다. Frontend에는 `(5분 내로 재계산 시 반영됩니다)` 문구를 표시한다. 다음 collector 또는 visualization 재계산 때 `visualize_optimization_model.render()`는 이 승인파일을 읽고, `λ`는 `build_zone_model()`, `β/θ`는 `optimize_dashboard_solution()`의 실제 입력 상수로 사용한다.

| 저장 항목 | 의미 |
| --- | --- |
| `parameter_combination_count` | 평가한 λ/β/θ grid 조합 수 |
| `trials_per_parameter_combination` | 각 parameter 조합별 demand simulation 반복 횟수 |
| `case_count` | 전체 평가 case 수 |
| `estimated_runtime_seconds` | 현재 benchmark 기준 예상 실행 시간 |
| `elapsed_seconds` | 실제 full-run 완료까지 걸린 시간 |
| `actual_cases_per_second` | 실제 완료 case 수와 경과 시간으로 계산한 처리속도 |
| `worker_count` | calibration에 사용한 worker process 수 |
| `reserved_cpu_cores` | cron scheduler 등을 위해 남긴 CPU core 수 |
| `baseline_score` | 기존 `λ=0.30`, `β=0.08`, `θ=1.00` 기준 평균 unmet rides |
| `best` | 평균 unmet rides가 가장 낮은 parameter 조합 |
| `top_results` | 상위 10개 parameter 조합 |

| 승인 저장 항목 | 의미 |
| --- | --- |
| `optimization_model_constants.json.lambda_market` | 다음 재계산에서 사용할 승인 `λ` |
| `optimization_model_constants.json.beta_capture` | 다음 재계산에서 사용할 승인 `β` |
| `optimization_model_constants.json.theta_competition` | 다음 재계산에서 사용할 승인 `θ` |
| `optimization_model_constants.json.best_summary` | 승인 당시 best candidate의 핵심 score audit |

이 calibration layer는 “현재 baseline parameter가 틀렸다”는 최종 결론을 자동으로 내리는 장치가 아니다. 기존 상수값을 고정 assumption으로 두지 않고, unmet ride 기준으로 재검증할 수 있게 하는 장치다.

---

## 12. 현재 Dashboard Run 결과

`outputs/visualizations/optimization_model.html`은 위 모델을 시각화한다. 현재 생성된 dashboard run의 기준은 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| dashboard latest timestamp | 2026-06-18T04:25:03+0900 |
| model zones | 356 |
| GBIKE devices in snapshot | 2,823 |
| ALPACA devices in snapshot | 1,368 |
| optimization fleet `F` | 2,800 |
| allocated devices | 2,800 |
| active zones | 108 |
| expected rides | 13,263.6 |
| expected revenue | 29,179,830 KRW |
| expected variable cost | 3,979,068 KRW |
| expected rebalancing cost | 377,556 KRW |
| expected profit / Objective value | 24,823,206 KRW |

Origin-Destination Pair 기반 하루 재고 simulation의 현재 결과는 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| initial GCOO `P*` supply | 2,800 |
| initial ALPACA latest supply | 1,368 |
| target `Q(x*)` rides | 3,695.9 |
| modeled Origin-Destination Pairs | 3,221 |
| demand calibration factor | 1.16 |
| simulated combined demand | 3,711 |
| served rides | 2,969 |
| unmet rides | 742 |
| service rate | 80.0% |
| shortage zones | 73 |
| shortage events | 301 |
| peak shortage hour | 16:00-17:00 |

전처리 최신 시각과 dashboard run 시각이 다를 수 있다. 이는 데이터 수집이 계속 진행되는 동안 visualization이 특정 snapshot 기준으로 생성되기 때문이다. 보고서에는 반드시 "전처리 최신 데이터 기준"과 "dashboard run 기준"을 구분해서 적는 것이 좋다.

---

## 13. Competitor 또는 신규 진입자 모델 확장

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

## 13. 보고서에 쓰기 좋은 구조

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

8. **Origin-Destination Pair Inventory Simulation**

   `P*=x*`를 초기 GCOO 배치로 고정하고, 시간대별 Origin-Destination Pair flow를 따라 하루 PM 재고가 어떻게 이동하는지 simulation한다. 부족 zone과 부족 대수를 기록해 static model의 under-coverage를 측정한다.

9. **Business Interpretation**

   최적 배치는 단순 수요 순위가 아니라, demand potential, marginal capture, competitor density, capacity, rebalancing cost를 함께 균형화한 결과라고 해석한다.

---

## 14. Summary

이 프로젝트의 모델 흐름은 다음과 같다.

```text
TAGO device snapshots
-> 500m grid zone supply
-> device interval movement
-> inferred ride demand and Origin-Destination Pair flow
-> Linear Optimization baseline
-> Non-linear profit maximization
-> Simulation-based robustness evaluation
-> Origin-Destination Pair based temporal inventory validation
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
