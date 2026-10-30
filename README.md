# gyosya_map — 自治体別 業者公認確認データベース

住所と業者名（または許可番号）から、その業者がその住所を管轄する機関の公認を受けているかを判定する。
広告の「水道局指定」「許可業者」が**どこの機関の公認か**を照合できるようにするのが目的。

仕様書: [自治体別 業者公認確認データベース 仕様書](https://claude.ai/artifact/NGD2J6jMVFHDWdLbFergfN)

現在は **フェーズ0（川崎市＋神奈川県の試作）**。7業種すべての照合が通るところまでと、データモデルを実装している。
横浜市は「他機関の公認」の判定用に水道の制度だけ収録している。

## 使い方

```bash
pip install -e '.[test]'
python -m gyosya_map init
```

```bash
# 全件名簿（Excel / PDF / CSV）を取り込む。原本は data/raw/ に保存される
python -m gyosya_map import --scheme pref-kanagawa.demolition --file 解体工事業者登録簿.xlsx \
  --url https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html

# 告示（差分）を古い順に積み上げる
python -m gyosya_map notice --scheme kawasaki-suido.water --file 7422.pdf --date 2026-09-30

# 照合
python -m gyosya_map check --address 川崎市中原区小杉町3丁目1 --trade water --license 123
python -m gyosya_map check --address 川崎市幸区1 --trade 解体 --name サンプル解体 --json

# 定期処理（月1回など）
python -m gyosya_map expire    # 有効期限切れの行を expired に
python -m gyosya_map monitor   # 掲載ページのハッシュ監視 → 変化・取得失敗を再取得キューへ
python -m gyosya_map schemes   # 制度の一覧と名簿の取得日
```

業種: `water` 水道 / `sewer` 下水・排水 / `waste` 不用品回収・遺品整理 / `septic` 浄化槽 / `electrical` 電気工事 /
`demolition` 解体 / `reform` 大きなリフォーム。制度なし業種: `locksmith` `small_reform` `pest` `aircon_cleaning`。
日本語の業種名（「解体」など）でも指定できる。

## 判定

| 判定 | 条件 |
|---|---|
| 公認あり | 担当機関の名簿に有効な行がある（範囲が必要な制度は範囲も一致） |
| 他機関の公認のみ | 別の機関の名簿にだけ載っている（例: 横浜市の指定番号を川崎市の住所で照会） |
| 期限切れ・取消 | 行はあるが有効期間外、または廃止・取消 |
| 確認できず | 完全性の高い名簿（全件名簿・検索システム）をすべて確かめても見つからない |
| 判定不能 | 名簿が告示差分のみ・未公開・未取得、または制度情報が未整備 |
| 制度なし | 公認制度がない業種、または公共下水道の区域外など |

結果には根拠（出典URL・取得日）、その地域の注意点（`important` は必ず表示）、取得から90日を超えた名簿の警告、
PDF抽出行の検品前警告が付く。代表者名は照合にだけ使い、結果には出さない。

## 構成

| ファイル | 内容 |
|---|---|
| `gyosya_map/schema.sql` | authority / scheme / scheme_note / licensee / source_document と管轄・監視のテーブル |
| `gyosya_map/seed/kanagawa.json` | フェーズ0の担当機関・制度・市区町村コード・管轄 |
| `gyosya_map/jurisdiction.py` | 住所 → 市区町村コード → 業種ごとの担当機関（町丁目の上書き、下水道区域外） |
| `gyosya_map/normalize.py` | 商号（法人格・全角半角・空白・旧字体）、番号、住所、和暦の正規化 |
| `gyosya_map/ingest/` | 公開形式ごとのパーサー（Excel・PDF・告示PDF・CSV）、原本保存、取得とページ監視 |
| `gyosya_map/matcher.py` | 照合ロジックと判定 |
| `gyosya_map/freshness.py` | 期限の自動処理、90日警告 |

検索システム型の制度（国交省 建設業者検索）は、照会時に問い合わせる `SearchSystemClient` を `check()` に渡す。
渡さない場合は「検索システムでの確認が必要」として判定不能を返す。

## フェーズ0で残っていること

- 実データの取り込み: 各名簿・告示の実ファイルでパーサーの見出し対応（`ingest/columns.py`）を確認する
- 公開形式が未調査の制度（`publication_format: null`）: 川崎市の一般廃棄物、神奈川県・経産大臣の電気工事、横浜市の水道
- 建設業者検索の `SearchSystemClient` 実装
- 仕様書の未決事項（提供形態、住所→水道事業体の対応表の入手元、告示の過去分の取り方、浄化槽登録の全国一覧）

## テスト

```bash
python -m pytest
```

テストの名簿・告示はすべて架空の業者で、Excel・PDFはテスト内で生成している。
