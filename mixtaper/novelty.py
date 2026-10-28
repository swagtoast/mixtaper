"""Placing cut points inside a DJ transition.

Recognition tells us *which* tracks play and roughly *when* each one is dominant; the exact
cut inside the blend is chosen from the audio itself: the moment where the spectral
balance changes the most (bass swap, new groove coming in), then snapped onto the nearest
kick so every track starts on the beat.
"""

from __future__ import annotations

import numpy as np

SR = 16000
N_FFT = 2048
HOP = 512
_BANDS = [30, 60, 120, 250, 500, 1000, 2000, 4000, 7900]
_WEIGHTS = np.array([2.0, 2.0, 1.6, 1.0, 1.0, 1.0, 0.9, 0.8])


def _band_energies(x: np.ndarray, n_fft: int, hop: int, bands: list[int]) -> np.ndarray | None:
    n = 1 + (len(x) - n_fft) // hop
    if n < 4:
        return None
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(bands[:-1], bands[1:])]
    out = np.empty((n, len(masks)), dtype=np.float64)
    step = 2048  # frames per block keeps memory flat on long stretches
    for s in range(0, n, step):
        rows = np.arange(s, min(n, s + step))
        idx = np.arange(n_fft)[None, :] + hop * rows[:, None]
        spec = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        out[rows] = np.stack([spec[:, m].sum(1) for m in masks], 1)
    return out


def novelty_curve(pcm: np.ndarray, lo: float, hi: float, half: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (times, novelty) for frame centers within [lo, hi]."""
    a = max(0, int((lo - half - 1) * SR))
    b = min(len(pcm), int((hi + half + 1) * SR))
    x = np.asarray(pcm[a:b], dtype=np.float32) / 32768.0
    E = _band_energies(x, N_FFT, HOP, _BANDS)
    if E is None:
        return np.array([]), np.array([])
    F = np.log10(E + 1e-7)
    F = (F - F.mean(0)) / (F.std(0) + 1e-6)
    W = max(2, int(half * SR / HOP))
    T = F.shape[0]
    if T <= 2 * W:
        return np.array([]), np.array([])
    C = np.vstack([np.zeros((1, F.shape[1])), np.cumsum(F, 0)])
    i = np.arange(W, T - W)
    left = (C[i] - C[i - W]) / W
    right = (C[i + W] - C[i]) / W
    nov = np.sqrt((((right - left) * _WEIGHTS) ** 2).sum(1))
    times = (a + i * HOP + N_FFT / 2) / SR
    mask = (times >= lo) & (times <= hi)
    return times[mask], nov[mask]


def snap_to_onset(pcm: np.ndarray, t: float, radius: float = 0.35) -> float:
    """Move t onto the strongest low-frequency attack (kick) within ±radius seconds."""
    n_fft, hop = 1024, 128
    a = max(0, int((t - radius) * SR) - n_fft)
    b = min(len(pcm), int((t + radius) * SR) + n_fft)
    x = np.asarray(pcm[a:b], dtype=np.float32) / 32768.0
    E = _band_energies(x, n_fft, hop, [35, 160])
    if E is None:
        return t
    e = np.log10(E[:, 0] + 1e-7)
    flux = np.maximum(0.0, np.diff(e, prepend=e[0]))
    centers = (a + np.arange(len(e)) * hop + n_fft / 2) / SR
    ok = np.abs(centers - t) <= radius
    if not ok.any() or flux[ok].max() <= 0.05:
        return t
    j = np.flatnonzero(ok)[np.argmax(flux[ok])]
    # The frame registering the rise is centred ~half a window after the attack begins.
    return float(centers[j] - (n_fft / 2) / SR + 0.004)


def first_attack(pcm: np.ndarray, t: float, max_delay: float = 1.5) -> float:
    """The first clear attack of a track whose recording starts at t (within max_delay).

    Recordings usually open with a fraction of a second of silence before the first note;
    in a mix the previous track's last note rings there. Returns t when nothing stands out.
    """
    n_fft, hop = 1024, 128
    a = max(0, int((t - 1.0) * SR) - n_fft)
    b = min(len(pcm), int((t + max_delay) * SR) + n_fft)
    x = np.asarray(pcm[a:b], dtype=np.float32) / 32768.0
    n = 1 + (len(x) - n_fft) // hop
    if n < 8:
        return t
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    level = np.log10(((x[idx] * np.hanning(n_fft).astype(np.float32)) ** 2).sum(1) + 1e-9)
    rise = np.diff(level)
    times = (a + np.arange(1, n) * hop + n_fft / 2) / SR
    before = np.abs(rise[(times >= t - 1.0) & (times < t)])
    window = (times >= t - 0.05) & (times <= t + max_delay)
    if not window.any() or not len(before):
        return t
    # a note starting: level jumps well above the usual frame-to-frame wobble (>= 1.5 dB per 8 ms)
    strong = window & (rise > max(0.15, 8 * np.median(before))) & (rise >= 0.5 * rise[window].max())
    if not strong.any():
        return t
    return max(t, float(times[np.flatnonzero(strong)[0]]))


def find_cut(pcm: np.ndarray, lo: float, hi: float) -> float:
    """Best cut point in [lo, hi] (seconds)."""
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo < 1.5:
        return snap_to_onset(pcm, (lo + hi) / 2, radius=min(0.35, (hi - lo) / 2 + 0.1))
    times, nov = novelty_curve(pcm, lo, hi)
    if len(nov) == 0:
        return (lo + hi) / 2
    # Mild preference for the centre of the window breaks ties between equal peaks.
    centre = (lo + hi) / 2
    span = max(1.0, hi - lo)
    score = nov * (1.0 - 0.15 * np.abs(times - centre) / span)
    t = float(times[int(np.argmax(score))])
    return min(hi, max(lo, snap_to_onset(pcm, t)))


_XF_NFFT, _XF_HOP = 4096, 2048
_XF_FREQS = np.fft.rfftfreq(_XF_NFFT, 1 / SR)
_XF_EDGES = np.geomspace(40, 7000, 25)
_XF_BANDS = [(_XF_FREQS >= a) & (_XF_FREQS < b) for a, b in zip(_XF_EDGES[:-1], _XF_EDGES[1:])]
_XF_SEL = (_XF_FREQS >= 80) & (_XF_FREQS <= 4000)
_XF_PC = (np.round(12 * np.log2(np.maximum(_XF_FREQS[_XF_SEL], 1) / 440.0)) % 12).astype(int)


def _profile(pcm: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Per-frame timbre (24 log bands) and chroma (12 pitch classes) of [lo, hi]."""
    a, b = max(0, int(lo * SR)), min(len(pcm), int(hi * SR))
    x = np.asarray(pcm[a:b], dtype=np.float32) / 32768.0
    n = 1 + (len(x) - _XF_NFFT) // _XF_HOP
    if n < 2:
        return None
    idx = np.arange(_XF_NFFT)[None, :] + _XF_HOP * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(_XF_NFFT).astype(np.float32), axis=1)) ** 2
    tim = np.log10(np.stack([S[:, m].sum(1) for m in _XF_BANDS], 1) + 1e-8)
    tim -= tim.mean(1, keepdims=True)
    Ssel = S[:, _XF_SEL]
    chroma = np.stack([Ssel[:, _XF_PC == k].sum(1) for k in range(12)], 1)
    chroma /= chroma.sum(1, keepdims=True) + 1e-9
    t = (a + np.arange(n) * _XF_HOP + _XF_NFFT / 2) / SR
    return t, tim, chroma


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def crossover_cut(
    pcm: np.ndarray, lo: float, hi: float, ref_a: tuple[float, float], ref_b: tuple[float, float]
) -> float | None:
    """Where the outgoing track (heard alone in ref_a) hands over to the incoming one (ref_b).

    Each frame in [lo, hi] is compared (nearest-neighbour cosine on timbre + chroma) with
    both references; the cut is the change point that best separates "closer to A" from
    "closer to B".
    """
    cur, ra, rb = _profile(pcm, lo, hi), _profile(pcm, *ref_a), _profile(pcm, *ref_b)
    if cur is None or ra is None or rb is None or len(cur[0]) < 4:
        return None
    t, tim, ch = cur

    def sim(f: np.ndarray, r: np.ndarray) -> np.ndarray:
        return (_unit(f) @ _unit(r).T).max(1)

    d = (sim(tim, rb[1]) - sim(tim, ra[1])) + (sim(ch, rb[2]) - sim(ch, ra[2]))
    k = max(1, int(2.0 * SR / _XF_HOP))
    d = np.convolve(d, np.ones(k) / k, mode="same")
    c = np.concatenate([[0.0], np.cumsum(d)])
    gain = (c[-1] - c) - c          # B-likeness after the split minus before it
    j = int(np.argmax(gain[1:-1])) + 1
    if gain[j] <= 0:
        return None
    return float((t[j - 1] + t[min(j, len(t) - 1)]) / 2)


def transition_cut(
    pcm: np.ndarray, lo: float, hi: float, ref_a: tuple[float, float] | None, ref_b: tuple[float, float] | None
) -> float:
    """Crossover when both sides have clean references, novelty otherwise; snapped to a kick."""
    if hi < lo:
        lo, hi = hi, lo
    t = None
    if ref_a and ref_b and hi - lo >= 1.5 and ref_a[1] <= ref_b[0] + 1:
        t = crossover_cut(pcm, lo, hi, ref_a, ref_b)
    if t is None:
        return find_cut(pcm, lo, hi)
    return min(hi, max(lo, snap_to_onset(pcm, t)))


def split_points(pcm: np.ndarray, lo: float, hi: float, k: int, min_gap: float = 90.0) -> list[float]:
    """The k strongest change points in [lo, hi], at least min_gap apart (for long ID stretches)."""
    if k <= 0:
        return []
    times, nov = novelty_curve(pcm, lo + min_gap / 2, hi - min_gap / 2)
    picked: list[float] = []
    for j in np.argsort(nov)[::-1]:
        t = float(times[j])
        if all(abs(t - p) >= min_gap for p in picked):
            picked.append(t)
            if len(picked) == k:
                break
    return sorted(snap_to_onset(pcm, t) for t in picked)
