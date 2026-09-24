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

    ingest             Ingest (upload) files or directory trees
    extract            Extract (download) files from the service
    delete             Delete files and their metadata from the service
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
import time

import requests
import tapest_client
from tapest_client import (
    FileAlreadyExistsError,
    TapestClientError,
    normalize_identifier,
)
from tapest_client.config import CONFIG_FILE, Config

logger = logging.getLogger("tapest-client")

DEFAULT_CONFIG = json.dumps(dataclasses.asdict(Config()), indent=2) + "\n"

# Sort fields the API accepts. Both are document-root, index-backed fields,
# so every listing can be paged. Keep in sync with the API's OrderByValue.
METADATA_ORDER_CHOICES = [
    "identifier",
    "ingested",
]


# -- Parser construction -----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="tapest-client",
        description="TapeSt command-line client",
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog, max_help_position=40
        ),
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="verbose output"
    )
    verbosity.add_argument(
        "-V",
        "--debug",
        action="store_true",
        help="debug output (implies verbose)",
    )

    parser.add_argument("--config", default=None, help="configuration file")
    parser.add_argument("--host", default=None, help="API host URL")
    parser.add_argument(
        "--ca-cert",
        default=None,
        metavar="PATH",
        help="CA certificate bundle (PEM) for SSL verification",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ingest
    sub = subparsers.add_parser(
        "ingest",
        help="Ingest (upload) one or more files and/or directory trees",
    )
    sub.add_argument(
        "paths",
        metavar="PATH",
        nargs="+",
        help="files and/or directories to ingest; identifiers are derived "
        "from the paths as given (directories are walked recursively)",
    )
    sub.add_argument(
        "--identifier",
        default=None,
        help="identifier for the file; only with exactly one PATH",
    )
    sub.add_argument(
        "--prefix",
        default=None,
        help="prepend this prefix to every derived identifier",
    )
    sub.add_argument(
        "--skip",
        action="store_true",
        help="skip files that are already stored and match",
    )
    sub.set_defaults(func=_run_ingest)

    # extract
    sub = subparsers.add_parser(
        "extract",
        help="Extract (download) files from the service",
    )
    sub.add_argument(
        "args",
        metavar="FILE_ID... DEST",
        nargs="+",
        help="one or more file identifiers, then the destination; the "
        "destination is a file when one identifier is given and it is not "
        "an existing directory, else a directory",
    )
    sub.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="file identifier prefix (repeatable); with a prefix the only "
        "positional argument is the destination directory",
    )
    sub.add_argument(
        "--force",
        action="store_true",
        help="overwrite local files that differ from the record (identical "
        "files are always left as they are)",
    )
    sub.add_argument(
        "--wait",
        choices=["poll", "none", "forever"],
        default="poll",
        help="how long to wait for files still on tape: 'poll' gives up "
        "when no file arrives in extract_timeout seconds (default); 'none' "
        "takes only what is cached now and exits; 'forever' polls until the "
        "whole set arrives",
    )
    sub.set_defaults(func=_run_extract)

    # delete
    sub = subparsers.add_parser(
        "delete", help="Delete files and their metadata from the service"
    )
    sub.add_argument("file_id", metavar="FILE_ID", nargs="+")
    sub.set_defaults(func=_run_delete)

    # query-metadata
    sub = subparsers.add_parser(
        "query-metadata", help="Query metadata for one or many files"
    )
    sub.add_argument("file_id", metavar="FILE_ID", nargs="?", default=None)
    sub.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="file identifier prefix (repeatable)",
    )
    sub.add_argument(
        "--identifier",
        action="append",
        default=None,
        help="file identifier (repeatable)",
    )
    sub.add_argument("--storage", default=None)
    sub.add_argument(
        "--pending", action="store_true", help="pending files only"
    )
    sub.add_argument(
        "--errors", action="store_true", help="files with errors only"
    )
    sub.add_argument(
        "--regex",
        action="append",
        default=None,
        help="file identifier regex pattern (repeatable), "
        "complementary to --prefix",
    )
    sub.add_argument(
        "--parent",
        default=None,
        metavar="PATH",
        help="list one directory level under PATH (immediate files plus "
        "child directories) instead of the full recursive subtree",
    )
    sub.add_argument("--order", choices=METADATA_ORDER_CHOICES, default=None)
    sub.add_argument(
        "--desc",
        action="store_true",
        help="sort descending (use with --order)",
    )
    sub.add_argument(
        "--limit",
        type=int,
        default=None,
        help="page size (server caps a page at 1000 files)",
    )
    sub.add_argument(
        "--page-token",
        default=None,
        help="resume a listing from the nextPageToken of a previous page",
    )
    sub.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="output format; 'table' renders a per-file copies summary",
    )
    sub.set_defaults(func=_run_query_metadata)

    # update-metadata
    sub = subparsers.add_parser(
        "update-metadata", help="Update metadata for a single file"
    )
    sub.add_argument("file_id", metavar="FILE_ID")
    sub.add_argument(
        "json_input",
        metavar="JSON",
        nargs="?",
        default=None,
        help="JSON string, or - for stdin",
    )
    sub.add_argument(
        "--file", dest="json_file", default=None, help="read JSON from file"
    )
    sub.add_argument(
        "--stdin", action="store_true", help="read JSON from stdin"
    )
    sub.add_argument("--storage", default=None)
    sub.set_defaults(func=_run_update_metadata)

    # status
    sub = subparsers.add_parser("status", help="Retrieve service status")
    sub.set_defaults(func=_run_status)

    # write-config
    sub = subparsers.add_parser(
        "write-config", help="Write default configuration file"
    )
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
            f"{config_file} or {USER_CONFIG_FILE}"
        )
    if not config.token:
        raise TapestClientError(
            "No API token configured. Set token in "
            f"{config_file} or TAPEST_CLIENT_TOKEN env var"
        )
    logger.debug(
        "Config: host=%s account=%s",
        config.host,
        config.storage_account_name,
    )
    return config


def _print_json(data: object) -> None:
    """Print data as formatted JSON to stdout."""
    print(json.dumps(data, indent=2))


def _print_jsonl(data: object) -> None:
    """Print one record as a single JSON line, for a batch in progress."""
    print(json.dumps(data), flush=True)


# Per-copy timestamp fields shown (as presence marks) in the table view.
_COPY_MARKS = ("stored", "checked", "queued", "required", "extracted")


def _print_metadata_item_header(item: dict) -> None:
    """Print the identifier/size/flags header line for one file item."""
    header = f"{item.get('identifier', '?')}  ({item.get('size', '')} B)"
    if item.get("ingested"):
        header += f"  ingested={item['ingested']}"
    if item.get("recached"):
        header += f"  recached={item['recached']}"
    print(header)


def _print_metadata_copy_line(name: str, copy: dict, name_w: int) -> None:
    """Print a single storage-copy row of the per-file copies table."""
    marks = "  ".join(
        f"{'✓' if copy.get(m) else '·':>4}" for m in _COPY_MARKS
    )
    errs = len(copy.get("errors") or [])
    sm = copy.get("storage_metadata")
    sm_str = f"  {sm}" if sm else ""
    print(f"  {name:<{name_w}}  {marks}  {errs:>4}{sm_str}")


def _print_metadata_copies(copies: dict) -> None:
    """Print the copies table for one file item, or a placeholder."""
    if not copies:
        print("  (no copies)")
        return
    name_w = max(len("storage"), *(len(n) for n in copies))
    cols = "  ".join(f"{m[:4]:>4}" for m in _COPY_MARKS)
    print(f"  {'storage':<{name_w}}  {cols}  errs")
    for name, copy in sorted(copies.items()):
        _print_metadata_copy_line(name, copy, name_w)


def _print_metadata_footer(result: dict, items: list) -> None:
    """Print the trailing files/pending/errors summary line, if present, plus
    the next-page token when there is one."""
    if "files" in result:
        footer = (
            f"\n{result.get('files', len(items))} files, "
            f"{result.get('filesPending', 0)} pending, "
            f"{result.get('filesWithErrors', 0)} with errors"
        )
        if result.get("nextPageToken"):
            footer += f"\nnext page token: {result['nextPageToken']}"
        print(footer)


def _print_metadata_table(result: dict) -> None:
    """Render a per-file, per-copy summary table to stdout.

    Renders child directories of a one-level listing too. Falls back to JSON
    only when the shape has neither a metadata list nor common prefixes.
    Accepts either a query envelope or a single file document.
    """
    if "metadata" in result or "commonPrefixes" in result:
        items = result.get("metadata")
        prefixes = result.get("commonPrefixes")
        if items is None and not prefixes:
            _print_json(result)
            return
        items = items or []
    else:
        items = [result]
        prefixes = None
    if not items and not prefixes:
        print("0 files")
        return

    for item in items:
        _print_metadata_item_header(item)
        _print_metadata_copies(item.get("copies") or {})

    for prefix in prefixes or []:
        print(f"{prefix}  (directory)")

    _print_metadata_footer(result, items)


# -- Subcommand handlers -----------------------------------------------------


def _run_write_config(
    _config: Config | None, args: argparse.Namespace
) -> None:
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
    """Retrieve service status.

    The client version is reported alongside the service response, so that a
    single command answers which client is talking to which service.
    """
    logger.info("Retrieving service status ...")
    result = tapest_client.retrieve_status(config)
    _print_json(
        {"tapest_client_version": tapest_client.__version__, **result}
    )
    logger.info("Service status retrieved successfully")


def _extract_targets(args: argparse.Namespace) -> tuple[list[str], str]:
    """Split the positional arguments into identifiers and a destination."""
    if args.prefix:
        if len(args.args) != 1:
            raise TapestClientError(
                "with --prefix give only the destination directory"
            )
        return [], args.args[0]
    if len(args.args) < 2:
        raise TapestClientError(
            "give one or more FILE_ID and then the destination"
        )
    return args.args[:-1], args.args[-1]


def _extract_to_file(
    config: Config, identifier: str, dest: str, args: argparse.Namespace
) -> None:
    """Extract one file to a target file name.

    Skip an existing identical file; --force overwrites one that differs, and
    a file that differs without --force is an error. --wait bounds the wait
    for a file still on tape, the same as for a directory.
    """
    logger.info(
        "Extracting file with identifier '%s' to local pathname '%s' ...",
        identifier,
        dest,
    )
    file_metadata = tapest_client.retrieve_file_metadata(config, identifier)
    path = Path(dest)
    if not _should_extract(file_metadata, path, args.force):
        return
    _, unavailable = _collect_files(config, [(file_metadata, path)], args.wait)
    if unavailable:
        if args.wait == "poll":
            limit = _poll_limit(config, [(file_metadata, path)])
            raise TapestClientError(
                f"File {identifier} unavailable after {limit} s",
                exit_code=117,
            )
        logger.warning(
            "File %s not yet available; re-run extract to fetch it",
            identifier,
        )
        return
    logger.info(
        "Successfully extracted file with identifier '%s' to "
        "local pathname '%s'",
        identifier,
        dest,
    )


def _retrieve_all_metadata(config: Config, query: dict) -> list[dict]:
    """Return the metadata for every file that matches the query.

    A metadata query returns one page of at most MAX_LIMIT rows plus a
    nextPageToken. Follow the token so a broad extract works on the whole
    matched set, not only the first page.
    """
    query = dict(query)
    metadata_list = []
    seen_tokens = set()
    while True:
        result = tapest_client.retrieve_metadata(config, query=query)
        metadata_list.extend(result.get("metadata", []))
        token = result.get("nextPageToken")
        # Stop on a repeated token so a service that echoes one cannot hold
        # the loop open forever.
        if not token or token in seen_tokens:
            return metadata_list
        seen_tokens.add(token)
        query["page_token"] = token


def _extract_to_directory(
    config: Config,
    identifiers: list[str],
    dest: str,
    args: argparse.Namespace,
) -> None:
    """Extract every file that matches the query into a directory.

    Mark the whole matched set required in one call, then read each file as
    it becomes available. Already-present identical files are left as they
    are. A file that never arrives does not stop the others. ``--wait``
    decides how long to wait for files on tape.
    """
    args.identifier = [
        normalize_identifier(i) for i in identifiers
    ] or None
    query = _build_query(args)

    root = Path(dest)
    if not root.is_dir():
        raise TapestClientError(f"Directory does not exist: {dest}")

    logger.info("Marking matching files required ...")
    summary = tapest_client.mark_required(config, query)
    if not summary.get("matched"):
        logger.info("No files match the specified criteria")
        return
    logger.info(
        "Matched %d file(s), %d bytes; marked %d for recall",
        summary.get("matched", 0),
        summary.get("totalSize", 0),
        summary.get("marked", 0),
    )

    logger.info("Retrieving metadata for files to extract ...")
    metadata_list = _retrieve_all_metadata(config, query)
    logger.info("Extracting %d file(s) to '%s' ...", len(metadata_list), dest)

    pending = []
    skipped_count = 0
    conflicts = []
    for file_metadata in metadata_list:
        path = root / file_metadata["identifier"].lstrip("/")
        # Skip identical files, --force overwrites. A differing file is a
        # conflict: leave it and go on, so one edit does not stop the batch.
        try:
            if _should_extract(file_metadata, path, args.force):
                pending.append((file_metadata, path))
            else:
                skipped_count += 1
        except FileAlreadyExistsError:
            conflicts.append(str(path))

    extracted_count, unavailable = _collect_files(config, pending, args.wait)

    if skipped_count:
        logger.warning(
            "Skipped %d file(s) that are already in '%s'", skipped_count, dest
        )
    logger.info(
        "Successfully extracted %d file(s) to '%s'", extracted_count, dest
    )
    if conflicts:
        logger.warning(
            "%d file(s) differ from the record and were left as is; re-run "
            "with --force to overwrite: %s",
            len(conflicts),
            ", ".join(conflicts),
        )
    if unavailable:
        if args.wait == "poll":
            raise TapestClientError(
                f"{len(unavailable)} file(s) unavailable: no file arrived "
                f"in {_poll_limit(config, pending)} s"
                + (
                    f" ({len(conflicts)} more differ from the record)"
                    if conflicts
                    else ""
                )
                + f": {', '.join(unavailable)}",
                exit_code=117,
            )
        logger.warning(
            "%d file(s) not yet available; re-run extract to fetch them: %s",
            len(unavailable),
            ", ".join(unavailable),
        )
    if conflicts:
        raise TapestClientError(
            f"{len(conflicts)} file(s) differ from the record; re-run with "
            "--force to overwrite",
            exit_code=1,
        )


def _poll_limit(config: Config, pending: list[tuple[dict, Path]]) -> int:
    """Return the seconds 'poll' waits for the next of the pending files."""
    return tapest_client.extract_time_limit(config, [fm for fm, _ in pending])


def _collect_files(
    config: Config,
    pending: list[tuple[dict, Path]],
    wait: str = "poll",
) -> tuple[int, list[str]]:
    """Poll the service for the bytes, taking each file as it is ready.

    The files are already marked required by the up-front bulk call.
    ``wait`` bounds the polling: 'poll' gives up when no file arrives in
    :func:`_poll_limit` seconds, 'none' makes one pass, 'forever' polls until
    nothing is pending. Return the count extracted and the identifiers that
    never came.
    """
    default_duration = max(1, config.default_sleep_duration)
    limit = _poll_limit(config, pending)
    deadline = time.monotonic() + limit
    extracted_count = 0

    while pending:
        still_pending = []
        sleep_seconds = 0
        for file_metadata, path in pending:
            ready, retry_after = tapest_client.attempt_extract(
                config, file_metadata, str(path)
            )
            if ready:
                _print_jsonl(file_metadata)
                extracted_count += 1
                deadline = time.monotonic() + limit
            else:
                still_pending.append((file_metadata, path))
                sleep_seconds = max(sleep_seconds, retry_after)
        pending = still_pending
        if not pending or wait == "none":
            break
        sleep_seconds = sleep_seconds or default_duration
        if wait == "poll":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_seconds = min(sleep_seconds, remaining)
        time.sleep(sleep_seconds)

    return extracted_count, [fm["identifier"] for fm, _ in pending]


def _run_extract(config: Config, args: argparse.Namespace) -> None:
    """Extract files from the service.

    The last positional argument is the destination. It is a target file
    when exactly one identifier is given and it is not an existing
    directory, otherwise it is a target directory. With --prefix the
    destination directory is the only positional argument.
    """
    identifiers, dest = _extract_targets(args)
    if len(identifiers) == 1 and not Path(dest).is_dir():
        _extract_to_file(
            config, normalize_identifier(identifiers[0]), dest, args
        )
        return
    _extract_to_directory(config, identifiers, dest, args)


def _should_extract(
    file_metadata: dict, path: Path, force: bool
) -> bool:
    """Decide whether a file must be fetched.

    An existing identical file is left as is (returns False). ``force``
    overwrites one that differs; without it, a differing file is an error.

    :raises FileAlreadyExistsError: If the path holds a different file and
        ``force`` is not set.
    """
    if not path.exists():
        return True
    if tapest_client.is_same_file(
        str(path), file_metadata["size"], file_metadata["checksum"]
    ):
        logger.info(
            "Skipping file at local pathname '%s': already extracted", path
        )
        return False
    if not force:
        raise FileAlreadyExistsError(f"File already exists at {path}")
    path.unlink()
    return True


def _run_delete(config: Config, args: argparse.Namespace) -> None:
    """Delete one or more files."""
    for file_id in args.file_id:
        identifier = normalize_identifier(file_id)
        logger.info("Deleting file with identifier '%s' ...", identifier)
        tapest_client.delete_file(config, identifier)
        logger.info(
            "Successfully deleted file with identifier '%s'", identifier
        )


def _build_query(args: argparse.Namespace) -> dict:
    """Build a metadata query dict from CLI arguments."""
    query = {}
    if getattr(args, "prefix", None):
        query["prefixes"] = args.prefix
    if getattr(args, "regex", None):
        query["regexes"] = args.regex
    if getattr(args, "identifier", None):
        query["identifiers"] = args.identifier
    if getattr(args, "parent", None) is not None:
        query["parent"] = args.parent
    if getattr(args, "pending", False):
        query["pending_only"] = True
    if getattr(args, "errors", False):
        query["errors_only"] = True
    if getattr(args, "order", None):
        query["order_by"] = args.order
    if getattr(args, "desc", False):
        query["order_dir"] = "desc"
    if getattr(args, "limit", None) is not None:
        query["limit"] = args.limit
    if getattr(args, "page_token", None) is not None:
        query["page_token"] = args.page_token
    return query


def _run_query_metadata(config: Config, args: argparse.Namespace) -> None:
    """Query file metadata for a single file or by filter."""
    if args.file_id:
        logger.info(
            "Retrieving metadata for file with identifier '%s' ...",
            args.file_id,
        )
        result = tapest_client.retrieve_file_metadata(
            config, args.file_id
        )
    else:
        logger.info(
            "Retrieving metadata for all files matching input "
            "parameters ..."
        )
        query = _build_query(args)
        result = tapest_client.retrieve_metadata(
            config, query=query, storage_name=args.storage
        )
    if getattr(args, "format", "json") == "table":
        _print_metadata_table(result)
    else:
        _print_json(result)


def _run_update_metadata(config: Config, args: argparse.Namespace) -> None:
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
    logger.info(
        "Updating metadata for file with identifier '%s' ...", args.file_id
    )
    result = tapest_client.update_file_metadata(
        config, args.file_id, update, storage_name=args.storage
    )
    _print_json(result)
    logger.info(
        "Successfully updated metadata for file with identifier '%s'",
        args.file_id,
    )


def _ingest_named_file(config: Config, args: argparse.Namespace) -> None:
    """Ingest the one PATH under the identifier given by --identifier."""
    if len(args.paths) != 1:
        raise TapestClientError("--identifier accepts exactly one PATH")
    local_path = args.paths[0]
    if not Path(local_path).is_file():
        raise TapestClientError(f"Path is not a file: {local_path}")
    identifier = normalize_identifier(args.identifier)
    logger.info(
        "Ingesting file at local pathname '%s' with identifier '%s' ...",
        local_path,
        identifier,
    )
    result = _ingest_one(
        config, identifier, local_path, args.skip
    )
    if result is None:
        return
    _print_json(result)
    logger.info(
        "Successfully ingested file with identifier '%s' from "
        "local pathname '%s'",
        identifier,
        local_path,
    )


def _collect_ingest_files(
    paths: list[str], prefix: str
) -> list[tuple[str, str]]:
    """Pair each identifier with its local pathname.

    A file path gives one pair, a directory path is walked recursively.

    :raises TapestClientError: If a path does not exist.
    """
    files: list[tuple[str, str]] = []
    for cli_path in paths:
        path = Path(cli_path)
        base = path.as_posix()
        # Identifiers are built by naive join and canonicalized centrally,
        # so '.', '..', '//' and stray slashes all collapse to '/<relpath>'.
        if path.is_file():
            files.append(
                (normalize_identifier(f"{prefix}/{base}"), str(path))
            )
        elif path.is_dir():
            for sub in sorted(path.rglob("*")):
                if sub.is_file():
                    rel = sub.relative_to(path).as_posix()
                    identifier = normalize_identifier(
                        f"{prefix}/{base}/{rel}"
                    )
                    files.append((identifier, str(sub)))
        else:
            raise TapestClientError(f"Path does not exist: {cli_path}")
    return files


def _run_ingest(config: Config, args: argparse.Namespace) -> None:
    """Ingest files and directory trees.

    Each identifier is built from the path as given: a file path gives one
    identifier, a directory path is walked recursively and each file below
    it keeps its location in the tree. --prefix is prepended to every
    derived identifier. --identifier names one file instead, so its
    identifier and its local path can differ.
    """
    if args.identifier is not None:
        _ingest_named_file(config, args)
        return

    files = _collect_ingest_files(args.paths, args.prefix or "")
    logger.info("Ingesting %d file(s) ...", len(files))
    ingested_count = 0
    skipped_count = 0
    for identifier, local_path in files:
        ingested = _ingest_one(config, identifier, local_path, args.skip)
        if ingested is None:
            skipped_count += 1
            continue
        _print_jsonl(ingested)
        ingested_count += 1
    if skipped_count:
        logger.warning(
            "Skipped %d file(s) that are already stored", skipped_count
        )
    logger.info("Successfully ingested %d file(s)", ingested_count)


def _ingest_one(
    config: Config,
    identifier: str,
    local_path: str,
    skip: bool,
) -> dict | None:
    """Ingest one file. With --skip, do not send a file that is already
    stored with the same content.

    With --skip the client reads the stored metadata first. A file already
    stored with the same checksum is not sent again. A file stored with a
    different checksum is an error: a re-ingest does not overwrite live
    content. Delete the identifier first to reuse it. Without --skip the
    upload goes straight out and the service answers 409 for a taken
    identifier.

    Returns None for a file that is already stored.

    :raises FileAlreadyExistsError: If the identifier is stored with
        different content.
    """
    checksum = None
    if skip:
        checksum = tapest_client.generate_checksum(local_path)
        existing = tapest_client.retrieve_file_metadata(
            config, identifier, missing_ok=True
        )
        if existing is not None:
            if checksum != existing.get("checksum"):
                raise FileAlreadyExistsError(
                    f"A different file is already stored with identifier "
                    f"{identifier}; delete it first to reuse the identifier"
                )
            logger.info(
                "Skipping file with identifier '%s': already stored",
                identifier,
            )
            return None
    return tapest_client.ingest_file(
        config, identifier, local_path, checksum=checksum
    )


def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    _setup_logging(args)

    try:
        if getattr(args, "needs_config", True):
            config = _load_config(args)
        else:
            config = None
        args.func(config, args)
    except requests.ConnectionError as exc:
        logger.error("Connection failed: %s", exc)
        sys.exit(1)
    except TapestClientError as exc:
        logger.error("Error: %s", exc)
        sys.exit(getattr(exc, "exit_code", 1))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
