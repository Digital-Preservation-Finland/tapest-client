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
e.g. `TAPEST_CLIENT_ICE_TOKEN`.

Config file format:

```ini
[tapest-client]
ice_token = <token>
ice_host = https://tapest.example.com
storage_account_name = ida
verify_ssl = true
```

## Command-line usage

For per-command help, use `tapest-client <command> --help`.

### Global options

| Flag        | Description                    |
|-------------|--------------------------------|
| `--verbose` | Verbose output                 |
| `--debug`   | Debug output (implies verbose) |
| `--config`  | Configuration file             |
| `--host`    | API host URL                   |

### Ingest (upload)

```
tapest-client ingest-one FILE_ID LOCAL_PATH [--storage NAME]
tapest-client ingest-many LOCAL_DIR [--skip] [--force]
```

Examples:

```
tapest-client ingest-one /path/to/identifier /local/file.dat
tapest-client ingest-many /local/dir --skip
```

### Extract (download)

```
tapest-client extract-one FILE_ID LOCAL_PATH [--storage NAME]
tapest-client extract-many LOCAL_DIR --prefix PFX [--skip] [--force] [--storage NAME]
```

Examples:

```
tapest-client extract-one /path/to/identifier /local/output.dat
tapest-client extract-many /local/dir --prefix /data --skip
```

### Delete

```
tapest-client delete FILE_ID [--storage NAME]
```

### Query metadata

```
tapest-client query-metadata [FILE_ID] [options]
```

| Flag           | Description                         |
|----------------|-------------------------------------|
| `--prefix`     | File identifier prefix (repeatable) |
| `--identifier` | File identifier (repeatable)        |
| `--storage`    | Storage name                        |
| `--limit`      | Limit number of results             |
| `--pending`    | Pending files only                  |
| `--errors`     | Files with errors only              |
| `--order`      | Order by field                      |

Examples:

```
tapest-client query-metadata /path/to/identifier
tapest-client query-metadata --pending
tapest-client query-metadata --prefix /prefix --limit 100
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
    ice_host="https://tapest-api.csc.fi",
    ice_token="<token>",
)

metadata = extract_file(config, "/path/to/file", "/local/path")
```

## Testing

```
pip install -e .[testing]
pytest
```

### Benchmarks

SHA-256 buffer size benchmark using pytest-benchmark:

```
pytest benchmarks/test_benchmark_checksum.py --benchmark-enable -v
```

To benchmark on a specific directory (e.g. GlusterFS):

```
BENCHMARK_DIR=/mnt/cache_vol/tapest pytest benchmarks/test_benchmark_checksum.py --benchmark-enable -v
```

## Releasing

Tag the repository with the version matching `setup.cfg`:

```
git tag -a v<major>.<minor>.<patch> -m 'Version <major>.<minor>.<patch>'
git push -u origin v<major>.<minor>.<patch>
```
