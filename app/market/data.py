"""Market data engine: quotes, history, market-hours awareness and caching.

Two providers are available:
  * YFinanceProvider  - live data via yfinance (.NS / .BO tickers)
  * OfflineProvider   - deterministic synthetic data (demo / tests / no network)
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime

import numpy as np
import pandas as pd

from app.config import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- market hours
from app.market.calendar import IST, is_market_open, market_status, now_ist  # noqa: E402,F401
from app.markets import DEFAULT_MARKET, MarketProfile, get_market, market_for_symbol  # noqa: E402


def _scale(df: pd.DataFrame, divisor: float) -> pd.DataFrame:
    """Yahoo quotes JSE equities in cents; convert OHLC to rand (indices are already in points)."""
    if divisor == 1.0:
        return df
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out:
            out[col] = out[col] / divisor
    return out


def _divisor_for(symbol: str) -> float:
    if symbol.startswith("^"):
        return 1.0
    return market_for_symbol(symbol).price_divisor


# ---------------------------------------------------------------- cache
class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float):
        with self._lock:
            hit = self._data.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        return None

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = TTLCache()


# ---------------------------------------------------------------- quote model
@dataclass
class Quote:
    symbol: str
    name: str
    currency: str
    ltp: float
    previous_close: float
    day_high: float | None
    day_low: float | None
    week52_high: float | None
    week52_low: float | None
    volume: float | None
    open: float | None
    change: float
    change_pct: float
    as_of: str                 # ISO timestamp of last price
    is_stale: bool             # True when the price is a previous-session close
    market_open: bool
    source: str                # yfinance | nse | offline
    market: str = "IN"
    currency_symbol: str = "\u20b9"

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- providers
class YFinanceProvider:
    name = "yfinance"

    def history(self, symbol: str, period: str = "1y", interval: str = "1d",
                start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        import yfinance as yf

        if start is not None:
            df = yf.Ticker(symbol).history(start=start, end=end, interval=interval, auto_adjust=False)
        else:
            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            raise LookupError(f"No price history returned for {symbol}")
        df = df.rename(columns=str.title)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        return _scale(df, _divisor_for(symbol))

    def quote(self, symbol: str, history: pd.DataFrame) -> Quote:
        import yfinance as yf

        t = yf.Ticker(symbol)
        info: dict = {}
        try:
            fi = t.fast_info
            info = {
                "last_price": fi.last_price,
                "previous_close": fi.previous_close,
                "day_high": fi.day_high,
                "day_low": fi.day_low,
                "year_high": fi.year_high,
                "year_low": fi.year_low,
                "last_volume": fi.last_volume,
                "open": fi.open,
                "currency": fi.currency,
            }
        except Exception as exc:  # fast_info can fail for illiquid tickers
            log.debug("fast_info failed for %s: %s", symbol, exc)
        div = _divisor_for(symbol)
        if div != 1.0:
            for k in ("last_price", "previous_close", "day_high", "day_low", "year_high", "year_low", "open"):
                if info.get(k) is not None:
                    try:
                        info[k] = float(info[k]) / div
                    except (TypeError, ValueError):
                        info[k] = None
        from app.market.tickers import resolve_ticker

        resolved = resolve_ticker(symbol, allow_search=False)
        name = resolved.name
        if resolved.method == "fallback":  # unknown symbol: ask Yahoo for the long name (slower)
            try:
                name = t.info.get("longName") or t.info.get("shortName") or symbol
            except Exception:
                pass
        return build_quote(symbol, name, history, info, source=self.name)


class NSELiveProvider(YFinanceProvider):
    """Official NSE quote (via nsemine) layered on Yahoo history; falls back to Yahoo."""

    name = "nse"

    def quote(self, symbol: str, history: pd.DataFrame) -> Quote:
        base = symbol.rsplit(".", 1)[0]
        if not symbol.upper().endswith(".NS"):
            return super().quote(symbol, history)
        info = None
        try:
            from nsemine.live import get_stock_live_quotes

            raw = get_stock_live_quotes(base)
            if raw and raw.get("close"):
                ts = raw.get("datetime")
                info = {
                    "last_price": raw.get("close"),
                    "previous_close": raw.get("previous_close"),
                    "day_high": raw.get("high"),
                    "day_low": raw.get("low"),
                    "year_high": raw.get("year_high"),
                    "year_low": raw.get("year_low"),
                    "last_volume": raw.get("volume"),
                    "open": raw.get("open"),
                    "currency": "INR",
                    "last_update": ts.replace(tzinfo=IST).isoformat() if isinstance(ts, datetime) else None,
                    "name": raw.get("name"),
                }
        except Exception as exc:  # blocked IP, NSE outage, library change
            log.debug("NSE live quote failed for %s: %s", symbol, exc)
        if info is None:
            return super().quote(symbol, history)
        from app.market.tickers import resolve_ticker

        resolved = resolve_ticker(symbol, allow_search=False)
        name = resolved.name if resolved.method != "fallback" else (info.get("name") or symbol)
        return build_quote(symbol, name, history, info, source=self.name)


class OfflineProvider:
    """Deterministic synthetic GBM price paths seeded from the symbol."""

    name = "offline"

    def _seed(self, symbol: str) -> int:
        return int(hashlib.sha1(symbol.encode()).hexdigest()[:8], 16)

    def history(self, symbol: str, period: str = "1y", interval: str = "1d",
                start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        if interval != "1d":
            return self._intraday(symbol, period, interval)
        if start is not None:
            days = max(2, int(np.busday_count(start.date(), (end or now_ist()).date())) + 1)
        else:
            days = {"5d": 5, "1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "max": 2520}.get(period, 252)
        rng = np.random.default_rng(self._seed(symbol))
        base = 100 + (self._seed(symbol) % 3000)
        drift, vol = 0.08 / 252, (0.18 + (self._seed(symbol) % 20) / 100) / np.sqrt(252)
        rets = rng.normal(drift, vol, days)
        close = base * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, vol / 2, days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, days)))
        volume = rng.integers(500_000, 5_000_000, days).astype(float)
        idx = pd.bdate_range(end=(end or now_ist()).date(), periods=days)
        return pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx
        )

    def _intraday(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        step = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60}.get(interval, 5)
        sessions = {"1d": 1, "5d": 5, "1mo": 22}.get(period, 1)
        daily = self.history(symbol, "1mo")
        rng = np.random.default_rng(self._seed(symbol) + step)
        frames = []
        for day_ts, row in daily.tail(sessions).iterrows():
            n = int(375 / step)
            path = np.linspace(row["Open"], row["Close"], n) + rng.normal(0, row["Close"] * 0.0015, n).cumsum() * 0.3
            times = pd.date_range(datetime.combine(day_ts.date(), datetime.min.time()).replace(hour=9, minute=15, tzinfo=IST), periods=n, freq=f"{step}min")
            frames.append(pd.DataFrame({"Open": path, "High": path * 1.001, "Low": path * 0.999, "Close": path, "Volume": 10_000.0}, index=times))
        return pd.concat(frames)

    def quote(self, symbol: str, history: pd.DataFrame) -> Quote:
        from app.market.tickers import resolve_ticker

        name = resolve_ticker(symbol, allow_search=False).name
        return build_quote(symbol, name, history, {}, source=self.name)


def build_quote(symbol: str, name: str, history: pd.DataFrame, info: dict, source: str) -> Quote:
    last = history.iloc[-1]
    prev = history.iloc[-2] if len(history) > 1 else last
    profile = market_for_symbol(symbol)
    open_now = is_market_open(market=profile)

    ltp = _f(info.get("last_price")) or float(last["Close"])
    prev_close = _f(info.get("previous_close")) or float(prev["Close"])
    last_ts = history.index[-1]
    if isinstance(last_ts, pd.Timestamp):
        last_ts = last_ts.tz_localize(profile.zone) if last_ts.tzinfo is None else last_ts.astimezone(profile.zone)
        as_of = last_ts.isoformat()
        last_date = last_ts.date()
    else:
        as_of, last_date = str(last_ts), None
    today = datetime.now(profile.zone).date()
    if info.get("last_update"):
        as_of = info["last_update"]
    stale = (not open_now) or (last_date is not None and last_date < today and info.get("last_price") is None)

    change = ltp - prev_close
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0
    year = history.tail(252)
    return Quote(
        symbol=symbol,
        name=name,
        currency=profile.currency,
        ltp=round(ltp, 2),
        previous_close=round(prev_close, 2),
        day_high=_f(info.get("day_high")) or round(float(last["High"]), 2),
        day_low=_f(info.get("day_low")) or round(float(last["Low"]), 2),
        week52_high=_f(info.get("year_high")) or round(float(year["High"].max()), 2),
        week52_low=_f(info.get("year_low")) or round(float(year["Low"].min()), 2),
        volume=_f(info.get("last_volume")) or float(last["Volume"]),
        open=_f(info.get("open")) or round(float(last["Open"]), 2),
        change=round(change, 2),
        change_pct=round(change_pct, 2),
        as_of=as_of,
        is_stale=bool(stale),
        market_open=open_now,
        source=source,
        market=profile.id,
        currency_symbol=profile.currency_symbol,
    )


def _f(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if np.isfinite(f) and f != 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- engine
class MarketDataEngine:
    def __init__(self, provider=None) -> None:
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            settings = get_settings()
            if settings.offline_mode:
                self._provider = OfflineProvider()
            elif settings.quote_source.lower() == "yfinance":
                self._provider = YFinanceProvider()
            else:
                self._provider = NSELiveProvider()   # falls back to Yahoo for non-NSE symbols
        return self._provider

    def history(self, symbol: str, period: str = "1y", interval: str = "1d",
                start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        key = f"hist:{self.provider.name}:{symbol}:{period}:{interval}:{start.date() if start else ''}:{end.date() if end else ''}"
        settings = get_settings()
        ttl = settings.history_cache_seconds if interval == "1d" else (
            settings.live_quote_seconds if is_market_open(market=market_for_symbol(symbol)) else settings.quote_cache_seconds)
        cached = _cache.get(key, ttl)
        if cached is not None:
            return cached
        df = self.provider.history(symbol, period, interval, start, end)
        _cache.set(key, df)
        return df

    def quote(self, symbol: str, max_age: float | None = None) -> Quote:
        key = f"quote:{self.provider.name}:{symbol}"
        settings = get_settings()
        open_now = is_market_open(market=market_for_symbol(symbol))
        ttl = settings.quote_cache_seconds if open_now else settings.history_cache_seconds
        if max_age is not None and open_now:
            ttl = min(ttl, max_age)
        cached = _cache.get(key, ttl)
        if cached is not None:
            return cached
        q = self.provider.quote(symbol, self.history(symbol))
        _cache.set(key, q)
        return q

    def clear_cache(self) -> None:
        _cache.clear()


_engine: MarketDataEngine | None = None


def get_engine() -> MarketDataEngine:
    global _engine
    if _engine is None:
        _engine = MarketDataEngine()
    return _engine


def set_engine(engine: MarketDataEngine | None) -> None:
    global _engine
    _engine = engine
    _cache.clear()


def sparkline_points(history: pd.DataFrame, n: int = 60) -> list[float]:
    return [round(float(v), 2) for v in history["Close"].tail(n).tolist()]


# ---------------------------------------------------------------- market overview (home page)
def _quote_or_none(symbol: str, name: str | None = None) -> Quote | None:
    try:
        engine = get_engine()
        hist = engine.history(symbol, "6mo")
        key = f"quote:{engine.provider.name}:{symbol}"
        ttl = get_settings().quote_cache_seconds if is_market_open(market=market_for_symbol(symbol)) else get_settings().history_cache_seconds
        q = _cache.get(key, ttl)
        if q is None:
            if isinstance(engine.provider, OfflineProvider):
                from app.market.tickers import resolve_ticker

                q = build_quote(symbol, name or resolve_ticker(symbol, allow_search=False).name, hist, {}, source="offline")
            else:
                q = engine.provider.quote(symbol, hist)
                if name:
                    q.name = name
            _cache.set(key, q)
        q.sparkline = sparkline_points(hist, 30)  # type: ignore[attr-defined]
        return q
    except Exception as exc:
        log.debug("overview quote failed for %s: %s", symbol, exc)
        return None


def market_overview(timeout: float = 8.0, market=DEFAULT_MARKET) -> dict:
    """Index strip + popular stock cards for the home page; fetched concurrently, best effort."""
    from concurrent.futures import ThreadPoolExecutor, wait

    p = get_market(market) if not isinstance(market, MarketProfile) else market
    with ThreadPoolExecutor(max_workers=8) as pool:
        idx_f = {pool.submit(_quote_or_none, sym, name): (sym, name) for sym, name in p.indices}
        pop_f = {pool.submit(_quote_or_none, sym): sym for sym in p.popular}
        wait(list(idx_f) + list(pop_f), timeout=timeout)
    indices = [f.result() for f in idx_f if f.done() and f.result() is not None]
    popular = [f.result() for f in pop_f if f.done() and f.result() is not None]
    return {"indices": indices, "popular": popular}


# ---------------------------------------------------------------- chart series
CHART_RANGES = {
    # range: (period, interval)
    "1d": ("1d", "5m"),
    "1w": ("5d", "30m"),
    "1m": ("1mo", "1d"),
    "6m": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "all": ("max", "1d"),
}
MAX_POINTS = 600


def chart_series(symbol: str, rng: str = "1y", start: datetime | None = None, end: datetime | None = None) -> dict:
    """Closing-price series for the chart. rng is one of CHART_RANGES or 'custom' with start/end."""
    engine = get_engine()
    if rng == "custom" and start is not None:
        end = end or now_ist()
        span_days = (end - start).days
        interval = "30m" if span_days <= 7 else "1d"
        df = engine.history(symbol, "custom", interval, start, end)
        label = f"{start:%d %b %Y} to {end:%d %b %Y}"
    else:
        period, interval = CHART_RANGES.get(rng, CHART_RANGES["1y"])
        df = engine.history(symbol, period, interval)
        label = rng
    df = df.dropna(subset=["Close"])
    if len(df) > MAX_POINTS:  # thin evenly so the browser draws at most MAX_POINTS
        step = int(np.ceil(len(df) / MAX_POINTS))
        df = pd.concat([df.iloc[::step], df.iloc[[-1]]]).loc[~pd.concat([df.iloc[::step], df.iloc[[-1]]]).index.duplicated()]
    points = []
    zone = market_for_symbol(symbol).zone
    for ts, row in df.iterrows():
        t = ts if isinstance(ts, pd.Timestamp) else pd.Timestamp(ts)
        t = t.tz_localize(zone) if t.tzinfo is None else t.tz_convert(zone)
        points.append([int(t.timestamp() * 1000), round(float(row["Close"]), 2)])
    first, last = (points[0][1], points[-1][1]) if points else (None, None)
    return {
        "symbol": symbol, "range": rng, "interval": interval, "label": label, "points": points,
        "first": first, "last": last,
        "change": round(last - first, 2) if points else None,
        "change_pct": round((last / first - 1) * 100, 2) if points and first else None,
        "high": max(p[1] for p in points) if points else None,
        "low": min(p[1] for p in points) if points else None,
        "intraday": interval != "1d",
    }
