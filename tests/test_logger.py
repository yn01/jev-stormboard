"""ロガーの最小限のテスト。

パース / フィルタ / 重複排除 / 再起動時の復元 の 4 点を確認する。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from logger.feed import parse_feed, should_save
from logger.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str, feed_key: str):
    return parse_feed((FIXTURES / name).read_bytes(), feed_key)


# ---------------------------------------------------------------- パース


def test_随時フィードをパースできる():
    entries = load("extra_sample.xml", "extra")
    assert len(entries) == 4

    first = entries[0]
    assert first.id.startswith("https://www.data.jma.go.jp/developer/xml/data/")
    assert first.url == first.id
    assert first.title
    assert first.author
    assert first.summary
    assert first.feed == "extra"


def test_updatedをJSTの日付に直してファイル名を決める():
    from logger.feed import JST, parse_updated

    entry = load("extra_sample.xml", "extra")[0]

    # updated は UTC の Z 表記
    assert entry.updated.endswith("Z")
    assert entry.updated_dt.tzinfo is not None
    assert entry.date_jst == entry.updated_dt.astimezone(JST).strftime("%Y-%m-%d")

    # 日付は JST で決まる。UTC の 17:50 は JST では翌日の 02:50
    assert parse_updated("2026-09-18T17:50:14Z").astimezone(JST).strftime(
        "%Y-%m-%d %H:%M"
    ) == "2026-09-19 02:50"

    # ファイル名は電文 URL の末尾
    assert entry.filename == entry.url.rsplit("/", 1)[-1]
    assert entry.filename.endswith(".xml")


def test_壊れたXMLではParseErrorになる():
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        parse_feed(b"<feed><entry></feed>", "extra")


# ---------------------------------------------------------------- フィルタ


def test_随時フィードは全件が保存対象():
    entries = load("extra_sample.xml", "extra")
    assert all(should_save(e) for e in entries)


def test_定時フィードは警報級の可能性だけが保存対象():
    entries = load("regular_sample.xml", "regular")
    kept = [e for e in entries if should_save(e)]

    assert len(entries) == 5
    assert len(kept) == 2
    assert all("警報級の可能性" in e.title for e in kept)


def test_除外リストで種類を落とせる(monkeypatch):
    from logger import config, feed

    entries = load("extra_sample.xml", "extra")
    target = entries[0].title
    monkeypatch.setattr(config, "TITLE_EXCLUDES", (target,))
    monkeypatch.setattr(feed.config, "TITLE_EXCLUDES", (target,))

    kept = [e for e in entries if should_save(e)]
    assert all(e.title != target for e in kept)
    assert len(kept) < len(entries)


# ---------------------------------------------------------------- 保存と重複排除


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(raw_dir=tmp_path / "raw", index_path=tmp_path / "index.jsonl")


def test_電文を保存すると索引に1行追記される(store: Store, tmp_path: Path):
    entry = load("extra_sample.xml", "extra")[0]
    body = b"<Report>test</Report>"

    record = store.save(entry, body)
    assert record is not None
    assert record["id"] == entry.id
    assert record["bytes"] == len(body)
    assert record["feed"] == "extra"
    assert record["summary"] == entry.summary

    # 本体は gzip で保存され、元の内容に戻せる
    saved = tmp_path / record["path"]
    assert saved.exists()
    assert gzip.decompress(saved.read_bytes()) == body

    # 索引は 1 行
    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == entry.id


def test_同じidは2回保存されない(store: Store, tmp_path: Path):
    entry = load("extra_sample.xml", "extra")[0]

    assert store.save(entry, b"first") is not None
    assert store.save(entry, b"second") is None  # 2 回目は保存されない

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    # 中身も 1 回目のまま上書きされていない
    saved = tmp_path / json.loads(lines[0])["path"]
    assert gzip.decompress(saved.read_bytes()) == b"first"


def test_一時ファイルが残らない(store: Store, tmp_path: Path):
    entry = load("extra_sample.xml", "extra")[0]
    store.save(entry, b"body")
    assert list((tmp_path / "raw").rglob(".tmp-*")) == []


# ---------------------------------------------------------------- 再起動時の復元


def test_再起動しても保存済みidを引き継ぐ(tmp_path: Path):
    entries = load("extra_sample.xml", "extra")
    raw, index = tmp_path / "raw", tmp_path / "index.jsonl"

    first = Store(raw_dir=raw, index_path=index)
    for entry in entries[:2]:
        first.save(entry, b"body")
    assert first.seen_count == 2

    # 別プロセスでの再起動に相当する
    second = Store(raw_dir=raw, index_path=index)
    assert second.seen_count == 2
    assert all(second.has(e.id) for e in entries[:2])

    # すでに保存済みの分は重複しない
    assert second.save(entries[0], b"body") is None
    # 未保存の分は保存される
    assert second.save(entries[2], b"body") is not None

    lines = index.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert len({json.loads(line)["id"] for line in lines}) == 3


def test_索引に壊れた行があっても復元できる(tmp_path: Path):
    entries = load("extra_sample.xml", "extra")
    raw, index = tmp_path / "raw", tmp_path / "index.jsonl"

    first = Store(raw_dir=raw, index_path=index)
    first.save(entries[0], b"body")

    # 書き込み途中で落ちて、行が欠けた状態を作る
    with index.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "broken", "tit\n')

    second = Store(raw_dir=raw, index_path=index)
    assert second.seen_count == 1
    assert second.has(entries[0].id)


# ---------------------------------------------------------------- stats


def test_statsが種類別の件数を集計する(tmp_path: Path):
    from logger.stats import render, summarize

    entries = load("extra_sample.xml", "extra")
    store = Store(raw_dir=tmp_path / "raw", index_path=tmp_path / "index.jsonl")
    for entry in entries:
        store.save(entry, b"x" * 100)

    from logger.stats import load_records

    summary = summarize(load_records(tmp_path / "index.jsonl"))
    assert summary["total"] == 4
    assert summary["total_bytes"] == 400
    assert sum(summary["by_title"].values()) == 4

    text = render(summary)
    assert "保存件数: 4件" in text
    assert entries[0].title in text
