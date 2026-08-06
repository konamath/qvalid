"""The agent's view of the cache, driven with the bytes a client sends. See D075.

D069 is why these tests are shaped this way. The browser path had never once
worked because every test called the page functions with a dictionary already
built, so the one layer between the wire and the handler was the one layer
nothing exercised. Here that layer is :func:`~qvalid.mcp.protocol.handle`, it
takes bytes and returns bytes, and it is what every test below drives.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qvalid.cli import app
from qvalid.mcp.protocol import PROTOCOL_VERSION, handle
from qvalid.mcp.tools import MAX_ROWS, TOOLS


def cache_with_a_slice(folder: Path, rows: int = 120) -> Path:
    """A cache built the only way a cache is meant to be built, by ``fetch``."""
    lines = ["date,value"]
    level = 4000.0
    for index in range(rows):
        day = dt.date(2022, 1, 3) + dt.timedelta(days=index)
        if day.weekday() < 5:
            level *= 1.0 + (0.004 if index % 3 else -0.003)
            lines.append(f"{day.isoformat()},{level:.2f}")
    source = folder / "levels.csv"
    source.write_text("\n".join(lines) + "\n")
    root = folder / "cache"
    result = CliRunner().invoke(
        app,
        [
            "fetch",
            "SP500",
            "--source",
            "file",
            "--file",
            str(source),
            "--start",
            "2022-01-03",
            "--end",
            "2022-05-02",
            "--cache",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    return root


def tools_ast() -> ast.Module:
    """The tool module parsed, so a boundary is checked by structure."""
    source = Path(__file__).resolve().parents[2] / "src/qvalid/mcp/tools.py"
    return ast.parse(source.read_text(encoding="utf-8"))


def ask(root: Path, method: str, **params: Any) -> dict[str, Any]:
    raw = json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params}).encode()
    answer = handle(raw, root)
    assert answer is not None
    return json.loads(answer)


def call(root: Path, name: str, **arguments: Any) -> dict[str, Any]:
    return ask(root, "tools/call", name=name, arguments=arguments)["result"]


class TestTheProtocolItself:
    def test_initialise_echoes_the_revision_it_speaks(self, tmp_path: Path) -> None:
        result = ask(cache_with_a_slice(tmp_path), "initialize")["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "quantify-cache"

    def test_a_notification_gets_no_answer(self, tmp_path: Path) -> None:
        """A message without an id must not be replied to. Replying is what
        makes a client wait for a response it will then fail to match."""
        raw = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        assert handle(raw, tmp_path) is None

    def test_bytes_that_are_not_json_become_a_parse_error(self, tmp_path: Path) -> None:
        answer = handle(b"{not json", tmp_path)
        assert answer is not None
        assert json.loads(answer)["error"]["code"] == -32700

    def test_a_message_that_is_not_json_rpc_is_refused(self, tmp_path: Path) -> None:
        answer = handle(json.dumps({"hello": "there"}).encode(), tmp_path)
        assert answer is not None
        assert json.loads(answer)["error"]["code"] == -32600

    def test_an_unknown_method_is_named(self, tmp_path: Path) -> None:
        assert ask(tmp_path, "resources/list")["error"]["code"] == -32601

    def test_the_id_comes_back_unchanged(self, tmp_path: Path) -> None:
        """A client matches responses to requests by it, so an invented one
        strands the caller."""
        raw = json.dumps({"jsonrpc": "2.0", "id": "abc-1", "method": "tools/list"}).encode()
        answer = handle(raw, tmp_path)
        assert answer is not None and json.loads(answer)["id"] == "abc-1"

    def test_a_refused_tool_is_a_result_and_not_a_protocol_failure(self, tmp_path: Path) -> None:
        """A protocol failure says the request was malformed; a refused call
        says it was understood and the answer is no. Collapsing them would tell
        an agent its query was wrong when the cache was simply empty."""
        result = call(tmp_path, "read_series", source="file", symbol="X", start="a", end="b")
        assert result["isError"] is True
        assert "not in the cache" in result["content"][0]["text"]


class TestWhatTheAgentCanSee:
    def test_the_catalogue_lists_every_tool_with_a_schema(self, tmp_path: Path) -> None:
        tools = ask(tmp_path, "tools/list")["result"]["tools"]
        assert {item["name"] for item in tools} == {tool.name for tool in TOOLS}
        for item in tools:
            assert item["inputSchema"]["type"] == "object"
            assert item["description"]

    def test_coverage_reports_the_slice_and_how_often_it_was_asked_for(
        self, tmp_path: Path
    ) -> None:
        root = cache_with_a_slice(tmp_path)
        first = call(root, "cache_coverage")["structuredContent"]["items"]
        assert len(first) == 1
        assert first[0]["symbol"] == "SP500"
        assert first[0]["requests"] == 1

    def test_a_second_request_shows_up_as_a_request_and_not_a_second_slice(
        self, tmp_path: Path
    ) -> None:
        """The manifest records hits as well as downloads, per D033, and the
        difference between two requests and two slices is the difference
        between a cache working and a cache failing."""
        root = cache_with_a_slice(tmp_path)
        cache_with_a_slice(tmp_path)
        items = call(root, "cache_coverage")["structuredContent"]["items"]
        assert len(items) == 1
        assert items[0]["requests"] == 2

    def test_a_series_comes_back_as_numbers(self, tmp_path: Path) -> None:
        payload = call(
            cache_with_a_slice(tmp_path),
            "read_series",
            source="file",
            symbol="SP500",
            start="2022-01-03",
            end="2022-05-02",
        )["structuredContent"]
        assert payload["n_observations"] == len(payload["values"]) == len(payload["timestamp_ns"])
        assert payload["n_observations"] > 50

    def test_and_can_come_back_as_returns(self, tmp_path: Path) -> None:
        root = cache_with_a_slice(tmp_path)
        levels = call(
            root, "read_series", source="file", symbol="SP500", start="2022-01-03", end="2022-05-02"
        )["structuredContent"]
        returns = call(
            root,
            "read_series",
            source="file",
            symbol="SP500",
            start="2022-01-03",
            end="2022-05-02",
            as_returns=True,
        )["structuredContent"]
        assert returns["n_observations"] == levels["n_observations"] - 1
        assert returns["series_id"].endswith(":returns")

    def test_the_hash_check_is_reachable(self, tmp_path: Path) -> None:
        payload = call(cache_with_a_slice(tmp_path), "verify_cache")["structuredContent"]
        assert payload["intact"] is True
        assert payload["checked"] == 1

    def test_and_it_finds_a_file_edited_by_hand(self, tmp_path: Path) -> None:
        """The guarantee D033 bought by hashing on the way in, now visible to
        the agent before a bad file reaches a report."""
        root = cache_with_a_slice(tmp_path)
        raw = next((root / "raw").iterdir())
        raw.write_text(raw.read_text() + "2030-01-01,99999.0\n")
        payload = call(root, "verify_cache")["structuredContent"]
        assert payload["intact"] is False
        assert payload["problems"]


class TestTheBoundaryIsReadOnly:
    """Not for safety: for provenance. Writing is ``qvalid fetch``, which
    records a manifest line, and a tool that wrote would put data in the cache
    with none, leaving a manifest that reads as complete while being wrong."""

    def test_no_tool_writes_fetches_or_deletes(self) -> None:
        names = {tool.name for tool in TOOLS}
        forbidden = {"fetch", "download", "write", "delete", "remove", "store", "put"}
        assert not any(word in name for name in names for word in forbidden)

    def test_the_tools_module_imports_nothing_that_reaches_the_network(self) -> None:
        """By import and by call, not by substring. The first version of this
        banned the word ``requests`` and flagged a field named ``requests``
        that counts manifest lines, which is D059's mistake again: forbidding a
        sequence of characters catches the syntax and misses the target."""
        imported: set[str] = set()
        for node in ast.walk(tools_ast()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {"urllib", "requests", "socket", "http", "httpx", "subprocess"}

    def test_and_calls_nothing_that_writes(self) -> None:
        called = {
            node.func.attr
            for node in ast.walk(tools_ast())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & {"write_text", "write_bytes", "unlink", "mkdir", "rmtree"}

    def test_an_unknown_tool_is_named_rather_than_answered_with_nothing(
        self, tmp_path: Path
    ) -> None:
        result = call(tmp_path, "delete_everything")
        assert result["isError"] is True
        assert "no tool named" in result["content"][0]["text"]

    def test_a_series_too_large_to_read_honestly_is_refused(self, tmp_path: Path) -> None:
        """A model handed two hundred thousand rows summarises them and reports
        the summary as if it had read the series."""
        root = cache_with_a_slice(tmp_path, rows=MAX_ROWS * 2)
        result = call(
            root, "read_series", source="file", symbol="SP500", start="2022-01-03", end="2022-05-02"
        )
        assert result["isError"] is True
        assert "qvalid fetch --out" in result["content"][0]["text"]


def test_the_command_refuses_a_cache_that_does_not_exist(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["mcp", "--cache", str(tmp_path / "nowhere")])
    assert result.exit_code == 2
    assert "qvalid fetch" in result.stdout + (result.stderr or "")


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_a_request_before_any_fetch_still_answers(tmp_path: Path, method: str) -> None:
    """An empty cache is a state, not an error: the agent should be able to ask
    what is there and be told nothing is."""
    assert "result" in ask(tmp_path, method)
