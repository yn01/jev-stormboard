"""判定結果の保存と読み込み。

判定は Jev の API を呼ぶので、同じ電文を二重に判定しないようにキャッシュする
(リプレイを繰り返してもコストが増えないようにするため)。結果は
`data/judged/judgements.jsonl` に追記し、再起動しても履歴が残るようにする。

`data/raw` は読み取り専用。ここからは書き込まない。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.judge import Judgement
from core.message import IndexRecord

ROOT = Path(__file__).resolve().parent.parent
JUDGED_DIR = ROOT / "data" / "judged"
JUDGED_PATH = JUDGED_DIR / "judgements.jsonl"


@dataclass
class JudgedAnswer:
    """保存した判定結果のうち、質問1問ぶん。"""

    key: str
    label: str
    aspect: str
    kind: str
    value: object
    confidence: float | None
    certainty: float
    probabilities: dict[str, float] = field(default_factory=dict)

    def top_probabilities(self, n: int = 2) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:n]


@dataclass
class JudgedMessage:
    """保存した判定結果1件(電文1本ぶん)。"""

    id: str
    title: str  # 電文の見出し(Head/Title)
    kind: str  # 電文の種類名(Control/Title)
    author: str  # 発表官署
    updated: str  # 電文の発表時刻(UTC表記)
    report_datetime: str  # Head/ReportDateTime(JST表記)
    headline: str
    latency_ms: float
    model: str
    input_tokens: int | None
    output_tokens: int | None
    question_count: int
    judged_at: str
    answers: list[JudgedAnswer] = field(default_factory=list)

    # -------------------------------------------------- 取り出し

    def answer(self, key: str) -> JudgedAnswer | None:
        for a in self.answers:
            if a.key == key:
                return a
        return None

    def value_of(self, key: str, default: float = 0.0) -> float:
        a = self.answer(key)
        if a is None:
            return default
        try:
            return float(a.value)
        except (TypeError, ValueError):
            return default

    @property
    def relevance(self) -> float:
        """「この地域に関係する」の値(0〜1)。注目の判定の並べ替えに使う。"""
        return self.value_of("relevant")

    @property
    def action(self) -> str:
        a = self.answer("action")
        return str(a.value) if a is not None else ""

    # -------------------------------------------------- 変換

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "author": self.author,
            "updated": self.updated,
            "report_datetime": self.report_datetime,
            "headline": self.headline,
            "latency_ms": round(self.latency_ms, 1),
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "question_count": self.question_count,
            "judged_at": self.judged_at,
            "answers": [
                {
                    "key": a.key,
                    "label": a.label,
                    "aspect": a.aspect,
                    "kind": a.kind,
                    "value": a.value,
                    "confidence": a.confidence,
                    "certainty": round(a.certainty, 4),
                    "probabilities": {str(k): round(v, 4) for k, v in a.probabilities.items()},
                }
                for a in self.answers
            ],
        }

    @classmethod
    def from_json(cls, data: dict) -> "JudgedMessage":
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            kind=data.get("kind", ""),
            author=data.get("author", ""),
            updated=data.get("updated", ""),
            report_datetime=data.get("report_datetime", ""),
            headline=data.get("headline", ""),
            latency_ms=float(data.get("latency_ms", 0.0)),
            model=data.get("model", ""),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            question_count=int(data.get("question_count", 0)),
            judged_at=data.get("judged_at", ""),
            answers=[
                JudgedAnswer(
                    key=a["key"],
                    label=a.get("label", a["key"]),
                    aspect=a.get("aspect", ""),
                    kind=a.get("kind", ""),
                    value=a.get("value"),
                    confidence=a.get("confidence"),
                    certainty=float(a.get("certainty", 0.0)),
                    probabilities={k: float(v) for k, v in (a.get("probabilities") or {}).items()},
                )
                for a in data.get("answers", [])
            ],
        )

    @classmethod
    def from_judgement(cls, judgement: Judgement, record: IndexRecord) -> "JudgedMessage":
        return cls(
            id=record.id,
            title=judgement.message.title or record.title,
            kind=judgement.message.kind or record.title,
            author=record.author or judgement.message.publishing_office,
            updated=record.updated,
            report_datetime=judgement.message.report_datetime,
            headline=judgement.message.headline or record.summary,
            latency_ms=judgement.latency_ms,
            model=judgement.model,
            input_tokens=judgement.input_tokens,
            output_tokens=judgement.output_tokens,
            question_count=len(judgement.answers),
            judged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            answers=[
                JudgedAnswer(
                    key=a.key,
                    label=a.label,
                    aspect=a.aspect,
                    kind=a.kind,
                    value=a.value,
                    confidence=a.confidence,
                    certainty=a.certainty,
                    probabilities={str(k): float(v) for k, v in a.probabilities.items()},
                )
                for a in judgement.answers
            ],
        )


class JudgedStore:
    """判定結果のキャッシュ(メモリ)と永続化(jsonl)。

    スレッドから同時に呼ばれるのでロックで守る。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or JUDGED_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._by_id: dict[str, JudgedMessage] = {}
        self._load()

    def _load(self) -> None:
        """保存済みの判定結果を読み込む。壊れた行は飛ばす。"""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    judged = JudgedMessage.from_json(json.loads(line))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                # 同じ電文が複数行あれば、後の行(新しい判定)で上書きする
                self._by_id[judged.id] = judged

    # -------------------------------------------------- 参照

    def has(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._by_id

    def get(self, message_id: str) -> JudgedMessage | None:
        with self._lock:
            return self._by_id.get(message_id)

    def all(self) -> list[JudgedMessage]:
        """新しい順(電文の発表時刻)に並べて返す。"""
        with self._lock:
            items = list(self._by_id.values())
        return sorted(items, key=lambda j: j.updated, reverse=True)

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)

    # -------------------------------------------------- 追記

    def add(self, judged: JudgedMessage) -> None:
        with self._lock:
            self._by_id[judged.id] = judged
        self._append(judged)

    def _append(self, judged: JudgedMessage) -> None:
        line = json.dumps(judged.to_json(), ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
