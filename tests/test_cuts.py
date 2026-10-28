"""Cut placement helpers."""
import numpy as np
import pytest

from mixtaper.novelty import SR, first_attack


def mix_with_attack(at: float, seconds: float = 20.0) -> np.ndarray:
    rng = np.random.default_rng(0)
    x = rng.normal(0, 200, int(seconds * SR))                 # the previous track's quiet tail
    t = np.arange(int(0.3 * SR)) / SR
    burst = 8000 * np.sin(2 * np.pi * 220 * t) * np.exp(-t * 8)
    a = int(at * SR)
    x[a: a + len(burst)] += burst
    return x.astype("<i2")


def test_moves_to_the_first_note():
    assert first_attack(mix_with_attack(10.4), 10.0) == pytest.approx(10.4, abs=0.04)


def test_never_moves_further_than_max_delay():
    assert first_attack(mix_with_attack(12.0), 10.0) == 10.0


def test_never_moves_earlier():
    assert first_attack(mix_with_attack(9.5), 10.0) >= 10.0
