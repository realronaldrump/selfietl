import pytest

from selfietl.pipeline.compose import _filter_rows_by_date


def test_filter_rows_by_date_keeps_full_end_day():
    rows = [
        {"captured_at": "2020-01-01 23:59:59", "hash": "a"},
        {"captured_at": "2020-01-02 12:00:00", "hash": "b"},
        {"captured_at": "2020-01-03 00:00:00", "hash": "c"},
    ]

    filtered = _filter_rows_by_date(rows, "2020-01-01", "2020-01-02")

    assert [row["hash"] for row in filtered] == ["a", "b"]


def test_filter_rows_by_date_rejects_backwards_range():
    with pytest.raises(RuntimeError, match="Start date"):
        _filter_rows_by_date([], "2020-01-03", "2020-01-02")
