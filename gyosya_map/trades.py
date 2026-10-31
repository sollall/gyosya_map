"""業種の定義。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trade:
    key: str
    label: str
    # 照合で受け付ける制度の種類（scheme.trade の値）。空なら制度なし業種。
    scheme_trades: tuple[str, ...]


TRADES: dict[str, Trade] = {t.key: t for t in [
    Trade("water", "水道（給水）", ("water",)),
    Trade("sewer", "下水・排水", ("sewer",)),
    Trade("waste", "不用品回収・遺品整理", ("waste",)),
    Trade("septic", "浄化槽", ("septic",)),
    Trade("electrical", "電気工事", ("electrical",)),
    # 解体は、解体工事業登録か建設業許可（土木・建築・解体）のどちらかがあれば足りる
    Trade("demolition", "解体", ("demolition", "construction")),
    Trade("reform", "大きなリフォーム（500万円以上）", ("construction",)),
    # 公認制度がない業種
    Trade("locksmith", "鍵開け", ()),
    Trade("small_reform", "小規模リフォーム", ()),
    Trade("pest", "家庭の害虫駆除", ()),
    Trade("aircon_cleaning", "エアコンクリーニング", ()),
]}

# 管轄の対応表は scheme.trade 単位で持つ
SCHEME_TRADES = ("water", "sewer", "waste", "septic", "electrical", "demolition", "construction")


def get_trade(key_or_label: str) -> Trade:
    if key_or_label in TRADES:
        return TRADES[key_or_label]
    for t in TRADES.values():
        if key_or_label in t.label:
            return t
    raise KeyError(f"未知の業種: {key_or_label}（{', '.join(TRADES)}）")
