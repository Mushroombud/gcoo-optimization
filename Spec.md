# Spec.md

# Profit-Maximizing E-Scooter Placement for GCOO in Seoul

## 0. Purpose

This specification defines the implementation plan for a scenario-based nonlinear optimization model that determines how many GCOO e-scooters should be placed in each Seoul administrative dong at 04:00 each operating day.

The model maximizes expected daily operating profit by combining:

1. Seoul Bike rental and operation data as a proxy for short-distance mobility demand.
2. TAGO shared PM data as a source of GCOO and competitor PM availability.
3. A nonlinear demand-capture function that reflects demand saturation and competitor pressure.
4. Simulation and sensitivity analysis to test robustness under uncertain demand, utilization, and cost assumptions.

The model is intended to be implemented in Python and validated through baseline comparisons.

---

## 1. Core Modeling Decision

### 1.1 Final Model Type

Use a **scenario-based nonlinear resource allocation model**.

The model is not a street-level dispatch model and does not optimize the exact sidewalk or parking point where each scooter should be placed. It optimizes the number of GCOO scooters to place in each Seoul administrative dong.

### 1.2 Operating Assumption

All GCOO scooters are assumed to be reset at **04:00** every day.

- Operating day: `04:00` to next day `03:59`.
- Decision time: `04:00`.
- Decision unit: administrative dong.
- Decision variable: number of GCOO scooters placed in each dong.
- The model does not consider intra-day relocation.
- The model does not consider exact street-level placement within a dong.

This assumption turns the problem into a daily placement problem that is feasible to implement while still capturing the core business decision.

---

## 2. Spatial Unit

### 2.1 Zone Definition

Use all Seoul administrative dongs as zones.

Let:

```text
I = set of Seoul administrative dongs
|I| ≈ 427
```

Each zone `i` represents one administrative dong.

### 2.2 Why Administrative Dongs

1. They cover all of Seoul and avoid selection bias.
2. They are compatible with public administrative boundary data.
3. Seoul Bike stations and TAGO PM coordinates can be spatially joined to them.
4. The number of variables is manageable for code-based optimization.
5. They provide a realistic operational unit without requiring street-level placement data.

---

## 3. Data Inputs

## 3.1 Required Raw Data

### A. Seoul Administrative Dong Boundary Data

Purpose:

- Defines the spatial unit of optimization.
- Used to map Seoul Bike stations and PM device coordinates to administrative dongs.

Required fields:

| Field       | Description                   |
| ----------- | ----------------------------- |
| `dong_id`   | Unique administrative dong ID |
| `dong_name` | Administrative dong name      |
| `gu_name`   | District name                 |
| `geometry`  | Polygon geometry              |

Expected file format:

```text
data/raw/seoul_admin_dong.geojson
```

---

### B. Seoul Bike Station Data

Purpose:

- Provides station coordinates.
- Used to map rental and return stations to administrative dongs.

Required fields:

| Field          | Description           |
| -------------- | --------------------- |
| `station_id`   | Seoul Bike station ID |
| `station_name` | Station name          |
| `latitude`     | Station latitude      |
| `longitude`    | Station longitude     |

Expected file format:

```text
data/raw/seoul_bike_stations.csv
```

---

### C. Seoul Bike Rental History Data

Purpose:

- Used to construct PM-like short-distance mobility demand.

Required fields:

| Field               | Description              |
| ------------------- | ------------------------ |
| `rental_datetime`   | Rental timestamp         |
| `return_datetime`   | Return timestamp         |
| `rental_station_id` | Origin station ID        |
| `return_station_id` | Destination station ID   |
| `distance_m`        | Trip distance in meters  |
| `duration_min`      | Trip duration in minutes |

Expected file format:

```text
data/raw/seoul_bike_trips_*.csv
```

---

### D. TAGO Shared PM Snapshot Data

Purpose:

- Measures GCOO’s current placement.
- Measures competitor PM density.
- Defines realistic zone-level capacity.

Required fields:

| Field           | Description                 |
| --------------- | --------------------------- |
| `timestamp`     | Snapshot timestamp          |
| `operator_name` | PM operator name            |
| `device_id`     | Unique PM device ID         |
| `battery_level` | Battery level, if available |
| `latitude`      | Device latitude             |
| `longitude`     | Device longitude            |

Expected file format:

```text
data/raw/tago_pm_snapshots_*.csv
```

Recommended collection method:

- Collect snapshots near 04:00.
- Acceptable window: 03:30-04:30.
- Collect at least 7 days if possible.
- If only one snapshot is available, treat simulation and sensitivity results as more important.

---

### E. GCOO Pricing and Cost Assumptions

Purpose:

- Converts expected rides into operating profit.

Expected file format:

```text
config/model_config.yaml
```

Required parameters:

| Parameter                         | Description                                |
| --------------------------------- | ------------------------------------------ |
| `unlock_fee`                      | Base unlock fee per ride                   |
| `per_minute_fee`                  | Per-minute ride fee                        |
| `avg_scooter_speed_kmph`          | Assumed average e-scooter speed            |
| `variable_cost_per_ride`          | Cost incurred per ride                     |
| `base_fixed_cost_per_scooter_day` | Daily fixed operating cost per scooter     |
| `u0_avg_rides_per_scooter_day`    | Baseline average rides per scooter per day |
| `u_max_rides_per_scooter_day`     | Maximum feasible rides per scooter per day |

---

## 4. Processed Data Tables

The implementation should generate the following processed tables.

### 4.1 `dong_master.csv`

One row per administrative dong.

| Column      | Description              |
| ----------- | ------------------------ |
| `dong_id`   | Administrative dong ID   |
| `dong_name` | Administrative dong name |
| `gu_name`   | District name            |
| `area_km2`  | Area of dong, optional   |

---

### 4.2 `bike_trip_pm_like.csv`

Filtered Seoul Bike trips that resemble PM-like short-distance transportation demand.

| Column                | Description                          |
| --------------------- | ------------------------------------ |
| `operating_day`       | Operating day, based on 04:00 cutoff |
| `origin_dong_id`      | Origin administrative dong           |
| `destination_dong_id` | Destination administrative dong      |
| `distance_km`         | Trip distance in km                  |
| `duration_min`        | Trip duration in minutes             |
| `speed_kmph`          | Average speed                        |

---

### 4.3 `demand_scenario.csv`

Scenario-day and dong-level demand table.

| Column              | Description                                                 |
| ------------------- | ----------------------------------------------------------- |
| `scenario_day`      | Historical operating day used as scenario                   |
| `dong_id`           | Administrative dong ID                                      |
| `H_is`              | PM-like Seoul Bike trips starting from dong i on scenario s |
| `arrivals_is`       | PM-like trips arriving at dong i on scenario s              |
| `departures_is`     | PM-like trips departing from dong i on scenario s           |
| `avg_distance_km_i` | Average PM-like trip distance from dong i                   |

---

### 4.4 `tago_scenario.csv`

Scenario-day and dong-level PM supply table.

| Column                | Description                                         |
| --------------------- | --------------------------------------------------- |
| `scenario_day`        | Snapshot date used as scenario                      |
| `dong_id`             | Administrative dong ID                              |
| `gcoo_count_is`       | Number of effective GCOO PM devices in dong i       |
| `competitor_count_is` | Number of effective competitor PM devices in dong i |
| `total_pm_count_is`   | GCOO + competitor PM devices in dong i              |

---

### 4.5 `model_inputs.csv`

Final dong-level model inputs.

| Column    | Description                            |
| --------- | -------------------------------------- |
| `dong_id` | Administrative dong ID                 |
| `p_i`     | Expected revenue per ride              |
| `c_i`     | Daily fixed operating cost per scooter |
| `K_i`     | Zone capacity                          |
| `x_obs_i` | Observed GCOO placement                |
| `B_i`     | OD imbalance index                     |

---

---

## 5. Demand Construction

## 5.1 Operating Day Rule

For each timestamp `t`, define:

```python
if t.hour < 4:
    operating_day = date(t) - 1 day
else:
    operating_day = date(t)
```

This ensures that trips between midnight and 03:59 belong to the previous operating day.

---

## 5.2 PM-like Trip Filter

A Seoul Bike trip is classified as PM-like if all conditions below hold:

```text
0.5 km <= distance <= 3.0 km
3 min <= duration <= 20 min
origin_dong_id != destination_dong_id
5 km/h <= average_speed <= 25 km/h
```

The purpose is to retain short-distance transportation trips and remove most recreational, circular, abnormal, or long-distance bicycle trips.

### Filtered demand count

For each dong `i` and scenario day `s`:

```text
H_is = number of PM-like trips departing from dong i on day s
```

This is not yet GCOO demand. It is a spatial proxy for short-distance mobility demand.

---

## 5.3 Demand Scaling

Let:

```text
F = average number of effective GCOO scooters observed near 04:00
u0 = assumed average rides per scooter per day
```

Then the total expected daily GCOO rides should be approximately:

```text
F * u0
```

Define:

```text
alpha = (F * u0) / sum_i(mean_s(H_is))
```

Then:

```text
D_is = alpha * H_is
```

Where:

- `D_is` is the basic GCOO demand proxy for dong `i` on scenario day `s`.
- `alpha` scales Seoul Bike-derived spatial demand into the expected GCOO ride volume.

Baseline value:

```text
u0 = 4 rides/scooter/day
```

Sensitivity values:

```text
u0 in {3, 4, 5, 6}
```

---

## 6. Competitor Data Construction

## 6.1 Effective PM Device Count

A PM device is counted as effective if:

```text
battery_level is missing OR battery_level >= 20%
```

This removes nearly unusable devices if battery data is available.

For each dong `i` and scenario day `s`:

```text
G_is = number of effective GCOO devices
C_is = number of effective competitor devices
T_is = G_is + C_is
```

Competitors include all non-GCOO shared PM operators observed through TAGO.

---

## 6.2 Current GCOO Placement

Observed GCOO placement is:

```text
x_obs_i = mean_s(G_is)
```

This becomes the baseline placement for comparison.

Total scooter supply is:

```text
F = round(sum_i(x_obs_i))
```

If GCOO devices cannot be reliably separated in TAGO data, use scenario values:

```text
F in {300, 500, 700}
```

---

## 6.3 Zone Capacity

Capacity is based on observed PM supply, not arbitrary assumptions.

For each dong `i`:

```text
K_i = ceil(1.25 * P95_s(T_is))
```

Where:

- `T_is` is total effective PM devices observed in dong `i` on day `s`.
- `P95_s` is the 95th percentile across scenario days.
- `1.25` gives a 25% buffer over historically observed PM supply.

Minimum capacity rule:

```text
if K_i == 0 and mean_s(H_is) > 0:
    K_i = 3
```

This allows the model to place a small number of scooters in high-demand dongs where TAGO did not observe PM supply during the collection window.

---

## 7. Demand–Competition Co-Relevance

## 7.1 Normalized Demand and Competition

For diagnostic analysis, compute:

```text
D_tilde_is = D_is / max_i(D_is)
C_tilde_is = log(1 + C_is) / log(1 + C_max)
```

Where:

```text
C_max = max over all i,s of C_is
```

## 7.2 Co-Relevance Score

Define:

```text
R_is = D_tilde_is * C_tilde_is
```

Interpretation:

- High `D_tilde_is`, low `C_tilde_is`: possible under-served demand.
- High `D_tilde_is`, high `C_tilde_is`: validated PM market, but potentially competitive.
- Low `D_tilde_is`, high `C_tilde_is`: possible over-supplied market.
- Low `D_tilde_is`, low `C_tilde_is`: unattractive or unvalidated market.

`R_is` is diagnostic. It is not the objective function by itself.

---

## 8. Competitor-Adjusted Demand Potential

Competitor presence enters the model in two separate ways.

1. Market validation signal: competitor presence suggests that PM demand may actually exist.
2. Competition pressure: excessive competitor presence reduces GCOO’s demand capture.

The market validation effect is modeled as:

```text
A_is = D_is * (1 + lambda * C_tilde_is)
```

Where:

| Symbol       | Meaning                              |
| ------------ | ------------------------------------ |
| `A_is`       | competitor-adjusted demand potential |
| `D_is`       | Seoul Bike-derived GCOO demand proxy |
| `C_tilde_is` | normalized competitor density        |
| `lambda`     | strength of market validation effect |

Baseline:

```text
lambda = 0.3
```

Sensitivity:

```text
lambda in {0.0, 0.2, 0.3, 0.5}
```

---

## 9. Nonlinear Demand Capture Function

## 9.1 Functional Form

Expected GCOO rides in dong `i`, scenario `s`, when `x_i` scooters are placed:

```text
Q_is(x_i) = min(
    A_is * (1 - exp(- beta * x_i / (1 + theta * C_is))),
    U * x_i
)
```

Where:

| Symbol      | Meaning                                   |
| ----------- | ----------------------------------------- |
| `Q_is(x_i)` | expected GCOO rides in dong i, scenario s |
| `A_is`      | competitor-adjusted demand potential      |
| `x_i`       | GCOO scooters placed in dong i            |
| `C_is`      | competitor PM count                       |
| `beta`      | speed of demand capture by GCOO supply    |
| `theta`     | competition pressure parameter            |
| `U`         | maximum daily rides per scooter           |

## 9.2 Interpretation

This function has the required business behavior.

1. If `x_i = 0`, then `Q_is = 0`.
2. Increasing `x_i` increases expected rides.
3. Marginal rides decrease as `x_i` grows.
4. High competitor count `C_is` lowers GCOO's demand capture.
5. Expected rides cannot exceed `U * x_i`.
6. Expected rides cannot exceed local adjusted demand potential `A_is`.

## 9.3 Baseline Parameters

```yaml
lambda: 0.3
beta: calibrated
theta: 1.0
U: 6
```

Sensitivity ranges:

```yaml
lambda: [0.0, 0.2, 0.3, 0.5]
theta: [0.5, 1.0, 1.5, 2.0]
U: [4, 6, 8]
```

---

## 10. Calibration of Beta

`beta` is calibrated so that the model reproduces the assumed average utilization under observed GCOO placement.

Target:

```text
mean_s sum_i Q_is(x_obs_i) = u0 * F
```

Procedure:

1. Compute `x_obs_i` from TAGO GCOO observations.
2. Set `u0 = 4` as baseline.
3. Find beta by numerical root-finding.
4. Use bisection over a reasonable interval, e.g. `[0.0001, 10]`.

Pseudocode:

```python
def calibration_error(beta):
    total = 0
    for s in scenarios:
        for i in dongs:
            total += Q(i, s, x_obs[i], beta)
    avg_total = total / len(scenarios)
    return avg_total - u0 * F

beta = bisection(calibration_error, low=0.0001, high=10.0)
```

If no reliable `x_obs_i` exists, set:

```text
beta = 0.08
```

and include beta in sensitivity analysis.

---

## 11. Revenue Model

## 11.1 Revenue per Ride

For each dong `i`:

```text
p_i = unlock_fee + per_minute_fee * T_i
```

Where:

```text
T_i = avg_distance_km_i / avg_scooter_speed_kmph * 60
```

Baseline:

```yaml
avg_scooter_speed_kmph: 12
```

Sensitivity:

```yaml
avg_scooter_speed_kmph: [10, 12, 15]
```

If `avg_distance_km_i` is missing for a dong:

```text
avg_distance_km_i = citywide average PM-like trip distance
```

---

## 12. Cost Model

## 12.1 Variable Cost per Ride

```text
c_r = variable_cost_per_ride
```

Includes:

- payment fees
- customer support burden
- ride-linked maintenance burden

This is not directly observable, so it is configured as an assumption and tested in sensitivity analysis.

---

## 12.2 Fixed Cost per Scooter per Day

Each scooter incurs a fixed daily operating cost even if it receives no ride.

Base cost:

```text
c_0 = base_fixed_cost_per_scooter_day
```

Administrative-dong-specific imbalance index:

```text
B_i = abs(mean_arrivals_i - mean_departures_i) / (mean_arrivals_i + mean_departures_i + 1)
```

Dong-specific fixed cost:

```text
c_i = c_0 * (1 + mu * B_i)
```

Where:

| Symbol | Meaning                      |
| ------ | ---------------------------- |
| `B_i`  | OD imbalance index           |
| `mu`   | cost increase from imbalance |

Baseline:

```yaml
mu: 0.5
```

Sensitivity:

```yaml
mu: [0.0, 0.5, 1.0]
```

Interpretation:

- If arrivals and departures are balanced, `B_i` is low.
- If a dong is mostly an origin or mostly a destination, it may require more redistribution.
- Higher imbalance increases fixed operating cost.

---

## 13. Objective Function

Maximize expected daily operating profit:

```text
Maximize Pi(x) = (1 / |S|) * sum_s sum_i [ (p_i - c_r) * Q_is(x_i) - c_i * x_i ]
```

Where:

| Symbol      | Meaning                      |
| ----------- | ---------------------------- |
| `S`         | scenario days                |
| `I`         | Seoul administrative dongs   |
| `p_i`       | revenue per ride in dong i   |
| `c_r`       | variable cost per ride       |
| `Q_is(x_i)` | expected rides               |
| `c_i`       | daily fixed cost per scooter |
| `x_i`       | scooters placed              |

---

## 14. Constraints

### 14.1 Total Scooter Supply

```text
sum_i x_i <= F
```

Where:

```text
F = total available GCOO scooters at 04:00
```

### 14.2 Zone Capacity

```text
0 <= x_i <= K_i for all i
```

### 14.3 Integer Placement

```text
x_i is a nonnegative integer
```

### 14.4 No Additional Constraints in Baseline Model

Do not add arbitrary minimum service constraints in the baseline model.

Reason:

- The project’s main objective is profit maximization.
- Minimum coverage requirements would make sense for public service models, but GCOO is a private firm.
- Coverage constraints can be added as an extension if needed.

---

## 15. Optimization Algorithm

## 15.1 Why Greedy Marginal Allocation Works

The model is a separable nonlinear resource allocation problem.

Each dong's profit depends only on its own scooter count `x_i`.
The only coupling constraint is total scooter supply:

```text
sum_i x_i <= F
```

With diminishing returns, the marginal profit of each additional scooter in the same dong should decrease.

Therefore, the model can be solved by ranking marginal profit increments.

---

## 15.2 Dong-Level Profit Function

For each dong `i`, define:

```text
pi_i(k) = (1 / |S|) * sum_s [ (p_i - c_r) * Q_is(k) - c_i * k ]
```

where `k` is an integer number of scooters placed in dong `i`.

Marginal profit from the `k`-th scooter:

```text
Delta_i(k) = pi_i(k) - pi_i(k - 1)
```

---

## 15.3 Greedy Allocation Procedure

Pseudocode:

```python
marginal_items = []

for i in dongs:
    prev_profit = profit_i(i, 0)
    for k in range(1, K_i[i] + 1):
        current_profit = profit_i(i, k)
        delta = current_profit - prev_profit
        marginal_items.append({
            "dong_id": i,
            "k": k,
            "delta_profit": delta
        })
        prev_profit = current_profit

# Keep only positive marginal profit candidates
marginal_items = [m for m in marginal_items if m["delta_profit"] > 0]

# Sort descending by marginal profit
marginal_items.sort(key=lambda m: m["delta_profit"], reverse=True)

# Select at most F scooter placements
x = {i: 0 for i in dongs}
selected = 0

for item in marginal_items:
    if selected >= F:
        break
    i = item["dong_id"]
    if x[i] == item["k"] - 1:
        x[i] += 1
        selected += 1

return x
```

Important implementation note:

- The condition `x[i] == item["k"] - 1` ensures that the `k`-th scooter in a dong is not selected before the first `k-1` scooters.
- Before relying on greedy allocation, verify that marginal profits are non-increasing within each dong.

### 15.4 Concavity Check

For each dong `i`:

```python
deltas = [Delta_i(k) for k in range(1, K_i + 1)]
check = all(deltas[k] <= deltas[k-1] + tolerance for k in range(1, len(deltas)))
```

If more than 5% of dongs fail the concavity check, run a fallback solver.

Fallback options:

1. Use `scipy.optimize.minimize` with continuous variables and round results.
2. Use dynamic programming if `F` is small enough.
3. Use brute-force marginal sorting without relying on concavity, but keep the sequential-selection condition.

For this project, the greedy method is the primary implementation.

---

## 16. Baseline Models

The optimized placement must be compared with at least three baselines.

### 16.1 Baseline A: Observed GCOO Placement

```text
x_obs_i = mean_s(G_is)
```

If `sum_i x_obs_i` differs from `F` due to rounding, adjust proportionally.

---

### 16.2 Baseline B: Demand-Proportional Placement

```text
x_demand_i = round(F * mean_s(D_is) / sum_j mean_s(D_js))
```

Adjust rounding so that:

```text
sum_i x_demand_i = F
```

---

### 16.3 Baseline C: Competitor-Following Placement

```text
x_comp_i = round(F * mean_s(C_is) / sum_j mean_s(C_js))
```

If total competitor count is zero, skip this baseline.

---

### 16.4 Proposed Model

```text
x_star_i = result of nonlinear marginal-profit optimization
```

---

## 17. Evaluation Metrics

For each placement plan `x`, compute:

| Metric                       | Description                                                 |
| ---------------------------- | ----------------------------------------------------------- |
| `expected_profit`            | Mean profit across scenario days                            |
| `expected_rides`             | Mean expected rides across scenario days                    |
| `avg_utilization`            | Expected rides / total scooters                             |
| `profit_per_scooter`         | Expected profit / total scooters                            |
| `p5_profit`                  | 5th percentile profit from simulation                       |
| `prob_profit_below_observed` | Probability proposed model underperforms observed placement |
| `top_dongs`                  | Top dongs by allocated scooter count                        |

---

## 18. Simulation

## 18.1 Purpose

Simulation tests whether the optimized placement is robust under uncertain demand, competition, utilization, and cost assumptions.

---

## 18.2 Simulation Procedure

Run 1,000 simulation iterations.

For each iteration `m`:

1. Sample a historical scenario day `s` with replacement.
2. Sample parameters from configured ranges:
   - `u0`
   - `lambda`
   - `theta`
   - `U`
   - `variable_cost_per_ride`
   - `base_fixed_cost_per_scooter_day`
   - `mu`
3. Recompute `D_is`, `A_is`, `Q_is`, and costs.
4. Evaluate profit for each placement plan:
   - observed placement
   - demand-proportional placement
   - competitor-following placement
   - optimized placement
5. Store results.

The optimized placement does not need to be re-optimized in each simulation iteration for the main robustness test. It is evaluated under many possible worlds.

Optional extension:

- Re-optimize in each simulated world to estimate the value of perfect hindsight information.

---

## 18.3 Simulation Output

Generate:

```text
outputs/simulation_results.csv
outputs/simulation_summary.csv
```

Required summary columns:

| Column                       | Description                                    |
| ---------------------------- | ---------------------------------------------- |
| `plan_name`                  | Placement plan name                            |
| `mean_profit`                | Average simulated profit                       |
| `median_profit`              | Median simulated profit                        |
| `p05_profit`                 | 5th percentile profit                          |
| `p95_profit`                 | 95th percentile profit                         |
| `mean_rides`                 | Average expected rides                         |
| `mean_utilization`           | Average rides per scooter                      |
| `prob_negative_profit`       | Probability profit < 0                         |
| `prob_underperform_observed` | Probability profit < observed placement profit |

---

## 19. Sensitivity Analysis

Run one-at-a-time sensitivity analysis on the following parameters.

| Parameter                         | Baseline |   Sensitivity Values |
| --------------------------------- | -------: | -------------------: |
| `u0_avg_rides_per_scooter_day`    |        4 |           3, 4, 5, 6 |
| `lambda`                          |      0.3 |   0.0, 0.2, 0.3, 0.5 |
| `theta`                           |      1.0 |   0.5, 1.0, 1.5, 2.0 |
| `U`                               |        6 |              4, 6, 8 |
| `avg_scooter_speed_kmph`          |       12 |           10, 12, 15 |
| `variable_cost_per_ride`          |   config | -20%, baseline, +20% |
| `base_fixed_cost_per_scooter_day` |   config | -20%, baseline, +20% |
| `mu`                              |      0.5 |        0.0, 0.5, 1.0 |
| `F`                               | observed | -20%, observed, +20% |

For each run, output:

```text
parameter_name
parameter_value
expected_profit
expected_rides
profit_per_scooter
number_of_active_dongs
allocation_change_from_baseline
```

`allocation_change_from_baseline` can be measured as:

```text
0.5 * sum_i(abs(x_i_new - x_i_baseline)) / F
```

This ranges from 0 to 1 and measures how much the placement plan changes.

---

## 20. Value of Information Extension

This is optional but recommended if time permits.

### 20.1 Question

Would additional TAGO collection or a small pilot test be worth conducting before finalizing placement?

### 20.2 Operationalization

Compare:

1. Placement optimized using limited data.
2. Placement optimized using additional scenario days or pilot-informed parameters.

Define:

```text
EVSI = Expected profit with additional information - Expected profit without additional information
```

If:

```text
EVSI > data_collection_cost
```

then additional data collection is economically justified.

### 20.3 Implementation Option

Use a simple split-sample approach.

1. Use first half of scenario days as limited information.
2. Use all scenario days as improved information.
3. Optimize placement under each.
4. Evaluate both placements on a held-out test set.
5. Difference in test profit approximates the value of additional information.

---

## 21. Suggested Project Directory

```text
project/
  Spec.md
  README.md
  config/
    model_config.yaml
  data/
    raw/
      seoul_admin_dong.geojson
      seoul_bike_stations.csv
      seoul_bike_trips_YYYYMM.csv
      tago_pm_snapshots_YYYYMMDD.csv
    processed/
      dong_master.csv
      bike_trip_pm_like.csv
      demand_scenario.csv
      tago_scenario.csv
      model_inputs.csv
  src/
    01_prepare_zones.py
    02_prepare_bike_demand.py
    03_prepare_tago_supply.py
    04_build_model_inputs.py
    05_calibrate_model.py
    06_optimize_placement.py
    07_evaluate_baselines.py
    08_run_simulation.py
    09_sensitivity_analysis.py
    utils_geo.py
    utils_model.py
  outputs/
    allocation_optimized.csv
    allocation_baselines.csv
    zone_metrics.csv
    baseline_comparison.csv
    simulation_results.csv
    simulation_summary.csv
    sensitivity_results.csv
    figures/
```

---

## 22. Suggested `model_config.yaml`

```yaml
spatial:
  battery_threshold: 20
  capacity_multiplier: 1.25
  min_capacity_if_demand_positive: 3

demand:
  min_distance_km: 0.5
  max_distance_km: 3.0
  min_duration_min: 3
  max_duration_min: 20
  min_speed_kmph: 5
  max_speed_kmph: 25
  u0_avg_rides_per_scooter_day: 4

revenue:
  unlock_fee: 600
  per_minute_fee: 180
  avg_scooter_speed_kmph: 12

cost:
  variable_cost_per_ride: 300
  base_fixed_cost_per_scooter_day: 2500
  mu_imbalance_cost: 0.5

nonlinear_model:
  lambda_market_validation: 0.3
  theta_competition_pressure: 1.0
  U_max_rides_per_scooter_day: 6
  beta_default_if_uncalibrated: 0.08

simulation:
  n_iterations: 1000
  random_seed: 42

sensitivity:
  u0_values: [3, 4, 5, 6]
  lambda_values: [0.0, 0.2, 0.3, 0.5]
  theta_values: [0.5, 1.0, 1.5, 2.0]
  U_values: [4, 6, 8]
  speed_values: [10, 12, 15]
  mu_values: [0.0, 0.5, 1.0]
```

All monetary values are placeholders and must be reviewed before final analysis.

---

## 23. Implementation Functions

### 23.1 Geographic Functions

```python
load_admin_dongs(path) -> GeoDataFrame
load_bike_stations(path) -> DataFrame
assign_points_to_dongs(points_df, dongs_gdf, lat_col, lon_col) -> DataFrame
```

### 23.2 Demand Functions

```python
make_operating_day(timestamp) -> date
filter_pm_like_trips(trips_df, config) -> DataFrame
aggregate_demand_scenarios(pm_like_trips_df) -> DataFrame
compute_demand_scale_alpha(demand_df, F, u0) -> float
```

### 23.3 TAGO Functions

```python
filter_effective_pm_devices(tago_df, battery_threshold) -> DataFrame
aggregate_tago_scenarios(tago_df) -> DataFrame
compute_observed_gcoo_placement(tago_scenario_df) -> Series
compute_zone_capacity(tago_scenario_df, multiplier, min_capacity_rule) -> Series
```

### 23.4 Model Functions

```python
compute_competitor_adjusted_demand(D, C, lambda_) -> A
compute_Q(A, x, C, beta, theta, U) -> Q
compute_zone_profit(i, k, scenarios, params) -> float
calibrate_beta(x_obs, target_rides, params) -> float
optimize_by_marginal_profit(params) -> allocation
```

### 23.5 Evaluation Functions

```python
evaluate_placement(allocation, params) -> dict
make_demand_proportional_baseline(D, F) -> allocation
make_competitor_following_baseline(C, F) -> allocation
run_simulation(allocations, params, n_iterations) -> DataFrame
run_sensitivity(base_params, param_grid) -> DataFrame
```

---

## 24. Validation Checks

Run these checks before accepting results.

### Data Checks

```text
All Seoul Bike stations mapped to a dong or explicitly dropped.
All TAGO PM devices mapped to a dong or explicitly dropped.
No negative trip distances or durations.
No duplicate TAGO device IDs within the same snapshot.
PM-like trip filter retains a reasonable fraction of trips.
```

### Model Checks

```text
sum_i x_i <= F
0 <= x_i <= K_i
x_i integer
Q_is(x_i) <= A_is
Q_is(x_i) <= U * x_i
calibrated beta reproduces target utilization within tolerance
marginal profits mostly non-increasing by dong
```

### Result Checks

```text
Optimized placement should outperform at least one simple baseline.
If it does not outperform observed GCOO placement, explain why.
High-demand but high-competition dongs should not automatically dominate allocation.
Selected dongs should be interpretable based on demand, competition, and cost.
```

---

## 25. Main Tables for Final Report

### Table 1. Data Construction Summary

| Input                         | Processing             | Output                             |
| ----------------------------- | ---------------------- | ---------------------------------- |
| Seoul Bike trips              | PM-like filtering      | `H_is`, avg distance, OD imbalance |
| TAGO PM snapshots             | Spatial join by dong   | `G_is`, `C_is`, `K_i`              |
| GCOO pricing/cost assumptions | Revenue and cost model | `p_i`, `c_i`, `c_r`                |

### Table 2. Model Parameters

| Parameter | Meaning                  | Baseline | Sensitivity |
| --------- | ------------------------ | -------: | ----------: |
| `u0`      | avg rides/scooter/day    |        4 |         3-6 |
| `lambda`  | market validation effect |      0.3 |       0-0.5 |
| `theta`   | competition pressure     |      1.0 |     0.5-2.0 |
| `U`       | max rides/scooter/day    |        6 |         4-8 |
| `mu`      | imbalance cost effect    |      0.5 |       0-1.0 |

### Table 3. Baseline Comparison

| Plan                     | Expected Profit | Expected Rides | Utilization | Profit / Scooter |
| ------------------------ | --------------: | -------------: | ----------: | ---------------: |
| Observed GCOO            |                 |                |             |                  |
| Demand-Proportional      |                 |                |             |                  |
| Competitor-Following     |                 |                |             |                  |
| Proposed Nonlinear Model |                 |                |             |                  |

### Table 4. Top Allocation Dongs

| Rank | Dong | Gu  | Allocated Scooters | Expected Rides | Competitor Count | Profit / Scooter |
| ---- | ---- | --- | -----------------: | -------------: | ---------------: | ---------------: |

---

## 26. Main Figures for Final Report

Recommended figures:

1. Seoul map of PM-like demand by administrative dong.
2. Seoul map of competitor PM density by administrative dong.
3. Scatterplot: PM-like demand vs. competitor density.
4. Marginal profit curve for selected dongs.
5. Bar chart comparing expected profit across baseline plans.
6. Histogram of simulated profit for optimized vs observed placement.
7. Sensitivity tornado chart for key parameters.

---

## 27. Expected Managerial Insights

The final report should produce insights such as:

1. Demand concentration alone does not determine optimal placement.
2. Competitor density can validate market demand, but excessive density lowers marginal utilization.
3. Some high-demand dongs may be less attractive after competition and operating costs are considered.
4. Some moderate-demand dongs may be profitable because they are less saturated.
5. Increasing total scooter supply may eventually produce diminishing marginal profit.
6. The most valuable additional data is likely repeated TAGO snapshots or pilot utilization data in uncertain dongs.

---

## 28. Non-Goals

This project will not model:

1. Exact street-level scooter placement.
2. Real-time relocation during the day.
3. User-level choice behavior.
4. Detailed battery discharge dynamics.
5. Legal compliance at individual parking points.
6. Pricing optimization.
7. Multi-day inventory carryover.

These are excluded to keep the project feasible and focused on daily spatial resource allocation.

---

## 29. Final One-Sentence Model Description

GCOO’s daily placement decision is modeled as a scenario-based nonlinear resource allocation problem in which Seoul Bike data estimates PM-like demand, TAGO data measures GCOO and competitor PM supply, and the model allocates scooters across Seoul administrative dongs to maximize expected operating profit under demand saturation, competitor pressure, scooter supply, zone capacity, and operating cost constraints.
