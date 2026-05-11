"""Tests for tapest_client utility functions and internal helpers.

Covers checksum, file comparison, cleanup, header building, URL
construction, retry logic, and the TapestClientError exception.
"""

import pytest
import urllib.parse

from tapest_client.client import (
    TapestClientError,
    _build_headers,
    _file_url,
    _metadata_url,
    _normalize_identifier,
    _request_with_retry,
    generate_checksum,
    is_same_file,
    cleanup_file,
    parse_chunk_size,
)

from tests.conftest import mock_response


# === _normalize_identifier ===


def test_normalize_identifier_adds_slash():
    """Prepends a leading '/' when the identifier has none."""
    assert _normalize_identifier("foo/bar") == "/foo/bar"


def test_normalize_identifier_keeps_single_slash():
    """Leaves a correctly-formed identifier unchanged."""
    assert _normalize_identifier("/foo/bar") == "/foo/bar"


def test_normalize_identifier_removes_extra_slashes():
    """Collapses multiple leading slashes to exactly one."""
    assert _normalize_identifier("///foo/bar") == "/foo/bar"


def test_normalize_identifier_empty_string():
    """Empty string normalizes to the root identifier '/'."""
    assert _normalize_identifier("") == "/"


def test_normalize_identifier_list():
    """Normalizes every element in a list of identifiers."""
    result = _normalize_identifier(["foo", "/bar", "///baz"])
    assert result == ["/foo", "/bar", "/baz"]


def test_normalize_identifier_list_returns_list():
    """Returns a list when the input is a list."""
    result = _normalize_identifier(["/single"])
    assert isinstance(result, list)


def test_normalize_identifier_str_returns_str():
    """Returns a str when the input is a str."""
    result = _normalize_identifier("/single")
    assert isinstance(result, str)


# === generate_checksum ===


def test_checksum_known_content(tmp_path):
    """SHA-256 of known content matches expected digest."""
    path = tmp_path / "test.bin"
    path.write_bytes(b"hello world")
    expected = (
        "sha256:b94d27b9934d3e08a52e52d7da7dab" "fac484efe37a5380ee9088f7ace2efcde9"
    )
    assert generate_checksum(str(path)) == expected


def test_checksum_empty_file(tmp_path):
    """Empty file produces the sha256 empty-input digest."""
    path = tmp_path / "empty"
    path.write_bytes(b"")
    expected = (
        "sha256:e3b0c44298fc1c149afbf4c8996fb924" "27ae41e4649b934ca495991b7852b855"
    )
    assert generate_checksum(str(path)) == expected


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
    checksum = generate_checksum(path)
    assert is_same_file(str(path), path.stat().st_size, checksum) is True


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


def test_same_file_short_circuits_on_size(tmp_path, monkeypatch):
    """Skips checksum computation when size already mismatches."""
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    called = []
    monkeypatch.setattr(
        "tapest_client.client.generate_checksum", lambda *a: called.append(1)
    )
    assert is_same_file(str(path), 999, "sha256:irrelevant") is False
    assert called == []


# === cleanup_file ===


def test_cleanup_removes_when_enabled(tmp_path, config_fx):
    """Deletes file when cleanup_on_fail is True."""
    path = tmp_path / "removeme.bin"
    path.write_bytes(b"data")
    cleanup_file(config_fx(cleanup_on_fail=True), str(path))
    assert not path.exists()


def test_cleanup_noop_when_disabled(tmp_path, config_fx):
    """Does nothing when cleanup_on_fail is False (the default)."""
    path = tmp_path / "keepme.bin"
    path.write_bytes(b"data")
    cleanup_file(config_fx(), str(path))
    assert path.exists()


def test_cleanup_missing_file_no_error(config_fx):
    """Does not raise if file is already gone."""
    cleanup_file(config_fx(cleanup_on_fail=True), "/nonexistent/file")


# === _build_headers ===


def test_headers_minimal(config_fx):
    """Only Authorization header with just token."""
    cfg = config_fx(storage_account_name="")
    expected = {"Authorization": f"Bearer {cfg.token}"}
    assert _build_headers(cfg) == expected


def test_headers_all_options(config_fx):
    """Account, storage name, and extra headers are all included."""
    cfg = config_fx()
    headers = _build_headers(cfg, storage_name="s1", extra={"X-Custom": "val"})
    assert headers["Authorization"] == f"Bearer {cfg.token}"
    assert headers["X-ICE-Account"] == cfg.storage_account_name
    assert headers["X-ICE-Storage"] == "s1"
    assert headers["X-Custom"] == "val"


def test_headers_account_name_overrides_config(config_fx):
    """Per-call account_name takes precedence over config.storage_account_name."""
    cfg = config_fx(storage_account_name="ida")
    headers = _build_headers(cfg, account_name="kuvi")
    assert headers["X-ICE-Account"] == "kuvi"


def test_headers_account_name_when_config_unset(config_fx):
    """Per-call account_name works even when config.storage_account_name is empty.

    Covers the STORAGE_AGENT / cross-tenant case where the worker has
    no fixed account in config and supplies the account per request.
    """
    cfg = config_fx(storage_account_name="")
    headers = _build_headers(cfg, account_name="ida")
    assert headers["X-ICE-Account"] == "ida"


def test_headers_no_account_when_both_unset(config_fx):
    """No X-ICE-Account header when neither config nor argument set it."""
    cfg = config_fx(storage_account_name="")
    headers = _build_headers(cfg)
    assert "X-ICE-Account" not in headers


# === _file_url / _metadata_url ===


def test_file_url(config_fx):
    """Builds /file endpoint URL with encoded identifier."""
    cfg = config_fx()
    expected = f"{cfg.host}/file?identifier=%2Fpath%2Fto%2Ffile.dat"
    assert _file_url(cfg, "/path/to/file.dat") == expected


def test_metadata_url_with_identifier(config_fx):
    """Builds /metadata endpoint URL with encoded identifier."""
    cfg = config_fx()
    expected = f"{cfg.host}/metadata?identifier=%2Fmyfile.dat"
    assert _metadata_url(cfg, "/myfile.dat") == expected


def test_metadata_url_without_identifier(config_fx):
    """Builds bare /metadata URL when no identifier given."""
    cfg = config_fx()
    assert _metadata_url(cfg) == f"{cfg.host}/metadata"


# === URL encoding for special characters ===


@pytest.mark.parametrize(
    "special_chars",
    [
        "/with!@#$%^&*()chars",
        "/path/with space<tab>\t<newline>\nchars.dat",
        "/path/with\"single'double\\backslash[brackets]{braces}.dat",
        "/ÄäÖöÅåÍíÜüÆæ.dat",
        "/path/файл_Ελληνικά_عربي_中文_日本語_file.dat",
        "/path/with spaces in filename.dat",
        "/path/with\nnewline\rcarriage.dat",
        "/path/with\u00a0nbsp\u2003em_space.dat",
        "/path/with   multiple   spaces.dat",
        "/path/with%percent%20signs.dat",
        "/path/file&with&ampersands.dat",
        "/path/key=value=file.dat",
        "/path/pipe|tilde~chars.dat",
        "/path/math_∑_∫_±_≠.dat",
    ],
    ids=[
        "Special characters",
        "Tab and newlines",
        "Extended special characters",
        "Nordic etc. alphabets",
        "Greek, Cyrillic, Japanese, Chinese, Arabic alphabets",
        "Spaces",
        "Newlines",
        "Unicode spaces",
        "Multiple continuous spaces",
        "Percent sign",
        "Ampersand",
        "Equal sign",
        "Pipe and tilde",
        "Mathematical characters",
    ],
)
def test_file_special_characters(special_chars, config_fx):
    """Special characters are correctly URL-encoded in /file endpoint."""
    cfg = config_fx()
    identifier = special_chars
    url = _file_url(cfg, identifier)
    # Verify URL is properly formed
    assert url.startswith(f"{cfg.host}/file?identifier=")
    # Extract encoded identifier from URL
    encoded = url.split("identifier=")[1]
    # Verify it can be decoded back to original
    assert urllib.parse.unquote(encoded) == identifier


# === _request_with_retry ===


def test_retry_immediate_success(config_fx):
    """Non-202 response is returned immediately."""
    resp = mock_response(200)
    result = _request_with_retry(lambda: resp, config_fx(max_retry_attempts=3), "test")
    assert result.status_code == 200


def test_retry_202_then_success(config_fx):
    """Retries on 202 and returns the first non-202 response."""
    responses = [
        mock_response(202, headers={"Retry-After": "0"}),
        mock_response(201),
    ]
    it = iter(responses)
    result = _request_with_retry(
        lambda: next(it), config_fx(max_retry_attempts=5), "test"
    )
    assert result.status_code == 201


def test_retry_max_attempts(config_fx):
    """Raises after exhausting all retry attempts."""
    resp = mock_response(202, headers={"Retry-After": "0"})
    with pytest.raises(TapestClientError, match="file unavailable after 2 attempts"):
        _request_with_retry(lambda: resp, config_fx(max_retry_attempts=2), "test")


def test_retry_non_202_error_returned(config_fx):
    """Non-202 errors are returned to caller, not raised."""
    resp = mock_response(500)
    result = _request_with_retry(lambda: resp, config_fx(max_retry_attempts=3), "test")
    assert result.status_code == 500


@pytest.mark.parametrize(
    ("chunk_size", "expected_bytes"),
    [
        ("1 B", 1),
        ("1 KiB", 1024),
        ("16 MiB", 16 * 1024**2),  # the default
        ("128 MiB", 128 * 1024**2),
        ("1 GiB", 1024**3),
        ("25.6 MiB", int(25.6 * 1024**2)),
    ],
)
def test_parse_chunk_size_valid(chunk_size, expected_bytes):
    """Happy-path: '<number> <unit>' parses to the right byte count."""
    assert parse_chunk_size(chunk_size) == expected_bytes


@pytest.mark.parametrize(
    ("chunk_size", "expected_message"),
    [
        ("16MiB", "Could not parse"),
        ("16 XiB", "Could not parse"),
        ("abc MiB", "Could not parse"),
        ("", "Could not parse"),
        ("0 MiB", "must be positive"),
        ("-5 MiB", "must be positive"),
    ],
)
def test_parse_chunk_size_parse_error(chunk_size, expected_message):
    """Bad inputs should match with expected error."""
    with pytest.raises(TapestClientError, match=expected_message):
        parse_chunk_size(chunk_size)
