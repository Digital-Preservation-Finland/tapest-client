"""Tests for tapest_client.cli module."""

import json
from types import SimpleNamespace

import pytest

from tapest_client.cli import (
    build_parser,
    main,
    METADATA_ORDER_CHOICES,
    COPY_ORDER_FIELDS,
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
from tapest_client import TapestClientError


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
        limit=None,
        format="json",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _ingest_args(**overrides):
    """Build an ingest args namespace with sensible defaults."""
    defaults = dict(
        paths=[],
        skip=False,
        force=False,
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
    """ingest parses one-or-more paths plus --prefix/--skip/--force."""
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
    assert args.force is False


def test_extract_with_next_id():
    """extract accepts the next file identifier for tape ordering."""
    args = build_parser().parse_args(
        ["extract", "--next", "/next-id", "/id", "/path"]
    )
    assert args.next_file_id == "/next-id"
    assert args.args == ["/id", "/path"]


# -- Handlers -----------------------------------------------------------------


def test_status(config_fx, cli_fx, capsys):
    """status handler prints JSON service status."""
    cli_fx("retrieve_status", return_value={"status": "ok"})
    _run_status(config_fx(), SimpleNamespace())
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


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
    assert cli_fx.calls["ingest_file"] == [(("/id", str(path)), {})]
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
    assert cli_fx.calls["ingest_file"] == [(("/id", str(path)), {})]


def test_ingest_identifier_needs_exactly_one_path(config_fx, cli_fx):
    """--identifier with two paths is an error."""
    cli_fx("ingest_file")
    args = SimpleNamespace(
        paths=["/a", "/b"], identifier="/id", prefix=None,
        skip=False, force=False,
    )
    with pytest.raises(TapestClientError):
        _run_ingest(config_fx(), args)


def test_extract_one_identifier_to_a_file(config_fx, cli_fx):
    """One identifier and a destination that is not a directory give a
    single file."""
    cli_fx("extract_file", return_value={"identifier": "/id"})
    args = SimpleNamespace(
        args=["/id", "/path"], prefix=None, next_file_id=None,
        skip=False, force=False,
    )
    _run_extract(config_fx(), args)
    assert cli_fx.calls["extract_file"] == [
        (("/id", "/path"), {"next_identifier": None})
    ]


def test_extract_needs_a_destination(config_fx, cli_fx):
    """One positional argument and no prefix is an error."""
    cli_fx("extract_file")
    args = SimpleNamespace(
        args=["/id"], prefix=None, next_file_id=None,
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
    """Order choices match the server's accepted set: 'queued' present,
    obsolete 'required' gone."""
    assert "queued" in METADATA_ORDER_CHOICES
    assert "required" not in METADATA_ORDER_CHOICES
    assert set(METADATA_ORDER_CHOICES) == {
        "identifier", "storage", "ingested", "stored",
        "checked", "recached", "extracted", "queued",
    }


def test_query_metadata_copy_order_requires_storage(config_fx, cli_fx):
    """Ordering by a per-copy field without --storage exits 2 before any
    request is issued."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    for field in sorted(COPY_ORDER_FIELDS):
        with pytest.raises(SystemExit) as exc:
            _run_query_metadata(config_fx(), _metadata_args(order=field))
        assert exc.value.code == 2
    assert cli_fx.calls["retrieve_metadata"] == []


def test_query_metadata_copy_order_with_storage_ok(config_fx, cli_fx):
    """A per-copy order field passes the guard when --storage is given."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(
        config_fx(), _metadata_args(order="stored", storage="kaj_lt9")
    )
    assert cli_fx.calls["retrieve_metadata"][0][1]["storage_name"] == "kaj_lt9"


def test_query_metadata_storage_order_no_storage_ok(config_fx, cli_fx):
    """order=storage (tape-layout ordering) is allowed without --storage."""
    cli_fx("retrieve_metadata", return_value={"metadata": []})
    _run_query_metadata(config_fx(), _metadata_args(order="storage"))
    assert cli_fx.calls["retrieve_metadata"][0][1]["query"][
        "order_by"
    ] == "storage"


def test_query_metadata_ingested_order_no_storage_ok(config_fx, cli_fx):
    """order=ingested (file-level) is allowed without --storage."""
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


def test_print_metadata_table_split_falls_back_to_json(capsys):
    """Split results (metadata is None) fall back to JSON output."""
    _print_metadata_table({"metadata": None, "metadataSplits": [[]]})
    assert json.loads(capsys.readouterr().out)["metadata"] is None


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


def test_ingest_files(tmp_path, monkeypatch, config_fx, cli_fx):
    """Plain file paths become identifiers built from the path-as-given."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    (tmp_path / "b.dat").write_bytes(b"b")
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["a.dat", "b.dat"])
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"] == [
        (
            ([("/a.dat", "a.dat"), ("/b.dat", "b.dat")],),
            {"skip": False, "force": False},
        )
    ]


def test_ingest_directory(tmp_path, monkeypatch, config_fx, cli_fx):
    """A directory is walked; each file's identifier extends the dir path."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    (d / "a.dat").write_bytes(b"a")
    (d / "sub").mkdir()
    (d / "sub" / "b.dat").write_bytes(b"b")
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["acme"])
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"] == [
        (
            (
                [
                    ("/acme/a.dat", "acme/a.dat"),
                    ("/acme/sub/b.dat", "acme/sub/b.dat"),
                ],
            ),
            {"skip": False, "force": False},
        )
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
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["."])
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"] == [
        (
            (
                [
                    ("/a.dat", "a.dat"),
                    ("/sub/b.dat", "sub/b.dat"),
                ],
            ),
            {"skip": False, "force": False},
        )
    ]


def test_ingest_mixed(tmp_path, monkeypatch, config_fx, cli_fx):
    """Files and directories can be passed together in one invocation."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loose.dat").write_bytes(b"x")
    d = tmp_path / "dir1"
    d.mkdir()
    (d / "in.dat").write_bytes(b"y")
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["loose.dat", "dir1"])
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"] == [
        (
            ([("/loose.dat", "loose.dat"), ("/dir1/in.dat", "dir1/in.dat")],),
            {"skip": False, "force": False},
        )
    ]


def test_ingest_with_prefix(tmp_path, monkeypatch, config_fx, cli_fx):
    """--prefix is prepended to every derived identifier."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.dat").write_bytes(b"a")
    d = tmp_path / "dir1"
    d.mkdir()
    (d / "b.dat").write_bytes(b"b")
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["a.dat", "dir1"], prefix="acme/2024")
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"] == [
        (
            (
                [
                    ("/acme/2024/a.dat", "a.dat"),
                    ("/acme/2024/dir1/b.dat", "dir1/b.dat"),
                ],
            ),
            {"skip": False, "force": False},
        )
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
    cli_fx("ingest_files", return_value=[])
    args = _ingest_args(paths=["a.dat"], prefix=prefix_input)
    _run_ingest(config_fx(), args)
    assert cli_fx.calls["ingest_files"][0][0][0] == [
        ("/acme/2024/a.dat", "a.dat")
    ]


def test_extract_files(config_fx, cli_fx, capsys):
    """extract queries metadata then extracts matching files."""
    metadata = {"metadata": [{"identifier": "/a"}, {"identifier": "/b"}]}
    cli_fx("retrieve_metadata", return_value=metadata)
    cli_fx("extract_files_to_directory", return_value=[{"identifier": "/a"}])
    args = SimpleNamespace(
        args=["/dir"],
        prefix=["pfx"],
        next_file_id=None,
        skip=False,
        force=False,
    )
    _run_extract(config_fx(), args)


def test_extract_files_requires_source(config_fx):
    """extract needs a destination when no prefix is given."""
    args = SimpleNamespace(
        args=["/dir"],
        prefix=None,
        next_file_id=None,
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
