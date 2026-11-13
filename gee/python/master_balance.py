"""
GEO-SENTINEL — master_balance.py
================================
Balance hidrico mensual maestro (agua + lluvia + clima) sobre un AOI, 100 %
server-side en Google Earth Engine y expuesto como router FastAPI.

  - Agua: NDWI sobre compuestos mensuales medianos de Sentinel-2 SR con
    mascara s2cloudless (QA60 prohibido en salares, ver docs/methodology.md).
  - Entradas: precipitacion dual-source, CHIRPS Daily y ERA5-Land
    (la divergencia entre ambas es la banda de incertidumbre).
  - Salidas: PET con envolvente (ERA5-Land = techo, Oudin 2005 = piso) y
    evaporacion real modelada por ERA5-Land (total_evaporation_sum).
  - Energia: radiacion solar descendente en superficie de ERA5-Land
    (MJ/m2/mes), ademas de la radiacion extraterrestre que usa Oudin.
  - Balance P - PET calculado con ambos estimadores de PET.

Reglas de calidad: mes sin imagenes -> null (jamas se interpola); ceros
anomalos rodeados de meses con agua se excluyen y se auditan en la respuesta.

Registro en una app FastAPI:
    from gee.python import master_balance
    app.include_router(master_balance.router)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import ee
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from analytics.oudin import oudin_monthly

logger = logging.getLogger("geosentinel.master_balance")

router = APIRouter(prefix="/api/v1/gee", tags=["master-balance"])

_KELVIN = 273.15
_CHIRPS_SCALE_M = 5566
_ERA5_SCALE_M = 11132
_S2_SR_START = date(2017, 3, 28)
_ERA5_BANDS = ("temperature_2m", "potential_evaporation_sum", "total_precipitation_sum",
               "total_evaporation_sum", "surface_solar_radiation_downwards_sum")

_METHODOLOGY = {
    "agua": "NDWI sobre compuestos mensuales medianos de Sentinel-2 L2A con filtro s2cloudless.",
    "lluvia": "Dual-source: UCSB-CHG/CHIRPS/DAILY (suma mensual) y ERA5-Land total_precipitation_sum.",
    "clima": "ECMWF/ERA5_LAND/MONTHLY_AGGR — temperature_2m, potential_evaporation_sum, "
             "total_evaporation_sum, surface_solar_radiation_downwards_sum.",
    "pet_oudin": "Oudin (2005): PE = 0.408·Ra·(T+5)/100 mm/día si T+5>0; Ra FAO-56 con la "
                 "latitud del centroide y el día 15 del mes; T = media mensual ERA5-Land.",
    "balance": "P − PET con P CHIRPS y ambos PET; la severidad del déficit se juzga contra el piso (Oudin).",
}
_CONVERSIONS = {
    "temp_c": "temperature_2m (K) - 273.15",
    "pet_mm": "potential_evaporation_sum (m) × -1000",
    "precip_era5_mm": "total_precipitation_sum (m) × 1000",
    "aet_era5_mm": "total_evaporation_sum (m, negativo = evaporación) × -1000",
    "solar_rad_mj_m2": "surface_solar_radiation_downwards_sum (J/m²) / 1e6",
}

class MasterBalanceRequest(BaseModel):
    aoi_geojson: dict[str, Any] = Field(...)
    start: date
    end: date
    cloud_prob_max: int = Field(40, ge=5, le=95)
    ndwi_threshold: float = Field(0.0)
    scale_m: int = Field(20, ge=10, le=120)

    @field_validator("aoi_geojson")
    @classmethod
    def _polygon_only(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("aoi_geojson debe ser Polygon o MultiPolygon")
        return v

class BalanceMonth(BaseModel):
    month: str
    water_km2: float | None
    precip_mm: float | None
    precip_era5_mm: float | None
    temp_c: float | None
    pet_mm: float | None
    pet_oudin_mm: float | None
    aet_era5_mm: float | None
    solar_rad_mj_m2: float | None
    balance_era5_mm: float | None
    balance_oudin_mm: float | None

class ExcludedWaterMonth(BaseModel):
    month: str
    reason: str
    raw_area_km2: float | None

class MasterBalanceResponse(BaseModel):
    series: list[BalanceMonth]
    excluded_water: list[ExcludedWaterMonth]
    methodology: dict[str, str]
    conversions: dict[str, str]
    notes: list[str]

def _s2_masked(aoi: ee.Geometry, start: str, end: str, cloud_prob_max: int) -> ee.ImageCollection:
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi).filterDate(start, end)
    clouds = ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY").filterBounds(aoi).filterDate(start, end)
    joined = ee.Join.saveFirst("cloud_prob").apply(
        primary=s2, secondary=clouds,
        condition=ee.Filter.equals(leftField="system:index", rightField="system:index"),
    )

    def _mask(img: ee.Image) -> ee.Image:
        img = ee.Image(img)
        prob = ee.Image(img.get("cloud_prob")).select("probability")
        return (img.updateMask(prob.lt(cloud_prob_max)).divide(10000)
                .copyProperties(img, ["system:time_start"]))

    return ee.ImageCollection(joined.map(_mask))

def _build_monthly_graph(
    aoi: ee.Geometry, start: date, n_months: int,
    cloud_prob_max: int, ndwi_threshold: float, scale_m: int,
) -> ee.FeatureCollection:
    
    end_str = ee.Date(start.isoformat()).advance(n_months, "month").format("YYYY-MM-dd")
    s2 = _s2_masked(aoi, start.isoformat(), end_str.getInfo(), cloud_prob_max) 
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterBounds(aoi).select("precipitation")
    era5 = (ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
            .filterBounds(aoi)
            .select(list(_ERA5_BANDS)))

    start_ee = ee.Date(start.isoformat())

    def _per_month(offset: ee.Number) -> ee.Feature:
        m_start = start_ee.advance(offset, "month")
        m_end = m_start.advance(1, "month")

        monthly_s2 = s2.filterDate(m_start, m_end)
        n_images = monthly_s2.size()
        ndwi = monthly_s2.median().normalizedDifference(["B3", "B8"])
        water_dict = (
            ndwi.gt(ndwi_threshold)
            .multiply(ee.Image.pixelArea()).divide(1e6).rename("water_km2")
            .reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi,
                          scale=scale_m, maxPixels=1e13, bestEffort=True)
        )
        water_area = ee.Algorithms.If(water_dict.contains("water_km2"), water_dict.get("water_km2"), None)

        precip_dict = (
            chirps.filterDate(m_start, m_end).sum()
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=aoi,
                          scale=_CHIRPS_SCALE_M, maxPixels=1e13, bestEffort=True)
        )
        precip = ee.Algorithms.If(precip_dict.contains("precipitation"), precip_dict.get("precipitation"), None)

        era5_month = era5.filterDate(m_start, m_end).mean()
        clima_dict = era5_month.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=aoi,
            scale=_ERA5_SCALE_M, maxPixels=1e13, bestEffort=True
        )
        def _band(name: str):
            return ee.Algorithms.If(clima_dict.contains(name), clima_dict.get(name), None)

        return ee.Feature(None, {
            "month": m_start.format("YYYY-MM"),
            "water_km2": water_area,
            "n_images": n_images,
            "precip_mm": precip,
            "temp_k": _band("temperature_2m"),
            "pet_m": _band("potential_evaporation_sum"),
            "precip_era5_m": _band("total_precipitation_sum"),
            "aet_m": _band("total_evaporation_sum"),
            "ssrd_j": _band("surface_solar_radiation_downwards_sum"),
        })

    return ee.FeatureCollection(ee.List.sequence(0, n_months - 1).map(_per_month))

def _centroid_lat(geojson: dict[str, Any]) -> float:
    """Latitud media de los vértices del AOI (suficiente para Ra en AOIs de cuenca)."""
    rings = geojson["coordinates"] if geojson["type"] == "Polygon" else \
        [r for poly in geojson["coordinates"] for r in poly]
    lats = [pt[1] for ring in rings for pt in ring]
    return sum(lats) / len(lats)


def _r(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


def _clean_water(
    raw: list[dict[str, Any]],
) -> tuple[dict[str, float | None], list[ExcludedWaterMonth]]:
    excluded: list[ExcludedWaterMonth] = []
    seq: list[tuple[str, float | None]] = []

    for row in raw:
        area = row.get("water_km2")
        if area is None or row.get("n_images", 0) == 0:
            excluded.append(ExcludedWaterMonth(
                month=row["month"], reason="sin imágenes válidas",
                raw_area_km2=None))
            seq.append((row["month"], None))
        else:
            seq.append((row["month"], float(area)))

    nonzero = sorted(a for _, a in seq if a is not None and a > 0)
    floor = max(0.1, 0.2 * nonzero[len(nonzero) // 2]) if nonzero else 0.1

    def _neighbor(idx: int, step: int) -> float | None:
        j = idx + step
        while 0 <= j < len(seq):
            a = seq[j][1]
            if a is not None:
                return a
            j += step
        return None

    result: dict[str, float | None] = {}
    for i, (month, area) in enumerate(seq):
        if area is not None and area <= 0.01:
            prev_a, next_a = _neighbor(i, -1), _neighbor(i, +1)
            if prev_a is not None and next_a is not None and prev_a > floor and next_a > floor:
                excluded.append(ExcludedWaterMonth(
                    month=month,
                    reason="cero anómalo",
                    raw_area_km2=round(area, 4)))
                result[month] = None
                continue
        result[month] = round(area, 2) if area is not None else None
    return result, excluded

@router.post("/master-balance", response_model=MasterBalanceResponse)
async def master_balance(req: MasterBalanceRequest) -> MasterBalanceResponse:
    n_months = (req.end.year - req.start.year) * 12 + (req.end.month - req.start.month)
    if n_months < 1:
        raise HTTPException(422, "El rango debe cubrir al menos un mes completo")
    if n_months > 120:
        raise HTTPException(422, "Rango máximo: 120 meses")

    notes: list[str] = []
    if req.start < _S2_SR_START:
        notes.append(f"Sentinel-2 SR Harmonized inicia {_S2_SR_START.isoformat()}")

    try:
        aoi = ee.Geometry(req.aoi_geojson)
        fc = _build_monthly_graph(
            aoi, req.start, n_months, req.cloud_prob_max, req.ndwi_threshold, req.scale_m)
        raw: list[dict[str, Any]] = [f["properties"] for f in fc.getInfo()["features"]]
    except ee.EEException as exc:
        logger.error("master-balance falló en GEE: %s", exc)
        raise HTTPException(502, f"Earth Engine: {exc}") from exc

    water_clean, excluded = _clean_water(raw)
    lat = _centroid_lat(req.aoi_geojson)

    series: list[BalanceMonth] = []
    era5_missing: list[str] = []
    for row in raw:
        month = row["month"]
        temp_k, pet_m, precip = row.get("temp_k"), row.get("pet_m"), row.get("precip_mm")
        if temp_k is None and pet_m is None:
            era5_missing.append(month)
        temp_c = float(temp_k) - _KELVIN if temp_k is not None else None
        pet = abs(float(pet_m) * 1000.0) if pet_m is not None else None
        year, mon = int(month[:4]), int(month[5:7])
        pet_oudin = oudin_monthly(lat, year, mon, temp_c)
        p = float(precip) if precip is not None else None
        p_era5, aet, ssrd = row.get("precip_era5_m"), row.get("aet_m"), row.get("ssrd_j")
        series.append(BalanceMonth(
            month=month,
            water_km2=water_clean.get(month),
            precip_mm=_r(p),
            precip_era5_mm=_r(float(p_era5) * 1000.0) if p_era5 is not None else None,
            temp_c=_r(temp_c, 2),
            pet_mm=_r(pet),
            pet_oudin_mm=_r(pet_oudin),
            aet_era5_mm=_r(float(aet) * -1000.0) if aet is not None else None,
            solar_rad_mj_m2=_r(float(ssrd) / 1e6) if ssrd is not None else None,
            balance_era5_mm=_r(p - pet) if p is not None and pet is not None else None,
            balance_oudin_mm=_r(p - pet_oudin) if p is not None and pet_oudin is not None else None,
        ))

    if era5_missing:
        notes.append("Meses sin ERA5-Land: " + ", ".join(era5_missing))

    return MasterBalanceResponse(
        series=series, excluded_water=excluded,
        methodology=_METHODOLOGY, conversions=_CONVERSIONS, notes=notes)