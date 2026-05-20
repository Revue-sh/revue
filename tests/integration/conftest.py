"""Conftest for repo-root integration tests."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: integration tests that build wheels, create venvs, or download deps",
    )
