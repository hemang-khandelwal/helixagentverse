"""Check chart semantics against supplied result values and real missing cases."""
import io

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.container import ErrorbarContainer

from charts.plots import plot_representation, plot_required_enrolment
from genegap.schemas import GapResult, GroupGap


@pytest.fixture
def result():
    return GapResult(
        variant_id="TEST-VARIANT", gene="TEST-GENE", mapping_version="B",
        groups=[
            GroupGap(group="under", n_trial=15.5, carrier_ratio=0.125, required_n=59, target_share=0.1),
            GroupGap(group="zero", n_trial=0, carrier_ratio=0, required_n=123, target_share=0.2),
            GroupGap(group="over", n_trial=900, carrier_ratio=1.725, required_n=84, target_share=0.3),
            GroupGap(group="missing", n_trial=12, carrier_ratio=None, required_n=None),
            GroupGap(group="thin", n_trial=8, carrier_ratio=0.8, required_n=22, insufficient_data=True),
        ],
    )


def texts(fig):
    return "\n".join([*(text.get_text() for text in fig.texts), *(text.get_text() for text in fig.axes[0].texts)])


def test_representation_plots_carrier_ratios_and_preserves_real_zero(result):
    for group in result.groups:
        group.enrolment_ratio = 91.0
        group.ci_low = 0.2
        group.ci_high = 0.7
    fig = plot_representation(result)
    ax = fig.axes[0]
    assert [bar.get_width() for bar in ax.patches] == [0.125, 0, 1.725]
    assert len(ax.get_yticklabels()) == len(result.groups)
    assert any(list(line.get_xdata()) == [1, 1] for line in ax.lines)
    assert len(ax.collections) == 1  # The genuine zero has a visible point.
    assert ax.collections[0].get_offsets()[0].tolist() == [0, 1]
    assert "N/A — target share missing" in texts(fig)
    assert "N/A — insufficient allele data" in texts(fig)
    assert not any(isinstance(container, ErrorbarContainer) for container in ax.containers)
    assert "representation-ratio intervals were not supplied" in texts(fig)


def test_required_enrolment_uses_total_values_without_subtracting_current_enrolment(result):
    fig = plot_required_enrolment(result, target_carriers=30)
    assert [bar.get_width() for bar in fig.axes[0].patches] == [59, 123, 84]
    assert "30 expected carriers" in texts(fig)
    assert "not additional recruits" in texts(fig)
    assert "not a guarantee" in texts(fig)
    assert "Total participants" in fig.axes[0].get_xlabel()
    unknown = plot_required_enrolment(result)
    assert "Expected-carrier target was not supplied" in texts(unknown)
    assert [bar.get_width() for bar in unknown.axes[0].patches] == [59, 123, 84]


@pytest.mark.parametrize("renderer", [plot_representation, plot_required_enrolment])
def test_x_linked_and_thin_data_do_not_plot_stale_estimates(result, renderer):
    result.inheritance = "x_linked"
    fig = renderer(result)
    assert not fig.axes[0].patches
    assert sum("X-linked analysis not assessed" in text.get_text() for text in fig.axes[0].texts) == len(result.groups)


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_invalid_ratios_are_rejected(value):
    result = GapResult(variant_id="TEST", groups=[GroupGap(group="test", n_trial=0, carrier_ratio=value)])
    with pytest.raises(ValueError, match="carrier_ratio.*finite and non-negative"):
        plot_representation(result)


def test_negative_required_counts_and_invalid_thresholds_are_rejected():
    result = GapResult(variant_id="TEST", groups=[GroupGap(group="test", n_trial=0, required_n=-1)])
    with pytest.raises(ValueError, match="required_n"):
        plot_required_enrolment(result)
    result.groups[0].required_n = 12
    for threshold in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="target_carriers"):
            plot_required_enrolment(result, target_carriers=threshold)


@pytest.mark.parametrize("renderer", [plot_representation, plot_required_enrolment])
def test_duplicate_group_ids_are_rejected(result, renderer):
    result.groups.append(result.groups[0].model_copy(update={"group": " UNDER "}))
    with pytest.raises(ValueError, match="unique"):
        renderer(result)


@pytest.mark.parametrize("renderer", [plot_representation, plot_required_enrolment])
@pytest.mark.parametrize("extension", ["png", "svg"])
def test_headless_exports_include_context_and_wrapped_source_notes(result, renderer, extension):
    note = "Mapping weights are PLACEHOLDER and require expert review. " * 8
    fig = renderer(result, target_label="Synthetic source population", notes=[note])
    assert isinstance(fig.canvas, FigureCanvasAgg)
    content = texts(fig)
    assert "TEST-VARIANT" in content and "TEST-GENE" in content and "Mapping: B" in content
    assert "Expert review pending" in content
    assert "Synthetic source population" in content
    assert "PLACEHOLDER" in content
    assert "\n" in fig.texts[-1].get_text()
    output = io.BytesIO()
    fig.savefig(output, format=extension)
    assert len(output.getvalue()) > 1000


def test_missing_ratio_can_coexist_with_valid_required_enrolment():
    result = GapResult(variant_id="TEST", groups=[GroupGap(
        group="off_target", n_trial=0, carrier_ratio=None, required_n=146, target_share=None,
    )])
    ratio = plot_representation(result)
    required = plot_required_enrolment(result)
    assert not ratio.axes[0].patches
    assert "target share missing" in texts(ratio)
    assert [bar.get_width() for bar in required.axes[0].patches] == [146]


def test_empty_results_remain_explicit():
    fig = plot_representation(GapResult(variant_id="TEST"))
    assert "No group results supplied" in texts(fig)
    assert not fig.axes[0].patches
