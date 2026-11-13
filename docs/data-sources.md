# Fuentes de datos

Todas son públicas. Este repositorio no incluye datos descargados.

## Google Earth Engine

| Dataset | ID en GEE | Uso | Escala nativa |
|---|---|---|---|
| Sentinel-2 L2A | `COPERNICUS/S2_SR_HARMONIZED` | NDWI, NDTI, TSS (desde 2017-03) | 10–20 m |
| Probabilidad de nubes S2 | `COPERNICUS/S2_CLOUD_PROBABILITY` | máscara s2cloudless | 10 m |
| CHIRPS diario | `UCSB-CHG/CHIRPS/DAILY` | precipitación | ~5566 m |
| ERA5-Land mensual | `ECMWF/ERA5_LAND/MONTHLY_AGGR` | temperatura 2 m, PET | ~11132 m |
| JRC Monthly Water History | `JRC/GSW1_4/MonthlyHistory` | dinámica de agua 1984–2021 | 30 m |

## InSAR

| Fuente | Qué aporta |
|---|---|
| Sentinel-1 (Copernicus / ASF) | imágenes SAR de entrada |
| [ASF HyP3](https://hyp3-docs.asf.alaska.edu/) | procesamiento InSAR gratuito (interferogramas) |
| [MintPy](https://mintpy.readthedocs.io/) | inversión de series temporales (`timeseries.h5`) |

## Fuentes oficiales de Argentina

| Organismo | Qué ofrece |
|---|---|
| SEGEMAR (repositorio institucional y SIGAM) | cartas geológicas, boletines, servicios WMS/WFS/CSW |
| Secretaría de Minería de la Nación | informes mensuales de balance de minerales y proyecciones de exportaciones |
| datos.gob.ar | datos abiertos (API CKAN) |

## Referencias metodológicas

- Nechad, B., Ruddick, K., Park, Y. (2010). Calibration and validation of a generic multisensor algorithm for mapping of total suspended matter in turbid waters. *Remote Sensing of Environment*, 114.
- Oudin, L. et al. (2005). Which potential evapotranspiration input for a lumped rainfall–runoff model? *Journal of Hydrology*, 303.
- Mann (1945); Kendall (1975); Sen (1968) — prueba de tendencia y pendiente robusta.
- Yue, S., Pilon, P., Phinney, B., Cavadias, G. (2002). The influence of autocorrelation on the ability to detect trend in hydrological series. *Hydrological Processes*, 16.
- McFeeters (1996) — NDWI.
