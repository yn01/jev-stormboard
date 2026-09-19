"""判定結果の保存と読み込み。

判定は Jev の API を呼ぶので、同じ電文を二重に判定しないようにキャッシュする
(リプレイを繰り返してもコストが増えないようにするため)。結果は
`data/judged/judgements.jsonl` に追記し、再起動しても履歴が残るようにする。

`data/raw` は読み取り専用。ここからは書き込まない。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.judge import Judgement
from core.message import IndexRecord

ROOT = Path(__file__).resolve().parent.parent
JUDGED_DIR = ROOT / "data" / "judged"
JUDGED_PATH = JUDGED_DIR / "judgements.jsonl"


def _scale_max_of(answer_data: dict) -> int | None:
    """保存済みの答えから Score の目盛り最大値を取り出す。

    以前に保存したぶんには scale_max が無いので、その場合は
    probabilities のキー(段階の番号)から推測する。
    """
    if answer_data.get("scale_max") is not None:
        return int(answer_data["scale_max"])
    if answer_data.get("kind") != "score":
        return None
    keys: list[int] = []
    for key in (answer_data.get("probabilities") or {}):
        try:
            keys.append(int(key))
        except (TypeError, ValueError):
            continue
    return max(keys) if keys else None


def profile_fingerprint(profile: dict) -> str:
    """プロファイルの指紋(短いハッシュ)。

    判定結果に「どのプロファイルで判定したか」を残すために使う。
    プロファイルを書き換えると指紋が変わるので、過去の判定結果と
    食い違っていることを画面で知らせられる。
    """
    keys = ("id", "name", "area_code", "pref", "city", "profile")
    payload = json.dumps(
        {k: str(profile.get(k, "")) for k in keys}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


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
    scale_max: int | None = None  # Score の目盛りの最大値(0〜この値)

    def top_probabilities(self, n: int = 2) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def scale_ratio(self) -> float:
        """バーの長さに使う 0〜1 の比率。

        Score は目盛りの範囲に対する比率にする(範囲が分からないと
        バーの長さが意味を持たないため)。
        """
        try:
            value = float(self.value)
        except (TypeError, ValueError):
            return 0.0
        if self.kind == "score":
            top = self.scale_max or 0
            return value / top if top > 0 else 0.0
        if self.kind == "noul":
            return value
        return 0.0


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
    profile_id: str = ""  # どのプロファイルで判定したか
    profile_fingerprint: str = ""  # プロファイルの指紋(変更の検知に使う)
    answers: list[JudgedAnswer] = field(default_factory=list)

    @property
    def cache_key(self) -> str:
        """キャッシュの見出し。質問数が違えば別の結果になるので、電文IDと組にする。"""
        return f"{self.id}#{self.question_count}"

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
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "answers": [
                {
                    "key": a.key,
                    "label": a.label,
                    "aspect": a.aspect,
                    "kind": a.kind,
                    "value": a.value,
                    "confidence": a.confidence,
                    "certainty": round(a.certainty, 4),
                    "scale_max": a.scale_max,
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
            profile_id=data.get("profile_id", ""),
            profile_fingerprint=data.get("profile_fingerprint", ""),
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
                    scale_max=_scale_max_of(a),
                )
                for a in data.get("answers", [])
            ],
        )

    @classmethod
    def from_judgement(
        cls, judgement: Judgement, record: IndexRecord, profile: dict | None = None
    ) -> "JudgedMessage":
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
            profile_id=str((profile or {}).get("id", "")),
            profile_fingerprint=profile_fingerprint(profile or {}),
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
                    scale_max=a.scale_max,
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
        self._by_key: dict[str, JudgedMessage] = {}
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
                # 同じ電文・同じ質問数が複数行あれば、後の行(新しい判定)で上書きする
                self._by_key[judged.cache_key] = judged

    # -------------------------------------------------- 参照

    @staticmethod
    def key_of(message_id: str, question_count: int) -> str:
        return f"{message_id}#{question_count}"

    def has(self, message_id: str, question_count: int) -> bool:
        """その電文を、その質問数で判定済みか。

        10問と50問では結果が違うので、質問数ごとに別扱いにする。
        """
        with self._lock:
            return self.key_of(message_id, question_count) in self._by_key

    def get(self, message_id: str, question_count: int) -> JudgedMessage | None:
        with self._lock:
            return self._by_key.get(self.key_of(message_id, question_count))

    def all(self, question_count: int | None = None) -> list[JudgedMessage]:
        """新しい順(電文の発表時刻)に並べて返す。

        question_count を渡すと、その質問数で判定したものだけに絞る。
        """
        with self._lock:
            items = list(self._by_key.values())
        if question_count is not None:
            items = [j for j in items if j.question_count == question_count]
        return sorted(items, key=lambda j: j.updated, reverse=True)

    def count(self, question_count: int | None = None) -> int:
        if question_count is None:
            with self._lock:
                return len(self._by_key)
        return len(self.all(question_count))

    def average_latency_by_count(self) -> dict[int, tuple[float, int]]:
        """質問数ごとの平均レイテンシと件数。

        「質問数を増やしてもレイテンシがほとんど変わらない」ことを
        画面で見せるために使う(requirements.md R-61)。
        """
        totals: dict[int, list[float]] = {}
        with self._lock:
            items = list(self._by_key.values())
        for judged in items:
            totals.setdefault(judged.question_count, []).append(judged.latency_ms)
        return {n: (sum(v) / len(v), len(v)) for n, v in sorted(totals.items()) if v}

    def profile_fingerprints(self) -> set[str]:
        """保存済みの判定が、どのプロファイルで行われたか。"""
        with self._lock:
            return {j.profile_fingerprint for j in self._by_key.values() if j.profile_fingerprint}

    # -------------------------------------------------- 追記

    def add(self, judged: JudgedMessage) -> None:
        with self._lock:
            self._by_key[judged.cache_key] = judged
        self._append(judged)

    def _append(self, judged: JudgedMessage) -> None:
        line = json.dumps(judged.to_json(), ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
