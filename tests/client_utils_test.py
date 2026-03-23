"""Tests for tapest_client utility functions and internal helpers."""

import os
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

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


class TestGenerateChecksum:

    def test_known_content(self, tmp_path):
        """Checksum of known content matches expected SHA-256."""
        path = tmp_path / "test.bin"
        path.write_bytes(b"hello world")
        result = generate_checksum(str(path))
        # sha256("hello world") = b94d27...e3b0c44
        assert result.startswith("sha256:")
        assert result == "sha256:b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_empty_file(self, tmp_path):
        """Checksum of empty file returns sha256 of empty input."""
        path = tmp_path / "empty"
        path.write_bytes(b"")
        result = generate_checksum(str(path))
        assert result == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_accepts_pathlib_path(self, tmp_path):
        """Accepts pathlib.Path as well as str."""
        path = tmp_path / "test.bin"
        path.write_bytes(b"test")
        result_str = generate_checksum(str(path))
        result_path = generate_checksum(path)
        assert result_str == result_path

    def test_missing_file_raises_oserror(self):
        """Raises OSError (FileNotFoundError) for missing file."""
        with pytest.raises(OSError):
            generate_checksum("/nonexistent/path/to/file")

    def test_large_file(self, tmp_path):
        """Handles files larger than the 64KB read buffer."""
        path = tmp_path / "large.bin"
        data = b"x" * (65536 * 3 + 42)  # ~192KB + 42 bytes
        path.write_bytes(data)
        result = generate_checksum(path)
        assert result.startswith("sha256:")

    def test_checksum_throughput(self, tmp_path):
        """Measure checksum throughput on a 10MB file."""
        path = tmp_path / "perf.bin"
        size_mb = 10
        path.write_bytes(os.urandom(size_mb * 1024 * 1024))
        start = time.perf_counter()
        generate_checksum(path)
        elapsed = time.perf_counter() - start
        print(f"generate_checksum: {size_mb}MB in {elapsed:.3f}s, "
              f"throughput {size_mb/elapsed:.1f} MB/s")


# === is_same_file ===


class TestIsSameFile:

    def test_matching_file(self, tmp_path):
        path = tmp_path / "match.bin"
        path.write_bytes(b"hello world")
        checksum = generate_checksum(path)
        size = path.stat().st_size
        assert is_same_file(str(path), size, checksum) is True

    def test_wrong_size(self, tmp_path):
        path = tmp_path / "file.bin"
        path.write_bytes(b"hello world")
        checksum = generate_checksum(path)
        assert is_same_file(str(path), 999, checksum) is False

    def test_wrong_checksum(self, tmp_path):
        path = tmp_path / "file.bin"
        path.write_bytes(b"hello world")
        size = path.stat().st_size
        assert is_same_file(str(path), size, "sha256:bad") is False

    def test_missing_file(self):
        assert is_same_file("/nonexistent", 0, "sha256:abc") is False

    def test_short_circuits_on_size(self, tmp_path):
        """Does not compute checksum if size doesn't match."""
        path = tmp_path / "file.bin"
        path.write_bytes(b"hello world")
        with mock.patch("tapest_client.client.generate_checksum") as mock_cs:
            result = is_same_file(str(path), 999, "sha256:irrelevant")
            assert result is False
            mock_cs.assert_not_called()


# === cleanup_file ===


class TestCleanupFile:

    def test_removes_file_when_enabled(self, tmp_path):
        path = tmp_path / "removeme.bin"
        path.write_bytes(b"data")
        cleanup_file({"CLEANUP_ON_FAIL": True}, str(path))
        assert not path.exists()

    def test_noop_when_disabled(self, tmp_path):
        path = tmp_path / "keepme.bin"
        path.write_bytes(b"data")
        cleanup_file({"CLEANUP_ON_FAIL": False}, str(path))
        assert path.exists()

    def test_noop_when_not_set(self, tmp_path):
        path = tmp_path / "keepme.bin"
        path.write_bytes(b"data")
        cleanup_file({}, str(path))
        assert path.exists()

    def test_missing_file_no_error(self):
        cleanup_file({"CLEANUP_ON_FAIL": True}, "/nonexistent/file")


# === _build_headers ===


class TestBuildHeaders:

    def test_minimal_config(self):
        config = {"ICE_TOKEN": "tok123"}
        headers = _build_headers(config)
        assert headers == {"Authorization": "Bearer tok123"}

    def test_with_storage_account(self):
        config = {"ICE_TOKEN": "tok123", "STORAGE_ACCOUNT_NAME": "myaccount"}
        headers = _build_headers(config)
        assert headers["X-ICE-Account"] == "myaccount"

    def test_with_storage_name(self):
        config = {"ICE_TOKEN": "tok123"}
        headers = _build_headers(config, storage_name="tape-01")
        assert headers["X-ICE-Storage"] == "tape-01"

    def test_with_extra_headers(self):
        config = {"ICE_TOKEN": "tok123"}
        headers = _build_headers(config, extra={"X-ICE-Size": "42"})
        assert headers["X-ICE-Size"] == "42"
        assert headers["Authorization"] == "Bearer tok123"

    def test_all_options(self):
        config = {"ICE_TOKEN": "tok", "STORAGE_ACCOUNT_NAME": "acc"}
        headers = _build_headers(
            config, storage_name="s1", extra={"X-Custom": "val"}
        )
        assert headers["Authorization"] == "Bearer tok"
        assert headers["X-ICE-Account"] == "acc"
        assert headers["X-ICE-Storage"] == "s1"
        assert headers["X-Custom"] == "val"


# === _file_url / _metadata_url ===


class TestUrlBuilders:

    def test_file_url(self):
        config = {"ICE_HOST": "https://ice.example.com"}
        url = _file_url(config, "/path/to/file.dat")
        assert url == "https://ice.example.com/file?identifier=%2Fpath%2Fto%2Ffile.dat"

    def test_file_url_special_chars(self):
        config = {"ICE_HOST": "https://ice.example.com"}
        url = _file_url(config, "file with spaces & symbols.dat")
        assert "file%20with%20spaces" in url

    def test_metadata_url_with_identifier(self):
        config = {"ICE_HOST": "https://ice.example.com"}
        url = _metadata_url(config, "/myfile.dat")
        assert url == "https://ice.example.com/metadata?identifier=%2Fmyfile.dat"

    def test_metadata_url_without_identifier(self):
        config = {"ICE_HOST": "https://ice.example.com"}
        url = _metadata_url(config)
        assert url == "https://ice.example.com/metadata"


# === _request_with_retry ===


class TestRequestWithRetry:

    def _mock_response(self, status_code, headers=None, text=""):
        resp = mock.Mock()
        resp.status_code = status_code
        resp.headers = headers or {}
        resp.text = text
        return resp

    def test_immediate_success(self):
        response = self._mock_response(200)
        result = _request_with_retry(
            lambda: response, {"MAX_RETRY_ATTEMPTS": 3}, "test"
        )
        assert result.status_code == 200

    def test_retry_on_202_then_success(self):
        responses = [
            self._mock_response(202, {"Retry-After": "0"}),
            self._mock_response(201),
        ]
        call_count = [0]

        def request_fn():
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        result = _request_with_retry(
            request_fn, {"MAX_RETRY_ATTEMPTS": 5}, "test"
        )
        assert result.status_code == 201
        assert call_count[0] == 2

    def test_max_attempts_exceeded(self):
        response_202 = self._mock_response(202, {"Retry-After": "0"})
        with pytest.raises(TapestClientError, match="max 2 attempts exceeded"):
            _request_with_retry(
                lambda: response_202, {"MAX_RETRY_ATTEMPTS": 2}, "test"
            )

    def test_non_success_returned_not_raised(self):
        """Non-202 errors are returned, not raised — caller decides."""
        response = self._mock_response(500, text="Internal Server Error")
        result = _request_with_retry(
            lambda: response, {"MAX_RETRY_ATTEMPTS": 3}, "test"
        )
        assert result.status_code == 500

    def test_min_one_attempt(self):
        """Even with MAX_RETRY_ATTEMPTS=0, at least one attempt is made."""
        response = self._mock_response(200)
        result = _request_with_retry(
            lambda: response, {"MAX_RETRY_ATTEMPTS": 0}, "test"
        )
        assert result.status_code == 200


# === TapestClientError ===


class TestTapestClientError:

    def test_is_exception(self):
        assert issubclass(TapestClientError, Exception)

    def test_message(self):
        err = TapestClientError("something failed")
        assert str(err) == "something failed"
