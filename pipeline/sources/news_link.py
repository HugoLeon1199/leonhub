"""Link the public LEON news digest to VN tickers without an AI classifier.

The source is another static JSON artifact, so this collector keeps the same
auditable contract as the rest of the warehouse: exact phrase rules, explicit
aliases and a ``matched_by`` value on every link. Missing a story is preferable
to attaching an unrelated story to a company.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Iterable

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient

log = logging.getLogger(__name__)

# The news repo's own Pages URL, not leonquant.com: that domain now serves the
# hub, so the digest is only at the repo address.
SOURCE_URL = "https://hugoleon1199.github.io/leonquant/content.json"

# Newsrooms use these brands far more often than the full exchange legal name.
# This deliberately covers the most-followed/liquid names rather than trying to
# invent aliases for the entire 1,751-stock universe.
ALIASES: dict[str, tuple[str, ...]] = {
    "ACB": ("ACB", "Ngân hàng Á Châu"), "ACV": ("ACV", "Cảng hàng không Việt Nam"),
    "ANV": ("Nam Việt",), "BAF": ("BAF Việt Nam",), "BCM": ("Becamex IDC", "Becamex"),
    "BID": ("BIDV",), "BMP": ("Nhựa Bình Minh",), "BSR": ("Lọc hóa dầu Bình Sơn", "BSR"),
    "BVH": ("Bảo Việt",), "CEO": ("CEO Group",), "CII": ("Hạ tầng TP HCM", "CII"),
    "CMG": ("Công nghệ CMC", "CMC Corporation"), "CTD": ("Coteccons",),
    "CTG": ("VietinBank",), "CTR": ("Viettel Construction",),
    "DBC": ("Dabaco",), "DCM": ("Đạm Cà Mau", "Phân bón Cà Mau"),
    "DGC": ("Hóa chất Đức Giang", "Đức Giang"), "DGW": ("Digiworld", "Thế Giới Số"),
    "DIG": ("DIC Corp", "DIC Group"), "DPM": ("Đạm Phú Mỹ", "Phân bón Phú Mỹ"),
    "DXG": ("Đất Xanh",), "EIB": ("Eximbank",), "FMC": ("Thực phẩm Sao Ta", "Sao Ta"),
    "FOX": ("FPT Telecom",), "FPT": ("Tập đoàn FPT",), "FRT": ("FPT Retail", "FPT Shop", "Long Châu"),
    "GAS": ("PV GAS", "Khí Việt Nam"), "GEE": ("GELEX Electric", "Điện lực GELEX"),
    "GEX": ("Tập đoàn GELEX", "GELEX Group"), "GMD": ("Gemadept",),
    "GVR": ("Cao su Việt Nam", "VRG"), "HAG": ("Hoàng Anh Gia Lai",),
    "HBC": ("Xây dựng Hòa Bình", "Hòa Bình Corp"), "HCM": ("Chứng khoán HSC", "HSC"),
    "HDB": ("HDBank",), "HDC": ("Phát triển nhà Bà Rịa Vũng Tàu", "Hodeco"),
    "HHV": ("Đèo Cả",), "HPG": ("Hòa Phát",), "HSG": ("Hoa Sen Group", "Tôn Hoa Sen"),
    "HVN": ("Vietnam Airlines",), "IDC": ("IDICO",), "KBC": ("Kinh Bắc",),
    "KDC": ("KIDO",), "KDH": ("Nhà Khang Điền", "Khang Điền"),
    "LPB": ("LPBank", "LienVietPostBank", "Lộc Phát Việt Nam"), "MBB": ("MB Bank", "MBBank"),
    "MBS": ("Chứng khoán MB", "MBS"), "MCH": ("Masan Consumer",),
    "MSB": ("MSB", "Maritime Bank"), "MSN": ("Masan Group", "Tập đoàn Masan"),
    "MWG": ("Thế Giới Di Động", "Điện Máy Xanh", "Bách Hóa Xanh"),
    "NAB": ("Nam A Bank",), "NKG": ("Thép Nam Kim", "Tôn Nam Kim"),
    "NLG": ("Nam Long Group", "Tập đoàn Nam Long"), "NTP": ("Nhựa Tiền Phong",),
    "NVL": ("Novaland",), "OCB": ("OCB", "Phương Đông Bank"),
    "ORS": ("Chứng khoán Tiên Phong", "TPS"), "PAN": ("The PAN Group", "Tập đoàn PAN"),
    "PC1": ("PC1 Group", "Tập đoàn PC1"), "PDR": ("Phát Đạt",),
    "PLX": ("Petrolimex",), "PNJ": ("Phú Nhuận Jewelry", "PNJ"),
    "POW": ("PV Power",), "PVD": ("PV Drilling",), "PVS": ("PTSC",),
    "QNS": ("Đường Quảng Ngãi", "Sữa đậu nành Vinasoy"), "REE": ("Cơ điện lạnh REE", "REE Corp"),
    "SAB": ("Sabeco", "Bia Sài Gòn"), "SBT": ("TTC AgriS", "Thành Thành Công Biên Hòa"),
    "SHB": ("SHB", "Ngân hàng Sài Gòn Hà Nội"), "SHS": ("Chứng khoán Sài Gòn Hà Nội", "SHS"),
    "SIP": ("Đầu tư Sài Gòn VRG",), "SSB": ("SeABank",),
    "SSI": ("Chứng khoán SSI", "SSI Securities"), "STB": ("Sacombank",),
    "SZC": ("Sonadezi Châu Đức",), "TCB": ("Techcombank",),
    "TCH": ("Hoàng Huy Group", "Tài chính Hoàng Huy"), "TLH": ("Thép Tiến Lên",),
    "TV2": ("Tư vấn Xây dựng điện 2", "PECC2"), "VCB": ("Vietcombank",),
    "VCG": ("Vinaconex",), "VCI": ("Chứng khoán Vietcap", "Vietcap Securities"),
    "VEA": ("VEAM",), "VGC": ("Viglacera",), "VHC": ("Vĩnh Hoàn",),
    "VHM": ("Vinhomes",), "VIB": ("VIB", "Ngân hàng Quốc Tế"),
    "VIC": ("Vingroup",), "VIX": ("Chứng khoán VIX",), "VJC": ("Vietjet Air", "Vietjet"),
    "VND": ("VNDirect",), "VNM": ("Vinamilk",), "VPI": ("Văn Phú Invest",),
    "VGI": ("Viettel Global",), "VPB": ("VPBank",), "VRE": ("Vincom Retail",), "VTP": ("Viettel Post",),
}

# These codes are ordinary words/acronyms in prose. They may still match a
# legal company name or an explicit alias above, never the bare code itself.
SYMBOL_BLOCKLIST = {
    "AAA", "API", "APP", "APS", "ART", "CAN", "CAP", "CEO", "COM", "DAD",
    "DAN", "DHA", "FIT", "GAS", "HAG", "HAX", "HOT", "ICT", "ITC", "KOS",
    "MBS", "NICE", "ONE", "POT", "SAM", "SEA", "SIP", "TIP", "TNT", "TOP",
}

LEGAL_WORDS = re.compile(
    r"\b(?:(?:cong ty\s+)?(?:trach nhiem huu han|thuong mai co phan|co phan)|"
    r"ctcp|tmcp|tnhh|cong ty me|cong ty con)\b"
)
AMBIGUOUS_COMPANY_PHRASES = {"phat trien do thi"}
# Venue/operator names are indistinguishable from the generic place in news
# prose ("các bến xe Hà Nội", "bến xe Miền Tây"). They need a reviewed alias
# or an explicit stock-code mention, not a legal-name substring.
AMBIGUOUS_NAME_SYMBOLS = {"HNB", "WCS"}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def company_phrase(name: str) -> str:
    phrase = re.sub(r"\s+", " ", LEGAL_WORDS.sub(" ", normalize(name))).strip()
    # Short legal remnants are usually ordinary prose: "Quốc tế" (VIB),
    # "Hàng hải" (MSB), "Phương Đông" (OCB), "Phú Tài" (PTB). Treating those
    # as company identity produced convincing-looking but unrelated stories.
    return phrase if (
        len(phrase) >= 10 and len(phrase.split()) >= 3
        and phrase not in AMBIGUOUS_COMPANY_PHRASES
    ) else ""


def contains_phrase(text: str, phrase: str) -> bool:
    if len(phrase) < 5:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _article_objects(payload: Any) -> list[dict[str, Any]]:
    """Collect and de-duplicate link objects from both current and old schemas.

    The current digest has a canonical ``allArticles`` array but richer copies
    elsewhere carry ``excerpt``. Walking the document lets the same URL inherit
    that text without coupling this repo to the newsroom presentation schema.
    """
    found: dict[str, dict[str, Any]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url, title = value.get("url"), value.get("title")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and title:
                item = found.setdefault(url, {"url": url})
                for key in ("title", "source", "published_at", "publishedAt", "summary", "excerpt"):
                    candidate = value.get(key)
                    if candidate and len(str(candidate)) > len(str(item.get(key) or "")):
                        item[key] = candidate
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return list(found.values())


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def article_text(article: dict[str, Any]) -> str:
    """Text that belongs to the story, excluding embedded recommendation lists."""
    detail = str(article.get("excerpt") or article.get("summary") or "")
    # Several publishers append "related articles" directly to the summary;
    # those headlines start after a stamped separator. Matching through it
    # attached the current traffic story to VIC merely because the next link
    # happened to mention Vingroup.
    detail = re.split(r"\s+-\s+\d{2}-\d{2}-\d{4}", detail, maxsplit=1)[0]
    return f"{article.get('title') or ''} {detail[:1600]}".strip()


def link_articles(
    articles: Iterable[dict[str, Any]], listings: list[tuple[str, str | None]], fetched_at: datetime
) -> list[dict[str, Any]]:
    companies = [(s, company_phrase(n or "")) for s, n in listings]
    aliases = {
        s: tuple(n for a in values if (n := normalize(a)) != normalize(s))
        for s, values in ALIASES.items()
    }
    rows: list[dict[str, Any]] = []

    for article in articles:
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or "").strip()
        body = article_text(article)
        norm = normalize(body)
        if not title or not url or not norm:
            continue

        matches: dict[str, str] = {}
        for symbol, phrase in companies:
            if symbol not in AMBIGUOUS_NAME_SYMBOLS and contains_phrase(norm, phrase):
                matches[symbol] = "name"

        for sym, names in aliases.items():
            if any(contains_phrase(norm, alias) for alias in names):
                matches.setdefault(sym, "alias")

        # A bare uppercase token is still unsafe (HCM is the city; USD is the
        # currency). Require explicit stock context rather than guessing from
        # parentheses or a colon, both common in unrelated headlines.
        for symbol, _ in companies:
            if symbol in matches or symbol in SYMBOL_BLOCKLIST or len(symbol) < 3:
                continue
            code = re.escape(symbol)
            if re.search(
                rf"(?:mã|cổ phiếu|cp)\s*[:\-]?\s*{code}\b",
                body, re.IGNORECASE,
            ):
                matches[symbol] = "symbol"

        # The match must be in the headline. Body text is what produces every
        # false positive seen in QA, and no alias list fixes them because the
        # body is genuinely correct prose: a rate round-up really does name
        # seven banks, an airport story really does name two airlines, and a
        # football story really does contain the letters HHV. None of those is
        # news about a ticker.
        #
        # A story about a company names it in the headline. So the body is used
        # only to find candidates cheaply, and the headline decides. This drops
        # real stories that mention the company only in passing, which is the
        # intended trade: a wrong ticker on a story is worse than a missing one.
        norm_title = normalize(title)
        titled = {
            sym for sym, reason in matches.items()
            if (reason == "name" and any(
                    contains_phrase(norm_title, phrase)
                    for s, phrase in companies if s == sym))
            or (reason == "alias" and any(
                    contains_phrase(norm_title, alias)
                    for alias in aliases.get(sym, ())))
            or (reason == "symbol" and re.search(
                    rf"\b{re.escape(sym)}\b", title, re.IGNORECASE))
        }
        if not titled:
            continue

        for sym in titled:
            rows.append({
                "symbol": sym, "url": url, "title": title,
                "source": article.get("source"),
                "published_at": _parse_date(article.get("published_at") or article.get("publishedAt")),
                "matched_by": matches[sym], "fetched_at": fetched_at,
            })
    return rows


def collect(dry_run: bool = False, qa_sample: int = 20) -> dict[str, Any]:
    started = wh.utcnow()
    payload = HttpClient(delay=0).get_json(SOURCE_URL)
    articles = _article_objects(payload)

    # Dry-run must not change even the database schema; a real run opens the
    # writer connection, which applies the new news_link DDL before appending.
    con = wh.connect_reader() if dry_run else wh.connect()
    try:
        listings = con.execute(
            """SELECT DISTINCT ON (symbol) symbol, organ_name FROM eq_listing
               ORDER BY symbol, fetched_at DESC"""
        ).fetchall()
        rows = link_articles(articles, listings, started)
        unique = {(r["symbol"], r["url"]): r for r in rows}
        rows = list(unique.values())
        if not articles:
            raise RuntimeError("Public news artifact contained no article links")
        if not rows:
            raise RuntimeError(
                "Ticker matcher produced zero links; refusing to preserve a stale snapshot"
            )

        stats: dict[str, Any] = {
            "articles": len(articles), "listings": len(listings), "links": len(rows),
            "symbols": len({r["symbol"] for r in rows}),
            "by_method": {m: sum(r["matched_by"] == m for r in rows) for m in ("name", "alias", "symbol")},
        }
        sample = random.Random(42).sample(rows, min(max(qa_sample, 0), len(rows)))
        stats["qa_sample"] = [
            {"s": r["symbol"], "m": r["matched_by"], "t": r["title"], "u": r["url"]}
            for r in sample
        ]
        if dry_run:
            return stats

        new_rows = wh.append(con, "news_link", rows)
        stats["rows_new"] = new_rows
        wh.log_run(con, uuid.uuid4().hex[:12], "news_link", started, "ok",
                   rows_in=len(articles), rows_new=new_rows, detail={k: v for k, v in stats.items() if k != "qa_sample"})
        return stats
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Link LEON news stories to VN tickers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--qa-sample", type=int, default=20)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(args.dry_run, args.qa_sample), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
