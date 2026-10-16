import numpy as np
import pytest

from ui.waveform_view import TICK_LABEL_PX, TimeAxis, WaveformWidget, format_time

# Even a bare axis is a Qt graphics item: without an application Qt aborts the process.
pytestmark = pytest.mark.usefixtures("qapp")


@pytest.mark.parametrize("ms, text", [
    (0, "0:00"), (999, "0:01"), (1000, "0:01"), (65000, "1:05"), (59999.99, "1:00"),
    (600000, "10:00"), (3599000, "59:59"), (3600000, "1:00:00"), (3725000, "1:02:05"),
    (-5, "0:00"),
])
def test_format_time(ms, text):
    assert format_time(ms) == text


def test_axis_labels_are_minutes_and_seconds():
    axis = TimeAxis("bottom")
    assert axis.tickStrings([0, 30000, 90000], 1, 30000) == ["0:00", "0:30", "1:30"]


@pytest.mark.parametrize("span_ms, expected_major_ms", [
    (3_000, 1_000),            # a 3 s clip: a label every second
    (60_000, 10_000),
    (200_000, 30_000),         # a 3:20 song: every 30 s
    (3_600_000, 600_000),      # an hour: every 10 min
])
def test_ticks_fall_on_round_times(span_ms, expected_major_ms):
    axis = TimeAxis("bottom")
    levels = axis.tickSpacing(0, span_ms, 600)
    assert levels[0] == (expected_major_ms, 0)
    assert levels[1][0] < levels[0][0] and levels[0][0] % levels[1][0] == 0   # minors divide majors


def test_labels_are_not_crowded():
    axis = TimeAxis("bottom")
    for width in (200, 600, 1400):
        major = axis.tickSpacing(0, 240_000, width)[0][0]
        pixels_per_label = width * major / 240_000
        assert pixels_per_label >= TICK_LABEL_PX * 0.5


def test_degenerate_ranges_do_not_crash():
    axis = TimeAxis("bottom")
    assert axis.tickSpacing(0, 0, 600) == []     # empty range
    assert axis.tickSpacing(0, 1000, 0) == []    # axis not laid out yet (zero width)


def test_both_waveform_kinds_use_the_time_axis(qtbot):
    for compact in (False, True):
        w = WaveformWidget(compact=compact)
        qtbot.addWidget(w)
        assert isinstance(w.plot_widget.getAxis("bottom"), TimeAxis)


def test_dimming_changes_the_curve_and_back(qtbot):
    w = WaveformWidget(compact=True)
    qtbot.addWidget(w)
    w.set_waveform_data(np.array([0.0, 1000.0]), np.array([-0.5, 0.5]))
    normal = w.curve.opts["pen"].color().getRgb()
    assert not w.is_dimmed()
    w.set_dimmed(True)
    faint = w.curve.opts["pen"].color().getRgb()
    assert w.is_dimmed() and sum(faint[:3]) < sum(normal[:3])    # darker, so less prominent
    w.set_dimmed(False)
    assert not w.is_dimmed() and w.curve.opts["pen"].color().getRgb() == normal
