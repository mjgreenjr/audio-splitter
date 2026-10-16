# core/waveform.py
"""Reduce decoded audio to a few thousand points for drawing."""
import numpy as np
import soundfile as sf

DEFAULT_BUCKETS = 2000
# Frames read from a file at a time (about 24 s of 44.1 kHz audio): bounds memory use, and
# is how often a cancel request is noticed.
BLOCK_FRAMES = 1 << 20


class Cancelled(Exception):
    """Reading a file was stopped on request."""


def _assemble(starts, sample_rate, duration_ms, lows, highs):
    """The plot arrays: per bucket its minimum then its maximum. The last time is the duration."""
    time_ms = np.repeat(starts / sample_rate * 1000, 2)
    time_ms[-1] = duration_ms
    amplitude = np.empty(2 * len(starts), dtype=np.float32)
    amplitude[0::2] = lows
    amplitude[1::2] = highs
    return time_ms, amplitude


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
    return _assemble(starts, sample_rate, duration_ms, lows, highs)


def envelope_from_file(path, buckets=DEFAULT_BUCKETS, cancelled=None, block_frames=BLOCK_FRAMES):
    """Like ``min_max_envelope`` on the decoded file, but read in blocks.

    Memory stays at one block however long the file is, and ``cancelled()`` (when given) is
    checked between blocks: when it returns True this raises ``Cancelled``. A file that ends
    before its header says raises ``ValueError``.
    """
    with sf.SoundFile(str(path)) as f:
        n, rate = f.frames, f.samplerate
        if n <= 2 * buckets:
            return min_max_envelope(f.read(dtype="float32"), rate, buckets)
        edges = np.linspace(0, n, buckets + 1).astype(np.int64)
        lows = np.empty(buckets, dtype=np.float32)
        highs = np.empty(buckets, dtype=np.float32)
        i = 0
        while i < buckets:
            if cancelled is not None and cancelled():
                raise Cancelled()
            j = i + 1   # as many whole buckets as fit in one block
            while j < buckets and edges[j + 1] - edges[i] <= block_frames:
                j += 1
            want = int(edges[j] - edges[i])
            data = f.read(want, dtype="float32")
            if len(data) != want:
                raise ValueError(f"{path} ended early")
            mono = data.mean(axis=1) if data.ndim > 1 else data
            starts = edges[i:j] - edges[i]
            lows[i:j] = np.minimum.reduceat(mono, starts)
            highs[i:j] = np.maximum.reduceat(mono, starts)
            i = j
    return _assemble(edges[:-1], rate, n / rate * 1000, lows, highs)
