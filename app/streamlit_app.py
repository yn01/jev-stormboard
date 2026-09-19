"""jev-stormboard の画面。

    streamlit run app/streamlit_app.py

気象庁の電文が次々に流れる中で、Jev が即座に判断を返す様子を見せる1画面。
判定そのものは app/engine.py のワーカースレッドが進めるので、画面は描画だけを行う。
判定のロジックは core/judge.py にあり、ここには重複させない。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine import INPUT_COST_PER_MTOK, Engine, available_days  # noqa: E402
from app.store import JudgedMessage  # noqa: E402
from core.judge import load_profile  # noqa: E402

JST = timezone(timedelta(hours=9), "JST")

FEATURED_LIMIT = 5  # 「注目の判定」に出すカードの最大数
STREAM_LIMIT = 60  # 「流れる電文」に出す行数

# Choice action の値ごとの色。深刻なほど赤に寄せる
ACTION_COLORS = {
    "通常どおり": "#6b7280",
    "予定変更を検討": "#0369a1",
    "今日中に備える": "#b45309",
    "外出を控える": "#c2410c",
    "早めの避難を検討": "#b91c1c",
}

st.set_page_config(page_title="jev-stormboard", page_icon="🌀", layout="wide")


# ---------------------------------------------------------------- 共有リソース


@st.cache_resource
def get_engine() -> Engine:
    """エンジンはセッションをまたいで1つだけ動かす。"""
    engine = Engine()
    engine.start()
    return engine


@st.cache_resource
def get_profile() -> dict:
    return load_profile()


# ---------------------------------------------------------------- 表示の部品


def jst_time(value: str, fmt: str = "%m-%d %H:%M") -> str:
    """UTC表記の時刻を JST の短い表記にする。"""
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(JST).strftime(fmt)


def action_badge(action: str) -> str:
    color = ACTION_COLORS.get(action, "#6b7280")
    return (
        f'<span style="background:{color};color:#fff;padding:2px 10px;'
        f'border-radius:12px;font-size:0.8rem;white-space:nowrap;">{action}</span>'
    )


def bar(value: float, color: str = "#2563eb", width_pct: float = 100.0) -> str:
    """0〜1 の値を横棒にする。"""
    pct = max(0.0, min(1.0, value)) * 100
    return (
        f'<div style="background:#e5e7eb;border-radius:4px;height:8px;width:{width_pct}%;">'
        f'<div style="background:{color};width:{pct:.1f}%;height:8px;border-radius:4px;"></div>'
        "</div>"
    )


def answer_row(answer) -> str:
    """質問1問ぶんの表示。型ごとに見せ方を変える。"""
    if answer.kind == "noul":
        value = float(answer.value)
        color = "#dc2626" if value >= 0.7 else ("#2563eb" if value >= 0.4 else "#9ca3af")
        return (
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="width:120px;font-size:0.85rem;">{answer.label}</div>'
            f'<div style="flex:1;">{bar(value, color)}</div>'
            f'<div style="width:46px;text-align:right;font-size:0.85rem;">{value:.2f}</div>'
            "</div>"
        )

    if answer.kind == "score":
        value = float(answer.value)
        # criteria は5段階(0〜4)なので、0〜1 に直して棒にする
        ratio = value / 4.0
        color = "#dc2626" if ratio >= 0.7 else ("#2563eb" if ratio >= 0.4 else "#9ca3af")
        return (
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="width:120px;font-size:0.85rem;">{answer.label}</div>'
            f'<div style="flex:1;">{bar(ratio, color)}</div>'
            f'<div style="width:46px;text-align:right;font-size:0.85rem;">{value:.2f}</div>'
            "</div>"
        )

    # choice: 上位2件の選択肢と確率
    tops = answer.top_probabilities(2)
    parts = "　".join(f"{name} {prob:.0%}" for name, prob in tops)
    return (
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
        f'<div style="width:120px;font-size:0.85rem;">{answer.label}</div>'
        f'<div style="flex:1;font-size:0.85rem;">{parts}</div>'
        "</div>"
    )


def render_card(judged: JudgedMessage, noul_threshold: float) -> None:
    """注目の判定1件をカードで出す。"""
    with st.container(border=True):
        left, right = st.columns([5, 1])
        with left:
            st.markdown(
                f"**{judged.title or judged.kind}**　"
                f'<span style="color:#6b7280;font-size:0.85rem;">'
                f"{judged.author}　{jst_time(judged.updated)} JST</span>",
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(action_badge(judged.action), unsafe_allow_html=True)

        if judged.headline:
            st.markdown(
                f'<div style="color:#374151;font-size:0.88rem;margin:4px 0 10px;">'
                f"{judged.headline}</div>",
                unsafe_allow_html=True,
            )

        # 10問の結果。Noul はしきい値で薄くする
        rows: list[str] = []
        for answer in judged.answers:
            if answer.kind == "noul" and answer.certainty < noul_threshold:
                rows.append(f'<div style="opacity:0.35;">{answer_row(answer)}</div>')
            else:
                rows.append(answer_row(answer))
        st.markdown("".join(rows), unsafe_allow_html=True)

        st.caption(
            f"{judged.latency_ms:.0f}ms　{judged.question_count}問　"
            f"入力 {judged.input_tokens or 0:,}tok　{judged.model}"
        )


# ---------------------------------------------------------------- サイドバー


def render_sidebar() -> dict:
    engine = get_engine()
    st.sidebar.header("表示と動作")

    relevance_threshold = st.sidebar.slider(
        "関連度のしきい値",
        min_value=0.0,
        max_value=1.0,
        value=0.5,
        step=0.05,
        help="「この地域に関係する」(Noul)がこの値以上の電文を「注目の判定」に出します。",
    )

    noul_threshold = st.sidebar.slider(
        "Noulのしきい値(確信度)",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.05,
        help=(
            "Noul は値そのものが「真である確率」なので、確信度は 0.5 からの距離 "
            "abs(値-0.5)*2 で測ります。この値に満たない回答は薄く表示します。"
        ),
    )

    st.sidebar.divider()
    mode_label = st.sidebar.radio(
        "動作モード",
        ["ライブ", "リプレイ"],
        help="ライブは新しい電文を待ち受け、リプレイは保存済みの電文を再生します。",
    )

    replay_day, replay_speed = "", 1
    if mode_label == "リプレイ":
        days = available_days()
        replay_day = st.sidebar.selectbox("対象日", days, index=0 if days else None)
        speed_label = st.sidebar.select_slider(
            "再生速度", options=["1倍", "10倍", "60倍"], value="60倍"
        )
        replay_speed = {"1倍": 1, "10倍": 10, "60倍": 60}[speed_label]

    engine.set_mode(
        "replay" if mode_label == "リプレイ" else "live",
        replay_day=replay_day,
        replay_speed=replay_speed,
    )

    st.sidebar.divider()
    # 件数は指標の帯が2秒ごとに更新するので、ここでは出さない(食い違って見えるため)
    st.sidebar.caption(
        "判定結果は `data/judged/` に保存され、同じ電文は二重に判定しません。"
        "リプレイを繰り返しても API のコストは増えません。"
    )
    return {"relevance": relevance_threshold, "noul": noul_threshold}


# ---------------------------------------------------------------- 各セクション


def render_header(profile: dict) -> None:
    st.markdown(
        '<div style="background:#fef3c7;border-left:5px solid #d97706;padding:10px 14px;'
        'border-radius:4px;margin-bottom:10px;">'
        "<b>⚠️ これはデモです。実際の避難判断は、気象庁と自治体の情報に従ってください。</b>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"### 🌀 jev-stormboard　"
        f'<span style="font-size:1rem;color:#6b7280;">'
        f"{profile.get('name','')}（{profile.get('pref','')}{profile.get('city','')}）向けの判定"
        f"</span>",
        unsafe_allow_html=True,
    )


@st.fragment(run_every="2s")
def render_metrics() -> None:
    """指標の帯。2秒ごとに描画だけを更新する。"""
    engine = get_engine()
    m, s = engine.metrics, engine.status

    cols = st.columns(6)
    cols[0].metric("処理した電文", f"{m.total_count:,}件", f"今回 {m.judged_count:,}件")
    cols[1].metric("直近レイテンシ", f"{m.last_latency_ms:.0f}ms")
    cols[2].metric("平均レイテンシ", f"{m.average_latency_ms:.0f}ms")
    cols[3].metric("1件あたりの質問数", f"{m.question_count}問")
    cols[4].metric("累積の入力トークン", f"{m.input_tokens:,}")
    cols[5].metric(
        "推定コスト", f"${m.estimated_cost_usd:.4f}", help=f"入力 ${INPUT_COST_PER_MTOK}/MTok で計算"
    )

    if s.rate_limited:
        st.warning(
            f"APIのレート制限に当たりました。約{s.rate_limit_wait_sec:.0f}秒空けて再試行します"
            "（処理は止まっていません）。"
        )
    if s.error:
        st.error(f"エラー: {s.error}")

    state = "稼働中" if s.running else "停止"
    extra = ""
    if s.mode == "replay" and s.replay_total:
        extra = f"　{s.replay_done}/{s.replay_total}件"
    st.caption(f"{state}｜{s.message}{extra}")


@st.fragment(run_every="2s")
def render_featured(thresholds: dict) -> None:
    """注目の判定。関連度が高いものを新しい順にカードで出す。"""
    engine = get_engine()
    judged_all = engine.store.all()
    featured = [j for j in judged_all if j.relevance >= thresholds["relevance"]][:FEATURED_LIMIT]

    st.subheader("注目の判定")
    st.caption(
        f"「この地域に関係する」が {thresholds['relevance']:.2f} 以上の電文を、新しい順に最大"
        f"{FEATURED_LIMIT}件表示しています。"
    )

    if not featured:
        if judged_all:
            st.info(
                "しきい値を超える電文はまだありません。"
                "サイドバーで関連度のしきい値を下げると表示されます。"
            )
        else:
            st.info("判定を待っています。最初の結果が出るまで少しかかります。")
        return

    for judged in featured:
        render_card(judged, thresholds["noul"])


@st.fragment(run_every="2s")
def render_stream(thresholds: dict) -> None:
    """流れる電文。関連度が低いものも消さず、薄く表示する。"""
    engine = get_engine()
    judged_all = engine.store.all()[:STREAM_LIMIT]

    st.subheader("流れる電文")
    st.caption(
        "判定した電文を新しい順に並べています。関連度が低いものは薄く表示しますが、"
        "消さずに残しています（大量に流れる中から、自分に関係するものだけが浮かび上がる様子を見るため）。"
    )

    if not judged_all:
        st.caption("まだ判定した電文はありません。")
        return

    threshold = thresholds["relevance"]
    rows: list[str] = []
    rows.append(
        '<div style="display:flex;gap:10px;font-size:0.75rem;color:#6b7280;'
        'border-bottom:1px solid #e5e7eb;padding:4px 0;">'
        '<div style="width:90px;">時刻</div>'
        '<div style="flex:2;">種類</div>'
        '<div style="flex:1.4;">発表官署</div>'
        '<div style="width:110px;">関連度</div>'
        '<div style="width:120px;">行動</div>'
        '<div style="width:70px;text-align:right;">レイテンシ</div>'
        "</div>"
    )
    for judged in judged_all:
        low = judged.relevance < threshold
        opacity = "0.32" if low else "1"
        weight = "400" if low else "500"
        color = ACTION_COLORS.get(judged.action, "#6b7280")
        rows.append(
            f'<div style="display:flex;gap:10px;align-items:center;font-size:0.82rem;'
            f'padding:3px 0;border-bottom:1px solid #f3f4f6;opacity:{opacity};'
            f'font-weight:{weight};">'
            f'<div style="width:90px;color:#6b7280;">{jst_time(judged.updated)}</div>'
            f'<div style="flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            f"{judged.kind}</div>"
            f'<div style="flex:1.4;color:#6b7280;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{judged.author}</div>'
            f'<div style="width:110px;display:flex;align-items:center;gap:6px;">'
            f"{bar(judged.relevance, '#2563eb', 60)}"
            f'<span style="font-size:0.78rem;">{judged.relevance:.2f}</span></div>'
            f'<div style="width:120px;color:{color};">{judged.action}</div>'
            f'<div style="width:70px;text-align:right;color:#6b7280;">'
            f"{judged.latency_ms:.0f}ms</div>"
            "</div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


# ---------------------------------------------------------------- 画面


def main() -> None:
    profile = get_profile()
    thresholds = render_sidebar()

    render_header(profile)
    render_metrics()
    st.divider()
    render_featured(thresholds)
    st.divider()
    render_stream(thresholds)


main()
