"""Setup commands: check the environment, build the synthetic bank."""

from config import Config


def doctor(config: Config, check_llm: bool) -> int:
    """Print the active configuration and probe each dependency."""
    from src.db import Database

    key = config.anthropic_api_key
    print(f"LLM      : {config.llm_provider} / {config.model_id} (effort={config.llm_effort})")
    print(f"API key  : {'set (' + key[:7] + '...)' if key else 'not set'}")
    print(f"Database : {config.db_backend} "
          f"({config.duckdb_path if config.db_backend == 'duckdb' else config.snowflake_account})")
    print(f"As-of    : {config.as_of_date}  seed={config.seed}")

    try:
        with Database(config, read_only=True) as db:
            rows = db.query(f"SELECT COUNT(*) AS n FROM {config.bank_schema}.transactions")
            print(f"DB check : ok — {rows[0]['n']:,} transactions")
    except Exception as exc:  # noqa: BLE001 — doctor reports any failure instead of crashing
        print(f"DB check : FAILED — {exc}")

    if check_llm:
        from src.llm_client import LLMClient

        client = LLMClient(config)
        reply = client.ping()
        m = client.meter
        print(f"LLM check: ok — replied {reply!r} "
              f"({m.input_tokens} in / {m.output_tokens} out tokens, {m.seconds:.1f}s)")
    return 0


def seed(config: Config) -> int:
    """Generate the synthetic bank and print what was loaded."""
    if config.db_backend != "duckdb":
        print("Seeding is local-only. At Ally the data already lives in Snowflake.")
        return 1
    from src.synth.seed import run_seed

    counts = run_seed(config)
    print(f"Seeded Cairn Bank into {config.duckdb_path}")
    for table, n in counts.items():
        print(f"  {table:<32} {n:>8,}")
    return 0
