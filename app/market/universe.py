"""Full NSE equity universe (mainboard + SME) from NSE's daily CSVs, merged into search and resolution.

The bundled symbol map covers well-known names with aliases; this adds every other listed
company, including new listings, refreshed once a day in a background thread so page loads
never wait on it."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import get_settings

log = logging.getLogger(__name__)

CACHE_KEY = "mkt:nse-equities:v1"
CACHE_TTL = timedelta(hours=24)
_state: dict = {"rows": [], "version": 0, "loaded_at": 0.0, "loading": False}
_lock = threading.Lock()


def _title(name: str) -> str:
    out = name.strip().title()
    for a, b in (("Limited", "Limited"), (" Ltd.", " Ltd"), ("'S", "'s"), (" Of ", " of "), (" And ", " and "), (" & ", " & ")):
        out = out.replace(a, b)
    return re.sub(r"\s+", " ", out)


def _fetch_csv_rows() -> list[dict]:
    from nsemine.nse import get_all_equities_list, get_all_sme_stocks_list

    rows: list[dict] = []
    for fn, series in ((get_all_equities_list, "EQ"), (get_all_sme_stocks_list, "SME")):
        try:
            df = fn()
        except Exception as exc:
            log.warning("NSE %s list failed: %s", series, exc)
            df = None
        if df is None or df.empty:
            continue
        cols = {c.strip().lower(): c for c in df.columns}
        sym_c = cols.get("symbol")
        name_c = cols.get("name") or cols.get("name of company")
        date_c = cols.get("date_of_listing") or cols.get("date of listing")
        if not sym_c or not name_c:
            continue
        for _, r in df.iterrows():
            sym = str(r[sym_c]).strip().upper()
            name = str(r[name_c]).strip()
            if not sym or not name or sym == "NAN":
                continue
            listed = r[date_c] if date_c else None
            rows.append({"symbol": sym, "name": _title(name), "aliases": [], "series": series,
                         "listed": listed.strftime("%Y-%m-%d") if hasattr(listed, "strftime") else None})
    return rows


def _cache_get():
    from app.db import session_scope
    from app.models import IPOCache

    try:
        with session_scope() as s:
            row = s.get(IPOCache, CACHE_KEY)
            if row is None:
                return None
            fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched > CACHE_TTL:
                return None
            return json.loads(row.payload)
    except Exception:
        return None


def _cache_set(rows: list[dict]) -> None:
    from app.db import session_scope
    from app.models import IPOCache

    try:
        with session_scope() as s:
            row = s.get(IPOCache, CACHE_KEY)
            data = json.dumps(rows)
            if row is None:
                s.add(IPOCache(key=CACHE_KEY, payload=data, fetched_at=datetime.now(timezone.utc)))
            else:
                row.payload, row.fetched_at = data, datetime.now(timezone.utc)
    except Exception as exc:
        log.debug("universe cache write failed: %s", exc)


def _load(force: bool = False) -> None:
    try:
        rows = None if force else _cache_get()
        if rows is None:
            rows = _fetch_csv_rows()
            if rows:
                _cache_set(rows)
        with _lock:
            if rows:
                _state["rows"] = rows
                _state["version"] += 1
            _state["loaded_at"] = time.monotonic()
    except Exception as exc:
        log.warning("NSE universe load failed: %s", exc)
    finally:
        with _lock:
            _state["loading"] = False


def ensure_loaded(block: bool = False) -> None:
    """Kick off a refresh if the in-memory list is older than an hour (background unless block=True)."""
    if get_settings().offline_mode:
        return
    with _lock:
        fresh = _state["loaded_at"] and time.monotonic() - _state["loaded_at"] < 3600
        if fresh or _state["loading"]:
            return
        _state["loading"] = True
    if block:
        _load()
    else:
        threading.Thread(target=_load, daemon=True, name="nse-universe").start()


def rows() -> list[dict]:
    ensure_loaded()
    with _lock:
        return list(_state["rows"])


def version() -> int:
    with _lock:
        return _state["version"]


def set_rows_for_tests(items: list[dict]) -> None:
    with _lock:
        _state["rows"] = list(items)
        _state["version"] += 1
        _state["loaded_at"] = time.monotonic()
