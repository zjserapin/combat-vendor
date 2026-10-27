"""Build the synthetic Cairn Bank and load it into DuckDB.

Local-only by design: at Ally the data already lives in Snowflake, so nothing
here is ported. Pipeline: generate rows in memory -> write CSVs -> bulk load.
"""

from __future__ import annotations

import csv
import tempfile
from dataclasses import astuple, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb

from config import Config
from src.synth import reference as ref
from src.synth.analyst_notes import ANALYST_RATIONALE
from src.synth.background import make_account, make_customer, normal_activity
from src.synth.context import SeedContext
from src.synth.models import (
    Account,
    Customer,
    Disposition,
    JurisdictionRisk,
    KycNote,
    SarReport,
    Txn,
)
from src.synth.schema import create_all, make_alert_id
from src.synth.typologies import HUMAN_DECISION, TYPOLOGIES, VARIANT_COUNTS, PlantedCase

N_BACKGROUND_CUSTOMERS = 800
HANDLE_MINUTES = {"ESCALATE": (60, 180), "KYC_REVIEW": (30, 90), "DISMISS": (10, 40)}


@dataclass
class SyntheticBank:
    customers: list[Customer] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    transactions: list[Txn] = field(default_factory=list)
    kyc_notes: list[KycNote] = field(default_factory=list)
    sar_reports: list[SarReport] = field(default_factory=list)
    jurisdiction_risk: list[JurisdictionRisk] = field(default_factory=list)
    alert_dispositions: list[Disposition] = field(default_factory=list)


def build_bank(seed: int, as_of: date) -> SyntheticBank:
    """Generate every row in memory. Same seed + as_of -> identical bank."""
    planted_total = len(TYPOLOGIES) * sum(VARIANT_COUNTS.values())
    ctx = SeedContext.create(seed, as_of, N_BACKGROUND_CUSTOMERS + planted_total)
    bank = SyntheticBank()

    for _ in range(N_BACKGROUND_CUSTOMERS):
        customer = make_customer(ctx)
        account = make_account(ctx, customer)
        bank.customers.append(customer)
        bank.accounts.append(account)
        bank.transactions.extend(normal_activity(ctx, customer, account))

    for builder in TYPOLOGIES.values():
        for variant, count in VARIANT_COUNTS.items():
            for _ in range(count):
                _absorb(bank, ctx, builder(ctx, variant))

    bank.jurisdiction_risk = [JurisdictionRisk(*row) for row in ref.jurisdiction_rows()]
    _finalize(bank, ctx)
    return bank


def _absorb(bank: SyntheticBank, ctx: SeedContext, case: PlantedCase) -> None:
    bank.customers.append(case.customer)
    bank.accounts.append(case.account)
    bank.transactions.extend(case.txns)
    bank.kyc_notes.extend(case.notes)
    bank.sar_reports.extend(case.sars)
    bank.alert_dispositions.append(_disposition(ctx, case))


def _disposition(ctx: SeedContext, case: PlantedCase) -> Disposition:
    """What the human analyst decided for this case, and how long it took them."""
    flagged_days = [t.txn_ts.date() for t in case.txns if t.flag_reason]
    window_start, window_end = min(flagged_days), max(flagged_days)
    decision = HUMAN_DECISION[case.variant]
    decided_day = min(window_end + timedelta(days=ctx.rng.randint(1, 5)), ctx.as_of)
    return Disposition(
        alert_id=make_alert_id(case.customer.customer_id, case.typology, window_start),
        customer_id=case.customer.customer_id,
        typology=case.typology,
        window_start=window_start,
        window_end=window_end,
        human_decision=decision,
        analyst_id=ctx.rng.choice(ref.ANALYSTS),
        decided_at=datetime.combine(decided_day, time(ctx.rng.randint(9, 17), 0)),
        handle_minutes=ctx.rng.randint(*HANDLE_MINUTES[decision]),
        human_rationale=ANALYST_RATIONALE[(case.typology, case.variant)],
    )


def _finalize(bank: SyntheticBank, ctx: SeedContext) -> None:
    """Assign time-ordered IDs and compute balances once all activity exists."""
    bank.transactions.sort(key=lambda t: (t.txn_ts, t.account_id))
    for i, txn in enumerate(bank.transactions, start=1):
        txn.txn_id = i
    for i, note in enumerate(sorted(bank.kyc_notes, key=lambda n: n.note_date), start=1):
        note.note_id = i
    for i, sar in enumerate(sorted(bank.sar_reports, key=lambda s: s.filed_date), start=1):
        sar.sar_id = i

    net: dict[int, int] = {}
    for txn in bank.transactions:
        if txn.status == "BLOCKED":
            continue
        sign = 1 if txn.direction == "CREDIT" else -1
        net[txn.account_id] = net.get(txn.account_id, 0) + sign * txn.amount_cents
    for account in bank.accounts:
        opening = ctx.dollars(1_000, 25_000)
        account.balance_cents = max(opening + net.get(account.account_id, 0), ctx.dollars(50, 900))


def write_bank(bank: SyntheticBank, config: Config) -> dict[str, int]:
    """Rebuild the DuckDB file from scratch and bulk-load every table."""
    config.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    config.duckdb_path.unlink(missing_ok=True)
    targets = {
        "customers": config.bank_schema, "accounts": config.bank_schema,
        "transactions": config.bank_schema, "kyc_notes": config.bank_schema,
        "sar_reports": config.bank_schema, "jurisdiction_risk": config.bank_schema,
        "alert_dispositions": config.labels_schema,
    }
    counts: dict[str, int] = {}
    with duckdb.connect(str(config.duckdb_path)) as conn, tempfile.TemporaryDirectory() as tmp:
        create_all(conn, config)
        for table, schema in targets.items():
            rows = getattr(bank, table)
            _bulk_load(conn, f"{schema}.{table}", rows, Path(tmp) / f"{table}.csv")
            counts[f"{schema}.{table}"] = len(rows)
    return counts


def _bulk_load(conn, table: str, rows: list, path: Path) -> None:
    """CSV + INSERT ... SELECT is ~100x faster than row-by-row inserts."""
    if not rows:
        return
    columns = [f.name for f in fields(rows[0])]
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(astuple(r) for r in rows)
    col_list = ", ".join(columns)
    conn.execute(
        f"INSERT INTO {table} ({col_list}) "
        f"SELECT {col_list} FROM read_csv(?, header = true, all_varchar = true)",
        [str(path)],
    )


def run_seed(config: Config) -> dict[str, int]:
    """Entry point used by `main.py seed`."""
    return write_bank(build_bank(config.seed, config.as_of_date), config)
