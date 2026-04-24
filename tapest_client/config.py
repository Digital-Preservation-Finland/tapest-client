# ----------------------------------------------------------------------
# This file is part of TapeSt - Tape Storage
# The CSC Digital Preservation Tape Storage Service
#
# Copyright (C) 2025 CSC - IT Center for Science Ltd.
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
"""Configuration loading for tapest-client.

Loads settings from a JSON config file and/or environment variables.
Environment variables override file values.

All keys are lowercase. Environment variables use the uppercase
``TAPEST_CLIENT_`` prefix (e.g. ``TAPEST_CLIENT_TOKEN``).

Example config file (``/etc/tapest-client/client.conf``)::

    {
        "token": "<token>",
        "host": "https://tapest-api.csc.fi",
        "storage_account_name": "ida",
        "max_retry_attempts": 10,
        "default_sleep_duration": 120,
        "cleanup_on_fail": false,
        "verify_ssl": true,
        "ca_cert_path": ""
    }

Usage::

    from tapest_client.config import get_config

    config = get_config()    # loads on first call, cached thereafter
    print(config.host)
"""
import dataclasses
import json
import os
from typing import Any


CONFIG_FILE = "/etc/tapest-client/client.conf"

_BOOL_STRINGS = {"true", "yes", "1"}


def _parse_bool(value: str) -> bool:
    """Parse a boolean value from string."""
    return value.lower() in _BOOL_STRINGS


def _coerce(field_type: type, value: Any) -> Any:
    """Coerce a value to the field's annotated type."""
    if field_type is int:
        return int(value)
    if field_type is bool:
        return _parse_bool(value) if isinstance(value, str) else bool(value)
    return value


@dataclasses.dataclass
class Config:
    """TapeSt client configuration."""
    token: str = ""
    host: str = ""
    storage_account_name: str = ""
    max_retry_attempts: int = 10
    default_sleep_duration: int = 120
    cleanup_on_fail: bool = False
    verify_ssl: bool = True
    ca_cert_path: str = ""

    def read(self, config_file: str = CONFIG_FILE) -> None:
        """Load configuration from file and environment variables.

        Values are loaded in order of increasing priority:

        1. JSON config file (flat object with lowercase keys)
        2. Environment variables (``TAPEST_CLIENT_<KEY>``, e.g.
           ``TAPEST_CLIENT_HOST``)

        Args:
            config_file: Path to JSON config file.
        """
        fields = {f.name: f for f in dataclasses.fields(self)}

        # 1. Read from JSON config file
        if os.path.isfile(config_file):
            with open(config_file, encoding="utf-8") as fh:
                data = json.load(fh)
            for name, field in fields.items():
                if name in data:
                    setattr(self, name, _coerce(field.type, data[name]))

        # 2. Override with environment variables (uppercase with prefix)
        for name, field in fields.items():
            env_key = f"TAPEST_CLIENT_{name.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                setattr(self, name, _coerce(field.type, env_val))


_config = None


def get_config() -> Config:
    """Load and return configuration (lazy, cached on first call).

    On first call, creates a Config instance, loads from the default
    config file and environment variables, and caches the result.
    Subsequent calls return the cached instance.
    """
    global _config
    if _config is None:
        _config = Config()
        _config.read()
    return _config
