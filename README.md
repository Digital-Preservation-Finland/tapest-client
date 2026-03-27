# tapest-client

TapeSt API client library. Provides functions for interacting with the
TapeSt API service.

Used by `tapest-api` and `tapest-tape-worker`, and can also be used
by external clients.

## Installation

```
pip install .
```

Development install:

```
pip install -e .
```

## Usage

### With config file

```python
from tapest_client import get_config, extract_file

config = get_config()  # loads on first call from /etc/tapest-client/client.conf
metadata = extract_file(config, "/path/to/file", "/local/path")
```

Config file example (`/etc/tapest-client/client.conf`):

```ini
[tapest-client]
ice_token = <token>
ice_host = https://tapest-api.csc.fi
storage_account_name = ida
verify_ssl = true
```

Environment variables (`TAPEST_CLIENT_*`) override file values.

### With explicit config

```python
from tapest_client import Config, extract_file

config = Config(
    ice_host="https://ice.csc.fi",
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
