"""Pytest configuration and shared fixtures."""

import pytest


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="Run slow tests (benchmarks, etc.)")


def pytest_runtest_setup(item):
    """Skip slow tests unless --run-slow is given."""
    if "slow" in item.keywords and not item.config.getoption("--run-slow"):
        pytest.skip("slow tests require --run-slow flag")
