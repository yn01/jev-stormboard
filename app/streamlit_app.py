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
from app.geo import build_map_data  # noqa: E402
from app.mapview import (  # noqa: E402
    PLOTLY_AVAILABLE,
    build_figure,
    caption as map_caption,
    top_prefectures,
)
from app.store import JudgedMessage, profile_fingerprint  # noqa: E402
from core.judge import load_profile  # noqa: E402
from core.questions import QUESTION_SET_SIZES  # noqa: E402

JST = timezone(timedelta(hours=9), "JST")

FEATURED_LIMIT = 5  # 「注目の判定」に出すカードの最大数
STREAM_LIMIT = 60  # 「流れる電文」に出す行数

# Choice action の値ごとの色。深刻なほど赤に寄せる
ACTION_COLORS = {
    "通常どおり": "#15803d",
    "予定変更を検討": "#a16207",
    "今日中に備える": "#c2410c",
    "外出を控える": "#dc2626",
    "早めの避難を検討": "#991b1b",
}

# 結論バナーの背景。行動が重くなるほど赤に寄せる
ACTION_BANNER = {
    "通常どおり": ("#dcfce7", "#15803d", "#166534"),
    "予定変更を検討": ("#fef9c3", "#ca8a04", "#854d0e"),
    "今日中に備える": ("#ffedd5", "#ea580c", "#9a3412"),
    "外出を控える": ("#fee2e2", "#dc2626", "#991b1b"),
    "早めの避難を検討": ("#7f1d1d", "#450a0a", "#ffffff"),
}
BANNER_NONE = ("#f3f4f6", "#d1d5db", "#6b7280")

st.set_page_config(page_title="jev-stormboard", page_icon="🌀", layout="wide")


# ---------------------------------------------------------------- 共有リソース


@st.cache_resource
def get_engine() -> Engine:
    """エンジンはセッションをまたいで1つだけ動かす。"""
    engine = Engine()
    engine.start()
    return engine


def get_profile() -> dict:
    """profile.yaml を読む。

    キャッシュしない。画面を再読み込みしたときに、書き換えた内容が
    そのまま反映されるようにするため(小さな YAML なので毎回読んでよい)。
    """
    return load_profile()


# ---------------------------------------------------------------- 表示の部品


def visible_judgements(thresholds: dict) -> list[JudgedMessage]:
    """いま画面に出すべき判定結果。

    リプレイ中は、**再生位置までに発表された電文だけ**を見せる。そうしないと、
    朝を再生していても最新(夜)の電文が先頭に出続けてしまい、再生している様子が
    画面に出ない。
    """
    engine = get_engine()
    judged = engine.store.all(thresholds["question_count"])

    position = engine.status.replay_position
    if engine.status.mode == "replay" and position:
        judged = [j for j in judged if j.updated <= position]
    return judged


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
            f'<div style="width:62px;text-align:right;font-size:0.85rem;'
            f'font-variant-numeric:tabular-nums;">{value:.2f}</div>'
            "</div>"
        )

    if answer.kind == "score":
        value = float(answer.value)
        # 目盛りの範囲に対する比率でバーを描き、値にも「2.82 / 4」と範囲を併記する。
        # 範囲が分からないと、数字もバーの長さも意味を持たないため。
        ratio = answer.scale_ratio()
        top = answer.scale_max
        color = "#dc2626" if ratio >= 0.7 else ("#2563eb" if ratio >= 0.4 else "#9ca3af")
        shown = f"{value:.2f} / {top}" if top is not None else f"{value:.2f}"
        return (
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="width:120px;font-size:0.85rem;">{answer.label}</div>'
            f'<div style="flex:1;">{bar(ratio, color)}</div>'
            f'<div style="width:62px;text-align:right;font-size:0.85rem;'
            f'font-variant-numeric:tabular-nums;">{shown}</div>'
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
            + (f"　プロファイル {judged.profile_fingerprint}" if judged.profile_fingerprint else "")
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
        "判断がついた回答だけを濃く",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.05,
        help=(
            "Noul の値は「命題が真である確率」そのものなので、0.5 に近いほど"
            "「どちらとも言えない」という意味になります。確信度は 0.5 からの距離 "
            "abs(値-0.5)*2 で測り、この値に満たない回答をカード内で薄く表示します。"
            "質問数を増やすと曖昧な回答も増えるので、上げると読みやすくなります。"
        ),
    )
    st.sidebar.caption(
        "↑ カード内で、0.5 付近(どちらとも言えない)の Noul を薄くします。"
        "0 のままなら全部そのまま表示します。"
    )

    st.sidebar.checkbox(
        "関連度マップを表示",
        value=True,
        key="show_map",
        help="地図の描画が重い場合は外してください。本番当日の動作の確実性を優先するためのスイッチです。",
    )

    st.sidebar.divider()
    question_count = st.sidebar.radio(
        "質問数",
        QUESTION_SET_SIZES,
        format_func=lambda n: f"{n}問",
        horizontal=True,
        help=(
            "1回のリクエストで送る質問の数です。切り替えると、その質問数で判定し直します。"
            "質問数を増やしてもレイテンシがほとんど変わらないことを確かめてください。"
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
        question_count=question_count,
    )

    st.sidebar.divider()
    # 件数は指標の帯が2秒ごとに更新するので、ここでは出さない(食い違って見えるため)
    st.sidebar.caption(
        "判定結果は `data/judged/` に保存され、同じ電文は二重に判定しません。"
        "リプレイを繰り返しても API のコストは増えません。"
    )
    return {
        "relevance": relevance_threshold,
        "noul": noul_threshold,
        "question_count": question_count,
    }


# ---------------------------------------------------------------- 各セクション


def render_header(profile: dict) -> None:
    where = f"{profile.get('pref', '')}{profile.get('city', '')}" or "地域未設定"
    source = profile.get("_source", "profile.yaml")
    st.markdown(
        f"### 🌀 jev-stormboard　"
        f'<span style="font-size:1rem;color:#6b7280;">'
        f"{profile.get('name', '')}（{where}）向けの判定"
        f"</span>　"
        f'<span style="font-size:0.75rem;color:#9ca3af;">読み込み元: {source}</span>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every="2s")
def render_mode_bar() -> None:
    """モードの常時表示と操作ボタン。結論バナーのすぐ上に置く。

    本番中にライブかリプレイかを迷わないよう、本文の上部にも出す。
    リプレイの再生位置(時刻)が進むので、2秒ごとに描き直す。
    """
    engine = get_engine()
    status = engine.status

    left, right = st.columns([3, 2])

    with left:
        if status.mode == "replay":
            state = "一時停止中" if status.paused else f"{status.replay_speed}倍速で再生中"
            progress = (
                f"　{status.replay_done:,}/{status.replay_total:,}件"
                if status.replay_total
                else ""
            )
            clock = f"　⏱ {status.replay_position_jst}" if status.replay_position_jst else ""
            st.markdown(
                '<div style="display:flex;align-items:center;gap:10px;padding:6px 12px;'
                'background:#ede9fe;border:1px solid #7c3aed;border-radius:20px;'
                'display:inline-flex;">'
                '<span style="width:10px;height:10px;border-radius:50%;background:#7c3aed;'
                'display:inline-block;"></span>'
                '<span style="font-weight:700;color:#5b21b6;">リプレイ</span>'
                f'<span style="color:#5b21b6;font-size:0.85rem;">'
                f"{status.replay_day}　{state}{clock}{progress}</span>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            since = status.seconds_since_last_judge()
            if since is None:
                elapsed = "まだ判定していません"
            elif since < 60:
                elapsed = f"最終判定 {since:.0f}秒前"
            else:
                elapsed = f"最終判定 {since / 60:.0f}分前"
            st.markdown(
                "<style>@keyframes blink{0%,100%{opacity:1}50%{opacity:0.25}}</style>"
                '<div style="display:inline-flex;align-items:center;gap:10px;padding:6px 12px;'
                'background:#dcfce7;border:1px solid #16a34a;border-radius:20px;">'
                '<span style="width:10px;height:10px;border-radius:50%;background:#16a34a;'
                'display:inline-block;animation:blink 1.4s infinite;"></span>'
                '<span style="font-weight:700;color:#15803d;">ライブ</span>'
                f'<span style="color:#15803d;font-size:0.85rem;">{elapsed}</span>'
                "</div>",
                unsafe_allow_html=True,
            )

    with right:
        if status.mode == "replay":
            cols = st.columns(3)
            if status.paused:
                if cols[0].button("▶ 再生", use_container_width=True):
                    engine.resume()
                    st.rerun(scope="app")
            else:
                if cols[0].button("⏸ 一時停止", use_container_width=True):
                    engine.pause()
                    st.rerun(scope="app")
            if cols[1].button("⏮ 最初から", use_container_width=True):
                engine.restart()
                st.rerun(scope="app")
            if cols[2].button("↻ 更新", use_container_width=True):
                st.rerun(scope="app")
        else:
            if st.button("↻ いますぐ確認", use_container_width=True):
                engine.refresh_now()
                st.rerun(scope="app")


def _supplement(judged: JudgedMessage, key: str) -> str:
    """補足指標1つぶんの小さなカード。"""
    answer = judged.answer(key) if judged else None
    if answer is None:
        return (
            '<div style="flex:1;padding:8px 12px;background:#f9fafb;border-radius:6px;">'
            '<div style="font-size:0.75rem;color:#6b7280;">--</div>'
            '<div style="font-size:1.1rem;color:#9ca3af;">--</div></div>'
        )

    ratio = answer.scale_ratio()
    if answer.kind == "score":
        shown = f"{float(answer.value):.2f} / {answer.scale_max}"
    else:
        shown = f"{float(answer.value):.2f}"
    color = "#dc2626" if ratio >= 0.7 else ("#2563eb" if ratio >= 0.4 else "#6b7280")
    return (
        '<div style="flex:1;padding:8px 12px;background:#f9fafb;border-radius:6px;">'
        f'<div style="font-size:0.75rem;color:#6b7280;">{answer.label}</div>'
        f'<div style="font-size:1.25rem;font-weight:600;color:{color};'
        f'font-variant-numeric:tabular-nums;">{shown}</div>'
        f'<div style="margin-top:4px;">{bar(ratio, color)}</div>'
        "</div>"
    )


@st.fragment(run_every="2s")
def render_conclusion(thresholds: dict) -> None:
    """第1層: いま取るべき行動を1つだけ、大きく出す。

    画面を開いた瞬間に結論が分かるようにするための、この画面の主役。
    """
    judged_all = visible_judgements(thresholds)
    relevant = [j for j in judged_all if j.relevance >= thresholds["relevance"]]
    top = relevant[0] if relevant else None

    if top is None:
        background, border, text = BANNER_NONE
        headline = "関係する情報はまだありません"
        sub = (
            f"「この地域に関係する」が {thresholds['relevance']:.2f} 以上の電文が"
            "まだ判定されていません。"
        )
    else:
        background, border, text = ACTION_BANNER.get(top.action, BANNER_NONE)
        headline = top.action or "判定中"
        sub = (
            f"{top.title or top.kind}　|　{top.author}　|　"
            f"{jst_time(top.updated, '%m月%d日 %H:%M')} JST"
        )

    st.markdown(
        f'<div style="background:{background};border:2px solid {border};'
        f'border-radius:10px;padding:16px 22px;margin:6px 0 10px;">'
        f'<div style="font-size:0.78rem;color:{text};opacity:0.8;letter-spacing:0.08em;">'
        f"いま取るべき行動</div>"
        f'<div style="font-size:2rem;font-weight:700;line-height:1.25;color:{text};'
        f'margin-top:2px;">{headline}</div>'
        f'<div style="font-size:0.82rem;color:{text};opacity:0.85;margin-top:6px;">'
        f"{sub}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # 第2層: 補足指標を3つだけ
    parts = "".join(_supplement(top, key) for key in ("imminent", "severity", "impact"))
    st.markdown(
        f'<div style="display:flex;gap:10px;margin-bottom:6px;">{parts}</div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every="2s")
def render_metrics(profile: dict) -> None:
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

    # 質問数を増やしてもレイテンシがほとんど変わらないことを、その場で見比べられるように
    by_count = engine.store.average_latency_by_count()
    if by_count:
        parts = []
        for count, (average, n) in by_count.items():
            mark = "**" if count == m.question_count else ""
            parts.append(f"{mark}{count}問 {average:.0f}ms{mark}（{n:,}件）")
        st.caption("質問数別の平均レイテンシ:　" + "　/　".join(parts))

    render_profile_notice(profile, engine)

    if s.rate_limited:
        st.warning(
            f"APIのレート制限に当たりました。約{s.rate_limit_wait_sec:.0f}秒空けて再試行します"
            "（処理は止まっていません）。"
        )
    if s.error:
        st.error(f"エラー: {s.error}")

    state = "稼働中" if s.running else "停止"
    source = s.profile_source or "profile.yaml"
    st.caption(
        f"{state}｜{s.message}　|　プロファイル: `{source}`"
        f"（{s.profile_name}・指紋 `{s.profile_fingerprint}`）"
    )


def render_profile_notice(profile: dict, engine: Engine) -> None:
    """いま表示している判定が、どのプロファイルで出たものかを知らせる。

    profile.yaml を書き換えると判定の前提が変わるので、過去の判定結果と
    食い違っていることを画面に出す。
    """
    current = profile_fingerprint(profile)
    stored = engine.store.profile_fingerprints()

    legacy = not stored  # プロファイルを記録する前に判定したぶん
    others = {f for f in stored if f != current}

    if not others and not legacy:
        return

    lines = []
    if others:
        lines.append(
            f"**別のプロファイルで判定された結果が混ざっています。**"
            f"　いまの profile.yaml（{profile.get('name','')}・指紋 `{current}`）とは"
            f"前提が異なる判定が {len(others)} 種類あります。"
        )
    if legacy:
        lines.append(
            "**プロファイルを記録する前に判定した結果が含まれています。**"
            "　どのプロファイルで判定したかは分かりません。"
        )
    lines.append(
        "新しい判定は、いまの profile.yaml で行われます。"
        "過去のぶんを揃えたい場合は `data/judged/judgements.jsonl` を空にしてください。"
    )
    st.warning("\n\n".join(lines))


@st.fragment(run_every="6s")
def render_map(thresholds: dict, profile: dict) -> None:
    """関連度のヒートマップ。

    地図は1回あたりの転送量が大きい(GeoJSON を含めて約120KB)ので、
    ほかの部分より更新間隔を長くする。
    """
    map_data = build_map_data(visible_judgements(thresholds))

    if not PLOTLY_AVAILABLE:
        st.warning(
            "地図の描画に必要な `plotly` が入っていないため、関連度マップは表示できません。"
            "画面のほかの部分は動いています。\n\n"
            "プロジェクトの仮想環境で起動しているか確認してください:\n"
            "```\ncd ~/Dev/jev-stormboard\nsource .venv/bin/activate\n"
            "pip install -r requirements.txt\nstreamlit run app/streamlit_app.py\n```"
        )
        # 地図が出せなくても、関連度の高い地域は数字で見せる
        tops = top_prefectures(map_data)
        if tops:
            st.caption("関連度の高い地域: " + "　".join(f"{s.name} {s.relevance:.2f}" for s in tops))
        return

    left, right = st.columns([2.4, 1])
    with left:
        st.plotly_chart(
            build_figure(map_data, profile),
            use_container_width=True,
            config={"displayModeBar": False, "scrollZoom": False},
            key=f"relevance-map-{thresholds['question_count']}",
        )
    with right:
        st.markdown(
            '<div style="font-size:0.8rem;color:#6b7280;margin-bottom:6px;">'
            "関連度の高い地域</div>",
            unsafe_allow_html=True,
        )
        tops = top_prefectures(map_data)
        if not tops:
            st.caption("まだ判定した電文がありません。")
        rows = []
        for stat in tops:
            color = (
                "#b91c1c" if stat.relevance >= 0.75
                else "#ea580c" if stat.relevance >= 0.5
                else "#9ca3af"
            )
            rows.append(
                '<div style="display:flex;align-items:center;gap:8px;margin:5px 0;">'
                f'<div style="width:62px;font-size:0.85rem;">{stat.name}</div>'
                f'<div style="flex:1;">{bar(stat.relevance, color)}</div>'
                f'<div style="width:38px;text-align:right;font-size:0.85rem;'
                f'font-variant-numeric:tabular-nums;color:{color};">'
                f"{stat.relevance:.2f}</div>"
                "</div>"
            )
        st.markdown("".join(rows), unsafe_allow_html=True)
        if tops:
            st.caption(
                f"{tops[0].name}が最も高いのは、"
                f"「{tops[0].top_title[:22]}」の判定によるものです。"
            )

    st.caption(map_caption(map_data))


@st.fragment(run_every="2s")
def render_featured(thresholds: dict) -> None:
    """注目の判定。関連度が高いものを新しい順にカードで出す。"""
    # いま選んでいる質問数で判定したものだけを見せる(質問数が違えば別の結果)
    judged_all = visible_judgements(thresholds)
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
            st.info(
                f"{thresholds['question_count']}問での判定を待っています。"
                "最初の結果が出るまで少しかかります。"
            )
        return

    for judged in featured:
        render_card(judged, thresholds["noul"])


@st.fragment(run_every="2s")
def render_stream(thresholds: dict) -> None:
    """流れる電文。関連度が低いものも消さず、薄く表示する。"""
    judged_all = visible_judgements(thresholds)[:STREAM_LIMIT]

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
    engine = get_engine()

    # リプレイ中は背景の色味を変えて、ライブと見間違えないようにする
    if engine.status.mode == "replay":
        st.markdown(
            "<style>"
            '[data-testid="stAppViewContainer"]{background:'
            "linear-gradient(#faf5ff,#ffffff 160px);}"
            '[data-testid="stAppViewContainer"]::before{content:"";position:fixed;'
            "top:0;left:0;right:0;height:5px;background:#7c3aed;z-index:999;}"
            '[data-testid="stAppViewContainer"]::after{content:"";position:fixed;'
            "bottom:0;left:0;right:0;height:5px;background:#7c3aed;z-index:999;}"
            "</style>",
            unsafe_allow_html=True,
        )

    # 第1層: 注意書き → モード → 結論
    render_header(profile)
    render_mode_bar()
    render_conclusion(thresholds)

    st.divider()
    # 第1.5層: 全国に流れる電文の中で、自分に関係するところだけが濃くなる地図。
    # 重い場合に備えて折りたたみにできるが、主役なので初期状態は開いておく。
    if st.session_state.get("show_map", True):
        st.subheader("関連度マップ")
        render_map(thresholds, profile)
    else:
        st.caption("関連度マップは非表示です（サイドバーで表示できます）。")

    st.divider()
    render_featured(thresholds)
    st.divider()
    render_stream(thresholds)

    # 第3層: Jev の性能を示す数字。状況ではないので下に置く。
    # ただしデモの主張として重要なので、初期状態は開いておく。
    st.divider()
    with st.expander("Jev の性能（処理件数・レイテンシ・トークン・コスト）", expanded=True):
        render_metrics(profile)


main()
