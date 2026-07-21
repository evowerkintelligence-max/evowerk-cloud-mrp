from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import timeseries_quality as tsq


def _hourly_fixture(n: int = 40, spike_at: int = 12, short_gap_at=(20, 21), drop_range=(28, 33)) -> pd.DataFrame:
    """Regular hourly series with one injected spike, one short (2h) gap,
    and one long (5h) dropout - all well past the module's
    _MIN_ROWS_FOR_INTERVAL floor so interval detection is reliable."""
    rng = pd.date_range("2026-01-01", periods=n, freq="1h")
    values = [25.0 + (i % 4) * 0.5 for i in range(n)]
    values[spike_at] = 500.0  # spike, way outside local range
    for i in short_gap_at:
        values[i] = np.nan
    df = pd.DataFrame({"t": rng, "v": values, "other": [1.0] * n})
    df = df.drop(index=range(*drop_range)).reset_index(drop=True)  # long gap: rows physically absent
    return df


def test_gap_beyond_threshold_is_flagged_not_bridged():
    df = _hourly_fixture()
    cleaned, notes, excluded = tsq.clean_timeseries(df, "t", ["v"])
    assert not excluded
    assert any("longer than" in n.lower() and "not bridged" in n.lower() for n in notes)

    # The long-gap window (rows physically removed from the fixture) must
    # come back as NaN after resampling onto the regular grid, not as an
    # interpolated value spanning the whole dropout.
    dropped_start = pd.Timestamp("2026-01-01") + pd.Timedelta(hours=28)
    dropped_mid = dropped_start + pd.Timedelta(hours=2)
    row = cleaned[cleaned["t"] == dropped_mid]
    assert not row.empty
    assert pd.isna(row["v"].iloc[0])


def test_outlier_spike_is_smoothed():
    df = _hourly_fixture()
    cleaned, notes, _ = tsq.clean_timeseries(df, "t", ["v"])
    assert any("outlier" in n.lower() and "'v'" in n for n in notes)
    spike_time = pd.Timestamp("2026-01-01") + pd.Timedelta(hours=12)
    smoothed_value = cleaned.loc[cleaned["t"] == spike_time, "v"].iloc[0]
    assert smoothed_value < 100  # nowhere near the injected 500.0 spike


def test_short_gap_is_interpolated():
    df = _hourly_fixture()
    cleaned, notes, _ = tsq.clean_timeseries(df, "t", ["v"])
    assert any("interpolated" in n.lower() and "short-gap" in n.lower() for n in notes)
    gap_time = pd.Timestamp("2026-01-01") + pd.Timedelta(hours=20)
    value = cleaned.loc[cleaned["t"] == gap_time, "v"].iloc[0]
    assert not pd.isna(value)  # short (2h < 3x1h threshold) gap was filled in


def test_non_numeric_column_is_excluded_with_a_note_not_a_crash():
    df = _hourly_fixture()
    df["label"] = [("a" if i % 2 == 0 else "b") for i in range(len(df))]
    cleaned, notes, excluded = tsq.clean_timeseries(df, "t", ["v", "label"])
    assert "label" in excluded
    assert any("'label'" in n for n in notes)
    assert "v" in cleaned.columns  # the good column still gets cleaned


def test_mostly_gap_column_is_excluded_with_a_note_not_a_crash():
    df = _hourly_fixture()
    df["sparse"] = [np.nan] * len(df)
    df.loc[df.index[0], "sparse"] = 1.0  # single real value, > 90% missing
    cleaned, notes, excluded = tsq.clean_timeseries(df, "t", ["v", "sparse"])
    assert "sparse" in excluded
    assert any("'sparse'" in n and "missing" in n.lower() for n in notes)


def test_data_quality_notes_report_missing_percentage():
    df = _hourly_fixture()
    _, notes, _ = tsq.clean_timeseries(df, "t", ["v"])
    assert any("% of readings were missing" in n for n in notes)


def test_resample_false_preserves_row_count_no_synthetic_rows():
    df = _hourly_fixture()
    cleaned, notes, _ = tsq.clean_timeseries(df, "t", ["v"], resample=False)
    assert len(cleaned) == len(df)  # no new grid rows inserted
    assert any("longer than" in n.lower() for n in notes)  # gap still flagged
    assert any("outlier" in n.lower() for n in notes)  # spike still smoothed


def test_no_gaps_or_outliers_produces_clean_notes_without_false_positives():
    rng = pd.date_range("2026-01-01", periods=30, freq="1h")
    df = pd.DataFrame({"t": rng, "v": [10.0 + (i % 3) * 0.1 for i in range(30)]})
    cleaned, notes, excluded = tsq.clean_timeseries(df, "t", ["v"])
    assert not excluded
    assert not any("outlier" in n.lower() for n in notes)
    assert not any("longer than" in n.lower() for n in notes)
    assert len(cleaned) == len(df)
