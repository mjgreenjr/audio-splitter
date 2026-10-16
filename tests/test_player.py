from pathlib import Path
import time

import pytest

from core.player import PlayerThread, _ms_to_ffplay_ss
from conftest import ffplay_pids


def test_ms_to_seconds():
    assert _ms_to_ffplay_ss(1500) == "1.500"
    assert _ms_to_ffplay_ss(-5) == "0.000"


@pytest.fixture
def player(qtbot, tone_wav):
    p = PlayerThread(str(tone_wav))
    p.start()
    yield p
    p.shutdown()


def test_duration_from_ffprobe(player):
    assert 2900 <= player.duration <= 3100


def test_play_stop_leaves_no_process(player, tone_wav, qtbot):
    player.play(0)
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    assert player.is_playing()
    player.stop()
    qtbot.waitUntil(lambda: ffplay_pids(tone_wav.name) == [], timeout=5000)
    assert not player.is_playing()


def test_never_two_ffplay_processes(player, tone_wav, qtbot):
    """Regression: rapid play/seek/play must not produce double audio."""
    for ms in (0, 500, 1000, 200, 0):
        player.play(ms)
        player.seek(ms + 100)
        assert len(ffplay_pids(tone_wav.name)) <= 1
        time.sleep(0.05)
    qtbot.wait(500)
    assert len(ffplay_pids(tone_wav.name)) <= 1


def test_pause_resume(player, tone_wav, qtbot):
    player.play(0)
    qtbot.waitUntil(player.is_playing, timeout=5000)
    player.pause()
    assert player.is_paused() and not player.is_playing()
    qtbot.waitUntil(lambda: ffplay_pids(tone_wav.name) == [], timeout=5000)
    player.resume()
    qtbot.waitUntil(player.is_playing, timeout=5000)


def test_paused_seek_does_not_autoplay(player, tone_wav, qtbot):
    player.play(0)
    qtbot.waitUntil(player.is_playing, timeout=5000)
    player.pause()
    player.seek(1500)
    qtbot.wait(500)
    assert ffplay_pids(tone_wav.name) == []
    assert not player.is_playing()


def test_finishes_naturally(player, qtbot):
    with qtbot.waitSignal(player.finished, timeout=10000):
        player.play(2000)  # ~1 s left of a 3 s file


def test_stop_does_not_emit_finished(player, qtbot):
    fired = []
    player.finished.connect(lambda: fired.append(1))
    player.play(0)
    qtbot.waitUntil(player.is_playing, timeout=5000)
    player.stop()
    qtbot.wait(800)
    assert not fired


# --- playing several stems as one ffplay (mix of the enabled stems) ---------

from core.player import mix_graph


def test_mix_graph_single_stem_has_no_amix():
    g = mix_graph(["/m/a.flac"], 2500, 10000)
    assert g == "amovie=/m/a.flac:seek_point=2.500"


def test_mix_graph_sums_levels_and_quotes_awkward_names():
    g = mix_graph(["/m/a (1).flac", "/m/it's [x], y: z;w.flac"], 0, 10000)
    assert "amix=inputs=2:normalize=0" in g
    # escaped once for the option and once for the graph
    assert r"amovie=/m/it\\\'s \[x\]\, y\\: z\;w.flac:seek_point=0.000" in g


def test_mix_graph_with_no_stems_is_silence_to_the_end():
    assert mix_graph([], 4000, 10000) == "anullsrc=r=44100:cl=stereo:d=6.000"


@pytest.fixture
def stems(tone_wav, tmp_path):
    import shutil
    paths = []
    for name in ("it's [a], b: c;d (1).wav", "second stem.wav"):
        p = tmp_path / name
        shutil.copy(tone_wav, p)
        paths.append(str(p))
    return paths


@pytest.fixture
def stem_player(qtbot, stems):
    p = PlayerThread(stems)
    p.start()
    yield p
    p.shutdown()


def test_stems_play_as_one_ffplay_from_the_offset(needle, stem_player, qtbot):
    done = []
    stem_player.finished.connect(lambda: done.append(time.time()))
    t0 = time.time()
    stem_player.play(1000)  # 3 s of audio: about 2 s remain
    qtbot.waitUntil(lambda: len(ffplay_pids(needle)) == 1, timeout=5000)
    qtbot.waitUntil(lambda: bool(done), timeout=6000)
    assert 1.3 <= done[0] - t0 <= 2.8, "seek_point was not applied"
    assert ffplay_pids(needle) == []


def test_toggling_stems_while_playing_never_makes_two_ffplays(needle, stem_player, stems, qtbot):
    errors = []
    stem_player.error.connect(errors.append)
    stem_player.play(0)
    qtbot.waitUntil(lambda: len(ffplay_pids(needle)) == 1, timeout=5000)
    for subset in ([stems[0]], stems, [stems[1]], stems):
        stem_player.set_sources(subset)
        assert len(ffplay_pids(needle)) <= 1
        qtbot.wait(150)
    qtbot.wait(500)
    assert len(ffplay_pids(needle)) == 1
    assert not errors, errors


def test_all_stems_off_plays_silence_and_still_finishes(stem_player, qtbot):
    done = []
    stem_player.finished.connect(lambda: done.append(True))
    stem_player.play(1500)
    qtbot.waitUntil(stem_player.is_playing, timeout=5000)
    stem_player.set_sources([])
    qtbot.waitUntil(lambda: bool(done), timeout=6000)


def test_toggling_while_paused_does_not_start_playback(needle, stem_player, stems, qtbot):
    stem_player.play(0)
    qtbot.waitUntil(stem_player.is_playing, timeout=5000)
    stem_player.pause()
    qtbot.waitUntil(lambda: ffplay_pids(needle) == [], timeout=5000)
    stem_player.set_sources([stems[1]])
    qtbot.wait(400)
    assert ffplay_pids(needle) == []
    assert stem_player.is_paused()


def test_set_sources_needs_a_stem_player(player):
    with pytest.raises(RuntimeError):
        player.set_sources(["x.wav"])


def test_stem_player_needs_at_least_one_file():
    with pytest.raises(ValueError):
        PlayerThread([])


# --- the position shown must not jump while ffplay restarts ------------------

def test_toggling_a_stem_does_not_make_the_position_jump(stem_player, stems, qtbot):
    """A display tick that lands while the old ffplay is being replaced must not
    add the time already played to the new start position (the marker used to
    pop forward, then snap back)."""
    stem_player.play(0)
    qtbot.waitUntil(lambda: stem_player.get_position() >= 1200, timeout=6000)
    before = stem_player.get_position()
    stem_player.set_sources([stems[0]])  # restart requested; the old ffplay is still being torn down
    stem_player._update_position()       # a UI tick landing in that window
    assert stem_player.get_position() <= before + 150, stem_player.get_position()


def test_seeking_while_playing_does_not_overshoot_the_target(stem_player, qtbot):
    stem_player.play(0)
    qtbot.waitUntil(lambda: stem_player.get_position() >= 1200, timeout=6000)
    stem_player.seek(300)
    stem_player._update_position()
    assert stem_player.get_position() <= 300 + 150, stem_player.get_position()


def test_position_keeps_advancing_after_the_restart(stem_player, stems, qtbot):
    stem_player.play(0)
    qtbot.waitUntil(lambda: stem_player.get_position() >= 800, timeout=6000)
    stem_player.set_sources([stems[0]])
    resumed_from = stem_player.get_position()
    qtbot.waitUntil(lambda: stem_player.get_position() >= resumed_from + 400, timeout=6000)


# --- volume ----------------------------------------------------------------------

def _cmdline(pid):
    return Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")


def test_normalize_volume():
    from core.player import normalize_volume
    assert [normalize_volume(v) for v in (0, 55, 100, 150, -4, 49.6)] == [0, 55, 100, 100, 0, 50]
    assert [normalize_volume(v) for v in ("loud", None, True, float("nan"))] == [100] * 4


def test_ffplay_starts_at_the_players_volume(qtbot, tone_wav):
    p = PlayerThread(str(tone_wav), volume=35)
    p.start()
    try:
        p.play(0)
        qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
        args = _cmdline(ffplay_pids(tone_wav.name)[0])
        assert args[args.index("-volume") + 1] == "35"
    finally:
        p.shutdown()


def test_changing_the_volume_while_playing_restarts_the_one_ffplay(player, tone_wav, qtbot):
    player.play(0)
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    first = ffplay_pids(tone_wav.name)[0]
    player.set_volume(20)

    def restarted():
        pids = ffplay_pids(tone_wav.name)
        return len(pids) == 1 and pids[0] != first
    qtbot.waitUntil(restarted, timeout=5000)
    args = _cmdline(ffplay_pids(tone_wav.name)[0])
    assert args[args.index("-volume") + 1] == "20" and player.volume() == 20
    assert player.is_playing()


def test_changing_the_volume_while_stopped_starts_nothing(player, tone_wav, qtbot):
    player.set_volume(10)
    qtbot.wait(300)
    assert ffplay_pids(tone_wav.name) == [] and not player.is_playing()
    player.play(0)
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    args = _cmdline(ffplay_pids(tone_wav.name)[0])
    assert args[args.index("-volume") + 1] == "10"


def test_changing_the_volume_while_paused_stays_paused_and_applies_on_resume(player, tone_wav, qtbot):
    player.play(0)
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    player.pause()
    qtbot.waitUntil(lambda: ffplay_pids(tone_wav.name) == [], timeout=5000)
    player.set_volume(40)
    qtbot.wait(300)
    assert ffplay_pids(tone_wav.name) == [] and player.is_paused()
    player.resume()
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    args = _cmdline(ffplay_pids(tone_wav.name)[0])
    assert args[args.index("-volume") + 1] == "40"
