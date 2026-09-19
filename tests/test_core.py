"""判定コアのテスト。

Jev の API は呼ばない(ネットワークとAPIキーが要るため)。XMLの読み取り、
state の組み立て、質問の拡張、Noul の確信度の扱いを確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.judge import Answer, build_state, load_profile
from core.message import extract, find_by_id, latest, load_index, read_message_file
from core.questions import QUESTIONS, aspects, build_questions, by_key

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    return extract(read_message_file(FIXTURES / name))


# ---------------------------------------------------------------- XMLの読み取り


def test_気象警報注意報から地域と警報種別を取り出す():
    message = load("warning_tokyo.xml.gz")

    assert message.kind == "気象特別警報・警報・注意報"
    assert message.title == "東京都気象警報・注意報"
    assert message.publishing_office == "気象庁"
    assert message.info_type == "発表"
    assert message.report_datetime.startswith("2026-09-19T")
    assert "高波" in message.headline

    # 府県予報区(130000)が含まれる
    codes = {a.code for a in message.areas}
    assert "130000" in codes

    # 発表中の警報・注意報がまとまっている
    assert any("波浪警報" in w for w in message.warnings)


def test_府県気象情報から平文の本文を取り出す():
    message = load("info_chiba.xml.gz")

    assert message.kind == "府県気象情報"
    assert "台風第２５号" in message.title
    assert "台風第２５号" in message.body_text
    assert len(message.body_text) > 500
    # 平文の情報なので、警報の地域リストは空
    assert message.areas == []


def test_名前空間が違う要素もまとめて読める():
    # Control は jmaxml1/、Head は informationBasis1/、Body は body/meteorology1/
    message = load("warning_tokyo.xml.gz")
    assert message.kind  # Control
    assert message.title  # Head
    assert message.areas  # Body


def test_市町村等は発表中のものがある地域だけ残す():
    message = load("warning_tokyo.xml.gz")
    # 62 の市区町村すべてではなく、絞り込まれている
    city_areas = [a for a in message.areas if "市町村等" in a.level and "まとめた" not in a.level]
    assert all(a.kinds for a in city_areas)
    assert len(message.areas) < 62


# ---------------------------------------------------------------- state


def test_stateに電文とプロファイルの両方が入る():
    message = load("info_chiba.xml.gz")
    profile = load_profile(ROOT / "profile.yaml")

    state = build_state(message, profile)

    assert set(state) == {"message", "person"}
    assert state["message"]["kind"] == "府県気象情報"
    assert state["message"]["publishing_office"] == "銚子地方気象台"
    assert state["message"]["report_datetime"]
    assert "台風第２５号" in state["message"]["body_text"]
    assert state["person"]["pref"] == "東京都"
    assert state["person"]["profile"]


def test_警報の電文では対象地域がstateに入る():
    message = load("warning_tokyo.xml.gz")
    state = build_state(message, load_profile(ROOT / "profile.yaml"))

    assert "areas" in state["message"]
    assert any("130000" in a for a in state["message"]["areas"])
    assert state["message"]["warnings"]


def test_本文が長すぎる場合は切り詰める():
    from core.message import MAX_BODY_CHARS, Message

    message = Message(kind="test", body_text="あ" * (MAX_BODY_CHARS + 500))
    assert len(message.to_state()["body_text"]) == MAX_BODY_CHARS


# ---------------------------------------------------------------- 質問の定義


def test_質問は50問あり重複がない():
    assert len(QUESTIONS) == 50
    assert len({q.key for q in QUESTIONS}) == 50


def test_質問数を切り替えられる():
    from core.questions import QUESTION_SET_SIZES, select_questions

    assert QUESTION_SET_SIZES == (10, 30, 50)
    for size in QUESTION_SET_SIZES:
        assert len(select_questions(size)) == size
        assert len(build_questions(size)) == size

    # 先頭10問は基本の10問のまま(並び順がそのまま出題順になる)
    assert [q.key for q in select_questions(10)][:3] == ["message_kind", "imminent", "severity"]
    # 30問セットは10問セットをそのまま含む
    assert [q.key for q in select_questions(10)] == [q.key for q in select_questions(30)][:10]


def test_質問数の指定は範囲に収まる():
    from core.questions import select_questions

    assert len(select_questions(0)) == 1  # 最低1問
    assert len(select_questions(999)) == 50  # 定義数が上限
    assert len(select_questions(None)) == 50


def test_Scoreの目盛りの最大値を取り出せる():
    from core.questions import scale_max

    # criteria が5段階なら 0〜4 なので最大値は 4
    assert scale_max(by_key("severity").question) == 4
    assert scale_max(by_key("impact").question) == 4
    # Score 以外は目盛りを持たない
    assert scale_max(by_key("relevant").question) is None
    assert scale_max(by_key("action").question) is None


def test_質問はリストに足すだけで増える():
    from typesafe_sdk import Noul

    from core.questions import Q

    extra = Q(key="extra_q", label="追加の質問", aspect="move", question=Noul(instructions="テスト"))
    extended = QUESTIONS + [extra]

    questions = {q.key: q.question for q in extended}
    assert len(questions) == 51
    assert "extra_q" in questions


def test_各質問に観点のタグがついている():
    assert all(q.aspect for q in QUESTIONS)
    assert by_key("action").aspect == "action"
    # 移動・備え・住まい・周囲・仕事の観点が揃っている
    for aspect in ("message", "move", "prepare", "home", "family", "work"):
        assert aspect in aspects()


def test_3つの型をすべて使っている():
    kinds = {type(q.question).__name__ for q in QUESTIONS}
    assert kinds == {"Choice", "Score", "Noul"}


# ---------------------------------------------------------------- Noul の確信度


def test_Noulの確信度は0_5からの距離を使う():
    # 0.9 は「真」と判断がついている -> 0.8
    answer = Answer("k", "l", "a", "noul", 0.9, {"真": 0.9, "偽": 0.1}, None)
    assert answer.confidence is None
    assert answer.certainty == pytest.approx(0.8)

    # 0.1 は「偽」と判断がついている -> 同じく 0.8
    answer = Answer("k", "l", "a", "noul", 0.1, {"真": 0.1, "偽": 0.9}, None)
    assert answer.certainty == pytest.approx(0.8)

    # 0.5 はどちらとも言えない -> 0.0
    answer = Answer("k", "l", "a", "noul", 0.5, {"真": 0.5, "偽": 0.5}, None)
    assert answer.certainty == pytest.approx(0.0)


def test_ChoiceとScoreはconfidenceをそのまま使う():
    answer = Answer("k", "l", "a", "choice", "通常どおり", {"通常どおり": 0.7}, 0.7)
    assert answer.certainty == pytest.approx(0.7)

    answer = Answer("k", "l", "a", "score", 2.5, {2: 0.5, 3: 0.5}, 0.42)
    assert answer.certainty == pytest.approx(0.42)


# ---------------------------------------------------------------- 索引


def test_索引から電文をIDで引ける(tmp_path: Path):
    index = tmp_path / "index.jsonl"
    index.write_text(
        '{"id": "https://example.com/data/AAA_VPWW53.xml", "title": "警報", '
        '"updated": "2026-09-19T01:00:00Z", "author": "気象庁", "feed": "extra", '
        '"url": "https://example.com/data/AAA_VPWW53.xml", '
        '"path": "data/raw/2026-09-19/AAA_VPWW53.xml.gz", "bytes": 1}\n'
        '{"id": "https://example.com/data/BBB_VPFJ50.xml", "title": "情報", '
        '"updated": "2026-09-19T02:00:00Z", "author": "気象庁", "feed": "extra", '
        '"url": "https://example.com/data/BBB_VPFJ50.xml", '
        '"path": "data/raw/2026-09-19/BBB_VPFJ50.xml.gz", "bytes": 1}\n',
        encoding="utf-8",
    )
    records = load_index(index)
    assert len(records) == 2

    # URL全体でも、ファイル名だけでも引ける
    assert find_by_id(records, "https://example.com/data/AAA_VPWW53.xml").title == "警報"
    assert find_by_id(records, "AAA_VPWW53.xml").title == "警報"
    assert find_by_id(records, "AAA_VPWW53").title == "警報"
    assert find_by_id(records, "存在しない") is None

    # 最新は updated が新しいほう
    assert latest(records).title == "情報"
