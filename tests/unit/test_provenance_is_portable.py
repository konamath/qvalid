"""D050. The provenance hash has to mean the same thing on every machine.

The CI matrix failed on Windows and nowhere else: git checks out text with
CRLF there, the bytes of the run configuration change, and ``config_sha256``
changed with them. A provenance field whose value depends on the checkout
cannot answer "did we run the same configuration", which is the only question
it exists to answer.

This is not a Windows quirk to be worked around. Anyone who edits a
configuration in a Windows editor and sends it to a colleague on Linux hits the
same thing, with no CI to tell them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qvalid.pipeline import sha256_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def three_line_endings(tmp_path: Path) -> dict[str, Path]:
    """The same content, written the way each platform writes it."""
    content = "seed: 20260804\nn_paths: 100\ninitial_capital: 100000.0\n"
    written = {}
    for name, ending in (("unix", "\n"), ("windows", "\r\n"), ("classic_mac", "\r")):
        path = tmp_path / f"{name}.yaml"
        path.write_bytes(content.replace("\n", ending).encode("utf-8"))
        written[name] = path
    return written


class TestTheHashSurvivesTheCheckout:
    def test_the_same_configuration_hashes_the_same_on_every_platform(
        self, three_line_endings: dict[str, Path]
    ) -> None:
        """The whole bug. Before the fix these were three different hashes."""
        digests = {name: sha256_of(path) for name, path in three_line_endings.items()}
        assert len(set(digests.values())) == 1, digests

    def test_the_bytes_really_do_differ(self, three_line_endings: dict[str, Path]) -> None:
        """Otherwise the test above would be passing for the wrong reason."""
        raw = {name: path.read_bytes() for name, path in three_line_endings.items()}
        assert len(set(raw.values())) == 3

    def test_a_real_difference_still_changes_the_hash(self, tmp_path: Path) -> None:
        """Normalising a line ending must not normalise away the content."""
        one = tmp_path / "one.yaml"
        two = tmp_path / "two.yaml"
        one.write_text("seed: 20260804\n", encoding="utf-8")
        two.write_text("seed: 20260805\n", encoding="utf-8")
        assert sha256_of(one) != sha256_of(two)

    def test_a_trailing_newline_still_counts(self, tmp_path: Path) -> None:
        """It is content, not a platform convention, and the two are different."""
        with_newline = tmp_path / "with.csv"
        without = tmp_path / "without.csv"
        with_newline.write_bytes(b"a,b\n1,2\n")
        without.write_bytes(b"a,b\n1,2")
        assert sha256_of(with_newline) != sha256_of(without)

    def test_the_hashes_in_the_reference_are_the_hashes_of_the_fixtures(self) -> None:
        """Pins the whole chain: fixture bytes, hash rule, committed report.

        Discovered while fixing this: ``trades_long.csv`` carries CRLF in the
        repository, which is why Windows disagreed about the configuration and
        **not** about the log. The log was CRLF on both platforms, so its hash
        already matched by accident. ``.gitattributes`` now marks CSV as binary
        so a fixture is byte identical on every checkout, and the normalisation
        makes the hash independent of the convention either way.
        """
        reference = json.loads((FIXTURES / "expected_report.json").read_text(encoding="utf-8"))
        assert (
            sha256_of(FIXTURES / "run_config_full.yaml")
            == (reference["provenance"]["config_sha256"])
        )
        assert sha256_of(FIXTURES / "trades_long.csv") == reference["provenance"]["input_sha256"]

    def test_the_versioned_configuration_is_checked_out_with_lf(self) -> None:
        """``.gitattributes`` says ``eol=lf``. This is that rule, asserted."""
        assert b"\r" not in (FIXTURES / "run_config_full.yaml").read_bytes()
