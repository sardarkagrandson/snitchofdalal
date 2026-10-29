# SnitchofDalal - Indian Stock Market Sentiment & Technical Intelligence

A FastAPI application for NSE / BSE equities that combines live market data,
technical indicators, a structured news-sentiment matrix, a statistical cutoff
probability calculator, an automated Spotlight watchlist with email alerts, and
a portfolio health dashboard. The UI follows a Groww-inspired fintech design
with a toggleable dark mode.

> Signals are labelled as *Bullish / Bearish Bias* and *Technical Setups*.
> Nothing here is investment advice. Equity investments are subject to market
> risk; consult a SEBI-registered advisor before trading.

## Markets

The app covers two markets behind one switch in the header:

| | India | South Africa |
|---|---|---|
| Exchanges | NSE / BSE (`.NS`, `.BO`) | JSE (`.JO`) |
| Hours | 09:15-15:30 IST | 09:00-17:00 SAST |
| Prices | NSE live quotes, Yahoo fallback | Yahoo Finance (JSE quotes arrive in cents and are converted to rand) |
| Indices | NIFTY 50, SENSEX, NIFTY Bank, NIFTY IT | All Share, Top 40, Resources 10, Financials 15 |
| News | Google News (en-IN) | Google News (en-ZA) |
| Modules | all, including IPO and mutual funds | stocks, charts, probability, Spotlight, portfolio |

The switch sets a cookie, so each user keeps their choice; `DEFAULT_MARKET` picks the
starting market for new users. Spotlight and portfolio entries can mix markets: each
symbol carries its market, alerts poll during that exchange's hours, and amounts show in
that currency. Adding a market is a new profile in `app/markets.py` plus a symbol map.

## Features

| Module | What it does |
|---|---|
| Ticker resolution | Accepts names or symbols (`Tata Motors`, `TATAMOTORS.NS`, `RELIANCE.BO`, `SOL.JO`). Bundled maps of well-known NSE and JSE names with aliases, merged daily with NSE's full equity and SME lists so new listings are searchable the day they list; fuzzy matching and Yahoo search fallback. Every search box offers type-ahead suggestions (`/api/suggest`). |
| Market data engine | Official NSE quote (via `nsemine`) during market hours with `yfinance` as fallback and for history; LTP, previous close, day range, 52-week range, volume. Session-aware status (pre-open, open, post-close, closed), NSE holiday list fetched daily, next-open countdown. In-memory TTL cache. |
| Device licensing | Optional. Ed25519-signed licence keys bound to a per-installation Device ID, verified offline. Unlicensed devices only see the activation screen. |
| Live price | An open stock page refreshes price, change, day range and volume in place every few seconds during market hours (`LIVE_QUOTE_SECONDS`), flashes green or red on each tick, and moves the chart's last point; intraday charts refetch. Polling pauses in background tabs and slows to once a minute when the market is closed. |
| Technicals | 14-day RSI (Wilder), 20/50/200 EMA, 20-day ATR (Wilder), annualised volatility, plus labelled setups / breakdowns. Computed natively with pandas. |
| News & sentiment matrix | Google News RSS + Yahoo Finance RSS for the past 7 days, deduplicated, cached in SQLite. Each article is classified (category, sentiment, impact -1..+1, reasoning) by an AI model (Anthropic API) via structured output (Pydantic), with a keyword heuristic fallback when no API key is set. Aggregated into a recency-weighted score and momentum bias. |
| Cutoff probability | `P(touch) = 2 * [1 - Phi(|ln(P_target/P_0)| / (sigma * sqrt(T)))]` using 1-year realised volatility, with the assumptions stated on screen. |
| Spotlight watchlist | APScheduler (SQLite job store) polls each ticker on its own interval during market hours. HTML email alerts (aiosmtplib) when a cutoff is hit or a news item scores beyond +/-0.7, with news context and a technical summary. |
| IPO intelligence | Upcoming & ongoing issues from NSE (price band, lot, size, fresh vs OFS, subscription) with grey-market premium, DRHP metrics, a structured strengths / risks / sentiment read (AI model, with a rules fallback) and a 14-day news feed; recently listed IPOs with listing-day and till-date returns. |
| Mutual funds | Every scheme from AMFI's daily NAV file, grouped into plain-language kinds (liquid, large cap, small cap, index, ELSS, hybrid, gold, ...). A one-time guide explains what each kind is for, its risk and holding period, then hides itself. Fund pages show 1M to 5Y returns (annualised beyond a year) and a NAV chart from mfapi.in history. |
| Portfolio health | Holdings with buy price / quantity / date. Net P&L, day change, value-weighted sentiment exposure, and flags for negative catalysts, RSI extremes and 200 EMA breaks. |

## Quick start

### Option A - Docker (recommended, no Python needed)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), download
this project, then double-click `start.bat` (Windows) or run `./start.sh`
(macOS / Linux). The app opens at http://localhost:8000 and keeps running in the
background. A step-by-step guide for non-technical users is in
[INSTALL.md](INSTALL.md).

Equivalent manual commands:

```bash
cp .env.example .env     # optional: add email / AI keys
docker compose up -d     # builds locally, or pulls ghcr.io/sardarkagrandson/snitchofdalal
```

Data (watchlist, portfolio, caches, saved alert emails) lives in the
`snitchofdalal-data` Docker volume, so it survives restarts and updates.

### Option B - Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit as needed
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

### Demo without network

```bash
OFFLINE_MODE=true uvicorn app.main:app --reload
```

Offline mode serves deterministic synthetic prices and sample headlines so every
screen works without Yahoo Finance or RSS access. The heuristic sentiment
classifier is used, and alert emails are written to `data/outbox/*.html`.

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `OFFLINE_MODE` | `true` for synthetic data (no network). |
| `ANTHROPIC_API_KEY` | Enables LLM sentiment classification. Model via `SENTIMENT_MODEL` (default `claude-opus-5-5`). Server-side refusal fallbacks are enabled by default. |
| `SMTP_HOST/PORT/USERNAME/PASSWORD/USE_TLS/FROM` | Email delivery. When `SMTP_HOST` is empty, alerts are saved to `data/outbox/`. |
| `MARKET_HOLIDAYS` | Comma-separated `YYYY-MM-DD` dates on which polling is skipped. |
| `QUOTE_CACHE_SECONDS`, `HISTORY_CACHE_SECONDS`, `NEWS_CACHE_SECONDS` | Cache TTLs (defaults 60 / 900 / 1800). |
| `DATA_DIR` | Location of `snitchofdalal.db` (app data) and `jobs.db` (scheduler). |

## IPO data sources and overrides

- Issue calendar and past listings come from NSE's public JSON endpoints through the
  `nsemine` session helper. They are undocumented and may change; failures degrade to
  an empty list rather than an error.
- Grey market premium is parsed from a public GMP page (`IPO_GMP_URL`, empty to disable).
  It is unofficial and shown as such.
- DRHP metrics (revenue growth, PAT margin, D/E, P/E vs industry, ROE, ROCE) have no
  public API, so they are supplied through `ipo_overrides.json` in the data folder
  (`docker cp` it into `snitchofdalal:/data/`), keyed by symbol or company name.
  The same file can correct GMP or lot size, and add listings NSE does not return:

```json
{
  "ABCD": {"gmp": 45, "lot_size": 150, "fresh_issue_cr": 500, "ofs_cr": 200,
           "fundamentals": {"revenue_growth_pct": 22, "pat_margin_pct": 11, "debt_equity": 0.3,
                            "pe": 28, "industry_pe": 24, "roe_pct": 18, "roce_pct": 21,
                            "notes": "Order book 2x revenue; top customer 30% of sales"}},
  "WXYZ": {"listed": true, "symbol": "WXYZ.NS", "company": "WXYZ Ltd", "issue_price": 210, "listing_date": "2026-10-07"}
}
```

- Lists are cached for an hour and assessments for 12 hours; "Refresh now" on the page
  forces a refetch. Offline mode shows three demo issues and three demo listings.

## Settings for all users versus one machine

Settings are read in two layers. `.env.example` is committed and distributed with the
app, so anything you put there (the shared email sender, `LICENSE_PUBLIC_KEY`, an AI
key) applies to every installation with no user action. `.env` is created per machine
by the start script, ignored by git, and overrides individual lines. Both files are
hidden dotfiles in the project root (`ls -a`). If you place credentials in
`.env.example`, keep the repository private and prefer a sending service whose key can
be rotated, such as Brevo or SendGrid, over a personal mailbox password.

## Restricting the app to approved devices

Licensing is off until you set `LICENSE_PUBLIC_KEY`. To turn it on:

```bash
python tools/license_admin.py keygen            # creates license_private.pem, prints the public key
# paste LICENSE_PUBLIC_KEY=... into .env.example (so every installation gets it), rebuild the image
```

No Python on your machine? The tool is inside the image. Keep the key in the data volume:

```bash
docker compose exec snitchofdalal python tools/license_admin.py keygen --out /data/license_private.pem
docker compose exec snitchofdalal python tools/license_admin.py issue --key /data/license_private.pem --device-id 7F3A-91C2-0B5E-44D1 --name "Ravi K" --days 365
docker compose exec snitchofdalal python tools/license_admin.py list --key /data/license_private.pem
```

Until `LICENSE_PUBLIC_KEY` is set the app runs in open access and the footer shows
"Device licensing: off" together with the installation's Device ID.

Each installation then shows a **Device ID** (for example `7F3A-91C2-0B5E-44D1`) on its
activation screen. The user sends it to you, and you issue a key:

```bash
python tools/license_admin.py issue --device-id 7F3A-91C2-0B5E-44D1 --name "Ravi K" --days 365
python tools/license_admin.py list              # every key you have issued (issued_licenses.csv)
```

The user pastes the key once; the app verifies the signature, the Device ID and the
expiry locally and stores it. Keys for other devices, tampered keys and expired keys are
rejected. Keep `license_private.pem` secret and backed up: without it you cannot issue
keys, and anyone holding it can.

What this does and does not do:

- It stops the app being copied and used on a device you have not approved, and lets you
  time-limit access (re-issue on renewal). Issue shorter terms if you expect to revoke.
- Keys cannot be revoked before expiry without an online check; the design keeps the app
  fully offline. An online revocation list can be added later if needed.
- The Device ID lives in the Docker data volume. Deleting the volume creates a new ID and
  needs a new key; copying the volume copies the activation.
- The source code runs on the user's machine, so a determined, technical user could edit
  it to remove the check. This protects against casual sharing, not against attackers.

By default the Docker port is bound to `127.0.0.1`, so only the computer running it can
open the app. Set `BIND_HOST=0.0.0.0` to allow other devices on the same network.

## JSON API

| Method & path | Description |
|---|---|
| `GET /api/suggest?q=` | Type-ahead suggestions (symbol, name, exchange). |
| `GET /api/resolve?q=` | Resolve a name/symbol to a ticker. |
| `GET/POST/DELETE /api/license` | Licence status, activation, deactivation (only when licensing is enabled). |
| `GET /api/stock/{query}` | Full analysis: quote, technicals, sentiment summary, news matrix, sparkline. |
| `POST /api/probability` | `{query, target_price, horizon_days}` -> touch probability and assumptions. |
| `GET/POST /api/spotlight`, `PATCH/DELETE /api/spotlight/{id}`, `POST /api/spotlight/{id}/poll` | Watchlist CRUD and on-demand poll. |
| `GET /api/alerts` | Alert delivery log. |
| `GET /api/mf/kinds`, `GET /api/mf/schemes?kind=&q=&plan=&option=`, `GET /api/mf/{code}`, `GET /api/mf/{code}/chart?range=` | Mutual fund kinds, scheme lists, fund detail with returns, NAV chart series. |
| `GET /api/ipo/upcoming`, `GET /api/ipo/listed` | IPO cards with assessment and news; recent listings with returns (`?force=true` refetches). |
| `GET/POST /api/portfolio`, `DELETE /api/portfolio/{id}` | Holdings and portfolio health. |
| `GET /api/health` | Service status, market state, scheduler state. |

## Project layout

```
app/
  main.py            FastAPI routes (HTML + JSON)
  config.py          pydantic-settings
  db.py, models.py   SQLAlchemy + SQLite
  services.py        analysis aggregator
  market/            tickers.py, data.py (providers, cache, market hours), indicators.py, probability.py
  news/              ingest.py (RSS, dedupe, cache), sentiment.py (LLM + heuristic), schemas.py
  alerts/            scheduler.py (APScheduler), email.py (aiosmtplib + HTML template)
  portfolio/         health.py
  templates/, static/   Jinja2 UI (Tailwind CDN, Inter / JetBrains Mono, dark mode)
tests/               pytest suite (runs fully offline)
```

## Tests

```bash
pytest -q
```

The suite uses offline data and a temporary SQLite database; no network or API
keys are required.

## Notes and limitations

- Price sources: with `QUOTE_SOURCE=auto` the app asks NSE's public quote endpoint first
  (official LTP, no 15-minute delay) and falls back to Yahoo Finance when NSE is
  unreachable, rate-limited or blocks the client's IP (NSE sometimes blocks non-Indian
  addresses). History and indicators always use Yahoo Finance daily bars. The NSE path
  is a public, undocumented endpoint and may change without notice.
- Outside 09:15-15:30 IST no exchange produces new prices, so the last close is shown
  and labelled; the status bar shows the next opening time. The official NSE holiday
  list is fetched once a day and merged with `MARKET_HOLIDAYS`.
- The `ta` library is listed as optional: the same RSI / EMA / ATR definitions are
  implemented in `app/market/indicators.py` so the app runs on Python 3.13 where
  `ta` currently fails to build.
- The stylesheet is compiled and shipped (`app/static/tailwind.css`), so the UI needs
  no CDN and works offline. After editing templates run `python tools/build_css.py`
  (`pip install pytailwindcss`) and commit the result. Only the Inter / JetBrains Mono
  web fonts come from Google Fonts, with system fallbacks.
- "Keyword news scoring" in the status bar means no `ANTHROPIC_API_KEY` is set, so
  headlines are scored with the built-in financial keyword dictionary. Add a key to
  switch to the AI model's structured classification; the badge changes to "AI news scoring".

## Acknowledgements

The code in this repository was written with the help of
[Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant, working
from the product specification and feedback provided by the project owner. Market data
comes from NSE and Yahoo Finance; news from Google News and Yahoo Finance RSS feeds.
Grey-market premiums are compiled from public IPO tracking pages and are unofficial.
