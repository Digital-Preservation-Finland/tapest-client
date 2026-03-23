"""Tests for tapest_client API functions."""

import os
from unittest import mock

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

CONFIG = {
    "ICE_TOKEN": "tok123",
    "ICE_HOST": "https://ice.example.com",
    "STORAGE_ACCOUNT_NAME": "testaccount",
    "MAX_RETRY_ATTEMPTS": 2,
    "DEFAULT_SLEEP_DURATION": 0,
    "VERIFY_SSL": False,
}

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


class TestIngestFile:

    def test_success(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"hello world")
        resp = _mock_response(201, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.put", return_value=resp):
            result = ingest_file(CONFIG, "/pkg/file.dat", str(path))
        assert result == SAMPLE_METADATA

    def test_failure_raises(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"hello world")
        resp = _mock_response(500, text="Server Error")
        with mock.patch("tapest_client.client.requests.put", return_value=resp):
            with pytest.raises(TapestClientError, match="500"):
                ingest_file(CONFIG, "/pkg/file.dat", str(path))

    def test_with_storage_name(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"data")
        resp = _mock_response(201, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.put", return_value=resp) as m:
            ingest_file(CONFIG, "/id", str(path), storage_name="tape-01")
        headers = m.call_args[1]["headers"]
        assert headers["X-ICE-Storage"] == "tape-01"


# === recache_file ===


class TestRecacheFile:

    def test_success(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"hello world")
        resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
        resp_put = _mock_response(201, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta), \
             mock.patch("tapest_client.client.requests.put", return_value=resp_put):
            result = recache_file(CONFIG, "/pkg/file.dat", str(path))
        assert result == SAMPLE_METADATA

    def test_size_mismatch(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"short")
        meta = {**SAMPLE_METADATA, "size": 99999}
        resp_meta = _mock_response(200, json_data=meta)
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
            with pytest.raises(TapestClientError, match="size"):
                recache_file(CONFIG, "/pkg/file.dat", str(path))

    def test_checksum_mismatch(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"hello world")
        meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
        resp_meta = _mock_response(200, json_data=meta)
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
            with pytest.raises(TapestClientError, match="checksum"):
                recache_file(CONFIG, "/pkg/file.dat", str(path))

    def test_put_failure(self, tmp_path):
        path = tmp_path / "file.dat"
        path.write_bytes(b"hello world")
        resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
        resp_put = _mock_response(500, text="Error")
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta), \
             mock.patch("tapest_client.client.requests.put", return_value=resp_put):
            with pytest.raises(TapestClientError, match="500"):
                recache_file(CONFIG, "/pkg/file.dat", str(path))


# === extract_file ===


class TestExtractFile:

    def test_delegates_to_extract_file_with_metadata(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
        resp_get = _mock_response(200)
        resp_get.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get",
                        side_effect=[resp_meta, resp_get]):
            result = extract_file(CONFIG, "/pkg/file.dat", str(dest))
        assert result == SAMPLE_METADATA
        assert dest.exists()


# === extract_file_with_metadata ===


class TestExtractFileWithMetadata:

    def test_success(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            result = extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
        assert result == SAMPLE_METADATA
        assert dest.read_bytes() == b"hello world"

    def test_file_already_exists(self, tmp_path):
        dest = tmp_path / "out.dat"
        dest.write_bytes(b"existing")
        with pytest.raises(TapestClientError, match="already exists"):
            extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))

    def test_size_mismatch_cleans_up(self, tmp_path):
        dest = tmp_path / "out.dat"
        meta = {**SAMPLE_METADATA, "size": 99999}
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        config = {**CONFIG, "CLEANUP_ON_FAIL": True}
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            with pytest.raises(TapestClientError, match="size"):
                extract_file_with_metadata(config, meta, str(dest))
        assert not dest.exists()

    def test_checksum_mismatch(self, tmp_path):
        dest = tmp_path / "out.dat"
        meta = {**SAMPLE_METADATA, "checksum": "sha256:bad"}
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            with pytest.raises(TapestClientError, match="checksum"):
                extract_file_with_metadata(CONFIG, meta, str(dest))

    def test_retry_on_202_then_success(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp_202 = _mock_response(202, headers={"Retry-After": "0"})
        resp_200 = _mock_response(200)
        resp_200.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get",
                        side_effect=[resp_202, resp_200]):
            result = extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
        assert result == SAMPLE_METADATA

    def test_max_attempts_exceeded(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp_202 = _mock_response(202, headers={"Retry-After": "0"})
        with mock.patch("tapest_client.client.requests.get", return_value=resp_202):
            with pytest.raises(TapestClientError, match="attempts exceeded"):
                extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))

    def test_http_error_cleans_up(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp = _mock_response(500, text="Error")
        config = {**CONFIG, "CLEANUP_ON_FAIL": True}
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            with pytest.raises(TapestClientError, match="500"):
                extract_file_with_metadata(config, SAMPLE_METADATA, str(dest))

    def test_next_identifier_header(self, tmp_path):
        dest = tmp_path / "out.dat"
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp) as m:
            extract_file_with_metadata(
                CONFIG, SAMPLE_METADATA, str(dest), next_identifier="/next")
        headers = m.call_args[1]["headers"]
        assert "X-ICE-Next-File" in headers

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "sub" / "dir" / "out.dat"
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            extract_file_with_metadata(CONFIG, SAMPLE_METADATA, str(dest))
        assert dest.exists()


# === delete_file ===


class TestDeleteFile:

    def test_success(self):
        resp = _mock_response(204)
        with mock.patch("tapest_client.client.requests.delete", return_value=resp):
            result = delete_file(CONFIG, "/pkg/file.dat")
        assert result is None

    def test_failure_raises(self):
        resp = _mock_response(404, text="Not Found")
        with mock.patch("tapest_client.client.requests.delete", return_value=resp):
            with pytest.raises(TapestClientError, match="404"):
                delete_file(CONFIG, "/pkg/file.dat")


# === retrieve_file_metadata ===


class TestRetrieveFileMetadata:

    def test_success(self):
        resp = _mock_response(200, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            result = retrieve_file_metadata(CONFIG, "/pkg/file.dat")
        assert result == SAMPLE_METADATA

    def test_failure_raises(self):
        resp = _mock_response(404, text="Not Found")
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            with pytest.raises(TapestClientError, match="404"):
                retrieve_file_metadata(CONFIG, "/pkg/file.dat")


# === update_file_metadata ===


class TestUpdateFileMetadata:

    def test_success(self):
        updated = {**SAMPLE_METADATA, "custom": "value"}
        resp = _mock_response(200, json_data=updated)
        with mock.patch("tapest_client.client.requests.patch", return_value=resp):
            result = update_file_metadata(CONFIG, "/pkg/file.dat", {"custom": "value"})
        assert result["custom"] == "value"

    def test_failure_raises(self):
        resp = _mock_response(400, text="Bad Request")
        with mock.patch("tapest_client.client.requests.patch", return_value=resp):
            with pytest.raises(TapestClientError, match="400"):
                update_file_metadata(CONFIG, "/pkg/file.dat", {})


# === retrieve_metadata ===


class TestRetrieveMetadata:

    def test_success(self):
        resp = _mock_response(200, json_data=[SAMPLE_METADATA])
        with mock.patch("tapest_client.client.requests.post", return_value=resp):
            result = retrieve_metadata(CONFIG, query={"status": "stored"})
        assert result == [SAMPLE_METADATA]

    def test_default_query(self):
        resp = _mock_response(200, json_data=[])
        with mock.patch("tapest_client.client.requests.post", return_value=resp) as m:
            retrieve_metadata(CONFIG)
        assert m.call_args[1]["json"] == {}

    def test_failure_raises(self):
        resp = _mock_response(500, text="Error")
        with mock.patch("tapest_client.client.requests.post", return_value=resp):
            with pytest.raises(TapestClientError, match="500"):
                retrieve_metadata(CONFIG)


# === retrieve_status ===


class TestRetrieveStatus:

    def test_success(self):
        status = {"status": "ok"}
        resp = _mock_response(200, json_data=status)
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            result = retrieve_status(CONFIG)
        assert result == {"status": "ok"}

    def test_failure_raises(self):
        resp = _mock_response(503, text="Unavailable")
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            with pytest.raises(TapestClientError, match="503"):
                retrieve_status(CONFIG)


# === ingest_files_from_directory ===


class TestIngestFilesFromDirectory:

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(TapestClientError, match="does not exist"):
            ingest_files_from_directory(CONFIG, str(tmp_path / "nope"))

    def test_ingest_new_files(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "a.dat").write_bytes(b"aaa")
        (root / "b.dat").write_bytes(b"bbb")

        resp_404 = _mock_response(404, text="Not Found")
        resp_201 = _mock_response(201, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.get",
                        side_effect=TapestClientError("404 Not Found")), \
             mock.patch("tapest_client.client.requests.put",
                        return_value=resp_201):
            result = ingest_files_from_directory(CONFIG, str(root))
        assert len(result) == 2

    def test_skip_existing_same(self, tmp_path):
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

    def test_force_replaces_different(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        path = root / "a.dat"
        path.write_bytes(b"new content")
        meta = {**SAMPLE_METADATA, "size": 5, "checksum": "sha256:old"}
        resp_meta = _mock_response(200, json_data=meta)
        resp_del = _mock_response(204)
        resp_put = _mock_response(201, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta), \
             mock.patch("tapest_client.client.requests.delete", return_value=resp_del), \
             mock.patch("tapest_client.client.requests.put", return_value=resp_put):
            result = ingest_files_from_directory(CONFIG, str(root), force=True)
        assert len(result) == 1

    def test_existing_no_skip_no_force_raises(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "a.dat").write_bytes(b"data")
        resp_meta = _mock_response(200, json_data=SAMPLE_METADATA)
        with mock.patch("tapest_client.client.requests.get", return_value=resp_meta):
            with pytest.raises(TapestClientError, match="already exists"):
                ingest_files_from_directory(CONFIG, str(root))

    def test_skips_subdirectories(self, tmp_path):
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


class TestExtractFilesToDirectory:

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(TapestClientError, match="does not exist"):
            extract_files_to_directory(CONFIG, [], str(tmp_path / "nope"))

    def test_extract_new_files(self, tmp_path):
        meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp):
            result = extract_files_to_directory(CONFIG, [meta], str(tmp_path))
        assert len(result) == 1
        assert (tmp_path / "pkg" / "file.dat").exists()

    def test_skip_existing_same(self, tmp_path):
        path = tmp_path / "pkg" / "file.dat"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"hello world")
        checksum = generate_checksum(path)
        meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
                "size": 11, "checksum": checksum}
        result = extract_files_to_directory(CONFIG, [meta], str(tmp_path), skip=True)
        assert len(result) == 0

    def test_force_replaces_different(self, tmp_path):
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

    def test_existing_no_skip_no_force_raises(self, tmp_path):
        path = tmp_path / "pkg" / "file.dat"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"data")
        meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat"}
        with pytest.raises(TapestClientError, match="already exists"):
            extract_files_to_directory(CONFIG, [meta], str(tmp_path))

    def test_next_identifier_passed(self, tmp_path):
        meta1 = {**SAMPLE_METADATA, "identifier": "/pkg/a.dat"}
        meta2 = {**SAMPLE_METADATA, "identifier": "/pkg/b.dat"}
        resp = _mock_response(200)
        resp.iter_content.return_value = [b"hello world"]
        with mock.patch("tapest_client.client.requests.get", return_value=resp) as m:
            extract_files_to_directory(CONFIG, [meta1, meta2], str(tmp_path))
        first_call_headers = m.call_args_list[0][1]["headers"]
        assert "X-ICE-Next-File" in first_call_headers

    def test_existing_same_force_skips(self, tmp_path):
        """force=True with matching file should raise (not skip, not replace)."""
        path = tmp_path / "pkg" / "file.dat"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"hello world")
        checksum = generate_checksum(path)
        meta = {**SAMPLE_METADATA, "identifier": "/pkg/file.dat",
                "size": 11, "checksum": checksum}
        with pytest.raises(TapestClientError, match="already exists"):
            extract_files_to_directory(
                CONFIG, [meta], str(tmp_path), force=True)
