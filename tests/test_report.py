import math

from analysis.report import pct, wilson


def test_wilson_known_values():
    lo, hi = wilson(5, 10)
    assert math.isclose(lo, 0.2366, abs_tol=1e-4) and math.isclose(hi, 0.7634, abs_tol=1e-4)
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and math.isclose(hi, 0.2775, abs_tol=1e-4)
    lo, hi = wilson(10, 10)
    assert math.isclose(lo, 0.7225, abs_tol=1e-4) and hi == 1.0


def test_wilson_empty():
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_percentile_nearest_rank():
    xs = list(range(1, 101))
    assert pct(xs, 0.95) == 95 and pct(xs, 0.5) == 50 and pct([3.0], 0.95) == 3.0
