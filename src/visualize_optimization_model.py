from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

import folium
import pandas as pd
from folium.plugins import Fullscreen, MiniMap

from common import ensure_dir, write_json


LAMBDA_MARKET = 0.30
BETA_CAPTURE = 0.08
THETA_COMPETITION = 1.00
U_MAX_RIDES = 6.0
REVENUE_PER_RIDE_KRW = 2200.0
VARIABLE_COST_KRW = 300.0
FIXED_COST_PER_DEVICE_KRW = 2500.0
REBALANCING_KRW_PER_KM = 900.0
CAPACITY_MULTIPLIER = 1.25
OPTIMIZATION_FLEET = 500


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
        "ride_segments": int(len(segments)),
        "od_pairs": int(len(od_flows)),
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


def svg_simulation(rows: pd.DataFrame) -> str:
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
    min_v, max_v = min(values), max(values)
    span = max(max_v - min_v, 1.0)
    bins = 18
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - min_v) / span * bins))] += 1
    max_count = max(max(counts), 1)
    width = 900
    height = 300
    left = 54
    bottom = 250
    plot_w = 780
    plot_h = 190
    bar_gap = 4
    bar_w = (plot_w - bar_gap * (bins - 1)) / bins

    def sx(value: float) -> float:
        return left + plot_w * (value - min_v) / span

    parts = [
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img" aria-label="simulation profit distribution">',
        '<text x="0" y="24" class="svg-title">Simulation: demand/cost shock가 있을 때 Objective value 분포</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{left + plot_w}" y2="{bottom}" stroke="#94a3b8"></line>',
    ]
    for idx, count in enumerate(counts):
        x = left + idx * (bar_w + bar_gap)
        h = plot_h * count / max_count
        parts.append(f'<rect x="{x:.1f}" y="{bottom - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="3" fill="#0f766e" opacity="0.74"></rect>')
    for label, value, color in [("P10", p10, "#be123c"), ("P50", p50, "#2563eb"), ("P90", p90, "#0f766e")]:
        x = sx(value)
        parts.append(f'<line x1="{x:.1f}" y1="{bottom - plot_h}" x2="{x:.1f}" y2="{bottom}" stroke="{color}" stroke-width="2" stroke-dasharray="4 4"></line>')
        parts.append(f'<text x="{x + 5:.1f}" y="{bottom - plot_h + 18}" class="svg-label">{label}: {fmt_int(value)} KRW</text>')
    parts.append("</svg>")
    return "\n".join(parts)


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
        ("Fleet", rf"\(\sum_i x_i = {fleet_size}\)", "이번 dashboard run에서는 500대를 반드시 배치하는 planning problem으로 둠"),
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
        (r"\(D_i\)", "inferred ride origin count", "GBIKE 이동 segment에서 추정한 zone별 기본 수요"),
        (r"\(C_i\)", "latest ALPACA supply", "zone별 ALPACA 경쟁 공급량"),
        (r"\(K_i\)", r"\(\lceil \kappa \cdot \text{current PM supply}_i \rceil\)", "zone별 최대 배치 가능량"),
        (r"\(r_i(x_i)\)", "OD flow 기반", "이용 후 흩어진 PM을 회수/재배치하는 비용"),
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


def render_html(rows: pd.DataFrame, meta: dict[str, Any], solution: dict[str, Any], out_path: Path) -> None:
    active_rows = rows[rows["x_star"] > 0]
    best_zone = str(active_rows.iloc[0]["zone_id"]) if not active_rows.empty else "n/a"
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
    @media (max-width: 920px) {{ .hero, .two, .three {{ grid-template-columns: 1fr; }} .navlink {{ justify-self: start; }} iframe {{ height: 500px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="hero">
      <div>
        <h1>Sejong GBIKE 04:00 Deployment Optimization</h1>
        <p>이 페이지는 Sejong TAGO PM 데이터를 이용해 Solver에 넣을 수 있는 Optimization Model을 보여줍니다. 핵심은 decision variable <code>xᵢ</code>, objective function, constraints, 그리고 최종 배치 결과 <code>x*</code>입니다.</p>
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
              <br><strong><code>-r_i(x_i)</code>:</strong> 이용 후 흩어진 PM을 다음 운영 시작 전에 다시 회수하거나 재배치하는 기대 비용입니다. OD flow가 불균형한 zone일수록 이 비용이 커질 수 있습니다.
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
      <div class="card">{svg_capture_curve_korean()}</div>
      <div class="card">{svg_simulation(rows)}</div>
    </section>

    <section class="card" style="margin-top:16px;">
      <h2>Solution Table 최종 결과표</h2>
      <p>아래 표는 최종 배치량 <code>x*</code>, 해당 배치에서의 기대 ride <code>Q(x*)</code>, 경쟁 공급 <code>Cᵢ</code>, capacity <code>Kᵢ</code>, zone별 objective contribution을 함께 보여줍니다.</p>
      <div class="table-wrap">{result_table(rows)}</div>
    </section>
  </main>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")


def render(processed_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir = ensure_dir(out_dir)
    model, meta = build_zone_model(processed_dir)
    fleet_size = min(OPTIMIZATION_FLEET, int(meta.get("gbike_devices", OPTIMIZATION_FLEET)))
    rows, solution = optimize_dashboard_solution(model, fleet_size)
    map_path = out_dir / "optimization_model_map.html"
    page_path = out_dir / "optimization_model.html"
    data_path = out_dir / "optimization_model_data.json"
    render_model_map(rows, map_path)
    render_html(rows, meta, solution, page_path)
    payload = {
        "meta": meta,
        "solution": solution,
        "top_allocations": rows[rows["x_star"] > 0].head(40).to_dict("records"),
        "outputs": {"page": str(page_path), "map": str(map_path), "data": str(data_path)},
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
