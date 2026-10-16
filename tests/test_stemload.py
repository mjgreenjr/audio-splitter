import numpy as np
import pytest
import soundfile as sf

from core.stemload import DEFAULT_TIMEOUT_S, StemLoadThread, normalize_timeout


@pytest.mark.parametrize("value, expected", [
    (60, 60.0), ("45", 45.0), (0.5, 0.5), (3600, 3600.0),
    (0, DEFAULT_TIMEOUT_S), (-5, DEFAULT_TIMEOUT_S), ("soon", DEFAULT_TIMEOUT_S),
    (None, DEFAULT_TIMEOUT_S), (float("inf"), DEFAULT_TIMEOUT_S), (float("nan"), DEFAULT_TIMEOUT_S),
])
def test_normalize_timeout(value, expected):
    assert normalize_timeout(value) == expected


def _stem(path, seconds=2, sr=22050):
    sf.write(path, np.sin(np.arange(sr * seconds) / 30).astype("float32") * 0.4, sr)
    return str(path)


def test_loader_returns_every_readable_file_and_skips_the_rest(qtbot, tmp_path):
    good = [_stem(tmp_path / "a.flac"), _stem(tmp_path / "b.flac")]
    bad = tmp_path / "broken.flac"
    bad.write_bytes(b"not audio")
    th = StemLoadThread([good[0], str(bad), good[1]])
    with qtbot.waitSignal(th.loaded, timeout=10000) as got:
        th.start()
    th.wait()
    (result,) = got.args
    assert [p for p, _, _ in result] == good
    for _, times, amp in result:
        assert len(times) == len(amp) > 0


def test_a_cancelled_loader_says_nothing(qtbot, tmp_path):
    th = StemLoadThread([_stem(tmp_path / "a.flac")])
    seen = []
    th.loaded.connect(seen.append)
    th.cancel()
    th.run()          # synchronously: it must return without emitting
    assert seen == []
