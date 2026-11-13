"""
GEO-SENTINEL — analytics/trends.py
==================================
Análisis de tendencias sobre series mensuales (agua, precipitación, PET,
temperatura) con la batería metodológica habitual de los estudios
hidrológicos de referencia:

    1. Ordinary Least Squares (scipy.stats.linregress)
    2. Mann-Kendall (1945) + pendiente de Theil-Sen (1968)
    3. MK con pre-whitening clásico
    4. MK con trend-free pre-whitening (Yue et al., 2002)

Nota de integridad metodológica: la variante "Zhang (2001)" que algunos
reportes citan no está implementada con ese nombre en pymannkendall; este
módulo expone únicamente métodos verificados de la librería y los identifica
sin ambigüedad.

ANUALIZACIÓN CON REGLA DURA (sin prorrateo):
  - Variables acumulativas (aggregation="sum": precipitación, PET): el año
    exige 12 meses válidos o se EXCLUYE (prorratear con la estacionalidad de
    la Puna sesga: perder enero != perder junio).
  - Variables promedio (aggregation="mean": temperatura, área de agua):
    mínimo 10 meses válidos.
  - Todo año excluido se AUDITA en la respuesta. Jamás se rellena.

Uso como router FastAPI:
    from analytics import trends
    app.include_router(trends.router)

Requiere: pip install pymannkendall scipy numpy fastapi pydantic
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Literal

import numpy as np
import pymannkendall as mk
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from scipy import stats

logger = logging.getLogger("geosentinel.trends")
router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

MIN_YEARS = 8  # menos años que esto y cualquier "tendencia" es numerología


# ─────────────────────────────────────────────────────────────────────────────
# Esquemas
# ─────────────────────────────────────────────────────────────────────────────
class MonthlyValue(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="AAAA-MM")
    value: float | None


class TrendRequest(BaseModel):
    series: list[MonthlyValue] = Field(..., description="Serie mensual (con nulls permitidos)")
    aggregation: Literal["sum", "mean"] = Field(
        ..., description="sum: láminas (P, PET) | mean: temperatura, área de agua")
    parameter_name: str = Field("parametro", description="Etiqueta para el reporte")
    alpha: float = Field(0.05, gt=0, lt=0.5, description="Nivel de significancia")


class AnnualPoint(BaseModel):
    year: int
    value: float
    n_months: int


class ExcludedYear(BaseModel):
    year: int
    n_valid_months: int
    reason: str


class MethodResult(BaseModel):
    method: str
    slope_per_year: float | None
    intercept: float | None
    p_value: float | None
    trend: str          # 'creciente' | 'decreciente' | 'sin tendencia' | 'n/a'
    significant: bool
    note: str | None = None


class TrendResponse(BaseModel):
    parameter: str
    aggregation: str
    alpha: float
    annual_series: list[AnnualPoint]
    excluded_years: list[ExcludedYear]
    results: list[MethodResult]
    summary: str
    methodology_note: str


# ─────────────────────────────────────────────────────────────────────────────
# Anualización auditada
# ─────────────────────────────────────────────────────────────────────────────
def annualize(
    series: list[MonthlyValue], aggregation: str,
) -> tuple[list[AnnualPoint], list[ExcludedYear]]:
    by_year: dict[int, list[float]] = defaultdict(list)
    months_seen: dict[int, int] = defaultdict(int)

    for item in series:
        year = int(item.month[:4])
        months_seen[year] += 1
        if item.value is not None:
            by_year[year].append(float(item.value))

    min_months = 12 if aggregation == "sum" else 10
    annual: list[AnnualPoint] = []
    excluded: list[ExcludedYear] = []

    for year in sorted(months_seen):
        valid = by_year.get(year, [])
        if len(valid) < min_months:
            excluded.append(ExcludedYear(
                year=year, n_valid_months=len(valid),
                reason=(f"requiere {min_months} meses válidos para "
                        f"aggregation='{aggregation}' (sin prorrateo: la "
                        f"estacionalidad extrema de la Puna lo prohíbe)")))
            continue
        value = sum(valid) if aggregation == "sum" else sum(valid) / len(valid)
        annual.append(AnnualPoint(year=year, value=round(value, 3),
                                  n_months=len(valid)))

    return annual, excluded


# ─────────────────────────────────────────────────────────────────────────────
# Batería de métodos
# ─────────────────────────────────────────────────────────────────────────────
def _trend_word(mk_trend: str) -> str:
    return {"increasing": "creciente", "decreasing": "decreciente",
            "no trend": "sin tendencia"}.get(mk_trend, mk_trend)


def run_methods(years: np.ndarray, values: np.ndarray, alpha: float) -> list[MethodResult]:
    results: list[MethodResult] = []

    # 1 — OLS ------------------------------------------------------------
    ols = stats.linregress(years, values)
    results.append(MethodResult(
        method="Ordinary Least Squares",
        slope_per_year=round(float(ols.slope), 5),
        intercept=round(float(ols.intercept), 3),
        p_value=round(float(ols.pvalue), 5),
        trend=("creciente" if ols.slope > 0 else "decreciente")
              if ols.pvalue < alpha else "sin tendencia",
        significant=bool(ols.pvalue < alpha),
    ))

    # 2-4 — Familia Mann-Kendall (pendiente = Theil-Sen en todas) ---------
    mk_battery = [
        ("MK (1945) + Theil-Sen (1968)", mk.original_test, None),
        ("MK con pre-whitening", mk.pre_whitening_modification_test, None),
        ("MK trend-free pre-whitening (Yue et al. 2002)",
         mk.trend_free_pre_whitening_modification_test,
         "Variante recomendada cuando hay autocorrelación serial con tendencia"),
    ]
    for name, fn, note in mk_battery:
        try:
            r = fn(values, alpha=alpha)
            results.append(MethodResult(
                method=name,
                slope_per_year=round(float(r.slope), 5),
                intercept=round(float(r.intercept), 3),
                p_value=round(float(r.p), 5),
                trend=_trend_word(r.trend),
                significant=bool(r.h),
                note=note,
            ))
        except Exception as exc:  # serie degenerada (constante, etc.)
            logger.warning("Método %s falló: %s", name, exc)
            results.append(MethodResult(
                method=name, slope_per_year=None, intercept=None,
                p_value=None, trend="n/a", significant=False,
                note=f"No computable sobre esta serie: {exc}"))

    return results


def summarize(param: str, results: list[MethodResult], n_years: int) -> str:
    sig = [r for r in results if r.significant and r.slope_per_year is not None]
    if not sig:
        return (f"Sin tendencia estadísticamente significativa para {param} "
                f"({n_years} años analizados). Coherencia entre métodos: "
                f"{len(results)} evaluados, 0 significativos.")
    slopes = [r.slope_per_year for r in sig if r.slope_per_year is not None]
    mean_decade = 10 * sum(slopes) / len(slopes)
    direction = "creciente" if mean_decade > 0 else "decreciente"
    return (f"Tendencia {direction} significativa para {param}: "
            f"{mean_decade:+.3f} unidades/década (promedio de {len(sig)} de "
            f"{len(results)} métodos significativos, {n_years} años).")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/trend", response_model=TrendResponse)
async def trend_analysis(req: TrendRequest) -> TrendResponse:
    """Batería de tendencias (OLS + familia Mann-Kendall) sobre agregados
    anuales auditados de una serie mensual."""
    annual, excluded = annualize(req.series, req.aggregation)

    if len(annual) < MIN_YEARS:
        raise HTTPException(
            422,
            f"Solo {len(annual)} años completos tras la anualización "
            f"(mínimo {MIN_YEARS}). Años excluidos: "
            f"{[e.year for e in excluded]}. Ampliar el rango o revisar la "
            f"cobertura de datos — una tendencia sobre pocos años no es "
            f"defendible.")

    years = np.array([p.year for p in annual], dtype=float)
    values = np.array([p.value for p in annual], dtype=float)

    results = run_methods(years, values, req.alpha)

    return TrendResponse(
        parameter=req.parameter_name,
        aggregation=req.aggregation,
        alpha=req.alpha,
        annual_series=annual,
        excluded_years=excluded,
        results=results,
        summary=summarize(req.parameter_name, results, len(annual)),
        methodology_note=(
            "Anualización sin prorrateo (sum: 12/12 meses; mean: >=10). "
            "Pendientes en unidades/año; x10 para /década. La familia MK usa "
            "Theil-Sen como estimador de pendiente. La variante 'Zhang 2001' "
            "citada en algunos reportes no está disponible en pymannkendall "
            "bajo ese nombre y no se emula: solo métodos verificados."),
    )
