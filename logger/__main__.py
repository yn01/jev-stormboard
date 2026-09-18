"""ロガー本体。python -m logger で起動する。

高頻度フィードを 60 秒ごとにポーリングし、起動時と 1 時間ごとに長期フィードで
穴埋めする。通信エラーや XML のパース失敗ではプロセスを落とさず、失敗した
電文は次の周期で再び拾い直す(index.jsonl に載らないため)。
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

import httpx

from . import config
from .feed import Entry, parse_feed, should_save
from .http import Fetcher, NotModified
from .store import Store

log = logging.getLogger("logger")

# SIGINT / SIGTERM を受けたら立てる。ループはこれを見て安全に抜ける
_stop = threading.Event()


def setup_logging() -> None:
    config.PATHS.logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = RotatingFileHandler(
        config.PATHS.log_file,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _handle_signal(signum: int, _frame: object) -> None:
    log.info("シグナル %s を受信。安全に終了します", signal.Signals(signum).name)
    _stop.set()


def fetch_documents(entries: list[Entry], fetcher: Fetcher, store: Store) -> int:
    """未保存の電文本体をダウンロードして保存する。保存した件数を返す。

    1 件の失敗で全体を止めない。失敗した電文は索引に載らないので、次の周期で
    もう一度対象になる。
    """
    todo = [e for e in entries if not store.has(e.id)]
    if not todo:
        return 0

    saved = 0
    lock = threading.Lock()

    def work(entry: Entry) -> None:
        nonlocal saved
        if _stop.is_set():
            return
        try:
            body = fetcher.get_document(entry.url)
        except httpx.HTTPError as exc:
            log.warning("電文の取得に失敗: %s (%s)", entry.url, exc)
            return
        with lock:
            # 同一周期で重複しないよう、保存はロックの中で行う
            record = store.save(entry, body)
            if record is not None:
                saved += 1
                log.info("保存 %s | %s | %s", record["path"], entry.title, entry.author)
        time.sleep(config.DOWNLOAD_INTERVAL_SEC)

    with ThreadPoolExecutor(max_workers=config.DOWNLOAD_CONCURRENCY) as pool:
        list(pool.map(work, todo))
    return saved


def poll_feed(feed: config.Feed, fetcher: Fetcher, store: Store) -> None:
    """フィードを 1 つ取得し、保存対象の電文を保存する。"""
    try:
        xml_bytes = fetcher.get_feed(feed.url)
    except NotModified:
        log.debug("更新なし: %s", feed.url)
        return
    except httpx.HTTPError as exc:
        log.warning("フィードの取得に失敗: %s (%s)", feed.url, exc)
        return

    try:
        entries = parse_feed(xml_bytes, feed.key)
    except ET.ParseError as exc:
        log.warning("フィードのパースに失敗: %s (%s)", feed.url, exc)
        return

    targets = [e for e in entries if should_save(e)]
    saved = fetch_documents(targets, fetcher, store)
    if saved:
        log.info(
            "%s (%s): %d件中 %d件が保存対象、%d件を新規保存",
            feed.key,
            feed.kind,
            len(entries),
            len(targets),
            saved,
        )


def run() -> int:
    setup_logging()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    store = Store()
    log.info("ロガーを起動しました (保存済み %d件)", store.seen_count)

    high = [f for f in config.FEEDS if f.kind == "high"]
    long = [f for f in config.FEEDS if f.kind == "long"]

    last_backfill = 0.0
    with Fetcher() as fetcher:
        while not _stop.is_set():
            started = time.monotonic()

            # 起動時と 1 時間ごとに、長期フィードで停止中の分を穴埋めする
            if started - last_backfill >= config.BACKFILL_INTERVAL_SEC or last_backfill == 0.0:
                log.info("長期フィードで穴埋めします")
                for feed in long:
                    if _stop.is_set():
                        break
                    poll_feed(feed, fetcher, store)
                last_backfill = time.monotonic()

            for feed in high:
                if _stop.is_set():
                    break
                poll_feed(feed, fetcher, store)

            # 処理にかかった時間を差し引いて待つ
            elapsed = time.monotonic() - started
            _stop.wait(max(0.0, config.POLL_INTERVAL_SEC - elapsed))

    log.info("ロガーを終了しました (保存済み %d件)", store.seen_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
