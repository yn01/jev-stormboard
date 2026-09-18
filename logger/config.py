"""ロガーの設定。変更したい値はすべてここに集約する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# プロジェクトのルート(logger/ の 1 つ上)
ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- フィード


@dataclass(frozen=True)
class Feed:
    """取得対象のフィード 1 つ分。"""

    key: str  # 索引に記録する種別: "extra" / "regular"
    url: str
    kind: str  # "high"(高頻度) / "long"(長期)


FEEDS: tuple[Feed, ...] = (
    Feed("extra", "https://www.data.jma.go.jp/developer/xml/feed/extra.xml", "high"),
    Feed("extra", "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml", "long"),
    Feed("regular", "https://www.data.jma.go.jp/developer/xml/feed/regular.xml", "high"),
    Feed("regular", "https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml", "long"),
)

# ---------------------------------------------------------------- 保存対象

# 随時(extra)フィードは全エントリを保存する。
# 定時(regular)フィードは、title が以下のいずれかを含むものだけを保存する。
# 2026-09-19 時点の実際の表記は「警報級の可能性（明日まで）」「警報級の可能性（明後日以降）」。
REGULAR_TITLE_INCLUDES: tuple[str, ...] = ("警報級の可能性",)

# 種類(title)ごとの除外リスト。ここに含まれる文字列が title に含まれる電文は保存しない。
# 件数が極端に多い種類(大雨危険度通知など)を stats で見つけて、必要ならここに足す。
TITLE_EXCLUDES: tuple[str, ...] = ()

# ---------------------------------------------------------------- 動作


POLL_INTERVAL_SEC = 60  # 高頻度フィードのポーリング間隔。短くしないこと
BACKFILL_INTERVAL_SEC = 60 * 60  # 長期フィードによる穴埋めの間隔(起動時にも実行)

# 穴埋めで遡る上限(時間)。長期フィードは 7 日分(約 7,500 件)を含むため、
# 初回起動でその全件を取りに行かないよう上限を設ける。これより長く停止して
# いた場合は、必要に応じてこの値を一時的に大きくする。
BACKFILL_MAX_AGE_HOURS = 24

HTTP_TIMEOUT_SEC = 30.0
USER_AGENT = "jev-stormboard-logger/0.1 (personal demo; +https://github.com/yn01/jev-stormboard)"

DOWNLOAD_CONCURRENCY = 2  # 電文本体の並列度。上げないこと
DOWNLOAD_INTERVAL_SEC = 0.2  # 電文 1 件ごとの待ち。気象庁サーバーへの配慮

RETRY_MAX = 4  # 指数バックオフのリトライ回数
RETRY_BASE_SEC = 2.0

# ---------------------------------------------------------------- 保存先


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    data: Path = field(default_factory=lambda: ROOT / "data")
    raw: Path = field(default_factory=lambda: ROOT / "data" / "raw")
    index: Path = field(default_factory=lambda: ROOT / "data" / "index.jsonl")
    logs: Path = field(default_factory=lambda: ROOT / "logs")
    log_file: Path = field(default_factory=lambda: ROOT / "logs" / "logger.log")


PATHS = Paths()

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
