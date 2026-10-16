# core/waveform.py
"""Reduce decoded audio to a few thousand points for drawing."""
import numpy as np

DEFAULT_BUCKETS = 2000


def min_max_envelope(data, sample_rate, buckets=DEFAULT_BUCKETS):
    """Return ``(time_ms, amplitude)`` numpy arrays for plotting ``data``.

    ``data`` is a 1-D (mono) or 2-D (frames, channels) array; channels are
    averaged. The audio is split into ``buckets`` equal slices and each slice
    contributes its minimum then its maximum, so peaks stay visible. The last
    time value is exactly the audio length, which the waveform uses as the
    duration. Audio shorter than ``2 * buckets`` samples is returned as is.
    """
    data = np.asarray(data)
    if data.ndim > 1:
        data = data.mean(axis=1)
    n = len(data)
    if n == 0:
        return np.empty(0), np.empty(0)
    duration_ms = n / sample_rate * 1000
    if n <= 2 * buckets:
        return np.arange(n) * 1000 / sample_rate, data.astype(np.float32)

    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    starts = edges[:-1]
    # reduceat gives one value per slice; every slice is non-empty because n > buckets.
    lows = np.minimum.reduceat(data, starts)
    highs = np.maximum.reduceat(data, starts)
    t = starts / sample_rate * 1000
    time_ms = np.repeat(t, 2)
    time_ms[-1] = duration_ms
    amplitude = np.empty(2 * buckets, dtype=np.float32)
    amplitude[0::2] = lows
    amplitude[1::2] = highs
    return time_ms, amplitude
