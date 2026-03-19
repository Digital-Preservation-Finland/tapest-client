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

```python
from tapest_client import extract_file, update_file_metadata

config = {
    "ICE_HOST": "https://ice.csc.fi",
    "ICE_TOKEN": "<token>",
}

metadata = extract_file(config, "/path/to/file", "/local/path")
```

## Releasing

Tag the repository with the version matching `setup.cfg`:

```
git tag -a v<major>.<minor>.<patch> -m 'Version <major>.<minor>.<patch>'
git push -u origin v<major>.<minor>.<patch>
```
