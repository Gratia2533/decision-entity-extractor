import json
import sys
from types import SimpleNamespace

import pytest

from contracts.errors import PipelineError
from runtime.config import load_schema
from tests.runtime.test_runtime import resolver
from transport import cli


@pytest.fixture
def schema_path(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(
        json.dumps({"labels": [{"name": "PERSON", "description": "A named person"}]}),
        encoding="utf-8",
    )
    return path


def arguments(command, schema):
    return [command, "--artifact-root", "unused", "--schema", str(schema)]


def test_resolve_json_and_ownership(monkeypatch, schema_path, capsys):
    service, proposer, provider = resolver()
    monkeypatch.setattr(cli, "build_production_service", lambda **_: service)
    assert cli.main(arguments("resolve", schema_path) + ["--text", "Alice visited Taipei"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["entities"][0]["mention"] == "Alice"
    assert proposer.closed and provider.closed


@pytest.mark.parametrize("failure", [PipelineError("DECISION_TIMEOUT"), RuntimeError("secret")])
def test_resolution_failure_closes_and_redacts(monkeypatch, schema_path, capsys, failure):
    closed = []

    def resolve(_):
        raise failure

    service = SimpleNamespace(resolve=resolve, close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "build_production_service", lambda **_: service)
    assert cli.main(arguments("resolve", schema_path) + ["--text", "Alice"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "secret" not in output.err
    assert closed == [True]
    assert json.loads(output.err)["code"] in {"COMMAND_FAILED", "DECISION_TIMEOUT"}


@pytest.mark.parametrize("failure", [RuntimeError("secret"), KeyboardInterrupt()])
def test_serve_cleanup(monkeypatch, schema_path, capsys, failure):
    closed = []
    calls = []
    service = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "build_production_service", lambda **_: service)
    monkeypatch.setattr("transport.api.create_app", lambda value: value)

    def run(app, **kwargs):
        calls.append((app, kwargs))
        raise failure

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))
    assert cli.main(arguments("serve", schema_path)) in {1, 130}
    assert closed == [True]
    assert calls == [(service, {"host": "127.0.0.1", "port": 8000, "workers": 1})]
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize("text", ["", "   "])
def test_bad_text_never_builds(monkeypatch, schema_path, capsys, text):
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    assert cli.main(arguments("resolve", schema_path) + ["--text", text]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("payload", ["broken", '{"labels": []}', '{"labels": [], "unknown": 1}'])
def test_bad_schema_never_builds(monkeypatch, schema_path, capsys, payload):
    schema_path.write_text(payload)
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    assert cli.main(arguments("resolve", schema_path) + ["--text", "Alice"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "INVALID_CONFIGURATION"


def test_missing_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    assert cli.main(arguments("serve", tmp_path / "absent")) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("port", ["0", "65536", "bad"])
def test_bad_ports(schema_path, port):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments("serve", schema_path) + ["--port", port])
    assert error.value.code == 2


def test_identities_no_build(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    assert cli.main(["identities"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"CM", "BM"}


def test_check_no_build(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    monkeypatch.setattr(cli, "validate_artifact", lambda root, key: (None, {"key": key}))
    assert cli.main(["check", "--artifact-root", "unused"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"CM", "BM"}


def test_sample_schema():
    assert tuple(label.name for label in load_schema("config/schema.example.json").labels) == (
        "PERSON",
        "ORGANIZATION",
        "PLACE",
    )


def test_download_default_no_service(monkeypatch, capsys):
    from pathlib import Path

    calls = []
    monkeypatch.setattr(cli, "build_production_service", lambda **_: pytest.fail("built"))
    monkeypatch.setattr(
        "adapters.otter.provisioning.download_artifacts",
        lambda root: calls.append(root) or {"verified": True},
    )
    assert cli.main(["download"]) == 0
    assert calls == [Path(".local-artifacts")]
    assert json.loads(capsys.readouterr().out) == {"verified": True}
