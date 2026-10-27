"""All configuration in one place.

Two swaps happen here and nowhere else:
  - LLM_PROVIDER: "anthropic" (local) <-> "bedrock" (Ally)
  - DB_BACKEND:   "duckdb"    (local) <-> "snowflake" (Ally)
Logic files never read environment variables directly.
"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Config:
    """Runtime settings. Build with load_config(), not directly."""

    # --- LLM ---
    llm_provider: str  # "anthropic" | "bedrock"
    llm_model: str  # Anthropic API model id
    bedrock_model_id: str  # Bedrock model id (anthropic.-prefixed)
    llm_effort: str  # low | medium | high | xhigh | max
    anthropic_api_key: str
    aws_region: str
    aws_profile: str

    # --- Data ---
    db_backend: str  # "duckdb" | "snowflake"
    duckdb_path: Path
    snowflake_account: str
    snowflake_user: str
    snowflake_role: str  # harness role: reads bank + labels, writes agent schema
    snowflake_agent_role: str  # agent tools role: SELECT on bank schema only
    snowflake_warehouse: str
    snowflake_database: str
    snowflake_authenticator: str

    # Schema names. Labels live apart from bank data so the agent can be denied them.
    bank_schema: str
    labels_schema: str
    agent_schema: str

    # --- Run ---
    as_of_date: date  # "today" for the run: fixed locally for reproducibility, "today" at Ally
    seed: int
    alert_lookback_days: int  # alerts whose latest flagged activity falls in this window
    # Evidence pack windows (point-in-time: nothing after window_end + evidence_after_days)
    evidence_history_days: int  # profile + counterparty history before the window
    evidence_context_days: int  # "largest other transactions" lookback before the window
    evidence_after_days: int  # how far past the window an analyst could have seen activity

    # --- Agent ---
    bank_name: str  # appears in the system prompt
    agent_max_steps: int  # model calls per alert, including the decision
    agent_max_seconds: float  # wall-clock budget per alert
    agent_max_tokens: int  # max output tokens per model call (thinking included)
    tools_strict: bool  # strict tool schemas; turn off only if an endpoint rejects them
    dismiss_min_confidence: float  # a DISMISS below this goes to a human instead

    # --- Run & report ---
    triage_max_consecutive_failures: int  # stop a run if the model keeps failing
    input_usd_per_mtok: float  # model price, for the cost line (check your contract/Bedrock)
    output_usd_per_mtok: float
    review_sample_rate: float  # POLICY: share of agent dismissals a human still re-reviews
    manual_minutes_per_alert: float  # ASSUMPTION used only when handle time isn't recorded

    # --- Backtest ---
    backtest_holdout_share: float  # share of labelled alerts held out (reported, never tuned on)
    backtest_split_salt: str  # changing it re-draws the split — do that only on purpose

    @property
    def model_id(self) -> str:
        """The model id the active provider expects."""
        return self.bedrock_model_id if self.llm_provider == "bedrock" else self.llm_model

    def validate(self) -> None:
        """Fail fast on settings that would break a run halfway through."""
        if self.llm_provider not in {"anthropic", "bedrock"}:
            raise ValueError(f"LLM_PROVIDER must be anthropic|bedrock, got {self.llm_provider!r}")
        if self.db_backend not in {"duckdb", "snowflake"}:
            raise ValueError(f"DB_BACKEND must be duckdb|snowflake, got {self.db_backend!r}")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("Set ANTHROPIC_KEY in your shell (or ANTHROPIC_API_KEY in .env).")
        if self.db_backend == "snowflake" and not self.snowflake_account:
            raise ValueError("SNOWFLAKE_ACCOUNT is required when DB_BACKEND=snowflake.")


def _as_of(value: str) -> date:
    """ISO date, or "today" (what you want against live data)."""
    return date.today() if value.lower() == "today" else date.fromisoformat(value)


def load_config() -> Config:
    """Read settings from the environment (shell first, then .env)."""
    duckdb_path = Path(_env("DUCKDB_PATH", "data/cairn_bank.duckdb"))
    if not duckdb_path.is_absolute():
        duckdb_path = PROJECT_ROOT / duckdb_path

    return Config(
        llm_provider=_env("LLM_PROVIDER", "anthropic").lower(),
        llm_model=_env("LLM_MODEL", "claude-opus-5-5"),
        bedrock_model_id=_env("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5"),
        llm_effort=_env("LLM_EFFORT", "medium"),
        # Shell-exported ANTHROPIC_KEY wins so the key never needs to be copied into .env.
        anthropic_api_key=_env("ANTHROPIC_KEY") or _env("ANTHROPIC_API_KEY"),
        aws_region=_env("AWS_REGION", "us-east-1"),
        aws_profile=_env("AWS_PROFILE"),
        db_backend=_env("DB_BACKEND", "duckdb").lower(),
        duckdb_path=duckdb_path,
        snowflake_account=_env("SNOWFLAKE_ACCOUNT"),
        snowflake_user=_env("SNOWFLAKE_USER"),
        snowflake_role=_env("SNOWFLAKE_ROLE"),
        snowflake_agent_role=_env("SNOWFLAKE_AGENT_ROLE"),
        snowflake_warehouse=_env("SNOWFLAKE_WAREHOUSE"),
        snowflake_database=_env("SNOWFLAKE_DATABASE"),
        snowflake_authenticator=_env("SNOWFLAKE_AUTHENTICATOR", "externalbrowser"),
        bank_schema=_env("BANK_SCHEMA", "bank"),
        labels_schema=_env("LABELS_SCHEMA", "labels"),
        agent_schema=_env("AGENT_SCHEMA", "agent"),
        as_of_date=_as_of(_env("AS_OF_DATE", "2026-09-30")),
        seed=int(_env("SEED", "42")),
        alert_lookback_days=int(_env("ALERT_LOOKBACK_DAYS", "30")),
        evidence_history_days=int(_env("EVIDENCE_HISTORY_DAYS", "90")),
        evidence_context_days=int(_env("EVIDENCE_CONTEXT_DAYS", "30")),
        evidence_after_days=int(_env("EVIDENCE_AFTER_DAYS", "3")),
        bank_name=_env("BANK_NAME", "Cairn Bank"),
        agent_max_steps=int(_env("AGENT_MAX_STEPS", "6")),
        agent_max_seconds=float(_env("AGENT_MAX_SECONDS", "120")),
        agent_max_tokens=int(_env("AGENT_MAX_TOKENS", "16000")),
        tools_strict=_env("TOOLS_STRICT", "true").lower() in {"1", "true", "yes"},
        dismiss_min_confidence=float(_env("DISMISS_MIN_CONFIDENCE", "0.6")),
        triage_max_consecutive_failures=int(_env("TRIAGE_MAX_CONSECUTIVE_FAILURES", "3")),
        input_usd_per_mtok=float(_env("INPUT_USD_PER_MTOK", "4.0")),
        output_usd_per_mtok=float(_env("OUTPUT_USD_PER_MTOK", "20.0")),
        review_sample_rate=float(_env("REVIEW_SAMPLE_RATE", "0.10")),
        manual_minutes_per_alert=float(_env("MANUAL_MINUTES_PER_ALERT", "45")),
        backtest_holdout_share=float(_env("BACKTEST_HOLDOUT_SHARE", "0.5")),
        backtest_split_salt=_env("BACKTEST_SPLIT_SALT", "split-v1"),
    )
