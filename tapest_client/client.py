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
# TapeSt API client library. Expects a configuration dictionary:
# {
#     "ICE_TOKEN": "<token>",                # required
#     "ICE_HOST": "https://ice.csc.fi",      # required
#     "STORAGE_ACCOUNT_NAME": "<name>",      # required for trusted agents
#     "MAX_RETRY_ATTEMPTS": 10,              # optional (default: 10)
#     "DEFAULT_SLEEP_DURATION": 120,         # optional (default: 120)
#     "CLEANUP_ON_FAIL": True,               # optional (default: False)
#     "VERIFY_SSL": True,                    # optional (default: True)
# }
# ----------------------------------------------------------------------

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class TapestClientError(Exception):
    """Error raised by tapest-client operations."""


# === Internal Helpers ===


def _build_headers(config, storage_name=None, extra=None):
    """Build common request headers from config."""
    headers = {"Authorization": f"Bearer {config['ICE_TOKEN']}"}
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    if extra:
        headers.update(extra)
    return headers


def _file_url(config, identifier):
    """Build /file endpoint URL."""
    encoded = urllib.parse.quote(identifier, safe="")
    return f"{config['ICE_HOST']}/file?identifier={encoded}"


def _metadata_url(config, identifier=None):
    """Build /metadata endpoint URL."""
    if identifier:
        encoded = urllib.parse.quote(identifier, safe="")
        return f"{config['ICE_HOST']}/metadata?identifier={encoded}"
    return f"{config['ICE_HOST']}/metadata"


def _request_with_retry(request_fn, config, error_msg):
    """Execute request_fn in a retry loop, handling 202 Retry-After responses.

    Returns the first non-202 response. Raises TapestClientError if max
    retry attempts are exceeded.
    """
    max_attempts = max(1, config.get("MAX_RETRY_ATTEMPTS", 10))
    default_duration = config.get("DEFAULT_SLEEP_DURATION", 120)
    for _ in range(max_attempts):
        response = request_fn()
        if response.status_code != 202:
            return response
        seconds = int(response.headers.get("Retry-After", default_duration))
        time.sleep(seconds)
    raise TapestClientError(
        f"{error_msg}: max {max_attempts} attempts exceeded"
    )


# === Utility Functions ===


def generate_checksum(local_file_pathname):
    """Compute SHA-256 checksum for a file, returned as 'sha256:<hex>'."""
    sha256_hash = hashlib.sha256()
    with open(local_file_pathname, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            sha256_hash.update(block)
    return f"sha256:{sha256_hash.hexdigest()}"


def is_same_file(local_file_pathname, size, checksum):
    """Check if file matches expected size and checksum."""
    try:
        if Path(local_file_pathname).stat().st_size != size:
            return False
        return generate_checksum(local_file_pathname) == checksum
    except OSError:
        return False


def cleanup_file(config, local_file_pathname):
    """Remove the file if CLEANUP_ON_FAIL is enabled. Ignores missing files."""
    if config.get("CLEANUP_ON_FAIL", False):
        try:
            Path(local_file_pathname).unlink()
        except OSError:
            pass


# === Core Operation Functions ===


def ingest_file(config, identifier, local_file_pathname, storage_name=None):
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
    verify_ssl = config.get("VERIFY_SSL", True)

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


def recache_file(config, identifier, local_file_pathname, storage_name=None):
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
    verify_ssl = config.get("VERIFY_SSL", True)

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


def extract_file(config, identifier, local_file_pathname,
                 next_identifier=None, storage_name=None):
    """Extract a file by identifier to a local path."""
    file_metadata = retrieve_file_metadata(
        config, identifier, storage_name
    )
    return extract_file_with_metadata(
        config, file_metadata, local_file_pathname,
        next_identifier, storage_name
    )


def extract_file_with_metadata(config, file_metadata, local_file_pathname,
                               next_identifier=None, storage_name=None):
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

    verify_ssl = config.get("VERIFY_SSL", True)
    max_attempts = max(1, config.get("MAX_RETRY_ATTEMPTS", 10))
    default_duration = max(1, config.get("DEFAULT_SLEEP_DURATION", 120))

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
        f"max {max_attempts} attempts exceeded"
    )


def delete_file(config, identifier, storage_name=None):
    """Delete a file by identifier."""
    url = _file_url(config, identifier)
    headers = _build_headers(config, storage_name)
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.delete(url, headers=headers, verify=verify_ssl)
    if response.status_code == 204:
        return None
    raise TapestClientError(
        f"Failed to delete file {identifier}: "
        f"{response.status_code} {response.text}"
    )


def retrieve_file_metadata(config, identifier, storage_name=None):
    """Retrieve metadata for a single file."""
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, storage_name)
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve metadata for {identifier}: "
        f"{response.status_code} {response.text}"
    )


def update_file_metadata(config, identifier, file_metadata_update,
                         storage_name=None):
    """Update metadata for a single file."""
    url = _metadata_url(config, identifier)
    headers = _build_headers(config, storage_name)
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.patch(
        url, json=file_metadata_update, headers=headers, verify=verify_ssl
    )
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to update metadata for {identifier}: "
        f"{response.status_code} {response.text}"
    )


def retrieve_metadata(config, query=None, storage_name=None):
    """Retrieve metadata matching query parameters."""
    url = _metadata_url(config)
    headers = _build_headers(config, storage_name)
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.post(
        url, json=query or {}, headers=headers, verify=verify_ssl
    )
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve metadata: {response.status_code} {response.text}"
    )


def retrieve_status(config):
    """Retrieve service status."""
    url = f"{config['ICE_HOST']}/status"
    headers = {"Authorization": f"Bearer {config['ICE_TOKEN']}"}
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    raise TapestClientError(
        f"Failed to retrieve status: {response.status_code} {response.text}"
    )


# === Batch Operation Functions ===


def ingest_files_from_directory(config, local_directory_pathname,
                                skip=False, force=False):
    """Ingest all files from a directory tree.

    Identifiers are derived from relative paths, prefixed with the
    directory basename:
        directory:  /path/to/200182
        file:       /path/to/200182/sub/file.dat
        identifier: /200182/sub/file.dat
    """
    root = Path(local_directory_pathname).resolve()
    if not root.is_dir():
        raise TapestClientError(
            f"Directory does not exist: {local_directory_pathname}"
        )

    ingested = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        identifier = "/" + root.name + "/" + path.relative_to(root).as_posix()

        try:
            file_metadata = retrieve_file_metadata(config, identifier)
        except TapestClientError as e:
            if "404" not in str(e):
                raise
            ingested.append(ingest_file(config, identifier, str(path)))
            continue

        if skip or force:
            same = is_same_file(
                str(path), file_metadata["size"], file_metadata["checksum"]
            )
            if skip and same:
                continue
            if force and not same:
                delete_file(config, identifier)
                ingested.append(ingest_file(config, identifier, str(path)))
                continue

        raise TapestClientError(
            f"File already exists with identifier {identifier}"
        )

    return ingested


def extract_files_to_directory(config, metadata, local_directory_pathname,
                               skip=False, force=False, storage_name=None):
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

        if path.exists():
            if skip or force:
                same = is_same_file(
                    str(path), file_metadata["size"], file_metadata["checksum"]
                )
                if skip and same:
                    continue
                if force and not same:
                    path.unlink()
                else:
                    raise TapestClientError(
                        f"File already exists at {path}"
                    )
            else:
                raise TapestClientError(f"File already exists at {path}")

        extracted.append(
            extract_file_with_metadata(
                config, file_metadata, str(path), next_id, storage_name
            )
        )

    return extracted
