"""Tests for tapest_client API functions.

Verifies each public API operation (ingest, extract, delete, metadata,
batch) sends correct requests and handles success, error, and 202 retry
responses. All HTTP calls are mocked via the ``requests_fx`` fixture.
"""


import re
from base64 import b64encode

import pytest
from tusclient.exceptions import TusCommunicationError

from tapest_client import client
from tapest_client.client import (
    _tus_storage_path,
    FileAlreadyExistsError,
    TapestClientError,
    ingest_file,
    recache_file,
    extract_file,
    extract_file_with_metadata,
    attempt_extract,
    delete_file,
    retrieve_file_metadata,
    update_file_metadata,
    retrieve_metadata,
    mark_required,
    retrieve_status,
    _tus_upload_file,
)

from tests.conftest import mock_response

SAMPLE_METADATA = {
    "identifier": "/pkg/file.dat",
    "size": 11,
    "checksum": (
        "sha256:b94d27b9934d3e08a52e52d7da7dab" "fac484efe37a5380ee9088f7ace2efcde9"
    ),
    "created": "2025-01-01T00:00:00Z",
    "modified": "2025-06-01T12:00:00Z",
    # Full model: file-level root fields plus the per-copy map.
    "ingested": "2025-01-01T00:00:00Z",
    "copies": {
        "tape-cache": {
            "stored": "2025-01-01T00:00:00Z",
            "checked": "2025-01-01T00:00:00Z",
        },
        "tape-01": {
            "stored": "2025-06-01T12:00:00Z",
            "checked": "2025-06-01T12:00:00Z",
            "storage_metadata": {"volume": "VOL001"},
        },
    },
}


def _download_response(data=b"hello world"):
    """Build a 200 response whose iter_content yields *data*."""
    return mock_response(200, content=data)


# === ingest_file ===


def test_ingest_success(tmp_path, config_fx, requests_fx):
    """Successful ingest returns metadata from API."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["put"] = mock_response(201, json_data=SAMPLE_METADATA)
    result = ingest_file(config_fx(), "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_ingest_http_error(tmp_path, config_fx, requests_fx):
    """HTTP error raises TapestClientError with status code."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["put"] = mock_response(500, text="Server Error")
    with pytest.raises(TapestClientError, match="500"):
        ingest_file(config_fx(), "/pkg/file.dat", str(path))


def test_ingest_identical_duplicate_returns_metadata(
    tmp_path, config_fx, requests_fx
):
    """A 409 carrying the identical_duplicate marker is an idempotent success:
    ingest_file returns the body rather than raising."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    body = {
        "detail": "An identical file already exists",
        "error": "identical_duplicate",
        "metadata": SAMPLE_METADATA,
    }
    requests_fx.responses["put"] = mock_response(409, json_data=body)
    result = ingest_file(config_fx(), "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_ingest_conflict_without_marker_raises(
    tmp_path, config_fx, requests_fx
):
    """A 409 for a different file (no marker) is a genuine conflict.

    Whether to replace the stored file is the caller's decision, so the
    error is typed and also a builtin FileExistsError.
    """
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["put"] = mock_response(
        409, json_data={"detail": "A file already exists"}
    )
    with pytest.raises(FileAlreadyExistsError, match="already exists") as err:
        ingest_file(config_fx(), "/pkg/file.dat", str(path))
    assert isinstance(err.value, FileExistsError)
    assert isinstance(err.value, TapestClientError)


# === recache_file ===


def test_recache_success(tmp_path, config_fx, requests_fx):
    """Successful PUT recache verifies file and returns metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["get"] = mock_response(200, json_data=SAMPLE_METADATA)
    requests_fx.responses["put"] = mock_response(201, json_data=SAMPLE_METADATA)
    result = recache_file(
        config_fx(), "/pkg/file.dat", str(path), use_tus=False
    )
    assert result == SAMPLE_METADATA
    assert requests_fx.calls["put"]["headers"]["X-TAPEST-Recache"] == "true"


def test_recache_size_mismatch(tmp_path, config_fx, requests_fx):
    """Raises if local file size differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"short")
    meta = {**SAMPLE_METADATA, "size": 99999}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    with pytest.raises(TapestClientError, match="size"):
        recache_file(config_fx(), "/pkg/file.dat", str(path), use_tus=False)


def test_recache_checksum_mismatch(tmp_path, config_fx, requests_fx):
    """Raises if local file checksum differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    with pytest.raises(TapestClientError, match="checksum"):
        recache_file(config_fx(), "/pkg/file.dat", str(path), use_tus=False)


def test_recache_tus_success(tmp_path, config_fx, mock_tus_endpoints):
    """Default recache uploads over TUS with the X-TAPEST-Recache marker."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")  # matches SAMPLE_METADATA size + checksum
    config = config_fx()
    # Override the stub /metadata so the pre-check gets real fields.
    mock_tus_endpoints.get(f"{config.host}/metadata", json=SAMPLE_METADATA)

    result = recache_file(config, "/pkg/file.dat", str(path))

    assert result == SAMPLE_METADATA
    posts = [
        r for r in mock_tus_endpoints.request_history
        if r.method == "POST" and r.url.endswith("/tus")
    ]
    assert len(posts) == 1
    meta_header = posts[0].headers["Upload-Metadata"]
    recache_pair = f"X-TAPEST-Recache {b64encode(b'true').decode()}"
    assert recache_pair in meta_header


def test_recache_tus_size_mismatch(tmp_path, config_fx, mock_tus_endpoints):
    """TUS recache pre-check fails on size before any upload starts."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"short")
    config = config_fx()
    meta = {**SAMPLE_METADATA, "size": 99999}
    mock_tus_endpoints.get(f"{config.host}/metadata", json=meta)
    with pytest.raises(TapestClientError, match="size"):
        recache_file(config, "/pkg/file.dat", str(path))
    assert not [
        r for r in mock_tus_endpoints.request_history if r.method == "POST"
    ]


# === extract_file ===


def test_extract_delegates(tmp_path, config_fx, requests_fx):
    """extract_file fetches metadata then delegates."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = [
        mock_response(200, json_data=SAMPLE_METADATA),
        _download_response(),
    ]
    result = extract_file(config_fx(), "/pkg/file.dat", str(dest))
    assert result == SAMPLE_METADATA


# === extract_file_with_metadata ===


def test_extract_with_metadata_success(tmp_path, config_fx, requests_fx):
    """Successful extract writes file and returns metadata."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = _download_response()
    result = extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA
    assert dest.read_bytes() == b"hello world"


def test_extract_with_metadata_already_exists(tmp_path, config_fx):
    """Raises FileAlreadyExistsError if the destination is taken."""
    dest = tmp_path / "out.dat"
    dest.write_bytes(b"existing")
    with pytest.raises(FileAlreadyExistsError, match="already exists"):
        extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_size_mismatch_cleans_up(
    tmp_path, config_fx, requests_fx
):
    """Size mismatch removes file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "size": 99999}
    requests_fx.responses["get"] = _download_response()
    with pytest.raises(TapestClientError, match="size"):
        extract_file_with_metadata(config_fx(cleanup_on_fail=True), meta, str(dest))
    assert not dest.exists()


def test_extract_with_metadata_checksum_mismatch(tmp_path, config_fx, requests_fx):
    """Raises if downloaded file checksum differs from metadata."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    requests_fx.responses["get"] = _download_response()
    with pytest.raises(TapestClientError, match="checksum"):
        extract_file_with_metadata(config_fx(), meta, str(dest))


def test_extract_with_metadata_retry_202(tmp_path, config_fx, requests_fx):
    """202 Retry-After triggers retry; succeeds on second attempt."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = [
        mock_response(202, headers={"Retry-After": "0"}),
        _download_response(),
    ]
    result = extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA


def test_extract_with_metadata_max_attempts(tmp_path, config_fx, requests_fx):
    """Raises after exhausting all retry attempts on repeated 202."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = mock_response(202, headers={"Retry-After": "0"})
    with pytest.raises(TapestClientError, match="file unavailable after"):
        extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_http_error_cleans_up(tmp_path, config_fx, requests_fx):
    """HTTP error removes partial file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = mock_response(500, text="Error")
    with pytest.raises(TapestClientError, match="500"):
        extract_file_with_metadata(
            config_fx(cleanup_on_fail=True), SAMPLE_METADATA, str(dest)
        )


def test_attempt_extract_ready(tmp_path, config_fx, requests_fx):
    """A 200 writes the file and reports it ready."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = _download_response()
    ready, retry_after = attempt_extract(
        config_fx(), SAMPLE_METADATA, str(dest)
    )
    assert ready is True
    assert retry_after == 0
    assert dest.read_bytes() == b"hello world"


def test_attempt_extract_not_ready(tmp_path, config_fx, requests_fx):
    """A 202 reports not ready with the server's Retry-After and writes
    nothing, so a caller can mark the file and come back for it."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = mock_response(
        202, headers={"Retry-After": "7"}
    )
    ready, retry_after = attempt_extract(
        config_fx(), SAMPLE_METADATA, str(dest)
    )
    assert ready is False
    assert retry_after == 7
    assert not dest.exists()


def test_extract_with_metadata_creates_parent_dirs(tmp_path, config_fx, requests_fx):
    """Missing parent directories are created automatically."""
    dest = tmp_path / "sub" / "dir" / "out.dat"
    requests_fx.responses["get"] = _download_response()
    extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))
    assert dest.exists()


# === simple wrappers (delete, metadata, status) ===


def test_delete_success(config_fx, requests_fx):
    """Successful delete returns None."""
    requests_fx.responses["delete"] = mock_response(204)
    assert delete_file(config_fx(), "/pkg/file.dat") is None


def test_retrieve_file_metadata_success(config_fx, requests_fx):
    """Successful retrieval returns metadata dict."""
    requests_fx.responses["get"] = mock_response(200, json_data=SAMPLE_METADATA)
    assert retrieve_file_metadata(config_fx(), "/pkg/file.dat") == SAMPLE_METADATA


def test_update_file_metadata_success(config_fx, requests_fx):
    """Successful update returns updated metadata."""
    updated = {**SAMPLE_METADATA, "custom": "value"}
    requests_fx.responses["patch"] = mock_response(200, json_data=updated)
    result = update_file_metadata(config_fx(), "/pkg/file.dat", {"custom": "value"})
    assert result["custom"] == "value"


def test_retrieve_metadata_success(config_fx, requests_fx):
    """Successful query returns metadata list."""
    requests_fx.responses["post"] = mock_response(200, json_data=[SAMPLE_METADATA])
    result = retrieve_metadata(config_fx(), query={"status": "stored"})
    assert result == [SAMPLE_METADATA]


def test_retrieve_metadata_default_query(config_fx, requests_fx):
    """Omitted query sends empty dict to API."""
    requests_fx.responses["post"] = mock_response(200, json_data=[])
    retrieve_metadata(config_fx())
    assert requests_fx.calls["post"]["json"] == {}


def test_retrieve_metadata_regexes_not_normalized(config_fx, requests_fx):
    """Regex patterns are sent verbatim, not leading-slash normalized like
    prefixes/identifiers."""
    requests_fx.responses["post"] = mock_response(200, json_data=[])
    retrieve_metadata(config_fx(), query={"regexes": [r"2022/.*\.dat$"]})
    assert requests_fx.calls["post"]["json"] == {
        "regexes": [r"2022/.*\.dat$"]
    }


def test_retrieve_metadata_parent_passthrough(config_fx, requests_fx):
    """The parent path is forwarded to the API."""
    requests_fx.responses["post"] = mock_response(200, json_data=[])
    retrieve_metadata(config_fx(), query={"parent": "/dir/"})
    assert requests_fx.calls["post"]["json"] == {
        "parent": "/dir/",
    }


def test_mark_required_success(config_fx, requests_fx):
    """A successful mark returns the service summary and forwards the query."""
    summary = {"matched": 3, "totalSize": 99, "marked": 2}
    requests_fx.responses["post"] = mock_response(200, json_data=summary)
    result = mark_required(config_fx(), query={"prefixes": ["/p"]})
    assert result == summary
    assert requests_fx.calls["post"]["json"] == {"prefixes": ["/p"]}


def test_mark_required_refusal_raises_with_detail(config_fx, requests_fx):
    """A refusal (e.g. the set exceeds the maximum) surfaces the service
    detail, not the raw body."""
    requests_fx.responses["post"] = mock_response(
        413,
        json_data={"detail": "Extract size 9 bytes exceeds the maximum 1"},
    )
    with pytest.raises(TapestClientError, match="exceeds the maximum"):
        mark_required(config_fx(), query={"prefixes": ["/p"]})


def test_mark_required_refusal_non_dict_body_does_not_crash(
    config_fx, requests_fx
):
    """A JSON error body that is not an object surfaces cleanly, no crash."""
    requests_fx.responses["post"] = mock_response(
        413, json_data=["too large"], text='["too large"]'
    )
    with pytest.raises(TapestClientError, match="413"):
        mark_required(config_fx(), query={"prefixes": ["/p"]})


def test_retrieve_status_success(config_fx, requests_fx):
    """Successful status check returns status dict."""
    requests_fx.responses["get"] = mock_response(200, json_data={"status": "ok"})
    assert retrieve_status(config_fx()) == {"status": "ok"}


# === ca_cert_path ===


def test_ca_cert_path_used_as_verify(config_fx, requests_fx):
    """ca_cert_path is passed as the verify parameter when set."""
    ca = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    retrieve_status(config_fx(verify_ssl=True, ca_cert_path=ca))
    assert requests_fx.calls["get"]["verify"] == ca


def test_ca_cert_path_ignored_when_verify_ssl_false(config_fx, requests_fx):
    """verify_ssl=False takes precedence over ca_cert_path."""
    retrieve_status(config_fx(verify_ssl=False, ca_cert_path="/some/ca.pem"))
    assert requests_fx.calls["get"]["verify"] is False


@pytest.mark.usefixtures("mock_tus_endpoints")
def test_tus_upload(tmp_path, config_fx):
    """Test that we can upload with the tusclient without any issues."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"my test file")
    identifier = "this/is/my/test/file.dat"
    config = config_fx()
    _tus_upload_file(
        config=config,
        identifier=identifier,
        path=str(path),
        chunk_size="16.12 MiB",
    )


def test_tus_offset_conflict_is_not_reported_as_a_duplicate(
    tmp_path, config_fx, mock_tus_endpoints
):
    """TUS answers 409 to a chunk at an offset the server does not hold.
    The message must not claim another file exists under the identifier.
    """
    path = tmp_path / "big.dat"
    path.write_bytes(b"x" * 64)
    mock_tus_endpoints.patch(
        re.compile(f"^{config_fx().host}/tus"), text="", status_code=409
    )
    with pytest.raises(TapestClientError) as error:
        _tus_upload_file(
            config=config_fx(), identifier="/big.dat", path=str(path)
        )
    assert "offset" in str(error.value)
    assert "already exists" not in str(error.value)


def test_tus_conflict_document_over_a_chunk_reports_a_duplicate(
    tmp_path, config_fx, mock_tus_endpoints
):
    """A conflict can surface over a chunk, and arrives as an error document.
    No field of it is read.
    """
    path = tmp_path / "big.dat"
    path.write_bytes(b"x" * 64)
    mock_tus_endpoints.patch(
        re.compile(f"^{config_fx().host}/tus"),
        json={"an unread field": "of the service's own error document"},
        status_code=409,
    )
    with pytest.raises(FileAlreadyExistsError, match="already exists"):
        _tus_upload_file(
            config=config_fx(), identifier="/big.dat", path=str(path)
        )


def test_tus_bodyless_conflict_at_creation_is_not_an_offset_refusal(
    tmp_path, config_fx, mock_tus_endpoints
):
    """An upload that has not started has no offset to refuse, so a 409 to the
    creation request is a conflict at the identifier however thin its body.
    """
    path = tmp_path / "big.dat"
    path.write_bytes(b"x" * 64)
    mock_tus_endpoints.post(
        f"{config_fx().host}/tus", text="", status_code=409
    )
    with pytest.raises(FileAlreadyExistsError, match="already exists"):
        _tus_upload_file(
            config=config_fx(), identifier="/big.dat", path=str(path)
        )


def test_tus_failure_that_is_not_a_conflict_is_left_as_it_is(
    tmp_path, config_fx, mock_tus_endpoints
):
    """Only a 409 is renamed, other failures reach the caller as tuspy raised
    them.
    """
    path = tmp_path / "big.dat"
    path.write_bytes(b"x" * 64)
    mock_tus_endpoints.patch(
        re.compile(f"^{config_fx().host}/tus"), text="", status_code=500
    )
    with pytest.raises(TusCommunicationError):
        _tus_upload_file(
            config=config_fx(), identifier="/big.dat", path=str(path)
        )


# === TUS resume-state files ===


def test_two_identifiers_get_separate_state_files():
    """Two identifiers must not share a resume-state file."""
    assert _tus_storage_path("/a.dat") != _tus_storage_path("/b.dat")


def test_same_identifier_gets_the_same_state_file():
    """Resuming an interrupted upload must find its stored URL."""
    assert _tus_storage_path("/a.dat") == _tus_storage_path("/a.dat")


def test_state_file_name_is_filesystem_safe():
    """Identifiers hold slashes and arbitrary characters."""
    name = _tus_storage_path("/deep/path/with spaces/and:colons.dat").name
    assert "/" not in name and name.isalnum()


def test_tus_duplicate_raises_file_already_exists(
    tmp_path, config_fx, mock_tus_endpoints
):
    """A TUS upload onto a taken identifier raises the same error as PUT."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"my test file")
    config = config_fx()
    mock_tus_endpoints.post(
        f"{config.host}/tus",
        status_code=409,
        json={"detail": "A file already exists"},
    )
    with pytest.raises(FileAlreadyExistsError, match="already exists"):
        _tus_upload_file(
            config=config, identifier="/dup.dat", path=str(path)
        )


@pytest.mark.usefixtures("mock_tus_endpoints")
def test_tus_upload_of_same_file_under_two_identifiers(tmp_path, config_fx):
    """Two uploads of one local file under two identifiers both succeed."""
    path = tmp_path / "same.dat"
    path.write_bytes(b"the very same bytes")
    config = config_fx()
    for identifier in ("/dup/first.dat", "/dup/second.dat"):
        _tus_upload_file(
            config=config, identifier=identifier, path=str(path)
        )


# === Automatic selection of the upload path ===


@pytest.mark.usefixtures("mock_tus_endpoints")
def test_ingest_file_uses_tus_when_the_file_is_larger_than_the_limit(
    tmp_path, config_fx, monkeypatch
):
    """A file that is larger than the limit of the service goes to TUS."""
    path = tmp_path / "big.dat"
    path.write_bytes(b"x" * 100)
    used = {}

    def fake_tus(**kwargs):
        used["tus"] = True
        return {"ok": True}

    client._UPLOAD_LIMIT_CACHE.clear()
    monkeypatch.setattr(client, "_max_put_bytes", lambda config: 10)
    monkeypatch.setattr(client, "_tus_upload_file", fake_tus)
    result = client.ingest_file(config_fx(), "/big.dat", str(path))
    assert used.get("tus") is True
    assert result == {"ok": True}


def test_ingest_file_uses_put_when_the_file_is_smaller_than_the_limit(
    tmp_path, config_fx, monkeypatch, requests_fx
):
    """A file that is smaller than the limit uses PUT."""
    path = tmp_path / "small.dat"
    path.write_bytes(b"x" * 5)
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA
    )

    def fail_tus(**kwargs):
        raise AssertionError("TUS must not be used")

    monkeypatch.setattr(client, "_max_put_bytes", lambda config: 1000)
    monkeypatch.setattr(client, "_tus_upload_file", fail_tus)
    client.ingest_file(config_fx(), "/small.dat", str(path))
    assert requests_fx.calls["put"] is not None


@pytest.mark.usefixtures("mock_tus_endpoints")
def test_tus_upload_uses_the_chunk_size_of_the_configuration(
    tmp_path, config_fx, monkeypatch
):
    """A TUS upload uses tus_chunk_size when the caller gives no value."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"my test file")
    config = config_fx()
    config.tus_chunk_size = "500 MiB"
    seen = {}
    real = client.parse_chunk_size

    def spy(chunk_size):
        seen["chunk_size"] = chunk_size
        return real(chunk_size=chunk_size)

    monkeypatch.setattr(client, "parse_chunk_size", spy)
    client._tus_upload_file(
        config=config, identifier="/file.dat", path=str(path)
    )
    assert seen["chunk_size"] == "500 MiB"


def test_max_put_bytes_uses_the_limit_of_the_service(config_fx, monkeypatch):
    """The limit comes from the status of the service."""
    client._UPLOAD_LIMIT_CACHE.clear()
    monkeypatch.setattr(
        client, "retrieve_status",
        lambda config: {"max_non_tus_upload_bytes": 4242},
    )
    assert client._max_put_bytes(config_fx()) == 4242


def test_max_put_bytes_assumes_a_limit_when_the_service_gives_none(
    config_fx, monkeypatch
):
    """An older service gives no limit, thus the client assumes one."""
    client._UPLOAD_LIMIT_CACHE.clear()
    monkeypatch.setattr(client, "retrieve_status", lambda config: {})
    assert client._max_put_bytes(config_fx()) == (
        client._ASSUMED_MAX_PUT_BYTES
    )
