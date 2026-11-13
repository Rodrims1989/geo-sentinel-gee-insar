"""
Evapotranspiración potencial de Oudin (2005) con radiación extraterrestre FAO-56.

Es la envolvente conservadora (piso) del PET de ERA5-Land, que actúa como techo.
El déficit hídrico real vive entre ambos (ver docs/methodology.md, R4).

    PE [mm/día] = 0.408 · Ra · (T + 5) / 100     si T + 5 > 0, si no 0

    Ra   radiación extraterrestre [MJ m⁻² día⁻¹] (FAO-56, ec. 21)
    T    temperatura media del aire [°C]
    0.408 = 1 / (λ·ρ), con λ = 2.45 MJ kg⁻¹ y ρ = 1000 kg m⁻³

Funciones puras (numpy); el mismo cálculo se replica server-side en la suite GEE.

Referencias:
    Oudin, L. et al. (2005). J. Hydrology 303, 290-306.
    Allen, R.G. et al. (1998). FAO Irrigation and Drainage Paper 56.
"""

from __future__ import annotations

import calendar
import math

import numpy as np

G_SC = 0.0820          # constante solar [MJ m⁻² min⁻¹]
INV_LAMBDA_RHO = 0.408  # mm por MJ m⁻²


def extraterrestrial_radiation(lat_deg: float, doy) -> np.ndarray:
    """Ra [MJ m⁻² día⁻¹] para latitud (grados, sur negativa) y día juliano."""
    phi = math.radians(lat_deg)
    j = np.asarray(doy, dtype=float)
    dr = 1 + 0.033 * np.cos(2 * np.pi * j / 365)
    delta = 0.409 * np.sin(2 * np.pi * j / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))
    return (24 * 60 / np.pi) * G_SC * dr * (
        ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))


def oudin_daily(t_c, ra) -> np.ndarray:
    """PE de Oudin [mm/día]; NaN en la temperatura se propaga (null estricto)."""
    t = np.asarray(t_c, dtype=float)
    pe = INV_LAMBDA_RHO * np.asarray(ra, dtype=float) * (t + 5) / 100
    return np.where(np.isnan(t), np.nan, np.where(t + 5 > 0, pe, 0.0))


def mid_month_doy(year: int, month: int) -> int:
    """Día juliano del día 15 del mes (aproximación del mes por su punto medio)."""
    return (np.datetime64(f"{year:04d}-{month:02d}-15")
            - np.datetime64(f"{year:04d}-01-01")).astype(int) + 1


def oudin_monthly(lat_deg: float, year: int, month: int, t_mean_c: float | None) -> float | None:
    """PE de Oudin acumulada en el mes [mm/mes] a partir de la T media mensual.

    Usar la T media mensual (y no la diaria) subestima levemente en meses con
    días bajo -5 °C; se declara en la nota metodológica.
    """
    if t_mean_c is None or (isinstance(t_mean_c, float) and math.isnan(t_mean_c)):
        return None
    ra = extraterrestrial_radiation(lat_deg, mid_month_doy(year, month))
    days = calendar.monthrange(year, month)[1]
    return float(oudin_daily(t_mean_c, ra) * days)
