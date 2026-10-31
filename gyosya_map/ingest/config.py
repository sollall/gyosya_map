"""制度ごとの名簿の読み込み設定。scheme.parser_config（seed の "parser"）に JSON で持つ。

自治体ごとに見出しの言い回しや表の位置が違うため、コードを変えずに設定で吸収する。

  {
    "format": "excel",                 # excel / pdf / notice_pdf / csv（省略時は拡張子から）
    "sheets": ["登録業者一覧"],         # 読むシート（省略時は全シート）
    "header_row": 3,                   # 見出しの行（1始まり。省略時は自動で探す）
    "columns": {"license_no": "登録番号（知事）", "valid_until": 6},  # 見出しの文字列か列番号（1始まり）
    "aliases": {"name": ["屋号"]},       # 自動判定に足す見出しの語
    "skip_pattern": "以下余白|合計",     # 名称がこれに当たる行は捨てる（正規表現）
    "events": {"revoke": ["指定の効力を失った"]},  # 告示の見出しに足す語
    "default_event": "new"             # 1つの告示が1種類だけのとき
  }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

FORMATS = ("excel", "pdf", "notice_pdf", "csv")
EVENTS = ("new", "renew", "change", "revoke")


@dataclass
class ParserConfig:
    format: str | None = None
    sheets: list[str] | None = None
    header_row: int | None = None
    columns: dict[str, str | int] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    skip_pattern: str | None = None
    events: dict[str, list[str]] = field(default_factory=dict)
    default_event: str | None = None

    def __post_init__(self):
        if self.format is not None and self.format not in FORMATS:
            raise ValueError(f"未知の format: {self.format}（{', '.join(FORMATS)}）")
        for e in list(self.events) + ([self.default_event] if self.default_event else []):
            if e not in EVENTS:
                raise ValueError(f"未知の異動の種類: {e}（{', '.join(EVENTS)}）")
        from .columns import ALIASES
        for f in list(self.columns) + list(self.aliases):
            if f not in ALIASES:
                raise ValueError(f"未知の項目: {f}（{', '.join(ALIASES)}）")
        if self.skip_pattern:
            re.compile(self.skip_pattern)

    @classmethod
    def from_json(cls, text: str | None) -> "ParserConfig":
        return cls(**json.loads(text)) if text else cls()

    def skip(self, name: str) -> bool:
        return bool(self.skip_pattern and re.search(self.skip_pattern, name))


def load_for_scheme(conn, scheme_id: str) -> ParserConfig:
    row = conn.execute("SELECT parser_config FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()
    if row is None:
        raise KeyError(f"未登録の制度です: {scheme_id}")
    return ParserConfig.from_json(row[0])
