"""Bokeh time-series charts for dashboard figures.

The chart layer is plain Python so it can be tested without a browser. It turns
the read API's bucketed ``/series/{id}/points`` rows into a single-line Bokeh
figure with a datetime x-axis and a hover tooltip. The read API returns local
timestamps (``ts``) alongside UTC instants (``ts_utc``); the site time zone is
used to anchor the local timestamps so the plotted instants are correct.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bokeh.models import ColumnDataSource, DatetimeTickFormatter, HoverTool
from bokeh.plotting import figure

from .state import DEFAULT_RANGE, RANGES

RANGE_WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
TOOLTIP_TIME_FORMAT = "%Y-%m-%d %H:%M"


def window(
    range_key: str,
    *,
    now: datetime | None = None,
    site_timezone: str,
) -> tuple[str, str]:
    """Return the naive local ``(start, end)`` strings the read API expects."""

    key = range_key if range_key in RANGES else DEFAULT_RANGE
    zone = ZoneInfo(site_timezone)
    if now is None:
        end = datetime.now(zone)
    elif now.tzinfo is None:
        end = now.replace(tzinfo=zone)
    else:
        end = now.astimezone(zone)
    end = end.replace(tzinfo=None, microsecond=0)
    start = end - RANGE_WINDOWS[key]
    return start.isoformat(), end.isoformat()


def chart_series(
    rows: list[dict[str, Any]], *, site_timezone: str
) -> tuple[list[datetime], list[float], list[str]]:
    """Return ``(times, values, labels)`` for the valid bucketed rows.

    Invalid rows are skipped rather than failing the whole figure, so one bad
    bucket cannot break a scene.
    """

    zone = ZoneInfo(site_timezone)
    times: list[datetime] = []
    values: list[float] = []
    labels: list[str] = []
    for row in rows:
        ts = row.get("ts")
        value = row.get("value")
        if not isinstance(ts, str) or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            continue
        try:
            local = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if local.tzinfo is None:
            local = local.replace(tzinfo=zone)
        else:
            local = local.astimezone(zone)
        times.append(local)
        values.append(float(value))
        labels.append(local.strftime(TOOLTIP_TIME_FORMAT))
    return times, values, labels


def build_chart(
    rows: list[dict[str, Any]],
    *,
    site_timezone: str,
    unit: str = "",
) -> Any | None:
    """Build a single-line Bokeh figure, or ``None`` when there are no points."""

    times, values, labels = chart_series(rows, site_timezone=site_timezone)
    if not times:
        return None

    source = ColumnDataSource({"time": times, "value": values, "when": labels})
    plot = figure(
        height=220,
        sizing_mode="stretch_both",
        toolbar_location=None,
        x_axis_type="datetime",
        min_border_left=8,
        min_border_right=8,
        min_border_top=8,
        min_border_bottom=8,
        outline_line_color="#d8dde6",
    )
    plot.line("time", "value", source=source, line_width=1.5, color="#1f6feb")
    plot.xaxis.formatter = DatetimeTickFormatter(
        hours="%H:%M",
        days="%b %d",
        months="%b %Y",
    )
    plot.xaxis.major_label_orientation = 0
    plot.yaxis.axis_label = unit
    plot.grid.grid_line_alpha = 0.25

    value_label = f"Value ({unit})" if unit else "Value"
    plot.add_tools(
        HoverTool(
            tooltips=[("Time", "@when"), (value_label, "@value{0.0000}")],
            mode="vline",
        )
    )
    return plot


__all__ = [
    "RANGE_WINDOWS",
    "build_chart",
    "chart_series",
    "window",
]
