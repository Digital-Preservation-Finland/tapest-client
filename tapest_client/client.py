# ----------------------------------------------------------------------
# This file is part of TapeSt – Tape Storage
# The CSC Digital Preservation Tape Storage Service
#
# Copyright (C) 2025 CSC - IT Center for Science Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public
# License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# @author CSC - IT Center for Science Ltd., Espoo Finland <servicedesk@csc.fi>
# @license GNU Affero General Public License, version 3
# @link https://www.csc.fi/
# ----------------------------------------------------------------------
# TapeSt API client library. Accepts a Config instance or any object
# supporting attribute access for:
#
#     config.token                # required
#     config.host                 # required
#     config.storage_account_name     # optional
#     config.max_retry_attempts       # optional (default: 10)
#     config.default_sleep_duration   # optional (default: 120)
#     config.cleanup_on_fail          # optional (default: False)
#     config.verify_ssl               # optional (default: True)
#     config.ca_cert_path             # optional (default: "")
#
# Dict-style access (config["key"]) is also supported via Config.
#
# File identifiers are normalized by the client to canonical leading-
# slash form (`/foo/bar`) before being sent, and by the API to UTF-8
# NFC on receive. The identifier returned in API responses is the
# canonical form, so a value passed in as `foo/bar` or in NFD will
# appear as `/foo/bar` in NFC in the returned metadata.
# ----------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from collections.abc import Callable

from platformdirs import user_state_path
import requests
from tusclient import client as tus_client
from tusclient.exceptions import (
    TusCommunicationError,
    TusUploadFailed,
)
from tusclient.storage import filestorage

from tapest_client.config import Config

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CHUNK_SIZE_UNITS = {
    "B": 0,
    "KiB": 1,
    "MiB": 2,
    "GiB": 3,
    "TiB": 4,
    "PiB": 5,
    "EiB": 6,
    "ZiB": 7,
}


class TapestClientError(Exception):
    """Error raised by tapest-client operations."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class FileAlreadyExistsError(TapestClientError, FileExistsError):
    """Raised when a file already has the identifier or the local pathname.

    Also a builtin :class:`FileExistsError`, so a caller can catch either.
    What happens next is the caller's decision.
    """


# === Internal Helpers ===


def _build_headers(
    config: Config,
    storage_name: str | None = None,
    extra: dict[str, str] | None = None,
    account_name: str | None = None,
) -> dict[str, str]:
    """Build common request headers from config.

    ``account_name`` overrides ``X-TAPEST-Account`` per request; falls
    back to ``config.storage_account_name`` when unset.
    """
    headers = {"Authorization": f"Bearer {config.token}"}
    effective_account = account_name or config.storage_account_name
    if effective_account:
        headers["X-TAPEST-Account"] = effective_account
    if storage_name:
        headers["X-TAPEST-Storage"] = storage_name
    if extra:
        headers.update(extra)
    return headers


def _file_url(config: Config, identifier: str) -> str:
    """Build /file endpoint URL."""
    encoded = urllib.parse.quote(normalize_identifier(identifier), safe="")
    return f"{config.host}/file?identifier={encoded}"


def _metadata_url(config: Config, identifier: str | None = None) -> str:
    """Build /metadata endpoint URL."""
    if identifier:
        encoded = urllib.parse.quote(
            normalize_identifier(identifier), safe=""
        )
        return f"{config.host}/metadata?identifier={encoded}"
    return f"{config.host}/metadata"


def _verify_param(config: Config) -> str | bool:
    """Return the ``verify`` value for requests calls."""
    if config.verify_ssl and config.ca_cert_path:
        return config.ca_cert_path
    return config.verify_ssl


def _request_with_retry(
    request_fn: Callable[[], requests.Response], config: Config, error_msg: str
) -> requests.Response:
    """Execute request_fn in a retry loop, handling 202 Retry-After responses.

    Returns the first non-202 response. Raises TapestClientError if max
    retry attempts are exceeded.
    """
    max_attempts = max(1, config.max_retry_attempts)
    default_duration = config.default_sleep_duration
    for _ in range(max_attempts):
        response = request_fn()
        if response.status_code != 202:
            return response
        seconds = int(response.headers.get("Retry-After", default_duration))
        time.sleep(seconds)
    raise TapestClientError(
        f"{error_msg}: file unavailable after {max_attempts} attempts",
        exit_code=117,
    )


def normalize_identifier(identifier: str | list) -> str | list:
    """Canonicalize identifier path(s) to a single leading slash.

    Collapses empty (``//``) and ``.`` segments and resolves ``..``, so
    ``/./a``, ``a//b`` and ``x/../y`` become ``/a``, ``/a/b`` and ``/y``.
    A trailing slash is preserved, since prefix queries distinguish
    ``/foo/`` (children of foo) from ``/foo``.
    """
    if isinstance(identifier, list):
        return [normalize_identifier(i) for i in identifier]
    segments: list[str] = []
    for segment in identifier.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    normalized = "/" + "/".join(segments)
    if identifier.endswith("/") and normalized != "/":
        normalized += "/"
    return normalized


# === Utility Functions ===


def generate_checksum(local_file_pathname: str | Path) -> str:
    """Compute SHA-256 checksum for a file, returned as 'sha256:<hex>'."""
    sha256_hash = hashlib.sha256()
    with open(local_file_pathname, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            sha256_hash.update(block)
    return f"sha256:{sha256_hash.hexdigest()}"


def is_same_file(
    local_file_pathname: str | Path, size: int, checksum: str
) -> bool:
    """Check if file matches expected size and checksum."""
    try:
        if Path(local_file_pathname).stat().st_size != size:
            return False
        return generate_checksum(local_file_pathname) == checksum
    except OSError:
        return False


def cleanup_file(config: Config, local_file_pathname: str) -> None:
    """Remove the file if cleanup_on_fail is enabled. Ignores missing files."""
    if config.cleanup_on_fail:
        try:
            Path(local_file_pathname).unlink()
        except OSError:
            pass


def parse_chunk_size(chunk_size: str) -> int:
    """Parses human-readable chunk size into bytes representation of integers.

    :param chunk_size: Chunk size to parse, such as 16 MiB
    :return: Chunk size in bytes, in int representation
    :raises TapestClientError: If the chunk_size provided is problematic.
    """
    try:
        size, chunk_unit = chunk_size.split()
        chunk_size_in_bytes = float(size) * (
            1024 ** _CHUNK_SIZE_UNITS[chunk_unit]
        )
    except (ValueError, KeyError) as exc:
        raise TapestClientError(
            f"Could not parse chunk size from [{chunk_size}]."
        ) from exc
    if chunk_size_in_bytes <= 0:
        raise TapestClientError(
            f"Chunk size must be positive, got [{chunk_size}]."
        )
    return int(chunk_size_in_bytes)


def _tus_storage_path(identifier: str) -> Path:
    """Return the resume-state file for one upload.

    One file per identifier. A shared file fails two ways: tuspy keys
    records on file content alone, so identical content collides, and
    the store rewrites the whole file per write, so parallel uploads
    corrupt it.
    """
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
    return user_state_path("tapest-client") / "tus_storage.d" / digest


def _conflict_error_document(error: TusCommunicationError) -> dict | None:
    """Return the JSON error document of a 409, or None if it carried none."""
    if not isinstance(error.response_content, (bytes, str)):
        return None
    try:
        document = json.loads(error.response_content)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _raise_for_upload_error(
    error: TusCommunicationError, identifier: str
) -> None:
    """Re-raise a failed upload as the condition its 409 reports.

    Returns only for an identical duplicate, which is a success.
    """
    document = _conflict_error_document(error)
    # Only flask-tus-io answers 409 without a document, and only over a chunk.
    refused_offset = document is None and isinstance(error, TusUploadFailed)
    duplicate = (document or {}).get("error") == "identical_duplicate"
    if error.status_code == 409 and refused_offset:
        raise TapestClientError(
            f"The server refused a chunk of {identifier} with 409. The "
            f"chunk was probably sent at the wrong offset, so the upload "
            f"can be run again."
        ) from error
    if error.status_code == 409 and duplicate:
        return
    if error.status_code == 409:
        raise FileAlreadyExistsError(
            f"A different file already exists for identifier {identifier}"
        ) from error
    raise error


# === Core Operation Functions ===


def _tus_upload(
    config: Config,
    identifier: str,
    path: str,
    size: int,
    metadata: dict,
    account_name: str | None,
    chunk_size: str | None,
) -> dict:
    """Upload ``path`` to the TUS endpoint and return the file's metadata.

    Shared by TUS ingest and recache; the caller builds the ``Upload-Metadata``
    dict (``filename`` plus ``X-TAPEST-*`` keys).

    :param config: Config instance.
    :param identifier: File identifier, for the closing metadata fetch.
    :param path: Local file URI.
    :param size: File size in bytes.
    :param metadata: TUS Upload-Metadata dict.
    :param account_name: Per-call override for ``X-TAPEST-Account``.
    :param chunk_size: TUS chunk size. Falls back to ``config.tus_chunk_size``
        if unset.
    """
    chunk_size_in_bytes = parse_chunk_size(
        chunk_size=chunk_size or config.tus_chunk_size
    )
    tus_client_obj = tus_client.TusClient(url=f"{config.host}/tus")
    headers = _build_headers(
        config=config,
        extra={
            "X-TAPEST-Size": str(size),
        },
        account_name=account_name,
    )
    for key, value in headers.items():
        tus_client_obj.headers[key] = value
    storage_path = _tus_storage_path(metadata["filename"])
    # Platformdirs 2.5.4 version does not support "ensure_exists"
    # so this is a workaround for it.
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage = filestorage.FileStorage(str(storage_path))
    uploader = tus_client_obj.uploader(
        file_path=path,
        metadata=metadata,
        chunk_size=chunk_size_in_bytes,
        store_url=True,
        url_storage=storage,
        verify_tls_cert=_verify_param(config),
    )
    try:
        uploader.upload()
    except TusCommunicationError as error:
        _raise_for_upload_error(error=error, identifier=identifier)
    storage_path.unlink(missing_ok=True)
    return retrieve_file_metadata(
        config=config,
        identifier=identifier,
        account_name=account_name,
    )


def _tus_upload_file(
    config: Config,
    identifier: str,
    path: str,
    checksum: str | None = None,
    account_name: str | None = None,
    chunk_size: str | None = None,
):
    """Ingest a local file via TUS endpoint.

    :param config: Config instance.
    :param identifier: File identifier.
    :param path: local file URI.
    :param checksum: Pre-given checksum so that checksum calculation
        can be given during this function's call.
    :raises FileAlreadyExistsError: If a different file is already stored
        under the identifier. Replacing it is the caller's decision.
    :param account_name: Per-call override for ``X-TAPEST-Account``.
        Falls back to ``config.storage_account_name`` if unset.
    :param chunk_size: Size of chunks to send as part of TUS upload.
        Falls back to ``config.tus_chunk_size`` if unset.
    """
    stat = Path(path).stat()
    if checksum is None:
        checksum = generate_checksum(local_file_pathname=path)
    created = datetime.fromtimestamp(stat.st_ctime, timezone.utc).strftime(
        TIMESTAMP_FORMAT
    )
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime(
        TIMESTAMP_FORMAT
    )
    # File-identity metadata travels in the TUS Upload-Metadata, so it is
    # captured once on POST initialization and persisted by the server.
    metadata = {
        "filename": normalize_identifier(identifier=identifier),
        "X-TAPEST-Checksum": checksum,
        "X-TAPEST-Created": created,
        "X-TAPEST-Modified": modified,
    }
    return _tus_upload(
        config=config,
        identifier=identifier,
        path=path,
        size=stat.st_size,
        metadata=metadata,
        account_name=account_name,
        chunk_size=chunk_size,
    )


# Fallback limit for a service that does not report one; it matches the
# service default. Guessing low is safe: the file just takes the TUS path,
# which works at any size. Probing with a PUT is not, since requests sends
# the whole body before the 413 comes back.
_ASSUMED_MAX_PUT_BYTES = 1 << 30  # 1 GiB

_UPLOAD_LIMIT_CACHE: dict[str, int] = {}


def _max_put_bytes(config: Config) -> int:
    """Return the largest body that PUT /file accepts.

    Read from the service status once per host, falling back to
    ``_ASSUMED_MAX_PUT_BYTES``.
    """
    if config.host not in _UPLOAD_LIMIT_CACHE:
        try:
            limit = retrieve_status(config).get("max_non_tus_upload_bytes")
        except TapestClientError:
            limit = None
        _UPLOAD_LIMIT_CACHE[config.host] = limit or _ASSUMED_MAX_PUT_BYTES
    return _UPLOAD_LIMIT_CACHE[config.host]


def ingest_file(
    config: Config,
    identifier: str,
    local_file_pathname: str,
    checksum: str | None = None,
    account_name: str | None = None,
) -> dict:
    """Ingest a local file and return its metadata.

    A single stat() call is used to collect size, creation time and
    modification time, avoiding redundant filesystem round-trips.

    The ``identifier`` is normalized to canonical leading-slash form
    by the client, and to UTF-8 NFC by the API on receive. The
    ``identifier`` field in the returned metadata is the canonical
    form, which may differ from the value passed in.

    :param config: Config instance.
    :param identifier: File identifier.
    :param local_file_pathname: Local file URI.
    :param checksum: Pre-given checksum so that checksum calculation
        can be given during this function's call.
    :param account_name: Per-call override for ``X-TAPEST-Account``.
        Falls back to ``config.storage_account_name`` if unset.
    """
    path = Path(local_file_pathname)
    stat = path.stat()
    if checksum is None:
        checksum = generate_checksum(local_file_pathname=path)

    # Files over the PUT limit go to the TUS endpoint instead.
    if stat.st_size > _max_put_bytes(config):
        return _tus_upload_file(
            config=config,
            identifier=identifier,
            path=local_file_pathname,
            checksum=checksum,
            account_name=account_name,
        )
    created = datetime.fromtimestamp(stat.st_ctime, timezone.utc).strftime(
        TIMESTAMP_FORMAT
    )
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime(
        TIMESTAMP_FORMAT
    )

    url = _file_url(config, identifier)
    headers = _build_headers(
        config,
        extra={
            "X-TAPEST-Size": str(stat.st_size),
            "X-TAPEST-Checksum": checksum,
            "X-TAPEST-Created": created,
            "X-TAPEST-Modified": modified,
        },
        account_name=account_name,
    )
    verify_ssl = _verify_param(config)

    def do_request():
        with open(path, "rb") as f:
            return requests.put(
                url, headers=headers, data=f, stream=True, verify=verify_ssl
            )

    response = _request_with_retry(
        do_request, config, f"Failed to ingest file {identifier}"
    )
    if response.status_code == 201:
        return response.json()
    # A 409 with the identical-duplicate marker is an idempotent success.
    if response.status_code == 409:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if body.get("error") == "identical_duplicate":
            # Idempotent success: return the existing file's metadata.
            return body.get("metadata") or body
        raise FileAlreadyExistsError(
            f"File already exists with identifier {identifier}"
        )
    # The service refused the body as too large; retry over TUS.
    if response.status_code == 413:
        return _tus_upload_file(
            config=config,
            identifier=identifier,
            path=local_file_pathname,
            checksum=checksum,
            account_name=account_name,
        )
    raise TapestClientError(
        f"Failed to ingest file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def recache_file(
    config: Config,
    identifier: str,
    local_file_pathname: str,
    account_name: str | None = None,
    use_tus: bool = True,
    chunk_size: str | None = None,
) -> dict:
    """Re-upload a cached file, verifying it matches.

    Uploads over TUS by default; ``use_tus=False`` sends a single PUT instead,
    which can be faster for small files.

    See :func:`ingest_file` for the semantics of ``account_name``.

    :param use_tus: Upload over TUS (default) or a single PUT when False.
    :param chunk_size: TUS chunk size; falls back to
        ``config.tus_chunk_size`` if unset. Ignored when ``use_tus`` is
        False.
    """
    path = Path(local_file_pathname)
    file_metadata = retrieve_file_metadata(
        config, identifier, account_name=account_name
    )

    stat = path.stat()
    if stat.st_size != file_metadata["size"]:
        raise TapestClientError(
            f"Local file size ({stat.st_size}) does not match "
            f"ingested size ({file_metadata['size']})"
        )

    checksum = generate_checksum(path)
    if checksum != file_metadata["checksum"]:
        raise TapestClientError(
            "Local file checksum does not match ingested checksum"
        )

    if use_tus:
        # Identity fields come from the server record to preserve timestamps.
        metadata = {
            "filename": normalize_identifier(identifier=identifier),
            "X-TAPEST-Checksum": file_metadata["checksum"],
            "X-TAPEST-Created": file_metadata["created"],
            "X-TAPEST-Modified": file_metadata["modified"],
            "X-TAPEST-Recache": "true",
        }
        return _tus_upload(
            config=config,
            identifier=identifier,
            path=str(path),
            size=file_metadata["size"],
            metadata=metadata,
            account_name=account_name,
            chunk_size=chunk_size,
        )

    url = _file_url(config, identifier)
    headers = _build_headers(
        config,
        extra={
            "X-TAPEST-Size": str(file_metadata["size"]),
            "X-TAPEST-Checksum": file_metadata["checksum"],
            "X-TAPEST-Created": file_metadata["created"],
            "X-TAPEST-Modified": file_metadata["modified"],
            "X-TAPEST-Recache": "true",
        },
        account_name=account_name,
    )
    verify_ssl = _verify_param(config)

    def do_request():
        with open(path, "rb") as f:
            return requests.put(
                url, headers=headers, data=f, stream=True, verify=verify_ssl
            )

    response = _request_with_retry(
        do_request, config, f"Failed to recache file {identifier}"
    )
    if response.status_code == 201:
        return response.json()
    raise TapestClientError(
        f"Failed to recache file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def extract_file(
    config: Config,
    identifier: str,
    local_file_pathname: str,
    account_name: str | None = None,
) -> dict:
    """Extract a file by identifier to a local path.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    file_metadata = retrieve_file_metadata(
        config, identifier, account_name=account_name
    )
    return extract_file_with_metadata(
        config,
        file_metadata,
        local_file_pathname,
        account_name=account_name,
    )


def _write_extracted_file(
    config: Config,
    response: requests.Response,
    file_metadata: dict,
    local_file_pathname: str,
) -> None:
    """Write the response body to disk and verify size and checksum.

    Clean up and raise if the file does not match its metadata.
    """
    identifier = file_metadata["identifier"]
    path = Path(local_file_pathname)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    actual_size = path.stat().st_size
    if actual_size != file_metadata["size"]:
        cleanup_file(config, local_file_pathname)
        raise TapestClientError(
            f"Failed to extract file {identifier}: "
            f"size {actual_size} != expected {file_metadata['size']}"
        )

    actual_checksum = generate_checksum(path)
    if actual_checksum != file_metadata["checksum"]:
        cleanup_file(config, local_file_pathname)
        raise TapestClientError(
            f"Failed to extract file {identifier}: checksum mismatch"
        )

    created_ts = (
        datetime.strptime(file_metadata["created"], TIMESTAMP_FORMAT)
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )
    modified_ts = (
        datetime.strptime(file_metadata["modified"], TIMESTAMP_FORMAT)
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )
    os.utime(path, (created_ts, modified_ts))


def attempt_extract(
    config: Config,
    file_metadata: dict,
    local_file_pathname: str,
    account_name: str | None = None,
) -> tuple[bool, int]:
    """Make one extract request.

    Return ``(True, 0)`` and write the file when the service gives the bytes
    (200). Return ``(False, retry_after)`` when the file is still on tape
    (202); ``retry_after`` is the server's Retry-After in seconds, or 0 when
    it gives none. Clean up and raise for any other status.

    A 202 makes the service mark the file required. A caller can request every
    file once to mark a whole set, then come back for the bytes.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    identifier = file_metadata["identifier"]
    url = _file_url(config, identifier)
    headers = _build_headers(config, account_name=account_name)
    verify_ssl = _verify_param(config)

    response = requests.get(
        url, headers=headers, stream=True, verify=verify_ssl
    )
    if response.status_code == 200:
        _write_extracted_file(
            config, response, file_metadata, local_file_pathname
        )
        return True, 0
    if response.status_code == 202:
        return False, int(response.headers.get("Retry-After", 0))

    cleanup_file(config, local_file_pathname)
    raise TapestClientError(
        f"Failed to extract file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def extract_file_with_metadata(
    config: Config,
    file_metadata: dict,
    local_file_pathname: str,
    account_name: str | None = None,
) -> dict:
    """Extract a file using provided metadata to a local path.

    Retries a file that is still on tape (202) up to
    ``config.max_retry_attempts`` times and sleeps between attempts.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    path = Path(local_file_pathname)
    if path.exists():
        raise FileAlreadyExistsError(
            f"File already exists at {local_file_pathname}"
        )

    identifier = file_metadata["identifier"]
    max_attempts = max(1, config.max_retry_attempts)
    default_duration = max(1, config.default_sleep_duration)

    for _ in range(max_attempts):
        ready, retry_after = attempt_extract(
            config, file_metadata, local_file_pathname,
            account_name=account_name,
        )
        if ready:
            return file_metadata
        time.sleep(retry_after or default_duration)

    cleanup_file(config, local_file_pathname)
    raise TapestClientError(
        f"Failed to extract file {identifier}: "
        f"file unavailable after {max_attempts} attempts",
        exit_code=117,
    )


def delete_file(
    config: Config,
    identifier: str,
    account_name: str | None = None,
) -> None:
    """Delete a file by identifier.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    url = _file_url(config, identifier)
    headers = _build_headers(config, account_name=account_name)
    verify_ssl = _verify_param(config)
    response = requests.delete(url, headers=headers, verify=verify_ssl)
    if response.status_code == 204:
        return None
    raise TapestClientError(
        f"Failed to delete file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def retrieve_file_metadata(
    config: Config,
    identifier: str,
    account_name: str | None = None,
    missing_ok: bool = False,
) -> dict | None:
    """Retrieve metadata for a single file.

    With ``missing_ok`` a file that does not exist gives None instead of
    an error, for a caller that asks whether the identifier is free.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, account_name=account_name)
    verify_ssl = _verify_param(config)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    if missing_ok and response.status_code == 404:
        return None
    raise TapestClientError(
        f"Failed to retrieve metadata for {identifier}: "
        f"{response.status_code} {response.text}"
    )


def update_file_metadata(
    config: Config,
    identifier: str,
    file_metadata_update: dict,
    storage_name: str | None = None,
    account_name: str | None = None,
) -> dict:
    """Update metadata for a single file.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, storage_name, account_name=account_name)
    verify_ssl = _verify_param(config)
    response = requests.patch(
        url, json=file_metadata_update, headers=headers, verify=verify_ssl
    )
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to update metadata for {identifier}: "
        f"{response.status_code} {response.text}"
    )


def retrieve_metadata(
    config: Config,
    query: dict | None = None,
    storage_name: str | None = None,
    account_name: str | None = None,
) -> dict:
    """Retrieve metadata matching query parameters.

    See :func:`ingest_file` for the semantics of ``account_name``.
    """
    url = _metadata_url(config)
    headers = _build_headers(config, storage_name, account_name=account_name)
    verify_ssl = _verify_param(config)
    if query:
        for target_field in ("prefixes", "identifiers"):
            if query.get(target_field):
                query[target_field] = normalize_identifier(
                    query[target_field]
                )

    response = requests.post(
        url, json=query or {}, headers=headers, verify=verify_ssl
    )
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve metadata: {response.status_code} {response.text}"
    )


def retrieve_status(config: Config) -> dict:
    """Retrieve service status."""
    url = f"{config.host}/status"
    headers = {"Authorization": f"Bearer {config.token}"}
    verify_ssl = _verify_param(config)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve status: {response.status_code} {response.text}"
    )
