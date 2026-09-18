"""電文の保存と索引(index.jsonl)の管理。

data/raw/ に書き込むのはこのモジュールだけ。書き込みは一時ファイル →
rename で行い、途中で落ちても壊れたファイルを残さない。
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .feed import Entry


class Store:
    def __init__(self, raw_dir: Path | None = None, index_path: Path | None = None) -> None:
        self.raw_dir = raw_dir or config.PATHS.raw
        self.index_path = index_path or config.PATHS.index
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = self._load_seen()

    # ------------------------------------------------------------ 重複排除

    def _load_seen(self) -> set[str]:
        """index.jsonl から保存済みの id を読み込む(再起動時の復元)。

        途中で落ちて壊れた行があっても、その行だけを飛ばす。
        """
        seen: set[str] = set()
        if not self.index_path.exists():
            return seen
        with self.index_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_id = record.get("id")
                if entry_id:
                    seen.add(entry_id)
        return seen

    def has(self, entry_id: str) -> bool:
        return entry_id in self._seen

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    # ------------------------------------------------------------ 保存

    def save(self, entry: Entry, body: bytes) -> dict | None:
        """電文本体を gzip で保存し、索引に 1 行追記する。

        すでに保存済みの id なら何もせず None を返す。
        """
        if self.has(entry.id):
            return None

        day_dir = self.raw_dir / entry.date_jst
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / (entry.filename + ".gz")

        self._write_atomic(path, gzip.compress(body, compresslevel=9))

        record = {
            "id": entry.id,
            "title": entry.title,
            "updated": entry.updated,
            "author": entry.author,
            "feed": entry.feed,
            "url": entry.url,
            "path": str(path.relative_to(config.PATHS.root)),
            "bytes": len(body),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if entry.summary:
            record["summary"] = entry.summary

        self._append_index(record)
        self._seen.add(entry.id)
        return record

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """同じディレクトリに一時ファイルを書いてから rename する。"""
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".gz")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _append_index(self, record: dict) -> None:
        """索引に 1 行追記して fsync する(途中で落ちても行が残るように)。"""
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
