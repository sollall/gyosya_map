# gyosya_map — 自治体別 業者公認確認データベース

住所と業者名（または許可番号）から、その業者がその住所を管轄する機関の公認を受けているかを判定する。
広告の「水道局指定」「許可業者」が**どこの機関の公認か**を照合できるようにするのが目的。

仕様書: [自治体別 業者公認確認データベース 仕様書](https://claude.ai/artifact/NGD2J6jMVFHDWdLbFergfN)

現在は **フェーズ0（川崎市＋神奈川県の試作）**。制度と名簿は川崎市・神奈川県の分だけだが、
市区町村マスター（全国1,914件）と管轄の既定ルールは全国分を持っているので、
他の自治体は制度を書き足すだけで照合できる（下の「自治体を追加する」）。

## 使い方

[uv](https://docs.astral.sh/uv/) を使う。`uv sync` で依存（テスト用の dev グループを含む）を入れる。

```bash
uv sync
uv run gyosya-map init
```

版を上げたあとも `uv run gyosya-map init` を実行すれば、古いDBに足りない列を追加する。
列の追加で直せない変更のときは `uv run gyosya-map init --reset` で作り直す（取り込んだ名簿は消えるが、原本は data/raw/ に残る）。

川崎市の制度は `muni-141305.waste` のように、担当機関ID＋業種のIDで指定する（`uv run gyosya-map schemes` で一覧）。

### 名簿の取り込み（抽出データ／推奨）

原本（PDF・Excel・CSV）の読み取りは**収集担当のエージェント**が行い、**正規化済みの抽出データ**を
`data_extracts/<制度ID>/<日付>/` にコミットする。取り込みは、その抽出データを検証してDBに入れるだけの
決定的な処理で、自治体ごとの書式解釈を含まない。形式は [`doc/extract-format.md`](doc/extract-format.md)。

```bash
# 1件（manifest.json と rows.jsonl を検証し、不正なら何も取り込まずに落ちる）
uv run gyosya-map import-extract --scheme pref-14.demolition --dir data_extracts/pref-14.demolition/2026-11-05/

# data_extracts/ 配下の全件（制度ごとに日付の古い順。クリーンなDBに同じ結果を作れるか確かめるのに使う）
uv run gyosya-map import-extract --all
```

コミット済みの抽出データと決定的なコードだけで、クリーンな環境から同じDBと同じ地図HTMLが再生成できる状態を保つ。
検品（`manifest.json` の `review.result` が `passed`）が済んでいない行は、`needs_review = 1` になり地図に出ない。

### 名簿のダウンロード

制度ごとに登録してある掲載ページ（`source_url`）を開き、ページ内の Excel・PDF・CSV へのリンクから名簿を探してダウンロードする。
同じホストへのアクセスは10秒以上あける。

```bash
# 掲載ページにあるファイルのリンクを一覧する（どれが名簿か確かめる）
uv run gyosya-map fetch --scheme pref-14.demolition --list

# ダウンロードして取り込む（data/downloads/<制度ID>/<日付>/ に保存。前回と同じファイルなら取り込みは省略）
uv run gyosya-map fetch --scheme pref-14.demolition --import

# 掲載ページが分かっている全件名簿型の制度をまとめて（月1回の定期実行向け）
uv run gyosya-map fetch --all --import
```

ページに名簿以外のファイル（申請の手引きなど）もある場合は、seed の制度に `download` を書いて絞る。
絞り込みの設定が無いままファイルのリンクが複数あるときは、推測で選ばずに候補を出して止まる。
条件を書いた場合は、合う最初のリンクを取る。

```json
"download": {"text_pattern": "登録業者名簿", "href_pattern": "\\.xlsx?$", "pick": "first"}
```

告示（差分）型も `--import` で取り込める。告示日は告示PDFの「日付＋告示者（市長・管理者など）」の行から読み取り、
読み取れないときは `notice --date` での取り込みを案内する。

### 読み取れないとき

```bash
# PDF・Excel から読み取れる文字と表をそのまま見る（見出しの行や列名を確かめる）
uv run gyosya-map inspect --file data/downloads/<制度ID>/<日付>/<ファイル>

# 間違ったファイルを取り込んだときは、その制度の取り込みデータを消す（原本ファイルは残る）
uv run gyosya-map purge --scheme <制度ID>
```

### 手元のファイルを取り込む（パーサー）

`ingest/` のパーサー（Excel・CSV・PDF）で直接取り込むこともできる。きれいな表には安く効くので残しているが、
**PDF は既存の川崎市・神奈川県分の維持にとどめ、新しい自治体の受け皿にはしない**
（新しい自治体は上の抽出データで取り込む。理由は `doc/extract-format.md`）。

たとえば神奈川県の解体工事業者名簿なら、
[掲載ページ](https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html)から Excel をダウンロードする。
下の例のファイル名（`<ダウンロードした名簿>.xlsx` など）は、実際に保存したファイルのパスに置き換える。

```bash
# 1. 取り込む前に、見出しと行が読み取れるか確かめる
uv run gyosya-map preview --scheme pref-14.demolition --file ~/Downloads/<ダウンロードした名簿>.xlsx

# 2. 全件名簿（Excel / PDF / CSV）を取り込む。原本は data/raw/ に保存される。--url は出典として記録する掲載ページ
uv run gyosya-map import --scheme pref-14.demolition --file ~/Downloads/<ダウンロードした名簿>.xlsx \
  --url https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html

# --file にはファイルそのもののURL（.xlsx / .pdf へのリンク）も指定できる。掲載ページ（HTML）のURLは不可
uv run gyosya-map import --scheme pref-14.demolition --file https://www.pref.kanagawa.jp/<名簿ファイルのパス>.xlsx

# 告示（差分）を古い順に積み上げる（--date を省くと告示PDFから告示日を読み取る）
uv run gyosya-map notice --scheme kawasaki-suido.water --file https://www.city.kawasaki.jp/koho/cmsfiles/0000007/7422/7422.pdf \
  --date 2026-09-30
```

読み取りが0件のときは、取り込みを中止する（登録済みの行を消してしまわないため）。
`preview --parser-config '{"header_row": 3}'` のように読み込み設定を試して、合ったものを seed の `parser` に書く。

```bash
# 照合
uv run gyosya-map check --address 川崎市中原区小杉町3丁目1 --trade water --license 123
uv run gyosya-map check --address 川崎市幸区1 --trade 解体 --name サンプル解体 --json

# 建設業許可（検索システム型）も確かめる。ネットワークに接続する
uv run gyosya-map check --address 川崎市幸区1 --trade reform --name 見本建設株式会社 --search

# 定期処理（月1回など）
uv run gyosya-map expire    # 有効期限切れの行を expired に
uv run gyosya-map monitor   # 掲載ページのハッシュ監視 → 変化・取得失敗を再取得キューへ
uv run gyosya-map schemes   # 制度の一覧と名簿の取得日
```

業種: `water` 水道 / `sewer` 下水・排水 / `waste` 不用品回収・遺品整理 / `septic` 浄化槽 / `electrical` 電気工事 /
`demolition` 解体 / `reform` 大きなリフォーム。制度なし業種: `locksmith` `small_reform` `pest` `aircon_cleaning`。
日本語の業種名（「解体」など）でも指定できる。

## 地図

今日の時点で有効な登録業者を、業種ごとに色分けして地図にプロットしたHTMLを書き出す
（ブラウザで開く。地図は国土地理院の淡色地図）。期限切れ・取消・名簿から消えた業者は出さない。

```bash
# 初回だけ: 町丁目の座標（Geolonia 住所データ, 約50MB）をダウンロードして取り込む
uv run gyosya-map geo-update

# 名簿をまとめて取得・取り込み（全件名簿と告示。告示日は告示PDFから読み取る）
uv run gyosya-map fetch --all --import

# 地図を書き出す。業種ごとの件数と、名簿がまだ無い制度も表示する
uv run gyosya-map map --out data/map.html
uv run gyosya-map map --trade demolition --out data/kaitai.html

# 検品が済んでいない行も出す（確認用。既定では出さない）
uv run gyosya-map map --include-unreviewed --out data/map-all.html
```

- 検品（目視確認）が済んでいない行（`needs_review = 1`）は出さない。除いた件数を実行結果と地図の左下に出し、
  `--include-unreviewed` で含められる（含めた点はポップアップに「未検品」と付く）。
- 業種ごとに表示を切り替えられ、近い点はまとめて表示する。
- 左下に、名簿をまだ取り込んでいない制度（公開形式が未調査・検索システム型など）を出す。
- ピンを押すと、業者名・番号・有効期限・所在地・出典の名簿と取得日が出る。代表者名は出さない。
- 位置は町丁目の代表点で、番地までは合わせていない。丁目が分からない住所は町名の中心、
  町名も分からない住所は市区町村の中心に置き、ポップアップにその旨を出す。
- 市区町村が分からない住所は表示せず、件数を出す。
- 告示（差分）型の名簿は過去分が欠けるため、実際の登録業者より少なく出ることがある。
  `roster_exhaustive: false` の制度（公開名簿が制度の一部しか載せていないもの）も同じで、
  地図に出ないことを「公認がない」と読めない。

## 判定

| 判定 | 条件 |
|---|---|
| 公認あり | 担当機関の名簿に有効な行がある（範囲が必要な制度は範囲も一致） |
| 他機関の公認のみ | 別の機関・別の対象地域の名簿にだけ載っている（例: 横浜市の指定番号を川崎市の住所で照会） |
| 期限切れ・取消 | 行はあるが有効期間外、または廃止・取消 |
| 確認できず | 完全性の高い名簿（全件名簿・検索システム）をすべて確かめても見つからない |
| 判定不能 | 名簿が告示差分のみ・未公開・未取得・網羅的でない、または制度情報が未整備 |
| 制度なし | 公認制度がない業種、または公共下水道の区域外など |

結果には根拠（出典URL・取得日）、その地域の注意点（`important` は必ず表示）、取得から90日を超えた名簿の警告が付く。
検品が済んでいない行は根拠に「未検品」と付け、注意を出す（地図とは違い、照合では未検品の行も使う）。
代表者名は照合にだけ使い、結果には出さない。

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
- `excluded_municipalities`（制度ごと）は、担当機関は合っているが**制度の側が対象にしていない**市区町村。
  キーは `municipalities` と同じ書き方で、政令市を書けば行政区にも効く。管轄解決で候補から外し、
  理由を結果の警告に出す。対象外の市の住所で県の名簿に当たると「県の名簿に無い＝未登録」と
  誤判定するので必ず書く（例: 埼玉県浄化槽保守点検業者登録は「さいたま市、川越市、川口市及び越谷市を除く」県内）。
- `roster_exhaustive: false` は、公開名簿が制度の一部しか載せていない制度。名簿を取り込んでも完全性は
  `low` のままで、名簿に無いことを根拠に「確認できず」とは判定せず「判定不能＋担当課へ確認」になる
  （例: 東京都浄化槽工事業者登録は「登録」だけが公開され、建設業許可に基づく「届出」の一覧は非公開）。
- `parser` は名簿の読み込み設定（見出しの行・列名・シート・告示の見出し語など。`ingest/config.py`）。
  きれいな表ならコードを変えずにここで吸収できる。書く前に
  `uv run gyosya-map preview --scheme ID --file 名簿.xlsx --parser-config '{"header_row": 3}'` で読み取りを確かめる。
- 読み込み設定では読み取れない書式（多くのPDFなど）に**パーサーを足さない**。原本の読み取りは収集担当の
  エージェントに任せ、`data_extracts/` の抽出データとして取り込む（[`doc/extract-format.md`](doc/extract-format.md)）。
  この場合 seed に書くのは制度だけで、`parser` は要らない。

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
| `gyosya_map/extracts.py` | 抽出データ（`data_extracts/`）の検証と取り込み。書式解釈を含まない決定的な処理 |
| `doc/extract-format.md` | 抽出データの形式（収集担当エージェント向け） |
| `gyosya_map/ingest/` | 公開形式ごとのパーサー（Excel .xlsx/.xls・PDF・告示PDF・CSV）と制度ごとの読み込み設定、名簿のダウンロード、原本保存、ページ監視 |
| `gyosya_map/matcher.py` | 照合ロジックと判定 |
| `gyosya_map/search_system.py` | 検索システム型の制度（国交省 建設業者検索）への照会 |
| `gyosya_map/freshness.py` | 期限の自動処理、90日警告 |
| `gyosya_map/geo.py`, `mapview.py` | 住所 → 町丁目の代表点、地図のHTML |

### 検索システム型の制度（国交省 建設業者検索）

建設業許可（`mlit.construction`・`pref-<コード>.construction`）には名簿の一括ダウンロードがなく、
[建設業者・宅建業者等企業情報検索システム](https://etsuran2.mlit.go.jp/TAKKEN/kensetuKensaku.do)しかない。
そこで `gyosya_map/search_system.py` の `MlitConstructionSearch` が、**照合のときに業者名または
許可番号で1件ずつ問い合わせる**（`check --search`、または `check()` に `search_client=` を渡す）。
渡さない場合は「検索システムでの確認が必要」として判定不能を返す。

- **結果は `licensee` に入れない**。取り込むとクリーンな環境から同じDBを再生成できなくなり、
  検品済みの抽出データだけを地図に出すという約束も崩れるため、照合のときだけ参照する。
  地図では引き続き「名簿未取り込み」の制度として左下に出る。
- 許可番号で検索すると他県の同じ番号も返るので、許可行政庁コード（業者概要へのリンクの先頭2桁。
  00＝国土交通大臣、01〜47＝都道府県）で制度と一致する行だけを採り、商号・番号の完全一致で絞る。
- 有効期間は業者概要の画面にしかないため、一致した行ごとに1回だけ開く。
  同じホストへのアクセスは10秒以上あけるので、1回の照合に数十秒かかる。
- 「該当なし」「取得失敗」「システム変更（画面の作りが想定と違う）」「候補が多すぎる」を区別し、
  該当なし以外は判定不能のまま理由を警告に出す（詳細は `log`／`--search` の標準エラー出力）。
- robots.txt・利用規約・ご利用方法のPDFを確認し、機械的アクセスを禁じる記載がないことを
  2026-10-31 に確かめた（判断の根拠は `search_system.py` の冒頭と制度の `terms_of_use`）。

## フェーズ0で残っていること

- 実データの取り込み: 各名簿・告示の実ファイルでパーサーの見出し対応（`ingest/columns.py`）を確認する
- 電気工事は神奈川県も経済産業大臣も登録簿が一覧公開されておらず（`phone_only`。有料の閲覧・謄本交付請求だけ）、地図に出せない。
  業種として電気工事を出すなら、都道府県ごとに公開状況を調べ直すか、照会先の案内に留める判断が必要
- 建設業許可の許可業種（土・建・…の略号）は読み取っていない。範囲（`required_sub_scope`）が必要な
  建設業の制度が出てきたら、業者概要の業種の表を読む必要がある
- 仕様書の未決事項（提供形態、住所→水道事業体の対応表の入手元、告示の過去分の取り方、浄化槽登録の全国一覧）

## テスト

```bash
uv run pytest
```

テストの名簿・告示はすべて架空の業者で、Excel・PDFはテスト内で生成している。
