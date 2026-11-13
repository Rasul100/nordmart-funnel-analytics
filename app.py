from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from generate_data import build, save  # noqa: E402
from metrics import (  # noqa: E402
    FUNNEL_STEPS,
    checkout_by_device,
    device_comparisons,
    kpis,
    load_frames,
    mobile_fix_impact,
    retention_curve,
    session_funnel,
    weekly_cohorts,
)

st.set_page_config(page_title="NordMart — воронка и удержание", layout="wide")


@st.cache_data(show_spinner="Собираю синтетику NordMart…")
def _data():
    db = ROOT / "data" / "nordmart.sqlite"
    if not db.exists():
        save(build())
    return load_frames()


def _money(x: float) -> str:
    return f"{x:,.0f} ₽".replace(",", " ")


_frames = _data()
users, sessions, events = _frames["users"], _frames["sessions"], _frames["events"]
kpi = kpis(sessions, events, users)

st.title("NordMart — воронка, каналы, когорты")
st.markdown(
    "Трафик растёт, выручка отстаёт: нужно найти, **на каком шаге и в каком сегменте** теряем оплату."
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Пользователи", f"{kpi['users']:,}")
c2.metric("Сессии", f"{kpi['sessions']:,}")
c3.metric("CR сессии", f"{kpi['session_cr']:.1%}")
c4.metric("AOV", _money(kpi["aov"]))
c5.metric("Повторные покупатели", f"{kpi['repeat_buyer_share']:.0%}")

overall = session_funnel(sessions, events)
labels = ["Сессия", "Просмотр", "Корзина", "Чекаут", "Оплата"]
values = [int(overall.iloc[0][s]) for s in FUNNEL_STEPS]
fig_funnel = go.Figure(go.Funnel(y=labels, x=values, textinfo="value+percent initial"))
fig_funnel.update_layout(margin=dict(l=20, r=20, t=30, b=20), height=360)
st.plotly_chart(fig_funnel, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader("Просадка на чекауте: mobile vs desktop")
    by_dev = checkout_by_device(sessions, events).sort_values("device")
    fig_d = go.Figure(
        go.Bar(
            x=by_dev["device"],
            y=by_dev["checkout_to_pay"],
            error_y=dict(
                type="data",
                array=by_dev["ci_hi"] - by_dev["checkout_to_pay"],
                arrayminus=by_dev["checkout_to_pay"] - by_dev["ci_lo"],
            ),
            text=by_dev["checkout_to_pay"].map(lambda x: f"{x:.0%}"),
            textposition="inside",
        )
    )
    fig_d.update_layout(
        yaxis_tickformat=".0%", height=340, margin=dict(t=20), yaxis_title="begin_checkout → purchase, 95% CI"
    )
    st.plotly_chart(fig_d, use_container_width=True)
    cmp_ = device_comparisons(sessions, events).set_index("a")
    ia, md = cmp_.loc["ios"], cmp_.loc["mobile"]
    st.info(
        f"Проблема не в iOS, а в **mobile целиком**: iOS и Android не отличаются "
        f"({ia['rate_a']:.0%} vs {ia['rate_b']:.0%}, p = {ia['p_value']:.2f}). "
        f"Mobile платит из чекаута на **{1 - md['rate_a'] / md['rate_b']:.0%} реже desktop** "
        f"(Δ {md['diff'] * 100:+.1f} п.п., 95% CI {md['ci'][0] * 100:+.1f}…{md['ci'][1] * 100:+.1f}). "
        "Гипотеза: длинная форма, нет Apple Pay / Google Pay / сохранённой карты."
    )

with right:
    st.subheader("Качество канала ≠ объём сессий")
    by_ch = session_funnel(sessions, events, ["channel"])
    purch = events[events["event_name"] == "purchase"]
    rev = sessions.merge(purch[["session_id", "revenue"]], on="session_id", how="left")
    rps = rev.groupby("channel")["revenue"].sum() / sessions.groupby("channel").size()
    by_ch["rps"] = by_ch["channel"].map(rps)
    by_ch["session_cr"] = by_ch["purchase"] / by_ch["sessions"]
    fig_c = px.scatter(
        by_ch,
        x="session_cr",
        y="rps",
        size="sessions",
        color="channel",
        hover_name="channel",
        labels={"session_cr": "CR сессии", "rps": "Выручка на сессию, ₽"},
    )
    fig_c.update_layout(xaxis_tickformat=".1%", height=340, margin=dict(t=20))
    st.plotly_chart(fig_c, use_container_width=True)
    st.info(
        "**paid_social** раздувает верх воронки и слабо тащит в корзину. "
        "**email** — меньше сессий, выше повтор и выручка на сессию."
    )

st.subheader("Сколько стоит починить mobile checkout")
imp = mobile_fix_impact(sessions, events)
m1, m2, m3 = st.columns(3)
m1.metric("Mobile чекаутов за период", f"{imp['mobile_checkouts']:,}".replace(",", " "))
def _k(x: float) -> str:
    return f"+{x / 1000:,.0f} тыс. ₽".replace(",", " ")


m2.metric(
    f"Закрыть {imp['gap_closed']:.0%} разрыва, в месяц",
    _k(imp["scenario_revenue_30d"]),
    f"+{imp['scenario_revenue_share']:.1%} выручки",
)
m3.metric("Потолок (как desktop), в месяц", _k(imp["ceiling_revenue_30d"]), f"+{imp['ceiling_revenue_share']:.1%} выручки")
st.caption(
    f"Расчёт: mobile-чекауты × (CR desktop {imp['rate_desktop']:.0%} − CR mobile {imp['rate_mobile']:.0%}) × доля "
    f"закрытого разрыва × AOV mobile {_money(imp['aov_mobile'])}, период {imp['days']} дней → 30 дней. "
    "Это оценка сверху: часть разрыва — разница аудиторий, а не формы оплаты. Проверять A/B-тестом."
)

st.subheader("Когорты: доля пользователей с сессией по неделям жизни")
coh = weekly_cohorts(users, sessions)
curve = retention_curve(coh)
fig_r = px.line(curve, x="week_n", y="retention", markers=True, labels={"week_n": "Неделя жизни", "retention": "Retention"})
fig_r.update_layout(yaxis_tickformat=".0%", height=300, margin=dict(t=20))
st.plotly_chart(fig_r, use_container_width=True)
st.caption(
    "Учитываются только полностью прошедшие недели: у поздних когорт последние недели ещё не наступили, "
    "и без обрезки они дают ложный обвал retention до ~0."
)
if not coh.empty:
    pivot = coh.pivot(index="cohort", columns="week_n", values="retention")
    pivot.index = pd.to_datetime(pivot.index).strftime("%Y-%m-%d")
    fig_h = px.imshow(
        pivot,
        aspect="auto",
        color_continuous_scale="Blues",
        range_color=(0, 0.4),  # week 0 is always 100%; cap the scale so later weeks stay readable
        labels={"x": "Неделя жизни", "y": "Когорта", "color": "Retention"},
        text_auto=".0%",
    )
    fig_h.update_layout(height=380, margin=dict(t=20))
    st.plotly_chart(fig_h, use_container_width=True)

st.subheader("Категории: где бросают корзину")
by_cat = session_funnel(sessions, events, ["category"])
by_cat["cart_to_checkout"] = by_cat["begin_checkout"] / by_cat["add_to_cart"].replace(0, pd.NA)
by_cat["session_cr"] = by_cat["purchase"] / by_cat["sessions"]
st.dataframe(
    by_cat[["category", "sessions", "add_to_cart", "begin_checkout", "purchase", "cart_to_checkout", "session_cr"]]
    .assign(
        cart_to_checkout=lambda d: d["cart_to_checkout"].map(lambda x: f"{x:.0%}" if pd.notna(x) else "—"),
        session_cr=lambda d: d["session_cr"].map(lambda x: f"{x:.1%}"),
    )
    .sort_values("category"),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Вывод")
st.markdown(
    f"""
**Что сделал.** Разобрал воронку интернет-магазина — от просмотра товара до оплаты — по устройствам, каналам и категориям.

**Что понял.** Трафика достаточно, но на телефоне — и на iOS, и на Android одинаково — люди доходят до оплаты и не платят
заметно реже, чем на desktop. Закрыть хотя бы половину этого разрыва — это порядка **{_k(imp['scenario_revenue_30d'])}
в месяц** (оценка сверху). Реклама в соцсетях (paid social) даёт много просмотров, но почти никто не кладёт товар в корзину.
Электронику часто добавляют в корзину, но реже доводят до оформления заказа, чем остальные категории.
Удержание обычное для e-commerce: на второй неделе возвращается около трети пользователей, дальше доля плавно падает.

**Что делать.** Сначала упростить оплату на телефоне — форма в один экран, Apple Pay / Google Pay, сохранённая карта —
и проверить это A/B-тестом с метрикой checkout → pay на mobile.
Оценивать рекламные каналы не по числу сессий, а по тому, доходят ли они до корзины и покупки. На карточке электроники
показывать полную стоимость и доставку сразу, а не в конце, чтобы меньше отваливались на чекауте.
"""
)
