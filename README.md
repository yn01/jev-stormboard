# jev-stormboard

気象庁の防災情報XML(防災電文)をリアルタイムに取り込み、TypeSafe の
[Jev](https://typesafe.jp/) で「いま何が起きているか」を判定させる Streamlit デモです。
防災電文は種類が多く構造も複雑なため、型で定義した判定ロジックを安全に書ける Jev が
向いていると考え、その題材として作っています。

> ## ⚠️ これはデモです
> **実際の防災判断には使わないでください。**
> 避難や行動の判断は、必ず[気象庁](https://www.jma.go.jp/)および
> お住まいの自治体の発表を確認してください。
> 本リポジトリは個人による技術デモであり、情報の正確性・即時性を保証しません。

## 現在の段階

- **第一段階(いま): ロガー** — 防災電文を取得して保存し続ける
- 次の段階: Streamlit アプリ本体と Jev による判定、保存データを使ったリプレイ

## セットアップ

Python 3.11 以上が必要です。

```bash
git clone https://github.com/yn01/jev-stormboard.git
cd jev-stormboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## ロガーの起動

```bash
source .venv/bin/activate
caffeinate -i python -m logger
```

`caffeinate -i` は MacBook のスリープを防ぐためのものです。運用時は
**電源に接続し、蓋は開けたまま**にしてください(蓋を閉じるとスリープします)。

ログは画面と `logs/logger.log` の両方に出ます(5MB ごとにローテーション、5世代)。

## 保存するもの

| フィード | 保存対象 |
| --- | --- |
| 随時 (`extra.xml` / `extra_l.xml`) | **全エントリ** |
| 定時 (`regular.xml` / `regular_l.xml`) | **全エントリ** |

地震火山フィードとその他フィードは取得しません。

定時フィードを種類で絞りたい場合は、`logger/config.py` の `REGULAR_TITLE_INCLUDES`
に文字列を並べてください(部分一致、いずれかに当たれば保存)。空のときはフィルタなしです。

```python
REGULAR_TITLE_INCLUDES: tuple[str, ...] = ("警報級の可能性",)  # 初期値は () = 全件
```

動作は次のとおりです。

- 高頻度フィードを **60秒間隔**でポーリング
- **起動時と1時間ごと**に長期フィードで穴埋め(停止していた間の分を回収)
- 重複は entry の id で排除。起動時に `data/index.jsonl` から保存済み ID を読み込むため、
  **再起動しても続きから保存**されます
- フィード取得は `If-Modified-Since` / `ETag` による条件付き GET(更新が無ければ 304)
- 電文本体のダウンロードは並列度 2、1件ごとに 0.2 秒待ち。気象庁のサーバーに負荷をかけません

随時フィードの新着が **30分以上途切れる**と、ログに WARNING を出します
(`NO_NEW_ENTRY_WARN_SEC`)。気象庁側が静かなだけのこともありますが、取得が
止まっていることに気づくための目安です。

長期フィードは7日分(約 7,500 件)を含むため、穴埋めで遡る上限を
`BACKFILL_MAX_AGE_HOURS`(初期値 24時間)で制限しています。これより長く停止していた分を
回収したい場合は、この値を一時的に大きくしてください。

## 保存形式

電文本体は gzip で保存します。日付は entry の `updated` を JST に直したものです。

```
data/raw/2026-09-19/20260918175015_0_VPWW53_030000.xml.gz
```

索引 `data/index.jsonl` には、1電文につき1行を追記します。

```json
{"id":"https://www.data.jma.go.jp/developer/xml/data/20260918175015_0_VPWW53_030000.xml",
 "title":"気象特別警報・警報・注意報","updated":"2026-09-18T17:50:14Z","author":"盛岡地方気象台",
 "feed":"extra","url":"https://...","path":"data/raw/2026-09-19/2026...xml.gz",
 "bytes":20079,"fetched_at":"2026-09-19T03:20:55+00:00","summary":"【岩手県気象警報・注意報】..."}
```

ファイルは一時ファイルに書いてから rename するため、途中で落ちても壊れたファイルは残りません。

## ディレクトリ構成

```
jev-stormboard/
├── CLAUDE.md
├── README.md
├── logger/          # ロガー本体 (python -m logger)
│   ├── config.py    # 設定はすべてここ
│   ├── feed.py      # フィードのパースと絞り込み
│   ├── http.py      # 条件付きGETとリトライ
│   ├── store.py     # 保存と索引
│   └── stats.py     # 保存状況の表示
├── app/             # Streamlit アプリ (次の段階)
├── data/            # コミットしない
│   ├── raw/         # 保存した電文
│   ├── replay/      # リプレイ用の加工済みデータ
│   └── index.jsonl  # 索引
├── logs/            # コミットしない
└── tests/
```

`data/raw/` はロガーだけが書き込みます。後から加工も削除もしません。
加工したデータは `data/replay/` に置いてください。

## 保存状況と容量の確認

```bash
python -m logger.stats   # 件数、種類別の件数、日別の容量、最終の新着からの経過時間
du -sh data/             # 実際のディスク使用量
```

`stats` の「最終の新着からの経過時間」で、取得が続いているかを確認できます。
随時(extra)が 30分以上途切れていると、その旨が表示されます。

`data/` は数千ファイルになるため、`ls` や grep で中を探さず、
`index.jsonl` と `stats` で確認してください。

件数が極端に多い種類を `stats` で見つけたら、`logger/config.py` の
`TITLE_EXCLUDES` に文字列を足すと、その種類を保存しなくなります(部分一致、初期値は空)。

```python
TITLE_EXCLUDES: tuple[str, ...] = ("大雨危険度通知",)
```

定時フィードを種類で絞る `REGULAR_TITLE_INCLUDES` も同じファイルにあります(初期値は空 = 全件)。

## 停止と再起動

`Ctrl-C`(SIGINT)または `kill`(SIGTERM)で安全に停止します。取得中の電文を
中断し、書きかけのファイルを残さずに終了します。

再起動すると `data/index.jsonl` から保存済み ID を読み込むため、**重複せずに
続きから保存**されます。停止していた間の電文は、起動時の穴埋めで回収されます。

## テスト

```bash
python -m pytest
```

フィードのサンプルを fixture にして、パース・フィルタ・重複排除・再起動時の復元を確認します。

## データの出典

[気象庁防災情報XMLフォーマット 情報提供ページ](https://xml.kishou.go.jp/)

利用にあたっては、気象庁の[利用規約](https://www.jma.go.jp/jma/kishou/info/coment.html)を
必ず確認してください。取得の間隔や並列度を上げると気象庁のサーバーに負荷をかけるため、
設定値は変更しないでください。

## 今後の予定

- Streamlit アプリ本体と、Jev による電文の判定
- 保存データを使ったリプレイ機能(台風接近時の推移を再生する)
- 交通量データの重ね合わせ
