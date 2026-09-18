# jev-stormboard

## 目的と現在の段階
気象庁の防災情報XML(防災電文)をリアルタイムに取り込み、TypeSafe の Jev で
判定させる Streamlit デモアプリ。**実際の防災判断には使わないデモ**である。

- 第一段階(いま): 電文を保存し続けるロガー。`python -m logger`
- 次の段階: Streamlit アプリ本体と Jev の呼び出し、リプレイ機能

## ディレクトリ構成
| パス | 役割 |
| --- | --- |
| `logger/` | ロガー本体。`python -m logger` で起動 |
| `app/` | Streamlit アプリ(次段階。いまは空) |
| `data/raw/` | 保存した電文 `YYYY-MM-DD/<name>.xml.gz`。コミットしない |
| `data/replay/` | リプレイ用の加工済みデータ。コミットしない |
| `data/index.jsonl` | 索引(1電文1行)。コミットしない |
| `logs/` | ローテーションするログ。コミットしない |
| `tests/` | テストと小さな fixture |

## 守るルール
- `data/raw/` はロガーだけが書き込む。**後から加工も削除もしない**。
  加工したデータは `data/replay/` に置く。
- `data/` 配下を大量に読み込んだり検索したりしない(数千ファイルになる)。
  中身の確認には `data/index.jsonl` と `python -m logger.stats` を使う。
- **稼働中のロガーを勝手に止めたり再起動したりしない。** 必要な場合は確認する。
- 秘密情報はコミットしない。API キーは `.claude/settings.local.json` の env から読む。
- 気象庁のサーバーに負荷をかけない。ポーリング間隔を短くしない、並列度を上げない。
- 実際の防災判断に使うものではなくデモである。画面と README にもその旨を明記する。

## よく使うコマンド
依存は `.venv` に入れてある。先に `source .venv/bin/activate` すること。

```bash
caffeinate -i python -m logger    # 起動(スリープ防止)
python -m logger.stats            # 保存状況の確認
python -m pytest                  # テスト
du -sh data/                      # 容量確認
```

## Git の運用
- ブランチは `main`。リモートは GitHub `yn01/jev-stormboard`(private)。
- コミットは意味のある単位で小さく。メッセージは日本語1行で、何をなぜ変えたかを書く。
- コミット前に、秘密情報と `data/` 配下がステージに含まれていないことを必ず確認する。
- force push はしない。履歴の書き換えが必要なら確認する。

## 言語
応答とコミットメッセージは日本語で書く。
