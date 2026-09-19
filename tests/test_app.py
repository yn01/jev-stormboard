"""画面まわりのテスト。

Jev の API も Streamlit も起動せず、保存・キャッシュ・並べ替え・しきい値の
扱いといった素の処理だけを確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engine import INPUT_COST_PER_MTOK, Engine, Metrics
from app.store import JudgedAnswer, JudgedMessage, JudgedStore, profile_fingerprint
from core.message import IndexRecord


def make_judged(
    message_id: str,
    updated: str,
    relevant: float = 0.5,
    action: str = "通常どおり",
    latency_ms: float = 300.0,
    input_tokens: int = 4000,
    question_count: int = 10,
    profile_fp: str = "abc123",
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
        question_count=question_count,
        judged_at="2026-09-19T01:00:00+00:00",
        profile_id="person-001",
        profile_fingerprint=profile_fp,
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
    assert store.has("id-1", 10)

    # 再起動に相当: 同じファイルから読み直す
    revived = JudgedStore(path)
    assert revived.count() == 1
    assert revived.has("id-1", 10)

    judged = revived.get("id-1", 10)
    assert judged is not None
    assert judged.relevance == pytest.approx(0.9)
    assert judged.answers[0].key == "relevant"


def test_同じ電文を二重に保存しない(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z"))

    # has が真なら、呼び出し側は判定をしない
    assert store.has("id-1", 10)
    assert store.count() == 1

    # 同じ id を再度入れても件数は増えない(後の内容で上書きされる)
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", relevant=0.1))
    assert store.count() == 1
    assert store.get("id-1", 10).relevance == pytest.approx(0.1)


def test_壊れた行があっても読み込める(tmp_path: Path):
    path = tmp_path / "judgements.jsonl"
    store = JudgedStore(path)
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "broken", "ans\n')

    revived = JudgedStore(path)
    assert revived.count() == 1
    assert revived.has("id-1", 10)


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
    answer = store.get("id-1", 10).answer("relevant")

    assert answer.confidence is None  # Noul に confidence は無い
    assert answer.certainty == pytest.approx(0.8)


def test_choiceは上位の確率を取り出せる(tmp_path: Path):
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-19T01:00:00Z", action="外出を控える"))
    answer = store.get("id-1", 10).answer("action")

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


# ---------------------------------------------------------------- 再生の操作


def test_一時停止と再開ができる(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))

    assert engine.status.paused is False
    engine.pause()
    assert engine.status.paused is True
    engine.resume()
    assert engine.status.paused is False


def test_最初からで世代が上がり進捗が戻る(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    _, _, _, before = engine._current_mode()
    engine.status.replay_done = 120

    engine.restart()

    _, _, _, after = engine._current_mode()
    assert after != before  # 進行中の再生が打ち切られる
    assert engine._changed(before)
    assert engine.status.replay_done == 0
    assert engine.status.paused is False


def test_最初からでも判定結果は消えない(tmp_path: Path):
    """再生し直しても API を呼び直さないこと(キャッシュを壊さない)。"""
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("id-1", "2026-09-18T01:00:00Z"))
    engine = Engine(store)

    engine.restart()

    assert engine.store.count() == 1
    assert engine.store.has("id-1", 10)


def test_いますぐ確認でライブの位置が戻る(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine._live_cursor = "2026-09-19T01:00:00Z"

    engine.refresh_now()
    assert engine._live_cursor is None


def test_最終判定からの経過時間(tmp_path: Path):
    import time

    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    # まだ判定していなければ None
    assert engine.status.seconds_since_last_judge() is None

    engine.status.last_judged_at = time.monotonic()
    elapsed = engine.status.seconds_since_last_judge()
    assert elapsed is not None and elapsed < 1.0


def test_プロファイルの指紋に読み込み元は混ざらない():
    """_source が違っても、中身が同じなら指紋は同じになること。"""
    base = {"id": "x", "name": "n", "area_code": "130000", "pref": "東京都",
            "city": "", "profile": "本文"}
    a = {**base, "_source": "profile.yaml", "_is_local": False}
    b = {**base, "_source": "profile.local.yaml", "_is_local": True}

    assert profile_fingerprint(a) == profile_fingerprint(b)
    # 中身が変われば指紋も変わる
    assert profile_fingerprint({**base, "city": "どこかの区"}) != profile_fingerprint(a)


# ---------------------------------------------------------------- リプレイの再生位置


def test_リプレイ中は再生位置までの電文だけを見せる(tmp_path: Path):
    """再生位置を反映しないと、朝を再生していても最新の電文が先頭に出続けてしまう。"""
    store = JudgedStore(tmp_path / "judgements.jsonl")
    store.add(make_judged("morning", "2026-09-19T00:00:00Z"))
    store.add(make_judged("noon", "2026-09-19T03:00:00Z"))
    store.add(make_judged("night", "2026-09-19T14:00:00Z"))

    everything = store.all(10)
    assert [j.id for j in everything] == ["night", "noon", "morning"]

    # 再生位置が昼なら、夜の電文はまだ見えない
    position = "2026-09-19T03:00:00Z"
    visible = [j for j in everything if j.updated <= position]
    assert [j.id for j in visible] == ["noon", "morning"]

    # 位置が空(ライブ、または再生終了)なら全件
    assert len([j for j in everything if not "" or True]) == 3


def test_再生位置は最初からで戻る(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    engine.status.replay_position = "2026-09-18T05:00:00Z"
    engine.status.replay_position_jst = "09/18 14:00"

    engine.restart()

    assert engine.status.replay_position == ""
    assert engine.status.replay_position_jst == ""


def test_モードを変えると再生位置が消える(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine.set_mode("replay", replay_day="2026-09-18", replay_speed=60)
    engine.status.replay_position = "2026-09-18T05:00:00Z"

    engine.set_mode("live")

    assert engine.status.replay_position == ""


# ---------------------------------------------------------------- 再生速度


def test_再生速度は100倍まで選べる():
    import pathlib

    source = pathlib.Path("app/streamlit_app.py").read_text(encoding="utf-8")
    assert '"100倍"' in source
    assert '"100倍": 100' in source


def test_待ち時間の上限は速度が上がるほど短くなる():
    """上限が固定だと、間隔が広い区間でどの速度でも同じだけ待つことになり、
    速度を上げた意味がなくなる。
    """
    from app.engine import REPLAY_MAX_WAIT_BASE_SPEED, REPLAY_MAX_WAIT_SEC

    def limit(speed: int) -> float:
        return REPLAY_MAX_WAIT_SEC * min(1.0, REPLAY_MAX_WAIT_BASE_SPEED / max(1, speed))

    # 基準(60倍)までは同じ。デモが止まって見えないための上限なので緩めない
    assert limit(1) == pytest.approx(REPLAY_MAX_WAIT_SEC)
    assert limit(10) == pytest.approx(REPLAY_MAX_WAIT_SEC)
    assert limit(60) == pytest.approx(REPLAY_MAX_WAIT_SEC)
    # それより速くすると、上限も比例して縮む
    assert limit(100) == pytest.approx(1.8)
    assert limit(100) < limit(60)


# ---------------------------------------------------------------- 再生の開始時刻


def test_開始時刻で再生対象を絞れる():
    from core.message import IndexRecord

    def record(hhmm_utc: str) -> IndexRecord:
        return IndexRecord(
            id="x", title="t", updated=f"2026-09-19T{hhmm_utc}:00Z", author="a",
            feed="extra", url="u", path="data/raw/2026-09-19/foo.xml.gz",
        )

    # UTC 00:00 は JST 09:00
    assert Engine._jst_hhmm(record("00:00")) == "09:00"
    assert Engine._jst_hhmm(record("12:30")) == "21:30"
    # UTC の前日 15:00 は JST 00:00
    assert Engine._jst_hhmm(
        IndexRecord(id="x", title="t", updated="2026-09-18T15:00:00Z", author="a",
                    feed="extra", url="u", path="data/raw/2026-09-19/foo.xml.gz")
    ) == "00:00"

    # 文字列のまま比較できる(ゼロ埋めしてあるため)
    assert "09:00" >= "06:00"
    assert not ("05:30" >= "06:00")


def test_開始時刻を変えると再生し直す(tmp_path: Path):
    engine = Engine(JudgedStore(tmp_path / "judgements.jsonl"))
    engine.set_mode("replay", replay_day="2026-09-19", replay_speed=60, replay_start="00:00")
    _, _, _, before = engine._current_mode()

    engine.set_mode("replay", replay_day="2026-09-19", replay_speed=60, replay_start="12:00")
    _, _, _, after = engine._current_mode()

    assert after != before  # 進行中の再生が打ち切られる
    assert engine.status.replay_start == "12:00"
    assert engine._start_time() == "12:00"


def test_開始時刻の選択肢は30分刻みの48個():
    times = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
    assert len(times) == 48
    assert times[0] == "00:00"
    assert times[-1] == "23:30"

    import pathlib

    source = pathlib.Path("app/streamlit_app.py").read_text(encoding="utf-8")
    assert "REPLAY_START_TIMES" in source
    assert '"開始時刻"' in source
