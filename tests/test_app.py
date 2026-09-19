"""画面まわりのテスト。

Jev の API も Streamlit も起動せず、保存・キャッシュ・並べ替え・しきい値の
扱いといった素の処理だけを確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engine import INPUT_COST_PER_MTOK, Engine, Metrics
from app.store import JudgedAnswer, JudgedMessage, JudgedStore
from core.message import IndexRecord


def make_judged(
    message_id: str,
    updated: str,
    relevant: float = 0.5,
    action: str = "通常どおり",
    latency_ms: float = 300.0,
    input_tokens: int = 4000,
) -> JudgedMessage:
    return JudgedMessage(
        id=message_id,
        title="テスト電文",
        kind="気象特別警報・警報・注意報",
        author="気象庁",
        updated=updated,
        report_datetime="2026-09-19T10:00:00+09:00",
        headline="テストの見出し",
        latency_ms=latency_ms,
        model="jev-1.13.0",
        input_tokens=input_tokens,
        output_tokens=280,
        question_count=10,
        judged_at="2026-09-19T01:00:00+00:00",
        answers=[
            JudgedAnswer("relevant", "この地域に関係する", "message", "noul", relevant, None,
                         abs(relevant - 0.5) * 2, {"真": relevant, "偽": 1 - relevant}),
            JudgedAnswer("action", "取るべき行動", "action", "choice", action, 0.6, 0.6,
                         {action: 0.6, "通常どおり": 0.4}),
        ],
    )


# ---------------------------------------------------------------- 保存と復元


def test_判定結果を保存して読み直せる(tmp_path: Path):
    path = tmp_path / "judgements.jsonl"
    store = JudgedStore(path)
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", relevant=0.9))

    assert store.count() == 1
    assert store.has("id-1")

    # 再起動に相当: 同じファイルから読み直す
    revived = JudgedStore(path)
    assert revived.count() == 1
    assert revived.has("id-1")

    judged = revived.get("id-1")
    assert judged is not None
    assert judged.relevance == pytest.approx(0.9)
    assert judged.answers[0].key == "relevant"


def test_同じ電文を二重に保存しない(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z"))

    # has が真なら、呼び出し側は判定をしない
    assert store.has("id-1")
    assert store.count() == 1

    # 同じ id を再度入れても件数は増えない(後の内容で上書きされる)
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", relevant=0.1))
    assert store.count() == 1
    assert store.get("id-1").relevance == pytest.approx(0.1)


def test_壊れた行があっても読み込める(tmp_path: Path):
    path = tmp_path / "judgements.jsonl"
    store = JudgedStore(path)
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "broken", "ans\n')

    revived = JudgedStore(path)
    assert revived.count() == 1
    assert revived.has("id-1")


def test_新しい順に並ぶ(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("old", "2026-09-18T01:00:00Z"))
    store.add(make_judged("new", "2026-09-19T05:00:00Z"))
    store.add(make_judged("mid", "2026-09-19T01:00:00Z"))

    assert [j.id for j in store.all()] == ["new", "mid", "old"]


# ---------------------------------------------------------------- しきい値


def test_関連度でしきい値を切れる(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("high", "2026-09-19T03:00:00Z", relevant=0.95))
    store.add(make_judged("mid", "2026-09-19T02:00:00Z", relevant=0.60))
    store.add(make_judged("low", "2026-09-19T01:00:00Z", relevant=0.05))

    everything = store.all()
    assert len([j for j in everything if j.relevance >= 0.5]) == 2
    assert len([j for j in everything if j.relevance >= 0.9]) == 1
    # しきい値を上げても、一覧そのものからは消えない(薄く表示するため)
    assert len(everything) == 3


def test_Noulの確信度は0_5からの距離になる(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", relevant=0.9))
    answer = store.get("id-1").answer("relevant")

    assert answer.confidence is None  # Noul に confidence は無い
    assert answer.certainty == pytest.approx(0.8)


def test_choiceは上位の確率を取り出せる(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", action="外出を控える"))
    answer = store.get("id-1").answer("action")

    tops = answer.top_probabilities(2)
    assert tops[0] == ("外出を控える", pytest.approx(0.6))
    assert len(tops) == 2


# ---------------------------------------------------------------- 指標


def test_平均レイテンシと推定コスト():
    m = Metrics()
    assert m.average_latency_ms == 0.0  # 0件でも落ちない

    m.judged_count = 2
    m.total_latency_ms = 700.0
    m.input_tokens = 1_000_000
    assert m.average_latency_ms == pytest.approx(350.0)
    assert m.estimated_cost_usd == pytest.approx(INPUT_COST_PER_MTOK)


# ---------------------------------------------------------------- リプレイの日付


def test_保存先のパスから日付を取り出す():
    record = IndexRecord(
        id="x", title="t", updated="2026-09-18T20:00:00Z", author="a", feed="extra",
        url="u", path="data/raw/2026-09-19/foo.xml.gz",
    )
    # updated は UTC で 9/18 だが、保存先(JST)は 9/19
    assert Engine._day_of(record) == "2026-09-19"


def test_モードを変えると世代が上がって古い再生が止まる(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    _, _, _, first = engine._current_mode()

    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    mode, day, speed, second = engine._current_mode()

    assert mode == "replay"
    assert day == "2026-09-18"
    assert speed == 60
    assert second != first
    assert engine._changed(first)  # 古い世代は「変わった」と判定される
    assert not engine._changed(second)


def test_同じモードを指定しても世代は上がらない(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    _, _, _, before = engine._current_mode()

    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    _, _, _, after = engine._current_mode()
    assert before == after
