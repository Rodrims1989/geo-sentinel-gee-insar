# Geo-Sentinel · GEE + InSAR para salares andinos

Teledetección hídrica y monitoreo de deformación en **salares y cuencas endorreicas altoandinas** (Puna argentina, Atacama, altiplano), con métodos auditables y calidad de dato explícita.

> **EN —** Open, auditable remote-sensing toolkit for high-Andean salars: a Google Earth Engine basin water-balance suite (Sentinel-2 water + dual-source rainfall + PET envelope + solar radiation + InSAR QC viewer), a trend-analysis battery (OLS + Mann-Kendall family), an InSAR rate-of-change module and a climate-balance × ground-deformation lag analysis. Documentation is in Spanish; code identifiers and docstrings are mostly Spanish with English method names.

Todo el repositorio usa **datos públicos** y **datos sintéticos** para las demostraciones. No contiene datos de campo, credenciales ni información de ningún proyecto o empresa.

---

## ¿Qué hay acá?

| Módulo | Qué hace | Dónde |
|---|---|---|
| **Suite hídrica v3 (GEE Code Editor)** | 4 modos: balance mensual de la cuenca (agua km², entradas P CHIRPS + ERA5, salidas PET ERA5/Oudin + evaporación real, radiación solar, T°), calidad de agua (NDTI, TSS), dinámica JRC y **visor InSAR** (media, p5 vertical, leyenda de signos); *Master CSV* con metadatos por fila | [`gee/code-editor/suite_hidrica_v3.js`](gee/code-editor/suite_hidrica_v3.js) |
| **Motores GEE para backend (Python)** | Los mismos cálculos como routers FastAPI: balance maestro (con Oudin y P dual) y serie climática | [`gee/python/`](gee/python/) |
| **PET de Oudin (2005)** | Radiación extraterrestre FAO-56 + temperatura → piso conservador del PET | [`analytics/oudin.py`](analytics/oudin.py) |
| **Análisis de tendencias** | OLS + Mann-Kendall (+ pre-whitening, + trend-free pre-whitening) con Theil-Sen; anualización auditada sin prorrateo | [`analytics/trends.py`](analytics/trends.py) |
| **InSAR: velocidad y tasa de cambio** | Velocidad lineal vectorizada por píxel y métrica de alerta `Δv = v_reciente − v_baseline` con referenciación | [`insar/velocity.py`](insar/velocity.py) |
| **Cruce balance × deformación** | ¿La tasa de deformación responde, y con qué retardo, a la anomalía de P − PET? Spearman por retardo, sin rellenar huecos | [`analytics/balance_insar.py`](analytics/balance_insar.py) |
| **Documentación metodológica** | Las 10 reglas de calidad, fuentes de datos, flujo InSAR (HyP3 + MintPy), autenticación GEE | [`docs/`](docs/) |

## Principios (resumen)

1. **s2cloudless siempre; QA60 prohibido** en salares (confunde costra salina con nube).
2. **Null estricto:** sin imágenes → `null`. Jamás se interpola ni se inventa un valor.
3. **Lluvia dual-source** (CHIRPS + ERA5-Land) y **PET con envolvente** (ERA5 + Oudin).
4. **Cada cifra viaja con su pedigrí** (fórmula, umbral, escala, máscara).
5. **Tendencias con Mann-Kendall + Theil-Sen**, no regresión lineal simple.
6. **InSAR: la alerta es la tasa de cambio**, no la velocidad absoluta.

Detalle y justificación física en [`docs/methodology.md`](docs/methodology.md).

## Inicio rápido

```bash
git clone <este-repo> && cd geo-sentinel-gee-insar
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

pytest -q                                   # tests de tendencias e InSAR
python insar/examples/synthetic_demo.py     # demo InSAR con datos sintéticos
python analytics/examples/balance_insar_demo.py   # cruce balance × deformación
```

**Suite GEE (sin instalar nada):** abrí <https://code.earthengine.google.com>, pegá el contenido de `gee/code-editor/suite_hidrica_v3.js`, presioná *Run*, dibujá un área de estudio y elegí el modo de análisis.

**Backend GEE:** configurá una service account según [`docs/gee-service-account.md`](docs/gee-service-account.md) y verificá con `python scripts/verify_gee_auth.py`.

## Estructura

```
geo-sentinel-gee-insar/
├── gee/
│   ├── code-editor/suite_hidrica_v3.js     # app interactiva (JavaScript)
│   └── python/                             # routers FastAPI (earthengine-api)
│       ├── master_balance.py
│       └── climate_engine.py
├── analytics/
│   ├── trends.py                           # batería de tendencias
│   ├── oudin.py                            # PET Oudin + radiación FAO-56
│   ├── balance_insar.py                    # cruce P−PET × tasa de deformación
│   └── examples/balance_insar_demo.py
├── insar/
│   ├── velocity.py                         # velocidad lineal + tasa de cambio
│   └── examples/synthetic_demo.py
├── docs/
│   ├── methodology.md                      # las 10 reglas y su justificación
│   ├── data-sources.md                     # datasets y fuentes oficiales
│   ├── insar-workflow.md                   # HyP3 → MintPy → tasa de cambio
│   └── gee-service-account.md              # credenciales GEE sin costo
├── scripts/verify_gee_auth.py
├── tests/
└── requirements.txt
```

## Estado y hoja de ruta

- [x] Suite hídrica v3 (4 modos, con balance de entradas/salidas y visor InSAR) y motores Python equivalentes
- [x] PET de Oudin (2005) en GEE y en el backend, como envolvente del PET de ERA5
- [x] Batería de tendencias con tests
- [x] Módulo InSAR de velocidad y tasa de cambio (implementación de referencia, validada con datos sintéticos)
- [x] Cruce balance climático × tasa de deformación (validado con datos sintéticos)
- [ ] Cargador de stacks MintPy (`timeseries.h5`) → arrays `(T, H, W)`
- [ ] Perfil de línea sobre la malla InSAR
- [ ] Sumar niveles piezométricos al cruce (InSAR × balance × piezometría)
- [ ] Validación del pipeline contra un estudio hidrológico independiente (ver regla R10)

## Alcance y limitaciones

- **TSS** (Nechad 2010, banda B4) sin calibración local es un estimador **relativo**: sirve para patrones espaciales y temporales, no para valores absolutos.
- **JRC** (1984-2021, Landsat 30 m) y **Sentinel-2** (2017+, 20 m) son productos distintos: se presentan como series paralelas, nunca mezcladas.
- **ERA5-Land mensual** publica con ~2-3 meses de retraso: esos meses salen como `null`.
- **Evaporación real ERA5-Land** es un producto modelado (~11 km): orden de magnitud, no reemplaza mediciones de campo.
- **Oudin con T media mensual** subestima levemente en meses con días bajo −5 °C.
- Nada generado con asistencia de IA debe publicarse sin revisión humana.

## Licencia

MIT — ver [`LICENSE`](LICENSE).
