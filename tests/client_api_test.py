"""Tests for tapest_client API functions.

Verifies each public API operation (ingest, extract, delete, metadata,
batch) sends correct requests and handles success, error, and 202 retry
responses. All HTTP calls are mocked via the ``requests_fx`` fixture.
"""

import pytest

from tapest_client.client import (
    TapestClientError,
    ingest_file,
    recache_file,
    extract_file,
    extract_file_with_metadata,
    delete_file,
    retrieve_file_metadata,
    update_file_metadata,
    retrieve_metadata,
    retrieve_status,
    ingest_files_from_directory,
    extract_files_to_directory,
    generate_checksum,
)

from tests.conftest import mock_response

SAMPLE_METADATA = {
    "identifier": "/pkg/file.dat",
    "size": 11,
    "checksum": ("sha256:b94d27b9934d3e08a52e52d7da7dab"
                 "fac484efe37a5380ee9088f7ace2efcde9"),
    "created": "2025-01-01T00:00:00Z",
    "modified": "2025-06-01T12:00:00Z",
    "storage": "tape-01",
}


def _download_response(data=b"hello world"):
    """Build a 200 response whose iter_content yields *data*."""
    return mock_response(200, content=data)


# === ingest_file ===


def test_ingest_success(tmp_path, config_fx, requests_fx):
    """Successful ingest returns metadata from API."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    result = ingest_file(config_fx(), "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_ingest_http_error(tmp_path, config_fx, requests_fx):
    """HTTP error raises TapestClientError with status code."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["put"] = mock_response(500, text="Server Error")
    with pytest.raises(TapestClientError, match="500"):
        ingest_file(config_fx(), "/pkg/file.dat", str(path))


def test_ingest_storage_name_header(tmp_path, config_fx, requests_fx):
    """Storage name is sent as X-ICE-Storage header."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"data")
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    ingest_file(config_fx(), "/id", str(path), storage_name="tape-01")
    assert requests_fx.calls["put"]["headers"]["X-ICE-Storage"] \
        == "tape-01"


# === recache_file ===


def test_recache_success(tmp_path, config_fx, requests_fx):
    """Successful recache verifies file and returns metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    requests_fx.responses["get"] = mock_response(
        200, json_data=SAMPLE_METADATA)
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    result = recache_file(config_fx(), "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_recache_size_mismatch(tmp_path, config_fx, requests_fx):
    """Raises if local file size differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"short")
    meta = {**SAMPLE_METADATA, "size": 99999}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    with pytest.raises(TapestClientError, match="size"):
        recache_file(config_fx(), "/pkg/file.dat", str(path))


def test_recache_checksum_mismatch(tmp_path, config_fx, requests_fx):
    """Raises if local file checksum differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    with pytest.raises(TapestClientError, match="checksum"):
        recache_file(config_fx(), "/pkg/file.dat", str(path))


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
    result = extract_file_with_metadata(
        config_fx(), SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA
    assert dest.read_bytes() == b"hello world"


def test_extract_with_metadata_already_exists(tmp_path, config_fx):
    """Raises if destination file already exists."""
    dest = tmp_path / "out.dat"
    dest.write_bytes(b"existing")
    with pytest.raises(TapestClientError, match="already exists"):
        extract_file_with_metadata(config_fx(), SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_size_mismatch_cleans_up(
        tmp_path, config_fx, requests_fx):
    """Size mismatch removes file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "size": 99999}
    requests_fx.responses["get"] = _download_response()
    with pytest.raises(TapestClientError, match="size"):
        extract_file_with_metadata(
            config_fx(cleanup_on_fail=True), meta, str(dest))
    assert not dest.exists()


def test_extract_with_metadata_checksum_mismatch(
        tmp_path, config_fx, requests_fx):
    """Raises if downloaded file checksum differs from metadata."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    requests_fx.responses["get"] = _download_response()
    with pytest.raises(TapestClientError, match="checksum"):
        extract_file_with_metadata(config_fx(), meta, str(dest))


def test_extract_with_metadata_retry_202(
        tmp_path, config_fx, requests_fx):
    """202 Retry-After triggers retry; succeeds on second attempt."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = [
        mock_response(202, headers={"Retry-After": "0"}),
        _download_response(),
    ]
    result = extract_file_with_metadata(
        config_fx(), SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA


def test_extract_with_metadata_max_attempts(
        tmp_path, config_fx, requests_fx):
    """Raises after exhausting all retry attempts on repeated 202."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = mock_response(
        202, headers={"Retry-After": "0"})
    with pytest.raises(TapestClientError, match="file unavailable after"):
        extract_file_with_metadata(
            config_fx(), SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_http_error_cleans_up(
        tmp_path, config_fx, requests_fx):
    """HTTP error removes partial file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = mock_response(500, text="Error")
    with pytest.raises(TapestClientError, match="500"):
        extract_file_with_metadata(
            config_fx(cleanup_on_fail=True), SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_next_identifier(
        tmp_path, config_fx, requests_fx):
    """next_identifier is sent as X-ICE-Next-File header for prefetch."""
    dest = tmp_path / "out.dat"
    requests_fx.responses["get"] = _download_response()
    extract_file_with_metadata(
        config_fx(), SAMPLE_METADATA, str(dest), next_identifier="/next")
    assert "X-ICE-Next-File" in \
        requests_fx.calls["get"]["headers"]


def test_extract_with_metadata_creates_parent_dirs(
        tmp_path, config_fx, requests_fx):
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
    requests_fx.responses["get"] = mock_response(
        200, json_data=SAMPLE_METADATA)
    assert retrieve_file_metadata(
        config_fx(), "/pkg/file.dat") == SAMPLE_METADATA


def test_update_file_metadata_success(config_fx, requests_fx):
    """Successful update returns updated metadata."""
    updated = {**SAMPLE_METADATA, "custom": "value"}
    requests_fx.responses["patch"] = mock_response(200, json_data=updated)
    result = update_file_metadata(
        config_fx(), "/pkg/file.dat", {"custom": "value"})
    assert result["custom"] == "value"


def test_retrieve_metadata_success(config_fx, requests_fx):
    """Successful query returns metadata list."""
    requests_fx.responses["post"] = mock_response(
        200, json_data=[SAMPLE_METADATA])
    result = retrieve_metadata(config_fx(), query={"status": "stored"})
    assert result == [SAMPLE_METADATA]


def test_retrieve_metadata_default_query(config_fx, requests_fx):
    """Omitted query sends empty dict to API."""
    requests_fx.responses["post"] = mock_response(200, json_data=[])
    retrieve_metadata(config_fx())
    assert requests_fx.calls["post"]["json"] == {}


def test_retrieve_status_success(config_fx, requests_fx):
    """Successful status check returns status dict."""
    requests_fx.responses["get"] = mock_response(
        200, json_data={"status": "ok"})
    assert retrieve_status(config_fx()) == {"status": "ok"}


# === ingest_files_from_directory ===


def test_ingest_directory_not_a_directory(tmp_path, config_fx):
    """Raises if path does not exist."""
    with pytest.raises(TapestClientError, match="does not exist"):
        ingest_files_from_directory(config_fx(), str(tmp_path / "nope"))


def test_ingest_directory_new_files(tmp_path, config_fx, requests_fx):
    """Files not yet on server are ingested."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"aaa")
    (root / "b.dat").write_bytes(b"bbb")
    requests_fx.responses["get"] = TapestClientError("404 Not Found")
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    result = ingest_files_from_directory(config_fx(), str(root))
    assert len(result) == 2


def test_ingest_directory_skip_existing(tmp_path, config_fx, requests_fx):
    """skip=True skips files that match server metadata."""
    root = tmp_path / "pkg"
    root.mkdir()
    path = root / "a.dat"
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "size": 11, "checksum": checksum}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    result = ingest_files_from_directory(
        config_fx(), str(root), skip=True)
    assert len(result) == 0


def test_ingest_directory_force_replaces(
        tmp_path, config_fx, requests_fx):
    """force=True deletes and re-ingests files that differ from server."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"new content")
    meta = {**SAMPLE_METADATA, "size": 5, "checksum": "sha256:old"}
    requests_fx.responses["get"] = mock_response(200, json_data=meta)
    requests_fx.responses["delete"] = mock_response(204)
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    result = ingest_files_from_directory(
        config_fx(), str(root), force=True)
    assert len(result) == 1


def test_ingest_directory_conflict_raises(
        tmp_path, config_fx, requests_fx):
    """Raises if file exists on server and neither skip nor force is set."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"data")
    requests_fx.responses["get"] = mock_response(
        200, json_data=SAMPLE_METADATA)
    with pytest.raises(TapestClientError, match="already exists"):
        ingest_files_from_directory(config_fx(), str(root))


def test_ingest_directory_skips_subdirectories(
        tmp_path, config_fx, requests_fx):
    """Only regular files are ingested, subdirectories are skipped."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "a.dat").write_bytes(b"data")
    requests_fx.responses["get"] = TapestClientError("404")
    requests_fx.responses["put"] = mock_response(
        201, json_data=SAMPLE_METADATA)
    result = ingest_files_from_directory(config_fx(), str(root))
    assert len(result) == 1


# === extract_files_to_directory ===


def test_extract_directory_not_a_directory(tmp_path, config_fx):
    """Raises if target directory does not exist."""
    with pytest.raises(TapestClientError, match="does not exist"):
        extract_files_to_directory(
            config_fx(), [], str(tmp_path / "nope"))


def test_extract_directory_new_files(tmp_path, config_fx, requests_fx):
    """Files are downloaded and written to the target directory."""
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    requests_fx.responses["get"] = _download_response()
    result = extract_files_to_directory(
        config_fx(), [meta], str(tmp_path))
    assert len(result) == 1
    assert (tmp_path / "pkg" / "file.dat").exists()


def test_extract_directory_skip_existing(tmp_path, config_fx):
    """skip=True skips files that match local copy."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
            "size": 11, "checksum": checksum}
    result = extract_files_to_directory(
        config_fx(), [meta], str(tmp_path), skip=True)
    assert len(result) == 0


def test_extract_directory_force_replaces(
        tmp_path, config_fx, requests_fx):
    """force=True re-downloads files that differ from local copy."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old content")
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    requests_fx.responses["get"] = _download_response()
    result = extract_files_to_directory(
        config_fx(), [meta], str(tmp_path), force=True)
    assert len(result) == 1


def test_extract_directory_conflict_raises(tmp_path, config_fx):
    """Raises if file exists locally and neither skip nor force is set."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"data")
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    with pytest.raises(TapestClientError, match="already exists"):
        extract_files_to_directory(
            config_fx(), [meta], str(tmp_path))


def test_extract_directory_next_identifier(
        tmp_path, config_fx, requests_fx):
    """Next file identifier is sent as prefetch hint header."""
    meta1 = {**SAMPLE_METADATA, "identifier": "/pkg/a.dat"}
    meta2 = {**SAMPLE_METADATA, "identifier": "/pkg/b.dat"}
    requests_fx.responses["get"] = _download_response()
    extract_files_to_directory(
        config_fx(), [meta1, meta2], str(tmp_path))
    assert "X-ICE-Next-File" in \
        requests_fx.all_calls["get"][0]["headers"]


def test_extract_directory_force_same_raises(tmp_path, config_fx):
    """force=True with matching file raises rather than silently skipping."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
            "size": 11, "checksum": checksum}
    with pytest.raises(TapestClientError, match="already exists"):
        extract_files_to_directory(
            config_fx(), [meta], str(tmp_path), force=True)


# === ca_cert_path ===


def test_ca_cert_path_used_as_verify(config_fx, requests_fx):
    """ca_cert_path is passed as the verify parameter when set."""
    ca = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    retrieve_status(config_fx(verify_ssl=True, ca_cert_path=ca))
    assert requests_fx.calls["get"]["verify"] == ca


def test_ca_cert_path_ignored_when_verify_ssl_false(
        config_fx, requests_fx):
    """verify_ssl=False takes precedence over ca_cert_path."""
    retrieve_status(config_fx(
        verify_ssl=False, ca_cert_path="/some/ca.pem"))
    assert requests_fx.calls["get"]["verify"] is False
