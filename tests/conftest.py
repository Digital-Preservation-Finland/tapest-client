"""Pytest configuration and shared fixtures."""

import dataclasses
import itertools
import re
import types

import pytest
import requests_mock

from base64 import b64decode
from tapest_client.config import Config


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests (benchmarks, etc.)",
    )


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


@pytest.fixture(
    params=_CONFIG_PERMUTATIONS,
    ids=[" ".join(f"{k}={v}" for k, v in p.items()) for p in _CONFIG_PERMUTATIONS],
)
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


def mock_response(status_code, json_data=None, text="", headers=None, content=None):
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
            f"tapest_client.client.requests.{method}", _make_fake(method)
        )

    return types.SimpleNamespace(calls=calls, all_calls=all_calls, responses=responses)


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

            monkeypatch.setattr(f"tapest_client.cli.tapest_client.{func_name}", _fake)

    return _Fixture()


@pytest.fixture(scope="function")
def mock_tus_endpoints(config_fx):
    """Mock TUS endpoints by having dynamic responses."""
    config = config_fx()
    tus_url = f"^{config.host}/tus"
    # We're processing all endpoints that starts with given tus_url.
    matcher = re.compile(tus_url)
    # Uploads will be tracked within this dictionary.
    uploads = {}
    tus_supported_extensions = ["creation", "termination"]
    tus_version = "1.0.0"
    tus_supported_version = ["1.0.0"]
    options_headers = {
        "Tus-Extension": ",".join(tus_supported_extensions),
        "Tus-Resumable": tus_version,
        "Tus-Version": ",".join(tus_supported_version),
    }

    def head_response(request, context):
        try:
            upload = uploads[request.path_url]
        except KeyError:
            context.status_code = 404
            context.headers = {"Tus-Resumable": tus_version}
            return ""

        context.headers = {
            "Cache-Control": "no-store",
            "Tus-Resumable": tus_version,
            "Upload-Offset": str(upload["chunks_uploaded"]),
            "Upload-Metadata": upload["upload_metadata"],
            "Upload-Length": str(upload["upload_length"]),
        }
        return ""

    def patch_response(request, context):
        nonlocal uploads
        try:
            upload = uploads[request.path_url]
        except KeyError:
            context.status_code = 404
            context.headers = {"Tus-Resumable": tus_version}
            return ""

        content_length = int(request.headers["Content-Length"])
        upload["chunks_uploaded"] += content_length
        if upload["chunks_uploaded"] > upload["upload_length"]:
            upload["chunks_uploaded"] = upload["upload_length"]
        context.headers = {
            "Tus-Resumable": tus_version,
            "Upload-Offset": str(upload["chunks_uploaded"]),
        }
        return ""

    def post_response(request, context):
        nonlocal uploads
        # Pick the filename from the metadata.
        filename = None
        for key_value in request.headers["Upload-Metadata"].split(","):
            if key_value.startswith("filename"):
                _, filename = key_value.split(" ")
        if filename is None:
            context.status_code = 400
            context.headers = {"Tus-Resumable": tus_version}
            return ""

        filename = b64decode(filename).decode("UTF-8")
        upload_id = f"{request.path_url}/{filename}"
        uploads[upload_id] = {
            "chunks_uploaded": 0,
            "upload_metadata": request.headers["Upload-Metadata"],
            "upload_length": int(request.headers["Upload-Length"]),
        }

        context.headers = {
            "Location": upload_id,
            "Tus-Resumable": tus_version,
        }
        return ""

    def metadata_response(request, context):
        try:
            upload = uploads["/tus/" + request.qs.get("identifier")[0]]
        except KeyError:
            context.status_code = 404
            return "{}"
        if upload["chunks_uploaded"] == upload["upload_length"]:
            return "{}"
        context.status_code = 404
        return "{}"

    with requests_mock.Mocker() as mock:
        mock.options(
            matcher, text="", headers=options_headers, status_code=204
        )
        mock.head(matcher, text=head_response, status_code=204)
        mock.patch(matcher, text=patch_response, status_code=204)
        mock.post(matcher, text=post_response, status_code=201)
        mock.get(
            f"{config.host}/metadata", text=metadata_response, status_code=200
        )
        yield


@pytest.fixture(scope="function", autouse=True)
def monkeypatch_user_state_path(monkeypatch, tmp_path):
    state_path = tmp_path / "state"
    state_path.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "tapest_client.client.user_state_path", lambda *a, **kw: state_path
    )
    yield
