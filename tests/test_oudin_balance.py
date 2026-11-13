import math

import numpy as np
import pytest

from analytics.balance_insar import balance_anomaly, best_lag, lag_scan, monthly_rate
from analytics.examples.balance_insar_demo import LAG_TRUE, build_synthetic
from analytics.oudin import extraterrestrial_radiation, oudin_daily, oudin_monthly


def test_ra_matches_fao56_example_8():
    # FAO-56, ejemplo 8: 20°S, 3 de septiembre (J=246) → Ra = 32.2 MJ m-2 d-1
    assert extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.1)


def test_oudin_zero_below_minus_five_and_nan_propagates():
    pe = oudin_daily([-10.0, -5.0, 10.0, np.nan], 30.0)
    assert pe[0] == 0.0 and pe[1] == 0.0
    assert pe[2] == pytest.approx(0.408 * 30 * 15 / 100)
    assert math.isnan(pe[3])


def test_oudin_monthly_null_strict():
    assert oudin_monthly(-24.0, 2024, 1, None) is None
    assert oudin_monthly(-24.0, 2024, 1, 12.0) > oudin_monthly(-24.0, 2024, 7, 12.0)


def test_anomaly_removes_monthly_climatology():
    months = np.tile(np.arange(1, 13), 3)
    p = np.tile(np.linspace(50, 5, 12), 3) + np.repeat([0.0, 10.0, -10.0], 12)
    anom = balance_anomaly(p, np.zeros_like(p), months)
    for k in range(1, 13):
        assert anom[months == k].mean() == pytest.approx(0.0, abs=1e-9)


def test_monthly_rate_respects_gaps():
    r = monthly_rate([0.0, -1.0, np.nan, -3.0])
    assert math.isnan(r[0]) and r[1] == -1.0
    assert math.isnan(r[2]) and math.isnan(r[3])


def test_lag_scan_recovers_imposed_lag():
    _, months, precip, pet, disp = build_synthetic()
    res = lag_scan(balance_anomaly(precip, pet, months), monthly_rate(disp), max_lag=12)
    best = best_lag(res, alpha=0.05 / len(res))
    assert best is not None and best.lag_months == LAG_TRUE
    assert best.rho > 0


def test_lag_scan_none_when_independent():
    rng = np.random.default_rng(0)
    res = lag_scan(rng.normal(size=120), rng.normal(size=120), max_lag=6)
    assert best_lag(res, alpha=0.05 / len(res)) is None
