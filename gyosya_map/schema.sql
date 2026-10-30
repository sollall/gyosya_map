-- 制度（どの機関がどの公認を出すか）と名簿（誰が公認を受けているか）を分けて持つ。

CREATE TABLE IF NOT EXISTS authority (
    authority_id TEXT PRIMARY KEY,          -- 例: kawasaki-suido, pref-kanagawa
    name         TEXT NOT NULL,             -- 例: 川崎市上下水道局
    level        TEXT NOT NULL CHECK (level IN ('national', 'prefecture', 'municipality', 'water_utility')),
    parent_id    TEXT REFERENCES authority(authority_id)
);

CREATE TABLE IF NOT EXISTS scheme (
    scheme_id          TEXT PRIMARY KEY,    -- 機関×業種（×公認の種類）で一意
    authority_id       TEXT NOT NULL REFERENCES authority(authority_id),
    trade              TEXT NOT NULL,
    license_name       TEXT NOT NULL,       -- 自治体ごとの正式名称
    validity_years     INTEGER,
    publication_format TEXT CHECK (publication_format IN
                         ('full_list_pdf', 'full_list_excel', 'search_system', 'notice_diff', 'phone_only')),
                                            -- NULL = 未調査
    source_url         TEXT,
    contact            TEXT,
    terms_of_use       TEXT,                -- 公開資料の再利用条件
    required_sub_scope TEXT,                -- 範囲として必須の項目（例: 一時多量ごみ）
    verified_at        TEXT
);
CREATE INDEX IF NOT EXISTS scheme_trade ON scheme(trade, authority_id);

CREATE TABLE IF NOT EXISTS scheme_note (
    note_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id TEXT NOT NULL REFERENCES scheme(scheme_id),
    severity  TEXT NOT NULL CHECK (severity IN ('info', 'important')),
    body      TEXT NOT NULL
);

-- 取得した原本（PDF・Excel）。licensee の各行から出典をたどる。
CREATE TABLE IF NOT EXISTS source_document (
    doc_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id  TEXT NOT NULL REFERENCES scheme(scheme_id),
    url        TEXT,
    path       TEXT NOT NULL,               -- 保存した原本のパス
    sha256     TEXT NOT NULL,
    kind       TEXT NOT NULL,               -- full_list / notice
    published_on TEXT,                      -- 告示日など
    fetched_at TEXT NOT NULL,
    UNIQUE (scheme_id, sha256)
);

CREATE TABLE IF NOT EXISTS licensee (
    licensee_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id      TEXT NOT NULL REFERENCES scheme(scheme_id),
    license_no     TEXT NOT NULL,           -- 原文のまま
    license_no_key TEXT NOT NULL,           -- 照合用に正規化した番号
    name           TEXT NOT NULL,           -- 商号（原文）
    name_key       TEXT NOT NULL,           -- 正規化した名前
    address        TEXT,
    representative TEXT,
    valid_from     TEXT,
    valid_until    TEXT,
    sub_scope      TEXT,                    -- 許可の範囲（例: 一時多量ごみ）
    source_doc     INTEGER REFERENCES source_document(doc_id),
    fetched_at     TEXT,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'revoked', 'unknown')),
    needs_review   INTEGER NOT NULL DEFAULT 0, -- PDF抽出など目視検品待ち
    UNIQUE (scheme_id, license_no_key)
);
CREATE INDEX IF NOT EXISTS licensee_name ON licensee(name_key);
CREATE INDEX IF NOT EXISTS licensee_no ON licensee(license_no_key);

-- 名簿の取得日（名簿単位の鮮度。90日警告に使う）
CREATE TABLE IF NOT EXISTS roster_fetch (
    scheme_id  TEXT PRIMARY KEY REFERENCES scheme(scheme_id),
    fetched_at TEXT NOT NULL,
    doc_id     INTEGER REFERENCES source_document(doc_id)
);

-- 管轄解決
CREATE TABLE IF NOT EXISTS municipality (
    code            TEXT PRIMARY KEY,       -- 全国地方公共団体コード6桁
    pref_code       TEXT NOT NULL,          -- 2桁
    name            TEXT NOT NULL,          -- 例: 川崎市中原区
    city_name       TEXT,                   -- 区の場合の市名（例: 川崎市）
    kind            TEXT NOT NULL,          -- prefecture / designated_city / ward / city / town / village / special_ward
    health_center_city INTEGER NOT NULL DEFAULT 0, -- 保健所設置市
    public_sewer    INTEGER NOT NULL DEFAULT 1     -- 公共下水道の有無
);

-- 業種ごとの担当機関（市区町村コード単位）
CREATE TABLE IF NOT EXISTS jurisdiction (
    code         TEXT NOT NULL REFERENCES municipality(code),
    trade        TEXT NOT NULL,
    authority_id TEXT NOT NULL REFERENCES authority(authority_id),
    PRIMARY KEY (code, trade, authority_id)
);

-- 町丁目単位の上書き（給水区域が市境をまたぐ、下水道区域外など）
CREATE TABLE IF NOT EXISTS jurisdiction_override (
    code         TEXT NOT NULL REFERENCES municipality(code),
    town_prefix  TEXT NOT NULL,             -- 町名（丁目は含めても良い。前方一致）
    trade        TEXT NOT NULL,
    authority_id TEXT,                      -- NULL = 制度なし（区域外）
    note         TEXT,
    PRIMARY KEY (code, town_prefix, trade)
);

-- 掲載ページの変化検知
CREATE TABLE IF NOT EXISTS page_watch (
    url          TEXT PRIMARY KEY,
    scheme_id    TEXT REFERENCES scheme(scheme_id),
    last_sha256  TEXT,
    checked_at   TEXT,
    last_error   TEXT
);

CREATE TABLE IF NOT EXISTS refetch_queue (
    queue_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL,
    scheme_id  TEXT,
    reason     TEXT NOT NULL,
    queued_at  TEXT NOT NULL,
    done_at    TEXT
);
