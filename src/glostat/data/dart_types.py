from __future__ import annotations

from dataclasses import dataclass, field

# v1.2 L2 — DART OpenAPI value types. Pure dataclasses; client + parsers import freely.


@dataclass(frozen=True, slots=True)
class CorpCodeEntry:
    corp_code: str         # 8-digit DART corp_code (zero-padded)
    corp_name: str
    stock_code: str        # 6-digit KRX code (may be empty for delisted)
    modify_date: str


@dataclass(frozen=True, slots=True)
class DartFinancialItem:
    account_id: str        # XBRL element id e.g. "ifrs-full_Revenue"
    account_name: str      # localized account name (Korean)
    fs_div: str            # CFS / OFS (consolidated / separate)
    sj_div: str            # BS / IS / CIS / CF / SCE
    thstrm_amount: str     # current period
    frmtrm_amount: str     # prior period
    bfefrmtrm_amount: str  # period before prior
    thstrm_nm: str         # current period label
    currency: str = "KRW"

    @property
    def thstrm_value(self) -> float | None:
        return _parse_number(self.thstrm_amount)

    @property
    def frmtrm_value(self) -> float | None:
        return _parse_number(self.frmtrm_amount)


@dataclass(frozen=True, slots=True)
class DartFinancialStatements:
    corp_code: str
    bsns_year: str         # YYYY
    reprt_code: str        # 11013 / 11012 / 11014 / 11011
    items: tuple[DartFinancialItem, ...] = field(default_factory=tuple)

    def find(self, account_id_hint: str) -> DartFinancialItem | None:
        for it in self.items:
            if account_id_hint.lower() in it.account_id.lower():
                return it
        return None

    def find_by_name(self, account_name_hint: str) -> DartFinancialItem | None:
        for it in self.items:
            if account_name_hint in it.account_name:
                return it
        return None


@dataclass(frozen=True, slots=True)
class DartCompanyOverview:
    corp_code: str
    corp_name: str
    corp_name_eng: str
    stock_code: str
    ceo_nm: str
    est_dt: str            # YYYYMMDD founding
    induty_code: str       # KRX industry code
    market: str            # KOSPI / KOSDAQ / KONEX / OTHER


@dataclass(frozen=True, slots=True)
class DartExecutiveTransaction:
    corp_code: str
    repror: str            # reporter (임원 이름)
    isu_exctv_rgist_at: str
    isu_exctv_ofcps: str   # title (CEO, director, etc.)
    isu_main_shrholdr: str
    sp_stock_lmp_cnt: str  # share count change
    sp_stock_lmp_irds_cnt: str
    sp_stock_lmp_irds_rate: str
    bsis_dt: str           # transaction date YYYYMMDD
    rcept_dt: str          # filing date YYYYMMDD
    trd_kind: str          # transaction kind
    is_buy: bool = False
    is_sell: bool = False


def _parse_number(value: str | None) -> float | None:
    if value is None or value in {"-", "", "nan"}:
        return None
    s = value.strip().replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


__all__ = [
    "CorpCodeEntry",
    "DartCompanyOverview",
    "DartExecutiveTransaction",
    "DartFinancialItem",
    "DartFinancialStatements",
    "_parse_number",
]
