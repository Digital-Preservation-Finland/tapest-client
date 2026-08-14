# tapest-client

TapeSt API command-line tool and Python library for interacting with
the TapeSt API service.

Used by `tapest-api` and `tapest-tape-worker`, and can also be used
by external clients.

## Quick start

Install using RPM (preferred):

```
sudo dnf install python3-tapest-client
```

Or install from source:

```
git clone https://github.com/Digital-Preservation-Finland/tapest-client.git
cd tapest-client
pip install .
```

Create a configuration file and fill in your credentials:

```
tapest-client write-config
vi ~/.config/tapest-client/client.conf
```

Verify it works:

```
tapest-client status
```

## Configuration

The CLI looks for configuration in this order:

1. `--config /path/to/config.conf` (explicit)
2. `/etc/tapest-client/client.conf` (system-wide)
3. `~/.config/tapest-client/client.conf` (user-level)

Environment variables (`TAPEST_CLIENT_*`) override file values,
e.g. `TAPEST_CLIENT_TOKEN`.

Config file format (JSON):

```json
{
  "token": "<token>",
  "host": "https://tapest.example.com",
  "storage_account_name": "ida",
  "verify_ssl": true
}
```

### Config fields

| Field                    | Type   | Default | Description                                                                                                            |
|--------------------------|--------|---------|------------------------------------------------------------------------------------------------------------------------|
| `token`                  | string | `""`    | API token for authentication. Replace with your own token.                                                             |
| `host`                   | string | `""`    | TapeSt API host URL.                                                                                                   |
| `storage_account_name`   | string | `""`    | Account name used for storage operations. Only needed for agent accounts; leave empty for storage client accounts.     |
| `max_retry_attempts`     | int    | `10`    | Maximum number of retry attempts for API calls.                                                                        |
| `default_sleep_duration` | int    | `120`   | Sleep duration (seconds) between retries.                                                                              |
| `cleanup_on_fail`        | bool   | `false` | Remove local files on failed operations.                                                                               |
| `verify_ssl`             | bool   | `true`  | Verify the SSL certificate of the host. Do *not* change this except for testing purposes.                              |
| `ca_cert_path`           | string | `""`    | Path to a CA certificate bundle (PEM). When set, overrides the default certifi bundle. Leave empty to use the default. |

## File identifiers

`FILE_ID` values are sent to the API as-is. The API normalizes them
to UTF-8 NFC on receive, so the stored identifier returned by the API
may differ from the value passed in (for example, an identifier
containing NFD `a` + combining diaeresis is returned as NFC
`a-umlaut`).

## Command-line usage

For per-command help, use `tapest-client <command> --help`.

### Global options

| Flag        | Description                    |
|-------------|--------------------------------|
| `--verbose` | Verbose output                 |
| `--debug`   | Debug output (implies verbose) |
| `--config`  | Configuration file             |
| `--host`    | API host URL                   |

The TUS upload uses the chunk size of `tus_chunk_size` in the configuration
file. The default is `16 MiB`. A large chunk can give more speed on a fast
network.

### Ingest (upload)

```
tapest-client ingest PATH [PATH ...] [--identifier ID] [--prefix PFX] [--skip] [--force]
```

`ingest` accepts any mix of file paths and directory paths. Each file's
identifier is derived from the path as given; directories are walked
recursively and each contained file's identifier extends the directory
path with its relative location. `--prefix` is prepended to every derived
identifier.

`--identifier` gives the identifier of one file, thus the identifier and
the local path can be different. It accepts exactly one PATH.

Examples:

```
tapest-client ingest 2024/q1/*.dat 2024/q2/*.dat
tapest-client ingest --prefix acme some_dir extra-file.dat --skip
tapest-client ingest --identifier /path/to/identifier /local/file.dat
```

### Extract (download)

```
tapest-client extract FILE_ID [FILE_ID ...] DEST [--next FILE_ID] [--skip] [--force]
tapest-client extract --prefix PFX DEST [--skip] [--force]
```

The last argument is the destination. It is a file when one identifier is
given and the destination is not a directory that exists. In each other
case it is a directory.

`--next` gives the identifier of the file to read next. The service uses
it for the order of the tape.

Examples:

```
tapest-client extract /path/to/identifier /local/output.dat
tapest-client extract /data/a.dat /data/b.dat /local/dir
tapest-client extract --prefix /data /local/dir --skip
```

### Delete

```
tapest-client delete FILE_ID [FILE_ID ...]
```

### Query metadata

```
tapest-client query-metadata [FILE_ID] [options]
```

| Flag           | Description                                      |
|----------------|--------------------------------------------------|
| `--prefix`     | File identifier prefix (repeatable)              |
| `--regex`      | File identifier regex pattern (repeatable)       |
| `--parent`     | List one directory level under PATH              |
| `--identifier` | File identifier (repeatable)                     |
| `--storage`    | Storage name                                     |
| `--limit`      | Limit number of results                          |
| `--pending`    | Pending files only                               |
| `--errors`     | Files with errors only                           |
| `--order`      | Order by field                                   |

`--regex` matches file identifiers against a regular expression (MongoDB
`$regex` / PCRE-ish syntax), complementary to `--prefix`. Patterns are applied
**unanchored** — use `^`/`$` to anchor. Matching is always scoped to your own
account; a pattern can never reach another account's files. Quote patterns to
keep the shell from expanding metacharacters.

`--parent PATH` lists a single directory level under PATH (like `ls`) instead
of the full recursive subtree (like `ls -R`). The returned `metadata` holds the
files lying directly in the directory, and the immediate child directories are
reported separately as `commonPrefixes` - each ending in `/`, ready to feed back
as the next `--parent` for interactive browsing. An empty path or `/` lists the
root, and a trailing slash is optional.

Examples:

```
tapest-client query-metadata /path/to/identifier
tapest-client query-metadata --pending
tapest-client query-metadata --prefix /prefix --limit 100
tapest-client query-metadata --regex '/2022/.*\.dat$'
tapest-client query-metadata --parent /200182/2022/
```

### Update metadata

```
tapest-client update-metadata FILE_ID [JSON] [--file FILE] [--stdin]
```

JSON can be provided as:
- Inline string: `tapest-client update-metadata /id '{"key": "val"}'`
- From file: `tapest-client update-metadata /id --file update.json`
- From stdin: `cat update.json | tapest-client update-metadata /id --stdin`
- From stdin (POSIX): `cat update.json | tapest-client update-metadata /id -`

### Batch option flags

| Flag      | Description                             |
|-----------|-----------------------------------------|
| `--skip`  | Skip files that already exist and match |
| `--force` | Overwrite files that already exist      |

### Other commands

```
tapest-client status                 # Retrieve service status
tapest-client write-config           # Create default configuration file
```

## Python library usage

### With config file

```python
from tapest_client import get_config, extract_file

config = get_config()  # loads from /etc/tapest-client/client.conf
metadata = extract_file(config, "/path/to/file", "/local/path")
```

### With explicit config

```python
from tapest_client import Config, extract_file

config = Config(
    host="https://tapest-api.csc.fi",
    token="<token>",
)

metadata = extract_file(config, "/path/to/file", "/local/path")
```

## Testing

```
pip install -e .[testing]
pytest
```

### Benchmarks

SHA-256 buffer size benchmark using pytest-benchmark. `--run-slow` is
required; without it the benchmarks are skipped and the run reports
success having measured nothing:

```
pytest tests/benchmark --run-slow --benchmark-enable -v
```

To benchmark on a specific directory (e.g. GlusterFS):

```
BENCHMARK_DIR=/mnt/cache_vol/tapest pytest tests/benchmark --run-slow --benchmark-enable -v
```

## Releasing

Tag the repository with the version matching `setup.cfg`:

```
git tag -a v<major>.<minor>.<patch> -m 'Version <major>.<minor>.<patch>'
git push -u origin v<major>.<minor>.<patch>
```
