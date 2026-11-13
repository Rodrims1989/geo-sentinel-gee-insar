"""
GEO-SENTINEL — insar/velocity.py
================================
Velocidad lineal vectorizada y TASA DE CAMBIO sobre stacks de series
temporales InSAR (p. ej. salidas de HyP3 + MintPy).

Regla metodológica (docs/methodology.md, R8): en cuencas con señal regional
(uplift/subsidencia de cuenca o tectónica), la velocidad ABSOLUTA de un píxel
mezcla el fondo regional con la deformación local de interés. La métrica de
alerta es la TASA DE CAMBIO:

    delta_v = v(ventana reciente) - v(ventana baseline)

Toda salida declara: ventanas usadas, píxeles de referencia y la magnitud de
la señal regional estimada. Sin NaN-filling: un píxel con pocas observaciones
válidas devuelve NaN (null estricto), jamás un valor inventado.

Convenciones
------------
- stack: ndarray (T, H, W) de desplazamiento acumulado en LOS [mm], NaN = sin dato.
- t: ndarray (T,) tiempo en años decimales (mismo orden que stack).
- Velocidades en mm/año; delta_v en mm/año.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Window = tuple[float, float]  # (t_inicio, t_fin) en años decimales, inclusivo


@dataclass(frozen=True)
class RateOfChange:
    """Resultado de rate_of_change() con su pedigrí metodológico completo."""

    v_baseline: np.ndarray      # (H, W) mm/año
    v_recent: np.ndarray        # (H, W) mm/año
    delta_v: np.ndarray         # (H, W) mm/año = v_recent - v_baseline
    n_baseline: np.ndarray      # (H, W) observaciones válidas usadas
    n_recent: np.ndarray        # (H, W)
    regional_baseline: float | None  # mm/año, mediana sobre píxeles de referencia
    regional_recent: float | None    # mm/año
    baseline_window: Window
    recent_window: Window
    reference_pixels: int       # nº de píxeles de referencia utilizados
    min_obs: int


def linear_velocity(
    stack: np.ndarray, t: np.ndarray, min_obs: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Pendiente por mínimos cuadrados de cada píxel, vectorizada (sin bucles
    sobre píxeles) y tolerante a NaN.

    Devuelve (velocidad [mm/año], n_obs). Píxeles con n_obs < min_obs → NaN.
    """
    stack = np.asarray(stack, dtype=float)
    t = np.asarray(t, dtype=float)
    if stack.ndim != 3:
        raise ValueError("stack debe tener forma (T, H, W)")
    if stack.shape[0] != t.shape[0]:
        raise ValueError("t y stack deben tener la misma longitud en el eje temporal")
    if min_obs < 2:
        raise ValueError("min_obs debe ser >= 2 para ajustar una recta")

    valid = np.isfinite(stack)
    tt = t[:, None, None]
    n = valid.sum(axis=0).astype(float)

    s_t = np.where(valid, tt, 0.0).sum(axis=0)
    s_y = np.where(valid, stack, 0.0).sum(axis=0)
    s_tt = np.where(valid, tt * tt, 0.0).sum(axis=0)
    s_ty = np.where(valid, tt * stack, 0.0).sum(axis=0)

    denom = n * s_tt - s_t ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = (n * s_ty - s_t * s_y) / denom

    slope[(n < min_obs) | ~np.isfinite(slope) | (denom <= 0)] = np.nan
    return slope, n.astype(int)


def _window_slice(t: np.ndarray, window: Window) -> np.ndarray:
    lo, hi = window
    if hi <= lo:
        raise ValueError(f"Ventana inválida: {window}")
    return (t >= lo) & (t <= hi)


def rate_of_change(
    stack: np.ndarray,
    t: np.ndarray,
    baseline: Window,
    recent: Window,
    reference_mask: np.ndarray | None = None,
    min_obs: int = 4,
) -> RateOfChange:
    """
    Tasa de cambio = v(reciente) - v(baseline), con corrección opcional por
    píxeles de referencia estables.

    reference_mask: bool (H, W). Si se da, se resta a cada ventana la mediana
    de las velocidades de esos píxeles (referenciación local), lo que elimina
    la señal regional común dentro de cada ventana. Si no se da, se devuelve
    la velocidad sin referenciar y regional_* = None — el lector debe saber
    que delta_v puede contener señal regional no estacionaria.
    """
    stack = np.asarray(stack, dtype=float)
    t = np.asarray(t, dtype=float)
    sel_b = _window_slice(t, baseline)
    sel_r = _window_slice(t, recent)
    if sel_b.sum() < min_obs or sel_r.sum() < min_obs:
        raise ValueError(
            f"Ventanas con menos de {min_obs} fechas: baseline={int(sel_b.sum())}, "
            f"reciente={int(sel_r.sum())}")

    v_b, n_b = linear_velocity(stack[sel_b], t[sel_b], min_obs)
    v_r, n_r = linear_velocity(stack[sel_r], t[sel_r], min_obs)

    reg_b = reg_r = None
    n_ref = 0
    if reference_mask is not None:
        ref = np.asarray(reference_mask, dtype=bool)
        if ref.shape != v_b.shape:
            raise ValueError("reference_mask debe tener forma (H, W)")
        ref_b = v_b[ref & np.isfinite(v_b)]
        ref_r = v_r[ref & np.isfinite(v_r)]
        if ref_b.size == 0 or ref_r.size == 0:
            raise ValueError("Sin píxeles de referencia válidos en alguna ventana")
        reg_b, reg_r = float(np.median(ref_b)), float(np.median(ref_r))
        v_b = v_b - reg_b
        v_r = v_r - reg_r
        n_ref = int(ref.sum())

    return RateOfChange(
        v_baseline=v_b, v_recent=v_r, delta_v=v_r - v_b,
        n_baseline=n_b, n_recent=n_r,
        regional_baseline=reg_b, regional_recent=reg_r,
        baseline_window=baseline, recent_window=recent,
        reference_pixels=n_ref, min_obs=min_obs,
    )


def alert_mask(result: RateOfChange, threshold_mm_yr: float) -> np.ndarray:
    """Píxeles cuya |delta_v| supera el umbral (mm/año). NaN nunca alerta."""
    if threshold_mm_yr <= 0:
        raise ValueError("El umbral debe ser > 0")
    with np.errstate(invalid="ignore"):
        return np.abs(result.delta_v) >= threshold_mm_yr


def describe(result: RateOfChange) -> str:
    """Pedigrí legible: lo que debe acompañar a cualquier mapa o cifra."""
    ref = (
        f"referenciado con {result.reference_pixels} px "
        f"(señal regional baseline={result.regional_baseline:+.2f}, "
        f"reciente={result.regional_recent:+.2f} mm/año)"
        if result.regional_baseline is not None
        else "SIN referenciar (delta_v puede incluir señal regional)"
    )
    return (
        f"delta_v = v{result.recent_window} - v{result.baseline_window}; "
        f"min_obs={result.min_obs}; {ref}."
    )
