from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bokeh.models import GlyphRenderer, HoverTool

from transducer_dashboard import charts

SITE_TZ = "America/New_York"


def test_window_covers_each_configured_range() -> None:
    now = datetime(2026, 11, 17, 12, 30, 0)

    start, end = charts.window("24h", now=now, site_timezone=SITE_TZ)
    assert end == "2026-11-17T12:30:00"
    assert start == "2026-11-16T12:30:00"

    start, end = charts.window("7d", now=now, site_timezone=SITE_TZ)
    assert start == "2026-11-10T12:30:00"

    start, end = charts.window("30d", now=now, site_timezone=SITE_TZ)
    assert start == "2026-10-18T12:30:00"


def test_window_falls_back_to_the_default_range() -> None:
    now = datetime(2026, 11, 17, 12, 0)

    assert charts.window("nope", now=now, site_timezone=SITE_TZ) == charts.window(
        "7d", now=now, site_timezone=SITE_TZ
    )


def test_window_converts_an_aware_now_to_the_site_timezone() -> None:
    now = datetime(2026, 11, 17, 17, 0, tzinfo=ZoneInfo("UTC"))

    start, end = charts.window("24h", now=now, site_timezone=SITE_TZ)

    assert end == "2026-11-17T12:00:00"
    assert start == "2026-11-16T12:00:00"


def test_chart_series_anchors_local_timestamps_to_the_site_timezone() -> None:
    rows = [
        {"ts": "2026-11-10T00:00:00", "value": 1.5},
        {"ts": "2026-11-10T01:00:00", "value": 2},
    ]

    times, values, labels = charts.chart_series(rows, site_timezone=SITE_TZ)

    assert values == [1.5, 2.0]
    assert labels == ["2026-11-10 00:00", "2026-11-10 01:00"]
    assert times[0].utcoffset() == timedelta(hours=-5)
    assert times[0].astimezone(ZoneInfo("UTC")).hour == 5


def test_chart_series_skips_invalid_rows() -> None:
    rows = [
        {"ts": "not-a-date", "value": 1.0},
        {"ts": "2026-11-10T00:00:00", "value": "1.0"},
        {"ts": "2026-11-10T00:00:00", "value": True},
        {"ts": None, "value": 1.0},
        {"ts": "2026-11-10T02:00:00", "value": 3.0},
    ]

    times, values, _ = charts.chart_series(rows, site_timezone=SITE_TZ)

    assert values == [3.0]
    assert len(times) == 1


def test_build_chart_returns_none_without_points() -> None:
    assert charts.build_chart([], site_timezone=SITE_TZ) is None


def test_build_chart_draws_one_line_with_a_time_and_value_tooltip() -> None:
    rows = [
        {"ts": "2026-11-10T00:00:00", "value": 1.5},
        {"ts": "2026-11-10T01:00:00", "value": 2.5},
    ]

    plot = charts.build_chart(rows, site_timezone=SITE_TZ, unit="ft")

    renderers = [r for r in plot.renderers if isinstance(r, GlyphRenderer)]
    assert len(renderers) == 1
    assert renderers[0].glyph.line_width == 1.5
    hover = plot.select(HoverTool)[0]
    assert hover.tooltips == [("Time", "@when"), ("Value (ft)", "@value{0.0000}")]
    assert plot.yaxis.axis_label == "ft"


def test_build_chart_labels_the_value_without_a_unit() -> None:
    plot = charts.build_chart(
        [{"ts": "2026-11-10T00:00:00", "value": 1.5}], site_timezone=SITE_TZ
    )

    hover = plot.select(HoverTool)[0]
    assert ("Value", "@value{0.0000}") in hover.tooltips
