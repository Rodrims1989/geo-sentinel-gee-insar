# Flujo de trabajo InSAR

Los trabajos pesados de InSAR **no pasan por Google Earth Engine**: se procesan con HyP3 (ASF/NASA, gratuito para Sentinel-1) y MintPy en local. GEE queda limitado al módulo óptico.

```
Sentinel-1 SLC ──► ASF HyP3 (interferogramas) ──► MintPy (timeseries.h5)
                                                        │
                                       cargar como stack (T, H, W) [mm LOS]
                                                        │
                              insar/velocity.py: linear_velocity / rate_of_change
                                                        │
                                  mapa de Δv + píxeles en alerta + pedigrí
```

## 1. Procesamiento (fuera de este repo)

1. Elegir órbita, *frame* y rango de fechas del AOI en ASF Vertex.
2. Enviar los interferogramas a HyP3 (INSAR_GAMMA) con una red de pares coherente.
3. Descargar los productos y correr MintPy (`smallbaselineApp.py`) para obtener `timeseries.h5`.

## 2. Métrica de alerta

La **velocidad absoluta** de un píxel mezcla la señal regional con la deformación local. La métrica operativa es:

```
Δv = v(ventana reciente) − v(ventana baseline)       [mm/año]
```

```python
import numpy as np
from insar.velocity import rate_of_change, alert_mask, describe

# stack: (T, H, W) en mm, NaN = sin dato · t: años decimales (T,)
res = rate_of_change(
    stack, t,
    baseline=(2019.0, 2022.9),
    recent=(2023.0, 2024.9),
    reference_mask=zona_estable,      # bool (H, W): píxeles estables de referencia
    min_obs=4,
)
print(describe(res))                  # pedigrí que debe acompañar a todo mapa
alerta = alert_mask(res, threshold_mm_yr=8.0)
```

## 3. Qué declarar siempre (R8)

- Píxeles o punto de referencia y por qué se consideran estables.
- Ventanas baseline y reciente, con número de fechas en cada una.
- Magnitud de la señal regional estimada (`regional_baseline`, `regional_recent`).
- Si no hay referenciación, decirlo: `Δv` puede incluir señal regional no estacionaria.

## 4. Cruce con el balance de la cuenca

Antes de atribuir una alerta a la operación hay que descartar la respuesta natural del terreno a las entradas y salidas de agua. `analytics/balance_insar.py` correlaciona la **tasa** mensual de deformación con la **anomalía** de P − PET desplazada 0..12 meses:

```python
from analytics.balance_insar import balance_anomaly, monthly_rate, lag_scan, best_lag, describe

anom = balance_anomaly(master.precip_mm, master.pet_oudin_mm, master.month_num)  # Master CSV de GEE
rate = monthly_rate(insar.vertical_p5_mm)                                       # serie del modo InSAR
res = lag_scan(anom, rate, max_lag=12)
print(describe(res, best_lag(res, alpha=0.05 / len(res))))                      # Bonferroni
```

- Anomalía respecto de la climatología mensual (en la Puna P − PET es negativo casi siempre).
- Tasa vs anomalía, nunca acumulado vs acumulado: dos series con tendencia correlacionan espuriamente.
- Un retardo significativo es un indicio para el modelo conceptual, no una atribución causal.

## 5. Límites

- Velocidad en dirección *line-of-sight* (LOS), no vertical: no convertir sin descomposición asc/desc.
- Píxeles con pocas observaciones válidas devuelven `NaN`; nunca se rellenan.
- Una alerta es una señal para revisar, no una conclusión de causalidad: contrastar con precipitación, niveles y operación antes de atribuir causas.

## 6. Pendiente

- Cargador de `timeseries.h5` de MintPy a `(T, H, W)`.
- Perfil de línea sobre la malla InSAR.
- Descomposición asc/desc en vertical y este-oeste.
