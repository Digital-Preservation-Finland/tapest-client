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
# ----------------------------------------------------------------------

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from collections.abc import Callable

import requests

from tapest_client.config import Config

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class TapestClientError(Exception):
    """Error raised by tapest-client operations."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# === Internal Helpers ===


def _build_headers(config: Config, storage_name: str | None = None,
                   extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build common request headers from config."""
    headers = {"Authorization": f"Bearer {config.token}"}
    if config.storage_account_name:
        headers["X-ICE-Account"] = config.storage_account_name
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    if extra:
        headers.update(extra)
    return headers


def _file_url(config: Config, identifier: str) -> str:
    """Build /file endpoint URL."""
    encoded = urllib.parse.quote(identifier, safe="")
    return f"{config.host}/file?identifier={encoded}"


def _metadata_url(config: Config, identifier: str | None = None) -> str:
    """Build /metadata endpoint URL."""
    if identifier:
        encoded = urllib.parse.quote(identifier, safe="")
        return f"{config.host}/metadata?identifier={encoded}"
    return f"{config.host}/metadata"


def _verify_param(config: Config) -> str | bool:
    """Return the ``verify`` value for requests calls."""
    if config.verify_ssl and config.ca_cert_path:
        return config.ca_cert_path
    return config.verify_ssl


def _request_with_retry(request_fn: Callable[[], requests.Response],
                        config: Config, error_msg: str) -> requests.Response:
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
        exit_code=117
    )


# === Utility Functions ===


def generate_checksum(local_file_pathname: str | Path) -> str:
    """Compute SHA-256 checksum for a file, returned as 'sha256:<hex>'."""
    sha256_hash = hashlib.sha256()
    with open(local_file_pathname, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            sha256_hash.update(block)
    return f"sha256:{sha256_hash.hexdigest()}"


def is_same_file(local_file_pathname: str | Path, size: int,
                 checksum: str) -> bool:
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


# === Core Operation Functions ===


def ingest_file(config: Config, identifier: str, local_file_pathname: str,
                storage_name: str | None = None) -> dict:
    """Ingest a local file and return its metadata.

    A single stat() call is used to collect size, creation time and
    modification time, avoiding redundant filesystem round-trips.
    """
    path = Path(local_file_pathname)
    stat = path.stat()
    checksum = generate_checksum(path)
    created = datetime.fromtimestamp(
        stat.st_ctime, timezone.utc
    ).strftime(TIMESTAMP_FORMAT)
    modified = datetime.fromtimestamp(
        stat.st_mtime, timezone.utc
    ).strftime(TIMESTAMP_FORMAT)

    url = _file_url(config, identifier)
    headers = _build_headers(config, storage_name, {
        "X-ICE-Size": str(stat.st_size),
        "X-ICE-Checksum": checksum,
        "X-ICE-Created": created,
        "X-ICE-Modified": modified,
    })
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
    raise TapestClientError(
        f"Failed to ingest file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def recache_file(config: Config, identifier: str, local_file_pathname: str,
                 storage_name: str | None = None) -> dict:
    """Re-upload a cached file, verifying it matches."""
    path = Path(local_file_pathname)
    file_metadata = retrieve_file_metadata(config, identifier)

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

    url = _file_url(config, identifier)
    headers = _build_headers(config, storage_name, {
        "X-ICE-Size": str(file_metadata["size"]),
        "X-ICE-Checksum": file_metadata["checksum"],
        "X-ICE-Created": file_metadata["created"],
        "X-ICE-Modified": file_metadata["modified"],
        "X-ICE-Recache": "true",
    })
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


def extract_file(config: Config, identifier: str, local_file_pathname: str,
                 next_identifier: str | None = None,
                 storage_name: str | None = None) -> dict:
    """Extract a file by identifier to a local path."""
    file_metadata = retrieve_file_metadata(
        config, identifier, storage_name
    )
    return extract_file_with_metadata(
        config, file_metadata, local_file_pathname,
        next_identifier, storage_name
    )


def extract_file_with_metadata(config: Config, file_metadata: dict,
                               local_file_pathname: str,
                               next_identifier: str | None = None,
                               storage_name: str | None = None) -> dict:
    """Extract a file using provided metadata to a local path.

    Uses its own retry loop because the response body must be written to
    disk and verified after each attempt.
    """
    path = Path(local_file_pathname)
    if path.exists():
        raise TapestClientError(
            f"File already exists at {local_file_pathname}"
        )

    identifier = file_metadata["identifier"]
    url = _file_url(config, identifier)
    headers = _build_headers(
        config, storage_name or file_metadata.get("storage")
    )
    if next_identifier:
        headers["X-ICE-Next-File"] = urllib.parse.quote(
            next_identifier, safe=""
        )

    verify_ssl = _verify_param(config)
    max_attempts = max(1, config.max_retry_attempts)
    default_duration = max(1, config.default_sleep_duration)

    for _ in range(max_attempts):
        response = requests.get(
            url, headers=headers, stream=True, verify=verify_ssl
        )

        if response.status_code == 200:
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

            created_ts = datetime.strptime(
                file_metadata["created"], TIMESTAMP_FORMAT
            ).replace(tzinfo=timezone.utc).timestamp()
            modified_ts = datetime.strptime(
                file_metadata["modified"], TIMESTAMP_FORMAT
            ).replace(tzinfo=timezone.utc).timestamp()
            os.utime(path, (created_ts, modified_ts))

            return file_metadata

        if response.status_code == 202:
            seconds = int(response.headers.get(
                "Retry-After", default_duration
            ))
            time.sleep(seconds)
            continue

        cleanup_file(config, local_file_pathname)
        raise TapestClientError(
            f"Failed to extract file {identifier}: "
            f"{response.status_code} {response.text}"
        )

    cleanup_file(config, local_file_pathname)
    raise TapestClientError(
        f"Failed to extract file {identifier}: "
        f"file unavailable after {max_attempts} attempts",
        exit_code=117
    )


def delete_file(config: Config, identifier: str,
                storage_name: str | None = None) -> None:
    """Delete a file by identifier."""
    url = _file_url(config, identifier)
    headers = _build_headers(config, storage_name)
    verify_ssl = _verify_param(config)
    response = requests.delete(url, headers=headers, verify=verify_ssl)
    if response.status_code == 204:
        return None
    raise TapestClientError(
        f"Failed to delete file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def retrieve_file_metadata(config: Config, identifier: str,
                           storage_name: str | None = None) -> dict:
    """Retrieve metadata for a single file."""
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, storage_name)
    verify_ssl = _verify_param(config)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve metadata for {identifier}: "
        f"{response.status_code} {response.text}"
    )


def update_file_metadata(config: Config, identifier: str,
                         file_metadata_update: dict,
                         storage_name: str | None = None) -> dict:
    """Update metadata for a single file."""
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, storage_name)
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


def retrieve_metadata(config: Config, query: dict | None = None,
                      storage_name: str | None = None) -> dict:
    """Retrieve metadata matching query parameters."""
    url = _metadata_url(config)
    headers = _build_headers(config, storage_name)
    verify_ssl = _verify_param(config)
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


# === Batch Operation Functions ===


def ingest_files_from_directory(config: Config, local_directory_pathname: str,
                                skip: bool = False,
                                force: bool = False,
                                prefix: str | None = None) -> list[dict]:
    """Ingest all files from a directory tree.

    Identifiers are derived from relative paths within the directory.
    By default, the directory basename is used as prefix:
        directory:  /path/to/200182
        file:       /path/to/200182/sub/file.dat
        identifier: /200182/sub/file.dat

    When ``prefix`` is given, it replaces the directory basename. The
    prefix is normalized to a single leading ``/`` with no trailing ``/``:
        prefix:     kuvi/2024
        identifier: /kuvi/2024/sub/file.dat
    """
    root = Path(local_directory_pathname).resolve()
    if not root.is_dir():
        raise TapestClientError(
            f"Directory does not exist: {local_directory_pathname}"
        )

    if prefix is None:
        prefix = "/" + root.name
    else:
        prefix = "/" + prefix.strip("/")

    ingested = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        identifier = prefix + "/" + path.relative_to(root).as_posix()

        existing = _find_file_metadata(config, identifier)
        if existing is None:
            ingested.append(ingest_file(config, identifier, str(path)))
            continue
        if not (skip or force):
            # Raise before hashing; is_same_file reads the whole file.
            raise TapestClientError(
                f"File already exists with identifier {identifier}"
            )
        same = is_same_file(
            str(path), existing["size"], existing["checksum"]
        )
        action = _conflict_action(skip, force, same)
        if action is _ConflictAction.SKIP:
            continue
        if action is _ConflictAction.REPLACE:
            delete_file(config, identifier)
            ingested.append(ingest_file(config, identifier, str(path)))
            continue
        raise TapestClientError(
            f"File already exists with identifier {identifier}"
        )

    return ingested


def _find_file_metadata(config: Config, identifier: str) -> dict | None:
    """Return file metadata, or None if the file does not exist."""
    try:
        return retrieve_file_metadata(config, identifier)
    except TapestClientError as e:
        if "404" not in str(e):
            raise
        return None


class _ConflictAction(Enum):
    SKIP = "skip"
    REPLACE = "replace"
    ERROR = "error"


def _conflict_action(skip: bool, force: bool, same: bool) -> _ConflictAction:
    """Resolve an existing-file conflict."""
    if skip and same:
        return _ConflictAction.SKIP
    if force and not same:
        return _ConflictAction.REPLACE
    return _ConflictAction.ERROR


def extract_files_to_directory(config: Config, metadata: list[dict],
                               local_directory_pathname: str,
                               skip: bool = False, force: bool = False,
                               storage_name: str | None = None) -> list[dict]:
    """Extract files described in metadata list to a directory tree."""
    root = Path(local_directory_pathname)
    if not root.is_dir():
        raise TapestClientError(
            f"Directory does not exist: {local_directory_pathname}"
        )

    extracted = []
    for i, file_metadata in enumerate(metadata):
        identifier = file_metadata["identifier"]
        next_id = (
            metadata[i + 1]["identifier"] if i + 1 < len(metadata) else None
        )
        path = root / identifier.lstrip("/")

        if not path.exists():
            extracted.append(extract_file_with_metadata(
                config, file_metadata, str(path), next_id, storage_name
            ))
            continue
        if not (skip or force):
            # Raise before hashing; is_same_file reads the whole file.
            raise TapestClientError(f"File already exists at {path}")
        same = is_same_file(
            str(path), file_metadata["size"], file_metadata["checksum"]
        )
        action = _conflict_action(skip, force, same)
        if action is _ConflictAction.SKIP:
            continue
        if action is _ConflictAction.REPLACE:
            path.unlink()
            extracted.append(extract_file_with_metadata(
                config, file_metadata, str(path), next_id, storage_name
            ))
            continue
        raise TapestClientError(f"File already exists at {path}")

    return extracted
