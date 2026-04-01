"""Tests for tapest_client.cli module."""

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from tapest_client.cli import (
    build_parser, main, _load_config,
    _run_status, _run_write_config, _run_ingest, _run_extract, _run_delete,
    _run_query_metadata, _run_update_metadata, _run_ingest_directory,
    _run_extract_files,
)
from tapest_client import TapestClientError
from tapest_client.config import Config


@pytest.fixture
def config():
    """Test config with dummy values."""
    cfg = Config()
    cfg.ice_token = "test-token"
    cfg.ice_host = "https://test.example.com"
    return cfg


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
    monkeypatch.setattr("sys.argv", ["tapest-client"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "ingest-one" in capsys.readouterr().out


def test_help_exits_0():
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])
    assert exc_info.value.code == 0


def test_ingest_parses_all_options():
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
    args = build_parser().parse_args([
        "query-metadata", "--prefix", "pfx1", "--prefix", "pfx2",
        "--identifier", "id1", "--pending", "--errors", "--order", "ingested", "--limit", "10"])
    assert args.prefix == ["pfx1", "pfx2"]
    assert args.identifier == ["id1"]
    assert args.pending is True
    assert args.order == "ingested"
    assert args.limit == 10


def test_extract_with_next_id():
    args = build_parser().parse_args(
        ["extract-one", "/id", "/path", "/next-id"])
    assert args.next_file_id == "/next-id"


# -- Handlers -----------------------------------------------------------------

def test_status(config, capsys):
    with mock.patch("tapest_client.cli.tapest_client.retrieve_status",
                    return_value={"status": "ok"}):
        _run_status(config, SimpleNamespace())
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_ingest_calls_library(config, capsys):
    result = {"identifier": "/id", "size": 100}
    args = SimpleNamespace(
        file_id="/id", local_path="/path",
        storage=None, )
    with mock.patch("tapest_client.cli.tapest_client.ingest_file",
                    return_value=result) as m:
        _run_ingest(config, args)
    m.assert_called_once_with(config, "/id", "/path", storage_name=None)
    assert json.loads(capsys.readouterr().out)["identifier"] == "/id"



def test_extract_calls_library(config, capsys):
    args = SimpleNamespace(
        file_id="/id", local_path="/path",
        next_file_id=None, storage=None, )
    with mock.patch("tapest_client.cli.tapest_client.extract_file",
                    return_value={"identifier": "/id"}) as m:
        _run_extract(config, args)
    m.assert_called_once_with(
        config, "/id", "/path", next_identifier=None, storage_name=None)


def test_delete_calls_library(config):
    args = SimpleNamespace(file_id="/id", storage=None, )
    with mock.patch("tapest_client.cli.tapest_client.delete_file") as m:
        _run_delete(config, args)
    m.assert_called_once_with(config, "/id", storage_name=None)



# -- Metadata modes -----------------------------------------------------------

def test_metadata_retrieve_single(config, capsys):
    args = _metadata_args(file_id="/id")
    with mock.patch("tapest_client.cli.tapest_client.retrieve_file_metadata",
                    return_value={"identifier": "/id"}) as m:
        _run_query_metadata(config, args)
    m.assert_called_once_with(config, "/id", storage_name=None)


def test_metadata_query_builds_correct_body(config, capsys):
    args = _metadata_args(
        prefix=["pfx1"], identifier=["id1"], pending=True,
        order="ingested", limit=10)
    with mock.patch("tapest_client.cli.tapest_client.retrieve_metadata",
                    return_value={"metadata": []}) as m:
        _run_query_metadata(config, args)
    m.assert_called_once_with(config, query={
        "prefixes": ["pfx1"], "identifiers": ["id1"],
        "pending_only": True, "order_by": "ingested", "limit": 10,
    }, storage_name=None)



def test_update_metadata(config, capsys):
    args = SimpleNamespace(
        file_id="/id",
        json_input='{"stored": "2030-01-01T00:00:00Z"}',
        storage=None, stdin=False, json_file=None)
    with mock.patch("tapest_client.cli.tapest_client.update_file_metadata",
                    return_value={"identifier": "/id"}) as m:
        _run_update_metadata(config, args)
    m.assert_called_once_with(
        config, "/id", {"stored": "2030-01-01T00:00:00Z"}, storage_name=None)



# -- Batch operations ---------------------------------------------------------

def test_ingest_directory(config, capsys):
    results = [{"identifier": "/a"}, {"identifier": "/b"}]
    args = SimpleNamespace(
        local_dir="/dir", skip=False, force=False, )
    with mock.patch("tapest_client.cli.tapest_client"
                    ".ingest_files_from_directory",
                    return_value=results) as m:
        _run_ingest_directory(config, args)
    m.assert_called_once_with(config, "/dir", skip=False, force=False)
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_extract_files(config, capsys):
    metadata = {"metadata": [{"identifier": "/a"}, {"identifier": "/b"}]}
    args = SimpleNamespace(
        local_dir="/dir", prefix=["pfx"],
        identifier=None, skip=False, force=False,
        storage=None, )
    with mock.patch("tapest_client.cli.tapest_client.retrieve_metadata",
                    return_value=metadata), \
         mock.patch("tapest_client.cli.tapest_client"
                    ".extract_files_to_directory",
                    return_value=[{"identifier": "/a"}]) as m:
        _run_extract_files(config, args)
    m.assert_called_once()


def test_extract_files_requires_source(config):
    args = SimpleNamespace(
        local_dir="/dir", prefix=None,
        identifier=None, skip=False, force=False,
        storage=None, )
    with pytest.raises(SystemExit):
        _run_extract_files(config, args)


# -- Error handling & config --------------------------------------------------

def test_tapest_error_exits_1(monkeypatch):
    monkeypatch.setattr("sys.argv", ["tapest-client", "status"])
    with mock.patch("tapest_client.cli._load_config"), \
         mock.patch("tapest_client.cli.tapest_client.retrieve_status",
                    side_effect=TapestClientError("fail")):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1



def test_write_config_creates_file(tmp_path, monkeypatch, capsys):
    conf_path = tmp_path / "tapest-client" / "client.conf"
    monkeypatch.setattr("tapest_client.cli.USER_CONFIG_FILE", str(conf_path))
    _run_write_config(None, SimpleNamespace())
    assert conf_path.is_file()
    assert "ice_token" in conf_path.read_text()
    assert "written to" in capsys.readouterr().out


def test_write_config_exits_if_exists(tmp_path, monkeypatch, capsys):
    conf_path = tmp_path / "tapest-client" / "client.conf"
    conf_path.parent.mkdir(parents=True)
    conf_path.write_text("existing")
    monkeypatch.setattr("tapest_client.cli.USER_CONFIG_FILE", str(conf_path))
    with pytest.raises(SystemExit) as exc_info:
        _run_write_config(None, SimpleNamespace())
    assert exc_info.value.code == 1
    assert conf_path.read_text() == "existing"


def test_load_config_host_override(tmp_path):
    conf = tmp_path / "client.conf"
    conf.write_text("[tapest-client]\nice_host = https://h\nice_token = tok\n")
    args = SimpleNamespace(config=str(conf), host="https://override")
    assert _load_config(args).ice_host == "https://override"
