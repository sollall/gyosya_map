# data_extracts/

エージェントが原本（PDF・Excel・CSV）を読んで作った**正規化済み抽出データ**を置く。

```
data_extracts/<scheme_id>/<YYYY-MM-DD>/
  rows.jsonl      # 1行1業者
  manifest.json   # 出典・原本のsha256・抽出者・検品状態
```

形式と書き方は [`doc/extract-format.md`](../doc/extract-format.md)。取り込みは:

```bash
uv run gyosya-map import-extract --dir data_extracts/<scheme_id>/<YYYY-MM-DD>/
uv run gyosya-map import-extract --all     # 全件（再現性の確認）
```

原本のファイル自体はここに入れない（`data/` に置く。リポジトリには sha256 だけを残す）。
