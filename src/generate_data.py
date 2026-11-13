"""Synthetic NordMart events: 8 weeks, baked-in product effects."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEED = 42

N_USERS = 12_000
START = pd.Timestamp("2026-07-01")
END = pd.Timestamp("2026-08-25")
CHANNELS = np.array(["organic", "paid_search", "paid_social", "email", "referral"])
CHANNEL_P = np.array([0.34, 0.22, 0.24, 0.12, 0.08])
DEVICES = np.array(["desktop", "ios", "android"])
DEVICE_P = np.array([0.38, 0.34, 0.28])
CATEGORIES = np.array(["grocery", "home", "electronics", "beauty", "kids"])
CATEGORY_P = np.array([0.32, 0.18, 0.16, 0.20, 0.14])
PRICES = {
    "grocery": (180, 720),
    "home": (450, 2400),
    "electronics": (1200, 8900),
    "beauty": (290, 1600),
    "kids": (250, 1900),
}


def _rng() -> np.random.Generator:
    return np.random.default_rng(SEED)


def _user_table(rng: np.random.Generator) -> pd.DataFrame:
    first_seen = START + pd.to_timedelta(rng.integers(0, 40, N_USERS), unit="D")
    channel = rng.choice(CHANNELS, N_USERS, p=CHANNEL_P)
    device = rng.choice(DEVICES, N_USERS, p=DEVICE_P)
    # Email users return more; paid social are noisier one-shot visitors.
    loyalty = np.where(channel == "email", 0.55, np.where(channel == "paid_social", 0.18, 0.32))
    loyalty = np.clip(loyalty + rng.normal(0, 0.05, N_USERS), 0.08, 0.85)
    return pd.DataFrame(
        {
            "user_id": np.arange(1, N_USERS + 1),
            "first_seen": first_seen,
            "acq_channel": channel,
            "primary_device": device,
            "loyalty": loyalty,
        }
    )


MAX_SESSIONS = 14
MEAN_GAP_DAYS = 5.0


def _visit_days(loyalty: float, span: int, rng: np.random.Generator) -> list[int]:
    """Day offsets of a user's visits: first visit on day 0, then churn or return.

    After each visit the user comes back with probability `p_return`, after an
    exponential gap. A constant churn hazard gives the realistic decaying
    retention curve.

    The previous version drew k uniform days, sorted them and overwrote the
    smallest with 0. That keeps only the *largest* k-1 draws, skewing visits
    to the end of the window, so retention grew with week number.
    """
    p_return = min(0.92, 0.45 + 0.6 * loyalty)
    days = [0]
    day = 0.0
    while len(days) < MAX_SESSIONS and rng.random() < p_return:
        day += 1 + rng.exponential(MEAN_GAP_DAYS)
        if day > span:
            break
        days.append(int(day))
    return days


def _funnel_p(channel: str, device: str, category: str) -> dict[str, float]:
    view = 0.82
    cart = 0.28
    checkout = 0.62
    pay = 0.72

    if channel == "paid_social":
        view, cart = 0.88, 0.16
    elif channel == "email":
        view, cart, checkout, pay = 0.90, 0.36, 0.70, 0.80
    elif channel == "paid_search":
        cart, pay = 0.30, 0.74
    elif channel == "referral":
        cart, pay = 0.33, 0.78

    if device != "desktop":
        checkout *= 0.86
        pay *= 0.62  # mobile checkout drop-off
    if category == "electronics":
        cart *= 1.15
        checkout *= 0.78
        pay *= 0.82
    if category == "grocery":
        pay *= 1.08
    return {"view": view, "cart": cart, "checkout": checkout, "pay": pay}


def _events(users: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows_s: list[dict] = []
    rows_e: list[dict] = []
    sid = 1
    eid = 1
    for u in users.itertuples(index=False):
        span = max(1, (END - u.first_seen).days)
        for day_off in _visit_days(float(u.loyalty), span, rng):
            ts = u.first_seen + pd.Timedelta(days=int(day_off), hours=int(rng.integers(8, 23)), minutes=int(rng.integers(0, 60)))
            if ts > END:
                continue
            device = u.primary_device if rng.random() < 0.82 else rng.choice(DEVICES)
            channel = u.acq_channel if rng.random() < 0.7 else rng.choice(CHANNELS, p=CHANNEL_P)
            category = rng.choice(CATEGORIES, p=CATEGORY_P)
            p = _funnel_p(channel, device, category)
            weekend = ts.day_of_week >= 5
            if weekend and device == "desktop":
                p["pay"] = min(0.95, p["pay"] * 1.06)

            rows_s.append(
                {
                    "session_id": sid,
                    "user_id": int(u.user_id),
                    "session_ts": ts,
                    "device": device,
                    "channel": channel,
                    "category": category,
                }
            )
            steps = [("session_start", 1.0)]
            if rng.random() < p["view"]:
                steps.append(("view_item", p["view"]))
                if rng.random() < p["cart"]:
                    steps.append(("add_to_cart", p["cart"]))
                    if rng.random() < p["checkout"]:
                        steps.append(("begin_checkout", p["checkout"]))
                        if rng.random() < p["pay"]:
                            steps.append(("purchase", p["pay"]))

            lo, hi = PRICES[category]
            price = round(float(rng.uniform(lo, hi)), 2)
            qty = int(rng.integers(1, 4))
            for delay, (name, _) in enumerate(steps):
                event_ts = ts + pd.Timedelta(seconds=int(20 + delay * rng.integers(15, 90)))
                revenue = price * qty if name == "purchase" else 0.0
                rows_e.append(
                    {
                        "event_id": eid,
                        "session_id": sid,
                        "user_id": int(u.user_id),
                        "event_ts": event_ts,
                        "event_name": name,
                        "category": category,
                        "revenue": revenue,
                    }
                )
                eid += 1
            sid += 1
    return pd.DataFrame(rows_s), pd.DataFrame(rows_e)


def build() -> dict[str, pd.DataFrame]:
    rng = _rng()
    users = _user_table(rng)
    sessions, events = _events(users, rng)
    users_out = users.drop(columns=["loyalty"])
    return {"users": users_out, "sessions": sessions, "events": events}


def save(tables: dict[str, pd.DataFrame]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    db = DATA / "nordmart.sqlite"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    for name, df in tables.items():
        df.to_csv(DATA / f"{name}.csv", index=False)
        df.to_sql(name, conn, index=False, if_exists="replace")
    conn.close()


def main() -> None:
    tables = build()
    save(tables)
    ev = tables["events"]
    print(
        f"users={len(tables['users']):,} sessions={len(tables['sessions']):,} events={len(ev):,} "
        f"purchases={(ev.event_name == 'purchase').sum():,}",
        file=sys.stderr,
    )
    print(DATA / "nordmart.sqlite")


if __name__ == "__main__":
    main()
