# Autenticación de Google Earth Engine con Service Account (sin costo)

**Objetivo:** que un backend se autentique contra GEE de forma no interactiva usando una *service account* con clave JSON, bajo el plan de **uso no comercial gratuito** de Earth Engine.
**Tiempo estimado:** 20–30 minutos. Verificá que tu caso califica como uso no comercial; si pasa a facturar servicios a terceros, corresponde el plan comercial.

## Paso 1 — Proyecto en Google Cloud

1. Entrá a <https://console.cloud.google.com> y creá un proyecto (por ejemplo `mi-proyecto-gee`). Anotá el **Project ID** → variable `GEE_PROJECT_ID`.
2. En *APIs & Services → Library* habilitá **Google Earth Engine API**.

## Paso 2 — Registrar el proyecto en Earth Engine

1. Registrá el proyecto en <https://code.earthengine.google.com/register> y elegí **Unpaid usage** (uso no comercial).
2. Sin este paso, cualquier llamada devuelve `Not signed up for Earth Engine` aunque la API esté habilitada.

## Paso 3 — Service Account

1. *IAM & Admin → Service Accounts → Create Service Account* (por ejemplo `gee-backend`).
2. Su email → variable `GEE_SERVICE_ACCOUNT_EMAIL`.
3. Roles: **Earth Engine Resource Viewer**, **Earth Engine Resource Writer** (si exportás assets) y **Service Usage Consumer**.

## Paso 4 — Clave JSON

*Keys → Add Key → Create new key → JSON*. El archivo se descarga **una sola vez**.

> 🔒 **Reglas de seguridad (no negociables)**
> - Guardá el JSON **fuera del repositorio** (por ejemplo `~/secrets/gee-key.json`, `chmod 600`).
> - `.env` y las claves están en `.gitignore`: verificalo **antes** del primer commit.
> - Una clave subida a Git se considera comprometida aunque borres el commit: hay que rotarla (borrar la key en GCP y generar otra).

## Paso 5 — Verificación

```bash
export GEE_SERVICE_ACCOUNT_EMAIL="gee-backend@mi-proyecto-gee.iam.gserviceaccount.com"
export GEE_KEY_PATH="$HOME/secrets/gee-key.json"
export GEE_PROJECT_ID="mi-proyecto-gee"
python scripts/verify_gee_auth.py
```

Salida esperada: `OK — <n> imágenes Sentinel-2 disponibles sobre el AOI de prueba`.

## Errores frecuentes

| Error | Causa | Solución |
|---|---|---|
| `Not signed up for Earth Engine` | Proyecto no registrado en EE | Paso 2 |
| `Permission denied on resource project` | Falta *Service Usage Consumer* | Paso 3 |
| `Caller does not have required permission` | Roles de EE no asignados | Paso 3 |
| `FileNotFoundError` | `GEE_KEY_PATH` mal apuntado | Revisar la ruta |
| `invalid_grant: Invalid JWT` | Reloj desfasado o clave rotada | Sincronizar hora / regenerar clave |

## Cuotas

El plan no comercial tiene cuotas de cómputo concurrente y de requests, suficientes para NDWI/NDVI por AOI y exports puntuales. El InSAR pesado va por HyP3 + MintPy, no por GEE.
