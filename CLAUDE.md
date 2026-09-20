# 東京の防災電文、Jevで判定してみた

リポジトリ名は `jev-stormboard`。画面に出すアプリ名は「東京の防災電文、Jevで判定してみた」。

## 目的と現在の段階
気象庁の防災情報XML(防災電文)をリアルタイムに取り込み、TypeSafe の Jev で
判定させる Streamlit デモアプリ。**実際の防災判断には使わないデモ**である。

- 第一段階(いま): 電文を保存し続けるロガー。`python -m logger`
  - 保存対象は、随時・定時フィードとも**全エントリ**(地震火山とその他は取得しない)。
    種類で絞る場合は `logger/config.py` の `REGULAR_TITLE_INCLUDES` / `TITLE_EXCLUDES`。
- 次の段階: Streamlit アプリ本体と Jev の呼び出し、リプレイ機能

## 要件リスト
**`requirements.md` が要件の起点。** 作業の前に読み、要件の追加や実装完了があれば都度更新する。
(Python の依存関係ファイル `requirements.txt` とは別物なので混同しない)

## ディレクトリ構成
| パス | 役割 |
| --- | --- |
| `requirements.md` | 要件リスト(開発の起点) |
| `logger/` | ロガー本体。`python -m logger` で起動 |
| `core/` | 判定コア。`python -m core.judge --latest` |
| `profile.yaml` | 判定対象の人物プロファイル(1件) |
| `app/` | 画面。`streamlit run app/streamlit_app.py` |
| `data/judged/` | 判定結果 judgements.jsonl。コミットしない |
| `data/raw/` | 保存した電文 `YYYY-MM-DD/<name>.xml.gz`。コミットしない |
| `data/replay/` | リプレイ用の加工済みデータ。コミットしない |
| `data/index.jsonl` | 索引(1電文1行)。コミットしない |
| `logs/` | ローテーションするログ。コミットしない |
| `tests/` | テストと小さな fixture |

## 守るルール
- `data/raw/` はロガーだけが書き込む。**後から加工も削除もしない**。
  加工したデータは `data/replay/` に置く。画面は `data/raw` と `index.jsonl` を
  読み取るだけで、書き込むのは `data/judged/` のみ。
- `data/` 配下を大量に読み込んだり検索したりしない(数千ファイルになる)。
  中身の確認には `data/index.jsonl` と `python -m logger.stats` を使う。
- **稼働中のロガーを勝手に止めたり再起動したりしない。** 必要な場合は確認する。
- 秘密情報はコミットしない。API キーは `.claude/settings.local.json` の env から読む。
- 気象庁のサーバーに負荷をかけない。ポーリング間隔を短くしない、並列度を上げない。
- 実際の防災判断に使うものではなくデモである。画面と README にもその旨を明記する。

## よく使うコマンド
依存は `.venv` に入れてある。先に `source .venv/bin/activate` すること。

```bash
caffeinate -i python -m logger        # ロガー起動(スリープ防止)
streamlit run app/streamlit_app.py    # 画面(ロガーとは別プロセス)
python -m logger.stats            # 保存状況の確認
python -m pytest                  # テスト
du -sh data/                      # 容量確認
```

## 気づくための仕組み
- 随時フィードの新着が 30分以上途切れると、ログに WARNING が出る。
- `python -m logger.stats` の「最終の新着からの経過時間」で稼働状況を確認する。

## Git の運用
- ブランチは `main`。リモートは GitHub `yn01/jev-stormboard`(private)。
- コミットは意味のある単位で小さく。メッセージは日本語1行で、何をなぜ変えたかを書く。
- コミット前に、秘密情報と `data/` 配下がステージに含まれていないことを必ず確認する。
- force push はしない。履歴の書き換えが必要なら確認する。

## 言語
応答とコミットメッセージは日本語で書く。
