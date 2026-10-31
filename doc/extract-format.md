# 抽出データ形式（data_extracts/）

原本（PDF・Excel・CSV・Webページ）の読み取りは**収集担当のエージェント**が行い、
**正規化済みの抽出データ**をリポジトリにコミットする。インポータ（`gyosya-map import-extract`）は、
その抽出データを検証してDBに入れるだけの決定的な処理で、**自治体ごとの書式解釈をいっさい含まない**。

この文書だけを読めば正しい抽出データが作れることを目指している。迷ったら「推測で埋めない・null にする」。

## 置き場所

```
data_extracts/<scheme_id>/<YYYY-MM-DD>/
  rows.jsonl      # 1行1業者の正規化済みデータ
  manifest.json   # 出典・原本のsha256・抽出者・検品状態
```

- `<scheme_id>` は制度ID（`uv run gyosya-map schemes` で一覧。例 `pref-14.demolition`）。
  ディレクトリ名は `manifest.json` の `scheme_id` と一致していないとエラーになる。
- `<YYYY-MM-DD>` は**抽出した日**。同じ制度の抽出は日付の古い順に取り込まれる（告示は積み上げるため順番が要る）。
- 原本ファイル（PDF・Excel）は**リポジトリに入れない**（`data/` は .gitignore）。原本は sha256 で同一性を示す。
  原本が手元にあればインポータが sha256 を照合し、違っていればエラーになる。手元に無ければ照合を飛ばす
  （クリーンな環境でも取り込めるようにするため）。

## rows.jsonl

1行に1件のJSONオブジェクト。空行は無視される。項目は `licensee` テーブル（`gyosya_map/schema.sql`）に対応する。

| 項目 | 必須 | 内容 |
|---|---|---|
| `license_no` | ◯ | 許可番号・登録番号・指定番号を**原文のまま**（`第1234号` の「第」「号」も付けたまま） |
| `name` | ◯ | 商号・名称を**原文のまま**（`株式会社` を `(株)` に直したりしない） |
| `address` | | 主たる営業所の所在地を原文のまま。記載が無ければ `null` |
| `representative` | | 代表者氏名。照合にだけ使い、地図や照会結果には出さない。記載が無ければ `null` |
| `valid_from` | | 登録年月日・指定年月日を **`YYYY-MM-DD`** に変換して書く |
| `valid_until` | | 有効期間満了日を **`YYYY-MM-DD`**。記載が無ければ `null`（制度の `validity_years` から補われる） |
| `sub_scope` | | 許可の範囲（例 `一時多量ごみ`）。範囲の区別がない制度では `null` |
| `status` | | `active` / `expired` / `revoked` / `unknown`。省略か `null` なら有効期限から決まる（全件名簿のみ） |
| `event` | △ | `new` / `renew` / `change` / `revoke`（告示のみ・必須） |

- **`license_no_key` / `name_key` は書かない。** 照合キーはインポータが `normalize.py` で決定的に作る。
  `needs_review` も書かない（`manifest.json` の `review` から決まる）。書くとエラーになる。
- **読めなかった項目・原本に記載のない項目は `null`。** 推測で埋めない。空文字（`""`）はエラー。
  `license_no` と `name` が読めない行は、その行だけ落とすのではなく、収集担当が原本の見方を直してから出し直す。
- **日付は西暦のISO形式に変換して書く。**（`令和8年4月1日` → `2026-04-01`）。元号の変換はインポータではやらない。
- 知らない項目名があるとエラーになる（`licence_no` のような綴り違いを黙って無視しないため）。
- 同じ業者（`license_no` の照合キーが同じ）が2行あるとエラーになる。どちらが正しいかは収集担当が決める。
- 行が0件の `rows.jsonl` はエラーになる（原本を読めていない可能性があり、
  そのまま全件名簿として取り込むと登録済みの行が全部「名簿から消えた」扱いになるため）。

## manifest.json

**すべての項目を必ず書く**（分からない項目も `null` と書く。項目が無いとエラー）。

| 項目 | null可 | 内容 |
|---|---|---|
| `scheme_id` | | 制度ID。ディレクトリ名と一致させる |
| `source_url` | ◯ | 原本のURL（ファイルそのもののURL。無ければ掲載ページのURL） |
| `source_path` | | 保存した原本の相対パス（例 `data/raw/pref-14.demolition/meibo-2026-11.xlsx`） |
| `source_sha256` | | 原本の sha256（16進64桁）。`sha256sum <原本>` |
| `fetched_at` | | 原本を取得した日（`YYYY-MM-DD`）。名簿の鮮度（90日警告）に使う |
| `kind` | | `full_list`（全件名簿）か `notice`（告示・差分） |
| `published_on` | ◯ | 告示日・公表日（`YYYY-MM-DD`）。`kind` が `notice` のときは必須 |
| `extracted_by` | | 抽出したエージェント名 |
| `extracted_at` | | 抽出した日時（`YYYY-MM-DD` か `2026-11-05T10:00:00`） |
| `source_row_count` | | **原本側の総件数**（原本の表の行数・「計○件」の記載）。`rows.jsonl` の行数との突き合わせに使う |
| `terms_of_use` | ◯ | 公開資料の再利用条件（例 `政府標準利用規約2.0`） |
| `notes` | ◯ | 申し送り（読めなかった列、注記のある行など） |
| `review` | ◯ | 検品の記録。未検品なら `null` |

`review`（検品の記録。すべての項目が必須）:

| 項目 | 内容 |
|---|---|
| `reviewed_by` | 検品した人・エージェント |
| `reviewed_at` | 検品した日時 |
| `result` | `passed`（原本と一致した）か `failed`（合わなかった） |
| `method` | 確認した内容（何をどう確かめたか。あとから読んで再現できるように） |
| `sampled_rows` | 目視で原本と突き合わせた `rows.jsonl` の行番号（1始まり）。1件以上 |

### 件数の突き合わせ

`source_row_count` と `rows.jsonl` の行数が違うとエラーになる（読み落としを黙って通さないため）。
原本に重複行があるなど、理由がはっきりしていて行数が合わないときだけ、
`--allow-count-mismatch` を付けて警告にできる。理由は `notes` に書く。

## 例

`data_extracts/pref-14.demolition/2026-11-05/rows.jsonl`:

```jsonl
{"license_no": "第1234号", "name": "株式会社大和解体工業", "address": "神奈川県横浜市中区本町6丁目50-10", "representative": "山田 太郎", "valid_from": "2024-04-01", "valid_until": "2029-03-31", "sub_scope": null, "status": "active"}
{"license_no": "第1235号", "name": "有限会社みなと建設", "address": "神奈川県川崎市川崎区宮本町1", "representative": null, "valid_from": "2023-06-15", "valid_until": "2028-06-14", "sub_scope": null, "status": "active"}
```

`data_extracts/pref-14.demolition/2026-11-05/manifest.json`:

```json
{
  "scheme_id": "pref-14.demolition",
  "source_url": "https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/documents/meibo.xlsx",
  "source_path": "data/raw/pref-14.demolition/meibo-2026-11-05.xlsx",
  "source_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "fetched_at": "2026-11-05",
  "kind": "full_list",
  "published_on": "2026-11-01",
  "extracted_by": "収集担当エージェント",
  "extracted_at": "2026-11-05T10:20:00",
  "source_row_count": 2,
  "terms_of_use": "政府標準利用規約2.0",
  "notes": "原本の「備考」列は取り込んでいない。",
  "review": {
    "reviewed_by": "収集担当エージェント",
    "reviewed_at": "2026-11-05T11:00:00",
    "result": "passed",
    "method": "原本の件数（2件）と行数の一致を確認し、先頭と末尾の2行を原本の表示と1文字ずつ突き合わせた",
    "sampled_rows": [1, 2]
  }
}
```

告示（`kind: "notice"`）の場合は各行に `event` を書き、`status` は書かない:

```jsonl
{"license_no": "第1236号", "name": "株式会社港北工業", "address": "神奈川県横浜市港北区大豆戸町26-1", "representative": null, "valid_from": "2026-10-01", "valid_until": null, "sub_scope": null, "event": "new"}
{"license_no": "第1100号", "name": "株式会社旧解体", "address": null, "representative": null, "valid_from": null, "valid_until": "2026-09-30", "sub_scope": null, "event": "revoke"}
```

## 取り込み

```bash
# 1件
uv run gyosya-map import-extract --scheme pref-14.demolition --dir data_extracts/pref-14.demolition/2026-11-05/

# data_extracts/ 配下の全件（制度ごとに日付の古い順。再現性の確認に使う）
uv run gyosya-map import-extract --all
```

- `manifest.json` と `rows.jsonl` を検証し、**1件でも不正なら何も取り込まずにエラーで落ちる**。
  どの行の何が不正かを1件1行で出す。
- `manifest` の情報で `source_document` を登録し（`path` と `sha256` をそのまま記録）、
  `licensee` の行を入れ、`roster_fetch`（名簿の取得日）を更新する。
- `--all` は**先に全件を検証してから**取り込む。どれか1件でも形式に合っていなければ、どれも取り込まない。

### 既存の行の更新・重複の扱い

- `licensee` は `UNIQUE (scheme_id, license_no_key)`。同じ制度で同じ番号の行は**上書き更新**する
  （商号・所在地・有効期間・出典・検品状態を新しい抽出データの値にする）。
- 全件名簿（`full_list`）の取り込みでは、**新しい名簿に無くなった行**は有効でないものにする
  （有効期限が過ぎていれば `expired`、そうでなければ `unknown`）。行は消さない（過去の記録として残す）。
- 同じ原本（`scheme_id` と `source_sha256` が同じ）をもう一度取り込んでも `source_document` は増えず、
  同じ結果になる（何度でも取り込み直せる）。
- 1つの `rows.jsonl` の中に同じ番号の行が2つあるのはエラー。原本の重複は収集担当が解決する。

### 検品と地図

- `review.result == "passed"` のときだけ `licensee.needs_review = 0` になる。
  未検品（`review: null`）や `failed` の行は `needs_review = 1`。
- **`needs_review = 1` の行は地図に出さない。** `map` は除外した件数を出し、
  `map --include-unreviewed` で含められる（含めた点は「未検品」と付く）。
- `check`（照合）は未検品の行も使うが、結果に「未検品」と出し、注意を付ける。

## 既存のパーサーとの関係

`gyosya_map/ingest/` には Excel・CSV・PDF のパーサーがあり、`import` / `fetch --import` で使える。

- `ingest/excel.py`・`ingest/csvfile.py`: きれいな表には安く効くので、これからも使う。
  見出しの行や列名の違いは制度の読み込み設定（`seed` の `parser`）で吸収する。
- `ingest/pdf.py`: **既存の川崎市・神奈川県分の維持にとどめ、新規自治体の受け皿にはしない。**
  PDFの書式は自治体ごとに違い、パーサーに分岐を足すほど壊れやすくなる。
  新しい自治体のPDFは収集担当エージェントが読み、この形式の抽出データとしてコミットする。
- **インポータ（`extracts.py`）には自治体ごとの書式解釈を入れない。** ここに「○○市はこの列が…」という
  分岐が出てきたら、それは抽出データ側（収集担当の仕事）で解決すべき問題。
