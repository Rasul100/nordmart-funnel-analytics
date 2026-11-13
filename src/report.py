"""Print every number quoted in README. Run: python src/report.py

Also cross-checks pandas against sql/ so both layers give the same answer.
README must be regenerated from this output after any change to the generator.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from metrics import (  # noqa: E402
    checkout_by_device,
    connect,
    device_comparisons,
    kpis,
    load_frames,
    mobile_fix_impact,
    retention_curve,
    run_sql,
    session_funnel,
    weekly_cohorts,
)


def pp(x: float) -> str:
    return f"{x * 100:+.1f} п.п."


def main() -> None:
    fr = load_frames()
    users, sessions, events = fr["users"], fr["sessions"], fr["events"]
    k = kpis(sessions, events, users)
    print(f"users {k['users']:,} | sessions {k['sessions']:,} | purchases {k['purchases']:,} | "
          f"revenue {k['revenue']:,.0f} ₽")
    print(f"session CR {k['session_cr']:.1%} | AOV {k['aov']:,.0f} ₽ | repeat buyers {k['repeat_buyer_share']:.1%}")

    print("\nCheckout → pay по девайсам (95% Wilson CI):")
    for r in checkout_by_device(sessions, events).itertuples():
        print(f"  {r.device:<8} {r.purchase:>5}/{r.begin_checkout:<5} {r.checkout_to_pay:.1%} "
              f"[{r.ci_lo:.1%} … {r.ci_hi:.1%}]")
    for r in device_comparisons(sessions, events).itertuples():
        print(f"  {r.a} vs {r.b}: {r.rate_a:.1%} vs {r.rate_b:.1%}, Δ {pp(r.diff)} "
              f"[{pp(r.ci[0])} … {pp(r.ci[1])}], p = {r.p_value:.4f}, отн. {r.rate_a / r.rate_b - 1:+.0%}")

    imp = mobile_fix_impact(sessions, events)
    print(f"\nЭффект починки mobile checkout (за {imp['days']} дней, AOV mobile {imp['aov_mobile']:,.0f} ₽):")
    for name, label in (("scenario", f"закрыть {imp['gap_closed']:.0%} разрыва"), ("ceiling", "потолок, 100%")):
        print(f"  {label:<22} +{imp[name + '_orders']:.0f} заказов, +{imp[name + '_revenue']:,.0f} ₽ "
              f"(+{imp[name + '_revenue_share']:.1%} выручки), ≈ +{imp[name + '_revenue_30d']:,.0f} ₽ / 30 дней")

    print("\nКаналы:")
    print(run_sql("03_channels.sql").to_string(index=False))

    print("\nКатегории:")
    cat = session_funnel(sessions, events, ["category"])
    cat["cart_to_checkout"] = cat["begin_checkout"] / cat["add_to_cart"]
    cat["checkout_to_pay"] = cat["purchase"] / cat["begin_checkout"]
    cat["cart_rate"] = cat["add_to_cart"] / cat["view_item"]
    print(cat[["category", "sessions", "cart_rate", "cart_to_checkout", "checkout_to_pay"]].round(3).to_string(index=False))

    coh = weekly_cohorts(users, sessions)
    print("\nRetention по неделям жизни (все когорты, только полные недели):")
    for r in retention_curve(coh).itertuples():
        print(f"  week {r.week_n}: {r.retention:.1%}  (n = {r.cohort_size:,})")
    print("\nКогорты:")
    print(coh.pivot(index="cohort", columns="week_n", values="retention").round(3).to_string())

    # pandas vs SQL
    conn = connect()
    sql_coh = run_sql("02_cohorts.sql", conn)
    conn.close()
    a = coh.assign(cohort=coh["cohort"].dt.strftime("%Y-%m-%d"))[["cohort", "week_n", "users", "cohort_size"]]
    b = sql_coh.rename(columns={"cohort_week": "cohort", "active_users": "users"})[["cohort", "week_n", "users", "cohort_size"]]
    m = a.merge(b, on=["cohort", "week_n"], how="outer", suffixes=("_pd", "_sql"), indicator=True)
    ok = (m["_merge"] == "both").all() and (m["users_pd"] == m["users_sql"]).all() and (
        m["cohort_size_pd"] == m["cohort_size_sql"]).all()
    print(f"\npandas == SQL по когортам: {'да' if ok else 'НЕТ'} ({len(m)} ячеек)")
    if not ok:
        print(m[(m["_merge"] != "both") | (m["users_pd"] != m["users_sql"])].head(20).to_string())

    fun_sql = run_sql("01_funnel.sql").set_index("device")
    fun_pd = checkout_by_device(sessions, events).set_index("device").reindex(fun_sql.index)
    same = (fun_sql["purchase"] == fun_pd["purchase"]).all() and (fun_sql["begin_checkout"] == fun_pd["begin_checkout"]).all()
    print(f"pandas == SQL по воронке: {'да' if same else 'НЕТ'}")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
