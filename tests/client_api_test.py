"""Tests for tapest_client API functions.

Verifies each public API operation (ingest, extract, delete, metadata,
batch) sends correct requests and handles success, error, and 202 retry
responses. All HTTP calls are mocked.
"""

from unittest import mock

import pytest

from tapest_client.config import Config
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

CONFIG = Config(
    ice_token="tok123",
    ice_host="https://ice.example.com",
    storage_account_name="testaccount",
    max_retry_attempts=2,
    default_sleep_duration=0,
    verify_ssl=False,
)

CLEANUP_CONFIG = Config(
    ice_token="tok123",
    ice_host="https://ice.example.com",
    max_retry_attempts=2,
    default_sleep_duration=0,
    verify_ssl=False,
    cleanup_on_fail=True,
)

SAMPLE_METADATA = {
    "identifier": "/pkg/file.dat",
    "size": 11,
    "checksum": "sha256:b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
    "created": "2025-01-01T00:00:00Z",
    "modified": "2025-06-01T12:00:00Z",
    "storage": "tape-01",
}


def _mock_response(status_code, json_data=None, text="", headers=None):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    resp.headers = headers or {}
    return resp


# === ingest_file ===


def test_ingest_success(tmp_path):
    """Successful ingest returns metadata from API."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    resp = _mock_response(201, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.put", return_value=resp):
        result = ingest_file(CONFIG, "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_ingest_http_error(tmp_path):
    """HTTP error raises TapestClientError with status code."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    resp = _mock_response(500, text="Server Error")
    with mock.patch("tapest_client.client.requests.put", return_value=resp):
        with pytest.raises(TapestClientError, match="500"):
            ingest_file(CONFIG, "/pkg/file.dat", str(path))


def test_ingest_storage_name_header(tmp_path):
    """Storage name is sent as X-ICE-Storage header."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"data")
    resp = _mock_response(201, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.put", return_value=resp) as m:
        ingest_file(CONFIG, "/id", str(path), storage_name="tape-01")
    assert m.call_args[1]["headers"]["X-ICE-Storage"] == "tape-01"


# === recache_file ===


def test_recache_success(tmp_path):
    """Successful recache verifies file and returns metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
    resp_put = _mock_response(201, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta), \
         mock.patch("tapest_client.client.requests.put", return_value=resp_put):
        result = recache_file(CONFIG, "/pkg/file.dat", str(path))
    assert result == SAMPLE_METADATA


def test_recache_size_mismatch(tmp_path):
    """Raises if local file size differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"short")
    meta = {**SAMPLE_METADATA, "size": 99999}
    resp_meta = _mock_response(200, json_data=meta)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
        with pytest.raises(TapestClientError, match="size"):
            recache_file(CONFIG, "/pkg/file.dat", str(path))


def test_recache_checksum_mismatch(tmp_path):
    """Raises if local file checksum differs from ingested metadata."""
    path = tmp_path / "file.dat"
    path.write_bytes(b"hello world")
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    resp_meta = _mock_response(200, json_data=meta)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
        with pytest.raises(TapestClientError, match="checksum"):
            recache_file(CONFIG, "/pkg/file.dat", str(path))


# === extract_file ===


def test_extract_delegates(tmp_path):
    """extract_file fetches metadata then delegates to extract_file_with_metadata."""
    dest = tmp_path / "out.dat"
    resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
    resp_get = _mock_response(200)
    resp_get.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get",
                    side_effect=[resp_meta, resp_get]):
        result = extract_file(CONFIG, "/pkg/file.dat", str(dest))
    assert result == SAMPLE_METADATA


# === extract_file_with_metadata ===


def test_extract_with_metadata_success(tmp_path):
    """Successful extract writes file and returns metadata."""
    dest = tmp_path / "out.dat"
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        result = extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA
    assert dest.read_bytes() == b"hello world"


def test_extract_with_metadata_already_exists(tmp_path):
    """Raises if destination file already exists."""
    dest = tmp_path / "out.dat"
    dest.write_bytes(b"existing")
    with pytest.raises(TapestClientError, match="already exists"):
        extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_size_mismatch_cleans_up(tmp_path):
    """Size mismatch after download removes file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "size": 99999}
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        with pytest.raises(TapestClientError, match="size"):
            extract_file_with_metadata(CLEANUP_CONFIG, meta, str(dest))
    assert not dest.exists()


def test_extract_with_metadata_checksum_mismatch(tmp_path):
    """Raises if downloaded file checksum differs from metadata."""
    dest = tmp_path / "out.dat"
    meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        with pytest.raises(TapestClientError, match="checksum"):
            extract_file_with_metadata(CONFIG, meta, str(dest))


def test_extract_with_metadata_retry_202(tmp_path):
    """202 Retry-After triggers retry; succeeds on second attempt."""
    dest = tmp_path / "out.dat"
    resp_202 = _mock_response(202, headers={"Retry-After": "0"})
    resp_200 = _mock_response(200)
    resp_200.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get",
                    side_effect=[resp_202, resp_200]):
        result = extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
    assert result == SAMPLE_METADATA


def test_extract_with_metadata_max_attempts(tmp_path):
    """Raises after exhausting all retry attempts on repeated 202."""
    dest = tmp_path / "out.dat"
    resp_202 = _mock_response(202, headers={"Retry-After": "0"})
    with mock.patch("tapest_client.client.requests.get", return_value=resp_202):
        with pytest.raises(TapestClientError, match="attempts exceeded"):
            extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_http_error_cleans_up(tmp_path):
    """HTTP error removes partial file when cleanup_on_fail is set."""
    dest = tmp_path / "out.dat"
    resp = _mock_response(500, text="Error")
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        with pytest.raises(TapestClientError, match="500"):
            extract_file_with_metadata(CLEANUP_CONFIG, SAMPLE_METADATA, str(dest))


def test_extract_with_metadata_next_identifier(tmp_path):
    """next_identifier is sent as X-ICE-Next-File header for prefetch."""
    dest = tmp_path / "out.dat"
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp) as m:
        extract_file_with_metadata(
            CONFIG, SAMPLE_METADATA, str(dest), next_identifier="/next")
    assert "X-ICE-Next-File" in m.call_args[1]["headers"]


def test_extract_with_metadata_creates_parent_dirs(tmp_path):
    """Missing parent directories are created automatically."""
    dest = tmp_path / "sub" / "dir" / "out.dat"
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
    assert dest.exists()


# === simple wrappers (delete, metadata, status) ===


def test_delete_success():
    """Successful delete returns None."""
    resp = _mock_response(204)
    with mock.patch("tapest_client.client.requests.delete", return_value=resp):
        assert delete_file(CONFIG, "/pkg/file.dat") is None


def test_retrieve_file_metadata_success():
    """Successful retrieval returns metadata dict."""
    resp = _mock_response(200, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        assert retrieve_file_metadata(CONFIG, "/pkg/file.dat") == SAMPLE_METADATA


def test_update_file_metadata_success():
    """Successful update returns updated metadata."""
    updated = {**SAMPLE_METADATA, "custom": "value"}
    resp = _mock_response(200, json_data=updated)
    with mock.patch("tapest_client.client.requests.patch", return_value=resp):
        assert update_file_metadata(CONFIG, "/pkg/file.dat", {"custom": "value"})["custom"] == "value"


def test_retrieve_metadata_success():
    """Successful query returns metadata list."""
    resp = _mock_response(200, json_data=[SAMPLE_METADATA])
    with mock.patch("tapest_client.client.requests.post", return_value=resp):
        assert retrieve_metadata(CONFIG, query={"status": "stored"}) == [SAMPLE_METADATA]


def test_retrieve_metadata_default_query():
    """Omitted query sends empty dict to API."""
    resp = _mock_response(200, json_data=[])
    with mock.patch("tapest_client.client.requests.post", return_value=resp) as m:
        retrieve_metadata(CONFIG)
    assert m.call_args[1]["json"] == {}


def test_retrieve_status_success():
    """Successful status check returns status dict."""
    resp = _mock_response(200, json_data={"status": "ok"})
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        assert retrieve_status(CONFIG) == {"status": "ok"}


# === ingest_files_from_directory ===


def test_ingest_directory_not_a_directory(tmp_path):
    """Raises if path does not exist."""
    with pytest.raises(TapestClientError, match="does not exist"):
        ingest_files_from_directory(CONFIG, str(tmp_path / "nope"))


def test_ingest_directory_new_files(tmp_path):
    """Files not yet on server are ingested."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"aaa")
    (root / "b.dat").write_bytes(b"bbb")

    resp_201 = _mock_response(201, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.get",
                    side_effect=TapestClientError("404 Not Found")), \
         mock.patch("tapest_client.client.requests.put",
                    return_value=resp_201):
        result = ingest_files_from_directory(CONFIG, str(root))
    assert len(result) == 2


def test_ingest_directory_skip_existing(tmp_path):
    """skip=True skips files that match server metadata."""
    root = tmp_path / "pkg"
    root.mkdir()
    path = root / "a.dat"
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "size": 11, "checksum": checksum}
    resp_meta = _mock_response(200, json_data=meta)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
        result = ingest_files_from_directory(CONFIG, str(root), skip=True)
    assert len(result) == 0


def test_ingest_directory_force_replaces(tmp_path):
    """force=True deletes and re-ingests files that differ from server."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"new content")
    meta = {**SAMPLE_METADATA, "size": 5, "checksum": "sha256:old"}
    resp_meta = _mock_response(200, json_data=meta)
    resp_del = _mock_response(204)
    resp_put = _mock_response(201, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta), \
         mock.patch("tapest_client.client.requests.delete", return_value=resp_del), \
         mock.patch("tapest_client.client.requests.put", return_value=resp_put):
        result = ingest_files_from_directory(CONFIG, str(root), force=True)
    assert len(result) == 1


def test_ingest_directory_conflict_raises(tmp_path):
    """Raises if file exists on server and neither skip nor force is set."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.dat").write_bytes(b"data")
    resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
    with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
        with pytest.raises(TapestClientError, match="already exists"):
            ingest_files_from_directory(CONFIG, str(root))


def test_ingest_directory_skips_subdirectories(tmp_path):
    """Only regular files are ingested, subdirectories are skipped."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "a.dat").write_bytes(b"data")
    with mock.patch("tapest_client.client.requests.get",
                    side_effect=TapestClientError("404")), \
         mock.patch("tapest_client.client.requests.put",
                    return_value=_mock_response(201, json_data=SAMPLE_METADATA)):
        result = ingest_files_from_directory(CONFIG, str(root))
    assert len(result) == 1


# === extract_files_to_directory ===


def test_extract_directory_not_a_directory(tmp_path):
    """Raises if target directory does not exist."""
    with pytest.raises(TapestClientError, match="does not exist"):
        extract_files_to_directory(CONFIG, [], str(tmp_path / "nope"))


def test_extract_directory_new_files(tmp_path):
    """Files are downloaded and written to the target directory."""
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        result = extract_files_to_directory(CONFIG, [meta], str(tmp_path))
    assert len(result) == 1
    assert (tmp_path / "pkg" / "file.dat").exists()


def test_extract_directory_skip_existing(tmp_path):
    """skip=True skips files that match local copy."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
            "size": 11, "checksum": checksum}
    result = extract_files_to_directory(CONFIG, [meta], str(tmp_path), skip=True)
    assert len(result) == 0


def test_extract_directory_force_replaces(tmp_path):
    """force=True re-downloads files that differ from local copy."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old content")
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp):
        result = extract_files_to_directory(
            CONFIG, [meta], str(tmp_path), force=True)
    assert len(result) == 1


def test_extract_directory_conflict_raises(tmp_path):
    """Raises if file exists locally and neither skip nor force is set."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"data")
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
    with pytest.raises(TapestClientError, match="already exists"):
        extract_files_to_directory(CONFIG, [meta], str(tmp_path))


def test_extract_directory_next_identifier(tmp_path):
    """Next file identifier is sent as prefetch hint header."""
    meta1 = {**SAMPLE_METADATA, "identifier": "/pkg/a.dat"}
    meta2 = {**SAMPLE_METADATA, "identifier": "/pkg/b.dat"}
    resp = _mock_response(200)
    resp.iter_content.return_value = [b"hello world"]
    with mock.patch("tapest_client.client.requests.get", return_value=resp) as m:
        extract_files_to_directory(CONFIG, [meta1, meta2], str(tmp_path))
    assert "X-ICE-Next-File" in m.call_args_list[0][1]["headers"]


def test_extract_directory_force_same_raises(tmp_path):
    """force=True with matching file raises rather than silently skipping."""
    path = tmp_path / "pkg" / "file.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"hello world")
    checksum = generate_checksum(path)
    meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
            "size": 11, "checksum": checksum}
    with pytest.raises(TapestClientError, match="already exists"):
        extract_files_to_directory(
            CONFIG, [meta], str(tmp_path), force=True)
