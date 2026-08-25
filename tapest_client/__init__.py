"""TapeSt API client library."""

try:
    from ._version import version as __version__
except ImportError:
    # Package not installed
    __version__ = "unknown"

from .config import Config, get_config

from .client import (
    TapestClientError,
    FileAlreadyExistsError,
    normalize_identifier,
    generate_checksum,
    is_same_file,
    cleanup_file,
    ingest_file,
    recache_file,
    extract_file,
    extract_file_with_metadata,
    delete_file,
    retrieve_file_metadata,
    update_file_metadata,
    retrieve_metadata,
    retrieve_status,
)

__all__ = [
    "Config",
    "get_config",
    "TapestClientError",
    "FileAlreadyExistsError",
    "normalize_identifier",
    "generate_checksum",
    "is_same_file",
    "cleanup_file",
    "ingest_file",
    "recache_file",
    "extract_file",
    "extract_file_with_metadata",
    "delete_file",
    "retrieve_file_metadata",
    "update_file_metadata",
    "retrieve_metadata",
    "retrieve_status",
]
