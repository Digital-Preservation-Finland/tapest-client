# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.0.3 - 2026-03-20

### Changed
- Moved source from `__init__.py` to `tapest_client/client.py`, `__init__.py` is now public API barrel
- Added hardcoded `__version__ = "0.0.3"` in `__init__.py`
- Consolidated metadata into `setup.cfg`, `pyproject.toml` for build-system only
- Rewritten RPM spec to use pyproject macros (matching tapest-tape-worker pattern)
- Renamed RPM from `python3-tapest-ice-api-client` to `python3-tapest-client`
- Added `Obsoletes`/`Provides` for smooth RPM upgrade
- Removed `GIT_DEPTH` workaround from `.gitlab-ci.yml`

### Removed
- Obsolete SOURCES files (METADATA, RECORD)
- `csc-ice` references

## 0.0.2 - 2026-02-20

### Added
- RPM packaging via gitlab-ci-pipeline
- Hardcoded RPM version to match upstream csc-ice version

## 0.0.1 - 2026-02-14

### Added
- Initial RPM packaging of `ice_api_client.py` as `python3-tapest-ice-api-client`
