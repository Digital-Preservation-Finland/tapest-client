"""Tests for tapest_client.cli module."""

import json
from types import SimpleNamespace

import pytest

import tapest_client
from tapest_client.cli import (
    build_parser,
    main,
    METADATA_ORDER_CHOICES,
    _load_config,
    _print_metadata_table,
    _run_status,
    _run_write_config,
    _run_ingest,
    _run_extract,
    _run_delete,
    _run_query_metadata,
    _run_update_metadata,
)
from tapest_client import (
    FileAlreadyExistsError,
    TapestClientError,
    generate_checksum,
)


def _metadata_args(**overrides):
    """Build a metadata args namespace with sensible defaults."""
    defaults = dict(
        file_id=None,
        prefix=None,
        regex=None,
        identifier=None,
        parent=None,
        storage=None,
        pending=False,
        errors=False,
        order=None,
        desc=False,
        limit=None,
        page_token=None,
        format="json",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _jsonl(capsys):
    """Parse the stdout of a batch command, one JSON record per line."""
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
    ]


def _ingested(cli_fx):
    """Return the (identifier, path) pairs sent to ingest_file."""
    return [args for args, _ in cli_fx.calls["ingest_file"]]


def _ingest_args(**overrides):
    """Build an ingest args namespace with sensible defaults."""
    defaults = dict(
        paths=[],
        skip=False,
        prefix=None,
        identifier=None,
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
    assert "ingest" in capsys.readouterr().out


def test_help_exits_0():
    """--help exits with code 0."""
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])
    assert exc_info.value.code == 0


def test_ingest_parses_all_options():
    """ingest parses all global and subcommand options."""
    args = build_parser().parse_args(
        [
            "-v",
            "--config",
            "/conf",
            "--host",
            "https://h",
            "ingest",
            "--identifier",
            "/id",
            "/path",
        ]
    )
    assert args.verbose is True
    assert args.config == "/conf"
    assert args.host == "https://h"
    assert args.identifier == "/id"
    assert args.paths == ["/path"]


def test_metadata_query_params():
    """query-metadata parses repeatable and flag options."""
    args = build_parser().parse_args(
        [
            "query-metadata",
            "--prefix",
            "pfx1",
            "--prefix",
            "pfx2",
            "--identifier",
            "id1",
            "--pending",
            "--errors",
            "--order",
            "ingested",
            "--limit",
            "10",
        ]
    )
    assert args.prefix == ["pfx1", "pfx2"]
    assert args.identifier == ["id1"]
    assert args.pending is True
    assert args.order == "ingested"
    assert args.limit == 10


def test_metadata_query_parses_regex():
    """query-metadata parses repeatable --regex patterns."""
    args = build_parser().parse_args(
        [
            "query-metadata",
            "--regex",
            r"/2022/.*\.dat$",
            "--regex",
            "Experiment_1[0-9]/",
        ]
    )
    assert args.regex == [r"/2022/.*\.dat$", "Experiment_1[0-9]/"]


def test_metadata_query_parses_parent():
    """query-metadata parses --parent as a path (defaulting to None)."""
    args = build_parser().parse_args(
        ["query-metadata", "--parent", "/dir/"]
    )
    assert args.parent == "/dir/"

    default = build_parser().parse_args(["query-metadata"])
    assert default.parent is None


def test_ingest_parses_options():
    """ingest parses one-or-more paths plus --prefix and --skip."""
    args = build_parser().parse_args(
        [
            "ingest",
            "--prefix",
            "acme",
            "--skip",
            "/path/a.dat",
            "/path/b.dat",
            "/path/dir1",
        ]
    )
    assert args.paths == ["/path/a.dat", "/path/b.dat", "/path/dir1"]
    assert args.prefix == "acme"
    assert args.skip is True


def test_ingest_has_no_force_option():
    """ingest has no --force: a stored identifier cannot be replaced."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ingest", "--force", "/path/a.dat"])


# -- Handlers -----------------------------------------------------------------


def test_status(config_fx, cli_fx, capsys):
    """status handler prints JSON service status."""
    cli_fx("retrieve_status", return_value={"status": "ok"})
    _run_status(config_fx(), SimpleNamespace())
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_status_reports_the_client_version(config_fx, cli_fx, capsys):
    """status handler reports the client version next to the service
    response, so the two versions cannot be confused."""
    cli_fx(
        "retrieve_status", return_value={"tapest_api_version": "1.2.3"}
    )
    _run_status(config_fx(), SimpleNamespace())
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "tapest_client_version": tapest_client.__version__,
        "tapest_api_version": "1.2.3",
    }


def test_ingest_with_identifier_calls_library(
    tmp_path, config_fx, cli_fx, capsys
):
    """--identifier gives the identifier of one file."""
    path = tmp_path / "f.dat"
    path.write_bytes(b"x")
    cli_fx("ingest_file", return_value={"identifier": "/id", "size": 100})
    args = SimpleNamespace(
        paths=[str(path)], identifier="/id", prefix=None,
        skip=False, force=False,
    )
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [("/id", str(path))]
    assert json.loads(capsys.readouterr().out)["identifier"] == "/id"


def test_ingest_identifier_normalizes_leading_slash(
    tmp_path, config_fx, cli_fx
):
    """--identifier prepends a leading '/' if the user omits it."""
    path = tmp_path / "f.dat"
    path.write_bytes(b"x")
    cli_fx("ingest_file", return_value={"identifier": "/id"})
    args = SimpleNamespace(
        paths=[str(path)], identifier="id", prefix=None,
        skip=False, force=False,
    )
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [("/id", str(path))]


def test_ingest_identifier_needs_exactly_one_path(config_fx, cli_fx):
    """--identifier with two paths is an error."""
    cli_fx("ingest_file")
    args = SimpleNamespace(
        paths=["/a", "/b"], identifier="/id", prefix=None,
        skip=False, force=False,
    )
    with pytest.raises(TapestClientError):
        _run_ingest(config_fx(), args)


def test_extract_one_identifier_to_a_file(tmp_path, config_fx, cli_fx):
    """One identifier and a destination that is not a directory give a
    single file."""
    metadata = {"identifier": "/id", "size": 1, "checksum": "sha256:x"}
    dest = tmp_path / "out.dat"
    cli_fx("retrieve_file_metadata", return_value=metadata)
    cli_fx("extract_file_with_metadata", return_value=metadata)
    args = SimpleNamespace(
        args=["/id", str(dest)], prefix=None,
        skip=False, force=False,
    )
    _run_extract(config_fx(), args)
    assert cli_fx.calls["extract_file_with_metadata"] == [
        ((metadata, str(dest)), {})
    ]


def test_extract_one_identifier_skips_a_matching_file(
    tmp_path, config_fx, cli_fx, capsys
):
    """--skip works for one named file, not only for a directory tree."""
    dest = tmp_path / "out.dat"
    dest.write_bytes(b"hello world")
    metadata = {
        "identifier": "/id",
        "size": 11,
        "checksum": generate_checksum(dest),
    }
    cli_fx("retrieve_file_metadata", return_value=metadata)
    cli_fx("extract_file_with_metadata")
    args = SimpleNamespace(
        args=["/id", str(dest)], prefix=None,
        skip=True, force=False,
    )
    _run_extract(config_fx(), args)
    assert cli_fx.calls["extract_file_with_metadata"] == []
    assert capsys.readouterr().out == ""


def test_extract_needs_a_destination(config_fx, cli_fx):
    """One positional argument and no prefix is an error."""
    cli_fx("extract_file")
    args = SimpleNamespace(
        args=["/id"], prefix=None,
        skip=False, force=False,
    )
    with pytest.raises(TapestClientError):
        _run_extract(config_fx(), args)


def test_delete_calls_library(config_fx, cli_fx):
    """delete passes each identifier to the library."""
    cli_fx("delete_file")
    args = SimpleNamespace(file_id=["/id", "/id2"])
    _run_delete(config_fx(), args)
    assert cli_fx.calls["delete_file"] == [(("/id",), {}), (("/id2",), {})]


# -- Metadata modes -----------------------------------------------------------


def test_metadata_retrieve_single(config_fx, cli_fx, capsys):
    """query-metadata with file_id retrieves single file metadata."""
    cli_fx("retrieve_file_metadata", return_value={"identifier": "/id"})
    _run_query_metadata(config_fx(), _metadata_args(file_id="/id"))
    assert cli_fx.calls["retrieve_file_metadata"] == [
        (("/id",), {})
    ]


def test_metadata_query_builds_correct_body(config_fx, cli_fx, capsys):
    """query-metadata without file_id builds correct query dict."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(
        config_fx(),
        _metadata_args(
            prefix=["pfx1"],
            identifier=["id1"],
            pending=True,
            order="ingested",
            storage="kaj_lt9",
            limit=10,
        ),
    )
    assert cli_fx.calls["retrieve_metadata"] == [
        (
            (),
            {
                "query": {
                    "prefixes": ["pfx1"],
                    "identifiers": ["id1"],
                    "pending_only": True,
                    "order_by": "ingested",
                    "limit": 10,
                },
                "storage_name": "kaj_lt9",
            },
        )
    ]


def test_metadata_query_builds_regex_body(config_fx, cli_fx, capsys):
    """query-metadata passes --regex through verbatim as the regexes field."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(
        config_fx(),
        _metadata_args(regex=[r"/2022/.*\.dat$"]),
    )
    assert cli_fx.calls["retrieve_metadata"] == [
        (
            (),
            {
                "query": {"regexes": [r"/2022/.*\.dat$"]},
                "storage_name": None,
            },
        )
    ]


def test_metadata_query_builds_parent_body(config_fx, cli_fx, capsys):
    """query-metadata puts the --parent path in the query."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(
        config_fx(),
        _metadata_args(parent="/dir/"),
    )
    assert cli_fx.calls["retrieve_metadata"] == [
        (
            (),
            {
                "query": {"parent": "/dir/"},
                "storage_name": None,
            },
        )
    ]


def test_metadata_order_choices_synced():
    """Order choices match the server's OrderByValue: only the two pageable
    root fields, no per-copy or obsolete fields."""
    assert set(METADATA_ORDER_CHOICES) == {"identifier", "ingested"}


def test_query_metadata_order_rejects_non_pageable_field():
    """A dropped per-copy field is not a valid --order choice."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["query-metadata", "--order", "stored"])


def test_query_metadata_ingested_order_ok(config_fx, cli_fx):
    """order=ingested maps to the query order_by."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(config_fx(), _metadata_args(order="ingested"))
    assert cli_fx.calls["retrieve_metadata"][0][1]["query"][
        "order_by"
    ] == "ingested"


def test_query_metadata_table_format(config_fx, cli_fx, capsys):
    """--format table renders a per-file copies summary."""
    cli_fx(
        "retrieve_metadata",
        return_value={
            "files": 1, "filesPending": 1, "filesWithErrors": 0,
            "metadata": [
                {
                    "identifier": "/p/a.dat", "size": 123, "ingested": "t",
                    "copies": {
                        "tape-cache": {},
                        "kaj_lt9": {"stored": "t", "checked": "t"},
                    },
                }
            ],
        },
    )
    _run_query_metadata(config_fx(), _metadata_args(format="table"))
    out = capsys.readouterr().out
    assert "/p/a.dat" in out
    assert "ingested=t" in out  # file-level, on the header line
    assert "kaj_lt9" in out and "tape-cache" in out
    assert "storage" in out  # column header
    assert "1 files, 1 pending" in out


def test_print_metadata_table_renders_common_prefixes(capsys):
    """A one-level listing shows child directories when metadata is None."""
    _print_metadata_table(
        {"files": 0, "metadata": None, "commonPrefixes": ["/p/", "/q/"]}
    )
    out = capsys.readouterr().out
    assert "/p/  (directory)" in out
    assert "/q/  (directory)" in out


def test_print_metadata_table_common_prefixes_without_metadata_key(capsys):
    """A directory listing omits the metadata key, so detect it by prefixes."""
    _print_metadata_table(
        {"files": 0, "commonPrefixes": ["/p/", "/q/"]}
    )
    out = capsys.readouterr().out
    assert "/p/  (directory)" in out
    assert "/q/  (directory)" in out


def test_print_metadata_table_no_data_falls_back_to_json(capsys):
    """With neither a metadata list nor common prefixes, fall back to JSON."""
    _print_metadata_table({"metadata": None})
    assert json.loads(capsys.readouterr().out)["metadata"] is None


def test_print_metadata_table_shows_next_page_token(capsys):
    """A page with a nextPageToken prints it in the footer."""
    _print_metadata_table(
        {
            "files": 1,
            "filesPending": 0,
            "filesWithErrors": 0,
            "metadata": [{"identifier": "/a", "copies": {}}],
            "nextPageToken": "TOKEN123",
        }
    )
    assert "next page token: TOKEN123" in capsys.readouterr().out


def test_query_metadata_page_token_desc_map_to_query(config_fx, cli_fx):
    """--page-token and --desc map to the query page_token and order_dir."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(
        config_fx(),
        _metadata_args(order="ingested", desc=True, page_token="TOK"),
    )
    query = cli_fx.calls["retrieve_metadata"][0][1]["query"]
    assert query["order_dir"] == "desc"
    assert query["page_token"] == "TOK"


def test_update_metadata(config_fx, cli_fx, capsys):
    """update-metadata parses JSON input and passes it to library."""
    cli_fx("update_file_metadata", return_value={"identifier": "/id"})
    args = SimpleNamespace(
        file_id="/id",
        json_input='{"stored": "2030-01-01T00:00:00Z"}',
        storage=None,
        stdin=False,
        json_file=None,
    )
    _run_update_metadata(config_fx(), args)
    assert cli_fx.calls["update_file_metadata"] == [
        (("/id", {"stored": "2030-01-01T00:00:00Z"}), {"storage_name": None})
    ]


# -- Batch operations ---------------------------------------------------------


def test_ingest_paths_become_identifiers(
    tmp_path, monkeypatch, config_fx, cli_fx
):
    """Plain file paths become identifiers built from the path-as-given."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    (tmp_path / "b.dat").write_bytes(b"b")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["a.dat", "b.dat"])
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [("/a.dat", "a.dat"), ("/b.dat", "b.dat")]


def test_ingest_directory(tmp_path, monkeypatch, config_fx, cli_fx):
    """A directory is walked; each file's identifier extends the dir path."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    (d / "a.dat").write_bytes(b"a")
    (d / "sub").mkdir()
    (d / "sub" / "b.dat").write_bytes(b"b")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["acme"])
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [
        ("/acme/a.dat", "acme/a.dat"),
        ("/acme/sub/b.dat", "acme/sub/b.dat"),
    ]


def test_ingest_cwd_dot(tmp_path, monkeypatch, config_fx, cli_fx):
    """'ingest .' from inside a directory yields clean '/<relpath>' ids.

    Passing '.' must produce the same identifiers as passing the directory
    by an explicit path, with no '/./' segment.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.dat").write_bytes(b"b")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["."])
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [
        ("/a.dat", "a.dat"),
        ("/sub/b.dat", "sub/b.dat"),
    ]


def test_ingest_mixed(tmp_path, monkeypatch, config_fx, cli_fx):
    """Files and directories can be passed together in one invocation."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loose.dat").write_bytes(b"x")
    d = tmp_path / "dir1"
    d.mkdir()
    (d / "in.dat").write_bytes(b"y")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["loose.dat", "dir1"])
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [
        ("/loose.dat", "loose.dat"),
        ("/dir1/in.dat", "dir1/in.dat"),
    ]


def test_ingest_with_prefix(tmp_path, monkeypatch, config_fx, cli_fx):
    """--prefix is prepended to every derived identifier."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    d = tmp_path / "dir1"
    d.mkdir()
    (d / "b.dat").write_bytes(b"b")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["a.dat", "dir1"], prefix="acme/2024")
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [
        ("/acme/2024/a.dat", "a.dat"),
        ("/acme/2024/dir1/b.dat", "dir1/b.dat"),
    ]


def test_ingest_missing_path(tmp_path, config_fx):
    """ingest raises if a CLI path is neither file nor directory."""
    args = _ingest_args(paths=[str(tmp_path / "nope.dat")])
    with pytest.raises(TapestClientError, match="does not exist"):
        _run_ingest(config_fx(), args)


@pytest.mark.parametrize(
    "prefix_input",
    ["acme/2024", "/acme/2024", "acme/2024/", "/acme/2024/", "//acme/2024//"],
)
def test_ingest_prefix_normalization(
    tmp_path, monkeypatch, config_fx, cli_fx, prefix_input
):
    """--prefix is normalized to a single leading '/' with no trailing '/'."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    cli_fx("ingest_file", return_value={})
    args = _ingest_args(paths=["a.dat"], prefix=prefix_input)
    _run_ingest(config_fx(), args)
    assert _ingested(cli_fx) == [("/acme/2024/a.dat", "a.dat")]


def test_extract_files(tmp_path, config_fx, cli_fx, capsys):
    """extract queries metadata then requests each matching file once, which
    marks the whole set required in one sweep."""
    metadata = [{"identifier": "/a"}, {"identifier": "/b"}]
    cli_fx("retrieve_metadata", return_value={"metadata": metadata})
    cli_fx("attempt_extract", return_value=(True, 0))
    _run_extract(config_fx(), _extract_args(tmp_path))
    assert cli_fx.calls["attempt_extract"] == [
        ((metadata[0], str(tmp_path / "a")), {}),
        ((metadata[1], str(tmp_path / "b")), {}),
    ]
    assert len(_jsonl(capsys)) == 2


def test_extract_files_reports_unavailable(
    tmp_path, config_fx, cli_fx, monkeypatch, capsys
):
    """A file that never becomes ready is reported and raises 117, while the
    ready files are still extracted."""
    metadata = [{"identifier": "/a"}, {"identifier": "/b"}]
    cli_fx("retrieve_metadata", return_value={"metadata": metadata})

    def _attempt(config, file_metadata, path):
        return file_metadata["identifier"] == "/a", 0

    monkeypatch.setattr(
        "tapest_client.cli.tapest_client.attempt_extract", _attempt
    )
    with pytest.raises(TapestClientError) as exc_info:
        _run_extract(config_fx(), _extract_args(tmp_path))
    assert exc_info.value.exit_code == 117
    assert "/b" in str(exc_info.value)
    assert [record["identifier"] for record in _jsonl(capsys)] == ["/a"]


def test_extract_files_follows_pagination(
    tmp_path, config_fx, cli_fx, monkeypatch, capsys
):
    """extract reads every page, so a match set larger than one page is
    marked and collected in full, not only the first page."""
    pages = {
        None: {
            "metadata": [{"identifier": "/a"}, {"identifier": "/b"}],
            "nextPageToken": "TOKEN",
        },
        "TOKEN": {"metadata": [{"identifier": "/c"}]},
    }
    queries = []

    def _retrieve(config, query, storage_name=None):
        queries.append(dict(query))
        return pages[query.get("page_token")]

    monkeypatch.setattr(
        "tapest_client.cli.tapest_client.retrieve_metadata", _retrieve
    )
    cli_fx("attempt_extract", return_value=(True, 0))
    _run_extract(config_fx(), _extract_args(tmp_path))

    assert [q.get("page_token") for q in queries] == [None, "TOKEN"]
    extracted = [
        args[0]["identifier"]
        for args, _ in cli_fx.calls["attempt_extract"]
    ]
    assert extracted == ["/a", "/b", "/c"]
    assert len(_jsonl(capsys)) == 3


def test_extract_stops_on_repeated_page_token(
    tmp_path, config_fx, cli_fx, monkeypatch
):
    """A service that echoes the same nextPageToken does not hang the loop."""
    calls = []

    def _retrieve(config, query, storage_name=None):
        calls.append(query.get("page_token"))
        return {"metadata": [{"identifier": "/a"}], "nextPageToken": "SAME"}

    monkeypatch.setattr(
        "tapest_client.cli.tapest_client.retrieve_metadata", _retrieve
    )
    cli_fx("attempt_extract", return_value=(True, 0))
    _run_extract(config_fx(), _extract_args(tmp_path))

    assert calls == [None, "SAME"]


def _extract_args(dest, **overrides):
    """Build an extract args namespace for a destination directory."""
    defaults = dict(
        args=[str(dest)],
        prefix=["pfx"],
        skip=False,
        force=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_extract_files_requires_source(config_fx):
    """extract needs a destination when no prefix is given."""
    args = SimpleNamespace(
        args=["/dir"],
        prefix=None,
        skip=False,
        force=False,
    )
    with pytest.raises(TapestClientError):
        _run_extract(config_fx(), args)


# -- Error handling & config --------------------------------------------------


def test_tapest_error_exits_1(monkeypatch):
    """TapestClientError during execution exits with code 1."""
    monkeypatch.setattr("sys.argv", ["tapest-client", "status"])
    monkeypatch.setattr("tapest_client.cli._load_config", lambda args: None)
    monkeypatch.setattr(
        "tapest_client.cli.tapest_client.retrieve_status",
        lambda cfg: (_ for _ in ()).throw(TapestClientError("fail")),
    )
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
        config=str(conf), host="https://override", ca_cert=None
    )
    assert _load_config(args).host == "https://override"


# -- Conflict policy: --skip and --force --------------------------------------


def test_ingest_skip_sends_nothing_for_a_stored_file(
    tmp_path, config_fx, cli_fx, capsys
):
    """--skip sends nothing for a file that is already stored."""
    path = tmp_path / "a.dat"
    path.write_bytes(b"hello world")
    stored = {"identifier": "/a.dat", "checksum": generate_checksum(path)}
    cli_fx("retrieve_file_metadata", return_value=stored)
    cli_fx("ingest_file")
    _run_ingest(
        config_fx(),
        _ingest_args(paths=[str(path)], identifier="/a.dat", skip=True),
    )
    assert cli_fx.calls["ingest_file"] == []
    assert capsys.readouterr().out == ""


def test_ingest_skip_leaves_stored_files_out_of_the_results(
    tmp_path, monkeypatch, config_fx, cli_fx, capsys
):
    """The printed results are the files that the client sent."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"stored already")
    cli_fx(
        "retrieve_file_metadata",
        return_value={
            "identifier": "/a.dat",
            "checksum": generate_checksum(tmp_path / "a.dat"),
        },
    )
    cli_fx("ingest_file")
    _run_ingest(config_fx(), _ingest_args(paths=["a.dat"], skip=True))
    assert cli_fx.calls["ingest_file"] == []
    assert _jsonl(capsys) == []


def test_ingest_reports_how_many_files_were_skipped(
    tmp_path, monkeypatch, config_fx, cli_fx, caplog
):
    """A skipped file is counted in one line that shows without -v."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"stored already")
    cli_fx(
        "retrieve_file_metadata",
        return_value={
            "identifier": "/a.dat",
            "checksum": generate_checksum(tmp_path / "a.dat"),
        },
    )
    cli_fx("ingest_file")
    with caplog.at_level("WARNING", logger="tapest-client"):
        _run_ingest(config_fx(), _ingest_args(paths=["a.dat"], skip=True))
    assert "Skipped 1 file(s) that are already stored" in caplog.text


def test_ingest_prints_one_json_line_per_file(
    tmp_path, monkeypatch, config_fx, cli_fx, capsys
):
    """Each file is printed when it is done, not after the whole batch."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    (tmp_path / "b.dat").write_bytes(b"b")
    cli_fx("ingest_file", return_value={"identifier": "/x"})
    _run_ingest(config_fx(), _ingest_args(paths=["a.dat", "b.dat"]))
    assert _jsonl(capsys) == [{"identifier": "/x"}, {"identifier": "/x"}]


def test_ingest_never_deletes_a_stored_file(tmp_path, config_fx, cli_fx):
    """The client does not delete a stored file to reuse its identifier.

    An identifier that has been used is not free again, not even after a
    delete, so a local file that differs is an error.
    """
    path = tmp_path / "a.dat"
    path.write_bytes(b"hello world")
    cli_fx(
        "retrieve_file_metadata",
        return_value={"identifier": "/a.dat", "checksum": "sha256:old"},
    )
    cli_fx("delete_file")
    cli_fx("ingest_file")
    with pytest.raises(FileAlreadyExistsError, match="cannot be reused"):
        _run_ingest(
            config_fx(),
            _ingest_args(paths=[str(path)], identifier="/a.dat", skip=True),
        )
    assert cli_fx.calls["delete_file"] == []
    assert cli_fx.calls["ingest_file"] == []


def test_ingest_free_identifier_is_uploaded_under_skip(
    tmp_path, config_fx, cli_fx
):
    """A 404 from the check means the identifier is free: upload."""
    path = tmp_path / "a.dat"
    path.write_bytes(b"hello world")
    cli_fx("retrieve_file_metadata", return_value=None)
    cli_fx("ingest_file", return_value={"identifier": "/a.dat"})
    _run_ingest(
        config_fx(),
        _ingest_args(paths=[str(path)], identifier="/a.dat", skip=True),
    )
    assert cli_fx.calls["ingest_file"] == [
        (("/a.dat", str(path)), {"checksum": generate_checksum(path)})
    ]


def test_ingest_without_options_reads_no_metadata(
    tmp_path, config_fx, cli_fx
):
    """A plain ingest is one request; the 409 settles a duplicate."""
    path = tmp_path / "a.dat"
    path.write_bytes(b"hello world")
    cli_fx("retrieve_file_metadata")
    cli_fx("ingest_file", return_value={"identifier": "/a.dat"})
    _run_ingest(
        config_fx(), _ingest_args(paths=[str(path)], identifier="/a.dat")
    )
    assert cli_fx.calls["retrieve_file_metadata"] == []
    assert cli_fx.calls["ingest_file"] == [
        (("/a.dat", str(path)), {"checksum": None})
    ]


def test_extract_skip_leaves_a_matching_file(
    tmp_path, config_fx, cli_fx, capsys
):
    """--skip leaves a local file that matches size and checksum."""
    local = tmp_path / "a.dat"
    local.write_bytes(b"hello world")
    metadata = {
        "identifier": "/a.dat",
        "size": 11,
        "checksum": generate_checksum(local),
    }
    cli_fx("retrieve_metadata", return_value={"metadata": [metadata]})
    cli_fx("attempt_extract")
    _run_extract(config_fx(), _extract_args(tmp_path, skip=True))
    assert cli_fx.calls["attempt_extract"] == []
    assert _jsonl(capsys) == []


def test_extract_force_replaces_a_different_file(
    tmp_path, config_fx, cli_fx
):
    """--force removes the local file before extracting over it."""
    local = tmp_path / "a.dat"
    local.write_bytes(b"stale")
    metadata = {
        "identifier": "/a.dat",
        "size": 11,
        "checksum": "sha256:other",
    }
    cli_fx("retrieve_metadata", return_value={"metadata": [metadata]})
    cli_fx("attempt_extract", return_value=(True, 0))
    _run_extract(config_fx(), _extract_args(tmp_path, force=True))
    assert not local.exists()
    assert cli_fx.calls["attempt_extract"] == [
        ((metadata, str(local)), {})
    ]


def test_extract_existing_file_raises_without_options(
    tmp_path, config_fx, cli_fx
):
    """A taken local pathname is an error unless --skip or --force is given."""
    local = tmp_path / "a.dat"
    local.write_bytes(b"stale")
    metadata = {"identifier": "/a.dat", "size": 11, "checksum": "sha256:x"}
    cli_fx("retrieve_metadata", return_value={"metadata": [metadata]})
    cli_fx("attempt_extract")
    with pytest.raises(FileAlreadyExistsError, match="already exists"):
        _run_extract(config_fx(), _extract_args(tmp_path))
    assert cli_fx.calls["attempt_extract"] == []


def test_extract_missing_destination_directory(tmp_path, config_fx, cli_fx):
    """The destination directory has to exist before any file is written."""
    metadata = {"identifier": "/a.dat", "size": 11, "checksum": "sha256:x"}
    cli_fx("retrieve_metadata", return_value={"metadata": [metadata]})
    cli_fx("attempt_extract")
    with pytest.raises(TapestClientError, match="does not exist"):
        _run_extract(config_fx(), _extract_args(tmp_path / "nope"))
    assert cli_fx.calls["attempt_extract"] == []
