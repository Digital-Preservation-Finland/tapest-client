"""Pytest configuration and shared fixtures."""

import dataclasses
import itertools
import types

import pytest

from tapest_client.config import Config


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="Run slow tests (benchmarks, etc.)")


def pytest_runtest_setup(item):
    """Skip slow tests unless --run-slow is given."""
    if "slow" in item.keywords and not item.config.getoption("--run-slow"):
        pytest.skip("slow tests require --run-slow flag")


# -- Shared test defaults ----------------------------------------------------

CLIENT_CONFIG_DEFAULT = Config(
    token="tok123",
    host="https://ice.example.com",
    storage_account_name="testaccount",
    max_retry_attempts=2,
    default_sleep_duration=0,
    verify_ssl=True,
)


# -- Shared fixtures ---------------------------------------------------------

CONFIG_PARAMS = {
    "host": ["https://ice.local", "https://tapest.local"],
    "verify_ssl": [True, False],
    "ca_cert_path": ["", "/etc/pki/tls/ca-bundle.pem"],
}

_CONFIG_PERMUTATIONS = [
    dict(zip(CONFIG_PARAMS.keys(), values))
    for values in itertools.product(*CONFIG_PARAMS.values())
]


@pytest.fixture(params=_CONFIG_PERMUTATIONS, ids=[
    " ".join(f"{k}={v}" for k, v in p.items())
    for p in _CONFIG_PERMUTATIONS
])
def config_fx(request):
    """Create a Config based on CLIENT_CONFIG_DEFAULT with optional overrides.

    Parametrized over CONFIG_PARAMS permutations so every test
    automatically runs with all combinations, proving cross-cutting
    config is transparent to business logic.

    Usage::

        config = config_fx()                          # defaults
        config = config_fx(cleanup_on_fail=True)      # one override
    """
    base = dataclasses.replace(CLIENT_CONFIG_DEFAULT, **request.param)

    def _make(**overrides):
        return dataclasses.replace(base, **overrides)
    return _make


def mock_response(status_code, json_data=None, text="",
                  headers=None, content=None):
    """Build a fake requests.Response with the given attributes.

    Pass *content* (bytes) for download responses that need
    ``iter_content``::

        mock_response(200, content=b"file data")
    """
    resp = types.SimpleNamespace(
        status_code=status_code,
        json=lambda: json_data,
        text=text,
        headers=headers or {},
    )
    if content is not None:
        resp.iter_content = lambda chunk_size=None: iter([content])
    return resp


@pytest.fixture
def requests_fx(monkeypatch):
    """Replace ``tapest_client.client.requests`` methods with fakes.

    Set up responses before calling the code under test::

        requests_fx.responses["get"] = mock_response(200, json_data={...})

    For multiple sequential calls, use a list::

        requests_fx.responses["get"] = [resp_first, resp_second]

    After the call, inspect what was sent::

        requests_fx.calls["get"]["verify"]      # last call's verify kwarg
        requests_fx.calls["get"]["headers"]    # last call's headers
        requests_fx.all_calls["get"][0]        # first call (multi-call)
    """
    calls = {}
    all_calls = {}
    responses = {}
    _ok = mock_response(200, json_data={})

    def _resolve(resp):
        if isinstance(resp, list):
            resp = resp.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def _make_fake(method_name):
        def _fake(*args, **kwargs):
            calls[method_name] = kwargs
            all_calls.setdefault(method_name, []).append(kwargs)
            return _resolve(responses.get(method_name, _ok))
        return _fake

    for method in ("get", "put", "delete", "post", "patch"):
        monkeypatch.setattr(
            f"tapest_client.client.requests.{method}",
            _make_fake(method))

    return types.SimpleNamespace(
        calls=calls, all_calls=all_calls, responses=responses)


@pytest.fixture
def cli_fx(monkeypatch):
    """Replace ``tapest_client.cli.tapest_client`` library calls with fakes.

    Set up a fake before calling the code under test::

        cli_fx("ingest_file", return_value={"identifier": "/id"})
        _run_ingest(config, args)
        assert cli_fx.calls["ingest_file"] == [
            (("/id", "/path"), {"storage_name": None})]

    The patched function receives all arguments; ``config`` (always the
    first positional arg from CLI handlers) is stripped from captured
    calls so assertions focus on the business-relevant arguments.
    """
    calls = {}

    class _Fixture:
        def __init__(self):
            self.calls = calls

        def __call__(self, func_name, return_value=None):
            calls[func_name] = []

            def _fake(config, *args, **kwargs):
                calls[func_name].append((args, kwargs))
                if isinstance(return_value, BaseException):
                    raise return_value
                return return_value

            monkeypatch.setattr(
                f"tapest_client.cli.tapest_client.{func_name}", _fake)

    return _Fixture()
