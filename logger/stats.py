"""保存状況の表示。python -m logger.stats で起動する。

data/raw/ を歩き回らずに済むよう、件数と容量は index.jsonl から集計する。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import config
from .feed import JST, parse_updated


def load_records(index_path: Path) -> list[dict]:
    records: list[dict] = []
    if not index_path.exists():
        return records
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"


def summarize(records: list[dict]) -> dict:
    by_title: Counter[str] = Counter()
    by_feed: Counter[str] = Counter()
    by_day_count: Counter[str] = Counter()
    by_day_bytes: defaultdict[str, int] = defaultdict(int)
    total_bytes = 0
    latest: str | None = None

    for record in records:
        by_title[record.get("title", "(不明)")] += 1
        by_feed[record.get("feed", "(不明)")] += 1
        size = int(record.get("bytes", 0))
        total_bytes += size

        path = record.get("path", "")
        day = Path(path).parent.name if path else ""
        if not day:
            try:
                day = parse_updated(record["updated"]).astimezone(JST).strftime("%Y-%m-%d")
            except (KeyError, ValueError):
                day = "(不明)"
        by_day_count[day] += 1
        by_day_bytes[day] += size

        fetched = record.get("fetched_at")
        if fetched and (latest is None or fetched > latest):
            latest = fetched

    return {
        "total": len(records),
        "total_bytes": total_bytes,
        "by_title": by_title,
        "by_feed": by_feed,
        "by_day_count": by_day_count,
        "by_day_bytes": by_day_bytes,
        "latest": latest,
    }


def render(summary: dict) -> str:
    lines: list[str] = []
    lines.append(f"保存件数: {summary['total']}件")
    lines.append(f"合計容量: {human(summary['total_bytes'])} (gzip 前の元サイズ)")

    latest = summary["latest"]
    if latest:
        local = datetime.fromisoformat(latest).astimezone(JST)
        lines.append(f"最終取得: {local.strftime('%Y-%m-%d %H:%M:%S')} JST")
    else:
        lines.append("最終取得: なし")

    feeds = ", ".join(f"{k}={v}" for k, v in sorted(summary["by_feed"].items()))
    lines.append(f"フィード別: {feeds or 'なし'}")

    lines.append("")
    lines.append("種類(title)別の件数:")
    if not summary["by_title"]:
        lines.append("  (なし)")
    for title, count in summary["by_title"].most_common():
        lines.append(f"  {count:6d}  {title}")

    lines.append("")
    lines.append("日別の件数と容量:")
    if not summary["by_day_count"]:
        lines.append("  (なし)")
    for day in sorted(summary["by_day_count"]):
        count = summary["by_day_count"][day]
        size = summary["by_day_bytes"][day]
        lines.append(f"  {day}  {count:6d}件  {human(size):>9s}")

    if config.TITLE_EXCLUDES:
        lines.append("")
        lines.append(f"除外中の種類: {', '.join(config.TITLE_EXCLUDES)}")

    return "\n".join(lines)


def main() -> int:
    records = load_records(config.PATHS.index)
    print(render(summarize(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
