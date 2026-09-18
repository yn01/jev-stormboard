"""Atom フィードのパースと、保存対象の絞り込み。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import config

ATOM = "{http://www.w3.org/2005/Atom}"
JST = timezone(timedelta(hours=9), "JST")


@dataclass(frozen=True)
class Entry:
    """フィードの 1 エントリ。id は電文 URL と同一。"""

    id: str
    title: str
    updated: str  # フィードの表記のまま(例 "2026-09-18T17:50:14Z")
    author: str
    url: str
    summary: str
    feed: str  # "extra" / "regular"

    @property
    def updated_dt(self) -> datetime:
        return parse_updated(self.updated)

    @property
    def date_jst(self) -> str:
        """保存先ディレクトリに使う、JST での日付 (YYYY-MM-DD)。"""
        return self.updated_dt.astimezone(JST).strftime("%Y-%m-%d")

    @property
    def filename(self) -> str:
        """電文 URL の末尾をファイル名にする (例 20260918175015_0_VPWW53_030000.xml)。"""
        name = self.url.rsplit("/", 1)[-1] or "unknown.xml"
        # パス区切りなどが混ざっても安全な名前にしておく
        return name.replace("/", "_").replace("\\", "_")


def parse_updated(value: str) -> datetime:
    """フィードの updated を aware な datetime にする。"""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def parse_feed(xml_bytes: bytes, feed_key: str) -> list[Entry]:
    """フィードの XML から Entry の一覧を取り出す。

    パースに失敗した場合は ET.ParseError を投げる。個々のエントリに
    id や link が無い場合は、そのエントリだけを飛ばす。
    """
    root = ET.fromstring(xml_bytes)
    entries: list[Entry] = []
    for node in root.findall(f"{ATOM}entry"):
        entry_id = _text(node.find(f"{ATOM}id"))
        link = node.find(f"{ATOM}link")
        url = link.get("href", "") if link is not None else ""
        url = url or entry_id
        if not entry_id or not url:
            continue
        author_node = node.find(f"{ATOM}author")
        author = _text(author_node.find(f"{ATOM}name")) if author_node is not None else ""
        entries.append(
            Entry(
                id=entry_id,
                title=_text(node.find(f"{ATOM}title")),
                updated=_text(node.find(f"{ATOM}updated")),
                author=author,
                url=url,
                summary=_text(node.find(f"{ATOM}content")),
                feed=feed_key,
            )
        )
    return entries


def should_save(entry: Entry) -> bool:
    """保存対象かどうかを判定する。

    - 随時(extra): 全エントリが対象
    - 定時(regular): title に REGULAR_TITLE_INCLUDES のいずれかを含むものだけ
    - どちらも TITLE_EXCLUDES に当たるものは除外する
    """
    if any(word in entry.title for word in config.TITLE_EXCLUDES):
        return False
    if entry.feed == "regular":
        return any(word in entry.title for word in config.REGULAR_TITLE_INCLUDES)
    return True
