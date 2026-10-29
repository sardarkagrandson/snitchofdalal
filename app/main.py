"""FastAPI application: HTML UI + JSON API."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import licensing, services
from app.ipo import service as ipo_service
from app.mf import service as mf_service
from app.alerts import scheduler as sched
from app.config import get_settings
from app.db import get_db, init_db, session_scope
from app.market.data import chart_series, market_overview, market_status
from app.market.tickers import known_symbols, resolve_ticker, suggest
from app.markets import MARKETS, get_market, market_for_symbol
from app.models import AlertLog, Holding, SpotlightEntry
from app.portfolio.health import portfolio_health
from app.schemas import HoldingCreate, ProbabilityRequest, SpotlightCreate, SpotlightUpdate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["cur_for"] = lambda symbol: market_for_symbol(symbol).currency_symbol
templates.env.globals["market_of"] = lambda symbol: market_for_symbol(symbol)

MARKET_COOKIE = "sod-market"


def active_market(request: Request) -> str:
    """The market the user is browsing: cookie set by the header switch, else the configured default."""
    mid = (request.cookies.get(MARKET_COOKIE) or get_settings().default_market or "IN").upper()
    return mid if mid in MARKETS else "IN"


def _asset_version() -> str:
    """Short hash of the static files so browsers fetch fresh CSS/JS after an update."""
    import hashlib

    h = hashlib.sha1()
    for f in sorted((BASE_DIR / "static").glob("*")):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from app.market import universe

        universe.ensure_loaded()   # background fetch of NSE's full equity list
    except Exception as exc:
        log.debug("universe warm-up skipped: %s", exc)
    if os.environ.get("SNITCH_NO_SCHEDULER") != "1":
        try:
            sched.start_scheduler()
        except Exception as exc:  # scheduler is optional for the UI
            log.error("scheduler failed to start: %s", exc)
    yield
    sched.shutdown_scheduler()


app = FastAPI(title="SnitchofDalal", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_LICENSE_EXEMPT = ("/activate", "/static/", "/api/health", "/api/license", "/favicon.ico")


@app.middleware("http")
async def license_gate(request: Request, call_next):
    """Block every page and API call until this device holds a valid licence."""
    path = request.url.path
    if licensing.licensing_required() and not path.startswith(_LICENSE_EXEMPT):
        status = licensing.license_status()
        if not status["active"]:
            if path.startswith("/api/"):
                return JSONResponse(status_code=403, content={"detail": "Device not activated", "license": status})
            return RedirectResponse("/activate", status_code=303)
    return await call_next(request)


def ctx(request: Request, **kwargs) -> dict:
    s = get_settings()
    profile = get_market(active_market(request))
    return {
        "request": request,
        "app_name": s.app_name,
        "profile": profile,
        "markets": list(MARKETS.values()),
        "cur": profile.currency_symbol,
        "market": market_status(market=profile),
        "offline": s.offline_mode,
        "llm_enabled": s.llm_enabled,
        "smtp_enabled": s.smtp_enabled,
        "disclaimer": services.disclaimer_for(profile),
        "quote_source": "offline" if s.offline_mode else s.quote_source,
        "license": licensing.license_status() if licensing.licensing_required() else None,
        "device_id": licensing.get_or_create_device_id(),
        "asset_version": ASSET_VERSION,
        **kwargs,
    }


def _entry_or_404(db: Session, entry_id: int) -> SpotlightEntry:
    entry = db.get(SpotlightEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Spotlight entry not found")
    return entry


def _create_spotlight(db: Session, payload: SpotlightCreate, market: str = "IN") -> SpotlightEntry:
    t = resolve_ticker(payload.query, market=market)
    entry = SpotlightEntry(
        symbol=t.symbol, name=t.name, poll_interval_minutes=payload.poll_interval_minutes,
        cutoff_price=payload.cutoff_price, cutoff_direction=payload.cutoff_direction,
        email=str(payload.email), enabled=payload.enabled,
    )
    db.add(entry)
    db.flush()
    _safe_schedule(entry)
    return entry


def _safe_schedule(entry: SpotlightEntry) -> None:
    try:
        if sched.get_scheduler().running:
            sched.schedule_entry(entry)
    except Exception as exc:
        log.warning("could not schedule job for %s: %s", entry.symbol, exc)


def _create_holding(db: Session, payload: HoldingCreate, market: str = "IN") -> Holding:
    t = resolve_ticker(payload.query, market=market)
    h = Holding(symbol=t.symbol, name=t.name, buy_price=payload.buy_price,
                quantity=payload.quantity, buy_date=payload.buy_date)
    db.add(h)
    db.flush()
    return h


# ================================================================= activation
@app.get("/activate", response_class=HTMLResponse)
def activate_page(request: Request):
    status = licensing.license_status(force=True)
    if not licensing.licensing_required():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "activate.html", ctx(request, status=status, first_visit=True, token=None))


@app.post("/activate", response_class=HTMLResponse)
def activate_submit(request: Request, token: str = Form(...)):
    if not licensing.licensing_required():
        return RedirectResponse("/", status_code=303)
    try:
        licensing.activate(token)
    except licensing.LicenseError as exc:
        status = licensing.license_status(force=True)
        status["error"] = str(exc)
        return templates.TemplateResponse(
            request, "activate.html", ctx(request, status=status, first_visit=False, token=token), status_code=400
        )
    return RedirectResponse("/?msg=Device activated", status_code=303)


class LicenseActivate(BaseModel):
    token: str


@app.get("/api/license")
def api_license():
    return licensing.license_status(force=True)


@app.post("/api/license")
def api_license_activate(payload: LicenseActivate):
    if not licensing.licensing_required():
        return licensing.license_status()
    try:
        return licensing.activate(payload.token)
    except licensing.LicenseError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/license")
def api_license_deactivate():
    return licensing.deactivate()


# ================================================================= HTML pages
@app.get("/market/{market_id}")
def switch_market(request: Request, market_id: str, next: str = "/"):
    """Header switch: remember the chosen market in a cookie and go back."""
    mid = market_id.upper()
    if mid not in MARKETS:
        raise HTTPException(404, "Unknown market")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(MARKET_COOKIE, mid, max_age=365 * 24 * 3600, samesite="lax")
    return resp


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    mid = active_market(request)
    spotlight = db.query(SpotlightEntry).order_by(SpotlightEntry.created_at.desc()).limit(6).all()
    overview = market_overview(market=mid)
    return templates.TemplateResponse(
        request, "index.html",
        ctx(request, spotlight=spotlight, overview=overview, quick=known_symbols(mid)[:16]),
    )


@app.get("/stock", response_class=HTMLResponse)
def stock_redirect(q: str = ""):
    if not q.strip():
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/stock/{q.strip()}", status_code=303)


@app.get("/stock/{query}", response_class=HTMLResponse)
def stock_page(request: Request, query: str, target: float | None = None, horizon: int | None = None):
    mid = active_market(request)
    try:
        analysis = services.analyze(query, market=mid)
    except Exception as exc:
        log.warning("analysis failed for %r: %s", query, exc)
        return templates.TemplateResponse(
            request, "error.html", ctx(request, query=query, message=str(exc)), status_code=502
        )
    prob = None
    prob_error = None
    if target and horizon:
        try:
            _, _, prob = services.probability_for(analysis.ticker.symbol, target, horizon, market=mid)
        except ValueError as exc:
            prob_error = str(exc)
    return templates.TemplateResponse(
        request, "stock.html",
        ctx(request, a=analysis, matrix=analysis.matrix, prob=prob, prob_error=prob_error,
            target=target, horizon=horizon or 20, today=date.today()),
    )


@app.get("/spotlight", response_class=HTMLResponse)
def spotlight_page(request: Request, db: Session = Depends(get_db), msg: str | None = None):
    entries = db.query(SpotlightEntry).order_by(SpotlightEntry.created_at.desc()).all()
    logs = db.query(AlertLog).order_by(AlertLog.created_at.desc()).limit(25).all()
    return templates.TemplateResponse(
        request, "spotlight.html",
        ctx(request, entries=entries, logs=logs, next_runs=sched.next_run_times(), msg=msg),
    )


@app.post("/spotlight/add")
def spotlight_add_form(
    request: Request,
    query: str = Form(...), poll_interval_minutes: int = Form(60), cutoff_price: str = Form(""),
    cutoff_direction: str = Form("above"), email: str = Form(...), db: Session = Depends(get_db),
):
    try:
        payload = SpotlightCreate(
            query=query, poll_interval_minutes=poll_interval_minutes,
            cutoff_price=float(cutoff_price) if cutoff_price.strip() else None,
            cutoff_direction=cutoff_direction, email=email,
        )
        _create_spotlight(db, payload, active_market(request))
    except Exception as exc:
        return RedirectResponse(f"/spotlight?msg=Error: {exc}", status_code=303)
    return RedirectResponse("/spotlight?msg=Added to Spotlight", status_code=303)


@app.post("/spotlight/{entry_id}/toggle")
def spotlight_toggle(entry_id: int, db: Session = Depends(get_db)):
    entry = _entry_or_404(db, entry_id)
    entry.enabled = not entry.enabled
    db.flush()
    _safe_schedule(entry)
    return RedirectResponse("/spotlight", status_code=303)


@app.post("/spotlight/{entry_id}/delete")
def spotlight_delete(entry_id: int, db: Session = Depends(get_db)):
    entry = _entry_or_404(db, entry_id)
    db.delete(entry)
    sched.remove_job(entry_id)
    return RedirectResponse("/spotlight?msg=Removed", status_code=303)


@app.post("/spotlight/{entry_id}/poll")
async def spotlight_poll_now(entry_id: int):
    result = await sched.poll_spotlight(entry_id, force=True)
    return RedirectResponse(f"/spotlight?msg=Poll result: {result.get('status')}; alerts: {', '.join(result.get('alerts', [])) or 'none'}", status_code=303)


@app.post("/spotlight/test-email")
async def spotlight_test_email(email: str = Form(...)):
    from app.alerts.email import send_test_email

    delivered, detail = await send_test_email(email)
    msg = f"Test email delivered to {email}" if delivered else f"Error: {detail}"
    return RedirectResponse(f"/spotlight?msg={msg}", status_code=303)


@app.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request, db: Session = Depends(get_db), msg: str | None = None):
    holdings = db.query(Holding).order_by(Holding.buy_date.desc()).all()
    health = portfolio_health(holdings)
    return templates.TemplateResponse(request, "portfolio.html", ctx(request, health=health, msg=msg, today=date.today()))


@app.post("/portfolio/add")
def portfolio_add_form(
    request: Request,
    query: str = Form(...), buy_price: float = Form(...), quantity: float = Form(...),
    buy_date: date = Form(...), db: Session = Depends(get_db),
):
    try:
        _create_holding(db, HoldingCreate(query=query, buy_price=buy_price, quantity=quantity, buy_date=buy_date), active_market(request))
    except Exception as exc:
        return RedirectResponse(f"/portfolio?msg=Error: {exc}", status_code=303)
    return RedirectResponse("/portfolio?msg=Holding added", status_code=303)


@app.post("/portfolio/{holding_id}/delete")
def portfolio_delete(holding_id: int, db: Session = Depends(get_db)):
    h = db.get(Holding, holding_id)
    if h is None:
        raise HTTPException(404, "Holding not found")
    db.delete(h)
    return RedirectResponse("/portfolio?msg=Holding removed", status_code=303)


# ================================================================= IPO
@app.get("/ipo", response_class=HTMLResponse)
def ipo_page(request: Request):
    snap = ipo_service.ensure_snapshot()
    updated = snap["updated_at"]
    return templates.TemplateResponse(
        request, "ipo.html",
        ctx(request, cards=snap["cards"], listed=snap["listed"], loading=snap["loading"], load_error=snap["error"],
            fetched_label=updated.astimezone().strftime("%d %b %H:%M") if updated else "never",
            updated_iso=updated.isoformat() if updated else ""),
    )


@app.post("/ipo/refresh")
def ipo_refresh():
    ipo_service.ensure_snapshot(force=True)
    return RedirectResponse("/ipo", status_code=303)


@app.get("/api/ipo/status")
def api_ipo_status():
    return ipo_service.snapshot_status()


@app.get("/api/ipo/diagnostics")
def api_ipo_diagnostics():
    """Raw NSE rows from the last fetch and the parsed GMP table, for troubleshooting field names."""
    from app.ipo import sources as ipo_sources

    gmp = {}
    try:
        gmp = ipo_sources.fetch_gmp_table()
    except Exception as exc:
        gmp = {"error": str(exc)}
    nse_raw = ipo_sources.last_raw_rows()
    if not nse_raw:  # nothing fetched in this process yet: fetch a sample now so field names are visible
        try:
            ipo_sources.fetch_nse_upcoming()
            nse_raw = ipo_sources.last_raw_rows()
        except Exception as exc:
            nse_raw = {"error": str(exc)}
    return {"nse_raw": nse_raw, "gmp_table": gmp, "gmp_diagnostics": ipo_sources.gmp_diagnostics(),
            "snapshot": ipo_service.snapshot_status()}


@app.get("/api/ipo/upcoming")
def api_ipo_upcoming(force: bool = False):
    out = []
    for c in ipo_service.upcoming_cards(force):
        d = c.model_dump(mode="json")
        d["listing"]["est_listing_price"] = c.listing.est_listing_price
        d["listing"]["gmp_pct"] = c.listing.gmp_pct
        out.append(d)
    return out


@app.get("/api/ipo/listed")
def api_ipo_listed(force: bool = False):
    return [r.model_dump(mode="json") for r in ipo_service.recently_listed(force)]


# ================================================================= Mutual funds
@app.get("/mf", response_class=HTMLResponse)
def mf_page(request: Request, kind: str | None = None, q: str = "", plan: str = "direct", option: str = "growth"):
    from app.mf.taxonomy import KIND_BY_ID

    groups = mf_service.groups()
    if kind not in KIND_BY_ID:
        kind = None
    schemes = mf_service.list_schemes(kind, q, plan, option) if (kind or q.strip()) else []
    total = len(mf_service.universe())
    return templates.TemplateResponse(
        request, "mf.html",
        ctx(request, groups=groups, kind=KIND_BY_ID.get(kind).as_dict() if kind else None, kind_id=kind, schemes=schemes,
            q=q, plan=plan, option=option, total=total, today=date.today()),
    )


@app.get("/mf/{code}", response_class=HTMLResponse)
def mf_fund_page(request: Request, code: str):
    try:
        detail = mf_service.fund_detail(code)
    except Exception as exc:
        return templates.TemplateResponse(request, "error.html", ctx(request, query=code, message=str(exc)), status_code=404)
    return templates.TemplateResponse(request, "mf_fund.html", ctx(request, d=detail, today=date.today()))


@app.get("/api/mf/diagnostics")
def api_mf_diagnostics():
    """Fetch the scheme universe now and report what each source returned."""
    from app.mf import sources as mf_sources

    result: dict = {"universe_size": len(mf_service.universe())}
    try:
        n = len(mf_sources.fetch_amfi())
        result["amfi"] = {"ok": True, "schemes": n}
    except Exception as exc:
        result["amfi"] = {"ok": False, "error": str(exc)[:300]}
    result["attempts"] = mf_sources.diagnostics()
    return result


@app.get("/api/mf/kinds")
def api_mf_kinds():
    return mf_service.groups()


@app.get("/api/mf/schemes")
def api_mf_schemes(kind: str | None = None, q: str = "", plan: str = "direct", option: str = "growth", limit: int = 200):
    return [s.model_dump(mode="json") for s in mf_service.list_schemes(kind, q, plan, option, max(1, min(limit, 1000)))]


@app.get("/api/mf/{code}")
def api_mf_detail(code: str):
    try:
        return mf_service.fund_detail(code)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mf/{code}/chart")
def api_mf_chart(code: str, range: str = "1y", start: date | None = None, end: date | None = None):
    if range == "custom" and start is None:
        raise HTTPException(400, "start date is required for a custom range")
    try:
        return mf_service.chart_series(code, range, start, end)
    except Exception as exc:
        raise HTTPException(502, f"Chart data unavailable: {exc}")


# ================================================================= JSON API
@app.get("/api/health")
def api_health():
    s = get_settings()
    return {"status": "ok", "offline_mode": s.offline_mode, "llm": s.llm_enabled, "smtp": s.smtp_enabled,
            "quote_source": "offline" if s.offline_mode else s.quote_source,
            "license_required": licensing.licensing_required(),
            "markets": {mid: market_status(market=mid) for mid in MARKETS},
            "market": market_status(), "scheduler_running": sched.get_scheduler().running}


@app.get("/api/chart/{symbol}")
def api_chart(request: Request, symbol: str, range: str = "1y", start: date | None = None, end: date | None = None):
    from datetime import datetime as _dt, time as _time
    from app.market.calendar import IST as _IST

    if range == "custom":
        if start is None:
            raise HTTPException(400, "start date is required for a custom range")
        s_dt = _dt.combine(start, _time.min, tzinfo=_IST)
        e_dt = _dt.combine(end or date.today(), _time.max, tzinfo=_IST)
        if e_dt <= s_dt:
            raise HTTPException(400, "end must be after start")
    else:
        s_dt = e_dt = None
        if range not in ("1d", "1w", "1m", "6m", "1y", "all"):
            raise HTTPException(400, "unknown range")
    try:
        return chart_series(resolve_ticker(symbol, market=active_market(request)).symbol, range, s_dt, e_dt)
    except Exception as exc:
        raise HTTPException(502, f"Chart data unavailable: {exc}")


@app.get("/api/search/diagnostics")
def api_search_diagnostics(reload: bool = True):
    """State of the live NSE equity list behind search; reload=true refetches now."""
    from app.market import universe

    return universe.diagnostics(reload=reload and not get_settings().offline_mode)


@app.get("/api/suggest")
def api_suggest(request: Request, q: str = "", limit: int = 8, market: str | None = None):
    return suggest(q, limit=max(1, min(limit, 20)), market=market or active_market(request))


@app.get("/api/resolve")
def api_resolve(request: Request, q: str, market: str | None = None):
    try:
        return resolve_ticker(q, market=market or active_market(request)).as_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/stock/{query}")
def api_stock(request: Request, query: str, news: bool = True, market: str | None = None):
    try:
        return services.analyze(query, include_news=news, market=market or active_market(request)).as_dict()
    except Exception as exc:
        raise HTTPException(502, f"Unable to analyse '{query}': {exc}")


@app.post("/api/probability")
def api_probability(request: Request, payload: ProbabilityRequest):
    try:
        ticker, quote, prob = services.probability_for(payload.query, payload.target_price, payload.horizon_days, market=active_market(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return {"ticker": ticker.as_dict(), "quote": quote.as_dict(), "probability": prob.as_dict()}


def _iso_utc(dt) -> str | None:
    if dt is None:
        return None
    from datetime import timezone

    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _entry_dict(e: SpotlightEntry, next_runs: dict | None = None) -> dict:
    nr = (next_runs or {}).get(e.job_id)
    return {
        "id": e.id, "symbol": e.symbol, "name": e.name, "poll_interval_minutes": e.poll_interval_minutes,
        "cutoff_price": e.cutoff_price, "cutoff_direction": e.cutoff_direction, "email": e.email,
        "enabled": e.enabled, "last_polled_at": _iso_utc(e.last_polled_at),
        "last_price": e.last_price, "last_sentiment": e.last_sentiment, "last_error": e.last_error,
        "next_run_at": nr.isoformat() if nr else None,
    }


@app.get("/api/spotlight")
def api_spotlight_list(db: Session = Depends(get_db)):
    nr = sched.next_run_times()
    return [_entry_dict(e, nr) for e in db.query(SpotlightEntry).order_by(SpotlightEntry.id).all()]


@app.post("/api/spotlight", status_code=201)
def api_spotlight_create(request: Request, payload: SpotlightCreate, db: Session = Depends(get_db)):
    return _entry_dict(_create_spotlight(db, payload, active_market(request)))


@app.patch("/api/spotlight/{entry_id}")
def api_spotlight_update(entry_id: int, payload: SpotlightUpdate, db: Session = Depends(get_db)):
    entry = _entry_or_404(db, entry_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(entry, k, str(v) if k == "email" else v)
    db.flush()
    _safe_schedule(entry)
    return _entry_dict(entry)


@app.delete("/api/spotlight/{entry_id}", status_code=204)
def api_spotlight_delete(entry_id: int, db: Session = Depends(get_db)):
    db.delete(_entry_or_404(db, entry_id))
    sched.remove_job(entry_id)
    return JSONResponse(status_code=204, content=None)


@app.post("/api/spotlight/{entry_id}/poll")
async def api_spotlight_poll(entry_id: int, force: bool = True):
    with session_scope() as s:
        _entry_or_404(s, entry_id)
    return await sched.poll_spotlight(entry_id, force=force)


@app.get("/api/alerts")
def api_alerts(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(AlertLog).order_by(AlertLog.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "symbol": r.symbol, "kind": r.kind, "subject": r.subject, "recipient": r.recipient,
             "delivered": r.delivered, "detail": r.detail, "created_at": _iso_utc(r.created_at)} for r in rows]


@app.get("/api/portfolio")
def api_portfolio(db: Session = Depends(get_db)):
    return portfolio_health(db.query(Holding).order_by(Holding.buy_date.desc()).all()).as_dict()


@app.post("/api/portfolio", status_code=201)
def api_portfolio_add(request: Request, payload: HoldingCreate, db: Session = Depends(get_db)):
    h = _create_holding(db, payload, active_market(request))
    return {"id": h.id, "symbol": h.symbol, "name": h.name, "buy_price": h.buy_price,
            "quantity": h.quantity, "buy_date": h.buy_date.isoformat()}


@app.delete("/api/portfolio/{holding_id}", status_code=204)
def api_portfolio_delete(holding_id: int, db: Session = Depends(get_db)):
    h = db.get(Holding, holding_id)
    if h is None:
        raise HTTPException(404, "Holding not found")
    db.delete(h)
    return JSONResponse(status_code=204, content=None)
