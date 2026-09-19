"""関連度のヒートマップ(日本地図)。

**これは気象庁の警報マップではない。** 色の濃さは「警報の強さ」ではなく、
Jev が判定した「この電文がプロファイルの人物にとってどれだけ関係するか」
(relevant)である。そこがこの地図の存在意義なので、凡例と説明文でも
必ずその旨を出すこと(requirements.md R-90)。

- 下敷き : 判定済みの電文が発表対象としている都道府県を薄いグレーで塗る
           (電文のコードから機械的に決まる。Jev は使わない)
- 主役   : その上に relevant の最大値で色を重ねる
"""

from __future__ import annotations

import plotly.graph_objects as go

from .geo import GEOJSON_SOURCE, GEOJSON_VIA, MapData, load_geojson

# 結論バナーと同系統の色。薄いグレー -> 黄 -> 橙 -> 赤
RELEVANCE_SCALE = [
    [0.00, "#eef0f2"],
    [0.25, "#fef9c3"],
    [0.50, "#fdba74"],
    [0.75, "#f87171"],
    [1.00, "#b91c1c"],
]

# 電文はあるが関連度が低い地域の下敷き
UNDERLAY_COLOR = "#e5e7eb"
# 電文がまだ無い地域
EMPTY_COLOR = "#f8fafc"


def build_figure(map_data: MapData, profile: dict) -> go.Figure:
    """ヒートマップを組み立てる。"""
    stats = sorted(map_data.stats.values(), key=lambda s: s.code)

    with_message = [s for s in stats if s.count]
    without = [s for s in stats if not s.count]

    figure = go.Figure()

    # 下敷き1: まだ電文が無い都道府県(いちばん薄い)
    if without:
        figure.add_trace(
            go.Choropleth(
                geojson=load_geojson(),
                featureidkey="properties.code",
                locations=[s.code for s in without],
                z=[0] * len(without),
                colorscale=[[0, EMPTY_COLOR], [1, EMPTY_COLOR]],
                showscale=False,
                marker_line_width=0.4,
                marker_line_color="#ffffff",
                hovertemplate="%{text}<br>まだ電文がありません<extra></extra>",
                text=[s.name for s in without],
            )
        )

    # 下敷き2 + 主役: 電文がある都道府県を relevant の濃さで塗る
    if with_message:
        figure.add_trace(
            go.Choropleth(
                geojson=load_geojson(),
                featureidkey="properties.code",
                locations=[s.code for s in with_message],
                z=[s.relevance for s in with_message],
                zmin=0.0,
                zmax=1.0,
                colorscale=RELEVANCE_SCALE,
                marker_line_width=0.4,
                marker_line_color="#ffffff",
                colorbar=dict(
                    title=dict(text="あなたにとっての<br>関連度", font=dict(size=11)),
                    thickness=12,
                    len=0.7,
                    x=0.98,
                    tickvals=[0, 0.5, 1],
                    ticktext=["低い", "0.5", "高い"],
                    tickfont=dict(size=10),
                ),
                customdata=[[s.count, s.top_title, s.top_action or "--"] for s in with_message],
                text=[s.name for s in with_message],
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "関連度 %{z:.2f}<br>"
                    "判定した電文 %{customdata[0]}件<br>"
                    "%{customdata[1]}<br>"
                    "→ %{customdata[2]}"
                    "<extra></extra>"
                ),
            )
        )

    # プロファイルの居住地に目印
    lat, lon = profile.get("lat"), profile.get("lon")
    if lat and lon:
        where = f"{profile.get('pref', '')}{profile.get('city', '')}" or "居住地"
        figure.add_trace(
            go.Scattergeo(
                lat=[lat],
                lon=[lon],
                mode="markers",
                marker=dict(
                    size=13,
                    color="#1d4ed8",
                    symbol="star",
                    line=dict(width=1.5, color="#ffffff"),
                ),
                showlegend=False,
                hovertemplate=f"{profile.get('name', '')}の居住地<br>{where}<extra></extra>",
            )
        )

    figure.update_geos(
        fitbounds="locations",
        visible=False,
        bgcolor="rgba(0,0,0,0)",
        projection_type="mercator",
    )
    figure.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=460,
        dragmode=False,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return figure


def top_prefectures(map_data: MapData, limit: int = 6) -> list:
    """関連度の高い都道府県。地図の横に並べて、色の意味を数字でも見せる。"""
    with_message = [s for s in map_data.stats.values() if s.count]
    return sorted(with_message, key=lambda s: s.relevance, reverse=True)[:limit]


def caption(map_data: MapData) -> str:
    """地図の下に添える説明と出典。"""
    parts = [
        "色の濃さは、この電文がプロファイルの人物にとって**どれだけ関係するか**を "
        "Jev が判定した結果（relevant）です。**警報の強さではありません。**",
        f"判定済みの電文が対象とする {map_data.covered} 都道府県を塗っています"
        f"（関連度 0.5 以上は {map_data.highlighted} 都道府県）。",
    ]
    if map_data.nationwide_count:
        parts.append(
            f"全国を対象とする電文 {map_data.nationwide_count:,} 件"
            "（全般気象情報・台風情報・集約通報など）は、"
            "全国を一律に塗ると地図が意味を失うため色に含めていません。"
        )
    parts.append(f"地図の出典: {GEOJSON_SOURCE}（{GEOJSON_VIA} で GeoJSON 化したものを簡略化）")
    return "　".join(parts)
