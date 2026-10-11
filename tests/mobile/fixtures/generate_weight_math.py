"""Synthetic reference fits from Weight_graph's statsmodels/scipy algorithms.

Run manually with a Python environment containing numpy, scipy and statsmodels.
The committed JSON keeps ordinary mobile tests independent of Python extras.
No personal measurement data is read.
"""

import json
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator
from statsmodels.nonparametric.smoothers_lowess import lowess


def main():
    rng = np.random.default_rng(2026)
    fitted = []
    for label, xs, span in [
        ("regular", np.arange(150.0), 0.15),
        ("regular_default_span", np.arange(150.0), 0.04),
        ("irregular", np.cumsum(rng.choice([0.05, 0.1, 0.3, 1, 5, 20], 150)), 0.15),
        ("irregular_default_span", np.cumsum(rng.choice([0.05, 0.1, 0.3, 1, 5, 20], 150)), 0.04),
        ("two", np.array([0.0, 7.0]), 0.04),
        ("three", np.array([0.0, 0.25, 20.0]), 0.04),
    ]:
        ys = 80 + np.sin(xs / 9) + rng.normal(0, 0.25, len(xs))
        fitted.append({
            "name": label, "days": xs.tolist(), "weights": ys.tolist(), "span": span,
            "expected": lowess(ys, xs, frac=span, it=0, return_sorted=False).tolist(),
        })
    interpolated = []
    for label, xs, ys in [
        ("monotone_irregular", [0, 0.1, 7, 100, 400], [60, 62, 65, 66, 80]),
        ("turning_points", [0, 10, 12, 120, 300], [65, 80, 70, 82, 72]),
        ("two", [0, 500], [60, 80]),
        ("three", [0, 5, 80], [60, 90, 75]),
        ("constant", [0, 1, 70, 800], [80, 80, 80, 80]),
    ]:
        samples = 73
        query = np.linspace(xs[0], xs[-1], samples)
        interpolated.append({
            "name": label, "days": xs, "weights": ys, "samples": samples,
            "expected": PchipInterpolator(xs, ys)(query).tolist(),
        })
    dense_x = np.arange(100.0)
    dense_y = 80 + np.sin(dense_x / 4)
    smooth = lowess(dense_y, dense_x, frac=0.2, it=0, return_sorted=False)
    historical_x = np.array([-1500.0, -800.0, -100.0])
    historical_y = np.array([60.0, 72.0, 78.0])
    knots_x = np.concatenate([historical_x, dense_x[:20]])
    knots_y = np.concatenate([historical_y, smooth[:20]])
    reference = {
        "reference": "statsmodels.lowess(frac=span,it=0); scipy.PchipInterpolator",
        "loess": fitted, "pchip": interpolated,
        "historic": {
            "dense_days": dense_x.tolist(), "dense_weights": dense_y.tolist(),
            "historic_days": historical_x.tolist(), "historic_weights": historical_y.tolist(),
            "span": 0.2,
            "expected": PchipInterpolator(knots_x, knots_y)(np.linspace(historical_x[0], dense_x[0], 120)).tolist(),
            "dense_expected": smooth.tolist(),
        },
    }
    Path(__file__).with_name("weight_math.json").write_text(json.dumps(reference, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
