"""Time-series data-quality cleaning for sensor-style AutoML template inputs.
Called from inside a template's preprocess() only when the template maps a
date/time column and has numeric feature columns worth cleaning - never
touches automl_engine.py, just hands it a cleaner frame.

Steps (see docs/superpowers/specs/2026-07-17-manufacturing-automl-design.md,
"Time-series data quality"): detect the typical sampling interval, flag gaps
beyond N x that interval instead of silently bridging them, optionally
resample onto a regular grid, smooth outlier spikes per numeric column via a
rolling-median (Hampel) filter, and interpolate only the short gaps -
long gaps are left as NaN for the engine's existing dropna/SimpleImputer to
handle downstream (no changes needed there).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_GAP_MULTIPLIER = 3.0
_HAMPEL_WINDOW = 5
_HAMPEL_K = 3.0
_MAD_TO_STD = 1.4826  # scales MAD to be comparable to a standard deviation for a normal distribution
_MIN_ROWS_FOR_INTERVAL = 3
_MAX_GAP_FRACTION_TO_KEEP_COLUMN = 0.9


def _hampel_smooth(s: pd.Series, window: int = _HAMPEL_WINDOW, k: float = _HAMPEL_K) -> tuple[pd.Series, int]:
    """Rolling-median outlier filter: a point is an outlier if it's more than
    k robust-standard-deviations (via MAD) from its local rolling median.
    Outliers are replaced with the local rolling median; NaNs are untouched
    here (gap interpolation is a separate step)."""
    present = s.notna()
    if present.sum() < window:
        return s, 0
    roll_med = s.rolling(window, center=True, min_periods=1).median()
    mad = (s - roll_med).abs().rolling(window, center=True, min_periods=1).median()
    threshold = k * _MAD_TO_STD * mad
    is_outlier = present & (threshold > 0) & ((s - roll_med).abs() > threshold)
    if not is_outlier.any():
        return s, 0
    return s.where(~is_outlier, roll_med).infer_objects(copy=False), int(is_outlier.sum())


def _interpolate_short_gaps(s: pd.Series, gap_threshold: pd.Timedelta) -> tuple[pd.Series, int]:
    """s must have a sorted DatetimeIndex. Linearly interpolates a run of NaN
    values only when the two real values bounding it are <= gap_threshold
    apart in time ("short gap"); a run bounded by a bigger time gap, or
    unbounded at either end (leading/trailing NaN), is left as NaN
    ("long gap" - not bridged)."""
    out = s.copy()
    valid_positions = np.where(s.notna().to_numpy())[0]
    if len(valid_positions) < 2:
        return out, 0
    idx = s.index
    n_interpolated = 0
    for a, b in zip(valid_positions[:-1], valid_positions[1:]):
        if b - a <= 1:
            continue  # no gap between these two consecutive valid points
        if idx[b] - idx[a] > gap_threshold:
            continue  # long gap - leave the NaNs between a and b untouched
        span = s.iloc[a:b + 1]
        filled = span.interpolate(method="time")
        n_interpolated += int(span.isna().sum())
        out.iloc[a:b + 1] = filled.to_numpy()
    return out, n_interpolated


def clean_timeseries(
    df: pd.DataFrame,
    time_col: str,
    numeric_cols: list[str],
    *,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
    resample: bool = True,
    max_gap_fraction: float = _MAX_GAP_FRACTION_TO_KEEP_COLUMN,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Returns (cleaned_df, data_quality_notes, excluded_cols).

    `resample=True` (default) reindexes onto a regular grid at the detected
    interval - the literal "resample/align" step from the design spec, best
    suited to genuinely periodic data (e.g. a per-day aggregate). Templates
    cleaning per-row transactional data (where reindexing would desynchronize
    each row from its own target value) can pass `resample=False`: gap
    detection, Hampel smoothing, and short-gap interpolation still run, just
    without inserting synthetic new rows for missing timestamps.
    ponytail: shift(-1)-window forecasting-style featurization on top of this
    is left to the caller - this module only cleans, it doesn't featurize.
    """
    notes: list[str] = []
    excluded_cols: list[str] = []

    work = df.copy()
    work[time_col] = pd.to_datetime(work[time_col], errors="coerce")
    n_before = len(work)
    work = work.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    if n_before - len(work):
        notes.append(f"Dropped {n_before - len(work)} row(s) with an unparseable '{time_col}' value.")

    usable_cols: list[str] = []
    for c in numeric_cols:
        if c not in work.columns:
            continue
        coerced = pd.to_numeric(work[c], errors="coerce")
        gap_fraction = float(coerced.isna().mean()) if len(coerced) else 1.0
        if coerced.notna().sum() == 0 or gap_fraction > max_gap_fraction:
            notes.append(
                f"Excluded '{c}' from time-series cleaning (non-numeric or "
                f"{gap_fraction * 100:.0f}% missing, over the {max_gap_fraction * 100:.0f}% limit)."
            )
            excluded_cols.append(c)
            continue
        work[c] = coerced
        usable_cols.append(c)

    if len(work) < _MIN_ROWS_FOR_INTERVAL or work[time_col].nunique() < _MIN_ROWS_FOR_INTERVAL:
        notes.append("Not enough distinct timestamps to detect a sampling interval - skipped gap/smoothing steps.")
        return work, notes, excluded_cols

    deltas = work[time_col].diff().dropna()
    deltas = deltas[deltas > pd.Timedelta(0)]
    if deltas.empty:
        notes.append(
            "Could not detect a regular sampling interval (duplicate/non-increasing timestamps) - "
            "skipped gap/smoothing steps."
        )
        return work, notes, excluded_cols

    typical_interval = deltas.median()
    gap_threshold = typical_interval * gap_multiplier

    long_gaps = deltas[deltas > gap_threshold]
    if len(long_gaps):
        notes.append(
            f"{len(long_gaps)} gap(s) longer than {gap_multiplier:g}x the typical sampling interval "
            f"(~{typical_interval}) were flagged and left as missing, not bridged."
        )

    if resample:
        grid = pd.date_range(work[time_col].min(), work[time_col].max(), freq=typical_interval)
        aligned = work.set_index(time_col).reindex(grid)
        aligned.index.name = time_col
    else:
        aligned = work.set_index(time_col)

    total_cells = 0
    total_missing_before = 0
    for c in usable_cols:
        s = aligned[c]
        total_cells += len(s)
        total_missing_before += int(s.isna().sum())

        smoothed, n_outliers = _hampel_smooth(s)
        if n_outliers:
            notes.append(f"Smoothed {n_outliers} outlier spike(s) in '{c}' via rolling-median (Hampel) filter.")

        interpolated, n_interp = _interpolate_short_gaps(smoothed, gap_threshold)
        if n_interp:
            notes.append(f"Interpolated {n_interp} short-gap value(s) in '{c}'.")
        aligned[c] = interpolated

    if total_cells:
        pct_missing = total_missing_before / total_cells * 100
        if pct_missing > 0:
            notes.append(f"{pct_missing:.1f}% of readings were missing across the cleaned column(s) before interpolation.")

    result = aligned.reset_index()
    return result, notes, excluded_cols


if __name__ == "__main__":
    # ponytail: smallest runnable self-check instead of a full test suite entry point.
    rng = pd.date_range("2026-01-01", periods=30, freq="1h")
    vals = [20.0 + (i % 5) for i in range(30)]
    vals[10] = 500.0  # spike
    for i in (15, 16):  # short gap (2h < 3x1h threshold)
        vals[i] = np.nan
    frame = pd.DataFrame({"t": rng, "v": vals})
    frame = frame.drop(index=range(20, 25)).reset_index(drop=True)  # long gap (5h > 3x1h threshold)
    cleaned, notes, excluded = clean_timeseries(frame, "t", ["v"])
    assert not excluded
    assert any("outlier" in n.lower() for n in notes)
    assert any("short-gap" in n.lower() for n in notes)
    assert any("longer than" in n.lower() for n in notes)
    assert abs(cleaned.loc[cleaned["t"] == rng[10], "v"].iloc[0] - 500.0) > 100  # spike smoothed away
    print("timeseries_quality self-check OK")
