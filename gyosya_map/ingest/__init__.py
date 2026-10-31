"""名簿の取り込み。公開形式（publication_format）ごとにパーサーを持ち、自治体ごとの違いは
制度の読み込み設定（config.ParserConfig）で吸収する。

ここのパーサー（Excel・CSV・PDF）は、きれいな表で安く済む自治体のための近道。新しい自治体は
原本の読み取りを収集担当のエージェントに任せ、`data_extracts/` の抽出データとして
取り込む（`gyosya_map/extracts.py`、`doc/extract-format.md`）。
"""

from .config import ParserConfig, load_for_scheme  # noqa: F401
from .parse import parse_file  # noqa: F401
from .roster import apply_notice_events, import_full_list, register_document, store_document  # noqa: F401
