from __future__ import annotations

import math
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


def data_end(sessions: pd.DataFrame) -> pd.Timestamp:
    """First moment after the observed data (midnight after the last session)."""
    return sessions["session_ts"].max().normalize() + pd.Timedelta(days=1)


def weekly_cohorts(users: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Weekly retention by acquisition cohort, right-censored.

    Week n of a user counts only if it is fully observed:
    first_seen + 7·(n+1) days <= end of data. Otherwise the last, unfinished
    week of every cohort shows a fake drop (users simply had no time to return).
    """
    end = data_end(sessions)
    u = users[["user_id", "first_seen"]].copy()
    u["cohort"] = u["first_seen"].dt.to_period("W").dt.start_time
    s = sessions[["user_id", "session_ts"]].merge(u, on="user_id")
    s["week_n"] = ((s["session_ts"] - s["first_seen"]).dt.days // 7).clip(lower=0)
    active = s[["user_id", "week_n"]].drop_duplicates()
    max_w = int((end - u["first_seen"].min()).days // 7)
    rows = []
    for w in range(max_w + 1):
        eligible = u[u["first_seen"] + pd.Timedelta(days=7 * (w + 1)) <= end]
        if eligible.empty:
            continue
        act = eligible.merge(active[active["week_n"] == w], on="user_id")
        size = eligible.groupby("cohort")["user_id"].size()
        users_w = act.groupby("cohort")["user_id"].size().reindex(size.index, fill_value=0)
        rows.append(pd.DataFrame({"cohort": size.index, "week_n": w, "users": users_w.values, "cohort_size": size.values}))
    out = pd.concat(rows, ignore_index=True)
    out["retention"] = out["users"] / out["cohort_size"]
    return out.sort_values(["cohort", "week_n"]).reset_index(drop=True)


def retention_curve(cohorts: pd.DataFrame) -> pd.DataFrame:
    """All cohorts pooled: weighted retention by week of life."""
    g = cohorts.groupby("week_n")[["users", "cohort_size"]].sum()
    g["retention"] = g["users"] / g["cohort_size"]
    return g.reset_index()


Z95 = 1.959963984540054


def wilson(x: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = x / n
    den = 1 + z**2 / n
    mid = (p + z**2 / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return (mid - half, mid + half)


def two_prop_test(x1: int, n1: int, x2: int, n2: int) -> dict:
    """Difference of two proportions: Δ, 95% CI, two-sided z-test p-value."""
    p1, p2 = x1 / n1, x2 / n2
    pool = (x1 + x2) / (n1 + n2)
    se0 = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    z = (p1 - p2) / se0
    return {
        "diff": p1 - p2,
        "ci": (p1 - p2 - Z95 * se, p1 - p2 + Z95 * se),
        "p_value": math.erfc(abs(z) / math.sqrt(2)),
    }


def checkout_by_device(sessions: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """begin_checkout → purchase per device with Wilson 95% CI."""
    f = session_funnel(sessions, events, ["device"])
    f["checkout_to_pay"] = f["purchase"] / f["begin_checkout"]
    ci = [wilson(int(x), int(n)) for x, n in zip(f["purchase"], f["begin_checkout"])]
    f["ci_lo"] = [c[0] for c in ci]
    f["ci_hi"] = [c[1] for c in ci]
    return f[["device", "begin_checkout", "purchase", "checkout_to_pay", "ci_lo", "ci_hi"]]


def device_comparisons(sessions: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Is iOS really worse than Android? Is mobile worse than desktop?"""
    f = checkout_by_device(sessions, events).set_index("device")
    mob = f.loc[["ios", "android"], ["begin_checkout", "purchase"]].sum()
    rows = []
    for a, b, xa, na, xb, nb in [
        ("ios", "android", f.at["ios", "purchase"], f.at["ios", "begin_checkout"],
         f.at["android", "purchase"], f.at["android", "begin_checkout"]),
        ("mobile", "desktop", mob["purchase"], mob["begin_checkout"],
         f.at["desktop", "purchase"], f.at["desktop", "begin_checkout"]),
    ]:
        r = two_prop_test(int(xa), int(na), int(xb), int(nb))
        rows.append({"a": a, "b": b, "rate_a": xa / na, "rate_b": xb / nb, **r})
    return pd.DataFrame(rows)


def mobile_fix_impact(sessions: pd.DataFrame, events: pd.DataFrame, gap_closed: float = 0.5) -> dict:
    """Extra revenue if mobile checkout→pay closes `gap_closed` of the gap to desktop.

    Ceiling = gap fully closed. Revenue uses mobile AOV; numbers are for the
    observed window and scaled to 30 days.
    """
    f = checkout_by_device(sessions, events).set_index("device")
    mob = f.loc[["ios", "android"], ["begin_checkout", "purchase"]].sum()
    rate_mob = mob["purchase"] / mob["begin_checkout"]
    rate_desk = f.at["desktop", "checkout_to_pay"]
    purch = events[events["event_name"] == "purchase"].merge(sessions[["session_id", "device"]], on="session_id")
    aov_mob = purch.loc[purch["device"] != "desktop", "revenue"].mean()
    days = (data_end(sessions) - sessions["session_ts"].min().normalize()).days
    extra_max = mob["begin_checkout"] * (rate_desk - rate_mob)
    total_rev = purch["revenue"].sum()
    out = {
        "mobile_checkouts": int(mob["begin_checkout"]),
        "rate_mobile": float(rate_mob),
        "rate_desktop": float(rate_desk),
        "aov_mobile": float(aov_mob),
        "days": int(days),
        "gap_closed": gap_closed,
    }
    for name, share in (("ceiling", 1.0), ("scenario", gap_closed)):
        extra = extra_max * share
        out[f"{name}_orders"] = float(extra)
        out[f"{name}_revenue"] = float(extra * aov_mob)
        out[f"{name}_revenue_30d"] = float(extra * aov_mob / days * 30)
        out[f"{name}_revenue_share"] = float(extra * aov_mob / total_rev)
    return out


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
