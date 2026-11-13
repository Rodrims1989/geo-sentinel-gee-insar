# Metodología — reglas de teledetección hídrica en cuencas endorreicas

Los salares rompen los defaults de la teledetección genérica. Cada regla tiene una razón física; violarla produce resultados que *parecen* correctos y están mal.

## Reglas

**R1 — s2cloudless siempre; QA60 prohibido.**
La banda QA60 de Sentinel-2 confunde la costra salina brillante con nubes y enmascara el propio objeto de estudio. Se usa `COPERNICUS/S2_CLOUD_PROBABILITY` unido por `system:index`, con umbral de probabilidad (por defecto 40 %).

```javascript
var joined = ee.Join.saveFirst('cloud_prob').apply({
  primary: s2sr, secondary: s2clouds,
  condition: ee.Filter.equals({leftField: 'system:index', rightField: 'system:index'})});
// máscara: probability < CLOUD_PROB_MAX
```

**R2 — Null estricto. Jamás interpolar ni inventar.**
Mes sin imágenes válidas o sin píxeles de agua → `null`. Los huecos en los gráficos son información (estacionalidad de nubes, gaps de adquisición). En GEE, `median()` de una colección vacía no tiene bandas: envolver en `ee.Algorithms.If(hasImages, …)` antes de reducir.

**R3 — Precipitación dual-source: CHIRPS + ERA5-Land.**
Ninguna fuente sola es confiable en la Puna (red pluviométrica casi inexistente). Se reportan ambas; la divergencia es una banda de incertidumbre, no un problema a esconder. Escalas nativas: CHIRPS ~5566 m, ERA5-Land ~11132 m.

**R4 — PET con envolvente conservadora: ERA5 + Oudin (2005).**
ERA5-Land `potential_evaporation_sum` viene en metros con signo negativo para evaporación (×−1000 → mm). Oudin (2005), calculado desde temperatura y radiación extraterrestre (FAO-56, latitud del centroide, día 15 del mes), actúa como envolvente conservadora: `PE = 0.408·Ra·(T+5)/100` mm/día si T+5 > 0. La severidad del déficit se juzga contra el piso (Oudin): si hasta el estimador conservador supera varias veces a la precipitación, el déficit es indiscutible. Temperatura: `temperature_2m` en K − 273.15.

Complementos de energía y salidas (ERA5-Land): radiación solar descendente en superficie (`surface_solar_radiation_downwards_sum`, J/m² → MJ/m²) y evaporación real modelada (`total_evaporation_sum`, m, negativo = evaporación, ×−1000). Son productos de reanálisis: orden de magnitud, no medición.

**R5 — Agua: NDWI = (B3 − B8)/(B3 + B8) sobre compuestos mensuales medianos.**
Umbral por defecto NDWI > 0. Área = máscara × `ee.Image.pixelArea()` / 1e6 (km²), escala 20 m. Calidad de agua (NDTI, TSS Nechad 2010 mono-banda B4) **solo** sobre píxeles de agua. TSS sin calibración local es **relativo**.

**R6 — Toda cifra viaja con su pedigrí.**
Cada fila exportada lleva metadatos: fórmula, umbral, escala, máscara aplicada. Un CSV de áreas sin la fórmula NDWI y el umbral es inauditable.

**R7 — Tendencias: Mann-Kendall + Theil-Sen, no regresión lineal simple.**
Las series hidrológicas violan los supuestos de OLS (no-normalidad, outliers, autocorrelación). MK para significancia, Theil-Sen para magnitud. Se reporta p-valor, pendiente con unidades y *n*. Anualización sin prorrateo: acumulativas exigen 12 meses válidos; promedios, al menos 10.

**R8 — InSAR: la métrica de alerta es la tasa de cambio, no la velocidad absoluta.**
En cuencas con señal regional (uplift o subsidencia tectónica/de cuenca), la velocidad absoluta de un punto mezcla el fondo regional con la deformación local. Alerta = velocidad en ventana reciente − velocidad en ventana baseline. Declarar siempre: punto/píxeles de referencia, ventanas usadas y magnitud de la señal regional.

**R9 — Comparación multitemporal solo entre productos comparables.**
JRC Monthly Water History (Landsat, 30 m, 1984-2021) no se mezcla en una misma serie con NDWI/Sentinel-2 (20 m, 2017+): sensores, algoritmos y resoluciones distintos. Se muestran como series paralelas con la discontinuidad explícita.

**R10 — Validación contra benchmark independiente antes de publicar.**
Todo pipeline nuevo se corre sobre una cuenca con estudio hidrológico de referencia (consultora independiente, paper) y se comparan precipitación media anual, PET y temperatura media. Divergencia > 15 % sin explicación → no se publica. Nada generado por IA se publica sin revisión humana.

## Constantes de referencia

| Parámetro | Valor | Nota |
|---|---|---|
| `CLOUD_PROB_MAX` | 40 % | s2cloudless |
| `NDWI_THRESHOLD` | 0.0 | agua si NDWI > umbral |
| `SCALE_WATER` | 20 m | reducciones NDWI/calidad |
| `SCALE_CHIRPS` | 5566 m | nativa ~0.05° |
| `SCALE_ERA5` | 11132 m | nativa ~0.1° |
| `SCALE_JRC` | 30 m | nativa Landsat |

## Convenciones de código

- Constantes metodológicas en **un solo bloque** al inicio (fuente única de verdad para UI y export).
- Nota metodológica al pie de cada gráfico.
- Exportación pensada para ingesta posterior (CSV → Parquet, timestamps UTC).
- SRID explícito en todo producto espacial (para Argentina: POSGAR 2007, EPSG:5343–5349 según faja).
- Niveles de confianza explícitos en los informes: **DATO** (con fuente) · **INFERENCIA** (deducción razonada) · **HIPÓTESIS** (a verificar).
