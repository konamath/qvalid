"""Minimal deterministic SVG primitives for the embedded charts.

Written by hand rather than delegated to a plotting library, for two reasons
that are worth stating because the trade off is real.

Determinism. ``05`` requires two runs over the same input with the same seed to
produce byte identical reports. A matplotlib SVG embeds a ``dc:date`` element in
its metadata, so the file would differ on every run while nothing in the calling
code looked wrong. Getting that right means configuring the library carefully
and hoping a future version does not add another timestamp. Emitting the markup
here means the property holds by construction.

Dependencies. ``pyproject.toml`` does not list matplotlib, and adding a plotting
stack for three charts is a poor trade in a library whose test suite is meant to
be fast and offline.

The cost is that these charts are plain and that this module does not scale to
anything more elaborate. That is accepted: the JSON serialisation is the
reference output, and an interactive front end after v1.0 renders from it rather
than from here.

Every number that reaches the markup goes through :func:`_format`, which rounds
to a fixed number of decimals. Without it the coordinates would carry the full
repr of a float and a change in the seventeenth digit would change the file.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

import numpy as np

__all__ = [
    "CHART_HEIGHT",
    "CHART_WIDTH",
    "bar_chart",
    "escape",
    "histogram",
    "line_chart",
]

CHART_WIDTH = 720
CHART_HEIGHT = 260
_MARGIN_LEFT = 64
_MARGIN_RIGHT = 16
_MARGIN_TOP = 24
_MARGIN_BOTTOM = 36
_COORDINATE_DECIMALS = 3

_INK = "#1a1a1a"
_MUTED = "#8a8a8a"
_GRID = "#e4e4e4"
_ACCENT = "#1f4e79"
_WARN = "#a33"


def escape(text: str) -> str:
    """Escape text for inclusion in markup, quotes included."""
    return html.escape(str(text), quote=True)


def _format(value: float) -> str:
    """Round a coordinate to a fixed number of decimals, deterministically.

    ``-0.0`` is normalised to ``0.0``: the two are equal as numbers but differ
    as strings, and a sign that depends on the direction a value was approached
    from would break the byte for byte criterion for no reason.
    """
    rounded = round(float(value), _COORDINATE_DECIMALS)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:.{_COORDINATE_DECIMALS}f}"


def _label(value: float) -> str:
    """Format an axis label with a fixed number of significant digits."""
    if value == 0.0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.2f}k"
    if magnitude >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _frame(title: str, x_label: str, y_label: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_WIDTH}" '
        f'height="{CHART_HEIGHT}" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'role="img" aria-label="{escape(title)}">',
        f'<rect width="{CHART_WIDTH}" height="{CHART_HEIGHT}" fill="#ffffff"/>',
        f'<text x="{_MARGIN_LEFT}" y="16" font-family="Georgia, serif" font-size="12" '
        f'fill="{_INK}">{escape(title)}</text>',
        f'<text x="{CHART_WIDTH - _MARGIN_RIGHT}" y="{CHART_HEIGHT - 8}" '
        f'text-anchor="end" font-family="Georgia, serif" font-size="10" '
        f'fill="{_MUTED}">{escape(x_label)}</text>',
        f'<text x="4" y="{_MARGIN_TOP - 8}" font-family="Georgia, serif" font-size="10" '
        f'fill="{_MUTED}">{escape(y_label)}</text>',
    ]


def _axes(low: float, high: float) -> list[str]:
    """Horizontal grid lines with labels, at five levels."""
    plot_top = _MARGIN_TOP
    plot_bottom = CHART_HEIGHT - _MARGIN_BOTTOM
    span = high - low if high > low else 1.0
    parts: list[str] = []
    for step in range(5):
        fraction = step / 4.0
        y = plot_bottom - fraction * (plot_bottom - plot_top)
        value = low + fraction * span
        parts.append(
            f'<line x1="{_MARGIN_LEFT}" y1="{_format(y)}" '
            f'x2="{CHART_WIDTH - _MARGIN_RIGHT}" y2="{_format(y)}" '
            f'stroke="{_GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{_MARGIN_LEFT - 6}" y="{_format(y + 3)}" text-anchor="end" '
            f'font-family="Georgia, serif" font-size="9" fill="{_MUTED}">'
            f"{escape(_label(value))}</text>"
        )
    return parts


def line_chart(values: Sequence[float], *, title: str, x_label: str, y_label: str) -> str:
    """Render a single series as a polyline.

    Parameters
    ----------
    values : sequence of float
        At least two points, all finite.
    title, x_label, y_label : str

    Returns
    -------
    str
        A complete standalone ``<svg>`` element.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.size < 2 or not bool(np.all(np.isfinite(array))):
        raise ValueError("a line chart needs at least two finite points")
    low, high = float(array.min()), float(array.max())
    span = high - low if high > low else 1.0
    plot_top, plot_bottom = _MARGIN_TOP, CHART_HEIGHT - _MARGIN_BOTTOM
    plot_left, plot_right = _MARGIN_LEFT, CHART_WIDTH - _MARGIN_RIGHT

    steps = np.linspace(plot_left, plot_right, array.size)
    heights = plot_bottom - (array - low) / span * (plot_bottom - plot_top)
    points = " ".join(f"{_format(x)},{_format(y)}" for x, y in zip(steps, heights, strict=True))

    parts = _frame(title, x_label, y_label)
    parts.extend(_axes(low, high))
    parts.append(f'<polyline points="{points}" fill="none" stroke="{_ACCENT}" stroke-width="1.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def histogram(
    values: Sequence[float],
    *,
    title: str,
    x_label: str,
    y_label: str,
    marker: float | None = None,
    marker_label: str = "observed",
    n_bins: int = 40,
) -> str:
    """Render a histogram, optionally with a vertical marker.

    Parameters
    ----------
    values : sequence of float
    marker : float or None, optional
        Where to draw the vertical rule. Used to place the realised drawdown
        inside the simulated distribution, which is the reading ``02`` section 5
        asks for.
    n_bins : int, optional
        Fixed rather than derived from the data, so the picture does not change
        shape when a single path moves.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.size < 2 or not bool(np.all(np.isfinite(array))):
        raise ValueError("a histogram needs at least two finite values")
    low, high = float(array.min()), float(array.max())
    if high <= low:
        high = low + 1.0
    counts, _edges = np.histogram(array, bins=n_bins, range=(low, high))
    plot_top, plot_bottom = _MARGIN_TOP, CHART_HEIGHT - _MARGIN_BOTTOM
    plot_left, plot_right = _MARGIN_LEFT, CHART_WIDTH - _MARGIN_RIGHT
    tallest = int(counts.max()) if counts.max() > 0 else 1

    parts = _frame(title, x_label, y_label)
    parts.extend(_axes(0.0, float(tallest)))
    width = (plot_right - plot_left) / n_bins
    for index, count in enumerate(counts):
        if count == 0:
            continue
        height = count / tallest * (plot_bottom - plot_top)
        x = plot_left + index * width
        parts.append(
            f'<rect x="{_format(x)}" y="{_format(plot_bottom - height)}" '
            f'width="{_format(max(width - 1.0, 0.5))}" height="{_format(height)}" '
            f'fill="{_ACCENT}" fill-opacity="0.55"/>'
        )
    if marker is not None and np.isfinite(marker):
        position = plot_left + (float(marker) - low) / (high - low) * (plot_right - plot_left)
        position = min(max(position, plot_left), plot_right)
        parts.append(
            f'<line x1="{_format(position)}" y1="{_format(plot_top)}" '
            f'x2="{_format(position)}" y2="{_format(plot_bottom)}" '
            f'stroke="{_WARN}" stroke-width="2" stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<text x="{_format(position + 4)}" y="{_format(plot_top + 10)}" '
            f'font-family="Georgia, serif" font-size="9" fill="{_WARN}">'
            f"{escape(marker_label)} {escape(_label(float(marker)))}</text>"
        )
    parts.append(
        f'<text x="{_MARGIN_LEFT}" y="{CHART_HEIGHT - 8}" font-family="Georgia, serif" '
        f'font-size="9" fill="{_MUTED}">{escape(_label(low))} to {escape(_label(high))}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(
    labels: Sequence[str], values: Sequence[float], *, title: str, x_label: str, y_label: str
) -> str:
    """Render one bar per label, with a zero line when values straddle it.

    Used for the attribution by regime, where negative bars carry as much
    information as positive ones: a strategy whose profit in one state is
    cancelled by losses in another is a different object from one that is flat
    everywhere else.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0 or not bool(np.all(np.isfinite(array))):
        raise ValueError("a bar chart needs at least one finite value")
    if len(labels) != array.size:
        raise ValueError(f"got {len(labels)} labels for {array.size} values")
    low = min(0.0, float(array.min()))
    high = max(0.0, float(array.max()))
    span = high - low if high > low else 1.0
    plot_top, plot_bottom = _MARGIN_TOP, CHART_HEIGHT - _MARGIN_BOTTOM
    plot_left, plot_right = _MARGIN_LEFT, CHART_WIDTH - _MARGIN_RIGHT

    parts = _frame(title, x_label, y_label)
    parts.extend(_axes(low, high))
    zero = plot_bottom - (0.0 - low) / span * (plot_bottom - plot_top)
    parts.append(
        f'<line x1="{_MARGIN_LEFT}" y1="{_format(zero)}" '
        f'x2="{CHART_WIDTH - _MARGIN_RIGHT}" y2="{_format(zero)}" '
        f'stroke="{_INK}" stroke-width="1"/>'
    )
    width = (plot_right - plot_left) / array.size
    for index, value in enumerate(array):
        height = abs(value) / span * (plot_bottom - plot_top)
        x = plot_left + index * width
        y = zero - height if value >= 0.0 else zero
        parts.append(
            f'<rect x="{_format(x + 1.0)}" y="{_format(y)}" '
            f'width="{_format(max(width - 2.0, 0.5))}" height="{_format(height)}" '
            f'fill="{_ACCENT if value >= 0.0 else _WARN}" fill-opacity="0.7"/>'
        )
        parts.append(
            f'<text x="{_format(x + width / 2.0)}" y="{CHART_HEIGHT - _MARGIN_BOTTOM + 12}" '
            f'text-anchor="middle" font-family="Georgia, serif" font-size="9" '
            f'fill="{_MUTED}">{escape(labels[index])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
