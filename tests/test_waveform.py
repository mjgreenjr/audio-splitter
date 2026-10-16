import numpy as np

from core.waveform import min_max_envelope

SR = 44100


def test_long_audio_is_reduced_to_two_points_per_bucket():
    data = np.random.default_rng(0).uniform(-1, 1, SR * 10).astype(np.float32)
    t, y = min_max_envelope(data, SR, buckets=2000)
    assert len(t) == len(y) == 4000
    assert np.all(np.diff(t) >= 0)  # time never goes backwards


def test_last_time_is_the_exact_duration():
    data = np.zeros(SR * 3 + 17, dtype=np.float32)
    t, _ = min_max_envelope(data, SR)
    assert t[-1] == len(data) / SR * 1000


def test_a_single_spike_survives_the_reduction():
    data = np.zeros(SR * 10, dtype=np.float32)
    data[123456] = 0.9
    data[300001] = -0.8
    _, y = min_max_envelope(data, SR)
    assert y.max() == np.float32(0.9) and y.min() == np.float32(-0.8)


def test_bucket_min_and_max_are_the_true_extremes():
    rng = np.random.default_rng(1)
    data = rng.uniform(-1, 1, 20_000).astype(np.float32)
    _, y = min_max_envelope(data, SR, buckets=10)
    edges = np.linspace(0, len(data), 11).astype(int)
    for i in range(10):
        chunk = data[edges[i]:edges[i + 1]]
        assert y[2 * i] == chunk.min() and y[2 * i + 1] == chunk.max()


def test_stereo_is_averaged_to_mono():
    left = np.full(SR, 0.5, dtype=np.float32)
    right = np.full(SR, -0.5, dtype=np.float32)
    _, y = min_max_envelope(np.column_stack([left, right]), SR)
    assert np.allclose(y, 0.0)


def test_short_audio_is_returned_unreduced():
    data = np.linspace(-1, 1, 500, dtype=np.float32)
    t, y = min_max_envelope(data, SR)
    assert len(t) == len(y) == 500
    assert np.allclose(y, data)


def test_empty_audio():
    t, y = min_max_envelope(np.empty(0), SR)
    assert len(t) == len(y) == 0


# --- reading a file in blocks gives the same picture as reading it whole -------

def _write_flac(path, data, sr=SR):
    import soundfile as sf
    sf.write(path, data, sr, subtype="PCM_16")
    return sf.read(path, dtype="float32")[0]   # exactly what the file decodes to


def test_envelope_from_file_matches_the_whole_file_envelope(tmp_path):
    from core.waveform import envelope_from_file
    rng = np.random.default_rng(3)
    decoded = _write_flac(tmp_path / "a.flac", rng.uniform(-1, 1, (SR * 7 + 123, 2)))
    want_t, want_y = min_max_envelope(decoded, SR)
    for block in (1 << 20, 50_000, 3_000):   # one block, a few, one per bucket or two
        t, y = envelope_from_file(tmp_path / "a.flac", block_frames=block)
        assert np.array_equal(t, want_t) and np.array_equal(y, want_y)


def test_envelope_from_file_handles_mono_and_short_files(tmp_path):
    from core.waveform import envelope_from_file
    decoded = _write_flac(tmp_path / "m.flac", np.sin(np.arange(SR * 3) / 50))
    t, y = envelope_from_file(tmp_path / "m.flac", block_frames=70_000)
    want_t, want_y = min_max_envelope(decoded, SR)
    assert np.array_equal(t, want_t) and np.array_equal(y, want_y)
    short = _write_flac(tmp_path / "s.flac", np.linspace(-0.5, 0.5, 900))
    t, y = envelope_from_file(tmp_path / "s.flac")
    assert len(t) == len(y) == 900 and np.allclose(y, short)


def test_envelope_from_file_stops_when_asked(tmp_path):
    import pytest
    from core.waveform import Cancelled, envelope_from_file
    _write_flac(tmp_path / "a.flac", np.zeros(SR * 5))
    with pytest.raises(Cancelled):
        envelope_from_file(tmp_path / "a.flac", cancelled=lambda: True)


def test_envelope_from_file_rejects_something_that_is_not_audio(tmp_path):
    import pytest
    from core.waveform import envelope_from_file
    (tmp_path / "x.flac").write_bytes(b"not audio")
    with pytest.raises(Exception):
        envelope_from_file(tmp_path / "x.flac")
