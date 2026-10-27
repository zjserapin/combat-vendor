"""Shared fixtures: a seeded Cairn Bank in a temp directory, built once per test session."""

import shutil
from dataclasses import replace

import pytest

from config import load_config
from src.synth.seed import build_bank, write_bank


@pytest.fixture(scope="session")
def config(tmp_path_factory):
    """Local DuckDB config pointed at a throwaway file."""
    path = tmp_path_factory.mktemp("db") / "cairn_test.duckdb"
    return replace(load_config(), db_backend="duckdb", duckdb_path=path,
                   llm_provider="anthropic", anthropic_api_key="test-key")


@pytest.fixture(scope="session")
def bank(config):
    return build_bank(config.seed, config.as_of_date)


@pytest.fixture(scope="session")
def seeded(config, bank):
    write_bank(bank, config)
    return config


@pytest.fixture
def workspace(seeded, tmp_path):
    """A private copy of the seeded bank, for tests that write to the agent schema."""
    path = tmp_path / "workspace.duckdb"
    shutil.copy(seeded.duckdb_path, path)
    return replace(seeded, duckdb_path=path)
