from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


RIDE_REFERENCE_INTERVAL_MINUTES = 5.0
MOVEMENT_50M_THRESHOLD_M = 50.0
MOVEMENT_200M_THRESHOLD_M = 200.0
RIDE_DISTANCE_THRESHOLD_REFERENCE_M = 100.0
RIDE_ALLOWED_INTERVAL_MINUTES = (5.0, 10.0)
RIDE_INTERVAL_TOLERANCE_MINUTES = 1.0


def scaled_distance_threshold_m(
    interval_minutes: pd.Series,
    threshold_reference_m: float,
    reference_minutes: float,
) -> pd.Series:
    minutes = pd.to_numeric(interval_minutes, errors="coerce")
    threshold = float(threshold_reference_m) * minutes / float(reference_minutes)
    return threshold.where(minutes > 0)


def distance_meets_scaled_threshold(
    distance_m: pd.Series,
    interval_minutes: pd.Series,
    threshold_reference_m: float,
    reference_minutes: float,
) -> tuple[pd.Series, pd.Series]:
    distance = pd.to_numeric(distance_m, errors="coerce")
    threshold = scaled_distance_threshold_m(interval_minutes, threshold_reference_m, reference_minutes)
    return distance >= threshold, threshold


def within_any_interval(
    interval_minutes: pd.Series,
    target_minutes: Iterable[float],
    tolerance_minutes: float,
) -> pd.Series:
    minutes = pd.to_numeric(interval_minutes, errors="coerce")
    mask = pd.Series(False, index=minutes.index)
    for target in target_minutes:
        mask = mask | minutes.between(float(target) - tolerance_minutes, float(target) + tolerance_minutes)
    return mask


def scaled_rule_text(
    threshold_reference_m: float,
    reference_minutes: float,
    intervals: Iterable[float] = RIDE_ALLOWED_INTERVAL_MINUTES,
) -> str:
    parts = []
    for minutes in intervals:
        scaled = threshold_reference_m * float(minutes) / float(reference_minutes)
        parts.append(f"{minutes:.0f}min >= {scaled:.0f}m")
    return " or ".join(parts)
