"""
Alert state.

The scanner is stateless: it looks at today and says whether today qualifies. A
five-day shock qualifies on day 1, day 2, day 3, day 4 and day 5, so a naive
daily job emails you five times about one event. Worse, you learn to ignore it.

This module remembers what has already been sent. It is a small JSON file
rather than the DuckDB store because it has to survive between runs on a
scheduler with no persistent disk, which means being committed back to the repo
— and a 2KB text file diffs cleanly in git while a binary database does not.

Rules:
  - one email per industry per event
  - an industry stays suppressed for `cooldown_days` after its last alert
  - an escalation (WATCH -> INVESTIGATE -> MAJOR) breaks through the cooldown,
    because "this got worse" is genuinely new information
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

LEVEL_RANK = {"NONE": 0, "WATCH": 1, "INVESTIGATE": 2, "MAJOR_EVENT": 3}


@dataclass
class IndustryState:
    last_alert_date: str
    last_level: str
    last_score: float
    first_alert_date: str
    times_alerted: int = 1


@dataclass
class AlertState:
    industries: Dict[str, IndustryState] = field(default_factory=dict)
    last_run: Optional[str] = None
    last_run_status: str = "never"
    runs_since_last_alert: int = 0

    # ---- persistence ---------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "AlertState":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not stop the job. Starting fresh risks
            # one duplicate email; crashing risks missing every future event.
            return cls()
        return cls(
            industries={k: IndustryState(**v)
                        for k, v in raw.get("industries", {}).items()},
            last_run=raw.get("last_run"),
            last_run_status=raw.get("last_run_status", "never"),
            runs_since_last_alert=raw.get("runs_since_last_alert", 0),
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump({
                "industries": {k: asdict(v) for k, v in self.industries.items()},
                "last_run": self.last_run,
                "last_run_status": self.last_run_status,
                "runs_since_last_alert": self.runs_since_last_alert,
            }, f, indent=2, sort_keys=True)
        os.replace(tmp, path)   # atomic, so a killed job can't truncate it

    # ---- decisions -----------------------------------------------------
    def should_notify(self, industry: str, level: str, score: float,
                      today: dt.date, cooldown_days: int = 45) -> tuple:
        """Returns (send: bool, reason: str)."""
        prev = self.industries.get(industry)
        if prev is None:
            return True, "first alert for this industry"

        last = dt.date.fromisoformat(prev.last_alert_date)
        days = (today - last).days

        if LEVEL_RANK[level] > LEVEL_RANK[prev.last_level]:
            return True, (f"escalated from {prev.last_level} to {level} "
                          f"after {days} days")
        if days >= cooldown_days:
            return True, f"{days} days since the last alert, treating as new"
        return False, (f"already alerted {days} days ago at {prev.last_level}; "
                       f"suppressed until {cooldown_days} days have passed")

    def record(self, industry: str, level: str, score: float,
               today: dt.date) -> None:
        prev = self.industries.get(industry)
        first = prev.first_alert_date if prev else today.isoformat()
        times = (prev.times_alerted + 1) if prev else 1
        self.industries[industry] = IndustryState(
            last_alert_date=today.isoformat(), last_level=level,
            last_score=round(float(score), 1), first_alert_date=first,
            times_alerted=times)
        self.runs_since_last_alert = 0

    def prune(self, today: dt.date, keep_days: int = 400) -> None:
        """Drop industries not seen in over a year, to keep the file small."""
        self.industries = {
            k: v for k, v in self.industries.items()
            if (today - dt.date.fromisoformat(v.last_alert_date)).days <= keep_days
        }

    def summary(self) -> List[str]:
        out = []
        for name, st in sorted(self.industries.items()):
            out.append(f"{name}: {st.last_level} on {st.last_alert_date} "
                       f"(score {st.last_score}, alerted {st.times_alerted}x)")
        return out
