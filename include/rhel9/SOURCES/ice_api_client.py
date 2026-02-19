#--------------------------------------------------------------------------------
# This file is part of ICE – Ingest · Check · Extract
# The CSC Cold Storage Service for Digital Preservation
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
#--------------------------------------------------------------------------------
# This file provides a baseline client library of functions for interacting with
# the ICE service. It expects a configuration dictionary to be provided to most
# functions, which defines both required and optional details. Defaults are shown
# for optional fields:
# {
#     "ICE_TOKEN": "<token>",                # required
#     "STORAGE_ACCOUNT_NAME": "<name>"       # required for trusted agents
#     "ICE_HOST": "https://ice.csc.fi",      # optional
#     "MAX_RETRY_ATTEMPTS": 10,              # optional
#     "DEFAULT_SLEEP_DURATION": 120,         # optional
#     "CLEANUP_ON_FAIL": True,               # optional
# }
#--------------------------------------------------------------------------------
# Note: The file has no hard dependencies with the ICE implementation and can
# be easily copied into any code base using Python 3.9 or later and for which
# the requests and urllib3 libraries are available:
# 
# pip install requests urllib3
#--------------------------------------------------------------------------------

from __future__ import annotations

import time
import os
import calendar
import datetime
import requests
import urllib.parse
import hashlib
import sys
import urllib3
from datetime import datetime, timezone
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if sys.version_info < (3, 9):
    raise ImportError("This implementation requires Python 3.9 or later.")


# === Utility Functions ===

# Use UTC
os.environ["TZ"] = "UTC"
time.tzset()

TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ' # ISO 8601 UTC

def normalize_timestamp(timestamp):
    """Returns the input timestamp as a normalized ISO 8601 UTC timestamp string YYYY-MM-DDThh:mm:ssZ"""

    # Sniff the input timestamp value and convert to a datetime instance as needed
    if isinstance(timestamp, str):
        timestamp = datetime.utcfromtimestamp(dateutil.parser.parse(timestamp).timestamp())
    elif isinstance(timestamp, float) or isinstance(timestamp, int):
        timestamp = datetime.utcfromtimestamp(timestamp)
    elif not isinstance(timestamp, datetime):
        raise Exception("Invalid timestamp value")

    # Return the normalized ISO UTC timestamp string
    return timestamp.strftime(TIMESTAMP_FORMAT)


def generate_timestamp():
    """Get current time as a normalized ISO 8601 UTC timestamp string YYYY-MM-DDThh:mm:ssZ"""
    time.sleep(1) # ensure a unique timestamp, as timestamps have single-second resolution
    timestamp = normalize_timestamp(datetime.utcnow().replace(microsecond=0))
    time.sleep(1) # ensure all subsequent actions happen after the newly generated timestamp 
    return timestamp


def generate_checksum(local_file_pathname: str) -> str:
    """Computes a SHA-256 checksum for the specified file and returns it in 'sha256:' URI format.

    Args:
        local_file_pathname (str): Full local pathname to the file.

    Returns:
        str: A lowercase SHA-256 checksum string prefixed with 'sha256:'.

    Raises:
        Exception: If required parameters are empty or if the pathname does not resolve to a file.
    """
    if not local_file_pathname:
        raise Exception("Local pathname cannot be empty")
    if not os.path.exists(local_file_pathname):
        raise Exception("File does not exist at specified local pathname")
    if not os.path.isfile(local_file_pathname):
        raise Exception("Local pathname must resolve to a file")
    sha256_hash = hashlib.sha256()
    with open(local_file_pathname, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest().lower()}"


def is_same_file(local_file_pathname: str, size: int, checksum: str) -> bool:
    """Returns true if the file located at the specified local pathname exists and matches the specified
       size and checksum, else returns false.

    Args:
        local_file_pathname (str): Full local pathname to the file to be removed.
        size (int): The file size to be matched
        checksum (str): The checksum to be matched

    Returns:
        None

    Raises:
        Exception: If required parameters are empty.
    """
    if not local_file_pathname:
        raise Exception("Local pathname cannot be empty")
    if size is None or size < 0:
        raise Exception("Size cannot be empty or negative")
    if not checksum:
        raise Exception("Checksum cannot be empty")
    if (not os.path.isfile(local_file_pathname)) or (size != os.path.getsize(local_file_pathname)) or (checksum != generate_checksum(local_file_pathname)):
        return False
    else:
        return True


def cleanup_file(config: dict, local_file_pathname: str) -> None:
    """Removes the local file if CLEANUP_ON_FAIL is enabled in the configuration.
       Exits without error if the file does not exist.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        local_file_pathname (str): Full local pathname to the file to be removed.

    Returns:
        None

    Raises:
        Exception: If required parameters are empty or if the file exists but cannot be removed.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if config.get("CLEANUP_ON_FAIL", False):
        if not local_file_pathname:
            raise Exception("Local pathname cannot be empty")
        if os.path.exists(local_file_pathname):
            if not os.path.isfile(local_file_pathname):
                raise Exception("Local pathname must resolve to a file")
            os.remove(local_file_pathname)


# === Core Operation Functions ===


def ingest_file(config: dict, identifier: str, local_file_pathname: str, storage_name: str = None) -> dict:
    """Ingests the file at the local pathname using the specified identifier, and returns
       the metadata description of the successfully ingested file. A checksum will be
       generated for the local file and provided to the service, along with creation and
       modification timestamps of the local file.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file to be ingested.
        local_file_pathname (str): Local pathname of the file to be ingested.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        The metadata description of the ingested file.

    Raises:
        Exception: If required parameters are empty or if the file does not exist, or if
        checksum generation fails, or if ingestion of the file fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not identifier:
        raise Exception("Identifier cannot be empty")
    if not local_file_pathname:
        raise Exception("Local pathname cannot be empty")
    if not (os.path.exists(local_file_pathname) and os.path.isfile(local_file_pathname)):
        raise Exception("Local file must exist")
    size = os.path.getsize(local_file_pathname)
    checksum = generate_checksum(local_file_pathname)
    created = datetime.fromtimestamp(os.path.getctime(local_file_pathname), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    modified = datetime.fromtimestamp(os.path.getmtime(local_file_pathname), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/file?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}",
        "X-ICE-Size": f"{size}",
        "X-ICE-Checksum": f"{checksum}",
        "X-ICE-Created": f"{created}",
        "X-ICE-Modified": f"{modified}"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    current_attempt = 1
    max_attempts = config.get("MAX_RETRY_ATTEMPTS", 10)
    default_duration = config.get("DEFAULT_SLEEP_DURATION", 120)
    verify_ssl = config.get("VERIFY_SSL", True)

    # Try up to the configured maximum attempts to ingest the file, sleeping in between attempts as directed ...
    while current_attempt <= max_attempts:

        with open(local_file_pathname, "rb") as f:
            response = requests.put(url, headers=headers, data=f, stream=True, verify=verify_ssl)

        if response.status_code == 201:

            return response.json()

        elif response.status_code == 202:

            # Retry after pause
            seconds = int(response.headers.get("Retry-After", default_duration))
            time.sleep(seconds)

        else:

            # Report request failure
            raise Exception(f"Failed to ingest file {identifier}: {response.text}")

        current_attempt += 1

    # Report max attempts failure
    raise Exception(f"Failed to ingest file {identifier}: maximum of {max_attempts} attempts exceeded")


def recache_file(config: dict, identifier: str, local_file_pathname: str, storage_name: str = None) -> dict:
    """Recaches the file at the local pathname using the specified identifier, and returns the
       metadata description of the successfully recached file. A checksum will be generated
       for the local file and provided to the service, and both size and checksum of the local
       file must match those recorded for the file when first ingested.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file to be recached.
        local_file_pathname (str): Local pathname of the file to be recached.
        storage_name (str): Optional storage name, used for multi-storage configurations.

    Returns:
        The metadata description of the recached file.

    Raises:
        Exception: If required parameters are empty or if the file does not exist, or if
        checksum generation fails, or if recaching of the file fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not identifier:
        raise Exception("Identifier cannot be empty")
    if not local_file_pathname:
        raise Exception("Local pathname cannot be empty")
    if not (os.path.exists(local_file_pathname) and os.path.isfile(local_file_pathname)):
        raise Exception("Local file must exist")

    file_metadata = retrieve_file_metadata(config, identifier)

    if os.path.getsize(local_file_pathname) != file_metadata["size"]:
        raise Exception("Local file size does not match originally ingested file size")

    if generate_checksum(local_file_pathname) != file_metadata["checksum"]:
        raise Exception("Local file checksum does not match originally ingested file checksum")

    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/file?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}",
        "X-ICE-Size": f"{file_metadata['size']}",
        "X-ICE-Checksum": f"{file_metadata['checksum']}",
        "X-ICE-Created": f"{file_metadata['created']}",
        "X-ICE-Modified": f"{file_metadata['modified']}",
        "X-ICE-Recache": "true"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    current_attempt = 1
    max_attempts = config.get("MAX_RETRY_ATTEMPTS", 10)
    default_duration = config.get("DEFAULT_SLEEP_DURATION", 120)
    verify_ssl = config.get("VERIFY_SSL", True)

    # Try up to the configured maximum attempts to ingest the file, sleeping in between attempts as directed ...
    while current_attempt <= max_attempts:

        with open(local_file_pathname, "rb") as f:
            response = requests.put(url, headers=headers, data=f, stream=True, verify=verify_ssl)

        if response.status_code == 201:

            return response.json()

        elif response.status_code == 202:

            # Retry after pause
            seconds = int(response.headers.get("Retry-After", default_duration))
            time.sleep(seconds)

        else:

            # Report request failure
            raise Exception(f"Failed to recache file {identifier}: {response.text}")

        current_attempt += 1

    # Report max attempts failure
    raise Exception(f"Failed to recache file {identifier}: maximum of {max_attempts} attempts exceeded")


def extract_file(config: dict, identifier: str, local_file_pathname: str, next_identifier: str = None, storage_name: str = None) -> dict:
    """Extracts the file associated with the specified identifier to the specified local pathname;
       and if specified, noting the next file identifier in the extraction request.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file to be extracted.
        local_file_pathname (str): Local pathname to where the extracted file will be saved.
        next_identifier (str): Identifier of the next file to be extracted, if any.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        The file metadata of the extracted file.

    Raises:
        Exception: If required parameters are empty or if a file already exists at the specified local pathname.
    """
    file_metadata = retrieve_file_metadata(config, identifier, storage_name)
    return extract_file_with_metadata(config, file_metadata, local_file_pathname, next_identifier, storage_name)


def extract_file_with_metadata(config: dict, file_metadata: dict, local_file_pathname: str, next_identifier: str = None, storage_name: str = None) -> dict:
    """Extracts the file described in the provided file metadata description to the specified
       local pathname; and if specified, noting the next file identifier in the extraction request.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        file_metadata (dict): File metadata description.
        local_file_pathname (str): Local pathname to where the extracted file will be saved.
        next_identifier (str): Identifier of the next file to be extracted, if any.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        The file metadata of the extracted file.

    Raises:
        Exception: If required parameters are empty or if a file already exists at the specified local pathname.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not file_metadata:
        raise Exception("File metadata cannot be empty")
    if not local_file_pathname:
        raise Exception("Root directory pathname cannot be empty")
    if os.path.exists(local_file_pathname):
        raise Exception("A file already exists at the specified local pathname")
    identifier = file_metadata["identifier"]
    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/file?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    if next_identifier:
        headers["X-ICE-Next-File"] = urllib.parse.quote(next_identifier, safe="")
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    elif "storage" in file_metadata:
        headers["X-ICE-Storage"] = file_metadata["storage"]

    current_attempt = 1
    max_attempts = max(1, config.get("MAX_RETRY_ATTEMPTS", 10))
    default_duration = max(1, config.get("DEFAULT_SLEEP_DURATION", 120))
    verify_ssl = config.get("VERIFY_SSL", True)

    # Try up to the configured maximum attempts to extract the file, sleeping in between attempts as directed ...
    while current_attempt <= max_attempts:

        response = requests.get(url, headers=headers, stream=True, verify=verify_ssl)

        if response.status_code == 200:

            # Save file to disk
            os.makedirs(os.path.dirname(local_file_pathname), exist_ok=True)
            with open(local_file_pathname, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Verify file size
            size = os.path.getsize(local_file_pathname)
            if size != file_metadata["size"]:
                cleanup_file(config, local_file_pathname)
                raise Exception(f"Failed to extract file {identifier}: size of extracted file does not match size in metadata")

            # Verify checksum
            checksum = generate_checksum(local_file_pathname)
            if checksum != file_metadata["checksum"]:
                cleanup_file(config, local_file_pathname)
                raise Exception(f"Failed to extract file {identifier}: checksum of extracted file does not match checksum in metadata")

            # Set file timestamps to original values
            created_ts = calendar.timegm(datetime.strptime(file_metadata["created"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
            modified_ts = calendar.timegm(datetime.strptime(file_metadata["modified"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
            os.utime(local_file_pathname, (created_ts, modified_ts))

            return file_metadata

        elif response.status_code == 202:

            # Retry after pause
            seconds = int(response.headers.get("Retry-After", default_duration))
            time.sleep(seconds)

        else:

            # Report request failure
            cleanup_file(config, local_file_pathname)
            raise Exception(f"Failed to extract file {identifier}: {response.text}")

        current_attempt += 1

    # Report max attempts failure
    cleanup_file(config, local_file_pathname)
    raise Exception(f"Failed to extract file {identifier}: maximum of {max_attempts} attempts exceeded")


def delete_file(config: dict, identifier: str, storage_name: str = None) -> None:
    """Deletes the file associated with the specified identifier.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file to be deleted.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        None

    Raises:
        Exception: If required parameters are empty or if file deletion fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not identifier:
        raise Exception("Identifier cannot be empty")
    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/file?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.delete(url, headers=headers, verify=verify_ssl)
    if response.status_code == 204:
        return None
    else:
        raise Exception(f"Failed to delete file. Status Code: {response.status_code}: {response.text}")


def retrieve_file_metadata(config: dict, identifier: str, storage_name: str = None) -> dict:
    """Retrieves the metadata for the file associated with the specified identifier.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file for which metadata is to be retrieved.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        dict: The file metadata.

    Raises:
        Exception: If required parameters are empty or if metadata retrieval fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not identifier:
        raise Exception("Identifier cannot be empty")
    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/metadata?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to retrieve file metadata. Status Code: {response.status_code}: {response.text}")


def update_file_metadata(config: dict, identifier: str, file_metadata_update: dict, storage_name: str = None) -> dict:
    """Updates the metadata for the file associated with the specified identifier.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        identifier (str): Identifier of the file for which metadata is to be updated.
        file_metadata_update (dict): Partial file metadata description containing the metadata to be updated.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        dict: The complete and updated file metadata.

    Raises:
        Exception: If required parameters are empty or if metadata update fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not identifier:
        raise Exception("Identifier cannot be empty")
    if not file_metadata_update:
        raise Exception("File metadata update cannot be empty")
    encoded_identifier = urllib.parse.quote(identifier, safe="")
    url = config["ICE_HOST"] + f"/metadata?identifier={encoded_identifier}"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.patch(url, json=file_metadata_update, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to update file metadata. Status Code: {response.status_code}: {response.text}")


def retrieve_metadata(config: dict, query: dict = {}, storage_name: str = None) -> dict:
    """Retrieves metadata for all files matching the query parameters.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        query (dict): Optional dictionary defining the query parameters, if any.
        storage_name (str): Optional storage name, used for multi-storage and/or tape configurations.

    Returns:
        dict: The metadata query results.

    Raises:
        Exception: If required parameters are empty or if metadata retrieval fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    url = config["ICE_HOST"] + "/metadata"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    if "STORAGE_ACCOUNT_NAME" in config:
        headers["X-ICE-Account"] = config["STORAGE_ACCOUNT_NAME"]
    if storage_name:
        headers["X-ICE-Storage"] = storage_name
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.post(url, json=query, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to retrieve metadata. Status Code: {response.status_code}: {response.text}")


def retrieve_status(config: dict) -> dict:
    """Retrieves the current service status details.

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.

    Returns:
        dict: The current service status details.

    Raises:
        Exception: If required parameters are empty or if status retrieval fails.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    url = config["ICE_HOST"] + "/status"
    headers = {
        "Authorization": f"Bearer {config['ICE_TOKEN']}"
    }
    verify_ssl = config.get("VERIFY_SSL", True)
    response = requests.get(url, headers=headers, verify=verify_ssl)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to retrieve status. Status Code: {response.status_code}: {response.text}")


# === Batch Operation Functions ===


def ingest_files_from_directory(config: dict, local_directory_pathname: str, skip: bool = False, force: bool = False) -> list[dict]:
    """Ingests all local files within the scope of a specified root directory, and returns
       a list of file metadata descriptions for all newly ingested files. Identifiers are
       derived from the relative pathname of each file within the scope of the root directory,
       and including the basename of the root directory;
       e.g. 
           root directory      = "/some/path/to/a/root/directory/200182"
           file local pathname = "/some/path/to/a/root/directory/200182/relative/path/to/file/xyz.dat"
           file identifier     = "/200182/relative/path/to/file/xyz.dat"

       By default, an error occurs if any file is already ingested using a derived identifier;
       however, if skip == True is specified, local files corresponding to the identifiers
       of already ingested files will be ignored if their size and checksum match those of
       the ingested file. This allows for easier ingestion of new files from the scope of a
       common root directory.

       If force == True, then in cases where an already ingested file exists, if the files are
       not the same based on size and checksum, the previously ingested file will be deleted and
       the local file ingested in its place. USE THIS FEATURE WITH GREAT CAUTION!

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        local_directory_pathname (str): Local pathname to directory from which files will be ingested.
        skip (bool): Default = False. If False, ignore already ingested files that match.
        force (bool): Default = False. If True, replace ingested files with different local files.

    Returns:
        A list of file metadata descriptions for all ingested files.

    Raises:
        Exception: If required parameters are empty or if the root directory does not exist or
        if skip == False and a file has already been ingested with a derived identifier or if
        force == False and the local file does not match the already ingested file.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not local_directory_pathname:
        raise Exception("Root directory pathname cannot be empty")
    if not (os.path.exists(local_directory_pathname) and os.path.isdir(local_directory_pathname)):
        raise Exception("Root directory must exist")
    local_directory_pathname = os.path.basename(os.path.normpath(local_directory_pathname))
    ingested_files = []
    for dirpath, _, filenames in os.walk(local_directory_pathname):
        for filename in filenames:
            local_file_pathname = os.path.join(dirpath, filename)
            relative_path = os.path.normpath(os.path.relpath(local_file_pathname, local_directory_pathname))
            identifier = "/" + os.path.join(local_directory_pathname, relative_path).replace(os.sep, "/")
            try:
                file_metadata = retrieve_file_metadata(config, identifier)
                conflict = True
                if skip or force:
                    same_file = is_same_file(local_file_pathname, file_metadata["size"], file_metadata["checksum"])
                    if skip and same_file:
                        conflict = False
                        continue
                    if force and not same_file:
                        delete_file(config, identifier)
                        conflict = False
                if conflict:
                    raise Exception(f"A file already exists with the identifier {identifier}")
            except Exception as e:
                if not str(e).startswith("Failed to retrieve file metadata. Status Code: 404"):
                    raise
            ingested_files.append(ingest_file(config, identifier, local_file_pathname))
    return ingested_files


def extract_files_to_directory(config: dict, metadata: list[dict], local_directory_pathname: str, skip: bool = False, force: bool = False, storage_name: str = None) -> list[dict]:
    """Extracts all files described in the provided list of file metadata descriptions into the specified
       root directory, using the file identifiers as relative local pathnames;
       e.g. 
           root directory      = "/some/path/to/a/root/directory"
           file identifier     = "/200182/relative/path/to/file/xyz.dat"
           file local pathname = "/some/path/to/a/root/directory/200182/relative/path/to/file/xyz.dat"

       It is assumed the sequence of files provided is optimized for extraction; i.e. obtained with a
       metadata query where order_by = storage was specified.

       By default, an error occurs if any file is already extracted using a derived identifier;
       however, if skip == True is specified, ingested files corresponding to the identifiers
       of existing local will be ignored if their size and checksum match those of the local file.
       This allows for easier resumption of extractions of large numbers of files.

       If force == True, then in cases where the ingested file does not match the size and checksum of
       a local file with a local pathname derived from the same identifier, the existing local file will
       be deleted and the ingested file extracted in its place. USE THIS FEATURE WITH GREAT CAUTION!

    Args:
        config (dict): Configuration dictionary with ICE parameters and settings.
        local_directory_pathname (str): Local pathname to directory into which files will be extracted.
        skip (bool): Default = False. If True, ignore already extracted files that match.
        force (bool): Default = False. If True, replace local files with extracted files when different.

    Returns:
        A list of file metadata descriptions for all extracted files.

    Raises:
        Exception: If required parameters are empty or if the root directory does not exist or if
        skip == False and a local file already exists or if skip == True and force == False and a
        local file already exists that does not match the ingested file.
    """
    if not config:
        raise Exception("Configuration cannot be empty")
    if not metadata:
        raise Exception("Metadata cannot be empty")
    if not local_directory_pathname:
        raise Exception("Root directory pathname cannot be empty")
    if not (os.path.exists(local_directory_pathname) and os.path.isdir(local_directory_pathname)):
        raise Exception("Root directory must exist")
    extracted_files = []
    for i, file_metadata in enumerate(metadata):
        identifier = file_metadata["identifier"]
        next_identifier = metadata[i + 1]["identifier"] if i + 1 < len(metadata) else None
        local_file_pathname = os.path.join(local_directory_pathname, identifier.lstrip("/"))
        if os.path.exists(local_file_pathname):
            conflict = True
            if skip or force:
                same_file = is_same_file(local_file_pathname, file_metadata["size"], file_metadata["checksum"])
                if skip and same_file:
                    continue
                if force and not same_file:
                    os.path.unlink(local_file_pathname)
                    conflict = False
            if conflict:
                raise Exception(f"A file already exists at the specified local pathname {local_file_pathname}")
        extracted_files.append(extract_file_with_metadata(config, file_metadata, local_file_pathname, next_identifier, storage_name))
    return extracted_files
