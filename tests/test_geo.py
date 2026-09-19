"""関連度マップのテスト。

GeoJSON の読み込み、都道府県の対応付け、全国対象の電文の扱いを確認する。
Plotly の図そのものは描かず、組み立てが通ることだけ見る。
"""

from __future__ import annotations

import pathlib

import pytest

from app.geo import (
    NATIONWIDE_CODE,
    area_code_of,
    build_map_data,
    is_nationwide,
    load_geojson,
    prefecture_names,
    prefecture_of,
)

DATA_URL = "https://www.data.jma.go.jp/developer/xml/data/"


def make(message_id: str, relevance: float = 0.5, title: str = "電文", action: str = "通常どおり"):
    """地図の集計に必要な最小限の形だけを持つ、判定結果の代わり。"""

    class Stub:
        id = message_id
        kind = title
        top_title = title

        def __init__(self) -> None:
            self.title = title
            self.relevance = relevance
            self.action = action

    return Stub()


# ---------------------------------------------------------------- コードの取り出し


def test_電文IDから府県予報区コードを取り出す():
    assert area_code_of(DATA_URL + "20260919015757_0_VPWW53_130000.xml") == "130000"
    assert area_code_of(DATA_URL + "20260918223117_0_VPFJ50_120000.xml") == "120000"
    # ファイル名だけでも引ける
    assert area_code_of("20260919015757_0_VPWW53_130000.xml") == "130000"
    # 形が違えば空
    assert area_code_of("https://example.com/foo.xml") == ""


def test_上位2桁が都道府県コードになる():
    assert prefecture_of(DATA_URL + "20260919015757_0_VPWW53_130000.xml") == "13"  # 東京都
    assert prefecture_of(DATA_URL + "20260918223117_0_VPFJ50_120000.xml") == "12"  # 千葉県
    # 北海道は複数の予報区に分かれるが、どれも 01
    assert prefecture_of(DATA_URL + "x_0_VPWW53_014100.xml") == "01"
    assert prefecture_of(DATA_URL + "x_0_VPWW53_017000.xml") == "01"
    # 沖縄も複数に分かれる
    assert prefecture_of(DATA_URL + "x_0_VPWW53_471000.xml") == "47"


def test_010000は北海道ではなく全国():
    """`010000` は全国。北海道(011000〜017000)と取り違えないこと。"""
    nationwide = DATA_URL + f"x_0_VPZJ50_{NATIONWIDE_CODE}.xml"
    assert is_nationwide(nationwide)
    assert prefecture_of(nationwide) is None

    hokkaido = DATA_URL + "x_0_VPWW53_013000.xml"
    assert not is_nationwide(hokkaido)
    assert prefecture_of(hokkaido) == "01"


def test_範囲外のコードは対象外():
    assert prefecture_of(DATA_URL + "x_0_VPWW53_990000.xml") is None
    assert prefecture_of(DATA_URL + "x_0_VPWW53_000000.xml") is None


# ---------------------------------------------------------------- GeoJSON


def test_GeoJSONに47都道府県がある():
    data = load_geojson()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 47

    names = prefecture_names()
    assert names["13"] == "東京都"
    assert names["01"] == "北海道"
    assert names["47"] == "沖縄県"
    assert len(names) == 47


def test_GeoJSONの各地物に座標がある():
    for feature in load_geojson()["features"]:
        geometry = feature["geometry"]
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        assert polygons, feature["properties"]["name"]
        # 外周は閉じたリング(4点以上)
        assert len(polygons[0][0]) >= 4, feature["properties"]["name"]


# ---------------------------------------------------------------- 集計


def test_関連度は最大値を取る():
    """1本でも関係する電文があれば浮かび上がるよう、平均ではなく最大値にする。"""
    judged = [
        make(DATA_URL + "a_0_VPWW53_130000.xml", relevance=0.1),
        make(DATA_URL + "b_0_VPWW53_130000.xml", relevance=0.9, title="東京都気象警報"),
        make(DATA_URL + "c_0_VPWW53_130000.xml", relevance=0.4),
    ]
    data = build_map_data(judged)

    tokyo = data.stats["13"]
    assert tokyo.count == 3
    assert tokyo.relevance == pytest.approx(0.9)
    assert tokyo.top_title == "東京都気象警報"


def test_全国対象の電文は地図に塗らない():
    judged = [
        make(DATA_URL + f"a_0_VPZJ50_{NATIONWIDE_CODE}.xml", relevance=0.95),
        make(DATA_URL + f"b_0_VPZJ50_{NATIONWIDE_CODE}.xml", relevance=0.95),
        make(DATA_URL + "c_0_VPWW53_130000.xml", relevance=0.8),
    ]
    data = build_map_data(judged)

    assert data.nationwide_count == 2
    # 全国の 0.95 がどこにも塗られていない
    assert all(s.relevance <= 0.8 for s in data.stats.values())
    assert data.stats["13"].relevance == pytest.approx(0.8)
    assert data.covered == 1


def test_電文の無い都道府県は数えない():
    data = build_map_data([make(DATA_URL + "a_0_VPWW53_130000.xml", relevance=0.8)])

    assert len(data.stats) == 47  # 47件すべて器はある
    assert data.covered == 1  # 電文があるのは1件だけ
    assert data.stats["01"].count == 0
    assert data.stats["01"].relevance == 0.0


def test_しきい値を超えた都道府県を数える():
    judged = [
        make(DATA_URL + "a_0_VPWW53_130000.xml", relevance=0.9),
        make(DATA_URL + "b_0_VPWW53_140000.xml", relevance=0.6),
        make(DATA_URL + "c_0_VPWW53_010000.xml", relevance=0.2),  # 全国(塗らない)
        make(DATA_URL + "d_0_VPWW53_470000.xml", relevance=0.05),
    ]
    data = build_map_data(judged)

    assert data.highlighted == 2  # 0.5以上は東京都と神奈川県
    assert data.nationwide_count == 1


def test_読めないコードは不明として数える():
    data = build_map_data([make("https://example.com/broken.xml", relevance=0.9)])
    assert data.unknown_count == 1
    assert data.covered == 0


# ---------------------------------------------------------------- 図の組み立て


def test_SVGが組み立てられる():
    from app.mapview import build_svg, caption, legend_html, top_prefectures

    judged = [
        make(DATA_URL + "a_0_VPWW53_130000.xml", relevance=0.9, title="東京都気象警報"),
        make(DATA_URL + f"b_0_VPZJ50_{NATIONWIDE_CODE}.xml", relevance=0.5),
    ]
    data = build_map_data(judged)
    profile = {"name": "テスト", "pref": "東京都", "city": "", "lat": 35.68, "lon": 139.69}

    svg = build_svg(data, profile)
    assert "<svg" in svg and "</svg>" in svg
    # 関東8都県のパスが入っている
    assert svg.count('class="pref"') == 8
    # 居住地の目印
    assert "home-pulse" in svg
    # 関連度が高い東京都はラベルが出る
    assert "0.90" in svg

    assert "警報の強さではありません" in legend_html()

    text = caption(data)
    assert "警報の強さではありません" in text
    assert "地球地図日本" in text
    assert "全国を対象とする電文 1 件" in text

    tops = top_prefectures(data)
    assert tops[0].name == "東京都"


def test_関東圏だけを描く():
    from app.mapview import KANTO_CODES, kanto_codes_in_view

    codes = kanto_codes_in_view()
    assert len(codes) == 8
    assert set(codes) == set(KANTO_CODES)
    # 北海道・沖縄は描かない(見たい関東が小さくなるため)
    assert "01" not in codes and "47" not in codes


def test_東京都の島嶼部は関東の地図から外す():
    """伊豆諸島・小笠原まで描くと、関東が縦に伸びて小さくなる。"""
    from app.mapview import MIN_LAT, _kanto_shapes

    tokyo = next(path for code, _, path in _kanto_shapes() if code == "13")
    ys = []
    for token in tokyo.replace("M", " ").replace("L", " ").replace("Z", " ").split():
        if "," in token:
            ys.append(float(token.split(",")[1]))
    # SVG の高さに収まっている(南へはみ出していない)
    assert max(ys) <= 460
    assert MIN_LAT > 34.0


def test_関連度が色になる():
    from app.mapview import relevance_color

    low = relevance_color(0.0)
    high = relevance_color(1.0)
    assert low != high
    assert low.startswith("#") and len(low) == 7
    # 範囲外でも落ちない
    assert relevance_color(-1.0) == low
    assert relevance_color(2.0) == high


# ---------------------------------------------------------------- 依存


def test_地図の描画に外部ライブラリが要らない():
    """SVG を自前で組み立てるので、plotly などの描画ライブラリに依存しない。

    依存が減るぶん、環境の取り違えで画面が落ちる余地も減る。
    """
    import app.mapview as mapview

    source = pathlib.Path(mapview.__file__).read_text(encoding="utf-8")
    assert "import plotly" not in source
    assert "pydeck" not in source
