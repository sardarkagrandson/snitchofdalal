def test_pages_render(client):
    for path in ("/", "/stock/Tata%20Motors", "/stock/INFY.NS?target=99999&horizon=20", "/spotlight", "/portfolio"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert "Tata Motors" in client.get("/stock/Tata%20Motors").text
    assert client.get("/stock?q=").status_code == 200  # redirects home


def test_stock_api_shape(client):
    d = client.get("/api/stock/Reliance").json()
    assert d["ticker"]["symbol"] == "RELIANCE.NS"
    assert {"ltp", "previous_close", "day_high", "day_low", "week52_high", "week52_low", "volume", "is_stale"} <= set(d["quote"])
    assert {"rsi_14", "ema_20", "ema_50", "ema_200", "atr_20"} <= set(d["technicals"])
    assert d["sentiment"]["momentum_bias"] in {"Bullish Bias", "Bearish Bias", "Neutral"}
    assert all({"category", "sentiment", "impact_score", "reasoning"} <= set(r) for r in d["news_matrix"])
    assert "not investment advice" in d["disclaimer"]


def test_probability_api(client):
    r = client.post("/api/probability", json={"query": "TCS", "target_price": 10, "horizon_days": 20})
    assert r.status_code == 200
    p = r.json()["probability"]
    assert 0 <= p["touch_probability_pct"] <= 100 and len(p["assumptions"]) >= 3
    assert client.post("/api/probability", json={"query": "TCS", "target_price": -1, "horizon_days": 20}).status_code == 422


def test_spotlight_crud(client):
    r = client.post("/api/spotlight", json={"query": "Wipro", "poll_interval_minutes": 30, "cutoff_price": 500,
                                            "cutoff_direction": "below", "email": "me@example.com"})
    assert r.status_code == 201
    eid = r.json()["id"]
    assert r.json()["symbol"] == "WIPRO.NS"
    r = client.patch(f"/api/spotlight/{eid}", json={"enabled": False, "poll_interval_minutes": 15})
    assert r.json()["enabled"] is False and r.json()["poll_interval_minutes"] == 15
    assert client.post(f"/api/spotlight/{eid}/poll").json()["status"] == "disabled"
    assert any(e["id"] == eid for e in client.get("/api/spotlight").json())
    assert client.delete(f"/api/spotlight/{eid}").status_code == 204
    assert client.delete(f"/api/spotlight/{eid}").status_code == 404
    assert client.post("/api/spotlight", json={"query": "Wipro", "email": "not-an-email"}).status_code == 422


def test_portfolio_crud(client):
    r = client.post("/api/portfolio", json={"query": "HDFC Bank", "buy_price": 1500, "quantity": 4, "buy_date": "2025-02-01"})
    assert r.status_code == 201 and r.json()["symbol"] == "HDFCBANK.NS"
    hid = r.json()["id"]
    d = client.get("/api/portfolio").json()
    assert d["invested"] >= 6000 and any(h["id"] == hid for h in d["holdings"])
    assert client.delete(f"/api/portfolio/{hid}").status_code == 204
    assert client.post("/api/portfolio", json={"query": "HDFC Bank", "buy_price": 1, "quantity": 1, "buy_date": "2999-01-01"}).status_code == 422


def test_form_flows(client):
    r = client.post("/spotlight/add", data={"query": "ITC", "poll_interval_minutes": "60", "cutoff_price": "",
                                            "cutoff_direction": "above", "email": "x@y.co"}, follow_redirects=False)
    assert r.status_code == 303 and "Added" in r.headers["location"]
    r = client.post("/portfolio/add", data={"query": "ITC", "buy_price": "400", "quantity": "10", "buy_date": "2025-01-01"}, follow_redirects=False)
    assert r.status_code == 303 and "added" in r.headers["location"]


def test_home_overview_and_suggest_markup(client):
    html = client.get("/").text
    assert "NIFTY 50" in html and "Most watched" in html
    assert "Reliance Industries" in html          # company name, not the bare symbol
    assert html.count("data-suggest") >= 2 and "tailwind.css?v=" in html
    assert "cdn.tailwindcss.com" not in html
    assert "Keyword news scoring" in html and "Device licensing: off" in html


def test_stock_page_rounds_indicators(client):
    html = client.get("/stock/HDFC%20Bank").text
    import re
    rsi = re.search(r'RSI \(14\)</span><span class="num[^"]*">([0-9.]+)', html)
    assert rsi and len(rsi.group(1).split(".")[-1]) <= 1


def test_chart_api(client):
    for rng, intraday in (("1d", True), ("1w", True), ("1m", False), ("6m", False), ("1y", False), ("all", False)):
        d = client.get(f"/api/chart/TCS?range={rng}").json()
        assert d["symbol"] == "TCS.NS" and d["intraday"] is intraday and len(d["points"]) > 1, rng
        assert all(len(p) == 2 for p in d["points"]) and d["points"][0][0] < d["points"][-1][0]
        assert d["high"] >= d["low"] and d["change_pct"] is not None
    assert len(client.get("/api/chart/TCS?range=all").json()["points"]) <= 600
    d = client.get("/api/chart/Infosys?range=custom&start=2026-09-01&end=2026-09-30").json()
    assert d["range"] == "custom" and d["interval"] == "1d" and "Sep 2026" in d["label"]
    assert client.get("/api/chart/TCS?range=custom").status_code == 400
    assert client.get("/api/chart/TCS?range=2w").status_code == 400
    assert client.get("/api/chart/TCS?range=custom&start=2026-09-10&end=2026-09-01").status_code == 400
    assert 'id="price-chart"' in client.get("/stock/TCS").text


def test_test_email_saved_to_outbox(client):
    r = client.post("/spotlight/test-email", data={"email": "me@example.com"}, follow_redirects=False)
    assert r.status_code == 303 and "Error" in r.headers["location"] and "SMTP" in r.headers["location"]


def test_smtp_blocked_port_message(monkeypatch):
    import asyncio
    from app.alerts import email as em
    from app.config import get_settings

    st = get_settings()
    monkeypatch.setattr(st, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(st, "smtp_timeout_seconds", 0.2)

    async def no_route(host, port, timeout):
        raise em.SMTPStageError("TCP connection to smtp.example.test:587 timed out: this network or ISP is blocking port 587. Try SMTP_PORT=465")

    monkeypatch.setattr(em, "_open_ipv4_socket", no_route)
    ok, detail = asyncio.run(em.send_html_email("a@b.co", "s", "<p>x</p>"))
    assert not ok and "blocking port 587" in detail and "465" in detail


def test_live_quote_endpoint(client):
    d = client.get("/api/quote/TCS").json()
    assert d["symbol"] == "TCS.NS" and d["ltp"] > 0 and "next_poll_ms" in d and d["next_poll_ms"] >= 2000
    assert d["currency_symbol"] == "₹" and "server_time" in d
    assert client.get("/api/quote/Sasol").json()["currency"] == "ZAR"
    html = client.get("/stock/TCS").text
    assert 'id="live-quote"' in html and 'id="live-price"' in html and 'id="live-day-marker"' in html
