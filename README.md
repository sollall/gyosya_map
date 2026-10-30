# gyosya_map — 自治体別 業者公認確認データベース

住所と業者名（または許可番号）から、その業者がその住所を管轄する機関の公認を受けているかを判定する。
広告の「水道局指定」「許可業者」が**どこの機関の公認か**を照合できるようにするのが目的。

仕様書: [自治体別 業者公認確認データベース 仕様書](https://claude.ai/artifact/NGD2J6jMVFHDWdLbFergfN)

現在は **フェーズ0（川崎市＋神奈川県の試作）**。制度と名簿は川崎市・神奈川県の分だけだが、
市区町村マスター（全国1,914件）と管轄の既定ルールは全国分を持っているので、
他の自治体は制度を書き足すだけで照合できる（下の「自治体を追加する」）。

## 使い方

```bash
pip install -e '.[test]'
python -m gyosya_map init
```

川崎市の制度は `muni-141305.waste` のように、担当機関ID＋業種のIDで指定する（`python -m gyosya_map schemes` で一覧）。

```bash
# 全件名簿（Excel / PDF / CSV）を取り込む。原本は data/raw/ に保存される
python -m gyosya_map import --scheme pref-14.demolition --file 解体工事業者登録簿.xlsx \
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

## 自治体を追加する

担当機関は法律で決まる**既定ルール**（`jurisdiction.py` の `default_authorities`）で全国の市区町村について自動で決まる。
seed に書くのは、制度（名簿）と、既定と違う管轄だけ。

| 業種 | 既定の担当機関 |
|---|---|
| 電気工事 | 都道府県＋経済産業大臣 |
| 解体 | 都道府県（建設業許可は都道府県＋国土交通大臣） |
| 一般廃棄物・下水道 | 市町村（行政区は市、特別区は区） |
| 浄化槽 | 市町村（清掃）＋保健所設置市または都道府県（保守点検）＋都道府県（工事） |
| 水道 | 既定なし。事業体が市区町村と一致しないので必ず書く |

担当機関のIDは `pref-<都道府県コード>`、`muni-<団体コード6桁>` で、市区町村マスターから自動で作る。
上下水道局・企業団などは seed の `authorities` に書く。

`gyosya_map/seed/prefectures/<コード>-<名前>.json` を足す:

```json
{
  "prefecture": "13",
  "authorities": [{"authority_id": "tokyo-suido", "name": "東京都水道局", "level": "water_utility", "parent_id": "pref-13"}],
  "schemes": [{
    "scheme_id": "pref-13.demolition", "authority_id": "pref-13", "trade": "demolition",
    "license_name": "東京都解体工事業登録", "validity_years": 5, "publication_format": "full_list_excel",
    "source_url": "https://...", "contact": "東京都 ○○課", "verified_at": "2026-10-30",
    "parser": {"sheets": ["登録業者一覧"], "header_row": 3, "aliases": {"name": ["屋号"]}}
  }],
  "municipalities": {"府中市": {"jurisdiction": {"water": ["tokyo-suido"]}}, "檜原村": {"public_sewer": false}},
  "overrides": [{"municipality": "八王子市", "town_prefix": "○○町", "trade": "sewer", "authority_id": null, "note": "公共下水道の区域外"}]
}
```

- `municipalities` のキーは都道府県内の名前か団体コード。政令市に書いた例外は行政区にも効く。
- `parser` は名簿の読み込み設定（見出しの行・列名・シート・告示の見出し語など。`ingest/config.py`）。
  自治体ごとの書式の違いはコードを変えずにここで吸収する。書く前に
  `python -m gyosya_map preview --scheme ID --file 名簿.xlsx --parser-config '{"header_row": 3}'` で読み取りを確かめる。

### 住所の解析

- 都道府県名があればその中だけで探す。無くて同名の市区町村が複数あれば（府中市・伊達市）入力し直しを求める。
- 郡名はあってもなくてもよい（「愛甲郡愛川町」「愛川町」）。「郡山市」「大和郡山市」は郡と誤らない。
- 政令市は区まで必要。

### 市区町村マスターの更新

`gyosya_map/seed/municipalities.csv` は [Geolonia 住所データ](https://github.com/geolonia/japanese-addresses)
（CC BY 4.0）から `scripts/build_municipalities.py` で作っている。合併・政令市移行のときは作り直す。
保健所設置市（中核市62市と地域保健法施行令で定める6市）は `seed/health_center_cities.csv`。政令市と特別区は自動で付く。

## 構成

| ファイル | 内容 |
|---|---|
| `gyosya_map/schema.sql` | authority / scheme / scheme_note / licensee / source_document と管轄・監視のテーブル |
| `gyosya_map/seed/` | 市区町村マスター、保健所設置市、国の制度、都道府県ごとの制度と例外 |
| `gyosya_map/jurisdiction.py` | 住所 → 市区町村コード → 業種ごとの担当機関（既定ルール、例外、町丁目の上書き） |
| `gyosya_map/normalize.py` | 商号（法人格・全角半角・空白・旧字体）、番号、住所、和暦の正規化 |
| `gyosya_map/ingest/` | 公開形式ごとのパーサー（Excel・PDF・告示PDF・CSV）と制度ごとの読み込み設定、原本保存、取得とページ監視 |
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
