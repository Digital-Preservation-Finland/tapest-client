# Changelog

All notable changes to this project are in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed
- **Breaking:** `ingest_file` and `recache_file` use the TUS protocol only. The
  HTTP PUT method is removed. A file that fits in one request goes as a single
  TUS creation-with-upload POST. A larger file goes in chunks. The parameter
  `use_tus` of `recache_file` is removed.

### Added
- The configuration field `tus_creation_with_upload_max_size` gives the
  largest file that goes as one creation-with-upload POST. A larger file goes
  in chunks. The default is 1 GiB. The environment value
  `TAPEST_CLIENT_TUS_CREATION_WITH_UPLOAD_MAX_SIZE` gives the same result.

### Removed
- **Breaking:** The client no longer reads `max_non_tus_upload_bytes` from the
  status. The API does not send it. The upload path no longer depends on a
  limit from the server.

### Fixed
- A chunked upload that ends in a conflict (409) removes its resume state.
  Before, the next run resumed the upload as complete, the server did not
  ingest the file, and the client returned the metadata that the server had.

## 0.0.12 - 2026-09-30

### Added
- `query-metadata` reads the listing one page at a time. `--limit` is the page
  size, `--desc` reverses the order, and `--page-token` takes the
  `nextPageToken` of the previous page. The table view shows the child
  directories and the next page token.
- `extract --no-wait` takes only the files that are cached now and exits.
- `status` reports `tapest_client_version` next to the version of the API.

### Changed
- `extract` of several files or a directory marks the whole set for recall
  with one `POST /recall`, then downloads each file when it is cached. Before,
  it marked one file, waited for it, and then marked the next, so the service
  could not batch the recall. A directory extract follows `nextPageToken`, so
  it takes every file of a large directory.
- **Breaking:** `query-metadata --order` accepts only `identifier` and
  `ingested`, as the API does. `storage`, `stored`, `checked` and `recached`
  are removed.
- An extract of files on tape waits until no file arrives in
  `extract_timeout` seconds (default 7200). Each file that arrives starts the
  wait again, and the wait is at least the time to read the largest file.
  Before, the extract stopped after `max_retry_attempts` requests, which was
  90 seconds with a Retry-After of 10 seconds. `max_retry_attempts` now
  applies only to ingest and recache uploads.
- **Breaking:** The library functions process one file. `ingest_files` and
  `extract_files_to_directory` are removed. Loop over `ingest_file` or
  `extract_file_with_metadata` in your own code. Thus the error handling of
  one file is yours.
- **Breaking:** `extract_file_with_metadata` raises `FileAlreadyExistsError`
  when a file exists at the local pathname. The class is a
  `TapestClientError` and also a builtin `FileExistsError`. A failed
  `ingest_file` raises `TapestClientError` with the message of the server,
  also when a different file has the identifier. The parameter
  `raise_for_status` of `ingest_file` is removed.
- The options `--skip` and `--force` are in the command-line client, not in the
  library. With `--skip` the command `ingest` reads the metadata of the file
  before the upload. Thus a file that is stored already is not sent again.
  Before, the client sent the whole file and the API answered 409 to it. A
  duplicate that appears between the read and the upload gives the error of the
  API.
- **Breaking:** `ingest` has no option `--force`. An identifier that has been
  used is not free again, not even after a delete, so the client cannot replace
  a stored file. A local file that differs from the stored one is an error. Use
  a new identifier for new content. `extract --force` is unchanged: it replaces
  a local file.
- **Breaking:** `ingest PATH...` and `extract FILE_ID... DEST` print one JSON
  record for each file on its own line, when the file is done. Before, they
  printed one JSON array after the whole batch, so a long run gave no output
  until the end, and nothing at all if it stopped in the middle. A command that
  handles one file prints one JSON object as before.
- The results of `ingest` and `extract` are the files that the client sent. A
  file that the client leaves as it is has a line in the log, not a record in
  the output.

### Fixed
- `ingest --identifier ID PATH` uses `--skip`, and `extract FILE_ID DEST` uses
  `--skip` and `--force`. Before, these options gave no result for one named
  file.
- A TUS chunk that the server refuses at its offset is reported as a refused
  offset. Before, every 409 was reported as a taken identifier.
- The table view of `query-metadata` prints `storage_metadata` as it is.
  Before, it was encoded to JSON a second time.

## 0.0.11 - 2026-08-18

### Changed
- **Breaking:** Each command accepts one file or many files. Thus the commands
  `ingest-one`, `ingest-many`, `extract-one` and `extract-many` are removed.
  Use `ingest PATH...` and `extract FILE_ID... DEST`. The command `delete`
  accepts more than one identifier. The old names give an error.
- `ingest` selects the upload path by the size of the file. A file that is
  larger than the limit of the API goes to the TUS endpoint. The client reads
  the limit one time for each host from the `max_non_tus_upload_bytes` field of
  the status. If the status does not have the field, the client uses 1 GiB.
  Thus you no longer select the path.
- `extract` uses the method of the command `cp`. The last argument is the
  destination. It is a file when you give one identifier and the destination is
  not a directory that exists. In each other case it is a directory.
- The next identifier for the order of the tape is now the option `--next`.

### Added
- `ingest --identifier ID PATH` gives the identifier of one file. Use it when
  the identifier and the local path are different.
- The configuration field `tus_chunk_size` gives the size of one chunk of a TUS
  upload, for example `"16 MiB"` or `"500 MiB"`. The default is 16 MiB. The
  environment value `TAPEST_CLIENT_TUS_CHUNK_SIZE` gives the same result.

### Removed
- **Breaking:** The command `tus-ingest-one`. The command `ingest` selects the
  TUS path automatically.

## 0.0.10 - 2026-08-10

### Added
- `query-metadata --parent PATH` lists one level below PATH. This is the same
  operation as `ls`. Before, the client listed all the levels below a prefix,
  which is the same operation as `ls -R`. The `metadata` field of the response
  has the files that are directly in the directory. The `commonPrefixes` field
  has the child directories. Each child directory ends with `/`, thus you can
  use it as the next `--parent` value. An empty path or `/` lists the root. Use
  this option to examine a large account one level at a time. The API must
  accept the `parent` query field.
- `query-metadata --regex PATTERN` compares the identifiers with a regular
  expression. You can give the option more than one time, and together with
  `--prefix`. The pattern is not anchored, thus use `^` and `$` to anchor it.
  The client compares only the files of your own account. The API must accept
  the `regexes` query field.

### Changed
- An ingest of a file that is already stored with the same content is now a
  success. Before, the client gave an error. `ingest_file` returns the metadata
  of the stored file. Thus a repeated ingest is safe after a partial failure.
  If the content is different, the client gives an error. The API must send the
  `identical_duplicate` marker with the answer 409. An older API sends 409
  without the marker, and the client then gives an error.

### Fixed
- The benchmark command in the README used a path that does not exist, and it
  did not have `--run-slow`. Thus pytest skipped all 12 benchmarks and showed a
  success.

## 0.0.9 - 2026-06-23

### Changed
- **Breaking:** The protocol headers `X-ICE-*` are now `X-TAPEST-*`. The client
  and the API must change together. An API that expects the old names is not
  compatible.

### Fixed
- `ingest-many .` no longer gives the error `422 Invalid value for query
  parameter identifier`. A path of `.` or a path with a final slash made an
  identifier with a `/./` part. The function `normalize_identifier` now does
  all of the normalization. It collapses `.` and `//`, resolves `..`, and keeps
  a final slash for a prefix query.
- A TUS upload keeps the checksum, the created time and the modified time. The
  client sends these values in `Upload-Metadata` at the start of the upload.
  Before, the client sent them in headers, which the chunked upload did not
  keep.

## 0.0.8 - 2026-05-27

### Added
- TUS resumable upload with the command `tus-ingest-one`.

### Changed
- `--force` on ingest and extract does nothing when the checksum of the file is
  the same as the checksum of the copy on the service. Before, it replaced the
  file.

### Fixed
- `tus_ingest_file` obeys `config.verify_ssl` and `ca_cert_path`. Before, a TUS
  upload always verified TLS. Thus an upload to a private CA endpoint failed.

## 0.0.7 - 2026-05-18

### Added
- The parameter `account_name` on each library function that sends the account
  header. If you do not give it, the client uses `config.storage_account_name`.
  Thus one configuration can serve more than one account.

### Changed
- The client normalizes the leading slash of each identifier. Before, it did
  this only in `ingest-one` and `ingest-many`. This applies to the URLs of
  `/file` and `/metadata`, the next-file header, and the `prefixes` and
  `identifiers` fields of a metadata query.

## 0.0.6 - 2026-04-29

### Added
- `ingest-many` accepts files and directories together in one command. The
  identifier comes from the path as given. The client walks a directory and
  adds the relative location to the identifier. `--prefix` goes before each
  identifier.
- `ingest-one` adds a leading `/` to the identifier if you do not give one.
  Thus the two commands make the same form of identifier.
- The dependency `certifi`, thus TLS verification uses the CA store of the
  system.

### Changed
- **Breaking:** The configuration file is now JSON, not INI. This is the same
  format as the file `/etc/tapest-client/client.conf` of the deployment. Write
  the file again, or make a new one with `tapest-client write-config`.
- **Breaking:** The fields `ice_host` and `ice_token` are now `host` and
  `token`. The environment values change in the same way.
- **Breaking:** The function `ingest_files_from_directory` is removed. Use
  `ingest_files` with your own list of identifier and path pairs.
- The client gives one line for a connection error. Before, it gave a Python
  traceback.

### Documentation
- The API normalizes each identifier to UTF-8 NFC. The client does not change
  the identifier. Thus the `identifier` field of the metadata can be different
  from the value that you sent.

## 0.0.5 - 2026-04-02

### Added
- The command-line tool `tapest-client`:
  - `ingest-one` and `ingest-many` send files to the service
  - `extract-one` and `extract-many` get files from the service
  - `delete` removes a file and its metadata
  - `query-metadata` and `update-metadata` read or change metadata
  - `status` gives the status of the service
  - `write-config` makes a default configuration file
- A user configuration at `~/.config/tapest-client/client.conf`.
- `update-metadata` accepts a JSON string, `-` for stdin, or `--stdin`.
- The attribute `TapestClientError.exit_code`. The code 117 shows that the file
  is not available.
- RPM packaging and SonarQube.

## 0.0.4 - 2026-03-27

### Added
- The class `Config` with attribute access and type conversion.
- `get_config()` reads an INI file and the `TAPEST_CLIENT_*` environment
  values. It keeps the result.

### Changed
- **Breaking:** The configuration is a dataclass, not a dict. Each key is in
  lower case. Use `config.ice_token`, not `config["ICE_TOKEN"]`.

## 0.0.3 - 2026-03-20

### Changed
- The source moved from `__init__.py` to `tapest_client/client.py`.
  `__init__.py` now gives only the public API.
- The metadata is in `setup.cfg`. `pyproject.toml` has only the build system.
- The RPM `python3-tapest-ice-api-client` is now `python3-tapest-client`. The
  spec has `Obsoletes` and `Provides` for the upgrade.

### Removed
- The obsolete SOURCES files and the `csc-ice` references.

## 0.0.2 - 2026-02-20

### Added
- RPM packaging with the gitlab-ci pipeline.

## 0.0.1 - 2026-02-14

### Added
- The first RPM of `ice_api_client.py`.
