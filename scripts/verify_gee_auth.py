"""
Verifica que las credenciales de Google Earth Engine funcionan.

Variables de entorno requeridas:
    GEE_SERVICE_ACCOUNT_EMAIL   email de la service account
    GEE_KEY_PATH                ruta al JSON de la clave (FUERA del repo)
    GEE_PROJECT_ID              Project ID de Google Cloud

Uso:  python scripts/verify_gee_auth.py
"""

from __future__ import annotations

import os
import sys

import ee


def main() -> int:
    missing = [v for v in ("GEE_SERVICE_ACCOUNT_EMAIL", "GEE_KEY_PATH", "GEE_PROJECT_ID")
               if not os.environ.get(v)]
    if missing:
        print(f"Faltan variables de entorno: {', '.join(missing)}")
        return 2

    key_path = os.environ["GEE_KEY_PATH"]
    if not os.path.isfile(key_path):
        print(f"No existe el archivo de clave: {key_path}")
        return 2

    credentials = ee.ServiceAccountCredentials(
        os.environ["GEE_SERVICE_ACCOUNT_EMAIL"], key_path)
    ee.Initialize(credentials, project=os.environ["GEE_PROJECT_ID"])

    # Prueba de vida: imágenes Sentinel-2 sobre un punto de la Puna.
    punto = ee.Geometry.Point([-66.9, -24.2])
    n = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
         .filterBounds(punto)
         .filterDate("2025-01-01", "2025-06-30")
         .size()
         .getInfo())
    print(f"OK — {n} imágenes Sentinel-2 disponibles sobre el AOI de prueba")
    return 0


if __name__ == "__main__":
    sys.exit(main())
