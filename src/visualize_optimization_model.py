from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

import folium
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, MiniMap

from common import ensure_dir, write_json
from hermes_embed import inject_hermes_widget


LAMBDA_MARKET = 0.30
BETA_CAPTURE = 0.08
THETA_COMPETITION = 1.00
U_MAX_RIDES = 6.0
REVENUE_PER_RIDE_KRW = 2200.0
VARIABLE_COST_KRW = 300.0
FIXED_COST_PER_DEVICE_KRW = 2500.0
REBALANCING_KRW_PER_KM = 900.0
CAPACITY_MULTIPLIER = 1.25
OPTIMIZATION_FLEET = 2800
TEMPORAL_TOP_OD_PAIRS = 5000
TEMPORAL_SIMULATION_SEED = 20260617
TEMPORAL_RANDOM_SIGMA = 0.35
TEMPORAL_COMMON_CORRELATION = 0.35
TEMPORAL_ORIGIN_CORRELATION = 0.45
TEMPORAL_REGRESSION_WEIGHT = 0.35
TEMPORAL_MAX_ANIMATION_FLOWS = 24
TEMPORAL_MAX_ANIMATION_ZONES = 140
TEMPORAL_SHORTAGE_TABLE_ROWS = 30
SEJONG_GRID_LAT_STEP = 0.0044915558749550845
SEJONG_GRID_LON_STEP = 0.005587124211191894
OPERATING_HOUR_SEQUENCE = list(range(4, 24)) + list(range(0, 4))
OPERATOR_MOVE_FILTER_NOTE = (
    "D_i and Origin-Destination Pair flows use only ride segments where excluded_from_demand is false. "
    "Segments flagged as likely operator moves are removed when speed > 28km/h, "
    "or speed > 25km/h with repeated fast moves, same Origin-Destination Pair/time clusters, or large battery delta."
)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def safe(value: Any) -> str:
    return html.escape(str(value))


def fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}"


def fmt_float(value: float | int, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}"


def hour_label(hour: int) -> str:
    return f"{int(hour):02d}:00-{(int(hour) + 1) % 24:02d}:00"


def standard_normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def demand_capture(adjusted_demand: float, x_value: float, competitor: float) -> float:
    if x_value <= 0 or adjusted_demand <= 0:
        return 0.0
    accessibility = 1.0 - math.exp(-BETA_CAPTURE * x_value / (1.0 + THETA_COMPETITION * competitor))
    return min(adjusted_demand * accessibility, U_MAX_RIDES * x_value)


def zone_profit(adjusted_demand: float, x_value: int, competitor: float, rebalance_km: float) -> float:
    rides = demand_capture(adjusted_demand, float(x_value), competitor)
    ride_margin = (REVENUE_PER_RIDE_KRW - VARIABLE_COST_KRW) * rides
    fixed_cost = FIXED_COST_PER_DEVICE_KRW * x_value
    rebalance_cost = REBALANCING_KRW_PER_KM * rebalance_km * rides
    return ride_margin - fixed_cost - rebalance_cost


def build_zone_model(processed_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    latest = read_csv(processed_dir / "sejong_pm_latest_snapshot.csv")
    segments = read_csv(processed_dir / "sejong_pm_inferred_rides.csv")
    od_flows = read_csv(processed_dir / "sejong_pm_od_flows.csv")

    if latest.empty:
        return pd.DataFrame(), {"ok": False, "notes": ["latest snapshot 데이터가 없습니다."]}

    latest = latest.copy()
    latest["operator_name"] = latest["operator_name"].astype(str)
    latest["zone_id"] = latest["zone_id"].astype(str)

    supply = (
        latest.pivot_table(
            index="zone_id",
            columns="operator_name",
            values="device_id",
            aggfunc="nunique",
            fill_value=0,
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    for column in ["GBIKE", "ALPACA"]:
        if column not in supply.columns:
            supply[column] = 0
    supply = supply.rename(columns={"GBIKE": "gbike_current", "ALPACA": "alpaca_competitor"})

    centers = (
        latest.groupby("zone_id")
        .agg(
            latitude=("zone_center_latitude", "first"),
            longitude=("zone_center_longitude", "first"),
            avg_battery=("battery_level", "mean"),
            total_current_pm=("device_id", "nunique"),
        )
        .reset_index()
    )
    model = centers.merge(supply[["zone_id", "gbike_current", "alpaca_competitor"]], on="zone_id", how="left")
    model[["gbike_current", "alpaca_competitor"]] = model[["gbike_current", "alpaca_competitor"]].fillna(0.0)

    raw_segment_count = int(len(segments))
    excluded_segment_count = 0
    if not segments.empty and "excluded_from_demand" in segments.columns:
        excluded_mask = segments["excluded_from_demand"].astype(str).str.lower().isin({"true", "1", "yes"})
        excluded_segment_count = int(excluded_mask.sum())
        segments = segments[~excluded_mask].copy()

    if not segments.empty and {"prev_zone_id", "distance_m", "speed_kmph", "battery_delta"}.issubset(segments.columns):
        seg = segments.copy()
        seg["prev_zone_id"] = seg["prev_zone_id"].astype(str)
        for col in ["distance_m", "speed_kmph", "battery_delta"]:
            seg[col] = pd.to_numeric(seg[col], errors="coerce")
        demand = (
            seg.groupby("prev_zone_id")
            .agg(
                inferred_rides=("ride_segment_id", "count"),
                ride_devices=("device_id", "nunique"),
                avg_distance_m=("distance_m", "mean"),
                avg_speed_kmph=("speed_kmph", "mean"),
                avg_battery_delta=("battery_delta", "mean"),
            )
            .reset_index()
            .rename(columns={"prev_zone_id": "zone_id"})
        )
    else:
        demand = pd.DataFrame(columns=["zone_id", "inferred_rides", "ride_devices", "avg_distance_m"])

    model = model.merge(demand, on="zone_id", how="left")
    for col in ["inferred_rides", "ride_devices", "avg_distance_m", "avg_speed_kmph", "avg_battery_delta"]:
        if col not in model.columns:
            model[col] = 0.0
    model[["inferred_rides", "ride_devices", "avg_distance_m", "avg_speed_kmph", "avg_battery_delta"]] = model[
        ["inferred_rides", "ride_devices", "avg_distance_m", "avg_speed_kmph", "avg_battery_delta"]
    ].fillna(0.0)

    if not od_flows.empty and {"prev_zone_id", "trip_count", "avg_distance_m"}.issubset(od_flows.columns):
        od = od_flows.copy()
        od["prev_zone_id"] = od["prev_zone_id"].astype(str)
        od["trip_count"] = pd.to_numeric(od["trip_count"], errors="coerce").fillna(0.0)
        od["avg_distance_m"] = pd.to_numeric(od["avg_distance_m"], errors="coerce").fillna(0.0)
        od["weighted_distance_m"] = od["trip_count"] * od["avg_distance_m"]
        rebalance = (
            od.groupby("prev_zone_id")
            .agg(
                od_pairs=("zone_id", "nunique"),
                od_trip_count=("trip_count", "sum"),
                weighted_distance_m=("weighted_distance_m", "sum"),
            )
            .reset_index()
            .rename(columns={"prev_zone_id": "zone_id"})
        )
        rebalance["expected_rebalance_km"] = (
            rebalance["weighted_distance_m"] / rebalance["od_trip_count"].clip(lower=1) / 1000.0
        )
        rebalance = rebalance[["zone_id", "od_pairs", "expected_rebalance_km"]]
    else:
        rebalance = pd.DataFrame(columns=["zone_id", "od_pairs", "expected_rebalance_km"])

    model = model.merge(rebalance, on="zone_id", how="left")
    model[["od_pairs", "expected_rebalance_km"]] = model[["od_pairs", "expected_rebalance_km"]].fillna(0.0)

    max_competitor = float(model["alpaca_competitor"].max())
    denominator = math.log1p(max_competitor) if max_competitor > 0 else 1.0
    model["competition_index"] = model["alpaca_competitor"].map(lambda value: math.log1p(float(value)) / denominator)
    model["D_i"] = model["inferred_rides"].astype(float)
    model["A_i"] = model["D_i"] * (1.0 + LAMBDA_MARKET * model["competition_index"])
    model["K_i"] = (CAPACITY_MULTIPLIER * model["total_current_pm"]).map(math.ceil)
    model.loc[(model["K_i"] <= 0) & (model["A_i"] > 0), "K_i"] = 3
    model["K_i"] = model["K_i"].astype(int)

    latest_counts = latest.groupby("operator_name")["device_id"].nunique().to_dict()
    meta = {
        "ok": True,
        "latest_timestamp": str(latest["timestamp"].max()),
        "latest_devices": int(latest["device_id"].nunique()),
        "gbike_devices": int(latest_counts.get("GBIKE", 0)),
        "alpaca_devices": int(latest_counts.get("ALPACA", 0)),
        "zones": int(model["zone_id"].nunique()),
        "raw_ride_segments": raw_segment_count,
        "ride_segments": int(len(segments)),
        "excluded_operator_move_segments": excluded_segment_count,
        "od_pairs": int(len(od_flows)),
        "operator_move_filter_note": OPERATOR_MOVE_FILTER_NOTE,
    }
    return model, meta


def optimize_dashboard_solution(model: pd.DataFrame, fleet_size: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = model.copy()
    rows["x_star"] = 0

    candidates: list[dict[str, Any]] = []
    for row in rows.itertuples():
        previous_profit = 0.0
        for k in range(1, int(row.K_i) + 1):
            current_profit = zone_profit(row.A_i, k, row.alpaca_competitor, row.expected_rebalance_km)
            candidates.append(
                {
                    "zone_id": row.zone_id,
                    "k": k,
                    "delta_profit": current_profit - previous_profit,
                }
            )
            previous_profit = current_profit
    candidates.sort(key=lambda item: item["delta_profit"], reverse=True)

    allocation = {zone_id: 0 for zone_id in rows["zone_id"].astype(str)}
    selected = 0
    for item in candidates:
        if selected >= fleet_size:
            break
        zone_id = str(item["zone_id"])
        if allocation[zone_id] == int(item["k"]) - 1:
            allocation[zone_id] += 1
            selected += 1

    rows["x_star"] = rows["zone_id"].map(allocation).fillna(0).astype(int)
    rows["Q_i_xstar"] = [
        demand_capture(row.A_i, row.x_star, row.alpaca_competitor)
        for row in rows.itertuples(index=False)
    ]
    rows["ride_revenue_krw"] = REVENUE_PER_RIDE_KRW * rows["Q_i_xstar"]
    rows["variable_cost_krw"] = VARIABLE_COST_KRW * rows["Q_i_xstar"]
    rows["fixed_cost_krw"] = FIXED_COST_PER_DEVICE_KRW * rows["x_star"]
    rows["rebalancing_cost_krw"] = REBALANCING_KRW_PER_KM * rows["expected_rebalance_km"] * rows["Q_i_xstar"]
    rows["profit_i_krw"] = (
        rows["ride_revenue_krw"]
        - rows["variable_cost_krw"]
        - rows["fixed_cost_krw"]
        - rows["rebalancing_cost_krw"]
    )
    rows["utilization_rides_per_device"] = 0.0
    active_mask = rows["x_star"] > 0
    rows.loc[active_mask, "utilization_rides_per_device"] = (
        rows.loc[active_mask, "Q_i_xstar"] / rows.loc[active_mask, "x_star"]
    )

    solution = {
        "fleet_size": int(fleet_size),
        "allocated_devices": int(rows["x_star"].sum()),
        "active_zones": int((rows["x_star"] > 0).sum()),
        "expected_rides": float(rows["Q_i_xstar"].sum()),
        "expected_revenue_krw": float(rows["ride_revenue_krw"].sum()),
        "expected_variable_cost_krw": float(rows["variable_cost_krw"].sum()),
        "expected_fixed_cost_krw": float(rows["fixed_cost_krw"].sum()),
        "expected_rebalancing_cost_krw": float(rows["rebalancing_cost_krw"].sum()),
        "expected_profit_krw": float(rows["profit_i_krw"].sum()),
        "binding_fleet": bool(int(rows["x_star"].sum()) == int(fleet_size)),
    }
    rows = rows.sort_values(["x_star", "profit_i_krw", "A_i"], ascending=[False, False, False])
    return rows, solution


def svg_allocation_bar(rows: pd.DataFrame) -> str:
    chart_rows = rows[rows["x_star"] > 0].head(16)
    width = 900
    row_h = 30
    top = 42
    height = top + max(1, len(chart_rows)) * row_h + 22
    max_x = max(float(chart_rows["x_star"].max()) if not chart_rows.empty else 1.0, 1.0)
    parts = [
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img" aria-label="optimized allocation by zone">',
        '<text x="0" y="22" class="svg-title">최종 결과: x*가 큰 상위 zone</text>',
    ]
    for idx, row in enumerate(chart_rows.itertuples(index=False)):
        y = top + idx * row_h
        bar_w = 580 * float(row.x_star) / max_x
        parts.append(f'<text x="0" y="{y + 18}" class="svg-label">{safe(row.zone_id)}</text>')
        parts.append(f'<rect x="172" y="{y + 5}" width="{bar_w:.1f}" height="17" rx="3" fill="#0f766e" opacity="0.86"></rect>')
        parts.append(f'<text x="{182 + bar_w:.1f}" y="{y + 18}" class="svg-value">x*={fmt_int(row.x_star)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def svg_cost_revenue(rows: pd.DataFrame) -> str:
    labels = [
        ("Ride revenue 운행매출", float(rows["ride_revenue_krw"].sum()), "#0f766e"),
        ("Variable cost 변동비", -float(rows["variable_cost_krw"].sum()), "#b45309"),
        ("Fixed cost 고정비", -float(rows["fixed_cost_krw"].sum()), "#be123c"),
        ("Rebalancing cost 재배치비", -float(rows["rebalancing_cost_krw"].sum()), "#2563eb"),
        ("Net profit 순이익", float(rows["profit_i_krw"].sum()), "#172033"),
    ]
    width = 900
    height = 320
    left = 160
    top = 54
    row_h = 42
    max_abs = max(max(abs(value) for _label, value, _color in labels), 1.0)
    center = 450
    scale = 330 / max_abs
    parts = [
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img" aria-label="objective decomposition">',
        '<text x="0" y="24" class="svg-title">Objective value 분해: 운행매출 - 비용</text>',
        f'<line x1="{center}" y1="42" x2="{center}" y2="{top + row_h * len(labels)}" stroke="#94a3b8"></line>',
    ]
    for idx, (label, value, color) in enumerate(labels):
        y = top + idx * row_h
        x = center if value >= 0 else center + value * scale
        width_bar = abs(value) * scale
        parts.append(f'<text x="0" y="{y + 19}" class="svg-label">{safe(label)}</text>')
        parts.append(f'<rect x="{x:.1f}" y="{y + 4}" width="{width_bar:.1f}" height="20" rx="3" fill="{color}" opacity="0.82"></rect>')
        parts.append(f'<text x="{left}" y="{y + 19}" class="svg-value">{fmt_int(value)} KRW</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def svg_capture_curve_korean() -> str:
    width = 900
    height = 330
    left = 58
    bottom = 282
    plot_w = 775
    plot_h = 215
    x_max = 80.0
    y_max = 120.0
    competitors = [0, 10, 40, 100]
    colors = ["#0f766e", "#2563eb", "#b45309", "#be123c"]

    def sx(x: float) -> float:
        return left + plot_w * x / x_max

    def sy(y: float) -> float:
        return bottom - plot_h * y / y_max

    parts = [
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img" aria-label="demand capture curve">',
        '<text x="0" y="24" class="svg-title">Non-linear demand capture: xᵢ가 커질수록 한계효과가 체감</text>',
        '<text x="0" y="48" class="svg-label">잠재수요 Aᵢ=120으로 고정하고, 배치량 xᵢ와 경쟁 공급 Cᵢ가 기대 ride Qᵢ를 어떻게 바꾸는지 보여줍니다.</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{left + plot_w}" y2="{bottom}" stroke="#94a3b8"></line>',
        f'<line x1="{left}" y1="{bottom}" x2="{left}" y2="{bottom - plot_h}" stroke="#94a3b8"></line>',
        f'<text x="{left + plot_w - 88}" y="{bottom + 28}" class="svg-label">배치량 xᵢ</text>',
        f'<text x="0" y="{bottom - plot_h + 12}" class="svg-label">기대 rides Qᵢ</text>',
    ]
    for competitor, color in zip(competitors, colors, strict=False):
        points = []
        for x in range(0, 81):
            points.append(f"{sx(x):.1f},{sy(demand_capture(120, x, competitor)):.1f}")
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3"></polyline>')
    for idx, (competitor, color) in enumerate(zip(competitors, colors, strict=False)):
        y = 52 + idx * 23
        parts.append(f'<rect x="628" y="{y - 10}" width="16" height="4" fill="{color}"></rect>')
        parts.append(f'<text x="650" y="{y - 5}" class="svg-label">ALPACA Cᵢ={competitor}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def capture_curve_panel() -> str:
    return f"""
      <h2>Non-linear demand capture 해석</h2>
      <p>
        이 그래프는 Solver 안의 수요식 <code>Q_i(x_i)</code>가 어떤 모양인지 보여줍니다.
        핵심은 PM을 더 많이 놓을수록 기대 ride는 증가하지만, 추가 1대가 만드는 효과는 점점 작아진다는 점입니다.
      </p>
      {svg_capture_curve_korean()}
      <div class="equation" style="margin-top:12px;">
        <b>그래프에 쓰인 수요식</b>
        <div class="math">\\[Q_i(x_i)=\\min\\left\\{{A_i\\left(1-e^{{-\\frac{{\\beta x_i}}{{1+\\theta C_i}}}}\\right),\\;Ux_i\\right\\}}\\]</div>
        <div class="equation-note">
          <strong>왼쪽 항:</strong> <code>A_i(1-exp(...))</code>는 zone의 잠재수요 중 GBIKE가 실제로 capture하는 ride 수입니다.
          <br><strong>오른쪽 항:</strong> <code>Ux_i</code>는 PM 대수로 가능한 최대 처리량입니다. PM 1대가 하루에 처리할 수 있는 ride 수에는 물리적 한계가 있으므로 상한을 둡니다.
          <br><strong>min을 쓰는 이유:</strong> 실제 기대 ride는 “수요가 만들어내는 ride”와 “기기가 처리할 수 있는 ride” 중 더 작은 값으로 제한됩니다.
        </div>
      </div>
      <div class="simulation-grid">
        <div class="sim-card">
          <b>왜 비선형인가</b>
          <span>처음 몇 대를 배치할 때는 사용자가 가까운 PM을 찾을 확률이 크게 올라갑니다. 하지만 이미 PM이 충분히 깔린 zone에서는 1대를 더 놓아도 접근성 개선폭이 작습니다. 그래서 직선이 아니라 위로 볼록한 concave curve가 됩니다.</span>
        </div>
        <div class="sim-card">
          <b>왜 <code>1-exp(-...)</code>인가</b>
          <span>이 형태는 “처음에는 빠르게 증가하고, 나중에는 1에 가까워지며 포화되는” 접근성/확률 모델입니다. 즉 배치량이 무한히 커져도 capture 비율은 100%를 넘을 수 없습니다.</span>
        </div>
        <div class="sim-card">
          <b>ALPACA Cᵢ가 커질 때</b>
          <span>그래프의 색상별 선은 경쟁 공급량 <code>C_i</code>가 다른 경우입니다. <code>C_i</code>가 커질수록 분모 <code>1+θC_i</code>가 커져 같은 <code>x_i</code>에서도 GBIKE가 잡는 수요가 줄어듭니다.</span>
        </div>
        <div class="sim-card">
          <b>Solver 관점의 의미</b>
          <span>Solver는 곡선이 가파른 구간의 zone에 PM을 먼저 배치하려고 합니다. 곡선이 이미 평평한 zone은 추가 배치의 marginal benefit이 작으므로, 비용을 고려하면 덜 매력적인 후보가 됩니다.</span>
        </div>
      </div>
      <div class="table-wrap compact-table sim-table">
        <table>
          <thead><tr><th>기호</th><th>현재 그림의 값/역할</th><th>해석</th></tr></thead>
          <tbody>
            <tr><td>\\(A_i\\)</td><td>120 rides로 고정</td><td>그래프 비교를 쉽게 하기 위해 zone의 보정 잠재수요를 같은 값으로 둠</td></tr>
            <tr><td>\\(x_i\\)</td><td>0대부터 80대까지 변화</td><td>04:00에 해당 zone에 배치하는 GBIKE PM 수</td></tr>
            <tr><td>\\(C_i\\)</td><td>0, 10, 40, 100</td><td>ALPACA 경쟁 공급량이 커질수록 GBIKE capture curve가 아래로 내려감</td></tr>
            <tr><td>\\(\\beta\\)</td><td>{fmt_float(BETA_CAPTURE, 2)}</td><td>배치량 증가가 수요 capture로 전환되는 속도</td></tr>
            <tr><td>\\(\\theta\\)</td><td>{fmt_float(THETA_COMPETITION, 2)}</td><td>경쟁 공급량이 GBIKE capture를 약화시키는 강도</td></tr>
            <tr><td>\\(U\\)</td><td>{fmt_float(U_MAX_RIDES, 1)} rides/device/day</td><td>PM 1대가 하루 처리할 수 있는 최대 ride 수</td></tr>
          </tbody>
        </table>
      </div>
      <p class="note"><strong>해석:</strong> 이 Section은 배치량을 한 대씩 늘릴 때 기대 ride가 왜 같은 폭으로 늘지 않는지 설명합니다. 따라서 모델은 “PM을 많이 두면 무조건 좋다”가 아니라, 각 zone에서 추가 1대가 만드는 수요 증가분이 비용보다 큰지를 비교하게 됩니다.</p>
    """


def simulation_summary(rows: pd.DataFrame) -> dict[str, Any]:
    active = rows[rows["x_star"] > 0]
    base_profit = float(active["profit_i_krw"].sum())
    profits: list[float] = []
    for idx in range(120):
        demand_multiplier = math.exp(0.20 * math.sin(idx * 1.31) - 0.5 * 0.20**2)
        cost_multiplier = 1.0 + 0.16 * math.cos(idx * 0.73)
        profit = float(
            (
                active["ride_revenue_krw"] * demand_multiplier
                - active["variable_cost_krw"] * demand_multiplier
                - active["fixed_cost_krw"]
                - active["rebalancing_cost_krw"] * cost_multiplier
            ).sum()
        )
        profits.append(profit)
    values = sorted(profits or [base_profit])
    p10 = values[max(0, int(len(values) * 0.10) - 1)]
    p50 = values[int(len(values) * 0.50)]
    p90 = values[min(len(values) - 1, int(len(values) * 0.90))]
    return {
        "base_profit": base_profit,
        "values": values,
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "scenario_count": len(values),
    }


def svg_simulation(summary: dict[str, Any]) -> str:
    values = summary["values"]
    p10 = float(summary["p10"])
    p50 = float(summary["p50"])
    p90 = float(summary["p90"])
    base_profit = float(summary["base_profit"])
    min_v, max_v = min(values), max(values)
    span = max(max_v - min_v, 1.0)
    bins = 18
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - min_v) / span * bins))] += 1
    max_count = max(max(counts), 1)
    width = 900
    height = 345
    left = 54
    bottom = 270
    plot_w = 770
    plot_h = 190
    bar_gap = 4
    bar_w = (plot_w - bar_gap * (bins - 1)) / bins

    def sx(value: float) -> float:
        return left + plot_w * (value - min_v) / span

    parts = [
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img" aria-label="simulation profit distribution">',
        '<text x="0" y="24" class="svg-title">Simulation: 수요와 비용이 흔들릴 때 Objective value 분포</text>',
        '<text x="0" y="48" class="svg-label">x* 배치는 고정하고, 실제 하루의 demand shock와 cost shock만 바꾸어 120개 scenario를 다시 계산합니다.</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{left + plot_w}" y2="{bottom}" stroke="#94a3b8"></line>',
        f'<line x1="{left}" y1="{bottom}" x2="{left}" y2="{bottom - plot_h}" stroke="#94a3b8"></line>',
        f'<text x="{left + plot_w - 170}" y="{bottom + 34}" class="svg-label">Objective value (KRW)</text>',
        f'<text x="0" y="{bottom - plot_h + 12}" class="svg-label">scenario 수</text>',
    ]
    for idx, count in enumerate(counts):
        x = left + idx * (bar_w + bar_gap)
        h = plot_h * count / max_count
        parts.append(f'<rect x="{x:.1f}" y="{bottom - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="3" fill="#0f766e" opacity="0.74"></rect>')
    for label, value, color in [("P10", p10, "#be123c"), ("P50", p50, "#2563eb"), ("P90", p90, "#0f766e")]:
        x = sx(value)
        parts.append(f'<line x1="{x:.1f}" y1="{bottom - plot_h}" x2="{x:.1f}" y2="{bottom}" stroke="{color}" stroke-width="2" stroke-dasharray="4 4"></line>')
        parts.append(f'<text x="{x + 5:.1f}" y="{bottom - plot_h + 18}" class="svg-label">{label}: {fmt_int(value)} KRW</text>')
    base_x = sx(base_profit)
    parts.append(f'<line x1="{base_x:.1f}" y1="{bottom - plot_h}" x2="{base_x:.1f}" y2="{bottom}" stroke="#172033" stroke-width="2"></line>')
    parts.append(f'<text x="{base_x + 5:.1f}" y="{bottom - 8}" class="svg-value">baseline: {fmt_int(base_profit)} KRW</text>')
    parts.append(f'<text x="{left}" y="{bottom + 18}" class="svg-label">{fmt_int(min_v)}</text>')
    parts.append(f'<text x="{left + plot_w - 58}" y="{bottom + 18}" class="svg-label">{fmt_int(max_v)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def simulation_panel(rows: pd.DataFrame) -> str:
    summary = simulation_summary(rows)
    spread = float(summary["p90"]) - float(summary["p10"])
    downside = float(summary["base_profit"]) - float(summary["p10"])
    return f"""
      <h2>Simulation: Objective value의 불확실성</h2>
      <p>
        Solver가 찾은 최적 배치 <code>x*</code>가 실제 운영일에도 항상 같은 profit을 낸다고 단정할 수는 없습니다.
        이 그래프는 <code>x*</code>는 그대로 둔 채, 하루 수요와 재배치비가 예상보다 높거나 낮아지는 상황을 여러 번 만들어
        Objective value가 어느 범위에서 흔들리는지 보여줍니다.
      </p>
      {svg_simulation(summary)}
      <div class="simulation-grid">
        <div class="sim-card">
          <b>그래프를 읽는 법</b>
          <span>각 막대는 특정 Objective value 구간에 들어온 scenario 개수입니다. 막대가 오른쪽에 많을수록 같은 배치 <code>x*</code>가 여러 불확실성 상황에서도 높은 profit을 낸다는 뜻입니다.</span>
        </div>
        <div class="sim-card">
          <b>demand shock</b>
          <span>실제 ride 수요가 예측보다 높거나 낮아지는 효과입니다. 수요가 커지면 운행매출과 ride당 변동비가 함께 증가하고, 수요가 작아지면 둘 다 줄어듭니다.</span>
        </div>
        <div class="sim-card">
          <b>cost shock</b>
          <span>재배치비가 평소보다 비싸지거나 싸지는 효과입니다. 예를 들어 회수 동선이 길어지거나 인력/차량 비용이 올라가면 같은 ride 수에서도 rebalancing cost가 커집니다.</span>
        </div>
        <div class="sim-card">
          <b>P10 / P50 / P90</b>
          <span><code>P10</code>은 나쁜 쪽 10% 경계, <code>P50</code>은 중앙값, <code>P90</code>은 좋은 쪽 10% 경계입니다. 따라서 <code>P10</code>은 downside risk, <code>P90-P10</code>은 profit 변동폭으로 해석할 수 있습니다.</span>
        </div>
      </div>
      <div class="table-wrap compact-table sim-table">
        <table>
          <thead><tr><th>항목</th><th>값</th><th>해석</th></tr></thead>
          <tbody>
            <tr><td>Scenario 수</td><td>{fmt_int(summary["scenario_count"])}</td><td>서로 다른 demand/cost 조합을 120번 계산</td></tr>
            <tr><td>Baseline objective</td><td>{fmt_int(summary["base_profit"])} KRW</td><td>shock를 주지 않은 기존 최적화 결과의 profit</td></tr>
            <tr><td>P10 objective</td><td>{fmt_int(summary["p10"])} KRW</td><td>나쁜 쪽 10% scenario에서 기대할 수 있는 하방 profit 기준</td></tr>
            <tr><td>P50 objective</td><td>{fmt_int(summary["p50"])} KRW</td><td>shock scenario들의 중앙값</td></tr>
            <tr><td>P90 objective</td><td>{fmt_int(summary["p90"])} KRW</td><td>좋은 쪽 10% scenario에서의 상방 profit 기준</td></tr>
            <tr><td>P90 - P10</td><td>{fmt_int(spread)} KRW</td><td>같은 배치안의 objective 변동폭</td></tr>
            <tr><td>Baseline - P10</td><td>{fmt_int(downside)} KRW</td><td>기준 예측 대비 하방으로 밀릴 수 있는 폭</td></tr>
          </tbody>
        </table>
      </div>
    """


def load_clean_temporal_segments(processed_dir: Path) -> pd.DataFrame:
    segments = read_csv(processed_dir / "sejong_pm_inferred_rides.csv")
    required = {"timestamp", "prev_zone_id", "zone_id", "prev_latitude", "prev_longitude", "latitude", "longitude"}
    if segments.empty or not required.issubset(segments.columns):
        return pd.DataFrame()

    seg = segments.copy()
    if "excluded_from_demand" in seg.columns:
        excluded = seg["excluded_from_demand"].astype(str).str.lower().isin({"true", "1", "yes"})
        seg = seg[~excluded].copy()
    seg = seg.dropna(subset=["timestamp", "prev_zone_id", "zone_id"])
    if seg.empty:
        return pd.DataFrame()

    timestamps = pd.to_datetime(seg["timestamp"], errors="coerce", utc=True)
    seg = seg[timestamps.notna()].copy()
    if seg.empty:
        return pd.DataFrame()

    seg["timestamp_kst"] = timestamps[timestamps.notna()].dt.tz_convert("Asia/Seoul")
    seg["operating_day"] = (seg["timestamp_kst"] - pd.Timedelta(hours=4)).dt.date.astype(str)
    seg["sim_hour"] = seg["timestamp_kst"].dt.hour.astype(int)
    seg["prev_zone_id"] = seg["prev_zone_id"].astype(str)
    seg["zone_id"] = seg["zone_id"].astype(str)
    for col in ["prev_latitude", "prev_longitude", "latitude", "longitude"]:
        seg[col] = pd.to_numeric(seg[col], errors="coerce")
    return seg.dropna(subset=["prev_latitude", "prev_longitude", "latitude", "longitude"])


def fit_temporal_od_rates(segments: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if segments.empty:
        return pd.DataFrame(), {"ok": False, "notes": ["clean temporal segment가 없습니다."]}

    pair_totals = (
        segments.groupby(["prev_zone_id", "zone_id"])
        .agg(
            observed_count=("timestamp", "count"),
            origin_latitude=("prev_latitude", "mean"),
            origin_longitude=("prev_longitude", "mean"),
            dest_latitude=("latitude", "mean"),
            dest_longitude=("longitude", "mean"),
            avg_distance_m=("distance_m", "mean") if "distance_m" in segments.columns else ("timestamp", "count"),
        )
        .reset_index()
        .sort_values("observed_count", ascending=False)
    )
    total_observed_pairs = int(len(pair_totals))
    pair_totals = pair_totals.head(TEMPORAL_TOP_OD_PAIRS)
    if pair_totals.empty:
        return pd.DataFrame(), {"ok": False, "notes": ["Origin-Destination Pair가 없습니다."]}

    pair_totals["pair_key"] = pair_totals["prev_zone_id"].astype(str) + "||" + pair_totals["zone_id"].astype(str)
    pair_keys = pair_totals["pair_key"].tolist()
    pair_key_set = set(pair_keys)
    observed_days = sorted(segments["operating_day"].dropna().unique().tolist())
    if not observed_days:
        observed_days = ["observed"]

    top_segments = segments.copy()
    top_segments["pair_key"] = top_segments["prev_zone_id"].astype(str) + "||" + top_segments["zone_id"].astype(str)
    top_segments = top_segments[top_segments["pair_key"].isin(pair_key_set)].copy()
    counts = (
        top_segments.groupby(["operating_day", "sim_hour", "pair_key"])
        .size()
        .reset_index(name="count")
    )

    n_hours = len(OPERATING_HOUR_SEQUENCE)
    n_pairs = len(pair_keys)
    n_days = len(observed_days)
    panel_size = max(1, n_days * n_hours * n_pairs)
    counts["log_count"] = np.log1p(counts["count"].to_numpy(dtype=float))

    grand_mean = float(counts["log_count"].sum() / panel_size)
    hour_log_sum = counts.groupby("sim_hour")["log_count"].sum().to_dict()
    pair_log_sum = counts.groupby("pair_key")["log_count"].sum().to_dict()
    hour_effects = {
        int(hour): float(hour_log_sum.get(hour, 0.0) / max(1, n_days * n_pairs) - grand_mean)
        for hour in OPERATING_HOUR_SEQUENCE
    }
    pair_effects = {
        pair_key: float(pair_log_sum.get(pair_key, 0.0) / max(1, n_days * n_hours) - grand_mean)
        for pair_key in pair_keys
    }

    sum_fitted_sq = 0.0
    for pair_key in pair_keys:
        pair_effect = pair_effects[pair_key]
        for hour in OPERATING_HOUR_SEQUENCE:
            fitted_log = grand_mean + hour_effects[int(hour)] + pair_effect
            sum_fitted_sq += n_days * fitted_log * fitted_log

    if counts.empty:
        rmse = 0.0
    else:
        counts["fitted_log"] = [
            grand_mean + hour_effects[int(row.sim_hour)] + pair_effects[str(row.pair_key)]
            for row in counts.itertuples(index=False)
        ]
        sse_adjustment = float(
            np.sum(
                np.square(counts["log_count"].to_numpy(dtype=float))
                - 2.0
                * counts["log_count"].to_numpy(dtype=float)
                * counts["fitted_log"].to_numpy(dtype=float)
            )
        )
        rmse = float(math.sqrt(max(0.0, sum_fitted_sq + sse_adjustment) / panel_size))

    empirical = (
        counts.groupby(["pair_key", "sim_hour"])["count"]
        .sum()
        .reset_index(name="observed_hour_count")
    )
    empirical["empirical_rate"] = empirical["observed_hour_count"] / max(1, n_days)
    rate_rows: list[dict[str, Any]] = []
    empirical_lookup = {
        (str(row.pair_key), int(row.sim_hour)): float(row.empirical_rate)
        for row in empirical.itertuples(index=False)
    }
    pair_meta = pair_totals.set_index("pair_key").to_dict("index")
    for pair_key in pair_keys:
        meta = pair_meta[pair_key]
        for hour in OPERATING_HOUR_SEQUENCE:
            regression_log = grand_mean + hour_effects[int(hour)] + pair_effects[pair_key]
            regression_rate = max(0.0, math.expm1(regression_log))
            empirical_rate = float(empirical_lookup.get((pair_key, hour), 0.0))
            blended_rate = (
                (1.0 - TEMPORAL_REGRESSION_WEIGHT) * empirical_rate
                + TEMPORAL_REGRESSION_WEIGHT * regression_rate
            )
            rate_rows.append(
                {
                    "pair_key": pair_key,
                    "prev_zone_id": str(meta["prev_zone_id"]),
                    "zone_id": str(meta["zone_id"]),
                    "sim_hour": int(hour),
                    "hour_label": hour_label(hour),
                    "empirical_rate": empirical_rate,
                    "regression_rate": regression_rate,
                    "blended_rate": max(0.0, blended_rate),
                    "observed_count": int(meta["observed_count"]),
                    "origin_latitude": float(meta["origin_latitude"]),
                    "origin_longitude": float(meta["origin_longitude"]),
                    "dest_latitude": float(meta["dest_latitude"]),
                    "dest_longitude": float(meta["dest_longitude"]),
                }
            )

    meta = {
        "ok": True,
        "observed_days": len(observed_days),
        "clean_segments": int(len(segments)),
        "total_observed_od_pairs": total_observed_pairs,
        "modeled_od_pairs": int(n_pairs),
        "modeled_od_pair_limit": int(TEMPORAL_TOP_OD_PAIRS),
        "hour_count": int(n_hours),
        "regression_formula": "log(1 + count_{day,hour,Origin-Destination Pair}) = alpha + hour_effect + origin_destination_pair_effect + error",
        "regression_solver": "balanced fixed-effect closed form over sparse Origin-Destination Pair/hour counts",
        "regression_rmse_log1p": rmse,
        "regression_weight": TEMPORAL_REGRESSION_WEIGHT,
    }
    return pd.DataFrame(rate_rows), meta


def build_zone_coordinate_lookup(rows: pd.DataFrame, rate_rows: pd.DataFrame) -> dict[str, dict[str, float]]:
    coords: dict[str, dict[str, float]] = {}
    for row in rows.itertuples(index=False):
        zone_id = str(row.zone_id)
        coords[zone_id] = {"latitude": float(row.latitude), "longitude": float(row.longitude)}
    for row in rate_rows.itertuples(index=False):
        origin = str(row.prev_zone_id)
        dest = str(row.zone_id)
        coords.setdefault(origin, {"latitude": float(row.origin_latitude), "longitude": float(row.origin_longitude)})
        coords.setdefault(dest, {"latitude": float(row.dest_latitude), "longitude": float(row.dest_longitude)})
    return coords


def allocate_served_by_destination(items: list[dict[str, Any]], served_total: int) -> list[int]:
    if not items or served_total <= 0:
        return [0 for _item in items]
    total_demand = sum(int(item["demand"]) for item in items)
    if total_demand <= 0:
        return [0 for _item in items]
    raw_values = [served_total * int(item["demand"]) / total_demand for item in items]
    served = [int(math.floor(value)) for value in raw_values]
    remainder = int(served_total - sum(served))
    order = sorted(range(len(items)), key=lambda idx: raw_values[idx] - served[idx], reverse=True)
    for idx in order[:remainder]:
        served[idx] += 1
    return served


def build_temporal_inventory_simulation(rows: pd.DataFrame, processed_dir: Path) -> dict[str, Any]:
    segments = load_clean_temporal_segments(processed_dir)
    rate_rows, rate_meta = fit_temporal_od_rates(segments)
    if rate_rows.empty:
        return {
            "ok": False,
            "summary": {"notes": rate_meta.get("notes", ["temporal simulation을 만들 수 없습니다."])},
            "hourly_summary": [],
            "shortages": [],
            "animation": {"frames": [], "bounds": {}},
            "method": rate_meta,
        }

    target_expected_rides = float(rows["Q_i_xstar"].sum()) if "Q_i_xstar" in rows.columns else 0.0
    uncalibrated_rate_total = float(rate_rows["blended_rate"].sum())
    demand_calibration_factor = (
        target_expected_rides / uncalibrated_rate_total
        if target_expected_rides > 0.0 and uncalibrated_rate_total > 0.0
        else 1.0
    )
    rate_rows = rate_rows.copy()
    rate_rows["base_blended_rate"] = rate_rows["blended_rate"]
    rate_rows["blended_rate"] = rate_rows["blended_rate"] * demand_calibration_factor

    coords = build_zone_coordinate_lookup(rows, rate_rows)
    inventory = {
        str(row.zone_id): int(round(float(row.x_star) + float(row.alpaca_competitor)))
        for row in rows.itertuples(index=False)
    }
    for zone_id in coords:
        inventory.setdefault(zone_id, 0)

    rng = np.random.default_rng(TEMPORAL_SIMULATION_SEED)
    simulated_demands: list[dict[str, Any]] = []
    for hour in OPERATING_HOUR_SEQUENCE:
        hour_rates = rate_rows[rate_rows["sim_hour"] == hour].copy()
        common_z = float(rng.normal())
        origins = sorted(hour_rates["prev_zone_id"].astype(str).unique().tolist())
        origin_z = {origin: float(rng.normal()) for origin in origins}
        for row in hour_rates.itertuples(index=False):
            independent_z = float(rng.normal())
            origin = str(row.prev_zone_id)
            residual_scale = math.sqrt(
                max(0.0, 1.0 - TEMPORAL_COMMON_CORRELATION**2 - TEMPORAL_ORIGIN_CORRELATION**2)
            )
            z_value = (
                TEMPORAL_COMMON_CORRELATION * common_z
                + TEMPORAL_ORIGIN_CORRELATION * origin_z[origin]
                + residual_scale * independent_z
            )
            multiplier = math.exp(TEMPORAL_RANDOM_SIGMA * z_value - 0.5 * TEMPORAL_RANDOM_SIGMA**2)
            demand_lambda = max(0.0, float(row.blended_rate) * multiplier)
            demand = int(rng.poisson(demand_lambda))
            if demand <= 0:
                continue
            simulated_demands.append(
                {
                    "sim_hour": int(hour),
                    "hour_label": hour_label(hour),
                    "origin": origin,
                    "destination": str(row.zone_id),
                    "demand": demand,
                    "rate": float(row.blended_rate),
                    "base_rate": float(row.base_blended_rate),
                    "regression_rate": float(row.regression_rate),
                    "empirical_rate": float(row.empirical_rate),
                    "normal_z": z_value,
                    "normal_quantile": standard_normal_cdf(z_value),
                    "origin_latitude": float(row.origin_latitude),
                    "origin_longitude": float(row.origin_longitude),
                    "dest_latitude": float(row.dest_latitude),
                    "dest_longitude": float(row.dest_longitude),
                }
            )

    hourly_summary: list[dict[str, Any]] = []
    shortages: list[dict[str, Any]] = []
    movement_records: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    all_lats = [value["latitude"] for value in coords.values()]
    all_lons = [value["longitude"] for value in coords.values()]
    bounds = {
        "min_latitude": min(all_lats) if all_lats else 36.45,
        "max_latitude": max(all_lats) if all_lats else 36.65,
        "min_longitude": min(all_lons) if all_lons else 127.20,
        "max_longitude": max(all_lons) if all_lons else 127.35,
    }

    for hour in OPERATING_HOUR_SEQUENCE:
        hour_items = [item for item in simulated_demands if int(item["sim_hour"]) == hour]
        start_inventory = int(sum(inventory.values()))
        by_origin: dict[str, list[dict[str, Any]]] = {}
        for item in hour_items:
            by_origin.setdefault(str(item["origin"]), []).append(item)

        arrivals: dict[str, int] = {}
        flow_events: list[dict[str, Any]] = []
        zone_hour: dict[str, dict[str, int]] = {}
        hour_demand = 0
        hour_served = 0
        hour_unmet = 0

        for origin, items in by_origin.items():
            total_demand = int(sum(int(item["demand"]) for item in items))
            available = int(max(0, inventory.get(origin, 0)))
            served_total = min(total_demand, available)
            unmet_total = total_demand - served_total
            inventory[origin] = available - served_total
            served_by_item = allocate_served_by_destination(items, served_total)

            hour_demand += total_demand
            hour_served += served_total
            hour_unmet += unmet_total
            zone_hour.setdefault(origin, {"demand": 0, "served": 0, "unmet": 0})
            zone_hour[origin]["demand"] += total_demand
            zone_hour[origin]["served"] += served_total
            zone_hour[origin]["unmet"] += unmet_total

            if unmet_total > 0:
                shortages.append(
                    {
                        "hour": int(hour),
                        "hour_label": hour_label(hour),
                        "zone_id": origin,
                        "demand": total_demand,
                        "served": served_total,
                        "shortage": unmet_total,
                        "available_at_hour_start": available,
                        "inventory_after_departures": int(inventory.get(origin, 0)),
                    }
                )

            for item, served in zip(items, served_by_item, strict=False):
                destination = str(item["destination"])
                unmet = int(item["demand"]) - int(served)
                arrivals[destination] = arrivals.get(destination, 0) + int(served)
                flow_event = {
                    "hour": int(hour),
                    "hour_label": hour_label(hour),
                    "origin": origin,
                    "destination": destination,
                    "demand": int(item["demand"]),
                    "served": int(served),
                    "unmet": unmet,
                    "rate": float(item["rate"]),
                    "base_rate": float(item["base_rate"]),
                    "regression_rate": float(item["regression_rate"]),
                    "empirical_rate": float(item["empirical_rate"]),
                    "normal_z": float(item["normal_z"]),
                    "normal_quantile": float(item["normal_quantile"]),
                    "origin_latitude": float(item["origin_latitude"]),
                    "origin_longitude": float(item["origin_longitude"]),
                    "dest_latitude": float(item["dest_latitude"]),
                    "dest_longitude": float(item["dest_longitude"]),
                }
                flow_events.append(flow_event)
                movement_records.append(flow_event)

        for destination, count in arrivals.items():
            inventory[destination] = int(inventory.get(destination, 0)) + int(count)

        end_inventory = int(sum(inventory.values()))
        active_zones = set()
        for event in flow_events:
            active_zones.add(str(event["origin"]))
            active_zones.add(str(event["destination"]))
        active_zones.update(row["zone_id"] for row in shortages if int(row["hour"]) == hour)

        zone_records = []
        for zone_id in active_zones:
            coord = coords.get(zone_id)
            if not coord:
                continue
            stats = zone_hour.get(zone_id, {"demand": 0, "served": 0, "unmet": 0})
            zone_records.append(
                {
                    "zone_id": zone_id,
                    "latitude": float(coord["latitude"]),
                    "longitude": float(coord["longitude"]),
                    "inventory": int(inventory.get(zone_id, 0)),
                    "demand": int(stats["demand"]),
                    "served": int(stats["served"]),
                    "unmet": int(stats["unmet"]),
                }
            )
        zone_records = sorted(
            zone_records,
            key=lambda item: (item["unmet"], item["demand"], item["inventory"]),
            reverse=True,
        )[:TEMPORAL_MAX_ANIMATION_ZONES]
        flow_records = sorted(
            flow_events,
            key=lambda item: (item["served"], item["demand"], -item["unmet"]),
            reverse=True,
        )[:TEMPORAL_MAX_ANIMATION_FLOWS]

        service_rate = hour_served / hour_demand if hour_demand > 0 else 1.0
        hourly_summary.append(
            {
                "hour": int(hour),
                "hour_label": hour_label(hour),
                "demand": int(hour_demand),
                "served": int(hour_served),
                "unmet": int(hour_unmet),
                "service_rate": service_rate,
                "inventory_start": start_inventory,
                "inventory_end": end_inventory,
            }
        )
        frames.append(
            {
                "hour": int(hour),
                "hour_label": hour_label(hour),
                "demand": int(hour_demand),
                "served": int(hour_served),
                "unmet": int(hour_unmet),
                "service_rate": service_rate,
                "flows": flow_records,
                "zones": zone_records,
            }
        )

    total_demand = int(sum(row["demand"] for row in hourly_summary))
    total_served = int(sum(row["served"] for row in hourly_summary))
    total_unmet = int(sum(row["unmet"] for row in hourly_summary))
    shortage_zones = sorted({row["zone_id"] for row in shortages})
    peak_hour = max(hourly_summary, key=lambda item: item["unmet"], default={"hour_label": "n/a", "unmet": 0})
    initial_gcoo = int(round(float(rows["x_star"].sum())))
    initial_alpaca = int(round(float(rows["alpaca_competitor"].sum())))
    summary = {
        "initial_gcoo_p_star": initial_gcoo,
        "initial_alpaca_latest": initial_alpaca,
        "initial_total_inventory": initial_gcoo + initial_alpaca,
        "target_expected_rides": target_expected_rides,
        "simulated_demand": total_demand,
        "demand_gap_vs_target": total_demand - target_expected_rides,
        "demand_gap_rate_vs_target": (
            (total_demand - target_expected_rides) / target_expected_rides if target_expected_rides > 0 else 0.0
        ),
        "demand_calibration_factor": demand_calibration_factor,
        "served_rides": total_served,
        "unmet_rides": total_unmet,
        "service_rate": total_served / total_demand if total_demand > 0 else 1.0,
        "unmet_rate": total_unmet / total_demand if total_demand > 0 else 0.0,
        "shortage_zone_count": len(shortage_zones),
        "shortage_event_count": len(shortages),
        "peak_shortage_hour": peak_hour["hour_label"],
        "peak_shortage_count": int(peak_hour["unmet"]),
        "terminal_inventory": int(sum(inventory.values())),
    }
    method = {
        **rate_meta,
        "random_seed": TEMPORAL_SIMULATION_SEED,
        "random_sigma": TEMPORAL_RANDOM_SIGMA,
        "common_correlation": TEMPORAL_COMMON_CORRELATION,
        "origin_correlation": TEMPORAL_ORIGIN_CORRELATION,
        "target_expected_rides": target_expected_rides,
        "uncalibrated_temporal_rate_total": uncalibrated_rate_total,
        "demand_calibration_factor": demand_calibration_factor,
        "calibration_note": "Blended hourly Origin-Destination Pair rates are scaled so the expected simulated day demand matches optimization expected rides sum_i Q_i(x_i*).",
        "norm_inv_note": "Correlated standard-normal shocks Z are equivalent to applying NORM.INV/Phi^-1 to correlated uniform quantiles; demand uses Poisson(lambda_hat * exp(sigma Z - sigma^2/2)).",
        "initial_inventory_rule": "GCOO inventory starts from P*=x*, ALPACA inventory starts from latest snapshot, and both are pooled as available PM supply for combined market demand.",
    }
    top_shortages = sorted(shortages, key=lambda item: item["shortage"], reverse=True)[:TEMPORAL_SHORTAGE_TABLE_ROWS]
    return {
        "ok": True,
        "summary": summary,
        "hourly_summary": hourly_summary,
        "movements": movement_records,
        "shortages": shortages,
        "top_shortages": top_shortages,
        "top_movements": sorted(movement_records, key=lambda item: item["demand"], reverse=True)[:40],
        "animation": {"bounds": bounds, "frames": frames},
        "method": method,
    }


def shortage_table(shortages: list[dict[str, Any]]) -> str:
    if not shortages:
        return "<p class=\"note\">이번 simulated day에서는 수요가 있었지만 PM이 부족했던 zone이 없습니다.</p>"
    rows = []
    for row in shortages[:TEMPORAL_SHORTAGE_TABLE_ROWS]:
        rows.append(
            "<tr>"
            f"<td>{safe(row['hour_label'])}</td>"
            f"<td>{safe(row['zone_id'])}</td>"
            f"<td>{fmt_int(row['demand'])}</td>"
            f"<td>{fmt_int(row['served'])}</td>"
            f"<td>{fmt_int(row['shortage'])}</td>"
            f"<td>{fmt_int(row['available_at_hour_start'])}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap compact-table sim-table\">"
        "<table><thead><tr><th>시간대</th><th>Zone</th><th>수요</th><th>처리</th><th>부족</th><th>시작 재고</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def hourly_inventory_table(hourly_rows: list[dict[str, Any]]) -> str:
    body = []
    for row in hourly_rows:
        body.append(
            "<tr>"
            f"<td>{safe(row['hour_label'])}</td>"
            f"<td>{fmt_int(row['demand'])}</td>"
            f"<td>{fmt_int(row['served'])}</td>"
            f"<td>{fmt_int(row['unmet'])}</td>"
            f"<td>{fmt_float(100.0 * row['service_rate'], 1)}%</td>"
            f"<td>{fmt_int(row['inventory_end'])}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap compact-table sim-table\">"
        "<table><thead><tr><th>시간대</th><th>수요</th><th>처리</th><th>미처리</th><th>처리율</th><th>종료 재고</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def render_temporal_inventory_map(temporal: dict[str, Any], out_path: Path) -> None:
    animation = temporal.get("animation", {})
    bounds = animation.get("bounds", {})
    center = [
        (float(bounds.get("min_latitude", 36.45)) + float(bounds.get("max_latitude", 36.65))) / 2.0,
        (float(bounds.get("min_longitude", 127.20)) + float(bounds.get("max_longitude", 127.35))) / 2.0,
    ]
    m = folium.Map(location=center, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB positron", show=True).add_to(m)

    summary = temporal.get("summary", {})
    animation_json = json.dumps(animation, ensure_ascii=False)
    summary_json = json.dumps(summary, ensure_ascii=False)
    map_name = m.get_name()
    custom_style = f"""
    <style>
      .temporal-control {{
        min-width: 360px;
        max-width: 420px;
        padding: 12px 13px;
        background: rgba(255,255,255,.96);
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        box-shadow: 0 10px 28px rgba(15,23,42,.18);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #0f172a;
      }}
      .temporal-control .title {{ font-size: 14px; font-weight: 800; margin-bottom: 7px; }}
      .temporal-control .row {{ display: grid; grid-template-columns: auto minmax(150px,1fr) auto; gap: 8px; align-items: center; }}
      .temporal-control button {{
        border: 1px solid #0f766e;
        background: #0f766e;
        color: #fff;
        border-radius: 6px;
        padding: 7px 10px;
        font-weight: 800;
        cursor: pointer;
      }}
      .temporal-control input {{ width: 100%; accent-color: #0f766e; }}
      .temporal-control .hour {{ font-weight: 800; min-width: 92px; text-align: right; }}
      .temporal-control .stats {{ margin-top: 8px; color: #475569; font-size: 12px; line-height: 1.45; }}
      .temporal-control .bar {{ margin-top: 8px; height: 8px; background: #e2e8f0; border-radius: 999px; overflow: hidden; }}
      .temporal-control .fill {{ height: 100%; background: #0f766e; width: 0%; }}
      .temporal-legend {{
        padding: 11px 13px;
        background: rgba(255,255,255,.95);
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(15,23,42,.16);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #0f172a;
        line-height: 1.45;
        font-size: 12px;
      }}
      .temporal-legend b {{ font-size: 13px; }}
      .legend-swatch {{ display:inline-block; width:12px; height:12px; margin-right:6px; vertical-align:-2px; border:1px solid rgba(15,23,42,.25); }}
      .flow-arrow {{
        width: 0;
        height: 0;
        border-top: 5px solid transparent;
        border-bottom: 5px solid transparent;
        border-left: 11px solid #0f766e;
        opacity: .78;
        filter: drop-shadow(0 1px 1px rgba(15,23,42,.25));
        transform-origin: center center;
      }}
    </style>
    """
    custom_script = f"""
    window.addEventListener("load", () => {{
      const map = {map_name};
      const sim = {animation_json};
      const summary = {summary_json};
      const gridHalfLat = {SEJONG_GRID_LAT_STEP / 2.0:.12f};
      const gridHalfLon = {SEJONG_GRID_LON_STEP / 2.0:.12f};
      const gridLayer = L.layerGroup().addTo(map);
      const flowLayer = L.layerGroup().addTo(map);
      const shortageLayer = L.layerGroup().addTo(map);
      const initialFrameIndex = Math.max(0, sim.frames.findIndex(frame => frame.hour_label === summary.peak_shortage_hour));
      let frameIndex = initialFrameIndex;
      let timer = null;

      const TemporalControl = L.Control.extend({{
        options: {{ position: "topright" }},
        onAdd: function() {{
          const div = L.DomUtil.create("div", "temporal-control");
          div.innerHTML = `
            <div class="title">Origin-Destination Pair inventory simulation · 500m grid</div>
            <div class="row">
              <button type="button" id="temporal-play">Play</button>
              <input id="temporal-slider" type="range" min="0" max="${{Math.max(0, sim.frames.length - 1)}}" value="${{initialFrameIndex}}" step="1">
              <span class="hour" id="temporal-hour"></span>
            </div>
            <div class="bar"><div class="fill" id="temporal-fill"></div></div>
            <div class="stats" id="temporal-stats"></div>
          `;
          L.DomEvent.disableClickPropagation(div);
          L.DomEvent.disableScrollPropagation(div);
          return div;
        }}
      }});
      map.addControl(new TemporalControl());

      const LegendControl = L.Control.extend({{
        options: {{ position: "bottomleft" }},
        onAdd: function() {{
          const div = L.DomUtil.create("div", "temporal-legend");
          div.innerHTML = `
            <b>Simulation layers</b><br>
            <span class="legend-swatch" style="background:#dbeafe;"></span>500m grid inventory<br>
            <span class="legend-swatch" style="background:#fee2e2; border-color:#be123c;"></span>shortage origin<br>
            <span style="display:inline-block; width:24px; height:3px; background:#0f766e; margin-right:6px; vertical-align:3px;"></span>served Origin-Destination Pair movement<br>
            <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#2563eb; margin-right:6px;"></span>destination inflow
          `;
          return div;
        }}
      }});
      map.addControl(new LegendControl());

      function colorForZone(zone) {{
        if (zone.unmet > 0) return "#fee2e2";
        if (zone.demand > 0) return "#ccfbf1";
        if (zone.inventory >= 30) return "#bfdbfe";
        if (zone.inventory >= 10) return "#dbeafe";
        return "#eff6ff";
      }}

      function zoneBounds(zone) {{
        return [
          [zone.latitude - gridHalfLat, zone.longitude - gridHalfLon],
          [zone.latitude + gridHalfLat, zone.longitude + gridHalfLon],
        ];
      }}

      function flowWeight(flow, maxServed) {{
        return Math.max(2, Math.min(10, 1.4 + 7 * Math.sqrt((flow.served || 0) / Math.max(1, maxServed))));
      }}

      function bearingDegrees(from, to) {{
        const lat1 = from[0] * Math.PI / 180;
        const lat2 = to[0] * Math.PI / 180;
        const deltaLon = (to[1] - from[1]) * Math.PI / 180;
        const y = Math.sin(deltaLon) * Math.cos(lat2);
        const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
        return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
      }}

      function fitFrame(frame) {{
        const shortagePoints = frame.zones
          .filter(zone => zone.unmet > 0)
          .map(zone => [zone.latitude, zone.longitude]);
        const demandPoints = frame.zones
          .filter(zone => zone.demand > 0)
          .map(zone => [zone.latitude, zone.longitude]);
        const allPoints = [];
        frame.zones.forEach(zone => allPoints.push([zone.latitude, zone.longitude]));
        frame.flows.forEach(flow => {{
          allPoints.push([flow.origin_latitude, flow.origin_longitude]);
          allPoints.push([flow.dest_latitude, flow.dest_longitude]);
        }});
        const points = shortagePoints.length ? shortagePoints : (demandPoints.length ? demandPoints : allPoints);
        if (points.length) {{
          if (points.length === 1) {{
            map.setView(points[0], 14);
          }} else {{
            map.fitBounds(points, {{ padding: [110, 110], maxZoom: 14 }});
          }}
        }}
      }}

      function drawFrame(index) {{
        frameIndex = Math.max(0, Math.min(sim.frames.length - 1, index));
        const frame = sim.frames[frameIndex];
        gridLayer.clearLayers();
        flowLayer.clearLayers();
        shortageLayer.clearLayers();

        const maxInventory = Math.max(1, ...frame.zones.map(z => z.inventory || 0));
        frame.zones.forEach(zone => {{
          const hasShortage = zone.unmet > 0;
          const opacity = hasShortage ? 0.76 : Math.max(0.18, Math.min(0.58, 0.18 + 0.42 * (zone.inventory || 0) / maxInventory));
          const rect = L.rectangle(zoneBounds(zone), {{
            color: hasShortage ? "#be123c" : "#2563eb",
            weight: hasShortage ? 2.6 : 1,
            fillColor: colorForZone(zone),
            fillOpacity: opacity,
          }}).bindTooltip(
            `<b>${{zone.zone_id}}</b><br>inventory=${{zone.inventory}}<br>demand=${{zone.demand}}<br>served=${{zone.served}}<br>unmet=${{zone.unmet}}`,
            {{ sticky: true }}
          );
          rect.addTo(gridLayer);
          if (hasShortage) {{
            L.circleMarker([zone.latitude, zone.longitude], {{
              radius: Math.max(7, Math.min(18, 7 + Math.sqrt(zone.unmet) * 3.2)),
              color: "#be123c",
              weight: 3,
              fillColor: "#fff7ed",
              fillOpacity: 0.72,
            }}).bindTooltip(`<b>Shortage</b><br>${{zone.zone_id}}<br>unmet=${{zone.unmet}}`, {{ sticky: true }}).addTo(shortageLayer);
          }}
        }});

        const maxServed = Math.max(1, ...frame.flows.map(f => f.served || 0));
        frame.flows.forEach(flow => {{
          if (!flow.served) return;
          const from = [flow.origin_latitude, flow.origin_longitude];
          const to = [flow.dest_latitude, flow.dest_longitude];
          const samePlace = Math.abs(from[0] - to[0]) + Math.abs(from[1] - to[1]) < 0.0004;
          const popup = `<b>${{flow.origin}} → ${{flow.destination}}</b><br>demand=${{flow.demand}}<br>served=${{flow.served}}<br>unmet=${{flow.unmet}}`;
          if (samePlace) {{
            L.circle(from, {{
              radius: 165 + flowWeight(flow, maxServed) * 18,
              color: "#0f766e",
              weight: flowWeight(flow, maxServed),
              fill: false,
              opacity: 0.62,
            }}).bindTooltip(popup, {{ sticky: true }}).addTo(flowLayer);
          }} else {{
            L.polyline([from, to], {{
              color: "#0f766e",
              weight: flowWeight(flow, maxServed),
              opacity: 0.66,
            }}).bindTooltip(popup, {{ sticky: true }}).addTo(flowLayer);
            const mid = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
            L.marker(mid, {{
              interactive: false,
              icon: L.divIcon({{
                className: "",
                html: `<div class="flow-arrow" style="transform: rotate(${{bearingDegrees(from, to)}}deg);"></div>`,
                iconSize: [16, 16],
                iconAnchor: [8, 8],
              }}),
            }}).addTo(flowLayer);
            L.circleMarker(mid, {{
              radius: Math.max(3, Math.min(7, flowWeight(flow, maxServed))),
              color: "#0f766e",
              weight: 1,
              fillColor: "#0f766e",
              fillOpacity: 0.72,
            }}).addTo(flowLayer);
          }}
          L.circleMarker(to, {{
            radius: Math.max(3, Math.min(10, 2 + Math.sqrt(flow.served))),
            color: "#1d4ed8",
            weight: 1.2,
            fillColor: "#93c5fd",
            fillOpacity: 0.86,
          }}).bindTooltip(`Destination inflow<br>${{flow.destination}}<br>served=${{flow.served}}`, {{ sticky: true }}).addTo(flowLayer);
        }});

        const hourEl = document.getElementById("temporal-hour");
        const statsEl = document.getElementById("temporal-stats");
        const sliderEl = document.getElementById("temporal-slider");
        const fillEl = document.getElementById("temporal-fill");
        if (hourEl) hourEl.textContent = frame.hour_label;
        if (sliderEl) sliderEl.value = String(frameIndex);
        if (fillEl) fillEl.style.width = `${{(frameIndex + 1) / sim.frames.length * 100}}%`;
        if (statsEl) {{
          statsEl.innerHTML = `
            demand <b>${{frame.demand.toLocaleString()}}</b> ·
            served <b>${{frame.served.toLocaleString()}}</b> ·
            unmet <b>${{frame.unmet.toLocaleString()}}</b> ·
            service <b>${{(frame.service_rate * 100).toFixed(1)}}%</b><br>
            day total demand <b>${{(summary.simulated_demand || 0).toLocaleString()}}</b> ·
            unmet <b>${{(summary.unmet_rides || 0).toLocaleString()}}</b>
          `;
        }}
      }}

      setTimeout(() => {{
        const slider = document.getElementById("temporal-slider");
        const play = document.getElementById("temporal-play");
        if (slider) slider.addEventListener("input", event => drawFrame(Number(event.target.value)));
        if (play) play.addEventListener("click", () => {{
          if (timer) {{
            clearInterval(timer);
            timer = null;
            play.textContent = "Play";
            return;
          }}
          play.textContent = "Pause";
          timer = setInterval(() => drawFrame((frameIndex + 1) % sim.frames.length), 900);
        }});
        if (sim.bounds) {{
          map.fitBounds([
            [sim.bounds.min_latitude, sim.bounds.min_longitude],
            [sim.bounds.max_latitude, sim.bounds.max_longitude],
          ], {{ padding: [20, 20] }});
        }}
        drawFrame(initialFrameIndex);
        if (sim.frames[initialFrameIndex]) fitFrame(sim.frames[initialFrameIndex]);
      }}, 0);
    }});
    """
    m.get_root().header.add_child(folium.Element(custom_style))
    m.get_root().script.add_child(folium.Element(custom_script))
    MiniMap(toggle_display=True).add_to(m)
    Fullscreen(position="topright").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(out_path))
    inject_hermes_widget(out_path)


def temporal_simulation_panel(temporal: dict[str, Any], map_href: str) -> str:
    if not temporal.get("ok"):
        notes = temporal.get("summary", {}).get("notes", ["temporal simulation을 만들 수 없습니다."])
        return f"""
          <h2>Origin-Destination Pair 기반 하루 재고 Simulation</h2>
          <p>{safe('; '.join(str(note) for note in notes))}</p>
        """

    summary = temporal["summary"]
    method = temporal["method"]
    shortage_rows = temporal.get("top_shortages", [])
    hourly_rows = temporal.get("hourly_summary", [])
    return f"""
      <h2>Origin-Destination Pair 기반 하루 재고 Simulation: P* 사후 검증</h2>
      <p>
        이 Simulation은 최적화 결과 <code>P*=x*</code>를 GCOO의 04:00 초기 배치로 고정한 뒤,
        GBIKE와 ALPACA의 clean Origin-Destination Pair 이동 빈도를 함께 사용해 1시간 간격으로 PM 재고가 어떻게 흘러가는지 검증합니다.
        목적은 기존 static model이 하루 중 PM의 누적 이동과 특정 zone의 공급 부족을 얼마나 놓치는지 측정하는 것입니다.
      </p>
      <div class="equation" style="margin-top:12px;">
        <b>시간대별 Origin-Destination Pair 수요 생성 방식</b>
        <div class="math">\\[\\log(1+y_{{d,h,o,r}})=\\alpha+\\gamma_h+\\delta_{{o,r}}+\\epsilon\\]</div>
        <div class="equation-note">
          <strong>회귀:</strong> 관측된 clean ride segment를 04:00 기준 operating day, 시간대 <code>h</code>, Origin-Destination Pair <code>o→r</code>로 집계한 뒤,
          <code>{safe(method["regression_formula"])}</code>를 <code>{safe(method.get("regression_solver", "fixed-effect regression"))}</code> 방식으로 추정했습니다.
          관측 Origin-Destination Pair rate 총량은 validation 기준인 optimization 기대 ride <code>sum_i Q_i(x_i*)</code>와 맞도록
          factor <code>{fmt_float(method.get("demand_calibration_factor", 1.0), 3)}</code>을 곱해 보정했습니다.
          <br><strong>Random value:</strong> 시간 공통 shock와 origin별 shock를 섞은 correlated standard normal <code>Z</code>를 만들고,
          <code>λ̂·exp(σZ-σ²/2)</code>를 Poisson rate로 사용했습니다. 이는 Excel의 <code>NORM.INV</code>로 상관된 분위수를 normal shock로 바꾸는 원리와 같습니다.
          <br><strong>초기 재고:</strong> GCOO는 <code>P*=x*</code> {fmt_int(summary["initial_gcoo_p_star"])}대, ALPACA는 latest snapshot {fmt_int(summary["initial_alpaca_latest"])}대를 사용해 총 {fmt_int(summary["initial_total_inventory"])}대로 시작합니다.
        </div>
      </div>
      <div class="grid three" style="margin-top:14px;">
        <div class="metric"><div class="label">Target Q(x*) rides</div><div class="value">{fmt_float(summary.get("target_expected_rides", 0.0), 1)}</div></div>
        <div class="metric"><div class="label">Simulated combined demand</div><div class="value">{fmt_int(summary["simulated_demand"])}</div></div>
        <div class="metric"><div class="label">Demand gap</div><div class="value">{fmt_float(summary.get("demand_gap_rate_vs_target", 0.0) * 100.0, 1)}%</div></div>
        <div class="metric"><div class="label">Served rides</div><div class="value">{fmt_int(summary["served_rides"])}</div></div>
        <div class="metric"><div class="label">Unmet rides</div><div class="value">{fmt_int(summary["unmet_rides"])}</div></div>
        <div class="metric"><div class="label">Service rate</div><div class="value">{fmt_float(100.0 * summary["service_rate"], 1)}%</div></div>
        <div class="metric"><div class="label">Modeled Origin-Destination Pairs</div><div class="value">{fmt_int(method.get("modeled_od_pairs", 0))}</div></div>
        <div class="metric"><div class="label">Calibration factor</div><div class="value">{fmt_float(method.get("demand_calibration_factor", 1.0), 2)}</div></div>
        <div class="metric"><div class="label">Shortage zones</div><div class="value">{fmt_int(summary["shortage_zone_count"])}</div></div>
        <div class="metric"><div class="label">Peak shortage hour</div><div class="value">{safe(summary["peak_shortage_hour"])}</div></div>
      </div>
      <iframe class="temporal-map-frame" src="{safe(map_href)}" title="Origin-Destination Pair based temporal inventory simulation map"></iframe>
      <div class="grid two" style="margin-top:16px;">
        <div>
          <h3>시간대별 처리율</h3>
          {hourly_inventory_table(hourly_rows)}
        </div>
        <div>
          <h3>PM 부족 기록</h3>
          <p class="note">전체 shortage log는 <a href="./temporal_inventory_shortages.csv">temporal_inventory_shortages.csv</a>에, 전체 simulated Origin-Destination Pair movement log는 <a href="./temporal_inventory_od_movements.csv">temporal_inventory_od_movements.csv</a>에 저장됩니다.</p>
          {shortage_table(shortage_rows)}
        </div>
      </div>
      <p class="note">
        해석: unmet rides가 0에 가까우면 현재 <code>P*</code>가 Origin-Destination Pair 이동을 고려해도 combined market demand를 잘 흡수한다는 뜻입니다.
        반대로 특정 시간대/zone에서 shortage가 반복되면, static objective에는 보이지 않았던 시간대별 공급 부족이 남아 있다는 신호입니다.
      </p>
    """


def decision_variable_table() -> str:
    rows = [
        (r"\(x_i\)", r"\(x_i \in \mathbb{Z}_{+}\) 또는 \(x_i \ge 0\)", "04:00에 zone i에 배치할 GBIKE PM 수"),
        (r"\(Q_{is}\)", r"\(Q_{is} \ge 0\)", "scenario s에서 zone i가 처리하는 기대 ride 수"),
        (r"\(r_i(x_i)\)", r"\(r_i(x_i) \ge 0\)", "zone i에서 발생하는 기대 rebalancing cost"),
    ]
    body = "".join(f'<tr><td class="math-cell">{a}</td><td class="math-cell">{b}</td><td>{safe(c)}</td></tr>' for a, b, c in rows)
    return f"<table><thead><tr><th>Variable</th><th>Domain</th><th>의미</th></tr></thead><tbody>{body}</tbody></table>"


def constraints_table(fleet_size: int) -> str:
    rows = [
        (
            "Fleet",
            rf"\(\sum_i x_i = {fleet_size}\)",
            f"이번 dashboard run에서는 {fmt_int(fleet_size)}대를 반드시 배치하는 planning problem으로 둠",
        ),
        ("Capacity", r"\(0 \le x_i \le K_i\)", "각 500m zone의 물리적/운영적 수용량"),
        ("Demand capture", r"\(Q_{is} \le A_{is}\left(1-e^{-\frac{\beta x_i}{1+\theta C_{is}}}\right)\)", "배치량 증가의 체감효과와 경쟁 압력"),
        ("Device throughput", r"\(Q_{is} \le Ux_i\)", "PM 1대가 하루 처리할 수 있는 최대 ride 수"),
        ("Non-negativity", r"\(x_i,\; Q_{is},\; r_i(x_i) \ge 0\)", "음수 배치나 음수 수요를 방지"),
    ]
    body = "".join(f'<tr><td>{safe(a)}</td><td class="math-cell">{b}</td><td>{safe(c)}</td></tr>' for a, b, c in rows)
    return f"<table><thead><tr><th>Constraint</th><th>식</th><th>이유</th></tr></thead><tbody>{body}</tbody></table>"


def static_parameter_table(fleet_size: int) -> str:
    rows = [
        (r"\(F\)", fmt_int(fleet_size), "이번 run에서 배치할 전체 GBIKE PM 수"),
        (r"\(\lambda\)", fmt_float(LAMBDA_MARKET, 2), "경쟁사 존재를 market validation으로 반영하는 강도"),
        (r"\(\beta\)", fmt_float(BETA_CAPTURE, 2), "GBIKE 배치량이 수요 capture로 전환되는 속도"),
        (r"\(\theta\)", fmt_float(THETA_COMPETITION, 2), "ALPACA 공급량이 GBIKE capture를 약화시키는 정도"),
        (r"\(U\)", fmt_float(U_MAX_RIDES, 1), "PM 1대가 하루 처리 가능한 최대 ride 수"),
        (r"\(p_i\)", f"{fmt_int(REVENUE_PER_RIDE_KRW)} KRW", "현재 dashboard에서는 zone 공통 ride 1건 평균 매출로 둠"),
        (r"\(v\)", f"{fmt_int(VARIABLE_COST_KRW)} KRW", "ride 1건당 변동비"),
        (r"\(c_i\)", f"{fmt_int(FIXED_COST_PER_DEVICE_KRW)} KRW/day", "PM 1대당 일 운영비"),
        (r"\(\rho\)", f"{fmt_int(REBALANCING_KRW_PER_KM)} KRW/km", "재배치 거리 1km당 비용"),
        (r"\(\kappa\)", fmt_float(CAPACITY_MULTIPLIER, 2), r"\(K_i\) 계산에 쓰는 zone capacity multiplier"),
    ]
    body = "".join(
        f'<tr><td class="math-cell">{symbol}</td><td>{safe(value)}</td><td>{safe(note)}</td></tr>'
        for symbol, value, note in rows
    )
    return f"<table><thead><tr><th>Parameter</th><th>현재 값</th><th>의미</th></tr></thead><tbody>{body}</tbody></table>"


def data_parameter_table() -> str:
    rows = [
        (
            r"\(D_i\)",
            "clean inferred ride origin count",
            "운영자 이동 의심 flag가 없는 GBIKE 이동 segment만 사용한 zone별 기본 수요",
        ),
        (r"\(C_i\)", "latest ALPACA supply", "zone별 ALPACA 경쟁 공급량"),
        (r"\(K_i\)", r"\(\lceil \kappa \cdot \text{current PM supply}_i \rceil\)", "zone별 최대 배치 가능량"),
        (r"\(r_i(x_i)\)", "clean Origin-Destination Pair flow 기반", "운영자 이동 의심 segment를 제외한 Origin-Destination Pair로 추정한 회수/재배치 비용"),
    ]
    body = "".join(
        f'<tr><td class="math-cell">{symbol}</td><td>{value}</td><td>{safe(note)}</td></tr>'
        for symbol, value, note in rows
    )
    return f"<table><thead><tr><th>Parameter</th><th>계산 방식</th><th>의미</th></tr></thead><tbody>{body}</tbody></table>"


def result_table(rows: pd.DataFrame) -> str:
    columns = [
        ("zone_id", "Zone"),
        ("x_star", "x*"),
        ("Q_i_xstar", "Q(x*)"),
        ("gbike_current", "현재 GBIKE"),
        ("alpaca_competitor", "ALPACA"),
        ("A_i", "Aᵢ"),
        ("profit_i_krw", "Profit"),
        ("utilization_rides_per_device", "Rides/device"),
    ]
    body = []
    for row in rows[rows["x_star"] > 0].head(24).itertuples(index=False):
        cells = []
        for key, _label in columns:
            value = getattr(row, key)
            if key == "zone_id":
                text = safe(value)
            elif key in {"Q_i_xstar", "A_i", "utilization_rides_per_device"}:
                text = fmt_float(value, 2)
            else:
                text = fmt_int(value)
            cells.append(f"<td>{text}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    header = "".join(f"<th>{safe(label)}</th>" for _key, label in columns)
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_model_map(rows: pd.DataFrame, out_path: Path) -> None:
    center = [
        float(rows["latitude"].median()) if not rows.empty else 36.4801,
        float(rows["longitude"].median()) if not rows.empty else 127.2890,
    ]
    m = folium.Map(location=center, zoom_start=12, tiles=None)
    folium.TileLayer("CartoDB positron", name="CartoDB positron").add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    active = rows[rows["x_star"] > 0].copy()
    max_x = max(float(active["x_star"].max()) if not active.empty else 1.0, 1.0)
    max_profit = max(float(active["profit_i_krw"].abs().max()) if not active.empty else 1.0, 1.0)
    for row in active.head(220).itertuples(index=False):
        radius = max(5.0, min(28.0, 5.0 + 22.0 * math.sqrt(float(row.x_star) / max_x)))
        color = "#0f766e" if float(row.profit_i_krw) >= 0 else "#b45309"
        opacity = max(0.35, min(0.9, 0.35 + 0.55 * abs(float(row.profit_i_krw)) / max_profit))
        popup = (
            f"<b>{safe(row.zone_id)}</b><br>"
            f"x*={fmt_int(row.x_star)}<br>"
            f"Q(x*)={fmt_float(row.Q_i_xstar, 2)} rides<br>"
            f"profit={fmt_int(row.profit_i_krw)} KRW<br>"
            f"A_i={fmt_float(row.A_i, 2)}<br>"
            f"ALPACA={fmt_int(row.alpaca_competitor)}<br>"
            f"K_i={fmt_int(row.K_i)}"
        )
        folium.CircleMarker(
            location=[float(row.latitude), float(row.longitude)],
            radius=radius,
            color=color,
            weight=1.4,
            fill=True,
            fill_color=color,
            fill_opacity=opacity,
            popup=folium.Popup(popup, max_width=340),
            tooltip=f"{row.zone_id}: x*={row.x_star}",
        ).add_to(m)

    legend = """
    <div style="position: fixed; left: 18px; bottom: 24px; z-index: 9999; width: 306px;
      padding: 12px 14px; background: rgba(255,255,255,.94); border: 1px solid #cbd5e1;
      border-radius: 8px; box-shadow: 0 8px 24px rgba(15,23,42,.16);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #0f172a;">
      <b>Optimized allocation x*</b><br>
      <span style="font-size:12px;">원 크기 = 배치량 x*<br>
      초록 = zone profit ≥ 0<br>
      갈색 = zone profit < 0<br>
      클릭하면 constraint와 objective 계산값을 확인할 수 있습니다.</span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    MiniMap(toggle_display=True).add_to(m)
    Fullscreen(position="topright").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(out_path))
    inject_hermes_widget(out_path)


def render_html(
    rows: pd.DataFrame,
    meta: dict[str, Any],
    solution: dict[str, Any],
    temporal: dict[str, Any],
    temporal_map_href: str,
    out_path: Path,
) -> None:
    active_rows = rows[rows["x_star"] > 0]
    best_zone = str(active_rows.iloc[0]["zone_id"]) if not active_rows.empty else "n/a"
    raw_ride_segments = int(meta.get("raw_ride_segments", meta.get("ride_segments", 0)))
    clean_ride_segments = int(meta.get("ride_segments", 0))
    excluded_operator_moves = int(meta.get("excluded_operator_move_segments", 0))
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sejong Optimization Model</title>
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] }},
      svg: {{ fontCache: 'global' }}
    }};
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
  <style>
    :root {{
      --ink: #172033;
      --muted: #5d687a;
      --line: #d9e0ea;
      --panel: #ffffff;
      --bg: #f4f6f8;
      --green: #0f766e;
      --blue: #2563eb;
      --amber: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ background: #ffffff; border-bottom: 1px solid var(--line); }}
    .hero {{ max-width: 1240px; margin: 0 auto; padding: 28px 20px 24px; display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, .6fr); gap: 20px; align-items: end; }}
    h1 {{ margin: 0 0 10px; font-size: 30px; line-height: 1.18; }}
    h2, h3 {{ margin: 0 0 12px; }}
    p {{ color: var(--muted); line-height: 1.62; margin: 0; }}
    a {{ color: var(--green); font-weight: 700; text-decoration: none; }}
    .navlink {{ justify-self: end; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #fff; white-space: nowrap; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 22px 20px 48px; }}
    .grid {{ display: grid; gap: 16px; }}
    .two {{ grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }}
    .three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 8px 24px rgba(23, 32, 51, .05); }}
    .result {{ border-left: 5px solid var(--green); }}
    .metric {{ border-top: 1px solid var(--line); padding-top: 12px; }}
    .metric .label {{ color: var(--muted); font-size: 13px; }}
    .metric .value {{ font-size: 28px; font-weight: 800; margin-top: 4px; }}
    .formula-stack {{ display: grid; gap: 10px; margin-top: 12px; }}
    .equation {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }}
    .equation b {{ display: block; margin-bottom: 6px; color: var(--green); }}
    .equation .math {{ overflow-x: auto; font-size: 16px; }}
    .equation-note {{ margin-top: 8px; color: var(--muted); line-height: 1.55; font-size: 13px; }}
    .equation-note strong {{ color: var(--ink); }}
    .explain {{ margin-top: 14px; display: grid; gap: 10px; }}
    .explain-item {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px 13px; background: #f8fafc; }}
    .explain-item b {{ display: block; margin-bottom: 4px; }}
    .explain-item span {{ color: var(--muted); line-height: 1.55; }}
    .term-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }}
    .term {{ border-left: 3px solid var(--green); padding: 7px 9px; background: #f8fafc; font-size: 13px; line-height: 1.45; }}
    .term code {{ font-weight: 800; }}
    .simulation-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }}
    .sim-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 11px 12px; background: #f8fafc; }}
    .sim-card b {{ display: block; margin-bottom: 5px; color: var(--ink); }}
    .sim-card span {{ display: block; color: var(--muted); font-size: 13px; line-height: 1.55; }}
    .sim-table {{ margin-top: 12px; max-height: none; }}
    .temporal-map-frame {{ width: 100%; height: 720px; border: 1px solid var(--line); border-radius: 8px; background: #fff; margin-top: 16px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: right; vertical-align: top; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; background: #f8fafc; position: sticky; top: 0; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .compact-table th, .compact-table td {{ padding: 8px 7px; }}
    .math-cell {{ min-width: 150px; }}
    .table-wrap {{ max-height: 520px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .viz-svg {{ width: 100%; height: auto; display: block; }}
    .svg-title {{ font: 700 18px system-ui, sans-serif; fill: #172033; }}
    .svg-label {{ font: 500 12px system-ui, sans-serif; fill: #5d687a; }}
    .svg-value {{ font: 700 12px system-ui, sans-serif; fill: #172033; }}
    iframe {{ width: 100%; height: 620px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .note {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
    @media (max-width: 920px) {{ .hero, .two, .three, .simulation-grid {{ grid-template-columns: 1fr; }} .navlink {{ justify-self: start; }} iframe {{ height: 500px; }} .temporal-map-frame {{ height: 620px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="hero">
      <div>
        <h1>Sejong GBIKE 04:00 Deployment Optimization</h1>
      </div>
      <a class="navlink" href="./index.html">Visualization index</a>
    </div>
  </header>
  <main>
    <section class="grid two">
      <div class="card">
        <h2>1. Solver에 넣는 Model</h2>
        <div class="formula-stack">
          <div class="equation">
            <b>의사결정변수</b>
            <div class="math">\\[x_i = \\text{{04:00에 zone }} i \\text{{에 배치할 GBIKE PM 수}}\\]</div>
            <div class="equation-note">
              <strong>해석:</strong> Solver가 직접 고르는 값입니다. 세종시를 500m grid zone으로 나눈 뒤, 각 zone <code>i</code>에 GBIKE PM을 몇 대 놓을지 결정합니다.
              <br><strong>예:</strong> <code>x_i=20</code>이면 04:00에 해당 zone에 GBIKE PM 20대를 배치한다는 뜻입니다.
            </div>
          </div>
          <div class="equation">
            <b>목적함수: 기대 profit 최대화</b>
            <div class="math">\\[\\max_x \\sum_i \\left[(p_i-v)Q_i(x_i)-c_i x_i-r_i(x_i)\\right]\\]</div>
            <div class="equation-note">
              <strong><code>(p_i-v)</code>:</strong> ride 1건당 순수 운행마진입니다. <code>p_i</code>는 zone <code>i</code>에서 ride 1건이 만드는 평균 매출이고, <code>v</code>는 결제/정비/소모품 등 ride 1건이 발생할 때 같이 증가하는 변동비입니다.
              <br><strong><code>(p_i-v)Q_i(x_i)</code>:</strong> zone <code>i</code>에서 기대되는 총 운행이익입니다. 배치량 <code>x_i</code>가 커질수록 기대 ride 수 <code>Q_i(x_i)</code>가 늘어날 수 있으므로 이 항도 커질 수 있습니다.
              <br><strong><code>-c_i x_i</code>:</strong> PM을 배치해두는 데 드는 일 운영비입니다. ride가 발생하지 않아도 PM을 현장에 두면 충전 관리, 보험/감가, 현장 관리, 민원 대응 같은 비용이 생기므로 배치 대수 <code>x_i</code>에 비례해 차감합니다.
              <br><strong><code>-r_i(x_i)</code>:</strong> 이용 후 흩어진 PM을 다음 운영 시작 전에 다시 회수하거나 재배치하는 기대 비용입니다. Origin-Destination Pair flow가 불균형한 zone일수록 이 비용이 커질 수 있습니다.
            </div>
          </div>
          <div class="equation">
            <b>비선형 수요함수</b>
            <div class="math">\\[Q_i(x_i)=\\min\\left\\{{A_i\\left(1-e^{{-\\frac{{\\beta x_i}}{{1+\\theta C_i}}}}\\right),\\;Ux_i\\right\\}}\\]</div>
            <div class="equation-note">
              <strong>모델링 근거:</strong> PM 배치량이 늘면 사용자가 가까운 기기를 발견할 확률이 커지지만, 그 효과는 포화됩니다. 그래서 접근성 효과를 <code>1-exp(-...)</code>로 둡니다. 이 함수는 처음에는 빠르게 증가하고, 이후에는 완만해지는 concave 형태입니다.
              <br><strong>경쟁 반영:</strong> <code>C_i</code>는 ALPACA 공급량입니다. 경쟁 PM이 많을수록 같은 GBIKE 배치량 <code>x_i</code>의 수요 capture 효과가 약해지므로 분모에 <code>1+θC_i</code>를 넣었습니다.
              <br><strong>운영 한계:</strong> 아무리 수요가 많아도 PM 1대가 하루 처리할 수 있는 ride 수는 제한되므로 <code>Ux_i</code>를 상한으로 둡니다.
            </div>
          </div>
          <div class="equation">
            <b>보정된 잠재수요</b>
            <div class="math">\\[A_i=D_i\\left(1+\\lambda\\frac{{\\log(1+C_i)}}{{\\log(1+C_{{\\max}})}}\\right)\\]</div>
            <div class="equation-note">
              <strong>모델링 근거:</strong> <code>D_i</code>는 GBIKE device movement에서 추정한 기본 수요입니다. 하지만 경쟁사 PM이 많이 놓인 지역은 단순히 경쟁이 심한 곳일 뿐 아니라, PM 시장이 실제로 존재한다고 검증된 지역일 수도 있습니다.
              <br><strong>운영자 이동 제외:</strong> <code>D_i</code>와 Origin-Destination Pair flow는 운영자가 차량으로 이동시킨 것으로 의심되는 segment를 제외한 clean movement만 사용합니다.
              <br><strong>왜 log인가:</strong> 경쟁사가 0대에서 10대로 늘어나는 것은 강한 시장 신호지만, 100대에서 110대로 늘어나는 것은 추가 정보가 상대적으로 작습니다. 그래서 <code>log(1+C_i)</code>를 사용해 market validation 효과도 체감하도록 설계했습니다.
              <br><strong>λ의 의미:</strong> <code>λ</code>는 경쟁사 존재를 잠재수요 증가 신호로 얼마나 강하게 볼지 정하는 parameter입니다.
            </div>
          </div>
        </div>
      </div>
      <div class="card result">
        <h2>2. 변수와 제약조건 한눈에 보기</h2>
        <h3>Decision Variables</h3>
        <div class="table-wrap compact-table" style="max-height:none;">{decision_variable_table()}</div>
        <h3 style="margin-top:16px;">Constraints</h3>
        <div class="table-wrap compact-table" style="max-height:none;">{constraints_table(OPTIMIZATION_FLEET)}</div>
        <h3 style="margin-top:16px;">Static Parameters 현재 설정값</h3>
        <div class="table-wrap compact-table" style="max-height:none;">{static_parameter_table(OPTIMIZATION_FLEET)}</div>
        <h3 style="margin-top:16px;">Data-derived Parameters 데이터에서 계산되는 값</h3>
        <div class="table-wrap compact-table" style="max-height:none;">{data_parameter_table()}</div>
        <div class="equation-note" style="margin-top:10px;">
          <strong>운영자 이동 제외 Rule:</strong> 속도 &gt; 28km/h, 또는 속도 &gt; 25km/h이면서 30분 내 반복 고속 이동, 같은 시간/Origin-Destination Pair의 2대 이상 군집, 배터리 변화량 절댓값 20pp 이상인 segment는 <code>excluded_from_demand=true</code>로 두고 수요/Origin-Destination Pair 계산에서 제외합니다.
          이번 run에서는 raw ride segment {fmt_int(raw_ride_segments)}건 중 {fmt_int(excluded_operator_moves)}건을 제외하고 {fmt_int(clean_ride_segments)}건을 사용했습니다.
        </div>
      </div>
    </section>

    <section class="card result" style="margin-top:16px;">
      <h2>3. 이번 Run의 최종 결과</h2>
      <p style="margin-bottom:14px;">위 모델에 <code>Σᵢxᵢ = {OPTIMIZATION_FLEET}</code>라는 fleet constraint를 걸고 계산한 배치 결과입니다. 최상위 배치 zone은 <code>{safe(best_zone)}</code>입니다.</p>
      <div class="grid three">
        <div class="metric"><div class="label">배치 fleet</div><div class="value">{fmt_int(solution['allocated_devices'])}</div></div>
        <div class="metric"><div class="label">활성 zone</div><div class="value">{fmt_int(solution['active_zones'])}</div></div>
        <div class="metric"><div class="label">기대 rides</div><div class="value">{fmt_float(solution['expected_rides'], 1)}</div></div>
        <div class="metric"><div class="label">기대 revenue 운행매출</div><div class="value">{fmt_int(solution['expected_revenue_krw'])}</div></div>
        <div class="metric"><div class="label">기대 total cost</div><div class="value">{fmt_int(solution['expected_variable_cost_krw'] + solution['expected_fixed_cost_krw'] + solution['expected_rebalancing_cost_krw'])}</div></div>
        <div class="metric"><div class="label">Objective value</div><div class="value">{fmt_int(solution['expected_profit_krw'])}</div></div>
      </div>
    </section>

    <section class="grid two" style="margin-top:16px;">
      <div class="card">{svg_allocation_bar(rows)}</div>
      <div class="card">{svg_cost_revenue(rows)}</div>
    </section>

    <section class="card" style="margin-top:16px;">
      <h2>최적 배치 지도: x*</h2>
      <p>지도는 Solver-style output인 <code>x*</code>를 zone별로 보여줍니다. 원 크기는 배치량, 색상은 zone별 profit 부호입니다.</p>
      <iframe src="./optimization_model_map.html" title="Optimization result map"></iframe>
    </section>

    <section class="grid two" style="margin-top:16px;">
      <div class="card">{capture_curve_panel()}</div>
      <div class="card">{simulation_panel(rows)}</div>
    </section>

    <section class="card" style="margin-top:16px;">
      <h2>Solution Table 최종 결과표</h2>
      <p>아래 표는 최종 배치량 <code>x*</code>, 해당 배치에서의 기대 ride <code>Q(x*)</code>, 경쟁 공급 <code>Cᵢ</code>, capacity <code>Kᵢ</code>, zone별 objective contribution을 함께 보여줍니다.</p>
      <div class="table-wrap">{result_table(rows)}</div>
    </section>

    <section class="card result" style="margin-top:16px;">
      {temporal_simulation_panel(temporal, temporal_map_href)}
    </section>
  </main>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")
    inject_hermes_widget(out_path)


def mirror_html(src_path: Path, dst_path: Path) -> None:
    dst_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")


def render(processed_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir = ensure_dir(out_dir)
    model, meta = build_zone_model(processed_dir)
    fleet_size = OPTIMIZATION_FLEET
    rows, solution = optimize_dashboard_solution(model, fleet_size)
    temporal = build_temporal_inventory_simulation(rows, processed_dir)
    map_path = out_dir / "optimization_model_map.html"
    temporal_map_path = out_dir / "temporal_inventory_map.html"
    page_path = out_dir / "optimization_model.html"
    hermes_lab_path = out_dir / "hermes_lab.html"
    data_path = out_dir / "optimization_model_data.json"
    shortage_path = out_dir / "temporal_inventory_shortages.csv"
    hourly_path = out_dir / "temporal_inventory_hourly_summary.csv"
    movement_path = out_dir / "temporal_inventory_od_movements.csv"
    shortage_columns = [
        "hour",
        "hour_label",
        "zone_id",
        "demand",
        "served",
        "shortage",
        "available_at_hour_start",
        "inventory_after_departures",
    ]
    hourly_columns = [
        "hour",
        "hour_label",
        "demand",
        "served",
        "unmet",
        "service_rate",
        "inventory_start",
        "inventory_end",
    ]
    movement_columns = [
        "hour",
        "hour_label",
        "origin",
        "destination",
        "demand",
        "served",
        "unmet",
        "rate",
        "base_rate",
        "regression_rate",
        "empirical_rate",
        "normal_z",
        "normal_quantile",
        "origin_latitude",
        "origin_longitude",
        "dest_latitude",
        "dest_longitude",
    ]
    pd.DataFrame(temporal.get("shortages", []), columns=shortage_columns).to_csv(shortage_path, index=False)
    pd.DataFrame(temporal.get("hourly_summary", []), columns=hourly_columns).to_csv(hourly_path, index=False)
    pd.DataFrame(temporal.get("movements", []), columns=movement_columns).to_csv(movement_path, index=False)
    render_model_map(rows, map_path)
    render_temporal_inventory_map(temporal, temporal_map_path)
    render_html(rows, meta, solution, temporal, f"./{temporal_map_path.name}", page_path)
    mirror_html(page_path, hermes_lab_path)
    payload = {
        "meta": meta,
        "solution": solution,
        "temporal_inventory_simulation": {
            "summary": temporal.get("summary", {}),
            "method": temporal.get("method", {}),
            "hourly_summary": temporal.get("hourly_summary", []),
            "top_shortages": temporal.get("top_shortages", []),
            "top_movements": temporal.get("top_movements", []),
        },
        "top_allocations": rows[rows["x_star"] > 0].head(40).to_dict("records"),
        "outputs": {
            "page": str(page_path),
            "hermes_lab": str(hermes_lab_path),
            "map": str(map_path),
            "temporal_map": str(temporal_map_path),
            "data": str(data_path),
            "temporal_shortages": str(shortage_path),
            "temporal_hourly_summary": str(hourly_path),
            "temporal_od_movements": str(movement_path),
        },
    }
    write_json(data_path, payload)
    return payload


def main() -> None:
    result = render(Path("data/processed/sejong_tago"), Path("outputs/visualizations"))
    print(f"page={result['outputs']['page']}")
    print(f"map={result['outputs']['map']}")
    print(f"allocated={result['solution']['allocated_devices']}")
    print(f"active_zones={result['solution']['active_zones']}")


if __name__ == "__main__":
    main()
