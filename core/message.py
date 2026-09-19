"""保存済みの防災電文XMLを読み、Jev に渡せる形に整える。

電文の種類ごとに構造が違うので、すべてに対応はしない。気象警報・注意報
(VPWW53 / VPWW54)と府県気象情報(VPFJ50)を優先して扱い、それ以外の種類は
共通部分(見出し・本文テキスト)だけを拾う(requirements.md R-26)。

要素ごとに既定の名前空間が異なる(jmaxml1/ 、jmaxml1/body/meteorology1/ 、
jmaxml1/informationBasis1/)ため、パース後にタグから名前空間を落として扱う。
"""

from __future__ import annotations

import gzip
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "data" / "index.jsonl"

# 本文が長くなりすぎないよう、Jev に渡すテキストの上限
MAX_BODY_CHARS = 4000


def strip_namespaces(root: ET.Element) -> ET.Element:
    """パース後のツリーから名前空間を落とし、タグを local name にする。"""
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


def parse_xml(data: bytes) -> ET.Element:
    return strip_namespaces(ET.fromstring(data))


def read_message_file(path: Path) -> ET.Element:
    """保存済みの .xml.gz を読んでパースする。"""
    if path.suffix == ".gz":
        data = gzip.decompress(path.read_bytes())
    else:
        data = path.read_bytes()
    return parse_xml(data)


# ---------------------------------------------------------------- 取り出した電文


@dataclass
class Area:
    name: str
    code: str
    kinds: list[str] = field(default_factory=list)  # 「大雨警報(発表)」など
    level: str = ""  # Warning の type(府県予報区等 / 市町村等 など)

    def describe(self) -> str:
        if self.kinds:
            return f"{self.name}({self.code}): {'、'.join(self.kinds)}"
        return f"{self.name}({self.code})"


@dataclass
class Message:
    """Jev に渡すために整理した電文1本。"""

    kind: str = ""  # Control/Title 電文の種類名
    title: str = ""  # Head/Title 見出し
    headline: str = ""  # Head/Headline/Text 要約文
    body_text: str = ""  # 本文(平文)
    publishing_office: str = ""  # 発表官署
    report_datetime: str = ""  # 発表時刻(JST表記のまま)
    info_type: str = ""  # 発表 / 訂正 / 取消
    info_kind: str = ""
    serial: str = ""
    status: str = ""  # 通常 / 訓練 / 試験
    areas: list[Area] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # 発表中の警報・注意報のまとめ

    def to_state(self) -> dict:
        """Jev の state に入れる辞書にする。"""
        state: dict = {
            "kind": self.kind,
            "title": self.title,
            "headline": self.headline,
            "publishing_office": self.publishing_office,
            "report_datetime": self.report_datetime,
            "info_type": self.info_type,
            "status": self.status,
        }
        if self.serial:
            state["serial"] = self.serial
        if self.body_text:
            state["body_text"] = self.body_text[:MAX_BODY_CHARS]
        if self.areas:
            state["areas"] = [a.describe() for a in self.areas]
        if self.warnings:
            state["warnings"] = self.warnings
        return state


# ---------------------------------------------------------------- 抽出


def _text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    value = parent.findtext(tag)
    return value.strip() if value else ""


def extract(root: ET.Element) -> Message:
    """パース済みのXMLから Message を組み立てる。"""
    control = root.find("Control")
    head = root.find("Head")
    body = root.find("Body")

    message = Message(
        kind=_text(control, "Title"),
        title=_text(head, "Title"),
        publishing_office=_text(control, "PublishingOffice"),
        report_datetime=_text(head, "ReportDateTime"),
        info_type=_text(head, "InfoType"),
        info_kind=_text(head, "InfoKind"),
        serial=_text(head, "Serial"),
        status=_text(control, "Status"),
    )

    headline = head.find("Headline") if head is not None else None
    message.headline = _text(headline, "Text")

    if body is not None:
        _fill_warnings(body, message)
        message.body_text = _collect_body_text(body)

    return message


def _fill_warnings(body: ET.Element, message: Message) -> None:
    """気象警報・注意報(VPWW53 / VPWW54)の Warning から地域と種別を取り出す。

    4階層(府県予報区等 / 一次細分区域等 / 市町村等をまとめた地域等 / 市町村等)の
    うち、市町村等は数が多すぎるので「発表中のものがある地域」だけを拾う。
    """
    seen_kinds: list[str] = []
    for warning in body.findall("Warning"):
        level = warning.get("type", "")
        for item in warning.findall("Item"):
            area_elem = item.find("Area")
            if area_elem is None:
                continue
            kinds: list[str] = []
            for kind in item.findall("Kind"):
                name = _text(kind, "Name")
                if not name or name == "解除":
                    continue
                status = _text(kind, "Status")
                label = f"{name}({status})" if status else name
                kinds.append(label)
                if label not in seen_kinds:
                    seen_kinds.append(label)

            # 市町村等は件数が多いので、発表中のものがある地域だけ残す
            if "市町村等" in level and not kinds:
                continue
            message.areas.append(
                Area(
                    name=_text(area_elem, "Name"),
                    code=_text(area_elem, "Code"),
                    kinds=kinds,
                    level=level,
                )
            )
    message.warnings = seen_kinds


def _collect_body_text(body: ET.Element) -> str:
    """Body から平文のテキストを集める。

    府県気象情報(VPFJ50)は Body/Comment/Text に本文が入る。ほかの種類でも
    Text / Notice / Sentence といった平文の要素があれば拾う。
    """
    chunks: list[str] = []
    for elem in body.iter():
        if elem.tag not in ("Text", "Notice", "Sentence"):
            continue
        value = (elem.text or "").strip()
        if not value or value == "なし":
            continue
        if value not in chunks:
            chunks.append(value)
    return "\n\n".join(chunks)


# ---------------------------------------------------------------- 索引


@dataclass
class IndexRecord:
    """index.jsonl の1行。"""

    id: str
    title: str
    updated: str
    author: str
    feed: str
    url: str
    path: str
    bytes: int = 0
    fetched_at: str = ""
    summary: str = ""

    @classmethod
    def from_json(cls, line: str) -> "IndexRecord":
        record = json.loads(line)
        return cls(
            id=record.get("id", ""),
            title=record.get("title", ""),
            updated=record.get("updated", ""),
            author=record.get("author", ""),
            feed=record.get("feed", ""),
            url=record.get("url", ""),
            path=record.get("path", ""),
            bytes=int(record.get("bytes", 0)),
            fetched_at=record.get("fetched_at", ""),
            summary=record.get("summary", ""),
        )

    @property
    def full_path(self) -> Path:
        return ROOT / self.path


def load_index(index_path: Path | None = None) -> list[IndexRecord]:
    """索引を読み込む。data/raw は歩かない(数千ファイルになるため)。"""
    path = index_path or INDEX_PATH
    records: list[IndexRecord] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(IndexRecord.from_json(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return records


def find_by_id(records: list[IndexRecord], message_id: str) -> IndexRecord | None:
    """電文IDで探す。URL全体でも、ファイル名だけでも引けるようにする。"""
    for record in records:
        if record.id == message_id:
            return record
    # 末尾の一致(ファイル名だけを指定された場合)
    for record in records:
        if record.id.rsplit("/", 1)[-1] == message_id:
            return record
    for record in records:
        if message_id in record.id or message_id in record.path:
            return record
    return None


def latest(records: list[IndexRecord]) -> IndexRecord | None:
    """updated が最も新しいものを返す。"""
    if not records:
        return None
    return max(records, key=lambda r: r.updated)
