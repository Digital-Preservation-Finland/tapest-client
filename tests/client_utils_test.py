"""Tests for tapest_client utility functions and internal helpers.

Covers checksum, file comparison, cleanup, header building, URL
construction, retry logic, and the TapestClientError exception.
"""

import os
import time
from unittest import mock

import pytest

from tapest_client.config import Config
from tapest_client.client import (
    TapestClientError,
    _build_headers,
    _file_url,
    _metadata_url,
    _request_with_retry,
    generate_checksum,
    is_same_file,
    cleanup_file,
)


# === generate_checksum ===


def test_checksum_known_content(tmp_path):
    """SHA-256 of known content matches expected digest."""
    path = tmp_path / "test.bin"
    path.write_bytes(b"hello world")
    assert generate_checksum(str(path)) == \
        "sha256:b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_checksum_empty_file(tmp_path):
    """Empty file produces the sha256 empty-input digest."""
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert generate_checksum(str(path)) == \
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_checksum_pathlib_path(tmp_path):
    """Works with both str and pathlib.Path arguments."""
    path = tmp_path / "test.bin"
    path.write_bytes(b"test")
    assert generate_checksum(str(path)) == generate_checksum(path)


def test_checksum_missing_file():
    """Missing file raises OSError."""
    with pytest.raises(OSError):
        generate_checksum("/nonexistent/path/to/file")


# === is_same_file ===


def test_same_file_match(tmp_path):
    """Returns True when size and checksum both match."""
    path = tmp_path / "match.bin"
    path.write_bytes(b"hello world")
    assert is_same_file(str(path), path.stat().st_size, generate_checksum(path)) is True


def test_same_file_wrong_size(tmp_path):
    """Returns False when size does not match."""
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    assert is_same_file(str(path), 999, generate_checksum(path)) is False


def test_same_file_wrong_checksum(tmp_path):
    """Returns False when checksum does not match."""
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    assert is_same_file(str(path), path.stat().st_size, "sha256:bad") is False


def test_same_file_missing():
    """Returns False for nonexistent file."""
    assert is_same_file("/nonexistent", 0, "sha256:abc") is False


def test_same_file_short_circuits_on_size(tmp_path):
    """Skips checksum computation when size already mismatches."""
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    with mock.patch("tapest_client.client.generate_checksum") as mock_cs:
        assert is_same_file(str(path), 999, "sha256:irrelevant") is False
        mock_cs.assert_not_called()


# === cleanup_file ===


def test_cleanup_removes_when_enabled(tmp_path):
    """Deletes file when cleanup_on_fail is True."""
    path = tmp_path / "removeme.bin"
    path.write_bytes(b"data")
    cleanup_file(Config(cleanup_on_fail=True), str(path))
    assert not path.exists()


def test_cleanup_noop_when_disabled(tmp_path):
    """Does nothing when cleanup_on_fail is False (the default)."""
    path = tmp_path / "keepme.bin"
    path.write_bytes(b"data")
    cleanup_file(Config(), str(path))
    assert path.exists()


def test_cleanup_missing_file_no_error():
    """Does not raise if file is already gone."""
    cleanup_file(Config(cleanup_on_fail=True), "/nonexistent/file")


# === _build_headers ===


def test_headers_minimal():
    """Only Authorization header with just ice_token."""
    cfg = Config(ice_token="tok123")
    assert _build_headers(cfg) == {"Authorization": "Bearer tok123"}


def test_headers_all_options():
    """Account, storage name, and extra headers are all included."""
    cfg = Config(ice_token="tok", storage_account_name="acc")
    headers = _build_headers(cfg, storage_name="s1", extra={"X-Custom": "val"})
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-ICE-Account"] == "acc"
    assert headers["X-ICE-Storage"] == "s1"
    assert headers["X-Custom"] == "val"


# === _file_url / _metadata_url ===


def test_file_url():
    """Builds /file endpoint URL with encoded identifier."""
    cfg = Config(ice_host="https://ice.example.com")
    assert _file_url(cfg, "/path/to/file.dat") == \
        "https://ice.example.com/file?identifier=%2Fpath%2Fto%2Ffile.dat"


def test_metadata_url_with_identifier():
    """Builds /metadata endpoint URL with encoded identifier."""
    cfg = Config(ice_host="https://ice.example.com")
    assert _metadata_url(cfg, "/myfile.dat") == \
        "https://ice.example.com/metadata?identifier=%2Fmyfile.dat"


def test_metadata_url_without_identifier():
    """Builds bare /metadata URL when no identifier given."""
    cfg = Config(ice_host="https://ice.example.com")
    assert _metadata_url(cfg) == "https://ice.example.com/metadata"


# === _request_with_retry ===


def _mock_resp(status_code, headers=None):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


def test_retry_immediate_success():
    """Non-202 response is returned immediately."""
    resp = _mock_resp(200)
    result = _request_with_retry(lambda: resp, Config(max_retry_attempts=3), "test")
    assert result.status_code == 200


def test_retry_202_then_success():
    """Retries on 202 and returns the first non-202 response."""
    responses = [_mock_resp(202, {"Retry-After": "0"}), _mock_resp(201)]
    it = iter(responses)
    result = _request_with_retry(lambda: next(it), Config(max_retry_attempts=5), "test")
    assert result.status_code == 201


def test_retry_max_attempts():
    """Raises after exhausting all retry attempts."""
    resp = _mock_resp(202, {"Retry-After": "0"})
    with pytest.raises(TapestClientError, match="file unavailable after 2 attempts"):
        _request_with_retry(lambda: resp, Config(max_retry_attempts=2), "test")


def test_retry_non_202_error_returned():
    """Non-202 errors are returned to caller, not raised."""
    resp = _mock_resp(500)
    result = _request_with_retry(lambda: resp, Config(max_retry_attempts=3), "test")
    assert result.status_code == 500
