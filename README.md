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
| `outputs/visualizations/optimization_model_map.html` | zone별 최적 배치량 `x*` 지도 |
| `outputs/visualizations/optimization_model_data.json` | 최적화 결과와 산출물 경로 JSON |
| `outputs/visualizations/sejong_map.html` | 최신 PM 위치와 ride/OD 기반 지도 |
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
-> OD flow
-> optimization model input
-> non-linear deployment dashboard
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
\max_x \sum_i \left[(p_i-v)Q_i(x_i)-c_i x_i-r_i(x_i)\right]
```

각 항은 다음 의미를 갖습니다.

| Term | 의미 |
| --- | --- |
| `(p_i-v)Q_i(x_i)` | zone `i`에서 발생하는 기대 운행이익 |
| `p_i` | ride 1건당 평균 매출 |
| `v` | ride 1건당 변동비 |
| `Q_i(x_i)` | 배치량 `x_i`에서 실제로 잡을 수 있는 기대 ride 수 |
| `c_i x_i` | PM을 배치해두는 데 드는 일 운영비 |
| `r_i(x_i)` | 이용 후 흩어진 PM을 회수/재배치하는 기대 비용 |

이 구조는 “ride revenue - operating cost - rebalancing cost”를 직접 모델링합니다.

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

기본 수요는 inferred ride origin count에서 출발합니다.

```math
D_i = \text{inferred rides starting from zone } i
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
| Fleet | `Σ_i x_i = F` | 이번 run에서는 500대를 반드시 배치 |
| Capacity | `0 <= x_i <= K_i` | 각 500m zone의 물리적/운영적 수용량 |
| Demand capture | `Q_i <= A_i(1-exp(-βx_i/(1+θC_i)))` | 배치량 증가의 체감효과와 경쟁 압력 |
| Device throughput | `Q_i <= Ux_i` | PM 1대가 하루 처리 가능한 최대 ride 수 |
| Non-negativity | `x_i, Q_i, r_i(x_i) >= 0` | 음수 배치와 음수 수요 방지 |

정수 제약 `x_i ∈ Z_+`까지 넣으면 문제는 MINLP가 됩니다. 연속 완화로 설명하면 수업의 Non-linear Optimization 범위에 더 직접적으로 연결됩니다.

---

## 6. Simulation

Optimization은 특정 parameter와 demand assumption 아래에서 `x*`를 찾습니다. 실제 운영일에는 수요와 비용이 흔들릴 수 있으므로, dashboard는 `x*`를 고정한 뒤 demand/cost shock scenario를 만들어 Objective value 분포를 보여줍니다.

```text
1. 최적 배치 x*를 고정한다.
2. demand shock와 cost shock를 만든다.
3. 각 scenario에서 Objective value를 다시 계산한다.
4. P10, P50, P90, downside risk를 해석한다.
```

Simulation은 solver를 대체하는 algorithm이 아니라, 선택된 배치안의 robustness를 검증하는 단계입니다.

---

## Current Baseline Parameters

현재 optimization visualization에서 사용하는 static parameter는 다음과 같습니다.

| Parameter | 값 | 의미 |
| --- | ---: | --- |
| `F` | 500 | 이번 run에서 배치할 전체 GBIKE PM 수 |
| `λ` | 0.30 | 경쟁사 존재를 market validation으로 반영하는 강도 |
| `β` | 0.08 | GBIKE 배치량이 demand capture로 전환되는 속도 |
| `θ` | 1.00 | ALPACA 공급량이 GBIKE capture를 약화시키는 정도 |
| `U` | 6.0 rides/device/day | PM 1대가 하루 처리할 수 있는 최대 ride 수 |
| `p_i` | 2,200 KRW | ride 1건 평균 매출 |
| `v` | 300 KRW | ride 1건당 변동비 |
| `c_i` | 2,500 KRW/day | PM 1대당 일 운영비 |
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
| `sejong_pm_inferred_rides.csv` | 4-6분, 100m 이상 이동 interval로 추정한 ride segment |
| `sejong_pm_od_flows.csv` | inferred ride의 origin-destination flow |

---

## Visualization Outputs

전처리 후 다음 HTML/JSON이 생성됩니다.

```text
outputs/visualizations/index.html
outputs/visualizations/optimization_model.html
outputs/visualizations/optimization_model_map.html
outputs/visualizations/optimization_model_data.json
outputs/visualizations/sejong_map.html
outputs/visualizations/sejong_charts_dashboard.html
outputs/visualizations/sejong_visualization_manifest.json
```

`optimization_model.html`에는 다음이 포함됩니다.

- Solver에 넣는 model 식
- decision variables와 constraints
- static parameters와 data-derived parameters
- 최종 배치 결과 `x*`
- zone별 배치 지도
- non-linear demand capture 해설
- demand/cost shock simulation 해설

---

## Technical Stack

이 프로젝트는 Python 기반 데이터 파이프라인과 static HTML visualization으로 구성됩니다.

| Layer | Stack | 역할 |
| --- | --- | --- |
| Data collection | `requests`, TAGO API | PM provider/device snapshot 수집 |
| Data processing | `pandas`, `numpy` | snapshot 정규화, grid mapping, interval/OD 계산 |
| Config | `PyYAML`, `.env` | API key와 model parameter 관리 |
| Optimization prototype | Python functions | non-linear demand/profit 계산과 배치 결과 생성 |
| Charts | `pyecharts` | 시간 추세, operator 현황 chart |
| Maps | `folium`, `branca`, Leaflet | PM 위치, OD flow, optimization result 지도 |
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
        +-- inferred ride / OD flow generation
        |
        +--> src/visualize_sejong_tago.py
        |       +-- sejong_map.html
        |       +-- sejong_charts_dashboard.html
        |
        +--> src/visualize_optimization_model.py
                +-- build zone model
                +-- compute non-linear demand capture
                +-- compute deployment result x*
                +-- optimization_model.html
                +-- optimization_model_map.html
                +-- optimization_model_data.json
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
- `p_i`, `v`, `c_i`, `ρ`, `U`, `β`, `θ`, `λ`는 baseline assumption이며 실제 GCOO 내부 정산/운영 데이터가 있으면 보정해야 합니다.
- GPS noise, 수거/재배치 이동, 실제 이용 이동이 snapshot interval 안에서 섞일 수 있으므로 inferred ride는 proxy입니다.
- 현재 모델은 500m grid zone 단위이며, 실제 sidewalk-level parking constraints는 반영하지 않습니다.

---

## Suggested Report Framing

보고서에서는 다음 흐름이 가장 자연스럽습니다.

1. Sejong TAGO PM snapshot data description
2. 500m grid zone construction
3. Device interval에서 inferred ride와 OD flow 추정
4. Linear Optimization baseline
5. Non-linear demand capture 기반 main model
6. ALPACA competition effect
7. Fleet/capacity/throughput constraints
8. Profit-maximizing objective
9. Simulation으로 demand/cost shock 아래 robustness 평가
10. 최종 배치 `x*`의 business interpretation

프로젝트의 핵심 문장은 다음처럼 정리할 수 있습니다.

```text
This project formulates Sejong GBIKE 04:00 deployment as a profit-maximizing
non-linear optimization problem where demand capture saturates with deployment,
weakens under ALPACA competition, and is evaluated under demand/cost uncertainty.
```
