"""Tests for tapest_client.cli module."""

import json
from types import SimpleNamespace

import pytest

from tapest_client.cli import (
    build_parser, main, _load_config,
    _run_status, _run_write_config, _run_ingest, _run_extract, _run_delete,
    _run_query_metadata, _run_update_metadata, _run_ingest_directory,
    _run_extract_files,
)
from tapest_client import TapestClientError



def _metadata_args(**overrides):
    """Build a metadata args namespace with sensible defaults."""
    defaults = dict(
        file_id=None, prefix=None, identifier=None,
        storage=None, pending=False, errors=False, order=None,
        limit=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# -- Parsing ------------------------------------------------------------------

def test_no_args_shows_help(monkeypatch, capsys):
    """No arguments prints help and exits with code 1."""
    monkeypatch.setattr("sys.argv", ["tapest-client"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "ingest-one" in capsys.readouterr().out


def test_help_exits_0():
    """--help exits with code 0."""
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])
    assert exc_info.value.code == 0


def test_ingest_parses_all_options():
    """ingest-one parses all global and subcommand options."""
    args = build_parser().parse_args([
        "-v", "--config", "/conf", "--host", "https://h",
        "ingest-one", "--storage", "tape-1", "/id", "/path"])
    assert args.verbose is True
    assert args.config == "/conf"
    assert args.host == "https://h"
    assert args.file_id == "/id"
    assert args.local_path == "/path"
    assert args.storage == "tape-1"


def test_metadata_query_params():
    """query-metadata parses repeatable and flag options."""
    args = build_parser().parse_args([
        "query-metadata", "--prefix", "pfx1", "--prefix", "pfx2",
        "--identifier", "id1", "--pending", "--errors",
        "--order", "ingested", "--limit", "10"])
    assert args.prefix == ["pfx1", "pfx2"]
    assert args.identifier == ["id1"]
    assert args.pending is True
    assert args.order == "ingested"
    assert args.limit == 10


def test_extract_with_next_id():
    """extract-one accepts optional next file identifier."""
    args = build_parser().parse_args(
        ["extract-one", "/id", "/path", "/next-id"])
    assert args.next_file_id == "/next-id"


# -- Handlers -----------------------------------------------------------------

def test_status(config_fx, cli_fx, capsys):
    """status handler prints JSON service status."""
    cli_fx("retrieve_status", return_value={"status": "ok"})
    _run_status(config_fx(), SimpleNamespace())
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_ingest_calls_library(config_fx, cli_fx, capsys):
    """ingest-one passes file_id, path, and storage_name to library."""
    cli_fx(
        "ingest_file",
        return_value={"identifier": "/id", "size": 100})
    args = SimpleNamespace(file_id="/id", local_path="/path", storage=None)
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_file"] == [
        (("/id", "/path"), {"storage_name": None})]
    assert json.loads(capsys.readouterr().out)["identifier"] == "/id"


def test_extract_calls_library(config_fx, cli_fx):
    """extract-one passes file_id, path, next_identifier, and storage_name."""
    cli_fx("extract_file", return_value={"identifier": "/id"})
    args = SimpleNamespace(
        file_id="/id", local_path="/path",
        next_file_id=None, storage=None)
    _run_extract(config_fx(), args)
    assert cli_fx.calls["extract_file"] == [
        (("/id", "/path"), {"next_identifier": None, "storage_name": None})]


def test_delete_calls_library(config_fx, cli_fx):
    """delete passes file_id and storage_name to library."""
    cli_fx("delete_file")
    args = SimpleNamespace(file_id="/id", storage=None)
    _run_delete(config_fx(), args)
    assert cli_fx.calls["delete_file"] == [(("/id",), {"storage_name": None})]


# -- Metadata modes -----------------------------------------------------------

def test_metadata_retrieve_single(config_fx, cli_fx, capsys):
    """query-metadata with file_id retrieves single file metadata."""
    cli_fx(
        "retrieve_file_metadata",
        return_value={"identifier": "/id"})
    _run_query_metadata(config_fx(), _metadata_args(file_id="/id"))
    assert cli_fx.calls["retrieve_file_metadata"] == [
        (("/id",), {"storage_name": None})]


def test_metadata_query_builds_correct_body(config_fx, cli_fx, capsys):
    """query-metadata without file_id builds correct query dict."""
    cli_fx(
        "retrieve_metadata",
        return_value={"metadata": []})
    _run_query_metadata(config_fx(), _metadata_args(
        prefix=["pfx1"], identifier=["id1"], pending=True,
        order="ingested", limit=10))
    assert cli_fx.calls["retrieve_metadata"] == [((), {
        "query": {
            "prefixes": ["pfx1"], "identifiers": ["id1"],
            "pending_only": True, "order_by": "ingested", "limit": 10,
        },
        "storage_name": None,
    })]


def test_update_metadata(config_fx, cli_fx, capsys):
    """update-metadata parses JSON input and passes it to library."""
    cli_fx(
        "update_file_metadata",
        return_value={"identifier": "/id"})
    args = SimpleNamespace(
        file_id="/id",
        json_input='{"stored": "2030-01-01T00:00:00Z"}',
        storage=None, stdin=False, json_file=None)
    _run_update_metadata(config_fx(), args)
    assert cli_fx.calls["update_file_metadata"] == [
        (("/id", {"stored": "2030-01-01T00:00:00Z"}),
         {"storage_name": None})]


# -- Batch operations ---------------------------------------------------------

def test_ingest_directory(config_fx, cli_fx, capsys):
    """ingest-many prints JSON list of ingested files."""
    results = [{"identifier": "/a"}, {"identifier": "/b"}]
    cli_fx("ingest_files_from_directory", return_value=results)
    args = SimpleNamespace(local_dir="/dir", skip=False, force=False,
                           prefix=None)
    _run_ingest_directory(config_fx(), args)
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_extract_files(config_fx, cli_fx, capsys):
    """extract-many queries metadata then extracts matching files."""
    metadata = {"metadata": [{"identifier": "/a"}, {"identifier": "/b"}]}
    cli_fx("retrieve_metadata", return_value=metadata)
    cli_fx(
        "extract_files_to_directory",
        return_value=[{"identifier": "/a"}])
    args = SimpleNamespace(
        local_dir="/dir", prefix=["pfx"],
        identifier=None, skip=False, force=False, storage=None)
    _run_extract_files(config_fx(), args)


def test_extract_files_requires_source(config_fx):
    """extract-many exits if neither --prefix nor --identifier is given."""
    args = SimpleNamespace(
        local_dir="/dir", prefix=None,
        identifier=None, skip=False, force=False, storage=None)
    with pytest.raises(SystemExit):
        _run_extract_files(config_fx(), args)


# -- Error handling & config --------------------------------------------------

def test_tapest_error_exits_1(monkeypatch):
    """TapestClientError during execution exits with code 1."""
    monkeypatch.setattr("sys.argv", ["tapest-client", "status"])
    monkeypatch.setattr("tapest_client.cli._load_config", lambda args: None)
    monkeypatch.setattr(
        "tapest_client.cli.tapest_client.retrieve_status",
        lambda cfg: (_ for _ in ()).throw(TapestClientError("fail")))
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_write_config_creates_file(tmp_path, monkeypatch, capsys):
    """write-config creates config file in user config directory."""
    conf_path = tmp_path / "tapest-client" / "client.conf"
    monkeypatch.setattr("tapest_client.cli.USER_CONFIG_FILE", str(conf_path))
    _run_write_config(None, SimpleNamespace())
    assert conf_path.is_file()
    assert "token" in conf_path.read_text()
    assert "written to" in capsys.readouterr().out


def test_write_config_exits_if_exists(tmp_path, monkeypatch, capsys):
    """write-config refuses to overwrite existing config file."""
    conf_path = tmp_path / "tapest-client" / "client.conf"
    conf_path.parent.mkdir(parents=True)
    conf_path.write_text("existing")
    monkeypatch.setattr("tapest_client.cli.USER_CONFIG_FILE", str(conf_path))
    with pytest.raises(SystemExit) as exc_info:
        _run_write_config(None, SimpleNamespace())
    assert exc_info.value.code == 1
    assert conf_path.read_text() == "existing"


def test_load_config_host_override(tmp_path):
    """--host CLI flag overrides host from config file."""
    conf = tmp_path / "client.conf"
    conf.write_text('{"host": "https://h", "token": "tok"}')
    args = SimpleNamespace(
        config=str(conf), host="https://override", ca_cert=None)
    assert _load_config(args).host == "https://override"
