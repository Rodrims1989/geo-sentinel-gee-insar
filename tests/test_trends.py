import numpy as np
import pytest

from analytics.trends import (MonthlyValue, annualize, run_methods,
                              summarize)


def _series(years, fn, drop=None):
    out = []
    for y in years:
        for m in range(1, 13):
            v = fn(y, m)
            if drop and (y, m) in drop:
                v = None
            out.append(MonthlyValue(month=f"{y}-{m:02d}", value=v))
    return out


def test_sum_requires_12_months_and_audits_exclusions():
    s = _series(range(2010, 2013), lambda y, m: 10.0, drop={(2011, 3)})
    annual, excluded = annualize(s, "sum")
    assert [a.year for a in annual] == [2010, 2012]
    assert excluded[0].year == 2011 and excluded[0].n_valid_months == 11


def test_mean_accepts_10_months():
    s = _series([2015], lambda y, m: 5.0, drop={(2015, 1), (2015, 2)})
    annual, excluded = annualize(s, "mean")
    assert len(annual) == 1 and annual[0].n_months == 10 and not excluded


def test_detects_clear_decreasing_trend():
    years = np.arange(2000, 2020, dtype=float)
    rng = np.random.default_rng(1)
    values = 100 - 2.0 * (years - 2000) + rng.normal(0, 0.5, years.size)
    results = run_methods(years, values, 0.05)
    assert all(r.significant for r in results)
    assert all(r.slope_per_year < 0 for r in results if r.slope_per_year is not None)
    assert "decreciente" in summarize("x", results, years.size)


def test_flat_series_has_no_trend():
    years = np.arange(2000, 2020, dtype=float)
    rng = np.random.default_rng(2)
    values = 50 + rng.normal(0, 1.0, years.size)
    results = run_methods(years, values, 0.05)
    assert not any(r.significant for r in results)
