# Data and Model Sheet: Sejong GBIKE 04:00 Deployment Optimization

## 1. Purpose

이 문서는 현재 저장소가 보유한 **세종특별시 TAGO 공유 PM 스냅샷 데이터**를 바탕으로, GBIKE/GCOO의 매일 04:00 최적 배치 문제를 어떻게 **Linear Optimization**, **Non-linear Optimization**, 그리고 **Simulation** 문제로 모델링할 수 있는지 설명한다.

우리가 최종적으로 풀고 싶은 질문은 다음이다.

```text
세종특별시에서 매일 04:00에 각 500m grid zone에 GBIKE PM을 몇 대 배치해야
하루 기대 profit이 최대가 되는가?
```

경쟁사 관점에서는 같은 구조로 다음 질문을 풀 수 있다.

```text
ALPACA 또는 신규 진입자가 GBIKE의 기존 배치를 관찰했을 때,
어느 zone에 몇 대를 배치해야 자기 profit이 최대가 되는가?
```

이 문서에서 중요한 것은 특정 solver 알고리즘이 아니라, **최적화 모델의 구조**이다. 즉, 변수, 목적함수, 제약식, 수요함수, 경쟁효과, 비용항을 어떻게 정의하는지가 핵심이다.

---

## 2. Current Sejong Raw Data

## 2.1 Raw TAGO PM Snapshot Files

원천 데이터는 TAGO Personal Mobility API에서 수집한 세종특별시 공유 PM 스냅샷이다.

파일 패턴:

```text
data/raw/tago_pm_snapshots_sejong_*.csv
data/raw/api/tago_pm/*
```

현재 처리 요약:

```text
raw snapshot files: 396
device-snapshot rows: 1,646,957
latest timestamp: 2026-06-14T00:15:11+0900
latest devices: 4,171
latest GBIKE devices: 2,803
latest ALPACA devices: 1,368
zone size: 500m grid
```

대표 raw/processed row의 주요 컬럼:

| Column | Meaning |
| --- | --- |
| `timestamp` | 스냅샷 수집 시각 |
| `operator_name` | 운영사명, 현재 주요 값은 `GBIKE`, `ALPACA` |
| `device_id` | PM 기기 ID |
| `battery_level` | 배터리 잔량 |
| `latitude` | 위도 |
| `longitude` | 경도 |
| `city_code` | TAGO 도시 코드 |
| `city_name` | 도시명, 세종특별시 |

이 데이터의 장점은 외부 교통수단 proxy가 아니라, **실제 공유 PM 기기의 위치, 운영사, 배터리 상태**를 직접 관측한다는 점이다.

---

## 2.2 Spatial Unit: 500m Grid Zone

현재 Sejong 모델은 500m grid를 zone으로 사용한다.

처리 파일:

```text
data/processed/sejong_tago/sejong_pm_latest_snapshot.csv
data/processed/sejong_tago/sejong_pm_zone_snapshot_counts.csv
```

생성 컬럼:

| Column | Meaning |
| --- | --- |
| `zone_id` | 500m grid zone ID |
| `zone_lat_index` | 위도 방향 grid index |
| `zone_lon_index` | 경도 방향 grid index |
| `zone_center_latitude` | zone 중심 위도 |
| `zone_center_longitude` | zone 중심 경도 |

왜 500m grid를 쓰는가:

- PM 이용자는 보통 가까운 거리 안의 기기를 찾는다.
- 행정구역보다 운영 의사결정 단위가 더 세밀하다.
- 정확한 sidewalk-level 배치보다 데이터 요구량이 낮다.
- 04:00 배치 의사결정에서 "어느 생활권 블록에 몇 대를 둘 것인가"를 표현하기 적절하다.

모델에서:

```text
i ∈ I = 세종특별시 500m grid zone 집합
```

---

## 2.3 Processed Tables

현재 Sejong 전처리 결과는 다음 파일들에 저장된다.

```text
data/processed/sejong_tago/sejong_pm_snapshots_accumulated.csv
data/processed/sejong_tago/sejong_pm_latest_snapshot.csv
data/processed/sejong_tago/sejong_pm_operator_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_zone_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_device_intervals.csv
data/processed/sejong_tago/sejong_pm_activity_by_zone.csv
data/processed/sejong_tago/sejong_pm_inferred_rides.csv
data/processed/sejong_tago/sejong_pm_od_flows.csv
```

각 테이블의 역할은 다음과 같다.

| Table | Role in model |
| --- | --- |
| `sejong_pm_snapshots_accumulated.csv` | 모든 수집 시점의 device-level PM 상태 |
| `sejong_pm_latest_snapshot.csv` | 가장 최근 시점의 PM 배치 상태 |
| `sejong_pm_operator_snapshot_counts.csv` | 시점별 운영사별 총 공급량 |
| `sejong_pm_zone_snapshot_counts.csv` | 시점별 zone/operator별 공급량 |
| `sejong_pm_device_intervals.csv` | 같은 device의 연속 스냅샷 간 이동 거리 |
| `sejong_pm_activity_by_zone.csv` | zone/operator별 이동 activity summary |
| `sejong_pm_inferred_rides.csv` | 이동한 device interval을 ride segment로 추정한 데이터 |
| `sejong_pm_od_flows.csv` | inferred ride의 zone-to-zone OD flow |

---

## 3. From Snapshots To Demand

## 3.1 Device Interval

같은 운영사와 같은 device ID에 대해 시간순으로 스냅샷을 정렬하고, 직전 위치와 현재 위치를 비교한다.

```text
interval = same operator_name + same device_id
           between consecutive timestamps
```

생성되는 주요 값:

```text
prev_zone_id
zone_id
interval_minutes
distance_m
speed_kmph
battery_delta
same_zone
```

현재 처리 결과:

```text
device intervals: 1,642,786
moved >= 50m intervals: 8,085
moved >= 200m intervals: 7,655
```

해석:

대부분의 device는 스냅샷 사이에 움직이지 않는다. 위치가 충분히 변한 interval은 실제 이용, 수거/재배치, GPS noise 중 하나일 수 있다. 그중 ride에 가까운 후보를 추출한다.

---

## 3.2 Inferred Ride Segment

현재 ride segment는 다음 조건으로 추정한다.

```text
4 min <= interval_minutes <= 6 min
distance_m >= 100m
```

현재 결과:

```text
inferred ride segments: 6,877
operator: GBIKE only in current inferred ride output
average distance: 1,033.8m
average speed: 12.4km/h
OD pairs: 2,212
```

왜 이 조건인가:

- 약 5분 간격의 스냅샷 사이에서 device가 100m 이상 이동했다면 단순 GPS jitter보다 실제 이동일 가능성이 크다.
- 평균 속도 약 12km/h는 PM의 도시 내 단거리 이동 속도와 잘 맞는다.
- 너무 긴 interval은 중간 경로를 알 수 없고, 너무 짧은 이동은 GPS 오차일 수 있다.

주의:

이 데이터는 "완전한 실제 ride log"가 아니라 **snapshot 기반 inferred ride**이다. 따라서 모델에서는 확정 수요가 아니라 observed activity signal로 사용한다.

---

## 3.3 Zone-Level Demand Signal

각 zone `i`, scenario time/day `s`에 대해 다음 값을 만든다.

```text
R_is = number of inferred ride segments starting from zone i in scenario s
```

또는 시간대별 모델에서는:

```text
R_iτ = number of inferred ride segments starting from zone i during time block τ
```

여기서 `τ`는 04:00 이후의 운영 시간대를 의미한다.

예:

```text
τ ∈ {morning, daytime, evening, night}
```

모델에서의 수요 potential:

```text
D_is = scaled demand potential in zone i under scenario s
```

기본적으로:

```text
D_is = η * R_is
```

`η`는 관측된 inferred ride가 전체 실제 수요 중 얼마를 대표하는지 보정하는 scale parameter이다. 스냅샷 주기가 모든 ride를 완전히 포착하지 못하므로, `η`는 sensitivity analysis 대상이다.

---

## 4. Supply And Competition Variables From Data

## 4.1 Observed Supply

시점 `s`, zone `i`, 운영사 `k`의 관측 공급량:

```text
N_isk = number of effective devices of operator k in zone i at snapshot s
```

현재 운영사:

```text
k ∈ {GBIKE, ALPACA}
```

배터리 유효 조건:

```text
battery_effective = battery_level is missing OR battery_level >= 20
```

현재 최신 스냅샷에서는 모든 device가 effective로 처리되어 있다.

GBIKE 기준:

```text
G_is = N_is,GBIKE
C_is = N_is,ALPACA
```

ALPACA 기준으로 보면 반대로:

```text
G_is = N_is,ALPACA
C_is = N_is,GBIKE
```

---

## 4.2 04:00 Baseline Placement

우리의 의사결정 시점은 04:00이다.

실제 모델에서는 각 날짜 `d`에 대해 04:00 근처 스냅샷을 선택한다.

```text
S_04(d) = snapshots whose timestamp is closest to 04:00 on day d
```

baseline placement:

```text
x_i^obs = average observed GBIKE devices in zone i near 04:00
```

경쟁사 baseline:

```text
c_i^obs = average observed ALPACA devices in zone i near 04:00
```

왜 04:00인가:

- 새벽 시간은 이용 수요가 상대적으로 낮아 배치 reset을 가정하기 좋다.
- 아침 피크 전 공급을 결정한다.
- 하루 수익을 결정하는 첫 상태 변수로 해석할 수 있다.

---

## 5. Core Decision Problem

GBIKE 관점의 핵심 decision variable은 다음이다.

```text
x_i >= 0
```

의미:

```text
04:00에 zone i에 배치할 GBIKE PM 수
```

정수 모델이라면:

```text
x_i ∈ Z_+
```

연속 근사 모델이라면:

```text
x_i >= 0
```

수업에서 배운 optimization 관점에서는 두 단계로 제시할 수 있다.

1. Linear Optimization baseline
2. Non-linear Optimization main model

Linear model은 benchmark이고, Non-linear model이 실제 공유 PM 배치의 핵심 특성을 더 잘 반영한다.

---

## 6. Linear Optimization Baseline

## 6.1 Purpose

Linear Optimization baseline은 단순하지만 해석 가능한 비교 기준이다.

질문:

```text
각 zone의 scooter 1대당 기대 순이익이 주어졌을 때,
제약하에서 어디에 몇 대를 배치해야 하는가?
```

## 6.2 Variables

```text
x_i = number of GBIKE devices placed in zone i at 04:00
```

## 6.3 Parameters

| Symbol | Meaning |
| --- | --- |
| `F` | total GBIKE fleet available for deployment |
| `K_i` | maximum feasible devices in zone i |
| `D_i` | expected daily demand potential in zone i |
| `p_i` | expected revenue per ride in zone i |
| `v` | variable cost per ride |
| `c_i` | fixed daily cost per deployed device |
| `u_i` | expected rides per scooter in zone i |
| `ρ_i` | expected rebalancing cost per scooter in zone i |

Linear per-scooter profit coefficient:

```text
a_i = (p_i - v) * u_i - c_i - ρ_i
```

## 6.4 Objective

```text
maximize Σ_i a_i x_i
```

## 6.5 Constraints

Fleet size:

```text
Σ_i x_i <= F
```

Zone capacity:

```text
0 <= x_i <= K_i
```

Optional minimum coverage:

```text
x_i >= L_i    for strategic zones
```

Battery-effective supply:

```text
x_i <= available_charged_fleet_i
```

## 6.6 Why This Is Linear

목적함수와 제약식이 모두 `x_i`의 선형 결합이다. 이 모델은 수업의 Linear Optimization 내용과 직접 연결된다.

## 6.7 Limitation

Linear model은 scooter 1대의 수익성이 항상 같다고 가정한다. 하지만 실제로는 같은 zone에 scooter를 많이 둘수록 추가 1대의 효과가 줄어든다. 그래서 main model은 Non-linear Optimization으로 가야 한다.

---

## 7. Non-linear Optimization Main Model

## 7.1 Key Hypothesis

공유 PM 배치의 핵심 가설은 다음이다.

```text
zone i에 GBIKE를 더 많이 배치하면 사용자가 GBIKE를 발견하고 이용할 확률이 증가한다.
하지만 배치량 증가의 한계효과는 체감한다.
또한 ALPACA가 많이 배치된 zone에서는 GBIKE의 수요 capture가 약해진다.
```

따라서 수요는 배치량 `x_i`의 선형함수가 아니라 비선형함수로 두는 것이 자연스럽다.

---

## 7.2 Demand Capture Function

GBIKE가 zone `i`, scenario `s`에서 service할 기대 ride 수:

```text
Q_is(x_i)
  = min {
      A_is * [1 - exp(-β x_i / (1 + θ C_is))],
      U x_i
    }
```

| Symbol | Meaning |
| --- | --- |
| `Q_is(x_i)` | expected GBIKE rides captured in zone i, scenario s |
| `A_is` | adjusted demand potential |
| `x_i` | GBIKE devices placed in zone i at 04:00 |
| `C_is` | competitor devices, e.g. ALPACA count |
| `β` | accessibility/capture sensitivity |
| `θ` | competition pressure |
| `U` | maximum rides one device can serve per day |

## 7.3 Why `1 - exp(-βx)`?

이 함수는 다음 성질을 가진다.

```text
x_i = 0이면 Q_is = 0
x_i가 증가하면 Q_is 증가
x_i가 커질수록 추가 1대의 marginal benefit 감소
Q_is는 A_is를 넘지 않음
```

비즈니스 의미:

- 0대에서 5대로 늘리는 효과는 매우 크다.
- 이미 충분히 많은 zone에서 5대를 추가하는 효과는 작다.
- 사용자의 "가까운 PM을 찾을 확률"은 배치량에 대해 포화된다.

## 7.4 Why Competition In The Denominator?

```text
β x_i / (1 + θ C_is)
```

경쟁사 PM이 많으면 같은 GBIKE 공급량의 수요 capture 효과가 약해진다는 가정이다.

예:

- ALPACA가 거의 없는 zone에서는 GBIKE 1대 추가의 접근성 효과가 크다.
- ALPACA가 이미 많은 zone에서는 사용자가 ALPACA를 선택할 가능성이 있으므로 GBIKE 1대 추가 효과가 작다.

`θ`가 클수록 경쟁사의 압력이 강하게 반영된다.

## 7.5 Why `min(..., Ux_i)`?

수요가 아무리 많아도 scooter 1대가 하루에 처리할 수 있는 ride 수에는 한계가 있다.

```text
Q_is(x_i) <= U x_i
```

이는 배터리, 이동시간, 회전율, 고장, 앱 대기시간 등 물리적 운영 한계를 반영한다.

---

## 8. Adjusted Demand Potential

기본 demand는 inferred ride origin count에서 출발한다.

```text
D_is = η R_is
```

하지만 경쟁사 공급은 두 가지 의미를 가진다.

1. Market validation: ALPACA가 많으면 그 zone에 PM 수요가 존재한다는 신호일 수 있다.
2. Competition pressure: ALPACA가 많으면 GBIKE가 그 수요를 모두 가져가기 어렵다.

market validation을 반영하기 위해:

```text
C_tilde_is = log(1 + C_is) / log(1 + C_max)
```

```text
A_is = D_is * (1 + λ C_tilde_is)
```

| Symbol | Meaning |
| --- | --- |
| `λ` | competitor presence를 market validation으로 해석하는 강도 |
| `C_tilde_is` | normalized competitor density |

왜 log인가:

경쟁 PM이 0대에서 10대로 늘어나는 것은 강한 시장 신호지만, 1,000대에서 1,010대로 늘어나는 것은 추가 정보가 작다. 따라서 log를 사용해 체감효과를 표현한다.

---

## 9. Profit Maximization Objective

Scenario probability를 `π_s`라고 하자. GBIKE의 기대 profit은 다음과 같이 쓴다.

```text
maximize
  Σ_s π_s [
    Σ_i (p_i - v) Q_is(x_i)
    - Σ_i c_i x_i
    - Σ_i r_i(x_i)
  ]
```

| Term | Meaning |
| --- | --- |
| `(p_i - v) Q_is(x_i)` | ride revenue minus variable ride cost |
| `c_i x_i` | daily fixed operating cost of deployed PM |
| `r_i(x_i)` | expected rebalancing/collection/charging cost |

## 9.1 Revenue

Sejong inferred rides provide observed distance and speed. A simple revenue model is:

```text
p_i = unlock_fee + per_minute_fee * expected_duration_i
```

where:

```text
expected_duration_i = average inferred ride duration from zone i
```

If duration is unstable, use distance and assumed PM speed:

```text
expected_duration_i = avg_distance_i / avg_speed * 60
```

## 9.2 Fixed Cost

```text
c_i = base daily device cost + zone-specific operating risk
```

Zone-specific cost can increase with:

- low battery rate
- high OD imbalance
- high movement dispersion
- high retrieval distance

## 9.3 Rebalancing Cost

From `sejong_pm_od_flows.csv`, estimate how devices flow from origin zone `i` to destination zone `j`.

Let:

```text
P_ij = probability that a ride starting in i ends in j
d_ij = distance between zone centers
```

Expected rebalancing distance per used device from origin `i`:

```text
L_i = Σ_j P_ij d_ji
```

Expected rebalancing cost:

```text
r_i(x_i) = ρ L_i E[used devices from i]
```

where `ρ` is cost per scooter-km.

---

## 10. Constraints

## 10.1 Fleet Constraint

```text
Σ_i x_i <= F
```

GBIKE가 04:00에 배치할 수 있는 총 기기 수는 제한되어 있다.

## 10.2 Zone Capacity Constraint

```text
0 <= x_i <= K_i
```

`K_i`는 해당 zone의 수용 가능량이다. 다음 방식으로 추정할 수 있다.

```text
K_i = ceil(κ * P95_s(total effective devices in zone i at 04:00))
```

왜 필요한가:

현실적으로 한 500m grid 안에 PM을 무한히 둘 수 없다. 보행공간, 안전, 민원, 주차 가능성, 운영 효율 한계가 있다.

## 10.3 Battery Feasibility

```text
Σ_i x_i <= F_charged
```

또는 zone별로:

```text
x_i <= available effective devices assignable to zone i
```

04:00 배치량은 실제 운행 가능한 배터리 상태를 가진 기기여야 한다.

## 10.4 Minimum Service Coverage

전략적으로 반드시 커버해야 하는 zone 집합을 `M`이라 하면:

```text
x_i >= L_i    for i ∈ M
```

왜 필요한가:

순수 profit maximization은 외곽이나 저수요 지역을 완전히 버릴 수 있다. 하지만 서비스 coverage, 브랜드 노출, 지자체 관계를 고려하면 최소 배치가 필요할 수 있다.

## 10.5 Optional Integer Constraint

실제 PM 대수는 정수이므로:

```text
x_i ∈ Z_+
```

이 제약을 넣으면 문제는 MINLP가 된다. 수업 범위에서 Non-linear Optimization으로 설명하려면 `x_i >= 0` 연속변수로 완화한 뒤, 결과를 반올림하거나 후처리할 수 있다.

---

## 11. Why This Is Non-linear Optimization

이 모델이 non-linear인 이유는 목적함수 안에 다음 함수들이 들어가기 때문이다.

```text
1 - exp(-β x_i / (1 + θ C_is))
min { demand capture, U x_i }
log(1 + C_is)
possibly r_i(x_i)
```

특히:

```text
Q_is(x_i)
```

가 `x_i`에 대해 비선형이다. 따라서 Linear Optimization baseline보다 실제 공유 PM 배치의 포화효과와 경쟁효과를 더 잘 설명한다.

---

## 12. Simulation Design

수업에서 배운 Simulation은 uncertainty를 반영하는 데 사용한다. 우리는 최적화 모델의 입력값을 하나로 고정하지 않고, 여러 scenario를 생성해 robust한 배치를 찾는다.

## 12.1 Random Variables

시뮬레이션에서 흔들 수 있는 값:

| Variable | Meaning |
| --- | --- |
| `D_is` | zone별 demand potential |
| `β` | demand capture sensitivity |
| `θ` | competition pressure |
| `λ` | market validation strength |
| `U` | max rides per device per day |
| `p_i` | revenue per ride |
| `ρ` | rebalancing cost per scooter-km |
| `F` | available charged fleet |

## 12.2 Scenario Generation

Observed snapshots를 이용한 empirical scenario:

```text
s = one observed day/time block from Sejong TAGO data
```

또는 Monte Carlo scenario:

```text
D_is^sim = D_is * ε_is
ε_is ~ LogNormal(-σ²/2, σ²)
```

경쟁사 공급 변동:

```text
C_is^sim = C_is * ξ_is
ξ_is ~ LogNormal(-τ²/2, τ²)
```

비용 변동:

```text
ρ^sim ∈ {low, base, high}
```

## 12.3 Simulation Objective

각 simulated scenario에서 profit을 계산한다.

```text
Profit_s(x)
  = Σ_i (p_i - v) Q_is(x_i)
    - Σ_i c_i x_i
    - Σ_i r_i(x_i)
```

평균 profit 최대화:

```text
maximize E_s[Profit_s(x)]
```

위험회피형 목적함수:

```text
maximize E_s[Profit_s(x)] - γ Std_s[Profit_s(x)]
```

또는 downside risk:

```text
maximize percentile_10_s(Profit_s(x))
```

왜 simulation이 필요한가:

PM 수요는 날씨, 요일, 이벤트, 배터리 상태, 경쟁사 배치에 따라 변동이 크다. 단일 평균값만으로 최적화하면 특정 날짜에는 성능이 나쁠 수 있다. Simulation을 사용하면 profit의 평균뿐 아니라 변동성과 downside risk를 함께 평가할 수 있다.

---

## 13. Competitor Or New Entrant Model

ALPACA 또는 신규 진입자 관점에서는 GBIKE의 배치를 외생 변수로 두고 자기 배치를 최적화한다.

운영사 집합:

```text
k ∈ {GBIKE, ALPACA, NEW}
```

각 운영사의 배치:

```text
x_ik = devices of operator k placed in zone i
```

사용자 선택확률을 넣고 싶다면 utility 기반 discrete choice model을 사용할 수 있다.

```text
V_ik
  = α_access log(1 + x_ik)
  - α_price price_k
  + α_brand brand_ik
  + α_quality quality_ik
```

```text
P_ik = exp(V_ik) / Σ_l exp(V_il)
```

underlying hypothesis:

```text
사용자는 접근성이 높고, 가격이 낮고, 브랜드/품질 선호가 높은 운영사를 선택할 확률이 높다.
```

신규 진입자의 expected captured demand:

```text
Q_is,NEW = D_is * P_i,NEW * [1 - exp(-β_NEW x_i,NEW)]
```

신규 진입 여부:

```text
y_i ∈ {0, 1}
x_i,NEW <= M y_i
```

진입자 objective:

```text
maximize
  expected ride profit
  - deployment cost
  - rebalancing cost
  - market entry fixed cost
```

이 확장은 "GBIKE의 방어적 최적 배치"와 "ALPACA/신규 진입자의 공격적 최적 배치"를 같은 framework에서 비교할 수 있게 한다.

---

## 14. Recommended Report Structure

과제 보고서에서는 다음 흐름이 가장 설득력 있다.

1. Data description

```text
Sejong TAGO PM snapshots provide real operator-level device location, battery, and movement data.
```

2. Linear baseline

```text
We first formulate a linear allocation model where each zone has a constant expected profit per scooter.
```

3. Non-linear main model

```text
We then improve the model by using a concave demand-capture function, because accessibility benefits saturate as more scooters are placed in the same zone.
```

4. Competition

```text
ALPACA supply affects GBIKE demand both as a market validation signal and as competitive pressure.
```

5. Simulation

```text
We evaluate deployment policies under stochastic demand, competition, utilization, and rebalancing cost scenarios.
```

6. Business interpretation

```text
The optimal 04:00 placement balances demand potential, marginal accessibility gain, competitor density, battery feasibility, zone capacity, and rebalancing cost.
```

---

## 15. Summary

이 프로젝트에서 Sejong 데이터 기반 모델의 핵심은 다음 구조이다.

```text
TAGO device snapshots
-> 500m grid zone supply
-> device interval movement
-> inferred ride demand and OD flow
-> Linear Optimization baseline
-> Non-linear profit maximization
-> Simulation-based robustness evaluation
```

가장 중요한 모델링 선택은 다음이다.

```text
Q_is(x_i)
  = min {
      A_is * [1 - exp(-β x_i / (1 + θ C_is))],
      U x_i
    }
```

이 식은 다음을 동시에 반영한다.

- GBIKE가 많을수록 접근성이 높아진다.
- 접근성 효과는 체감한다.
- ALPACA가 많을수록 GBIKE의 capture가 약해진다.
- scooter 1대가 처리할 수 있는 하루 ride 수는 제한된다.

따라서 이 모델은 단순한 시각화나 수요 랭킹이 아니라, 실제 Sejong PM 스냅샷을 기반으로 한 **profit-maximizing non-linear deployment model**이다.
