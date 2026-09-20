"""関連度のヒートマップ(関東圏)。

**これは気象庁の警報マップではない。** 色の濃さは「警報の強さ」ではなく、
Jev が判定した「この電文がプロファイルの人物にとってどれだけ関係するか」
(relevant)である。そこがこの地図の存在意義なので、凡例と説明文でも
必ずその旨を出すこと(requirements.md R-90)。

描画は**自前の SVG**。地図の形(パス)は一度作ったら変わらないので、
更新のたびに作り直すのは塗りの色と目印だけになる。描画ライブラリを挟まないぶん、
再描画で一瞬消えてから出るようなちらつきが起きにくい。色の変化には CSS の
transition をかけてある。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from .geo import GEOJSON_SOURCE, GEOJSON_VIA, MapData, load_geojson

# 表示する範囲。全国だと見たい関東が小さくなるので、1都6県に山梨を足した範囲にする
KANTO_CODES: tuple[str, ...] = ("08", "09", "10", "11", "12", "13", "14", "19")

# 東京都は伊豆諸島・小笠原を含むため南へ大きく伸びる。本土だけを描くための下限
MIN_LAT = 34.8

# SVG の大きさ(ビューボックス)。実際の表示幅は CSS で決まる
VIEW_W = 640.0
VIEW_H = 420.0
PADDING = 10.0

# 関連度の色。**赤や橙は使わない。** 警報の強さと見間違えられるため、
# 青 → インディゴ → 紫 → マゼンタ の系統にする(requirements.md R-95)。
COLOR_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (30, 41, 59)),     # 情報はあるが関係が薄い(濃いグレー)
    (0.25, (30, 64, 175)),    # 濃い青
    (0.50, (67, 56, 202)),    # インディゴ
    (0.75, (124, 58, 237)),   # 紫
    (1.00, (217, 70, 239)),   # マゼンタ
)
EMPTY_FILL = "#131c2b"   # まだ電文が無い地域
STROKE = "#0b1220"
STROKE_HOT = "#f0abfc"   # 関連度が高い地域の縁取り(淡いマゼンタ)

# 事象の深刻さ(円)の色。こちらは警報らしい琥珀〜赤でよい。
# 面の色(青紫)と色相が離れているので、2つの軸を混同しない。
SEVERITY_FILL = "#fbbf24"
SEVERITY_HOT = "#ef4444"
SEVERITY_MIN_R = 3.0   # 円の最小半径
SEVERITY_MAX_R = 13.0  # 円の最大半径


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> str:
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def relevance_color(value: float) -> str:
    """0〜1 の関連度を色にする。"""
    value = max(0.0, min(1.0, value))
    for i in range(len(COLOR_STOPS) - 1):
        low, low_rgb = COLOR_STOPS[i]
        high, high_rgb = COLOR_STOPS[i + 1]
        if value <= high:
            span = high - low
            return _mix(low_rgb, high_rgb, (value - low) / span if span else 0.0)
    return _mix(COLOR_STOPS[-1][1], COLOR_STOPS[-1][1], 0.0)


# ---------------------------------------------------------------- 投影


@dataclass(frozen=True)
class Projection:
    """緯度経度を SVG の座標に直す。

    関東くらいの範囲なら、経度を cos(緯度) で縮めるだけで形はほぼ崩れない。
    """

    lon_min: float
    lat_min: float
    scale: float
    offset_x: float
    offset_y: float
    k: float  # 経度方向の縮み

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        x = (lon - self.lon_min) * self.k * self.scale + self.offset_x
        y = (self.lat_min - lat) * self.scale + self.offset_y
        return x, y


@lru_cache(maxsize=1)
def _kanto_shapes() -> tuple[tuple[str, str, str], ...]:
    """関東圏の (都県コード, 名前, SVG パス) を作る。1回だけ計算する。"""
    features = [
        f for f in load_geojson()["features"] if f["properties"]["code"] in KANTO_CODES
    ]

    def rings_of(feature: dict) -> list[list[list[float]]]:
        geometry = feature["geometry"]
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        rings: list[list[list[float]]] = []
        for polygon in polygons:
            outer = polygon[0]
            # 伊豆諸島・小笠原のように南へ離れたものは、関東の地図には描かない
            if max(point[1] for point in outer) < MIN_LAT:
                continue
            rings.append(outer)
        return rings

    everything = [point for f in features for ring in rings_of(f) for point in ring]
    lons = [p[0] for p in everything]
    lats = [p[1] for p in everything]
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)

    k = math.cos(math.radians((lat_min + lat_max) / 2))
    width = (lon_max - lon_min) * k
    height = lat_max - lat_min
    scale = min((VIEW_W - PADDING * 2) / width, (VIEW_H - PADDING * 2) / height)

    # 余った分だけ中央に寄せる
    offset_x = PADDING + ((VIEW_W - PADDING * 2) - width * scale) / 2
    offset_y = PADDING + ((VIEW_H - PADDING * 2) - height * scale) / 2
    projection = Projection(lon_min, lat_max, scale, offset_x, offset_y, k)

    shapes: list[tuple[str, str, str]] = []
    for feature in features:
        parts: list[str] = []
        for ring in rings_of(feature):
            points = [projection.to_xy(lon, lat) for lon, lat in ring]
            if len(points) < 3:
                continue
            head = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
            tail = "".join(f"L{x:.1f},{y:.1f}" for x, y in points[1:])
            parts.append(head + tail + "Z")
        if parts:
            shapes.append(
                (feature["properties"]["code"], feature["properties"]["name"], " ".join(parts))
            )
    return tuple(shapes)


@lru_cache(maxsize=1)
def _projection() -> Projection:
    _kanto_shapes()  # 形と同じ計算を使うため、先に作らせる
    features = [
        f for f in load_geojson()["features"] if f["properties"]["code"] in KANTO_CODES
    ]
    points = []
    for feature in features:
        geometry = feature["geometry"]
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        for polygon in polygons:
            outer = polygon[0]
            if max(p[1] for p in outer) < MIN_LAT:
                continue
            points += outer
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    k = math.cos(math.radians((lat_min + lat_max) / 2))
    width = (lon_max - lon_min) * k
    height = lat_max - lat_min
    scale = min((VIEW_W - PADDING * 2) / width, (VIEW_H - PADDING * 2) / height)
    offset_x = PADDING + ((VIEW_W - PADDING * 2) - width * scale) / 2
    offset_y = PADDING + ((VIEW_H - PADDING * 2) - height * scale) / 2
    return Projection(lon_min, lat_max, scale, offset_x, offset_y, k)


def kanto_codes_in_view() -> list[str]:
    return [code for code, _, _ in _kanto_shapes()]


# ---------------------------------------------------------------- 描画


def build_svg(map_data: MapData, profile: dict) -> str:
    """関連度マップの SVG を組み立てる。"""
    shapes = _kanto_shapes()
    projection = _projection()

    polygons: list[str] = []
    severity_dots: list[str] = []
    labels: list[str] = []
    for code, name, path in shapes:
        stat = map_data.stats.get(code)
        has_message = bool(stat and stat.count)
        relevance = stat.relevance if stat else 0.0

        fill = relevance_color(relevance) if has_message else EMPTY_FILL
        # 光らせるのは本当に関係が深いところだけ。全部光ると浮かび上がらない
        hot = has_message and relevance >= 0.85
        stroke = STROKE_HOT if hot else STROKE
        glow = ' filter="url(#glow)"' if hot else ""
        opacity = 1.0 if has_message else 0.55

        title = (
            f"{name}｜関連度 {relevance:.2f}"
            f"｜深刻さ {stat.severity:.2f}/{stat.severity_max}"
            f"｜電文 {stat.count}件"
            if has_message
            else f"{name}｜まだ電文がありません"
        )
        polygons.append(
            f'<path class="pref" d="{path}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{1.2 if hot else 0.6}" opacity="{opacity}"{glow}>'
            f"<title>{title}</title></path>"
        )
        if has_message:
            cx, cy = _label_point(path)
            # 事象の深刻さは、面の色ではなく円の大きさで表す。
            # 色で両方を表すと、関連度が高いのか警報が強いのか区別がつかない。
            ratio = stat.severity_ratio
            if ratio > 0:
                radius = SEVERITY_MIN_R + (SEVERITY_MAX_R - SEVERITY_MIN_R) * ratio
                color = SEVERITY_HOT if ratio >= 0.7 else SEVERITY_FILL
                severity_dots.append(
                    f'<circle class="sev" cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" '
                    f'fill="{color}" fill-opacity="0.22" stroke="{color}" '
                    f'stroke-width="1.8">'
                    f"<title>{title}</title></circle>"
                )
            if relevance >= 0.5:
                # 名前と数値は円の下に置く。円と重ねると両方読みにくい
                offset = SEVERITY_MIN_R + (SEVERITY_MAX_R - SEVERITY_MIN_R) * ratio + 11
                labels.append(
                    f'<text x="{cx:.1f}" y="{cy + offset:.1f}" class="lbl">{name[:-1]}</text>'
                    f'<text x="{cx:.1f}" y="{cy + offset + 13:.1f}" class="val">'
                    f"{relevance:.2f}</text>"
                )

    # 居住地の目印(脈打つ点)
    marker = ""
    lat, lon = profile.get("lat"), profile.get("lon")
    if lat and lon:
        mx, my = projection.to_xy(float(lon), float(lat))
        where = f"{profile.get('pref', '')}{profile.get('city', '')}" or "居住地"
        marker = (
            f'<g><circle cx="{mx:.1f}" cy="{my:.1f}" r="4" class="home-pulse"/>'
            f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="3.4" class="home"/>'
            f"<title>{profile.get('name', '')}の居住地　{where}</title></g>"
        )

    return f"""
<div class="jev-map">
  <svg viewBox="0 0 {VIEW_W:.0f} {VIEW_H:.0f}" preserveAspectRatio="xMidYMid meet">
    <defs>
      <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="2.4" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    {"".join(polygons)}
    {"".join(severity_dots)}
    {"".join(labels)}
    {marker}
  </svg>
</div>
"""


def _label_point(path: str) -> tuple[float, float]:
    """パスのいちばん大きいリングの重心。ラベルを置く位置に使う。"""
    best: list[tuple[float, float]] = []
    for part in path.split("Z"):
        points: list[tuple[float, float]] = []
        for token in part.replace("M", " ").replace("L", " ").split():
            if "," not in token:
                continue
            x, _, y = token.partition(",")
            try:
                points.append((float(x), float(y)))
            except ValueError:
                continue
        if len(points) > len(best):
            best = points
    if not best:
        return 0.0, 0.0
    return sum(p[0] for p in best) / len(best), sum(p[1] for p in best) / len(best)


MAP_STYLE = """
<style>
.jev-map{background:radial-gradient(circle at 50% 35%,#0f1a2e 0%,#0b1220 70%);
  border:1px solid #1e293b;border-radius:10px;padding:6px;margin:0 0 32px;}
/* 高さの上限は、右にある都県一覧＋凡例(「この地図の見方」)の下端に合わせた値。
   一覧は関東8都県で固定なので、実測した高さをそのまま使っている。 */
.jev-map svg{width:100%;height:auto;display:block;margin:0 auto;
  max-height:417px;}
.jev-map .pref{transition:fill .8s ease,opacity .8s ease,stroke .8s ease;}
.jev-map .lbl{fill:#f8fafc;font-size:11px;font-weight:700;text-anchor:middle;
  paint-order:stroke;stroke:#0b1220;stroke-width:3px;}
.jev-map .val{fill:#fbbf24;font-size:12px;font-weight:700;text-anchor:middle;
  font-variant-numeric:tabular-nums;paint-order:stroke;stroke:#0b1220;stroke-width:3px;}
.jev-map .home{fill:#38bdf8;stroke:#0b1220;stroke-width:1.2;}
.jev-map .home-pulse{fill:#38bdf8;opacity:.55;animation:jevpulse 2.2s ease-out infinite;}
@keyframes jevpulse{0%{r:4;opacity:.55}70%{r:16;opacity:0}100%{r:16;opacity:0}}
.jev-legend{margin-top:12px;padding-top:10px;border-top:1px solid #1e293b;
  font-size:.72rem;color:#94a3b8;}
.jev-legend .ttl{color:#64748b;font-size:.68rem;letter-spacing:.06em;margin-bottom:6px;}
.jev-legend .axis{margin-bottom:8px;}
.jev-legend .head{display:flex;align-items:baseline;gap:6px;}
.jev-legend .cap{color:#64748b;}
.jev-legend .row{display:flex;align-items:center;gap:8px;margin-top:2px;}
.jev-legend .bar{width:92px;height:9px;border-radius:5px;
  background:linear-gradient(90deg,#1e293b,#1e40af,#4338ca,#7c3aed,#d946ef);}
.jev-legend .ends{color:#64748b;white-space:nowrap;}
.jev-legend .note{color:#94a3b8;line-height:1.5;margin-top:8px;}
.jev-legend .note b{color:#f0abfc;}
.jev-map .sev{pointer-events:none;transition:r .8s ease,fill .8s ease,stroke .8s ease;}
</style>
"""


def legend_html() -> str:
    """凡例。2つの軸が別のものだと分かるように、縦に積んで出す。

    - 面の色 = Jev が判定した「自分にとっての関連度」
    - 円の大きさ = 電文が示す「事象の深刻さ」

    地図の枠の下ではなく、地図の横にある都県の一覧の下に置く。
    一覧には色のバーと円が並んでいるので、そのすぐ下にあると対応が取りやすい。
    """
    return (
        '<div class="jev-legend">'
        '<div class="ttl">この地図の見方</div>'
        # 面の色 = 関連度
        '<div class="axis">'
        '<div class="head"><b style="color:#c4b5fd;">面の色</b>'
        '<span class="cap">あなたにとっての関連度</span></div>'
        '<div class="row"><span class="bar"></span>'
        '<span class="ends">低 → 高</span></div>'
        "</div>"
        # 円 = 深刻さ
        '<div class="axis">'
        f'<div class="head"><b style="color:{SEVERITY_FILL};">円の大きさ</b>'
        '<span class="cap">事象の深刻さ</span></div>'
        '<div class="row">'
        f'<svg viewBox="0 0 88 26" width="88" height="26">'
        f'<circle cx="10" cy="13" r="4" fill="{SEVERITY_FILL}" fill-opacity=".22" '
        f'stroke="{SEVERITY_FILL}" stroke-width="1.4"/>'
        f'<circle cx="33" cy="13" r="7" fill="{SEVERITY_FILL}" fill-opacity=".22" '
        f'stroke="{SEVERITY_FILL}" stroke-width="1.4"/>'
        f'<circle cx="62" cy="13" r="11" fill="{SEVERITY_HOT}" fill-opacity=".22" '
        f'stroke="{SEVERITY_HOT}" stroke-width="1.4"/>'
        "</svg>"
        '<span class="ends">弱 → 強</span></div>'
        "</div>"
        '<div class="note">'
        "面の色は<b>警報の強さではありません</b>。"
        "色が濃く、円も大きい地域が「自分にとって重大」です。"
        "</div>"
        "</div>"
    )


def caption(map_data: MapData) -> str:
    """地図の下に添える説明と出典。"""
    in_view = set(kanto_codes_in_view())
    covered = sum(1 for code, s in map_data.stats.items() if code in in_view and s.count)
    parts = [
        f"関東圏の {covered}/{len(in_view)} 都県に判定済みの電文があります"
        f"（全国では {map_data.covered} 都道府県）。",
    ]
    if map_data.nationwide_count:
        parts.append(
            f"全国を対象とする電文 {map_data.nationwide_count:,} 件"
            "（全般気象情報・台風情報・集約通報など）は、"
            "全国を一律に塗ると地図が意味を失うため色に含めていません。"
        )
    parts.append(f"地図の出典: {GEOJSON_SOURCE}（{GEOJSON_VIA} の GeoJSON を簡略化）")
    return "　".join(parts)


def top_prefectures(map_data: MapData, limit: int = 6) -> list:
    """関連度の高い都道府県。地図の横に並べて、色の意味を数字でも見せる。"""
    with_message = [s for s in map_data.stats.values() if s.count]
    return sorted(with_message, key=lambda s: s.relevance, reverse=True)[:limit]


def kanto_prefectures(map_data: MapData) -> list:
    """地図に描いている関東の都県を、関連度の高い順に返す。

    地図の横に8件すべて並べる。6件だけだと下が空くうえ、
    2つの軸(関連度と深刻さ)を数字でも突き合わせられるようにしたい。
    """
    codes = set(kanto_codes_in_view())
    stats = [s for code, s in map_data.stats.items() if code in codes]
    return sorted(stats, key=lambda s: (s.relevance, s.count), reverse=True)
