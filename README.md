# Sejong GBIKE 04:00 Deployment Optimization

이 레포지토리는 세종특별시 TAGO Personal Mobility 데이터를 이용해 **GBIKE/GCOO의 매일 04:00 PM 최적 배치 문제**를 모델링하고 시각화하는 프로젝트입니다.

핵심 질문은 단순합니다.

```text
세종시의 각 500m grid zone에 GBIKE PM을 몇 대 배치해야
하루 기대 profit이 최대가 되는가?
```

이 프로젝트는 특정 solver algorithm보다 **Solver에 넣을 Optimization Model** 자체를 설득력 있게 정의하는 데 초점을 둡니다. 즉, decision variable, objective function, constraints, non-linear demand capture, competition effect, rebalancing cost, simulation-based robustness를 데이터와 연결해 설명합니다.

주요 산출물은 다음입니다.

| Output | 설명 |
| --- | --- |
| `Data_Model_Sheet.md` | 데이터 구조와 optimization model을 설명하는 Model Sheet |
| `outputs/visualizations/optimization_model.html` | 모델 식, 변수, 제약조건, 결과, simulation을 보여주는 HTML dashboard |
| `outputs/visualizations/hermes_lab.html` | 쓰기 가능한 독립 실험실 진입점. 복제된 model/page/code와 optimization model에 필요한 processed subset을 iframe으로 띄우고 우측 Agent sidebar에서 상태 저장과 되돌리기를 제공 |
| `outputs/visualizations/optimization_model_map.html` | zone별 최적 배치량 `x*` 지도 |
| `outputs/visualizations/temporal_inventory_map.html` | Origin-Destination Pair 기반 하루 재고 simulation을 Sejong 500m grid 지도 위에 animation으로 표시 |
| `outputs/visualizations/optimization_model_data.json` | 최적화 결과와 산출물 경로 JSON |
| `outputs/visualizations/temporal_inventory_shortages.csv` | Origin-Destination Pair 기반 하루 재고 simulation에서 PM 부족이 발생한 시간대/zone 기록 |
| `outputs/visualizations/temporal_inventory_hourly_summary.csv` | Origin-Destination Pair 기반 하루 재고 simulation의 시간대별 수요/처리/미처리 summary |
| `outputs/visualizations/temporal_inventory_od_movements.csv` | 시간대별 simulated Origin-Destination Pair demand, served movement, unmet movement, random shock 기록 |
| `outputs/visualizations/parameter_search_results.json` | λ, β, θ multi-start calibration simulation 결과 |
| `outputs/visualizations/optimization_model_constants.json` | `이 최적값 반영하기` 버튼으로 승인한 λ, β, θ 값. 다음 optimization dashboard 재계산 때 실제 상수로 사용 |
| `outputs/visualizations/sejong_map.html` | 최신 PM 위치와 ride/Origin-Destination Pair 기반 지도 |
| `outputs/visualizations/sejong_charts_dashboard.html` | 수집량, 공급자별 현황, battery/activity chart |

---

## Project Status

현재 프로젝트는 서울 PM API가 아닌 **Sejong TAGO PM snapshot**을 중심으로 동작합니다.

서울 TAGO 엔드포인트는 호출 자체는 가능하지만 서울 PM 공급자 행을 안정적으로 노출하지 않았고, 반대로 세종은 `GBIKE`, `ALPACA` 장치 위치 스냅샷을 반복 수집할 수 있었습니다. 따라서 모델과 시각화는 세종 500m grid zone을 기준으로 구성되어 있습니다.

현재 데이터 파이프라인은 다음을 생성합니다.

```text
TAGO API snapshot
-> raw PM CSV/JSON
-> 500m grid zone mapping
-> latest supply by operator and zone
-> device interval movement
-> inferred ride segment
-> Origin-Destination Pair flow
-> optimization model input
-> non-linear deployment dashboard
-> Origin-Destination Pair based temporal inventory simulation
```

---

## Optimization Model

## 1. Decision Variable

Solver가 직접 고르는 값은 각 zone의 GBIKE 배치량입니다.

```math
x_i = \text{04:00에 zone } i \text{에 배치할 GBIKE PM 수}
```

정수 모델에서는:

```math
x_i \in \mathbb{Z}_+
```

수업의 Non-linear Optimization 설명이나 Excel Solver 근사에서는:

```math
x_i \ge 0
```

로 완화할 수 있습니다.

---

## 2. Objective Function

목표는 하루 기대 profit을 최대화하는 것입니다.

```math
\max_x \sum_i \left[(p_i-v)Q_i(x_i)-r_i(x_i)\right]
```

각 항은 다음 의미를 갖습니다.

| Term | 의미 |
| --- | --- |
| `(p_i-v)Q_i(x_i)` | zone `i`에서 발생하는 기대 운행이익 |
| `p_i` | ride 1건당 평균 매출 |
| `v` | ride 1건당 변동비 |
| `Q_i(x_i)` | 배치량 `x_i`에서 실제로 잡을 수 있는 기대 ride 수 |
| `r_i(x_i)` | 이용 후 흩어진 PM을 회수/재배치하는 기대 비용. `log(1+Q_i(x_i))`로 묶음 이동 효과를 반영 |

이 구조는 “ride revenue - variable cost - rebalancing cost”를 직접 모델링합니다. 재배치비는 `r_i(x_i)=ρL_i log(1+Q_i(x_i))`로 계산해 OD flow가 큰 zone에 비용이 선형으로 과적용되지 않게 합니다.

---

## 3. Non-linear Demand Capture

GBIKE가 zone `i`에서 capture하는 기대 ride 수는 다음처럼 둡니다.

```math
Q_i(x_i)
= \min \left\{
A_i\left(1-e^{-\frac{\beta x_i}{1+\theta C_i}}\right),
Ux_i
\right\}
```

이 식을 쓰는 이유는 다음입니다.

- PM을 더 많이 배치하면 사용자가 가까운 기기를 찾을 확률이 올라갑니다.
- 하지만 이미 충분히 많은 PM이 있는 zone에서는 추가 1대의 효과가 작아집니다.
- ALPACA 공급량 `C_i`가 많을수록 같은 GBIKE 배치량의 demand capture 효과가 약해집니다.
- PM 1대가 하루에 처리할 수 있는 ride 수는 `U`로 제한됩니다.

즉, 모델은 “PM을 많이 놓으면 항상 같은 폭으로 수요가 늘어난다”는 linear assumption을 피합니다.

---

## 4. Adjusted Demand Potential

기본 수요는 완전 관측 운영일 기준 inferred ride origin 일평균 count에서 출발합니다.

```math
D_i = \text{average daily inferred rides starting from zone } i
```

경쟁사 ALPACA 공급량은 두 가지 의미를 가집니다.

| 역할 | 의미 |
| --- | --- |
| Market validation | ALPACA가 많은 곳은 PM 시장이 존재한다는 신호일 수 있음 |
| Competition pressure | ALPACA가 많으면 GBIKE가 같은 수요를 잡기 어려움 |

따라서 보정 잠재수요는 다음처럼 둡니다.

```math
A_i
=D_i\left(
1+\lambda
\frac{\log(1+C_i)}{\log(1+C_{\max})}
\right)
```

`log(1+C_i)`는 경쟁사 존재 신호도 체감한다고 보는 가정입니다. 0대에서 10대로 늘어나는 것은 강한 시장 신호지만, 100대에서 110대로 늘어나는 것은 추가 정보가 작기 때문입니다.

---

## 5. Constraints

현재 dashboard run에서 쓰는 핵심 제약조건은 다음입니다.

| Constraint | 식 | 의미 |
| --- | --- | --- |
| Fleet | `Σ_i x_i = F` | 이번 run에서는 2,800대를 반드시 배치 |
| Capacity | `0 <= x_i <= K_i` | 각 500m zone의 물리적/운영적 수용량 |
| Demand capture | `Q_i <= A_i(1-exp(-βx_i/(1+θC_i)))` | 배치량 증가의 체감효과와 경쟁 압력 |
| Device throughput | `Q_i <= Ux_i` | PM 1대가 하루 처리 가능한 최대 ride 수 |
| Non-negativity | `x_i, Q_i, r_i(x_i) >= 0` | 음수 배치와 음수 수요 방지 |

정수 제약 `x_i ∈ Z_+`까지 넣으면 문제는 MINLP가 됩니다. 연속 완화로 설명하면 수업의 Non-linear Optimization 범위에 더 직접적으로 연결됩니다.

---

## 6. Simulation

Optimization은 특정 parameter와 demand assumption 아래에서 `x*`를 찾습니다. 현재 dashboard에는 서로 다른 목적의 simulation이 두 개 들어갑니다.

### 6.1 Objective Robustness Simulation

실제 운영일에는 수요와 비용이 흔들릴 수 있으므로, dashboard는 `x*`를 고정한 뒤 demand/cost shock scenario를 만들어 Objective value 분포를 보여줍니다.

```text
1. 최적 배치 x*를 고정한다.
2. demand shock와 cost shock를 만든다.
3. 각 scenario에서 Objective value를 다시 계산한다.
4. P10, P50, P90, downside risk를 해석한다.
```

### 6.2 Origin-Destination Pair based Temporal Inventory Simulation

정적 최적화는 04:00 배치 `x_i`와 하루 총 기대 ride `Q_i(x_i)`를 직접 연결합니다. 하지만 실제 운영에서는 PM이 ride를 통해 zone 사이를 이동합니다. 따라서 “A에 처음 몰려 있어도 A→B 이동이 많으면 B의 공급이 늘어난다”는 누적 효과를 사후 검증하기 위해 시간대별 재고 simulation을 추가했습니다.

이 simulation은 solver를 다시 푸는 단계가 아니라, 이미 구한 최적 배치 `P* = {x_i*}`가 하루 Origin-Destination Pair 흐름 속에서도 공급 부족을 만들지 않는지 확인하는 lightweight extension입니다.

```text
1. clean inferred ride를 04:00 기준 operating day, 1시간대, Origin-Destination Pair로 집계한다.
2. log(1 + count) = hour effect + Origin-Destination Pair effect 회귀를 추정한다.
3. 관측 평균 빈도와 회귀 예측값을 섞어 시간대별 Origin-Destination Pair base rate λ_hat을 만든다.
4. 하루 Origin-Destination Pair rate는 관측 operating day의 평균 scale을 그대로 사용하며, optimization section의 기대 ride `sum_i Q_i(x_i*)`에 맞춰 확대하지 않는다.
5. 시간 공통 shock와 origin별 shock를 섞어 correlated normal random value Z를 만든다.
6. Excel NORM.INV와 같은 원리로 얻은 normal shock를 rate에 곱해 random Origin-Destination Pair demand를 생성한다.
7. GCOO 초기 재고는 P*=x*, ALPACA 초기 재고는 latest snapshot 공급량으로 둔다.
8. 04:00부터 다음 03:00까지 1시간씩 origin 재고를 차감하고 destination 재고를 증가시킨다.
9. demand가 있었지만 origin 재고가 부족한 경우 shortage log에 시간대, zone, 수요, 처리량, 부족량을 기록한다.
```

따라서 `Simulated combined demand`는 특정 random day에서 실현된 관측-scale 수요이고, `Model Q(x*) rides`는 optimization model이 예측한 하루 기대 ride 총량입니다. 두 값은 서로 다른 기준입니다. temporal simulation은 검증용이므로 관측 OD flow를 `sum_i Q_i(x_i*)`에 맞춰 강제로 스케일하지 않습니다.

재고 전이는 다음처럼 읽을 수 있습니다.

```math
S_{i,t+1}=S_{i,t}-\text{served departures}_{i,t}+\text{served arrivals}_{i,t}
```

수요가 재고보다 큰 경우:

```math
\text{unmet}_{i,t}=\max(0,\text{demand}_{i,t}-S_{i,t})
```

`optimization_model.html`의 맨 아래에는 `temporal_inventory_map.html`을 iframe으로 넣어, Sejong 지도 위에서 시간대별 Origin-Destination Pair 이동과 500m grid 재고를 animation으로 보여줍니다. 지도는 peak shortage hour를 첫 화면으로 열고, 붉은 500m grid는 PM 부족 origin, 초록 선과 화살표는 처리된 Origin-Destination Pair movement, 파란 점은 destination inflow를 뜻합니다. 전체 shortage 기록은 `temporal_inventory_shortages.csv`에 저장되고, 전체 simulated Origin-Destination Pair movement와 random shock audit trail은 `temporal_inventory_od_movements.csv`에 저장됩니다.

두 simulation 모두 solver를 대체하는 algorithm이 아니라, 선택된 배치안의 robustness와 운영상 약점을 검증하는 단계입니다.

### 6.3 λ, β, θ Calibration Simulation

`λ`, `β`, `θ`는 dashboard baseline parameter이지만, 값이 arbitrary해 보이면 모델 설득력이 떨어집니다. 그래서 `optimization_model.html` 하단에 **상수 Calibration Simulation** section을 추가했습니다.

이 section은 버튼을 눌렀을 때 로컬 visualization server의 `POST /api/parameter-search` endpoint를 호출합니다. 서버는 `λ`, `β`, `θ` grid 조합을 만들고, 각 조합마다 100개의 demand trial을 실행합니다. 현재 기본 grid는 `λ` 0.05 단위, `β` 0.005 단위, `θ` 0.05 단위이며, 총 28,675개 parameter 조합과 2,867,500개 case가 만들어집니다.

Full-run은 허용됩니다. 다만 Python의 `threading`은 CPU-bound 계산에서 GIL 때문에 core를 충분히 쓰기 어렵기 때문에, 구현은 `ProcessPoolExecutor` 기반 worker pool을 사용합니다. 기본 worker 수는 `os.cpu_count() - 2`이며, 현재 12 CPU 환경에서는 cron scheduler용 2개 core를 남기고 10 worker가 parameter 조합을 나눠 계산합니다. Worker process는 OS nice `+10`으로 낮은 priority에서 실행해, CPU를 많이 쓰더라도 server, browser, editor 응답성이 먼저 확보되도록 합니다. 단일 worker 기준 100 case / 4초 benchmark라면 단순 추정 31.9시간이고, 10 worker 기준 이론 추정은 약 3.2시간입니다.

OOM 방지를 위해 demand scenario는 worker 초기화 시 compact representation으로 한 번만 전달하고, calibration 중에는 case-level movement log를 저장하지 않습니다. 메모리에 유지하는 것은 demand scenario set, worker별 compact copy, 그리고 parameter 조합별 summary 결과입니다.

```text
1. λ, β, θ grid candidate를 하나 선택한다.
2. 해당 parameter로 A_i와 Q_i(x_i)를 다시 계산한다.
3. fleet F=2,800 제약 아래 x* 배치를 다시 구한다.
4. 모든 candidate에 동일한 100개 synthetic Origin-Destination Pair demand day set을 적용한다.
5. 각 demand trial마다 하루 재고 simulation을 돌려 unmet rides를 계산한다.
6. 100개 trial 평균 unmet rides가 가장 작은 candidate를 best parameter로 선택한다.
```

낮은 parameter가 demand 자체를 줄여 이기는 문제를 피하기 위해, 모든 parameter 조합은 같은 100개 demand scenario set으로 scoring합니다. 즉, parameter는 “수요를 작게 만드는 능력”이 아니라 “같은 수요를 더 잘 처리하는 배치를 만드는 능력”으로 비교됩니다.

Frontend에서는 `Full-run 시뮬레이션 시작하기` 버튼을 누르면 loading indicator가 돌고 버튼이 disabled 됩니다. 동시에 browser는 `GET /api/parameter-search-progress`를 1초 간격으로 polling해서 실제 완료된 parameter 조합 수와 case 수를 읽습니다. ETA는 초기 benchmark 고정값이 아니라 `completed_cases / elapsed_seconds`로 계산한 실제 처리속도 기준으로 갱신됩니다.

Backend도 lock을 사용하므로 이미 실행 중인 request가 있으면 추가 request는 `409`로 거절됩니다. 완료되면 결과는 화면에 표시되고, `outputs/visualizations/parameter_search_results.json`에도 저장됩니다. 결과 JSON에는 `elapsed_seconds`와 `actual_cases_per_second`도 함께 남겨 실제 full-run 소요시간을 audit할 수 있습니다.

Full-run 완료 후 화면의 `이 최적값 반영하기` 버튼을 누르면 best `λ/β/θ`가 `outputs/visualizations/optimization_model_constants.json`에 승인값으로 저장됩니다. 화면에는 `(5분 내로 재계산 시 반영됩니다)` 문구를 함께 표시합니다. 이후 collector 또는 visualization 재계산이 실행되면 renderer는 이 승인파일을 읽어 `build_zone_model(lambda_market=...)`과 `optimize_dashboard_solution(beta_capture=..., theta_competition=...)`에 실제 상수로 사용합니다.

---

## Current Baseline Parameters

현재 optimization visualization에서 사용하는 static parameter는 다음과 같습니다.

| Parameter | 값 | 의미 |
| --- | ---: | --- |
| `F` | 2,800 | 이번 run에서 배치할 전체 GBIKE PM 수 |
| `λ` | 0.30 | 경쟁사 존재를 market validation으로 반영하는 강도 |
| `β` | 0.08 | GBIKE 배치량이 demand capture로 전환되는 속도 |
| `θ` | 1.00 | ALPACA 공급량이 GBIKE capture를 약화시키는 정도 |
| `U` | 6.0 rides/device/day | PM 1대가 하루 처리할 수 있는 최대 ride 수 |
| `p_i` | 2,200 KRW | ride 1건 평균 매출 |
| `v` | 300 KRW | ride 1건당 변동비 |
| `ρ` | 900 KRW/km | 재배치 거리 1km당 비용 |
| `κ` | 1.25 | zone capacity `K_i` 계산 multiplier |

이 값들은 baseline assumption입니다. 보고서에서는 sensitivity analysis 대상으로 둘 수 있습니다.

---

## Data Pipeline

## Raw Inputs

TAGO Personal Mobility API에서 세종 PM provider와 PM list를 수집합니다.

```text
GetPMProvider
GetPMListByProvider(providerName, cityCode)
```

필요한 환경 변수:

```bash
OPEN_DATA_PORTAL_API_KEY="..."
```

기존 alias도 지원합니다.

```bash
DATA_GO_KR_SERVICE_KEY="..."
```

원시 데이터는 다음 위치에 저장됩니다.

```text
data/raw/api/tago_pm/
data/raw/tago_pm_snapshots_sejong_*.csv
data/raw/snapshot_manifest.jsonl
```

---

## Processed Outputs

수집 후 rolling 전처리 산출물이 다시 생성됩니다.

```text
data/processed/sejong_tago/sejong_pm_snapshots_accumulated.csv
data/processed/sejong_tago/sejong_pm_latest_snapshot.csv
data/processed/sejong_tago/sejong_pm_operator_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_zone_snapshot_counts.csv
data/processed/sejong_tago/sejong_pm_device_intervals.csv
data/processed/sejong_tago/sejong_pm_activity_by_zone.csv
data/processed/sejong_tago/sejong_pm_inferred_rides.csv
data/processed/sejong_tago/sejong_pm_operator_move_candidates.csv
data/processed/sejong_tago/sejong_pm_od_flows.csv
data/processed/sejong_tago/sejong_pm_preprocess_summary.json
data/processed/sejong_tago/collector_runs.jsonl
```

각 테이블의 역할은 다음입니다.

| Table | 역할 |
| --- | --- |
| `sejong_pm_latest_snapshot.csv` | 최신 PM 위치와 operator supply |
| `sejong_pm_zone_snapshot_counts.csv` | 시점별 zone/operator 공급량 |
| `sejong_pm_device_intervals.csv` | 같은 device의 연속 snapshot 간 이동 |
| `sejong_pm_inferred_rides.csv` | 4-6분, 100m 이상 이동 interval로 추정한 ride segment와 운영자 이동 의심 flag |
| `sejong_pm_operator_move_candidates.csv` | 속도/반복/군집/배터리 rule로 제외된 운영자 이동 의심 segment |
| `sejong_pm_od_flows.csv` | 운영자 이동 의심 segment를 제외한 clean inferred ride의 origin-destination flow |

---

## Visualization Outputs

전처리 후 다음 HTML/JSON이 생성됩니다.

```text
outputs/visualizations/index.html
outputs/visualizations/optimization_model.html
outputs/visualizations/hermes_lab.html
outputs/visualizations/optimization_model_map.html
outputs/visualizations/temporal_inventory_map.html
outputs/visualizations/optimization_model_data.json
outputs/visualizations/temporal_inventory_shortages.csv
outputs/visualizations/temporal_inventory_hourly_summary.csv
outputs/visualizations/temporal_inventory_od_movements.csv
outputs/visualizations/sejong_map.html
outputs/visualizations/sejong_charts_dashboard.html
outputs/visualizations/sejong_visualization_manifest.json
```

`optimization_model.html`에는 다음이 포함됩니다.

- Solver에 넣는 model 식
- decision variables와 constraints
- static parameters와 data-derived parameters
- 운영자 이동 의심 segment 제외 기준과 제외 건수
- 최종 배치 결과 `x*`
- zone별 배치 지도
- non-linear demand capture 해설
- demand/cost shock simulation 해설
- Sejong 500m grid 지도 기반 Origin-Destination Pair 하루 재고 simulation animation
- 수요가 있었지만 PM이 부족했던 시간대/zone shortage 기록
- 시간대별 simulated Origin-Destination Pair movement와 random shock audit log

---

## Technical Stack

이 프로젝트는 Python 기반 데이터 파이프라인과 static HTML visualization으로 구성됩니다.

| Layer | Stack | 역할 |
| --- | --- | --- |
| Data collection | `requests`, TAGO API | PM provider/device snapshot 수집 |
| Data processing | `pandas`, `numpy` | snapshot 정규화, grid mapping, interval/Origin-Destination Pair 계산 |
| Config | `PyYAML`, `.env` | API key와 model parameter 관리 |
| Optimization prototype | Python functions | non-linear demand/profit 계산과 배치 결과 생성 |
| Charts | `pyecharts` | 시간 추세, operator 현황 chart |
| Maps | `folium`, `branca`, Leaflet | PM 위치, Origin-Destination Pair flow, optimization result 지도 |
| Static serving | `python -m http.server` | `outputs/visualizations` 로컬 서빙 |
| Public tunnel | `cloudflared` optional | 외부 공유용 tunnel |
| Scheduling | `cron` | 5분 주기 수집/전처리/시각화 refresh |

React/Vite 같은 frontend build system은 사용하지 않습니다. 산출물은 정적 HTML이므로 서버 부하가 작고, cron이 파일을 재생성하면 브라우저에서 새로고침해 최신 결과를 볼 수 있습니다.

---

## Architecture

```text
config/model_config.yaml
        |
        v
src/collect_sejong_tago.py
        |
        +-- TAGO API fetch
        +-- raw snapshot write
        +-- 500m grid zone mapping
        +-- processed CSV generation
        +-- inferred ride / Origin-Destination Pair flow generation
        |
        +--> src/visualize_sejong_tago.py
        |       +-- sejong_map.html
        |       +-- sejong_charts_dashboard.html
        |
        +--> src/visualize_optimization_model.py
                +-- build zone model
                +-- compute non-linear demand capture
                +-- compute deployment result x*
                +-- simulate hourly Origin-Destination Pair inventory movement from P*
                +-- optimization_model.html
                +-- optimization_model_map.html
                +-- temporal_inventory_map.html
                +-- optimization_model_data.json
                +-- temporal_inventory_shortages.csv
                +-- temporal_inventory_od_movements.csv
```

현재 `collect_sejong_tago.py`가 cron entry point입니다. 이 파일이 한 번 실행될 때마다 일반 Sejong visualization과 optimization visualization이 모두 갱신됩니다.

---

## Solver/Internal Implementation Notes

현재 dashboard는 full-blown external MINLP solver를 직접 호출하지 않습니다. 대신 모델 구조를 명확히 보여주기 위한 lightweight optimization routine을 Python으로 구현합니다.

구현 위치:

```text
src/visualize_optimization_model.py
```

주요 함수:

| Function | 역할 |
| --- | --- |
| `build_zone_model()` | processed CSV에서 zone별 `D_i`, `C_i`, `A_i`, `K_i`, rebalancing proxy 생성 |
| `demand_capture()` | `Q_i(x_i)` 비선형 수요함수 계산 |
| `zone_profit()` | zone별 profit contribution 계산 |
| `optimize_dashboard_solution()` | fleet `F`를 zone별 `x*`로 배치 |
| `fit_temporal_od_rates()` | clean ride segment로 시간대별 Origin-Destination Pair log-frequency 회귀 추정 |
| `build_temporal_inventory_simulation()` | `P*=x*`와 ALPACA latest supply에서 시작하는 시간대별 재고 simulation |
| `temporal_simulation_panel()` | Origin-Destination Pair 이동 animation, 처리율, shortage table을 HTML 하단에 렌더링 |
| `render_html()` | model sheet dashboard HTML 생성 |
| `render_model_map()` | folium 기반 최적 배치 지도 생성 |

현재 `optimize_dashboard_solution()`은 각 zone에 `k`대를 둘 때의 incremental profit을 계산해 fleet 제약 안에서 배치 결과를 만듭니다. 수업 보고서에서는 이 부분을 solver algorithm으로 강조하기보다, 위에서 정의한 non-linear model을 Gurobi/Excel Solver에 넣을 수 있는 형태로 설명하는 것이 중요합니다.

외부 solver로 확장하려면 다음 방식이 가능합니다.

| Approach | 설명 |
| --- | --- |
| Continuous NLP | `x_i >= 0`으로 완화하고 SciPy/Gurobi nonlinear constraint로 풀이 |
| MINLP | `x_i ∈ Z_+`와 비선형 수요식을 함께 두고 MINLP solver 사용 |
| Piecewise Linear Approximation | `Q_i(x_i)`를 구간별 선형화해 MILP로 변환 |
| Scenario Optimization | `Q_is(x_i)`와 `π_s`를 두어 expected profit 또는 downside objective 최적화 |

---

## Setup

Python 3.11+ 환경을 권장합니다.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

`.env`에 API key를 넣습니다.

```bash
OPEN_DATA_PORTAL_API_KEY="..."
```

---

## Run Once

수집 없이 기존 raw data로 processed outputs와 visualizations만 재생성하려면:

```bash
.venv/bin/python src/collect_sejong_tago.py \
  --skip-fetch \
  --processed-dir data/processed/sejong_tago \
  --visualization-dir outputs/visualizations
```

TAGO API에서 새 snapshot을 수집하고 전체 pipeline을 실행하려면:

```bash
.venv/bin/python src/collect_sejong_tago.py \
  --config config/model_config.yaml \
  --env .env \
  --processed-dir data/processed/sejong_tago \
  --visualization-dir outputs/visualizations
```

Optimization visualization만 단독 재생성하려면:

```bash
.venv/bin/python src/visualize_optimization_model.py
```

---

## 5-minute Refresh

5분 주기 수집과 visualization refresh는 다음 스크립트로 설정합니다.

```bash
scripts/setup_sejong_tago_cron.sh --interval-minutes 5
```

이 스크립트가 수행하는 일:

1. `.venv` 생성 및 의존성 설치
2. 초기 Sejong TAGO collection 실행
3. processed CSV 재생성
4. Sejong map/chart HTML 생성
5. optimization model HTML/map/JSON 생성
6. 현재 Unix 사용자 crontab에 5분 주기 job 등록
7. optional static HTTP server와 Cloudflare Tunnel 실행

cron이 호출하는 명령은 다음 구조입니다.

```bash
python src/collect_sejong_tago.py \
  --config config/model_config.yaml \
  --env .env \
  --processed-dir data/processed/sejong_tago \
  --visualization-dir outputs/visualizations
```

`collect_sejong_tago.py` 안에서 일반 visualization과 optimization visualization이 함께 refresh됩니다.

---

## Static Serving

기본 setup script는 `outputs/visualizations`를 local static server로 서빙합니다.

```text
http://127.0.0.1:8080/
http://127.0.0.1:8080/optimization_model.html
http://127.0.0.1:8080/optimization_model_map.html
http://127.0.0.1:8080/sejong_map.html
http://127.0.0.1:8080/sejong_charts_dashboard.html
```

에이전트 채팅과 실험실 저장/되돌리기를 함께 쓰려면 에이전트 브리지를 실행합니다.

```bash
python scripts/hermes_bridge.py --init-lab
```

브리지는 같은 정적 페이지도 함께 제공합니다.

```text
http://127.0.0.1:8787/index.html
http://127.0.0.1:8787/hermes_lab.html
```

채팅 기록은 Hermes의 기존 `~/.hermes/state.db` Session Storage에 `gcoo-web` source로 저장됩니다. 위젯의 `기록` 버튼에서 이전 대화를 복원할 수 있습니다.

Cloudflare Tunnel을 사용하려면:

```bash
CLOUDFLARE_TUNNEL_TOKEN="..." scripts/setup_sejong_tago_cron.sh \
  --interval-minutes 5 \
  --static-port 8080
```

토큰 없이 quick tunnel을 쓸 수도 있습니다.

```bash
scripts/setup_sejong_tago_cron.sh --interval-minutes 5 --static-port 8080
```

정적 페이지 서빙 없이 cron만 등록하려면:

```bash
scripts/setup_sejong_tago_cron.sh --interval-minutes 5 --no-static-serving
```

---

## Monitoring

cron 등록 확인:

```bash
crontab -l | grep gcoo-sejong-tago-cron
```

수집/전처리/시각화 로그 확인:

```bash
tail -f logs/sejong_tago_cron.log
```

최근 collector summary 확인:

```bash
tail -n 1 data/processed/sejong_tago/collector_runs.jsonl
```

정적 페이지 확인:

```bash
curl -I http://127.0.0.1:8080/optimization_model.html
```

Cloudflare quick tunnel URL 확인:

```bash
grep -Eo 'https://[^ ]+\.trycloudflare\.com' logs/sejong_tago_cloudflared.log | tail -1
```

---

## Repository Layout

```text
.
├── Data_Model_Sheet.md
├── Spec.md
├── README.md
├── config/
│   └── model_config.yaml
├── scripts/
│   └── setup_sejong_tago_cron.sh
├── src/
│   ├── collect_sejong_tago.py
│   ├── visualize_sejong_tago.py
│   ├── visualize_optimization_model.py
│   ├── data_input.py
│   ├── common.py
│   └── ...
├── data/
│   ├── raw/
│   └── processed/sejong_tago/
└── outputs/
    └── visualizations/
```

---

## Important Files

| File | 설명 |
| --- | --- |
| `Data_Model_Sheet.md` | 과제 보고서에 들어갈 data/model explanation |
| `src/collect_sejong_tago.py` | 5분 refresh pipeline entry point |
| `src/visualize_optimization_model.py` | non-linear optimization model dashboard generator |
| `src/visualize_sejong_tago.py` | Sejong map/chart visualization generator |
| `scripts/setup_sejong_tago_cron.sh` | cron/static server/tunnel setup |
| `config/model_config.yaml` | model, API, cost, simulation parameter 설정 |

---

## Current Limitations

- TAGO snapshot은 실제 대여 시작/종료 event log가 아니므로, ride demand는 device movement interval에서 추정합니다.
- 현재 dashboard는 외부 commercial MINLP solver를 호출하지 않고, model을 설명하고 결과를 시각화하기 위한 Python routine을 사용합니다.
- Origin-Destination Pair 기반 하루 재고 simulation은 `x*`를 고정한 사후 검증입니다. `x_i`, `S_{i,t}`, `Q_{i,t}`를 동시에 최적화하는 full time-expanded nonlinear optimization은 아직 풀지 않습니다.
- `p_i`, `v`, `ρ`, `U`, `β`, `θ`, `λ`는 baseline assumption이며 실제 GCOO 내부 정산/운영 데이터가 있으면 보정해야 합니다.
- GPS noise, 수거/재배치 이동, 실제 이용 이동이 snapshot interval 안에서 섞일 수 있으므로 inferred ride는 proxy입니다.
- Origin-Destination Pair simulation의 random demand는 snapshot에서 추정한 빈도와 회귀 smoothing을 기반으로 한 synthetic day입니다. 실제 결제/대여 event log가 있으면 시간대별 rate와 shortage 측정이 더 정확해집니다.
- 현재 모델은 500m grid zone 단위이며, 실제 sidewalk-level parking constraints는 반영하지 않습니다.

---

## Suggested Report Framing

보고서에서는 다음 흐름이 가장 자연스럽습니다.

1. Sejong TAGO PM snapshot data description
2. 500m grid zone construction
3. Device interval에서 inferred ride와 Origin-Destination Pair flow 추정
4. Linear Optimization baseline
5. Non-linear demand capture 기반 main model
6. ALPACA competition effect
7. Fleet/capacity/throughput constraints
8. Profit-maximizing objective
9. Simulation으로 demand/cost shock 아래 objective robustness 평가
10. Origin-Destination Pair 기반 시간대별 재고 simulation으로 `P*`가 실제 Origin-Destination Pair 흐름을 충분히 버티는지 검증
11. 최종 배치 `x*`의 business interpretation

프로젝트의 핵심 문장은 다음처럼 정리할 수 있습니다.

```text
This project formulates Sejong GBIKE 04:00 deployment as a profit-maximizing
non-linear optimization problem where demand capture saturates with deployment,
weakens under ALPACA competition, and is evaluated under demand/cost uncertainty
plus Origin-Destination Pair based temporal inventory movement.
```
