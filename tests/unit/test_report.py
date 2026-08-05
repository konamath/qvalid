"""Tests for the report layer, the pipeline and the command line entry point.

``TestByteForByte`` is the criterion ``05`` v0.6 states: two runs of the same
input with the same seed produce identical reports except for the execution
timestamp. It is checked on all three outputs, with no tolerance, because a
reproducibility claim with a tolerance is not a reproducibility claim.

``TestAbsenceIsNotApproval`` is the criterion ``02`` section 7 states. A section
that did not run appears in the panel with the reason, and the type system
refuses an entry that carries neither a result nor a reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from qvalid import __version__
from qvalid.cli import app
from qvalid.contracts import Basis
from qvalid.exceptions import SchemaError
from qvalid.pipeline import (
    RunConfig,
    _number,
    load_config,
    run_validation,
    sha256_of,
)
from qvalid.report.html import render_html, write_html
from qvalid.report.json import TIMESTAMP_FIELD, report_to_dict, report_to_json, write_json
from qvalid.report.latex import render_latex, write_latex
from qvalid.report.model import Evidence, EvidenceStatus, RunProvenance, ValidationReport
from qvalid.report.svg import bar_chart, histogram, line_chart

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "trades_long.csv"
CONFIG = FIXTURES / "run_config_full.yaml"
SMALL_LOG = FIXTURES / "trades_generic.csv"
SMALL_CONFIG = FIXTURES / "run_config.yaml"
STAMP = "2026-08-04T21:00:00Z"


@pytest.fixture(scope="module")
def run():
    return run_validation(LOG, CONFIG, executed_at=STAMP)


def minimal_report(panel: tuple[Evidence, ...] = ()) -> ValidationReport:
    return ValidationReport(
        provenance=RunProvenance(
            package_version="0.1.0.dev0",
            input_name="a.csv",
            input_sha256="ab" * 32,
            config_sha256="cd" * 32,
            seed=1,
            n_replications=10,
            executed_at=STAMP,
        ),
        grid={
            "period": "DAILY",
            "periods_per_year": 260.89,
            "calendar_id": "WEEKDAYS_UTC",
            "basis": "FIXED_INITIAL",
            "initial_capital": 1.0,
            "active_fraction": 1.0,
        },
        parameters={"risk_free_rate": 0.0},
        panel=panel,
    )


class TestByteForByte:
    """The criterion of ``05`` v0.6, on all three outputs."""

    def test_two_runs_produce_identical_json(self) -> None:
        first = run_validation(LOG, CONFIG, executed_at=STAMP)
        second = run_validation(LOG, CONFIG, executed_at=STAMP)
        assert report_to_json(first.report) == report_to_json(second.report)

    def test_two_runs_produce_identical_html_and_latex(self) -> None:
        first = run_validation(LOG, CONFIG, executed_at=STAMP)
        second = run_validation(LOG, CONFIG, executed_at=STAMP)
        assert render_html(first.report, charts=first.charts) == render_html(
            second.report, charts=second.charts
        )
        assert render_latex(first.report) == render_latex(second.report)

    def test_only_the_timestamp_differs_between_real_runs(self) -> None:
        """Without injecting the stamp, the two files differ in exactly one field."""
        first = json.loads(report_to_json(run_validation(LOG, CONFIG).report))
        second = json.loads(report_to_json(run_validation(LOG, CONFIG).report))
        first["provenance"][TIMESTAMP_FIELD] = "held"
        second["provenance"][TIMESTAMP_FIELD] = "held"
        assert first == second

    def test_the_written_files_are_identical(self, tmp_path: Path) -> None:
        for index in (1, 2):
            current = run_validation(LOG, CONFIG, executed_at=STAMP)
            write_json(current.report, tmp_path / f"{index}.json")
            write_html(current.report, tmp_path / f"{index}.html", charts=current.charts)
            write_latex(current.report, tmp_path / f"{index}.tex")
        for suffix in ("json", "html", "tex"):
            assert (tmp_path / f"1.{suffix}").read_bytes() == (
                tmp_path / f"2.{suffix}"
            ).read_bytes()

    def test_the_charts_are_deterministic_on_their_own(self) -> None:
        values = np.random.default_rng(1).normal(0.0, 1.0, 500)
        assert line_chart(values, title="t", x_label="x", y_label="y") == line_chart(
            values, title="t", x_label="x", y_label="y"
        )
        assert histogram(values, title="t", x_label="x", y_label="y", marker=0.5) == histogram(
            values, title="t", x_label="x", y_label="y", marker=0.5
        )
        assert bar_chart(["a", "b"], [1.0, -2.0], title="t", x_label="x", y_label="y") == (
            bar_chart(["a", "b"], [1.0, -2.0], title="t", x_label="x", y_label="y")
        )

    def test_negative_zero_does_not_leak_into_the_markup(self) -> None:
        """``-0.0`` and ``0.0`` are equal as numbers and differ as strings."""
        assert "-0.000" not in bar_chart(["a"], [0.0], title="t", x_label="x", y_label="y")


class TestAbsenceIsNotApproval:
    """The criterion of ``02`` section 7, enforced by the type rather than by review."""

    def test_a_ran_entry_needs_a_payload(self) -> None:
        with pytest.raises(ValueError, match="carries no payload"):
            Evidence(name="x", status=EvidenceStatus.RAN)

    def test_an_absent_entry_needs_a_reason(self) -> None:
        with pytest.raises(ValueError, match="carries no reason"):
            Evidence(name="x", status=EvidenceStatus.SUPPRESSED)

    def test_an_entry_cannot_carry_both(self) -> None:
        with pytest.raises(ValueError, match="also carries a reason"):
            Evidence(name="x", status=EvidenceStatus.RAN, payload={}, reason="why")
        with pytest.raises(ValueError, match="but carries a payload"):
            Evidence(name="x", status=EvidenceStatus.FAILED, reason="why", payload={})

    def test_every_panel_entry_of_a_real_run_satisfies_the_invariant(self, run) -> None:
        for entry in run.report.panel:
            assert (entry.payload is None) != (entry.reason is None)

    def test_the_deflated_sharpe_declares_that_no_correction_was_applied(self, run) -> None:
        """D004: without a declared trial count the test does not run and the report says so."""
        entry = run.report.entry("deflated_sharpe")
        assert entry.status is EvidenceStatus.NOT_REQUESTED
        assert "D004" in entry.reason or "was not declared" in entry.reason

    def test_absent_sections_are_counted_in_the_rendered_outputs(self, run) -> None:
        assert "did not run" in render_html(run.report, charts=run.charts)
        assert "did not run" in render_latex(run.report)

    def test_a_missing_section_is_different_from_an_absent_one(self, run) -> None:
        with pytest.raises(KeyError, match="not in the panel"):
            run.report.entry("a_section_that_does_not_exist")
        assert run.report.entry("deflated_sharpe").ran is False


class TestDeclaredCompleteness:
    """``01``: without these fields the report is not reproducible and is worth nothing."""

    REQUIRED_GRID = (
        "period",
        "periods_per_year",
        "calendar_id",
        "basis",
        "initial_capital",
        "active_fraction",
    )
    REQUIRED_PARAMETERS = ("risk_free_rate", "hac_bandwidth", "pnl_rtol", "confidence_level")
    REQUIRED_PROVENANCE = (
        "package_version",
        "input_sha256",
        "config_sha256",
        "seed",
        "n_replications",
        "executed_at",
    )

    def test_every_required_field_is_present(self, run) -> None:
        payload = report_to_dict(run.report)
        for key in self.REQUIRED_GRID:
            assert key in payload["grid"], key
        for key in self.REQUIRED_PARAMETERS:
            assert key in payload["parameters"], key
        for key in self.REQUIRED_PROVENANCE:
            assert key in payload["provenance"], key

    def test_the_contract_refuses_a_report_missing_a_grid_field(self) -> None:
        with pytest.raises(ValueError, match="must declare every grid field"):
            ValidationReport(
                provenance=minimal_report().provenance,
                grid={"period": "DAILY"},
                parameters={},
            )

    def test_panel_names_must_be_unique(self) -> None:
        duplicate = (
            Evidence(name="a", status=EvidenceStatus.RAN, payload={}),
            Evidence(name="a", status=EvidenceStatus.RAN, payload={}),
        )
        with pytest.raises(ValueError, match="unique names"):
            ValidationReport(
                provenance=minimal_report().provenance,
                grid=minimal_report().grid,
                parameters={},
                panel=duplicate,
            )

    def test_the_hash_identifies_the_data(self, tmp_path: Path) -> None:
        copy = tmp_path / "renamed.csv"
        copy.write_bytes(LOG.read_bytes())
        assert sha256_of(copy) == sha256_of(LOG)

    def test_the_version_is_the_package_version(self, run) -> None:
        assert run.report.provenance.package_version == __version__


class TestSerialisation:
    def test_keys_are_sorted_at_every_level(self, run) -> None:
        payload = report_to_dict(run.report)
        assert list(payload) == sorted(payload)
        assert list(payload["grid"]) == sorted(payload["grid"])
        assert list(payload["parameters"]) == sorted(payload["parameters"])

    def test_the_panel_keeps_its_order(self, run) -> None:
        """A mapping sorted by name would scramble the reading order of the report."""
        names = [entry["name"] for entry in report_to_dict(run.report)["panel"]]
        assert names == [entry.name for entry in run.report.panel]
        assert names != sorted(names)

    def test_non_finite_values_are_refused(self) -> None:
        report = minimal_report(
            (Evidence(name="x", status=EvidenceStatus.RAN, payload={"v": float("nan")}),)
        )
        with pytest.raises(ValueError, match="non finite"):
            report_to_json(report)

    def test_an_unserialisable_type_raises_rather_than_stringifying(self) -> None:
        report = minimal_report(
            (Evidence(name="x", status=EvidenceStatus.RAN, payload={"v": object()}),)
        )
        with pytest.raises(TypeError, match="not serialisable"):
            report_to_json(report)

    def test_numpy_scalars_and_arrays_survive(self) -> None:
        report = minimal_report(
            (
                Evidence(
                    name="x",
                    status=EvidenceStatus.RAN,
                    payload={"a": np.float64(1.5), "b": np.int64(2), "c": np.arange(3)},
                ),
            )
        )
        parsed = json.loads(report_to_json(report))
        assert parsed["panel"][0]["payload"] == {"a": 1.5, "b": 2, "c": [0, 1, 2]}

    def test_the_json_ends_with_a_newline(self, run) -> None:
        assert report_to_json(run.report).endswith("\n")


class TestHtmlAndLatex:
    def test_the_html_is_self_contained(self, run) -> None:
        markup = render_html(run.report, charts=run.charts)
        assert "<link" not in markup
        assert 'src="http' not in markup
        assert "@import" not in markup
        assert "<script" not in markup

    def test_the_html_embeds_the_charts(self, run) -> None:
        markup = render_html(run.report, charts=run.charts)
        assert markup.count("<svg") == len(run.charts)
        assert len(run.charts) >= 1

    def test_the_html_states_that_no_grade_is_reported(self, run) -> None:
        assert "No aggregate grade" in render_html(run.report, charts=run.charts)

    def test_latex_escapes_special_characters(self) -> None:
        report = minimal_report(
            (
                Evidence(
                    name="x",
                    status=EvidenceStatus.RAN,
                    payload={"note": "100% & $5_of#it {here} ~ ^"},
                ),
            )
        )
        rendered = render_latex(report)
        assert r"\%" in rendered
        assert r"\&" in rendered
        assert r"\_" in rendered
        assert "100% &" not in rendered

    def test_standalone_latex_wraps_the_fragment(self, run) -> None:
        assert render_latex(run.report, standalone=True).startswith("\\documentclass")
        assert not render_latex(run.report).startswith("\\documentclass")

    def test_undefined_values_are_shown_as_undefined(self) -> None:
        report = minimal_report(
            (Evidence(name="x", status=EvidenceStatus.RAN, payload={"v": None}),)
        )
        assert "undefined" in render_html(report)
        assert "undefined" in render_latex(report)


class TestPipeline:
    def test_a_small_log_reports_the_sections_it_could_not_run(self) -> None:
        """The twelve trade fixture is too sparse to resample, and the report says so."""
        small = run_validation(SMALL_LOG, SMALL_CONFIG, executed_at=STAMP)
        absent = small.report.sections_absent
        assert "resampling" in absent
        assert "risk_tail" in absent
        assert small.report.entry("calendar_metrics").ran

    def test_a_failing_section_does_not_abort_the_run(self) -> None:
        small = run_validation(SMALL_LOG, SMALL_CONFIG, executed_at=STAMP)
        assert small.report.entry("resampling").status is EvidenceStatus.FAILED
        assert len(small.report.sections_run) >= 3

    def test_the_full_run_reaches_every_section(self, run) -> None:
        for name in (
            "trade_metrics",
            "calendar_metrics",
            "grid_selection",
            "resampling",
            "risk_tail",
            "drawdown_distribution",
            "risk_of_ruin",
            "regimes",
        ):
            assert run.report.entry(name).ran, name

    def test_the_regime_section_finds_the_planted_effect(self, run) -> None:
        payload = run.report.entry("regimes").payload
        assert payload["equality_of_means_p"] < 1e-6
        assert payload["n_states"] == 9

    def test_the_verdict_refuses_to_score_an_uncorrected_strategy(self, run) -> None:
        """``02`` section 7, visible in the report the pipeline actually produces.

        The example run has a full simulation and a complete risk section, and
        still gets no certainty equivalent, because the number of configurations
        tested was never declared. A ranking that scored it anyway would launder
        the missing correction into an endorsement.
        """
        verdict = run.report.entry("verdict")
        assert verdict.status is EvidenceStatus.SUPPRESSED
        assert verdict.payload is None
        assert "not comparable" in verdict.reason
        assert verdict.observed == ["deflated_sharpe"]

    def test_the_verdict_is_suppressed_when_there_are_no_paths(self) -> None:
        small = run_validation(SMALL_LOG, SMALL_CONFIG, executed_at=STAMP)
        verdict = small.report.entry("verdict")
        assert verdict.status is EvidenceStatus.SUPPRESSED
        assert "no simulated distribution" in verdict.reason

    def test_a_declared_trial_count_still_blocks_without_the_matrix(self, tmp_path: Path) -> None:
        """Both shapes of D004 absence keep the verdict out, and for different reasons."""
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["n_trials"] = 40
        for name in ("symbology.yaml", "mapping_generic.yaml", "reference_daily.csv"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        report = run_validation(LOG, path, executed_at=STAMP).report
        assert report.entry("verdict").status is EvidenceStatus.SUPPRESSED

    def test_a_declared_shorter_requirement_list_lets_the_verdict_run(self, tmp_path: Path) -> None:
        """Shortening the list is a declaration and it shows up in the report.

        The strict list makes the verdict unreachable from a single trade log,
        because the deflated Sharpe needs a trial matrix a log cannot supply.
        A user who is comparing strategies that all lack the correction can say
        so, and the report records that they did.
        """
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["verdict_requirements"] = ["resampling"]
        for name in ("symbology.yaml", "mapping_generic.yaml", "reference_daily.csv"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        report = run_validation(LOG, path, executed_at=STAMP).report
        verdict = report.entry("verdict")
        assert verdict.status is EvidenceStatus.RAN
        assert verdict.payload["requirements"] == ["resampling"]
        assert verdict.payload["requirements_are_default"] is False
        assert verdict.payload["certainty_equivalent"] is not None
        assert any("never a grade" in w for w in verdict.warnings)
        assert report.parameters["verdict_requirements"] == ["resampling"]

    def test_the_observed_drawdown_is_placed_in_the_simulated_distribution(self, run) -> None:
        payload = run.report.entry("drawdown_distribution").payload
        assert 0.0 <= payload["observed_quantile"] <= 1.0
        assert payload["observed"] is not None


def _regime_failure(config: Path, pattern: str) -> bool:
    """A bad reference series fails **the regime section**, not the whole run.

    D053 changed this. These four cases used to raise out of
    :func:`run_validation` and take the report with them, which contradicted
    the promise in the module docstring of ``pipeline.py``: a typed failure
    becomes an :class:`Evidence` entry so the reader still gets the metrics,
    the risk section and everything else that did work.

    The refusal itself is unchanged, and is still asserted here. What moved is
    where the person meets it.
    """
    import re

    report = run_validation(LOG, config, executed_at=STAMP).report
    regimes = next(item for item in report.panel if item.name == "regimes")
    assert regimes.status is EvidenceStatus.FAILED, regimes.status
    assert re.search(pattern, regimes.reason or ""), regimes.reason
    assert any(item.name == "calendar_metrics" and item.ran for item in report.panel)
    return True


class TestConfiguration:
    def test_the_fixture_config_loads(self) -> None:
        config = load_config(CONFIG)
        assert isinstance(config, RunConfig)
        assert config.seed == 20260804

    def test_a_missing_config_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            load_config(tmp_path / "absent.yaml")

    def test_a_malformed_config_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("seed: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_config(path)

    def test_a_non_mapping_config_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="must be a mapping"):
            load_config(path)

    def test_an_unknown_key_is_refused(self, tmp_path: Path) -> None:
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["tolerance"] = 0.1
        path = tmp_path / "extra.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.raises(SchemaError, match=r"[Ee]xtra"):
            load_config(path)

    def test_the_seed_has_no_default(self, tmp_path: Path) -> None:
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        del payload["seed"]
        path = tmp_path / "noseed.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.raises(SchemaError, match="seed"):
            load_config(path)

    def test_a_reference_missing_grid_periods_is_refused(self, tmp_path: Path) -> None:
        """Aligning by position would shift every label; the file must carry timestamps."""
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        truncated = tmp_path / "reference_daily.csv"
        lines = (FIXTURES / "reference_daily.csv").read_text(encoding="utf-8").splitlines()
        truncated.write_text("\n".join(lines[:200]) + "\n", encoding="utf-8")
        for name in ("symbology.yaml", "mapping_generic.yaml"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        assert _regime_failure(path, r"missing \d+ of the \d+ grid periods")

    def test_a_reference_without_a_timestamp_column_is_refused(self, tmp_path: Path) -> None:
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        (tmp_path / "reference_daily.csv").write_text("ret\n0.1\n0.2\n", encoding="utf-8")
        for name in ("symbology.yaml", "mapping_generic.yaml"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        assert _regime_failure(path, "timestamp column")


class TestCommandLine:
    def test_validate_writes_html(self, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        result = CliRunner().invoke(
            app, ["validate", str(LOG), "--config", str(CONFIG), "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()
        assert "sections ran" in result.output

    def test_validate_writes_json_alongside(self, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        result = CliRunner().invoke(
            app,
            ["validate", str(LOG), "--config", str(CONFIG), "--out", str(out), "--also-json"],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "report.json").is_file()

    def test_an_unknown_suffix_is_refused_rather_than_guessed(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            ["validate", str(LOG), "--config", str(CONFIG), "--out", str(tmp_path / "r.pdf")],
        )
        assert result.exit_code == 2
        assert "unknown output suffix" in result.output

    def test_a_typed_error_becomes_an_exit_code(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                "validate",
                str(LOG),
                "--config",
                str(tmp_path / "absent.yaml"),
                "--out",
                str(tmp_path / "r.json"),
            ],
        )
        assert result.exit_code == 1
        assert "SchemaError" in result.output

    def test_version_prints_the_package_version(self) -> None:
        result = CliRunner().invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestSvgPrimitives:
    def test_a_line_chart_needs_two_finite_points(self) -> None:
        with pytest.raises(ValueError, match="two finite points"):
            line_chart([1.0], title="t", x_label="x", y_label="y")
        with pytest.raises(ValueError, match="two finite points"):
            line_chart([1.0, float("nan")], title="t", x_label="x", y_label="y")

    def test_a_histogram_needs_two_finite_values(self) -> None:
        with pytest.raises(ValueError, match="two finite values"):
            histogram([1.0], title="t", x_label="x", y_label="y")

    def test_a_bar_chart_needs_matching_labels(self) -> None:
        with pytest.raises(ValueError, match="labels for"):
            bar_chart(["a"], [1.0, 2.0], title="t", x_label="x", y_label="y")
        with pytest.raises(ValueError, match="at least one finite value"):
            bar_chart([], [], title="t", x_label="x", y_label="y")

    def test_a_constant_series_still_renders(self) -> None:
        assert "<svg" in line_chart([1.0, 1.0, 1.0], title="t", x_label="x", y_label="y")
        assert "<svg" in histogram([2.0, 2.0], title="t", x_label="x", y_label="y")

    def test_the_marker_is_clamped_inside_the_plot(self) -> None:
        markup = histogram([0.0, 1.0], title="t", x_label="x", y_label="y", marker=1_000.0)
        assert "stroke-dasharray" in markup

    def test_titles_are_escaped(self) -> None:
        markup = line_chart([1.0, 2.0], title="<script>", x_label="&", y_label='"')
        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup


class TestRemainingBranches:
    """Branches reachable only by construction, kept covered so they stay correct."""

    def test_a_declared_trial_count_without_the_matrix_is_still_absent(
        self, tmp_path: Path
    ) -> None:
        """D004 has two shapes of absence and the report distinguishes them."""
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["n_trials"] = 40
        payload["ruin_barrier"] = None
        for name in ("symbology.yaml", "mapping_generic.yaml", "reference_daily.csv"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        report = run_validation(LOG, path, executed_at=STAMP).report
        entry = report.entry("deflated_sharpe")
        assert entry.status is EvidenceStatus.NOT_REQUESTED
        assert "matrix of all tested" in entry.reason
        ruin = report.entry("risk_of_ruin")
        assert ruin.status is EvidenceStatus.NOT_REQUESTED
        assert "no ruin barrier was declared" in ruin.reason

    def test_a_missing_reference_file_is_refused(self, tmp_path: Path) -> None:
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        for name in ("symbology.yaml", "mapping_generic.yaml"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        assert _regime_failure(path, "reference series not found")

    def test_naive_reference_timestamps_are_refused(self, tmp_path: Path) -> None:
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        for name in ("symbology.yaml", "mapping_generic.yaml"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
        (tmp_path / "reference_daily.csv").write_text(
            "period_end,ret\n2022-01-03 21:00:00,0.01\n2022-01-04 21:00:00,0.02\n",
            encoding="utf-8",
        )
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        assert _regime_failure(path, "naive")

    def test_a_suppressed_entry_renders_its_observed_and_threshold(self) -> None:
        report = minimal_report(
            (
                Evidence(
                    name="x",
                    status=EvidenceStatus.SUPPRESSED,
                    reason="too few periods",
                    observed=40,
                    threshold=60,
                ),
            )
        )
        markup = render_html(report)
        assert "Observed 40" in markup
        assert "threshold 60" in markup

    def test_warnings_render_in_both_formats(self) -> None:
        report = minimal_report(
            (
                Evidence(
                    name="x",
                    status=EvidenceStatus.RAN,
                    payload={"v": 1.0},
                    warnings=("a caveat worth reading",),
                ),
            )
        )
        assert "a caveat worth reading" in render_html(report)
        assert "a caveat worth reading" in render_latex(report)

    def test_an_empty_payload_renders_no_table(self) -> None:
        with_payload = render_latex(
            minimal_report((Evidence(name="x", status=EvidenceStatus.RAN, payload={"v": 1.0}),))
        )
        without = render_latex(
            minimal_report((Evidence(name="x", status=EvidenceStatus.RAN, payload={}),))
        )
        assert with_payload.count("begin{table}") == without.count("begin{table}") + 1

    def test_large_axis_labels_are_abbreviated(self) -> None:
        markup = line_chart([0.0, 5_000_000.0], title="t", x_label="x", y_label="y")
        assert "M<" in markup

    def test_enums_serialise_by_value(self) -> None:
        report = minimal_report(
            (
                Evidence(
                    name="x",
                    status=EvidenceStatus.RAN,
                    payload={"basis": Basis.CURRENT_EQUITY},
                ),
            )
        )
        assert json.loads(report_to_json(report))["panel"][0]["payload"]["basis"] == (
            "CURRENT_EQUITY"
        )

    def test_numpy_scalars_and_non_finite_values_are_normalised(self) -> None:
        assert _number(np.float64(1.5)) == 1.5
        assert _number(np.int64(3)) == 3
        assert _number(float("inf")) is None
        assert _number(float("nan")) is None
        assert _number("text") == "text"
