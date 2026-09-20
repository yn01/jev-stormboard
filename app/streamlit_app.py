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
    MAP_STYLE,
    build_svg,
    caption as map_caption,
    kanto_prefectures,
    legend_html,
)
from app.store import JudgedMessage, profile_fingerprint  # noqa: E402
from core.judge import load_profile  # noqa: E402
from core.questions import QUESTION_SET_SIZES  # noqa: E402

JST = timezone(timedelta(hours=9), "JST")

# リプレイの開始時刻の選択肢(30分刻み)
REPLAY_START_TIMES: list[str] = [
    f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)
]

FEATURED_LIMIT = 5  # 「注目の判定」に出すカードの最大数
STREAM_LIMIT = 60  # 「流れる電文」に出す行数

# Choice action の値ごとの色。深刻なほど赤に寄せる
# 画面に出す行動の文言。(呼びかけ, 副題)。
#
# **Jev に渡す選択肢の名前(core/questions.py の criteria のキー)は変えないこと。**
# 名前を変えると、保存済みの判定結果と食い違って色が付かなくなり、
# 揃えるには全件を判定し直すことになる。表示だけをここで差し替える。
#
# 副題は、内閣府「避難情報に関するガイドライン」の警戒レベルに**対応する段階**を
# 目安として示したもの。**「相当」を必ず付けること。**
# 警戒レベルは地域の状況に対して行政と気象庁が出すもので、ここで出しているのは
# 「この人の事情を踏まえた個人の行動」なので、別物である。言い切ると
# 避難指示などの発令と取り違えられる。
ACTION_LABELS = {
    "通常どおり": ("いつもどおりで", "平常の行動"),
    "予定変更を検討": ("予定の見直しを", "警戒レベル1 相当"),
    "今日中に備える": ("早めの備えを", "警戒レベル2 相当"),
    "外出を控える": ("外出は控えて", "警戒レベル3 相当"),
    "早めの避難を検討": ("早めの避難を", "警戒レベル4 相当"),
}

# 警戒レベルの副題を出すときに添える注記
ALERT_LEVEL_NOTE = "気象庁・自治体が出す警戒レベルの目安です。発令そのものではありません。"


def action_label(action: str) -> tuple[str, str]:
    """行動の表示名(呼びかけ, 副題)。知らない値はそのまま返す。"""
    return ACTION_LABELS.get(action, (action or "判定中", ""))


# 暗い背景の上で読める色。行動が重くなるほど赤に寄せる
ACTION_COLORS = {
    "通常どおり": "#4ade80",
    "予定変更を検討": "#facc15",
    "今日中に備える": "#fb923c",
    "外出を控える": "#f87171",
    "早めの避難を検討": "#ef4444",
}

# 結論バナー: (背景, 枠線, 文字)
ACTION_BANNER = {
    "通常どおり": ("rgba(34,197,94,.14)", "#22c55e", "#86efac"),
    "予定変更を検討": ("rgba(234,179,8,.14)", "#eab308", "#fde047"),
    "今日中に備える": ("rgba(249,115,22,.16)", "#f97316", "#fdba74"),
    "外出を控える": ("rgba(239,68,68,.16)", "#ef4444", "#fca5a5"),
    "早めの避難を検討": ("rgba(239,68,68,.30)", "#f87171", "#fecaca"),
}
BANNER_NONE = ("rgba(148,163,184,.10)", "#334155", "#94a3b8")

# 画面共通の色
INK = "#e2e8f0"      # 本文
MUTED = "#94a3b8"    # 補足
LINE = "#1e293b"     # 罫線
PANEL = "#121c2e"    # カード背景
TRACK = "#1e293b"    # バーの下地

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
    color = ACTION_COLORS.get(action, MUTED)
    label, _ = action_label(action)
    return (
        f'<span style="background:{color};color:#0b1220;padding:2px 10px;'
        f'border-radius:12px;font-size:0.8rem;font-weight:700;'
        f'white-space:nowrap;">{label}</span>'
    )


def bar(value: float, color: str = "#38bdf8", width_pct: float = 100.0) -> str:
    """0〜1 の値を横棒にする。"""
    pct = max(0.0, min(1.0, value)) * 100
    return (
        f'<div style="background:{TRACK};border-radius:4px;height:8px;width:{width_pct}%;">'
        f'<div style="background:{color};width:{pct:.1f}%;height:8px;border-radius:4px;'
        f'transition:width .6s ease;"></div></div>'
    )


def answer_row(answer) -> str:
    """質問1問ぶんの表示。型ごとに見せ方を変える。"""
    if answer.kind == "noul":
        value = float(answer.value)
        color = "#f87171" if value >= 0.7 else ("#38bdf8" if value >= 0.4 else MUTED)
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
        color = "#f87171" if ratio >= 0.7 else ("#38bdf8" if ratio >= 0.4 else MUTED)
        shown = f"{value:.2f} / {top}" if top is not None else f"{value:.2f}"
        return (
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="width:120px;font-size:0.85rem;">{answer.label}</div>'
            f'<div style="flex:1;">{bar(ratio, color)}</div>'
            f'<div style="width:62px;text-align:right;font-size:0.85rem;'
            f'font-variant-numeric:tabular-nums;">{shown}</div>'
            "</div>"
        )

    # choice: 上位2件の選択肢と確率。行動は画面用の文言に直す
    tops = answer.top_probabilities(2)
    parts = "　".join(
        f"{action_label(name)[0] if answer.key == 'action' else name} {prob:.0%}"
        for name, prob in tops
    )
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
                f'<span style="color:{MUTED};font-size:0.85rem;">'
                f"{judged.author}　{jst_time(judged.updated)} JST</span>",
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(action_badge(judged.action), unsafe_allow_html=True)

        if judged.headline:
            st.markdown(
                f'<div style="color:#cbd5e1;font-size:0.88rem;margin:4px 0 10px;">'
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

    replay_day, replay_speed, replay_start = "", 1, ""
    if mode_label == "リプレイ":
        days = available_days()
        replay_day = st.sidebar.selectbox("対象日", days, index=0 if days else None)

        # その日のどこから再生を始めるか。30分刻み
        replay_start = st.sidebar.select_slider(
            "開始時刻",
            options=REPLAY_START_TIMES,
            value="00:00",
            help=(
                "この時刻より前の電文は飛ばして再生します。"
                "画面には、その時刻までに届いた電文も含めて表示されます"
                "（その時刻時点の状況から始まる、という見え方になります）。"
            ),
        )

        speed_label = st.sidebar.select_slider(
            "再生速度", options=["1倍", "10倍", "60倍", "100倍"], value="60倍"
        )
        replay_speed = {"1倍": 1, "10倍": 10, "60倍": 60, "100倍": 100}[speed_label]

    engine.set_mode(
        "replay" if mode_label == "リプレイ" else "live",
        replay_day=replay_day,
        replay_speed=replay_speed,
        question_count=question_count,
        replay_start=replay_start,
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


def header_html(profile: dict) -> str:
    """タイトルとプロファイル。1行に収める。

    読み込み元は、自分用の profile.local.yaml を使っているときだけ出す。
    公開用の profile.yaml のときは既定なので、出すと横幅を食うだけになる。
    """
    local = (
        f'<span style="font-size:0.7rem;color:#38bdf8;opacity:.85;">'
        f"{profile.get('_source', '')}</span>"
        if profile.get("_is_local")
        else ""
    )
    return (
        '<div style="display:flex;align-items:baseline;gap:8px;white-space:nowrap;'
        'overflow:hidden;text-overflow:ellipsis;">'
        '<span style="font-size:1.2rem;font-weight:700;">🌀 jev-stormboard</span>'
        f"{local}"
        "</div>"
    )


def render_mode_bar(profile: dict) -> None:
    """タイトル・プロファイル・モード・操作ボタンを1行にまとめた帯。

    本番中にライブかリプレイかを迷わないよう、本文の上部に出す。
    縦を詰めて、関連度マップがスクロールなしで見えるようにする。
    """
    engine = get_engine()
    status = engine.status

    # タイトル / モードバッジ / 操作ボタン を横一列に。
    # バッジを結論バナーの真上に積むと、左側に要素が固まって見えるため。
    left, middle, right = st.columns([2.3, 1.8, 1.9])

    with left:
        st.markdown(header_html(profile), unsafe_allow_html=True)

    with middle:
        if status.mode == "replay":
            state = "一時停止" if status.paused else f"{status.replay_speed}倍"
            # 件数まで入れると横に収まらないので、状態と時刻だけにする
            progress = ""
            # 「09/20 00:01」の時刻部分だけ。日付はバッジの先頭に出ている
            position = status.replay_position_jst
            clock = f"　⏱ {position.split(' ')[-1]}" if position else ""
            st.markdown(
                '<div class="jev-modebar">'
                '<span class="jev-badge" style="background:rgba(124,58,237,.18);'
                'border-color:#a78bfa;">'
                '<span class="dot" style="background:#a78bfa;"></span>'
                '<b style="color:#c4b5fd;">リプレイ</b>'
                f'<span style="color:#c4b5fd;font-size:0.78rem;">'
                f"{status.replay_day[5:]}　{state}{clock}{progress}</span>"
                "</span></div>",
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
                '<div class="jev-modebar">'
                '<span class="jev-badge" style="background:rgba(34,197,94,.16);'
                'border-color:#4ade80;">'
                '<span class="dot" style="background:#4ade80;'
                'animation:blink 1.4s infinite;"></span>'
                '<b style="color:#86efac;">ライブ</b>'
                f'<span style="color:#86efac;font-size:0.82rem;">{elapsed}</span>'
                "</span></div>",
                unsafe_allow_html=True,
            )

    with right:
        # ボタンは常に3つ置く。モードで数が変わると、部分更新の差分が
        # 画面の要素の並びと合わなくなって描画が壊れるため(Bad delta path index)。
        replay = status.mode == "replay"
        cols = st.columns(3)

        play_label = "▶ 再生" if status.paused else "⏸ 停止"
        if cols[0].button(
            play_label if replay else "―",
            use_container_width=True,
            disabled=not replay,
            key="btn_play",
        ):
            engine.resume() if status.paused else engine.pause()

        if cols[1].button(
            "⏮ 先頭" if replay else "―",
            use_container_width=True,
            disabled=not replay,
            key="btn_restart",
        ):
            engine.restart()

        if cols[2].button(
            "↻ 更新" if replay else "↻ 確認",
            use_container_width=True,
            key="btn_refresh",
        ):
            if not replay:
                engine.refresh_now()


def _supplement(judged: JudgedMessage, key: str) -> str:
    """補足指標1つぶんの小さなカード。"""
    answer = judged.answer(key) if judged else None
    if answer is None:
        return (
            f'<div style="flex:1;padding:10px 12px;background:{PANEL};border-radius:10px;'
            f'border:1px solid {LINE};display:flex;flex-direction:column;'
            f'justify-content:center;">'
            f'<div style="font-size:0.72rem;color:{MUTED};">--</div>'
            f'<div style="font-size:1.3rem;color:{MUTED};">--</div></div>'
        )

    ratio = answer.scale_ratio()
    if answer.kind == "score":
        shown = f"{float(answer.value):.2f} / {answer.scale_max}"
    else:
        shown = f"{float(answer.value):.2f}"
    color = "#f87171" if ratio >= 0.7 else ("#38bdf8" if ratio >= 0.4 else MUTED)
    return (
        f'<div style="flex:1;padding:10px 12px;background:{PANEL};border-radius:10px;'
        f'border:1px solid {LINE};display:flex;flex-direction:column;'
        f'justify-content:center;">'
        f'<div style="font-size:0.72rem;color:{MUTED};">{answer.label}</div>'
        f'<div style="font-size:1.3rem;font-weight:700;color:{color};'
        f'font-variant-numeric:tabular-nums;line-height:1.2;">{shown}</div>'
        f'<div style="margin-top:6px;">{bar(ratio, color)}</div>'
        "</div>"
    )


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
        headline_sub = ""
        sub = (
            f"「この地域に関係する」が {thresholds['relevance']:.2f} 以上の電文が"
            "まだ判定されていません。"
        )
    else:
        background, border, text = ACTION_BANNER.get(top.action, BANNER_NONE)
        headline, headline_sub = action_label(top.action)
        sub = (
            f"{top.title or top.kind}　|　{top.author}　|　"
            f"{jst_time(top.updated, '%m月%d日 %H:%M')} JST"
        )

    # 副題(警戒レベル相当)。該当が無いときは出さない
    subtitle = (
        f'<div style="font-size:0.72rem;font-weight:600;letter-spacing:0.04em;'
        f'color:{text};opacity:0.62;margin-top:3px;" title="{ALERT_LEVEL_NOTE}">'
        f"{headline_sub}</div>"
        if headline_sub
        else ""
    )

    # 結論と補足指標を横一列に並べる。結論の右が空くのを避け、
    # 関連度マップをスクロールなしで見える位置まで押し上げるため。
    conclusion = (
        f'<div style="flex:2.3;background:{background};border:2px solid {border};'
        f'border-radius:10px;padding:12px 18px;display:flex;flex-direction:column;'
        f'justify-content:center;">'
        f'<div style="font-size:0.72rem;color:{text};opacity:0.8;letter-spacing:0.08em;">'
        f"いま取るべき行動</div>"
        f'<div style="font-size:1.7rem;font-weight:700;line-height:1.2;color:{text};'
        f'margin-top:2px;">{headline}</div>'
        f"{subtitle}"
        f'<div style="font-size:0.75rem;color:{text};opacity:0.85;margin-top:4px;'
        f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{sub}</div>'
        "</div>"
    )
    supplements = "".join(_supplement(top, key) for key in ("imminent", "severity", "impact"))

    st.markdown(
        f'<div style="display:flex;gap:10px;align-items:stretch;margin:2px 0 14px;">'
        f"{conclusion}{supplements}</div>",
        unsafe_allow_html=True,
    )


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


def render_ticker(thresholds: dict) -> None:
    """判定した電文を1行で流す速報欄。

    結論バナーと地図の間に置く。見出しは付けず、いちばん新しい判定を
    1行だけ出す。更新のたびに中身が入れ替わるので、電文が次々に
    判定されていく様子がそのまま伝わる。
    """
    judged_all = visible_judgements(thresholds)
    if not judged_all:
        st.markdown(
            '<div class="jev-ticker empty">判定を待っています…</div>',
            unsafe_allow_html=True,
        )
        return

    judged = judged_all[0]
    relevant = judged.relevance >= thresholds["relevance"]
    action_color = ACTION_COLORS.get(judged.action, MUTED)
    # 関連度の色は地図と揃える(青紫系)
    relevance_color = (
        "#d946ef" if judged.relevance >= 0.75
        else "#7c3aed" if judged.relevance >= 0.5
        else "#64748b"
    )

    st.markdown(
        f'<div class="jev-ticker{"" if relevant else " dim"}">'
        '<span class="dot"></span>'
        f'<span class="t">{jst_time(judged.updated, "%H:%M")}</span>'
        f'<span class="kind">{judged.kind}</span>'
        f'<span class="who">{judged.author}</span>'
        f'<span class="rel" style="color:{relevance_color};">'
        f"関連度 {judged.relevance:.2f}</span>"
        f'<span class="act" style="color:{action_color};">{action_label(judged.action)[0]}</span>'
        f'<span class="ms">{judged.latency_ms:.0f}ms</span>'
        f'<span class="q">{judged.question_count}問</span>'
        "</div>",
        unsafe_allow_html=True,
    )


TICKER_STYLE = """
<style>
@keyframes jev-tick{from{opacity:0;transform:translateX(-10px);}
  to{opacity:1;transform:translateX(0);}}
.jev-ticker{display:flex;align-items:center;gap:12px;white-space:nowrap;overflow:hidden;
  padding:7px 14px;margin:0 0 12px;border-radius:8px;
  background:linear-gradient(90deg,rgba(56,189,248,.10),rgba(18,28,46,.55) 40%);
  border:1px solid #1e293b;border-left:3px solid #38bdf8;
  font-size:.82rem;animation:jev-tick .35s ease-out;}
.jev-ticker.dim{border-left-color:#334155;opacity:.55;}
.jev-ticker.empty{color:#64748b;border-left-color:#334155;}
.jev-ticker .dot{width:7px;height:7px;border-radius:50%;background:#38bdf8;flex:none;
  animation:blink 1.4s infinite;}
.jev-ticker.dim .dot{background:#475569;}
.jev-ticker .t{color:#94a3b8;font-variant-numeric:tabular-nums;flex:none;}
.jev-ticker .kind{font-weight:600;overflow:hidden;text-overflow:ellipsis;}
.jev-ticker .who{color:#94a3b8;flex:none;}
.jev-ticker .rel{font-variant-numeric:tabular-nums;flex:none;margin-left:auto;}
.jev-ticker .act{flex:none;}
.jev-ticker .ms,.jev-ticker .q{color:#64748b;font-variant-numeric:tabular-nums;flex:none;}
</style>
"""


def render_map(thresholds: dict, profile: dict) -> None:
    """関連度のヒートマップ。

    地図は1回あたりの転送量が大きい(GeoJSON を含めて約120KB)ので、
    ほかの部分より更新間隔を長くする。
    """
    map_data = build_map_data(visible_judgements(thresholds))

    left, right = st.columns([2.4, 1])
    with left:
        st.markdown(MAP_STYLE + build_svg(map_data, profile), unsafe_allow_html=True)
    with right:
        stats = kanto_prefectures(map_data)
        rows = [
            '<div style="display:flex;gap:8px;font-size:0.68rem;color:#64748b;'
            f'border-bottom:1px solid {LINE};padding-bottom:4px;margin-bottom:2px;">'
            '<div style="width:52px;">地域</div>'
            '<div style="flex:1;">関連度</div>'
            '<div style="width:34px;text-align:right;">値</div>'
            '<div style="width:52px;text-align:right;">深刻さ</div>'
            "</div>"
        ]
        if not stats:
            rows.append(
                f'<div style="color:{MUTED};font-size:0.82rem;">まだ判定した電文がありません。</div>'
            )
        for stat in stats:
            color = (
                "#d946ef" if stat.relevance >= 0.75
                else "#7c3aed" if stat.relevance >= 0.5
                else "#475569"
            )
            # 深刻さは地図と同じく円の大きさで見せる(色も地図に合わせる)
            ratio = stat.severity_ratio
            dot = "#ef4444" if ratio >= 0.7 else "#fbbf24"
            radius = 3 + 5 * ratio
            severity = (
                f'<svg width="20" height="20" style="vertical-align:middle;">'
                f'<circle cx="10" cy="10" r="{radius:.1f}" fill="{dot}" fill-opacity=".22" '
                f'stroke="{dot}" stroke-width="1.4"/></svg>'
                f'<span style="font-size:0.72rem;color:{dot};'
                f'font-variant-numeric:tabular-nums;">{stat.severity:.1f}</span>'
                if stat.count
                else f'<span style="color:#475569;font-size:0.72rem;">--</span>'
            )
            rows.append(
                '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
                f'<div style="width:52px;font-size:0.8rem;">{stat.name[:-1]}</div>'
                f'<div style="flex:1;">{bar(stat.relevance, color)}</div>'
                f'<div style="width:34px;text-align:right;font-size:0.8rem;'
                f'font-variant-numeric:tabular-nums;color:{color};">'
                f"{stat.relevance:.2f}</div>"
                f'<div style="width:52px;text-align:right;">{severity}</div>'
                "</div>"
            )
        # 地図は関東圏なので、注記も関東の中の1位にする。
        # 全国の1位を出すと、一覧に無い県の名前が出て食い違って見える
        top = next((s for s in stats if s.count and s.top_title), None)
        if top:
            rows.append(
                f'<div style="color:{MUTED};font-size:0.75rem;margin-top:10px;'
                f'border-top:1px solid {LINE};padding-top:8px;">'
                f"{top.name}が最も高いのは、"
                f"「{top.top_title[:26]}」の判定によるものです。</div>"
            )
        # 凡例は一覧の下に置く。一覧には色のバーと円が並んでいるので、
        # そのすぐ下にあると色と大きさの意味を突き合わせやすい
        rows.append(legend_html())
        st.markdown("".join(rows), unsafe_allow_html=True)

    st.caption(map_caption(map_data))


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
        message = (
            "しきい値を超える電文はまだありません。サイドバーで関連度のしきい値を下げてください。"
            if judged_all
            else f"{thresholds['question_count']}問での判定を待っています。"
        )
        st.caption(message)
        return

    for judged in featured:
        render_card(judged, thresholds["noul"])


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
        f'<div style="display:flex;gap:10px;font-size:0.75rem;color:{MUTED};'
        f'border-bottom:1px solid {LINE};padding:4px 0;">'
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
        opacity = "0.30" if low else "1"
        weight = "400" if low else "500"
        color = ACTION_COLORS.get(judged.action, "#6b7280")
        rows.append(
            f'<div style="display:flex;gap:10px;align-items:center;font-size:0.82rem;'
            f'padding:3px 0;border-bottom:1px solid {LINE};opacity:{opacity};'
            f'font-weight:{weight};">'
            f'<div style="width:90px;color:{MUTED};">{jst_time(judged.updated)}</div>'
            f'<div style="flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            f"{judged.kind}</div>"
            f'<div style="flex:1.4;color:{MUTED};overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{judged.author}</div>'
            f'<div style="width:110px;display:flex;align-items:center;gap:6px;">'
            f"{bar(judged.relevance, '#38bdf8', 60)}"
            f'<span style="font-size:0.78rem;">{judged.relevance:.2f}</span></div>'
            f'<div style="width:120px;color:{color};">{action_label(judged.action)[0]}</div>'
            f'<div style="width:70px;text-align:right;color:{MUTED};">'
            f"{judged.latency_ms:.0f}ms</div>"
            "</div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


# ---------------------------------------------------------------- 画面


@st.fragment(run_every="1s")
def render_upper(profile: dict, thresholds: dict) -> None:
    """画面の上半分(ヘッダー・モード・結論・補足指標・地図)。

    細かく分けて部分更新すると、サイドバーの操作で全体が再実行されたときに
    差分の宛先がずれて描画が壊れる(Bad delta path index)。まとめて1つにして、
    中の要素の並びが変わらないようにしてある。
    """
    render_mode_bar(profile)
    render_conclusion(thresholds)

    # 判定した電文を1行で流す。Jev が次々に判定している様子をそのまま見せる
    render_ticker(thresholds)

    # 大量に流れる電文の中で、自分に関係するところだけが濃くなる地図。
    # スクロールなしで見えるよう、余計な区切りを入れずにすぐ下に置く。
    if st.session_state.get("show_map", True):
        render_map(thresholds, profile)
    else:
        st.caption("関連度マップは非表示です（サイドバーで表示できます）。")


@st.fragment(run_every="1s")
def render_lower(profile: dict, thresholds: dict) -> None:
    """画面の下半分(注目の判定・流れる電文・Jevの性能)。"""
    render_featured(thresholds)
    st.divider()
    render_stream(thresholds)

    # Jev の性能を示す数字。状況ではないので下に置く。
    # ただしデモの主張として重要なので、初期状態は開いておく。
    st.divider()
    with st.expander("Jev の性能（処理件数・レイテンシ・トークン・コスト）", expanded=True):
        render_metrics(profile)


def main() -> None:
    profile = get_profile()
    thresholds = render_sidebar()
    engine = get_engine()

    # 画面上部の余白を詰めて、関連度マップをスクロールなしで見える位置に上げる
    st.markdown(
        "<style>"
        # Streamlit の上部ツールバーは固定表示。これより上に詰めると先頭が隠れる
        '[data-testid="stAppViewBlockContainer"],.block-container'
        "{padding-top:3.6rem;padding-bottom:2rem;}"
        'div[data-testid="stVerticalBlock"]{gap:.5rem;}'
        "hr{margin:.5rem 0;}"
        # モードのバッジ。1行に固定し、入りきらなければ省略記号にする。
        # 折り返すと高さが伸びて、下の結論バナーに重なってしまうため。
        ".jev-modebar{min-height:32px;display:flex;align-items:center;overflow:hidden;}"
        ".jev-badge{display:inline-flex;align-items:center;gap:8px;padding:3px 12px;"
        "border-radius:20px;border:1px solid;white-space:nowrap;overflow:hidden;"
        "text-overflow:ellipsis;max-width:100%;}"
        ".jev-badge .dot{width:9px;height:9px;border-radius:50%;display:inline-block;"
        "flex:none;}"
        ".jev-badge b{flex:none;}"
        # 使えないボタンは見えなくする。場所は取ったままにして、
        # 画面の要素の並びが変わらないようにする(差分のずれを防ぐため)
        '[data-testid="stButton"] button:disabled'
        "{opacity:0;pointer-events:none;border-color:transparent;}"
        "</style>",
        unsafe_allow_html=True,
    )

    # リプレイ中は背景の色味を変えて、ライブと見間違えないようにする
    if engine.status.mode == "replay":
        st.markdown(
            "<style>"
            '[data-testid="stAppViewContainer"]{background:'
            "linear-gradient(rgba(124,58,237,.10),rgba(11,18,32,0) 220px);}"
            '[data-testid="stAppViewContainer"]::before{content:"";position:fixed;'
            "top:0;left:0;right:0;height:3px;background:#a78bfa;z-index:999;}"
            '[data-testid="stAppViewContainer"]::after{content:"";position:fixed;'
            "bottom:0;left:0;right:0;height:3px;background:#a78bfa;z-index:999;}"
            "</style>",
            unsafe_allow_html=True,
        )

    st.markdown(TICKER_STYLE, unsafe_allow_html=True)
    render_upper(profile, thresholds)
    st.divider()
    render_lower(profile, thresholds)


main()
