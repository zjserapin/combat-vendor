"""Block 0: config resolution, provider swap, and the database wrapper. No network calls."""

from dataclasses import replace

import duckdb
import pytest

from config import load_config
from src.db import Database
from src.llm_client import LLMClient


def test_shell_key_wins_over_dotenv_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_KEY", "from-shell")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-dotenv")
    assert load_config().anthropic_api_key == "from-shell"


def test_falls_back_to_dotenv_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-dotenv")
    assert load_config().anthropic_api_key == "from-dotenv"


def test_model_id_follows_provider(config):
    assert config.model_id == config.llm_model
    bedrock = replace(config, llm_provider="bedrock")
    assert bedrock.model_id == bedrock.bedrock_model_id
    assert bedrock.model_id.startswith("anthropic.")


def test_validate_rejects_unknown_backend(config):
    with pytest.raises(ValueError, match="DB_BACKEND"):
        replace(config, db_backend="oracle").validate()


def test_llm_client_builds_without_network(config):
    client = LLMClient(config)
    assert client.model == config.llm_model
    assert client.meter.calls == 0


def test_query_returns_lowercase_dicts(seeded):
    with Database(seeded, read_only=True) as db:
        rows = db.query(f"SELECT COUNT(*) AS N FROM {seeded.bank_schema}.customers WHERE "
                        f"segment = ?", ["RETAIL"])
    assert list(rows[0]) == ["n"]
    assert rows[0]["n"] > 0


def test_read_only_connection_refuses_writes(seeded):
    with Database(seeded, read_only=True) as db, pytest.raises(duckdb.Error):
        db.execute(f"DELETE FROM {seeded.bank_schema}.customers")


def test_as_of_today_for_live_data(monkeypatch):
    from datetime import date
    monkeypatch.setenv("AS_OF_DATE", "today")
    assert load_config().as_of_date == date.today()
