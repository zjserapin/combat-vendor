"""Shared state and helpers for one seeding run.

IDs are drawn from shuffled pools so a customer's ID says nothing about whether
it was planted — otherwise "high IDs are the bad guys" would leak to the agent.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from src.synth import reference as ref

HISTORY_DAYS = 90  # how much transaction history the bank holds


@dataclass
class SeedContext:
    rng: random.Random
    as_of: date
    _customer_ids: list[int] = field(default_factory=list)
    _account_ids: list[int] = field(default_factory=list)

    @classmethod
    def create(cls, seed: int, as_of: date, max_customers: int) -> SeedContext:
        rng = random.Random(seed)
        customer_ids = list(range(10_001, 10_001 + max_customers))
        account_ids = list(range(500_001, 500_001 + max_customers * 3))
        rng.shuffle(customer_ids)
        rng.shuffle(account_ids)
        return cls(rng=rng, as_of=as_of, _customer_ids=customer_ids, _account_ids=account_ids)

    # --- ids ---
    def next_customer_id(self) -> int:
        return self._customer_ids.pop()

    def next_account_id(self) -> int:
        return self._account_ids.pop()

    # --- dates ---
    @property
    def history_start(self) -> date:
        return self.as_of - timedelta(days=HISTORY_DAYS)

    def days_ago(self, n: int) -> date:
        return self.as_of - timedelta(days=n)

    def at(self, day: date, hour_lo: int = 8, hour_hi: int = 20) -> datetime:
        """A random timestamp on `day` between the given hours."""
        hour = self.rng.randint(hour_lo, hour_hi)
        return datetime.combine(day, time(hour, self.rng.randint(0, 59), self.rng.randint(0, 59)))

    def recent_window(self, length_days: int) -> tuple[date, date]:
        """An alert window that ends 2-25 days before as-of."""
        end = self.days_ago(self.rng.randint(2, 25))
        return end - timedelta(days=length_days), end

    # --- random picks ---
    def dollars(self, lo: float, hi: float) -> int:
        """Random amount in integer cents."""
        return int(round(self.rng.uniform(lo, hi), 2) * 100)

    def person_name(self) -> str:
        return f"{self.rng.choice(ref.FIRST_NAMES)} {self.rng.choice(ref.LAST_NAMES)}"

    def home_city(self) -> str:
        return self.rng.choice(ref.HOME_CITIES)

    def onboarded(self, min_days: int = 200, max_days: int = 3000) -> date:
        return self.days_ago(self.rng.randint(min_days, max_days))
