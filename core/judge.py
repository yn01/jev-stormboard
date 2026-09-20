"""保存済みの防災電文1本を Jev で判定する。

    python -m core.judge --latest            # 最新の電文を判定
    python -m core.judge --id <電文ID>       # 電文を指定して判定(繰り返し試せる)

全問を1回の Jev リクエストにまとめて送る(requirements.md R-24)。
レイテンシと入力トークン数をログに出す(R-30)。後でコスト表示に使う。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .message import IndexRecord, Message, extract, find_by_id, latest, load_index, read_message_file
from .questions import (
    DEFAULT_QUESTION_COUNT,
    QUESTIONS,
    Q,
    build_questions,
    scale_max,
    select_questions,
)

ROOT = Path(__file__).resolve().parent.parent

# 公開用の仮プロファイル。リポジトリは public なので、ここには個人情報を書かない。
PROFILE_PATH = ROOT / "profile.yaml"

# 自分の実際の状況を書くファイル。.gitignore に入れてあり、コミットされない。
# こちらがあれば優先して読む。
LOCAL_PROFILE_PATH = ROOT / "profile.local.yaml"

log = logging.getLogger("core.judge")


# ---------------------------------------------------------------- APIキー


def load_api_key() -> None:
    """TYPESAFE_API_KEY が未設定なら .claude/settings.local.json の env から読む。

    キーの値は絶対に出力しない。
    """
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    settings_path = ROOT / ".claude" / "settings.local.json"
    if not settings_path.exists():
        return
    try:
        with settings_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        key = data.get("env", {}).get("TYPESAFE_API_KEY")
    except (OSError, json.JSONDecodeError):
        return
    if key:
        os.environ["TYPESAFE_API_KEY"] = key


# ---------------------------------------------------------------- プロファイル


def profile_path() -> Path:
    """実際に読むプロファイルのパス。

    `profile.local.yaml` があればそちらを優先する。無ければ公開用の
    `profile.yaml` を使う(requirements.md の「公開の方針」)。
    """
    return LOCAL_PROFILE_PATH if LOCAL_PROFILE_PATH.exists() else PROFILE_PATH


def load_profile(path: Path | None = None) -> dict:
    """プロファイルを読む。

    path を渡さない場合は profile_path() が選んだファイルを読む。
    どちらを読んだかは `_source` に入れて返す(画面に出すため)。
    """
    target = path or profile_path()
    with target.open(encoding="utf-8") as fh:
        profile = yaml.safe_load(fh) or {}
    profile["_source"] = target.name
    profile["_is_local"] = target == LOCAL_PROFILE_PATH
    return profile


# ---------------------------------------------------------------- 判定結果


@dataclass
class Answer:
    """質問1問ぶんの答え。

    Noul の値は「命題が真である確率」そのもので、Choice / Score の confidence とは
    意味が異なる(requirements.md「Noul の確信度の扱い」)。Noul には SDK が
    confidence を返さないため None を入れ、代わりに certainty で 0.5 からの距離を持つ。
    """

    key: str
    label: str
    aspect: str
    kind: str  # "choice" / "score" / "noul"
    value: Any  # 選択肢名 / 数値 / 0〜1 の確率
    probabilities: dict[Any, float]
    confidence: float | None  # Noul は None
    scale_max: int | None = None  # Score の目盛りの最大値(0〜この値)。他の型は None

    @property
    def certainty(self) -> float:
        """しきい値で絞り込むときに使う「どれだけ判断がついているか」(0〜1)。

        Choice / Score は confidence をそのまま使う。Noul は confidence が
        返らないので、0.5 からの距離を使う(0 または 1 に近いほど判断がついている)。
        """
        if self.kind == "noul":
            return abs(float(self.value) - 0.5) * 2
        return self.confidence if self.confidence is not None else 0.0

    def display_value(self) -> str:
        if self.kind == "noul":
            return f"{float(self.value):.3f}"
        if self.kind == "score":
            # 目盛りの範囲が分かるよう「2.82 / 4」の形で出す
            if self.scale_max is not None:
                return f"{float(self.value):.2f} / {self.scale_max}"
            return f"{float(self.value):.2f}"
        return str(self.value)


@dataclass
class Judgement:
    """電文1本の判定結果。"""

    record: IndexRecord
    message: Message
    answers: list[Answer]
    latency_ms: float
    model: str
    input_tokens: int | None
    output_tokens: int | None

    def by_key(self, key: str) -> Answer:
        for answer in self.answers:
            if answer.key == key:
                return answer
        raise KeyError(key)


# ---------------------------------------------------------------- state の組み立て


def build_state(message: Message, profile: dict) -> dict:
    """Jev に渡す state を組み立てる。

    電文の見出しと本文、発表官署、発表時刻、対象地域、そしてプロファイルを含める
    (requirements.md R-25)。
    """
    # 地域を絞るときに、この人の都道府県を先に残す
    prefix = str(profile.get("area_code", ""))[:2]
    return {
        "message": message.to_state(prefer_prefix=prefix),
        "person": {
            "name": profile.get("name", ""),
            "pref": profile.get("pref", ""),
            "city": profile.get("city", ""),
            "area_code": str(profile.get("area_code", "")),
            "profile": profile.get("profile", ""),
        },
    }


# ---------------------------------------------------------------- 判定


def _to_answer(q: Q, raw: Any) -> Answer:
    """SDK の答えを、型ごとの違いを吸収した Answer にする。"""
    kind = getattr(raw, "type", "")
    if kind == "choice":
        return Answer(
            key=q.key,
            label=q.label,
            aspect=q.aspect,
            kind="choice",
            value=raw.choice,
            probabilities=dict(raw.probabilities),
            confidence=raw.confidence,
        )
    if kind == "score":
        return Answer(
            key=q.key,
            label=q.label,
            aspect=q.aspect,
            kind="score",
            value=raw.score,
            probabilities={int(k): v for k, v in raw.probabilities.items()},
            confidence=raw.confidence,
            scale_max=scale_max(q.question),
        )
    # noul: 値そのものが「真である確率」。confidence は返らない
    return Answer(
        key=q.key,
        label=q.label,
        aspect=q.aspect,
        kind="noul",
        value=raw.noul,
        probabilities={"真": raw.noul, "偽": 1.0 - raw.noul},
        confidence=None,
    )


def judge(
    record: IndexRecord,
    profile: dict,
    client: Any = None,
    question_count: int | None = None,
) -> Judgement:
    """電文1本を判定する。全問を1リクエストにまとめて送る。

    question_count を渡すと、QUESTIONS の先頭からその数だけを使う
    (画面の「10問 / 30問 / 50問」の切り替え用)。
    """
    message = extract(read_message_file(record.full_path))
    state = build_state(message, profile)
    questions = build_questions(question_count)
    asked = select_questions(question_count)

    if client is None:
        load_api_key()
        from typesafe_sdk import TypeSafeClient  # 遅延import: APIキー設定後に読み込む

        client = TypeSafeClient()

    started = time.perf_counter()
    response = client.system_one(state, questions)
    latency_ms = (time.perf_counter() - started) * 1000

    answers = [_to_answer(q, response.answers[q.key]) for q in asked]

    log.info(
        "判定 %d問 / %.0fms / model=%s / 入力トークン=%s 出力トークン=%s",
        len(questions),
        latency_ms,
        response.model,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    return Judgement(
        record=record,
        message=message,
        answers=answers,
        latency_ms=latency_ms,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


# ---------------------------------------------------------------- 表示


def _width(text: str) -> int:
    """全角を2、半角を1として数える。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def render(judgement: Judgement, profile: dict) -> str:
    record, message = judgement.record, judgement.message
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append(f"電文: {message.kind}")
    lines.append(f"見出し: {message.title}")
    if message.headline:
        lines.append(f"要約: {message.headline}")
    lines.append(f"発表: {message.publishing_office} / {message.report_datetime} / {message.info_type}")
    if message.warnings:
        lines.append(f"発表中: {'、'.join(message.warnings)}")
    if message.areas:
        lines.append(f"対象地域: {len(message.areas)}件 (例: {message.areas[0].name})")
    lines.append(f"ID: {record.id.rsplit('/', 1)[-1]}")
    lines.append("")
    lines.append(f"判定対象: {profile.get('name','')} ({profile.get('pref','')}{profile.get('city','')})")
    lines.append("=" * 72)
    lines.append("")

    # 表
    headers = ("観点", "質問", "型", "値", "確信度", "内訳")
    rows: list[tuple[str, ...]] = []
    for answer in judgement.answers:
        if answer.kind == "noul":
            conf = f"{answer.certainty:.3f}*"
        else:
            conf = f"{answer.certainty:.3f}"
        top = sorted(answer.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:2]
        detail = " ".join(f"{k}={v:.2f}" for k, v in top)
        rows.append(
            (answer.aspect, answer.label, answer.kind, answer.display_value(), conf, detail)
        )

    widths = [max(_width(h), *(_width(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines.append("  ".join(_pad(h, w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(c, w) for c, w in zip(row, widths)))

    lines.append("")
    lines.append("* Noul の確信度は、値の 0.5 からの距離 (abs(値-0.5)*2)。")
    lines.append("  Noul の値そのものが「命題が真である確率」で、Choice/Score の確信度とは意味が異なる。")
    lines.append("")
    lines.append(
        f"レイテンシ: {judgement.latency_ms:.0f}ms / モデル: {judgement.model} / "
        f"入力トークン: {judgement.input_tokens} / 出力トークン: {judgement.output_tokens} "
        f"/ 質問数: {len(judgement.answers)}問"
    )
    lines.append("")
    lines.append("⚠ これはデモです。実際の防災判断には使わないでください。")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def select_record(records: list[IndexRecord], args: argparse.Namespace) -> IndexRecord | None:
    if args.id:
        return find_by_id(records, args.id)
    return latest(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="保存済みの防災電文を Jev で判定する")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true", help="最新の電文を判定する(既定)")
    group.add_argument("--id", type=str, default=None, help="電文ID(URL全体、またはファイル名)を指定して判定する")
    parser.add_argument("--profile", type=Path, default=None, help="プロファイルのYAML(既定: profile.yaml)")
    parser.add_argument(
        "--questions", type=int, default=None, help="使う質問数(先頭からこの数だけ。既定: 10)"
    )
    parser.add_argument("--json", action="store_true", help="結果をJSONで出力する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    records = load_index()
    if not records:
        print("索引が空です。先にロガーを動かしてください。", file=sys.stderr)
        return 1

    record = select_record(records, args)
    if record is None:
        print(f"電文が見つかりません: {args.id}", file=sys.stderr)
        return 1
    if not record.full_path.exists():
        print(f"電文ファイルがありません: {record.path}", file=sys.stderr)
        return 1

    profile = load_profile(args.profile)
    count = args.questions if args.questions else DEFAULT_QUESTION_COUNT
    judgement = judge(record, profile, question_count=count)

    if args.json:
        payload = {
            "id": judgement.record.id,
            "title": judgement.message.title,
            "kind": judgement.message.kind,
            "latency_ms": round(judgement.latency_ms, 1),
            "model": judgement.model,
            "input_tokens": judgement.input_tokens,
            "output_tokens": judgement.output_tokens,
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
                for a in judgement.answers
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(judgement, profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
