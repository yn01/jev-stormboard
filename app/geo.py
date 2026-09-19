"""都道府県の対応付けと、地図に渡すデータの組み立て。

電文の「発表対象地域」は、電文のファイル名の末尾にある**府県予報区コード**から
機械的に決まる(Jev は使わない)。地図の色は、そこに Jev の判定した relevant を
重ねたもの。

    .../20260919015757_0_VPWW53_130000.xml
                                ^^^^^^ 府県予報区コード。上位2桁が都道府県コード

`010000` だけは北海道ではなく**全国**を指す(全般気象情報、台風情報、集約通報など)。
全国を一律に塗ると地図が真っ赤になって意味を失うので、地図の塗りからは外す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEOJSON_PATH = ROOT / "assets" / "japan_prefectures.geojson"

# 全国を対象とする電文の府県予報区コード
NATIONWIDE_CODE = "010000"

# 出典(地図の下と README に明記する)
GEOJSON_SOURCE = "地球地図日本（国土地理院）"
GEOJSON_VIA = "dataofjapan/land"


def area_code_of(message_id: str) -> str:
    """電文ID(URL)の末尾から府県予報区コードを取り出す。

    取れなければ空文字を返す。XML は読まないので軽い。
    """
    name = message_id.rsplit("/", 1)[-1]
    name = name.split(".")[0]
    parts = name.split("_")
    if len(parts) < 4:
        return ""
    code = parts[3]
    return code if code.isdigit() else ""


def prefecture_of(message_id: str) -> str | None:
    """電文の対象となる都道府県コード(2桁)。

    全国を対象とする電文と、コードが読めないものは None を返す。
    """
    code = area_code_of(message_id)
    if not code or code == NATIONWIDE_CODE:
        return None
    pref = code[:2]
    return pref if "01" <= pref <= "47" else None


def is_nationwide(message_id: str) -> bool:
    return area_code_of(message_id) == NATIONWIDE_CODE


@lru_cache(maxsize=1)
def load_geojson() -> dict:
    """間引き済みの都道府県 GeoJSON を読む(1回だけ)。"""
    with GEOJSON_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def prefecture_names() -> dict[str, str]:
    """都道府県コード -> 名前。"""
    return {f["properties"]["code"]: f["properties"]["name"] for f in load_geojson()["features"]}


@dataclass
class PrefectureStat:
    """都道府県1つぶんの集計。"""

    code: str
    name: str
    count: int = 0  # その地域を対象とする判定済み電文の数
    relevance: float = 0.0  # relevant の最大値。地図の色の濃さになる
    top_title: str = ""  # relevant が最大だった電文
    top_action: str = ""


@dataclass
class MapData:
    """地図に渡すデータ一式。"""

    stats: dict[str, PrefectureStat] = field(default_factory=dict)
    nationwide_count: int = 0  # 全国を対象とする電文(地図には塗らない)
    unknown_count: int = 0  # コードが読めなかった電文

    @property
    def covered(self) -> int:
        return sum(1 for s in self.stats.values() if s.count)

    @property
    def highlighted(self) -> int:
        """関連度が高い(0.5以上)都道府県の数。"""
        return sum(1 for s in self.stats.values() if s.relevance >= 0.5)


def build_map_data(judged_messages) -> MapData:
    """判定結果から、都道府県ごとの関連度を集計する。

    relevant は**最大値**を取る。その地域に関係する電文が1本でもあれば
    浮かび上がってほしいため(平均だと薄まる)。
    """
    names = prefecture_names()
    data = MapData(stats={code: PrefectureStat(code, name) for code, name in names.items()})

    for judged in judged_messages:
        if is_nationwide(judged.id):
            data.nationwide_count += 1
            continue
        pref = prefecture_of(judged.id)
        if pref is None or pref not in data.stats:
            data.unknown_count += 1
            continue

        stat = data.stats[pref]
        stat.count += 1
        if judged.relevance > stat.relevance:
            stat.relevance = judged.relevance
            stat.top_title = judged.title or judged.kind
            stat.top_action = judged.action
    return data
