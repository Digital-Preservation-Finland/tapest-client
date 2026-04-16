# ----------------------------------------------------------------------
# This file is part of TapeSt - Tape Storage
# The CSC Digital Preservation Tape Storage Service
#
# Copyright (C) 2026 CSC - IT Center for Science Ltd.
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
"""TapeSt command-line client.

Usage::

    tapest-client [-v | -V] [-c CONFIG] [--host HOST] COMMAND [options] [args]

Commands::

    ingest-one         Ingest (upload) a single file
    ingest-many        Ingest (upload) all files from a directory
    extract-one       Extract (download) a single file from the service
    extract-many      Extract (download) files from the service to a directory
    delete             Delete a single file and its metadata from the service
    query-metadata     Query metadata for one or many files
    update-metadata    Update metadata for a single file
    status             Retrieve service status
    write-config       Write default configuration file
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
from pathlib import Path
import sys

import requests
import tapest_client
from tapest_client import TapestClientError
from tapest_client.config import CONFIG_FILE, Config

logger = logging.getLogger("tapest-client")

DEFAULT_CONFIG = json.dumps(dataclasses.asdict(Config()), indent=2) + "\n"

METADATA_ORDER_CHOICES = [
    "identifier", "storage", "ingested", "stored",
    "checked", "required", "recached", "extracted",
]


# -- Parser construction -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="tapest-client",
        description="TapeSt command-line client",
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog, max_help_position=40),
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true",
        help="verbose output")
    verbosity.add_argument(
        "-V", "--debug", action="store_true",
        help="debug output (implies verbose)")

    parser.add_argument(
        "--config", default=None,
        help="configuration file")
    parser.add_argument(
        "--host", default=None,
        help="API host URL")
    parser.add_argument(
        "--ca-cert", default=None, metavar="PATH",
        help="CA certificate bundle (PEM) for SSL verification")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ingest-one
    sub = subparsers.add_parser(
        "ingest-one", help="Ingest (upload) a single file")
    sub.add_argument("file_id", metavar="FILE_ID")
    sub.add_argument("local_path", metavar="LOCAL_PATH")
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_ingest)

    # ingest-many
    sub = subparsers.add_parser(
        "ingest-many", help="Ingest (upload) all files from a directory")
    sub.add_argument("local_dir", metavar="LOCAL_DIR")
    sub.add_argument(
        "--prefix", default=None,
        help="identifier prefix (default: directory basename)")
    sub.add_argument(
        "--skip", action="store_true",
        help="skip files that already exist and match")
    sub.add_argument(
        "--force", action="store_true",
        help="overwrite files that already exist")
    sub.set_defaults(func=_run_ingest_directory)

    # extract-one
    sub = subparsers.add_parser(
        "extract-one",
        help="Extract (download) a single file from the service")
    sub.add_argument("file_id", metavar="FILE_ID")
    sub.add_argument("local_path", metavar="LOCAL_PATH")
    sub.add_argument(
        "next_file_id", metavar="NEXT_FILE_ID", nargs="?", default=None)
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_extract)

    # extract-many
    sub = subparsers.add_parser(
        "extract-many",
        help="Extract (download) files from the service to a directory")
    sub.add_argument("local_dir", metavar="LOCAL_DIR")
    sub.add_argument(
        "--prefix", action="append", default=None,
        help="file identifier prefix (repeatable)")
    sub.add_argument(
        "--identifier", action="append", default=None,
        help="file identifier (repeatable)")
    sub.add_argument(
        "--skip", action="store_true",
        help="skip files that already exist and match")
    sub.add_argument(
        "--force", action="store_true",
        help="overwrite files that already exist")
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_extract_files)

    # delete
    sub = subparsers.add_parser(
        "delete",
        help="Delete a single file and its metadata from the service")
    sub.add_argument("file_id", metavar="FILE_ID")
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_delete)

    # query-metadata
    sub = subparsers.add_parser(
        "query-metadata", help="Query metadata for one or many files")
    sub.add_argument(
        "file_id", metavar="FILE_ID", nargs="?", default=None)
    sub.add_argument(
        "--prefix", action="append", default=None,
        help="file identifier prefix (repeatable)")
    sub.add_argument(
        "--identifier", action="append", default=None,
        help="file identifier (repeatable)")
    sub.add_argument("--storage", default=None)
    sub.add_argument(
        "--pending", action="store_true",
        help="pending files only")
    sub.add_argument(
        "--errors", action="store_true",
        help="files with errors only")
    sub.add_argument(
        "--order", choices=METADATA_ORDER_CHOICES, default=None)
    sub.add_argument("--limit", type=int, default=None)
    sub.set_defaults(func=_run_query_metadata)

    # update-metadata
    sub = subparsers.add_parser(
        "update-metadata", help="Update metadata for a single file")
    sub.add_argument("file_id", metavar="FILE_ID")
    sub.add_argument(
        "json_input", metavar="JSON", nargs="?", default=None,
        help="JSON string, or - for stdin")
    sub.add_argument(
        "--file", dest="json_file", default=None,
        help="read JSON from file")
    sub.add_argument(
        "--stdin", action="store_true",
        help="read JSON from stdin")
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_update_metadata)

    # status
    sub = subparsers.add_parser("status", help="Retrieve service status")
    sub.set_defaults(func=_run_status)

    # write-config
    sub = subparsers.add_parser(
        "write-config", help="Write default configuration file")
    sub.set_defaults(func=_run_write_config, needs_config=False)

    return parser


# -- Utilities ---------------------------------------------------------------

def _setup_logging(args: argparse.Namespace) -> None:
    """Configure logging based on verbosity flags."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    if args.debug:
        logger.setLevel(logging.DEBUG)
    elif args.verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)


USER_CONFIG_FILE = os.path.expanduser("~/.config/tapest-client/client.conf")


def _resolve_config_file(args_config: str | None) -> str:
    """Find the config file: explicit -c, then system, then user home."""
    if args_config:
        return args_config
    if os.path.isfile(CONFIG_FILE):
        return CONFIG_FILE
    if os.path.isfile(USER_CONFIG_FILE):
        return USER_CONFIG_FILE
    return CONFIG_FILE


def _load_config(args: argparse.Namespace) -> Config:
    """Create and load configuration, applying CLI overrides."""
    config = Config()
    config_file = _resolve_config_file(args.config)
    config.read(config_file=config_file)
    if args.host:
        config.host = args.host
    if args.ca_cert:
        config.ca_cert_path = args.ca_cert
    if not config.host:
        raise TapestClientError(
            "No API host configured. Set host in "
            f"{config_file} or {USER_CONFIG_FILE}")
    if not config.token:
        raise TapestClientError(
            "No API token configured. Set token in "
            f"{config_file} or TAPEST_CLIENT_TOKEN env var")
    logger.debug("Config: host=%s account=%s",
                 config.host, config.storage_account_name)
    return config


def _print_json(data: object) -> None:
    """Print data as formatted JSON to stdout."""
    print(json.dumps(data, indent=2))


# -- Subcommand handlers -----------------------------------------------------

def _run_write_config(_config: Config | None,
                      args: argparse.Namespace) -> None:
    """Write a default configuration file to the user config directory."""
    path = Path(USER_CONFIG_FILE)
    if path.is_file():
        logger.error("Configuration file already exists: %s", path)
        sys.exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG)
    print(f"Configuration file written to {path}")
    print(f"Edit it with your credentials:  vi {path}")


def _run_status(config: Config, args: argparse.Namespace) -> None:
    """Retrieve service status."""
    logger.info("Retrieving service status ...")
    result = tapest_client.retrieve_status(config)
    _print_json(result)
    logger.info("Service status retrieved successfully")


def _run_ingest(config: Config, args: argparse.Namespace) -> None:
    """Ingest a single file."""
    logger.info("Ingesting file at local pathname '%s' with identifier "
                "'%s' ...", args.local_path, args.file_id)
    result = tapest_client.ingest_file(
        config, args.file_id, args.local_path,
        storage_name=args.storage)
    _print_json(result)
    logger.info("Successfully ingested file with identifier '%s' from "
                "local pathname '%s'", args.file_id, args.local_path)


def _run_extract(config: Config, args: argparse.Namespace) -> None:
    """Extract a single file."""
    logger.info("Extracting file with identifier '%s' to local pathname "
                "'%s' ...", args.file_id, args.local_path)
    result = tapest_client.extract_file(
        config, args.file_id, args.local_path,
        next_identifier=args.next_file_id,
        storage_name=args.storage)
    _print_json(result)
    logger.info("Successfully extracted file with identifier '%s' to "
                "local pathname '%s'", args.file_id, args.local_path)


def _run_delete(config: Config, args: argparse.Namespace) -> None:
    """Delete a file."""
    logger.info("Deleting file with identifier '%s' ...", args.file_id)
    tapest_client.delete_file(
        config, args.file_id, storage_name=args.storage)
    logger.info("Successfully deleted file with identifier '%s'",
                args.file_id)


def _build_query(args: argparse.Namespace) -> dict:
    """Build a metadata query dict from CLI arguments."""
    query = {}
    if getattr(args, 'prefix', None):
        query["prefixes"] = args.prefix
    if getattr(args, 'identifier', None):
        query["identifiers"] = args.identifier
    if getattr(args, 'pending', False):
        query["pending_only"] = True
    if getattr(args, 'errors', False):
        query["errors_only"] = True
    if getattr(args, 'order', None):
        query["order_by"] = args.order
    if getattr(args, 'limit', None) is not None:
        query["limit"] = args.limit
    return query


def _run_query_metadata(config: Config,
                        args: argparse.Namespace) -> None:
    """Query file metadata for a single file or by filter."""
    if args.file_id:
        logger.info("Retrieving metadata for file with identifier "
                    "'%s' ...", args.file_id)
        result = tapest_client.retrieve_file_metadata(
            config, args.file_id, storage_name=args.storage)
    else:
        logger.info("Retrieving metadata for all files matching input "
                    "parameters ...")
        query = _build_query(args)
        result = tapest_client.retrieve_metadata(
            config, query=query, storage_name=args.storage)
    _print_json(result)


def _run_update_metadata(config: Config,
                         args: argparse.Namespace) -> None:
    """Update file metadata."""
    if args.json_input is None and not args.stdin and not args.json_file:
        logger.error("Provide JSON string, -f FILE, - or --stdin")
        sys.exit(2)
    try:
        if args.json_input == "-" or args.stdin:
            raw = sys.stdin.read()
        elif args.json_file:
            with open(args.json_file, "r", encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = args.json_input
        update = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TapestClientError(
            f"update-metadata {args.file_id}: {exc}"
        ) from exc
    logger.info("Updating metadata for file with identifier "
                "'%s' ...", args.file_id)
    result = tapest_client.update_file_metadata(
        config, args.file_id, update,
        storage_name=args.storage)
    _print_json(result)
    logger.info("Successfully updated metadata for file with "
                "identifier '%s'", args.file_id)


def _run_ingest_directory(config: Config,
                          args: argparse.Namespace) -> None:
    """Ingest all files from a directory."""
    logger.info("Ingesting files from directory '%s' ...", args.local_dir)
    results = tapest_client.ingest_files_from_directory(
        config, args.local_dir, skip=args.skip, force=args.force,
        prefix=args.prefix)
    _print_json(results)
    logger.info("Successfully ingested %d file(s) from '%s'",
                len(results), args.local_dir)


def _run_extract_files(config: Config,
                       args: argparse.Namespace) -> None:
    """Extract files to a directory."""
    if not any([args.prefix, args.identifier]):
        logger.error("extract-many requires at least one of -p or -i")
        sys.exit(2)

    query = _build_query(args)
    logger.info("Retrieving metadata for files to extract ...")
    metadata_result = tapest_client.retrieve_metadata(
        config, query=query, storage_name=args.storage)
    metadata_list = metadata_result.get("metadata", [])

    if not metadata_list:
        logger.info("No files match the specified criteria")
        return

    logger.info("Extracting %d file(s) to '%s' ...",
                len(metadata_list), args.local_dir)
    results = tapest_client.extract_files_to_directory(
        config, metadata_list, args.local_dir,
        skip=args.skip, force=args.force,
        storage_name=args.storage)
    _print_json(results)
    logger.info("Successfully extracted %d file(s) to '%s'",
                len(results), args.local_dir)


# -- Entry point -------------------------------------------------------------

def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    _setup_logging(args)

    try:
        if getattr(args, 'needs_config', True):
            config = _load_config(args)
        else:
            config = None
        args.func(config, args)
    except requests.ConnectionError as exc:
        logger.error("Connection failed: %s", exc)
        sys.exit(1)
    except TapestClientError as exc:
        logger.error("Error: %s", exc)
        sys.exit(getattr(exc, 'exit_code', 1))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
