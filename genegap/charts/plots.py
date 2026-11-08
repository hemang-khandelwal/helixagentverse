"""Task 6.2: standalone charts from the existing GeneGap result schema.

The renderer formats supplied results. It does not estimate frequencies, infer
missing ratios, calculate additional recruits, or create uncertainty intervals.
"""
from __future__ import annotations

import math
import textwrap
from collections.abc import Iterable
from numbers import Real

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

from genegap.schemas import GapResult, GroupGap


_INK = "#172b3a"
_MUTED = "#526577"
_BELOW = "#b34e4e"
_ABOVE = "#287b78"
_ENROLMENT = "#3f709e"


def _wrapped(value: object, width: int = 132) -> str:
    """Wrap literal text without interpreting source content as math markup."""
    paragraphs = str(value).splitlines() or [""]
    return "\n".join(
        textwrap.fill(paragraph, width=width, break_long_words=True, break_on_hyphens=False)
        for paragraph in paragraphs
    )


def _valid_number(value: object, field: str, group: str) -> None:
    try:
        valid = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        raise ValueError(f"{field} for group {group!r} must be finite and non-negative.")


def _missing_reason(result: GapResult, group: GroupGap, field: str) -> str:
    if result.inheritance == "x_linked":
        return "X-linked analysis not assessed"
    if group.insufficient_data:
        return "insufficient allele data"
    if field == "carrier_ratio":
        if group.target_share is None:
            return "target share missing"
        if group.target_share == 0:
            return "zero target share"
        return "representation ratio unavailable"
    if group.carrier_freq == 0:
        return "carrier frequency zero"
    return "required total unavailable"


def _rows(result: GapResult, field: str) -> list[tuple[str, Real | None, str]]:
    if not isinstance(result, GapResult):
        raise ValueError("Charts require a GapResult record.")
    rows = []
    seen = set()
    for group in result.groups:
        name = group.group.strip()
        identity = name.casefold()
        if not name or identity in seen:
            raise ValueError("Group IDs must be nonblank and unique within a chart.")
        seen.add(identity)
        # A stale value on a thin-data or unsupported row must never become a
        # plotted estimate merely because the optional field is populated.
        value = getattr(group, field)
        if result.inheritance == "x_linked" or group.insufficient_data:
            value = None
        if value is not None:
            _valid_number(value, field, name)
        rows.append((name, value, _missing_reason(result, group, field) if value is None else ""))
    return rows


def _make_figure(
    result: GapResult,
    rows: list[tuple[str, Real | None, str]],
    *,
    title: str,
    explanation: str,
    notes: Iterable[str],
    target_label: str,
) -> tuple[Figure, object]:
    target = _wrapped(f"Target population: {target_label or 'not provided'}", width=112)
    context = _wrapped(
        f"Variant: {result.variant_id}  |  Gene: {result.gene or 'not provided'}  |  "
        f"Mapping: {result.mapping_version}  |  Inheritance: {result.inheritance}",
        width=112,
    )
    footer_parts = [
        explanation,
        "Self-identified trial demographics and genetic ancestry are different measures. "
        "The mapping is an assumption; estimates support research review.",
    ]
    footer_parts.extend(f"Source note: {note}" for note in notes if str(note).strip())
    footer = "\n".join(_wrapped(part) for part in footer_parts)
    header_height = 1.62 + 0.17 * (target.count("\n") + context.count("\n"))
    plot_height = max(2.25, 0.43 * len(rows) + 0.55)
    footer_height = 0.65 + 0.16 * len(footer.splitlines())
    figure_height = header_height + plot_height + footer_height
    fig = Figure(figsize=(11, figure_height), dpi=120, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.18, footer_height / figure_height, 0.75, plot_height / figure_height])

    def heading(text, distance_from_top, **kwargs):
        return fig.text(
            0.055, 1 - distance_from_top / figure_height, text,
            va="top", color=_INK, parse_math=False, **kwargs,
        )

    heading(title, 0.22, fontsize=17, fontweight="bold")
    heading(context, 0.64, fontsize=9.5, linespacing=1.3)
    target_top = 0.90 + 0.17 * context.count("\n")
    heading(target, target_top, fontsize=9.5, linespacing=1.3)
    heading(
        "Expert review pending · research estimates",
        target_top + 0.25 + 0.17 * target.count("\n"),
        fontsize=9, fontweight="bold",
    )
    fig.text(
        0.055, 0.20 / figure_height, footer, va="bottom", fontsize=8.5,
        linespacing=1.35, color=_MUTED, parse_math=False,
    )

    positions = list(range(len(rows)))
    ax.set_yticks(positions)
    ax.set_yticklabels([_wrapped(name, width=21) for name, _, _ in rows], fontsize=10, parse_math=False)
    ax.set_ylim(max(len(rows), 1) - 0.5, -0.5)
    ax.tick_params(axis="y", length=0, pad=9, colors=_INK)
    ax.tick_params(axis="x", labelsize=9, colors=_MUTED)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#e3e9ee", linewidth=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#aebdc8")
    for position, (_, value, reason) in enumerate(rows):
        if value is None:
            ax.text(
                0.015, position, f"N/A — {reason}", transform=ax.get_yaxis_transform(),
                va="center", fontsize=9, color=_MUTED, parse_math=False,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 2},
            )
    if not rows:
        ax.text(
            0.5, 0.5, "No group results supplied", transform=ax.transAxes,
            ha="center", va="center", color=_MUTED, parse_math=False,
        )
    return fig, ax


def plot_representation(
    result: GapResult, *, notes: Iterable[str] = (), target_label: str = "",
) -> Figure:
    """Plot supplied carrier-representation ratios with a parity reference at 1.

    Missing estimates retain their group row and an N/A reason. The frequency
    intervals on GroupGap are not ratio intervals and are intentionally unused.
    """
    rows = _rows(result, "carrier_ratio")
    fig, ax = _make_figure(
        result, rows,
        title="Carrier representation by ancestry group",
        explanation=(
            "Ratio = group share of expected trial carriers / group share of expected target-population carriers. "
            "Parity = 1; below 1 indicates a smaller trial carrier share than the reference. "
            "Point estimates only: representation-ratio intervals were not supplied."
        ),
        notes=notes, target_label=target_label,
    )
    present = [(position, value) for position, (_, value, _) in enumerate(rows) if value is not None]
    colors = [_BELOW if value < 1 else _ABOVE for _, value in present]
    ax.barh([position for position, _ in present], [value for _, value in present], height=0.58, color=colors)
    for (position, value), color in zip(present, colors):
        if value == 0:
            ax.scatter([0], [position], s=22, color=color, zorder=4, clip_on=False)
        ax.annotate(
            f"{value:.4g}", (value, position), xytext=(7, 0), textcoords="offset points",
            va="center", fontsize=9, color=_INK, parse_math=False,
        )
    ax.axvline(1, linestyle="--", color=_MUTED, linewidth=1.2, zorder=2)
    maximum = max([1, *(value for _, value in present)])
    ax.set_xlim(0, maximum * 1.20)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=3))
    ax.set_xlabel("Carrier representation ratio (parity = 1)", fontsize=10, labelpad=10, color=_INK)
    ax.legend(
        handles=[Patch(color=_BELOW, label="Below parity"), Patch(color=_ABOVE, label="At or above parity")],
        loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False, ncol=2, fontsize=8.5,
    )
    return fig


def plot_required_enrolment(
    result: GapResult, *, notes: Iterable[str] = (), target_label: str = "",
    target_carriers: float | None = None,
) -> Figure:
    """Plot supplied required_n as a total group size, never additional recruits."""
    rows = _rows(result, "required_n")
    if target_carriers is not None:
        _valid_number(target_carriers, "target_carriers", "chart metadata")
        threshold = f"Target: {target_carriers:g} expected carriers per group."
    else:
        threshold = "Expected-carrier target was not supplied; the stored required_n values are shown unchanged."
    fig, ax = _make_figure(
        result, rows,
        title="Required total enrolment by ancestry group",
        explanation=(
            f"{threshold} Bars show the total participants required in each group, not additional recruits. "
            "The target is an expected carrier count, not a guarantee or a statistical-power calculation."
        ),
        notes=notes, target_label=target_label,
    )
    present = [(position, value) for position, (_, value, _) in enumerate(rows) if value is not None]
    ax.barh(
        [position for position, _ in present], [value for _, value in present],
        height=0.58, color=_ENROLMENT,
    )
    for position, value in present:
        if value == 0:
            ax.scatter([0], [position], s=22, color=_ENROLMENT, zorder=4, clip_on=False)
        ax.annotate(
            str(value), (value, position), xytext=(7, 0), textcoords="offset points",
            va="center", fontsize=9, color=_INK, parse_math=False,
        )
    maximum = max([1, *(value for _, value in present)])
    ax.set_xlim(0, maximum * 1.20)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=6, integer=True, min_n_ticks=2))
    ax.set_xlabel("Total participants in the ancestry group", fontsize=10, labelpad=10, color=_INK)
    return fig
