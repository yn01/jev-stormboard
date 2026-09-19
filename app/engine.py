"""判定をバックグラウンドで進めるエンジン。

画面(Streamlit)は描画だけを行い、Jev の呼び出しはこのワーカースレッドが担う。
画面の更新が判定でブロックされないようにするため。

動作モードは2つ。

- ライブ  : `data/index.jsonl` を数秒ごとに読み、未判定の新しい電文を判定する。
            ロガーとは別プロセスなので、index.jsonl は**読み取りのみ**。
- リプレイ: 指定した日の電文を、発表時刻の順に再生速度に応じて判定していく。

判定済みの電文は JudgedStore がキャッシュしているので、同じ電文を二重に
判定しない(リプレイを繰り返しても API のコストが増えない)。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.judge import judge, load_api_key, load_profile
from core.message import IndexRecord, load_index
from core.questions import DEFAULT_QUESTION_COUNT

from .store import JudgedMessage, JudgedStore, profile_fingerprint

log = logging.getLogger("app.engine")

JST = timezone(timedelta(hours=9), "JST")

# 入力トークンの単価(USD / 100万トークン)。指標の帯のコスト表示に使う
INPUT_COST_PER_MTOK = 0.042

LIVE_POLL_SEC = 5.0  # ライブモードで index.jsonl を見に行く間隔
RATE_LIMIT_WAIT_SEC = 20.0  # レート制限に当たったときに空ける間隔
ERROR_WAIT_SEC = 5.0

# リプレイで、電文と電文の間に空ける待ち時間の上限(60倍速のとき)。
# これが無いと、夜間など電文が何十分も空く区間で再生が止まって見える。
# 速度を上げたときは上限も比例して縮める。そうしないと、間隔が広い区間で
# どの速度でも同じだけ待つことになり、速度を上げた意味がなくなる。
REPLAY_MAX_WAIT_SEC = 3.0
REPLAY_MAX_WAIT_BASE_SPEED = 60

# ライブモードの起動時に、さかのぼって判定する件数の上限。
# 索引には数千件あるので、起動と同時に全部投げないようにする。
LIVE_INITIAL_BACKLOG = 20


@dataclass
class Metrics:
    """指標の帯に出す数値。"""

    judged_count: int = 0  # この起動で判定した件数
    total_count: int = 0  # 保存済みを含む判定の総数
    last_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    question_count: int = DEFAULT_QUESTION_COUNT

    @property
    def average_latency_ms(self) -> float:
        if self.judged_count == 0:
            return 0.0
        return self.total_latency_ms / self.judged_count

    @property
    def estimated_cost_usd(self) -> float:
        return self.input_tokens / 1_000_000 * INPUT_COST_PER_MTOK


@dataclass
class Status:
    """画面に出す、エンジンのいまの状態。"""

    mode: str = "live"
    running: bool = False
    message: str = "待機中"
    rate_limited_until: float = 0.0  # monotonic 時刻
    error: str = ""
    replay_day: str = ""
    replay_speed: int = 1
    replay_done: int = 0
    replay_total: int = 0
    # いま再生している電文の発表時刻(UTC表記)。画面はここまでの電文だけを見せる
    replay_position: str = ""
    replay_position_jst: str = ""
    queue_size: int = 0
    question_count: int = DEFAULT_QUESTION_COUNT
    profile_name: str = ""
    profile_source: str = ""  # profile.yaml か profile.local.yaml か
    profile_fingerprint: str = ""
    profile_reloaded_at: str = ""
    paused: bool = False
    last_judged_at: float = 0.0  # 最後に判定した時刻(monotonic)

    def seconds_since_last_judge(self) -> float | None:
        if self.last_judged_at == 0.0:
            return None
        return time.monotonic() - self.last_judged_at

    @property
    def rate_limited(self) -> bool:
        return self.rate_limited_until > time.monotonic()

    @property
    def rate_limit_wait_sec(self) -> float:
        return max(0.0, self.rate_limited_until - time.monotonic())


class Engine:
    """判定を進めるワーカー。Streamlit のセッションをまたいで1つだけ動かす。"""

    def __init__(self, store: JudgedStore | None = None) -> None:
        self.store = store or JudgedStore()
        self.metrics = Metrics(total_count=self.store.count())
        self.status = Status()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._client: Any = None
        self._profile: dict | None = None

        # モードの指示(画面から書き換えられる)
        self._mode = "live"
        self._replay_day = ""
        self._replay_speed = 1
        self._mode_generation = 0  # モードが変わったら増やし、古い再生を止める

        # ライブモードで「ここまでは見た」という位置(電文の updated)。
        # これより新しいものだけを判定するので、索引全体を際限なく遡らない。
        self._live_cursor: str | None = None

        # 画面から切り替える質問数。変えると未判定扱いになり、その数で判定し直す
        self._question_count = DEFAULT_QUESTION_COUNT

        # プロファイルの再読み込み用。ファイルの更新時刻が変わったら読み直す
        self._profile_mtime: float | None = None
        self._profile_path: Path | None = None

        # リプレイの一時停止。画面のボタンから切り替える
        self._paused = threading.Event()

    # ------------------------------------------------------------ 起動と停止

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="judge-engine", daemon=True)
            self._thread.start()
            self.status.running = True

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ------------------------------------------------------------ モードの切り替え

    def set_mode(
        self,
        mode: str,
        replay_day: str = "",
        replay_speed: int = 1,
        question_count: int | None = None,
    ) -> None:
        """画面から呼ぶ。指示が変わったら、進行中の再生を打ち切って切り替える。"""
        count = question_count or self._question_count
        with self._lock:
            changed = (
                mode != self._mode
                or replay_day != self._replay_day
                or replay_speed != self._replay_speed
                or count != self._question_count
            )
            if not changed:
                return
            count_changed = count != self._question_count
            self._mode = mode
            self._replay_day = replay_day
            self._replay_speed = replay_speed
            self._question_count = count
            self._mode_generation += 1
            self.status.mode = mode
            self.status.replay_day = replay_day
            self.status.replay_speed = replay_speed
            self.status.question_count = count
            self.status.replay_done = 0
            self.status.replay_total = 0
            self.status.replay_position = ""
            self.status.replay_position_jst = ""
            self.metrics.question_count = count
            if count_changed:
                # 質問数が変わると結果も変わるので、ライブの位置を戻して判定し直す
                self._live_cursor = None
        self._wake.set()

    # ------------------------------------------------------------ 画面からの操作

    def pause(self) -> None:
        """リプレイを一時停止する。デモ中に説明しながら止めるため。"""
        self._paused.set()
        self.status.paused = True
        self._wake.set()

    def resume(self) -> None:
        """一時停止を解除する。"""
        self._paused.clear()
        self.status.paused = False
        self._wake.set()

    def restart(self) -> None:
        """リプレイを最初から再生し直す。

        世代を上げて進行中の再生を打ち切るだけ。判定結果は消さないので、
        すでに判定した電文は API を呼ばずにそのまま流れる。
        """
        with self._lock:
            self._mode_generation += 1
            self.status.replay_done = 0
            self.status.replay_position = ""
            self.status.replay_position_jst = ""
        self._paused.clear()
        self.status.paused = False
        self._wake.set()

    def refresh_now(self) -> None:
        """ライブモードで、いますぐ索引を見に行く。"""
        self._live_cursor = None
        self._wake.set()

    def _wait_while_paused(self) -> None:
        while self._paused.is_set() and not self._stop.is_set():
            self.status.message = "リプレイ: 一時停止中"
            self._wake.wait(timeout=0.3)
            self._wake.clear()

    def _current_mode(self) -> tuple[str, str, int, int]:
        with self._lock:
            return self._mode, self._replay_day, self._replay_speed, self._mode_generation

    def _count(self) -> int:
        with self._lock:
            return self._question_count

    # ------------------------------------------------------------ 本体

    def _run(self) -> None:
        try:
            load_api_key()
            from typesafe_sdk import TypeSafeClient

            self._client = TypeSafeClient()
            self._reload_profile()
        except Exception as exc:  # 起動に失敗しても画面は動かす
            self.status.error = f"初期化に失敗しました: {exc}"
            self.status.running = False
            log.exception("エンジンの初期化に失敗")
            return

        while not self._stop.is_set():
            # 書き換えられていれば読み直す(画面の再読み込みで反映されるように)
            if self._reload_profile():
                self._live_cursor = None  # 以後は新しいプロファイルで判定する
            mode, day, speed, generation = self._current_mode()
            try:
                if mode == "replay":
                    self._run_replay(day, speed, generation)
                else:
                    self._run_live(generation)
            except Exception as exc:  # ワーカーを落とさない
                self.status.error = str(exc)
                log.exception("判定ループでエラー")
                self._sleep(ERROR_WAIT_SEC)

        self.status.running = False

    def _reload_profile(self) -> bool:
        """profile.yaml を読み直す。更新されていれば True。

        画面を再読み込みしたときに、書き換えたプロファイルが反映されるようにする。
        """
        from core.judge import profile_path

        # profile.local.yaml が増えたり消えたりしたら、読む先自体が変わる
        target = profile_path()
        try:
            mtime = target.stat().st_mtime
        except OSError:
            return False
        if (
            self._profile_mtime is not None
            and mtime == self._profile_mtime
            and target == self._profile_path
        ):
            return False
        try:
            profile = load_profile()
        except Exception as exc:
            self.status.error = f"profile.yaml を読めません: {exc}"
            return False

        self._profile = profile
        self._profile_mtime = mtime
        self._profile_path = target
        self.status.profile_name = str(profile.get("name", ""))
        self.status.profile_source = str(profile.get("_source", target.name))
        self.status.profile_fingerprint = profile_fingerprint(profile)
        self.status.profile_reloaded_at = datetime.now(JST).strftime("%H:%M:%S")
        log.info(
            "%s を読み込みました (%s)", self.status.profile_source, self.status.profile_fingerprint
        )
        return True

    def _sleep(self, seconds: float) -> None:
        """停止やモード変更で早く抜けられる待ち。"""
        self._wake.wait(timeout=seconds)
        self._wake.clear()

    # ------------------------------------------------------------ ライブ

    def _run_live(self, generation: int) -> None:
        self.status.message = "ライブ: 新しい電文を待っています"
        records = load_index()  # index.jsonl は読み取りのみ
        if not records:
            self._sleep(LIVE_POLL_SEC)
            return

        count = self._count()
        newest = max(r.updated for r in records)

        if self._live_cursor is None:
            # 初回だけ、直近のぶんをさかのぼって判定する(画面が空にならないように)。
            # 索引には数千件あるので、起動と同時に全件を投げない。
            pending = sorted(
                (r for r in records if not self.store.has(r.id, count)),
                key=lambda r: r.updated,
            )[-LIVE_INITIAL_BACKLOG:]
        else:
            # 2回目以降は、前回見た位置より新しいものだけ。
            # これがないと、未判定の古い電文を毎周期さかのぼって判定してしまう。
            cursor = self._live_cursor
            pending = sorted(
                (r for r in records if r.updated > cursor and not self.store.has(r.id, count)),
                key=lambda r: r.updated,
            )

        # 次の周期からは、いま見た中でいちばん新しい時刻より先だけを見る
        self._live_cursor = newest

        self.status.queue_size = len(pending)
        for record in pending:
            if self._stop.is_set() or self._changed(generation):
                return
            self.status.message = f"ライブ: 判定中 {record.title}"
            self._judge_one(record)
            self.status.queue_size = max(0, self.status.queue_size - 1)

        self.status.message = "ライブ: 新しい電文を待っています"
        self._sleep(LIVE_POLL_SEC)

    # ------------------------------------------------------------ リプレイ

    def _run_replay(self, day: str, speed: int, generation: int) -> None:
        records = [r for r in load_index() if self._day_of(r) == day]
        records.sort(key=lambda r: r.updated)

        self.status.replay_total = len(records)
        self.status.replay_done = 0
        self.status.replay_position = ""
        self.status.replay_position_jst = ""
        if not records:
            self.status.message = f"リプレイ: {day} の電文がありません"
            self._sleep(2.0)
            return

        self.status.message = f"リプレイ: {day} を {speed}倍速で再生中"
        previous: datetime | None = None

        for record in records:
            if self._stop.is_set() or self._changed(generation):
                return

            self._wait_while_paused()
            if self._stop.is_set() or self._changed(generation):
                return
            if not self.status.paused:
                self.status.message = f"リプレイ: {day} を {speed}倍速で再生中"

            current = self._updated_dt(record)
            if previous is not None and current is not None:
                gap = (current - previous).total_seconds() / max(1, speed)
                # 待ちが長くなりすぎないよう上限をかける(デモが止まって見えないように)。
                # 上限は速度に応じて縮める(60倍で3秒、100倍で1.8秒)
                if gap > 0:
                    limit = REPLAY_MAX_WAIT_SEC * min(
                        1.0, REPLAY_MAX_WAIT_BASE_SPEED / max(1, speed)
                    )
                    self._sleep(min(gap, limit))
                    if self._stop.is_set() or self._changed(generation):
                        return
            previous = current

            # 再生位置を進める。画面はこの時刻までの電文だけを表示する
            self.status.replay_position = record.updated
            if current is not None:
                self.status.replay_position_jst = current.astimezone(JST).strftime("%m/%d %H:%M")

            self._judge_one(record)
            self.status.replay_done += 1

        self.status.message = f"リプレイ: {day} の再生が終わりました({len(records)}件)"
        self.status.replay_position = ""  # 終わったら全件を見せる
        self.status.replay_position_jst = "再生終了"
        self._sleep(3.0)

    # ------------------------------------------------------------ 判定1件

    def _judge_one(self, record: IndexRecord) -> None:
        """1件を判定する。判定済みならキャッシュを使って API を呼ばない。

        同じ電文でも質問数が違えば結果が変わるので、質問数ごとに別扱いにする。
        """
        count = self._count()
        if self.store.has(record.id, count):
            return
        if not record.full_path.exists():
            return

        from typesafe_sdk import TypeSafeError, TypeSafeRateLimitError

        try:
            judgement = judge(
                record, self._profile or {}, client=self._client, question_count=count
            )
        except TypeSafeRateLimitError:
            # 処理は止めず、間隔を空けて次の周期で再試行する
            self.status.rate_limited_until = time.monotonic() + RATE_LIMIT_WAIT_SEC
            self.status.message = (
                f"APIのレート制限に当たりました。{RATE_LIMIT_WAIT_SEC:.0f}秒空けて再試行します"
            )
            log.warning("レート制限。%.0f秒待機", RATE_LIMIT_WAIT_SEC)
            self._sleep(RATE_LIMIT_WAIT_SEC)
            return
        except TypeSafeError as exc:
            self.status.error = f"判定に失敗: {exc}"
            log.warning("判定に失敗 %s: %s", record.id, exc)
            self._sleep(ERROR_WAIT_SEC)
            return
        except Exception as exc:  # XMLの読み取り失敗など
            log.warning("判定できませんでした %s: %s", record.id, exc)
            return

        judged = JudgedMessage.from_judgement(judgement, record, self._profile or {})
        self.store.add(judged)

        self.metrics.judged_count += 1
        self.metrics.total_count = self.store.count(count)
        self.metrics.last_latency_ms = judged.latency_ms
        self.metrics.total_latency_ms += judged.latency_ms
        self.metrics.input_tokens += judged.input_tokens or 0
        self.metrics.output_tokens += judged.output_tokens or 0
        self.metrics.question_count = judged.question_count
        self.status.last_judged_at = time.monotonic()
        self.status.error = ""

    # ------------------------------------------------------------ 補助

    def _changed(self, generation: int) -> bool:
        with self._lock:
            return generation != self._mode_generation

    @staticmethod
    def _updated_dt(record: IndexRecord) -> datetime | None:
        try:
            text = record.updated.replace("Z", "+00:00")
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @classmethod
    def _day_of(cls, record: IndexRecord) -> str:
        """電文の日付(JST)。保存先のディレクトリ名と揃える。"""
        parts = record.path.split("/")
        if len(parts) >= 3:
            return parts[-2]
        dt = cls._updated_dt(record)
        return dt.astimezone(JST).strftime("%Y-%m-%d") if dt else ""


def available_days() -> list[str]:
    """リプレイで選べる日付(新しい順)。索引から数えるので data/raw は歩かない。"""
    days: dict[str, int] = {}
    for record in load_index():
        day = Engine._day_of(record)
        if day:
            days[day] = days.get(day, 0) + 1
    return sorted(days, reverse=True)
