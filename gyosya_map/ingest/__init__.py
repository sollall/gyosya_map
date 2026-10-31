"""名簿の取り込み。公開形式（publication_format）ごとにパーサーを持ち、自治体ごとの違いは
制度の読み込み設定（config.ParserConfig）で吸収する。"""

from .config import ParserConfig, load_for_scheme  # noqa: F401
from .parse import parse_file  # noqa: F401
from .roster import apply_notice_events, import_full_list, store_document  # noqa: F401
