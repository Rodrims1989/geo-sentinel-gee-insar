"""
Demo con datos SINTÉTICOS (no hay datos reales en este repositorio).

Escenario: uplift regional de +8 mm/año en todo el dominio, y un parche local
que empieza a hundirse a -15 mm/año recién en la ventana reciente.

  - La velocidad absoluta del parche reciente (+8 - 15 = -7 mm/año) es
    ambigua: el uplift regional la disimula.
  - La tasa de cambio referenciada aísla el cambio local (~ -15 mm/año).

Uso:  python insar/examples/synthetic_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from insar.velocity import alert_mask, describe, rate_of_change  # noqa: E402


def make_stack(seed: int = 0):
    rng = np.random.default_rng(seed)
    t = 2019.0 + np.arange(72) / 12.0
    h = w = 40
    regional = 8.0 * (t - t[0])[:, None, None] * np.ones((72, h, w))
    local = np.zeros_like(regional)
    mask = np.zeros((h, w), bool)
    mask[15:25, 15:25] = True
    t_break = 2023.0
    after = np.clip(t - t_break, 0, None)[:, None, None]
    local[:, mask] = (-15.0 * after[:, 0, 0])[:, None]
    noise = rng.normal(0, 1.5, regional.shape)
    stack = regional + local + noise
    stack[rng.random(stack.shape) < 0.05] = np.nan  # 5 % de huecos
    return stack, t, mask


def main() -> None:
    stack, t, patch = make_stack()
    ref = np.zeros(patch.shape, bool)
    ref[:8, :8] = True  # esquina estable usada como referencia

    res = rate_of_change(
        stack, t, baseline=(2019.0, 2022.9), recent=(2023.0, 2024.9),
        reference_mask=ref,
    )
    print(describe(res))
    print(f"delta_v parche : {np.nanmedian(res.delta_v[patch]):+.1f} mm/año (esperado ~ -15)")
    print(f"delta_v fondo  : {np.nanmedian(res.delta_v[~patch]):+.1f} mm/año (esperado ~ 0)")
    print(f"píxeles en alerta (|dv|>=8): {int(alert_mask(res, 8).sum())} de {patch.size}")


if __name__ == "__main__":
    main()
