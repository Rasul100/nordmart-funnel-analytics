from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nordmart.sqlite"
SQL_DIR = ROOT / "sql"
FUNNEL_STEPS = ["session_start", "view_item", "add_to_cart", "begin_checkout", "purchase"]


def connect() -> sqlite3.Connection:
    if not DB.exists():
        from generate_data import build, save

        save(build())
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def run_sql(name: str, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or connect()
    q = (SQL_DIR / name).read_text()
    df = pd.read_sql_query(q, conn)
    if own:
        conn.close()
    return df


def load_frames() -> dict[str, pd.DataFrame]:
    conn = connect()
    out = {t: pd.read_sql_query(f"select * from {t}", conn) for t in ("users", "sessions", "events")}
    conn.close()
    for col in ("first_seen",):
        out["users"][col] = pd.to_datetime(out["users"][col])
    out["sessions"]["session_ts"] = pd.to_datetime(out["sessions"]["session_ts"])
    out["events"]["event_ts"] = pd.to_datetime(out["events"]["event_ts"])
    return out


def session_funnel(sessions: pd.DataFrame, events: pd.DataFrame, dims: list[str] | None = None) -> pd.DataFrame:
    flags = (
        events.assign(_one=1)
        .pivot_table(index="session_id", columns="event_name", values="_one", aggfunc="max", fill_value=0)
        .reset_index()
    )
    df = sessions.merge(flags, on="session_id", how="left")
    for step in FUNNEL_STEPS:
        if step not in df.columns:
            df[step] = 0
        df[step] = df[step].fillna(0).astype(int)
    dims = dims or []
    if not dims:
        return pd.DataFrame([_funnel_counts(df)])
    rows = []
    for keys, part in df.groupby(dims, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        rec = dict(zip(dims, keys))
        rec.update(_funnel_counts(part))
        rows.append(rec)
    return pd.DataFrame(rows)


def _funnel_counts(part: pd.DataFrame) -> dict:
    rec = {"sessions": int(len(part))}
    prev = max(len(part), 1)
    for step in FUNNEL_STEPS:
        n = int(part[step].sum())
        rec[step] = n
        rec[f"{step}_cr_from_prev"] = n / prev if prev else 0.0
        rec[f"{step}_cr_from_session"] = n / len(part) if len(part) else 0.0
        prev = max(n, 1)
    return rec


def weekly_cohorts(users: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    u = users[["user_id", "first_seen"]].copy()
    u["cohort"] = u["first_seen"].dt.to_period("W").dt.start_time
    s = sessions[["user_id", "session_ts"]].merge(u, on="user_id")
    s["week_n"] = ((s["session_ts"] - s["first_seen"]).dt.days // 7).clip(lower=0)
    size = u.groupby("cohort")["user_id"].nunique().rename("cohort_size")
    active = s.groupby(["cohort", "week_n"])["user_id"].nunique().rename("users")
    out = active.reset_index().merge(size.reset_index(), on="cohort")
    out["retention"] = out["users"] / out["cohort_size"]
    return out.sort_values(["cohort", "week_n"])


def kpis(sessions: pd.DataFrame, events: pd.DataFrame, users: pd.DataFrame) -> dict:
    purch = events[events["event_name"] == "purchase"]
    cr = purch["session_id"].nunique() / sessions["session_id"].nunique()
    aov = purch["revenue"].mean() if len(purch) else 0
    buyers = purch["user_id"].nunique()
    repeat = (purch.groupby("user_id").size() >= 2).mean() if buyers else 0
    return {
        "users": int(users["user_id"].nunique()),
        "sessions": int(sessions["session_id"].nunique()),
        "purchases": int(len(purch)),
        "revenue": float(purch["revenue"].sum()),
        "session_cr": float(cr),
        "aov": float(aov),
        "repeat_buyer_share": float(repeat),
    }
