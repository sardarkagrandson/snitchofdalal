"""Resolve company names / symbols to Yahoo Finance tickers (.NS / .BO)."""
from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

from app.markets import DEFAULT_MARKET, MARKETS, get_market, market_for_symbol

_SUFFIXES = tuple(sfx for m in MARKETS.values() for sfx in m.suffixes)


@dataclass
class ResolvedTicker:
    query: str
    symbol: str          # e.g. TATAMOTORS.NS
    base_symbol: str     # e.g. TATAMOTORS
    name: str
    exchange: str        # NSE | BSE | JSE
    confidence: float    # 0..1
    method: str          # exact-symbol | alias | fuzzy | suffix | search | fallback
    market: str = "IN"

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\b(ltd|limited|pvt|private|inc|corp|corporation|co|company|india)\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache
def _load_symbols(market_id: str = DEFAULT_MARKET) -> list[dict]:
    path = Path(__file__).with_name(get_market(market_id).symbols_file)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _all_rows(market_id: str = DEFAULT_MARKET) -> list[dict]:
    """Bundled names first (they carry aliases and rank as popular), then the live exchange list."""
    bundled = _load_symbols(market_id)
    if market_id != "IN":
        return list(bundled)
    from app.market import universe

    seen = {r["symbol"].upper() for r in bundled}
    extra = [r for r in universe.rows() if r["symbol"].upper() not in seen]
    return list(bundled) + extra


@lru_cache(maxsize=16)
def _index_cached(market_id: str, _version: int) -> tuple[dict[str, dict], dict[str, dict]]:
    by_symbol: dict[str, dict] = {}
    by_text: dict[str, dict] = {}
    for row in _all_rows(market_id):
        by_symbol.setdefault(row["symbol"].upper(), row)
        by_text.setdefault(_norm(row["name"]), row)
        for alias in row.get("aliases", []):
            by_text.setdefault(_norm(alias), row)
    return by_symbol, by_text


def _index(market_id: str = DEFAULT_MARKET) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_symbol, by_normalized_name_or_alias); rebuilt whenever the live list refreshes."""
    from app.market import universe

    return _index_cached(market_id, universe.version() if market_id == "IN" else 0)


def known_symbols(market_id: str = DEFAULT_MARKET) -> list[dict]:
    return list(_load_symbols(market_id))


def _exchange_for(symbol: str) -> str:
    u = symbol.upper()
    if u.endswith(".BO"):
        return "BSE"
    if u.endswith(".JO"):
        return "JSE"
    return "NSE"


def resolve_ticker(query: str, allow_search: bool = True, market: str = DEFAULT_MARKET) -> ResolvedTicker:
    """Resolve free-form text to a ticker. The active market is searched first, then the others.
    Never raises for non-empty input."""
    q = (query or "").strip()
    if not q:
        raise ValueError("Empty query")
    upper = q.upper()

    # 1. Already a Yahoo-style ticker with suffix: the suffix decides the market
    if upper.endswith(_SUFFIXES):
        m = market_for_symbol(upper)
        base = upper.rsplit(".", 1)[0]
        row = _index(m.id)[0].get(base)
        return ResolvedTicker(q, upper, base, row["name"] if row else base, _exchange_for(upper), 1.0, "exact-symbol", m.id)

    order = [get_market(market).id] + [mid for mid in MARKETS if mid != get_market(market).id]
    nq = _norm(q)

    # 2. Bare symbol, 3. exact name / alias, in market order
    for mid in order:
        by_symbol, by_text = _index(mid)
        sfx = get_market(mid).default_suffix
        if upper in by_symbol:
            row = by_symbol[upper]
            return ResolvedTicker(q, f"{upper}{sfx}", upper, row["name"], _exchange_for(sfx), 1.0, "exact-symbol", mid)
        if nq in by_text:
            row = by_text[nq]
            return ResolvedTicker(q, f"{row['symbol']}{sfx}", row["symbol"], row["name"], _exchange_for(sfx), 0.98, "alias", mid)

    # 4. Fuzzy name / alias match, in market order
    first = nq.split()[0] if nq else ""
    for mid in order:
        _, by_text = _index(mid)
        sfx = get_market(mid).default_suffix
        candidates = [t for t in by_text if t.startswith(first[:3])] if len(by_text) > 400 else list(by_text.keys())
        matches = difflib.get_close_matches(nq, candidates, n=1, cutoff=0.72)
        if not matches:
            for text, row in by_text.items():  # token containment, e.g. "tata motors share"
                if text and (text in nq or nq in text) and len(text) >= 4:
                    matches = [text]
                    break
        if matches:
            row = by_text[matches[0]]
            score = difflib.SequenceMatcher(None, nq, matches[0]).ratio()
            return ResolvedTicker(q, f"{row['symbol']}{sfx}", row["symbol"], row["name"], _exchange_for(sfx),
                                  round(max(score, 0.75), 2), "fuzzy", mid)

    # 5. Yahoo search (network) - best effort
    if allow_search:
        hit = _yahoo_search(q, market)
        if hit:
            return hit

    # 6. Fallback: treat as a symbol on the active market
    m = get_market(market)
    base = re.sub(r"[^A-Z0-9&-]", "", upper)
    return ResolvedTicker(q, f"{base}{m.default_suffix}", base, base, _exchange_for(m.default_suffix), 0.3, "fallback", m.id)


def _yahoo_search(query: str, market: str = DEFAULT_MARKET) -> ResolvedTicker | None:
    try:
        import yfinance as yf

        res = yf.Search(query, max_results=8, news_count=0)
        quotes = getattr(res, "quotes", None) or []
    except Exception as exc:  # network errors, API changes
        log.debug("yfinance search failed for %r: %s", query, exc)
        return None
    wanted = get_market(market).suffixes
    hits = [str(i.get("symbol", "")).upper() for i in quotes]
    for pref in (wanted, _SUFFIXES):
        for item, sym in zip(quotes, hits):
            if sym.endswith(pref):
                base = sym.rsplit(".", 1)[0]
                return ResolvedTicker(query, sym, base, item.get("longname") or item.get("shortname") or base,
                                      _exchange_for(sym), 0.85, "search", market_for_symbol(sym).id)
    return None


def suggest(query: str, limit: int = 8, market: str = DEFAULT_MARKET) -> list[dict]:
    """Rank the active market's bundled symbols for type-ahead: symbol prefix > name/alias prefix > substring > fuzzy."""
    q = (query or "").strip()
    if not q:
        return []
    m = get_market(market)
    nq = _norm(q)
    uq = q.upper()
    for sfx in _SUFFIXES:
        uq = uq.replace(sfx, "")
    scored: dict[str, tuple[float, dict]] = {}

    def consider(row: dict, score: float) -> None:
        cur = scored.get(row["symbol"])
        if cur is None or score > cur[0]:
            scored[row["symbol"]] = (score, row)

    for idx, row in enumerate(_all_rows(m.id)):
        sym = row["symbol"]
        texts = [row["name"], *row.get("aliases", [])]
        row = {**row, "_rank": idx}
        if sym == uq:
            consider(row, 100)
        elif sym.startswith(uq):
            consider(row, 90 - len(sym) * 0.1)
        for t in texts:
            nt = _norm(t)
            if not nt:
                continue
            if nt == nq:
                consider(row, 95)
            elif nt.startswith(nq):
                consider(row, 80 - len(nt) * 0.1)
            elif any(w.startswith(nq) for w in nt.split()):
                consider(row, 70)
            elif nq in nt:
                consider(row, 60)
            elif len(nq) >= 4 and nt[:2] == nq[:2] and abs(len(nt) - len(nq)) <= 12:
                r = difflib.SequenceMatcher(None, nq, nt).ratio()
                if r >= 0.6:
                    consider(row, 40 * r)
    # Tie-break by list order (roughly market-cap order), so TATAMOTORS beats TATACHEM for "tata".
    ranked = sorted(scored.values(), key=lambda x: (-x[0], x[1]["_rank"]))[:limit]
    return [
        {"symbol": f"{row['symbol']}{m.default_suffix}", "base_symbol": row["symbol"], "name": row["name"],
         "exchange": _exchange_for(m.default_suffix), "market": m.id}
        for _, row in ranked
    ]
