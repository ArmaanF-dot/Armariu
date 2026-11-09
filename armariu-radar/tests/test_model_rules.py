from datetime import date

import pytest

from radar import drift, locked


def test_locked_window_refuses_new_months():
    locked.guard([date(2026, 10, 1)])
    with pytest.raises(SystemExit):
        locked.guard([date(2026, 10, 1), date(2026, 11, 1)])
    locked.guard([date(2026, 11, 1)], allow=True)


def months(*lifts):
    return [{"date": f"2026-{i + 1:02d}-01", "lift": l} for i, l in enumerate(lifts)]


def test_drift_flags_three_low_months_in_a_row():
    assert drift.check(months(3.0, 2.4, 2.3, 2.2))["flagged"] is True
    assert drift.check(months(3.0, 2.4, 2.9, 2.2))["flagged"] is False
    assert drift.check(months(2.4, 2.3))["flagged"] is False


def test_drift_line_without_stored_results(tmp_path):
    assert "no monthly lift stored" in drift.line(tmp_path)


def test_bands_fold_a_short_last_chunk_into_the_one_before():
    from radar.refi_watch import Model
    scored = [(float(i), i % 2) for i in range(205)]   # 20 bands of 10 plus a short chunk of 5
    bands = Model._bands(scored, 20)
    assert len(bands) == 20
    assert bands[-1][0] == 190.0


def test_lead_size_uses_only_accounts_public_and_recent():
    from radar.lead_size import add_sizes, size_for
    acc = {"AB1": [
        {"known_from": "2025-03-01", "balance_sheet_date": "2024-12-31", "loans_1y": 100000.0,
         "loans_after": 250000.0, "total_assets_less_cl": 900000.0},
        {"known_from": "2026-03-01", "balance_sheet_date": "2025-12-31", "loans_1y": 1.0,
         "loans_after": None, "total_assets_less_cl": 5.0}]}
    on = date(2025, 6, 1)   # the 2025 accounts were not public yet
    assert size_for("AB1", on, acc) == {"borrowings_gbp": 350000, "total_assets_less_cl_gbp": 900000,
                                        "accounts_date": "2024-12-31"}
    assert size_for("AB1", date(2028, 6, 1), acc)["borrowings_gbp"] == ""      # older than two years
    assert size_for("ZZ9", on, acc)["borrowings_gbp"] == ""
    rows = [{"company_number": "AB1"}, {"company_number": "ZZ9"}]
    assert add_sizes(rows, on, acc) == 0.5
