import numpy as np
import pytest

from insar.velocity import alert_mask, linear_velocity, rate_of_change


def _ramp(rate, n=48, h=3, w=3):
    t = 2020.0 + np.arange(n) / 12.0
    return t, rate * (t - t[0])[:, None, None] * np.ones((n, h, w))


def test_linear_velocity_recovers_known_slope():
    t, stack = _ramp(5.0)
    v, n = linear_velocity(stack, t)
    assert np.allclose(v, 5.0, atol=1e-9)
    assert (n == 48).all()


def test_nan_tolerant_and_null_strict():
    t, stack = _ramp(-3.0)
    stack[:, 0, 0] = np.nan          # píxel sin datos
    stack[5:, 1, 1] = np.nan         # solo 5 observaciones
    v, n = linear_velocity(stack, t, min_obs=6)
    assert np.isnan(v[0, 0]) and np.isnan(v[1, 1])   # jamás inventa valores
    assert np.isclose(v[2, 2], -3.0)


def test_rate_of_change_isolates_local_signal_from_regional_uplift():
    n, h, w = 72, 20, 20
    t = 2019.0 + np.arange(n) / 12.0
    stack = 8.0 * (t - t[0])[:, None, None] * np.ones((n, h, w))
    stack[:, 8:12, 8:12] += (-15.0 * np.clip(t - 2023.0, 0, None))[:, None, None]
    ref = np.zeros((h, w), bool)
    ref[:4, :4] = True
    res = rate_of_change(stack, t, (2019.0, 2022.9), (2023.0, 2024.9), ref)
    assert np.isclose(np.nanmedian(res.delta_v[8:12, 8:12]), -15.0, atol=0.5)
    assert np.isclose(np.nanmedian(res.delta_v[:4, :4]), 0.0, atol=1e-6)
    assert np.isclose(res.regional_baseline, 8.0, atol=1e-6)
    assert alert_mask(res, 8.0)[8:12, 8:12].all()


def test_unreferenced_result_declares_no_regional_signal():
    t, stack = _ramp(2.0, n=72)
    res = rate_of_change(stack, t, (2020.0, 2022.9), (2023.0, 2025.9))
    assert res.regional_baseline is None and res.reference_pixels == 0


def test_short_window_rejected():
    t, stack = _ramp(1.0)
    with pytest.raises(ValueError):
        rate_of_change(stack, t, (2020.0, 2020.1), (2021.0, 2023.9))
