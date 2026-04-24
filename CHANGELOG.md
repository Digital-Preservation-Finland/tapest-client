# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.0.6 - 2026-04-24

### Changed
- **Breaking:** Client config file is now JSON instead of INI. Standardized on JSON to match the ansible-deployed `/etc/tapest-client/client.conf`, the server config, and the e2e test fixtures. Existing INI configs must be reformatted to JSON, or deleted and regenerated with `tapest-client write-config`.
- **Breaking:** Renamed config fields `ice_host` -> `host` and `ice_token` -> `token` (and env vars `TAPEST_CLIENT_ICE_HOST` / `TAPEST_CLIENT_ICE_TOKEN` -> `TAPEST_CLIENT_HOST` / `TAPEST_CLIENT_TOKEN`) to match the ansible-deployed key names. The `ice_` prefix was a relic of the pre-rebrand "CSC-ICE" naming.

## 0.0.5 - 2026-04-02

### Added
- Command-line tool (`tapest-client`) with argparse
  - `ingest-one` / `ingest-many` - upload files to the service
  - `extract-one` / `extract-many` - download files from the service
  - `delete` - delete a preserved file and its metadata
  - `query-metadata` / `update-metadata` - query or update file metadata
  - `status` - retrieve service status
  - `write-config` - create default user configuration file
- User-level config at `~/.config/tapest-client/client.conf`
- `update-metadata` accepts JSON string, `-` for stdin, or `--stdin`
- `TapestClientError.exit_code` attribute for distinct return codes (117 for file unavailable)
- RPM packaging: console_scripts entry point, `%{_bindir}/tapest-client`
- SonarQube integration
- `sonar-project.properties`

### Changed
- `.gitlab-ci.yml`: added `tapest.yml` pipeline, `CI_SONAR`, `CI_PYTHON_3`
- `setup.cfg`: added `console_scripts` entry point, updated description
- RPM spec: added `python3-devel`, `pip` to BuildRequires, `%{_bindir}/tapest-client` to `%files`

## 0.0.4 - 2026-03-27

### Added
- `Config` dataclass with attribute access (`config.ice_host`) and type coercion driven by field annotations
- `get_config()` loads from INI file and/or `TAPEST_CLIENT_*` env vars on first call, cached thereafter
- Test plan docstrings for all test modules

### Changed
- **Breaking:** Config is now a dataclass, not a dict. All config keys are lowercase. Client code uses attribute access (`config.ice_token`) instead of dict access (`config["ICE_TOKEN"]`).

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
