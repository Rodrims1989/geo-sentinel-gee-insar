"""
Demo sintético: cruce balance climático × deformación InSAR.

Construye 10 años mensuales con:
  * P estacional de la Puna (lluvias de verano) + variabilidad interanual,
  * T media estacional → PET de Oudin con Ra FAO-56 a 24°S,
  * una serie de desplazamiento vertical cuya TASA responde a la anomalía de
    P − PET con 3 meses de retardo, más una subsidencia de fondo y ruido,
  * huecos (meses sin dato InSAR ni ERA5) que se respetan como NaN.

El objetivo es mostrar que el análisis recupera el retardo impuesto (3 meses)
sin rellenar huecos. Ejecutar:  python analytics/examples/balance_insar_demo.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from analytics.balance_insar import (  # noqa: E402
    balance_anomaly, best_lag, describe, lag_scan, monthly_rate)
from analytics.oudin import oudin_monthly  # noqa: E402

LAT = -24.0
LAG_TRUE = 3


def build_synthetic(seed: int = 7):
    rng = np.random.default_rng(seed)
    years = np.repeat(np.arange(2015, 2025), 12)
    months = np.tile(np.arange(1, 13), 10)
    n = len(months)

    wet = np.cos(2 * np.pi * (months - 1) / 12).clip(0)        # pico en enero
    interannual = np.repeat(rng.gamma(2.0, 0.5, 10), 12)       # años secos/húmedos
    precip = 40 * wet * interannual + rng.gamma(1.0, 2.0, n)
    temp = 8 + 6 * np.cos(2 * np.pi * (months - 1) / 12) + rng.normal(0, 0.8, n)
    pet = np.array([oudin_monthly(LAT, int(y), int(m), float(t))
                    for y, m, t in zip(years, months, temp)])

    anom = balance_anomaly(precip, pet, months)
    rate = -0.4 + 0.03 * np.roll(anom, LAG_TRUE) + rng.normal(0, 0.25, n)
    rate[:LAG_TRUE] = -0.4
    disp = np.cumsum(rate)

    gaps = rng.choice(np.arange(5, n), size=8, replace=False)
    disp[gaps] = np.nan                                          # sin adquisición
    pet[gaps[:3]] = np.nan                                       # latencia ERA5
    return years, months, precip, pet, disp


def main() -> None:
    _, months, precip, pet, disp = build_synthetic()
    anom = balance_anomaly(precip, pet, months)
    rate = monthly_rate(disp)
    results = lag_scan(anom, rate, max_lag=12)
    best = best_lag(results, alpha=0.05 / len(results))          # Bonferroni

    print("lag  rho     p        n")
    for r in results:
        mark = "  <--" if best and r.lag_months == best.lag_months else ""
        print(f"{r.lag_months:>3}  {r.rho:+.2f}  {r.p_value:.1e}  {r.n_pairs}{mark}")
    print()
    print(describe(results, best))
    print(f"Retardo impuesto en los datos sintéticos: {LAG_TRUE} meses.")


if __name__ == "__main__":
    main()
