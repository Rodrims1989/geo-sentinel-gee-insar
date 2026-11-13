"""
Cruce balance climático × deformación InSAR (entradas/salidas de la cuenca
contra la respuesta del terreno).

Pregunta que responde: ¿la velocidad de deformación responde, con algún
retardo, a anomalías del balance climático (P − PET)? Si responde, parte de la
señal es natural (recarga/descarga) y hay que descontarla antes de atribuir una
alerta a la operación. Si no responde, la señal climática no la explica.

Decisiones metodológicas:

* **Anomalía, no balance bruto.** En la Puna P − PET es negativo casi todo el
  año; lo informativo es el desvío respecto de la climatología mensual.
* **Velocidad vs anomalía, no acumulados vs acumulados.** Correlacionar dos
  series acumuladas con tendencia da correlaciones espurias altas. Se
  correlaciona la tasa mensual de deformación con la anomalía desplazada.
* **Spearman** (robusto a outliers y relaciones monótonas no lineales) y
  número mínimo de pares; los meses sin dato se excluyen, nunca se rellenan.
* Correlación no es causalidad: el resultado es un indicio para el modelo
  conceptual, no una atribución.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr


def balance_anomaly(precip_mm, pet_mm, months) -> np.ndarray:
    """Anomalía mensual de P − PET respecto de su climatología (mm).

    months: enteros 1-12 alineados con las series. NaN se propaga.
    """
    p = np.asarray(precip_mm, dtype=float)
    e = np.asarray(pet_mm, dtype=float)
    m = np.asarray(months, dtype=int)
    bal = p - e
    clim = np.full(13, np.nan)
    for k in range(1, 13):
        sel = (m == k) & ~np.isnan(bal)
        if sel.any():
            clim[k] = bal[sel].mean()
    return bal - clim[m]


def monthly_rate(displacement_mm) -> np.ndarray:
    """Tasa mensual de deformación (mm/mes) como primera diferencia.

    El primer mes queda NaN; un mes sin dato invalida las dos tasas que toca.
    """
    d = np.asarray(displacement_mm, dtype=float)
    r = np.full_like(d, np.nan)
    r[1:] = np.diff(d)
    return r


@dataclass(frozen=True)
class LagResult:
    lag_months: int
    rho: float
    p_value: float
    n_pairs: int


def lag_scan(anomaly, rate, max_lag: int = 12, min_pairs: int = 18) -> list[LagResult]:
    """Spearman entre rate[t] y anomaly[t − lag] para lag = 0..max_lag."""
    a = np.asarray(anomaly, dtype=float)
    r = np.asarray(rate, dtype=float)
    out: list[LagResult] = []
    for lag in range(max_lag + 1):
        x = a[: len(a) - lag] if lag else a
        y = r[lag:]
        ok = ~np.isnan(x) & ~np.isnan(y)
        n = int(ok.sum())
        if n < min_pairs:
            continue
        rho, p = spearmanr(x[ok], y[ok])
        out.append(LagResult(lag, float(rho), float(p), n))
    return out


def best_lag(results: list[LagResult], alpha: float = 0.05) -> LagResult | None:
    """Retardo con mayor |rho| entre los significativos; None si ninguno lo es.

    Con muchos retardos probados conviene corregir alpha (p. ej. Bonferroni:
    alpha / n_lags); se deja al usuario para no ocultar la decisión.
    """
    sig = [r for r in results if r.p_value < alpha]
    return max(sig, key=lambda r: abs(r.rho)) if sig else None


def describe(results: list[LagResult], best: LagResult | None) -> str:
    """Resumen trazable para acompañar cualquier gráfico del cruce."""
    if not results:
        return "Sin retardos evaluables: pares válidos insuficientes."
    lags = f"{results[0].lag_months}-{results[-1].lag_months}"
    if best is None:
        return f"Ningún retardo significativo (lags {lags} meses, Spearman)."
    return (f"Retardo de mayor correlación: {best.lag_months} meses "
            f"(rho={best.rho:+.2f}, p={best.p_value:.3g}, n={best.n_pairs}; "
            f"lags evaluados {lags}). Indicio, no atribución causal.")
