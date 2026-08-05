"""The claims ``pyproject.toml`` makes, checked against the code that has to honour them.

Packaging metadata is the one part of a project nothing exercises. It drifts
silently, and the drift is charged to whoever installs the package rather than
to whoever wrote it: this project shipped ``statsmodels``, ``pyarrow`` and
``duckdb`` as hard dependencies for nine versions without importing any of
them. See D044.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from qvalid import __version__

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "qvalid"

#: Distribution name to the name it is imported under, where the two differ.
IMPORT_NAME = {"pyyaml": "yaml"}


@pytest.fixture(scope="module")
def project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return dict(tomllib.load(handle)["project"])


def imported_top_level_modules() -> set[str]:
    """Every top level module ``src/qvalid`` imports, from the syntax tree."""
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


class TestDeclaredDependenciesAreRealOnes:
    """D044. A dependency nobody imports is a download nobody needed."""

    def test_every_declared_dependency_is_imported(self, project: dict[str, object]) -> None:
        imported = imported_top_level_modules()
        unused = []
        for requirement in project["dependencies"]:  # type: ignore[union-attr]
            distribution = (
                str(requirement).split(">")[0].split("=")[0].split("[")[0].strip().lower()
            )
            module = IMPORT_NAME.get(distribution, distribution)
            if module not in imported:
                unused.append(distribution)
        assert unused == [], (
            f"declared but never imported: {unused}. Every name in the dependency "
            "list is charged to whoever installs the package. See D044."
        )

    def test_no_third_party_module_is_imported_without_being_declared(
        self, project: dict[str, object]
    ) -> None:
        """The other direction, which fails at install time on someone else's machine."""
        declared = {
            IMPORT_NAME.get(
                str(r).split(">")[0].split("=")[0].split("[")[0].strip().lower(),
                str(r).split(">")[0].split("=")[0].split("[")[0].strip().lower(),
            )
            for r in project["dependencies"]  # type: ignore[union-attr]
        }
        import sys

        undeclared = {
            module
            for module in imported_top_level_modules()
            if module not in declared
            and module != "qvalid"
            and module not in sys.stdlib_module_names
        }
        assert undeclared == set(), f"imported but not declared: {sorted(undeclared)}"


class TestTheMetadataMatchesTheCode:
    def test_the_version_is_declared_in_exactly_one_place_that_agrees(
        self, project: dict[str, object]
    ) -> None:
        """Two versions that disagree make the provenance field in the report a lie."""
        assert project["version"] == __version__

    def test_the_licence_file_the_metadata_points_at_exists(
        self, project: dict[str, object]
    ) -> None:
        assert project["license"] == "MIT"
        for name in project["license-files"]:  # type: ignore[union-attr]
            assert (ROOT / str(name)).is_file()

    def test_the_readme_the_metadata_points_at_exists(self, project: dict[str, object]) -> None:
        assert (ROOT / str(project["readme"])).is_file()

    def test_the_console_script_points_at_something_callable(self) -> None:
        from qvalid.cli import main

        assert callable(main)
