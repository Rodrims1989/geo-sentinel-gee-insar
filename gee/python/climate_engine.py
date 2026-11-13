"""
GEO-SENTINEL — climate_engine.py
================================
Motor de climatología (Pestaña 2). Un único endpoint combinado que fusiona
SERVER-SIDE dos datasets verificados de GEE y devuelve la serie mensual
alineada, lista para el gráfico de doble eje:

  - Precipitación:  UCSB-CHG/CHIRPS/DAILY  (banda 'precipitation', mm/día;
                    se acumula a mm/mes)
  - Temperatura y Evaporación Potencial:  ECMWF/ERA5_LAND/MONTHLY_AGGR
                    ('temperature_2m' en Kelvin; 'potential_evaporation_sum'
                    en metros, convención ECMWF: NEGATIVO = evaporación)

Conversiones aplicadas (documentadas en la respuesta):
  temp_c  = temperature_2m − 273.15
  pet_mm  = potential_evaporation_sum × (−1000)   [m → mm, signo físico]

Nota de latencia: ERA5-Land mensual publica con ~2-3 meses de retraso.
Los meses con CHIRPS pero sin ERA5 se devuelven con temp_c/pet_mm = null;
el frontend corta la línea (connectNulls=false) en lugar de interpolar.

Registro en main.py:
    from app.routers import climate_engine
    app.include_router(climate_engine.router)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import ee
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("geosentinel.climate_engine")

router = APIRouter(prefix="/api/v1/gee/climate", tags=["climate"])

# ─────────────────────────────────────────────────────────────────────────────
# Esquemas
# ─────────────────────────────────────────────────────────────────────────────


class ClimateRequest(BaseModel):
    aoi_geojson: dict[str, Any] = Field(..., description="Polygon GeoJSON WGS84")
    start: date
    end: date

    @field_validator("aoi_geojson")
    @classmethod
    def _polygon_only(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("aoi_geojson debe ser Polygon o MultiPolygon")
        return v


class ClimateMonth(BaseModel):
    month: str = Field(..., description="AAAA-MM")
    precip_mm: float | None = Field(None, description="Precipitación acumulada CHIRPS")
    temp_c: float | None = Field(None, description="Temperatura media 2 m, ERA5-Land")
    pet_mm: float | None = Field(None, description="Evaporación potencial acumulada, ERA5-Land")


class ClimateSeriesResponse(BaseModel):
    series: list[ClimateMonth]
    datasets: dict[str, str]
    conversions: dict[str, str]
    notes: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# Núcleo GEE (todo server-side; una llamada getInfo por dataset)
# ─────────────────────────────────────────────────────────────────────────────

_CHIRPS_SCALE_M = 5566     # resolución nativa ~0.05°
_ERA5_SCALE_M = 11132      # resolución nativa ~0.1°
_KELVIN = 273.15


def _month_count(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _chirps_monthly(aoi: ee.Geometry, start: date, n_months: int) -> list[dict[str, Any]]:
    """Precipitación mensual acumulada (mm) promediada espacialmente en el AOI."""
    chirps = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterBounds(aoi)
        .select("precipitation")
    )
    start_ee = ee.Date(start.isoformat())

    def _per_month(offset: ee.Number) -> ee.Feature:
        m_start = start_ee.advance(offset, "month")
        m_end = m_start.advance(1, "month")
        total = chirps.filterDate(m_start, m_end).sum()
        mean_mm = total.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi,
            scale=_CHIRPS_SCALE_M,
            maxPixels=1e13,
            bestEffort=True,
        ).get("precipitation")
        return ee.Feature(None, {"month": m_start.format("YYYY-MM"), "precip_mm": mean_mm})

    fc = ee.FeatureCollection(ee.List.sequence(0, n_months - 1).map(_per_month))
    return [f["properties"] for f in fc.getInfo()["features"]]


def _era5_monthly(aoi: ee.Geometry, start: date, end: date) -> list[dict[str, Any]]:
    """
    Temperatura media (K) y evaporación potencial (m, negativa) por mes.
    ERA5_LAND/MONTHLY_AGGR ya es mensual: se mapea imagen a imagen, sin
    re-agrupar. Las conversiones de unidad se hacen en Python (post-getInfo)
    para mantener el grafo de GEE mínimo.
    """
    era5 = (
        ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
        .filterBounds(aoi)
        .filterDate(start.isoformat(), end.isoformat())
        .select(["temperature_2m", "potential_evaporation_sum"])
    )

    def _per_image(img: ee.Image) -> ee.Feature:
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi,
            scale=_ERA5_SCALE_M,
            maxPixels=1e13,
            bestEffort=True,
        )
        return ee.Feature(
            None,
            {
                "month": ee.Date(img.get("system:time_start")).format("YYYY-MM"),
                "temp_k": stats.get("temperature_2m"),
                "pet_m": stats.get("potential_evaporation_sum"),
            },
        )

    fc = ee.FeatureCollection(era5.map(_per_image))
    return [f["properties"] for f in fc.getInfo()["features"]]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint combinado
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/monthly-series", response_model=ClimateSeriesResponse)
async def climate_monthly_series(req: ClimateRequest) -> ClimateSeriesResponse:
    """
    Serie climática mensual alineada: precipitación (CHIRPS) + temperatura y
    evaporación potencial (ERA5-Land) sobre el AOI. Un solo request del
    frontend → una fila por mes, lista para el gráfico de doble eje.
    """
    n_months = _month_count(req.start, req.end)
    if n_months < 1:
        raise HTTPException(422, "El rango debe cubrir al menos un mes completo")
    if n_months > 240:
        raise HTTPException(422, "Rango máximo: 240 meses por request")

    try:
        aoi = ee.Geometry(req.aoi_geojson)
        chirps_rows = _chirps_monthly(aoi, req.start, n_months)
        era5_rows = _era5_monthly(aoi, req.start, req.end)
    except ee.EEException as exc:
        logger.error("GEE climate series falló: %s", exc)
        raise HTTPException(502, f"Earth Engine: {exc}") from exc

    # ── Fusión por clave de mes ───────────────────────────────────────────
    era5_by_month: dict[str, dict[str, Any]] = {r["month"]: r for r in era5_rows}

    series: list[ClimateMonth] = []
    months_without_era5: list[str] = []

    for row in chirps_rows:
        month = row["month"]
        precip = row.get("precip_mm")
        era5 = era5_by_month.get(month)

        temp_c: float | None = None
        pet_mm: float | None = None
        if era5 is not None:
            if era5.get("temp_k") is not None:
                temp_c = round(float(era5["temp_k"]) - _KELVIN, 2)
            if era5.get("pet_m") is not None:
                # Convención ECMWF: negativo = evaporación → mm positivos.
                pet_mm = round(float(era5["pet_m"]) * -1000.0, 1)
        else:
            months_without_era5.append(month)

        series.append(
            ClimateMonth(
                month=month,
                precip_mm=round(float(precip), 1) if precip is not None else None,
                temp_c=temp_c,
                pet_mm=pet_mm,
            )
        )

    notes: list[str] = []
    if months_without_era5:
        notes.append(
            "Meses sin ERA5-Land (latencia de publicación ~2-3 meses): "
            + ", ".join(months_without_era5)
        )

    return ClimateSeriesResponse(
        series=series,
        datasets={
            "precipitacion": "UCSB-CHG/CHIRPS/DAILY",
            "temperatura_y_pet": "ECMWF/ERA5_LAND/MONTHLY_AGGR",
        },
        conversions={
            "temp_c": "temperature_2m (K) - 273.15",
            "pet_mm": "potential_evaporation_sum (m, negativo=evaporación) × -1000",
        },
        notes=notes,
    )
