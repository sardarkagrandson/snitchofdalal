import pytest

from app.market.tickers import resolve_ticker


@pytest.mark.parametrize("query,symbol,method", [
    ("Tata Motors", "TATAMOTORS.NS", "alias"),
    ("TATAMOTORS.NS", "TATAMOTORS.NS", "exact-symbol"),
    ("tatamotors", "TATAMOTORS.NS", "exact-symbol"),
    ("RELIANCE.BO", "RELIANCE.BO", "exact-symbol"),
    ("HDFC Bank Ltd", "HDFCBANK.NS", "alias"),
    ("L&T", "LT.NS", "alias"),
    ("maruti suzuki share", "MARUTI.NS", "fuzzy"),
    ("infosys limited", "INFY.NS", "alias"),
])
def test_resolution(query, symbol, method):
    r = resolve_ticker(query, allow_search=False)
    assert r.symbol == symbol
    assert r.method == method
    assert r.confidence >= 0.75


def test_fallback_and_exchange():
    r = resolve_ticker("ZZZUNKNOWN", allow_search=False)
    assert r.symbol == "ZZZUNKNOWN.NS" and r.method == "fallback" and r.confidence < 0.5
    assert resolve_ticker("TCS.BO", allow_search=False).exchange == "BSE"
    with pytest.raises(ValueError):
        resolve_ticker("   ")


def test_live_universe_merges_into_search(monkeypatch):
    from app.market import universe
    from app.market.tickers import suggest

    universe.set_rows_for_tests([
        {"symbol": "TONBO", "name": "Tonbo Imaging India Limited", "aliases": [], "series": "EQ", "listed": "2026-10-22"},
        {"symbol": "ZETWERK", "name": "Zetwerk Manufacturing Businesses Limited", "aliases": [], "series": "EQ", "listed": "2026-10-22"},
    ])
    try:
        r = resolve_ticker("Tonbo Imaging", allow_search=False)
        assert r.symbol == "TONBO.NS" and r.method == "alias" and r.name.startswith("Tonbo")
        assert resolve_ticker("zetwerk", allow_search=False).symbol == "ZETWERK.NS"
        assert suggest("ton")[0]["base_symbol"] == "TONBO"
        # bundled names still win ties and keep their aliases
        assert resolve_ticker("Tata Motors", allow_search=False).symbol == "TATAMOTORS.NS"
    finally:
        universe.set_rows_for_tests([])


def test_universe_csv_parsing(monkeypatch):
    import pandas as pd
    from app.market import universe

    eq = pd.DataFrame({"symbol": ["NEWCO"], "name": ["NEW COMPANY LIMITED"], "series": ["EQ"],
                       "date_of_listing": [pd.Timestamp("2026-10-28")]})
    sme = pd.DataFrame({"SYMBOL": ["SMALLCO"], "NAME OF COMPANY": ["SMALL CO LTD"], " DATE OF LISTING": ["01-Oct-2026"]})
    import nsemine.nse as nse
    monkeypatch.setattr(nse, "get_all_equities_list", lambda: eq)
    monkeypatch.setattr(nse, "get_all_sme_stocks_list", lambda: sme)
    rows = universe._fetch_csv_rows()
    assert rows[0] == {"symbol": "NEWCO", "name": "New Company Limited", "aliases": [], "series": "EQ", "listed": "2026-10-28"}
    assert rows[1]["symbol"] == "SMALLCO" and rows[1]["series"] == "SME"


def test_universe_direct_fallback_and_diagnostics(monkeypatch):
    import pandas as pd
    import nsemine.nse as nse
    from app.market import universe

    monkeypatch.setattr(nse, "get_all_equities_list", lambda: None)          # session path fails
    monkeypatch.setattr(nse, "get_all_sme_stocks_list", lambda: None)
    monkeypatch.setattr(universe, "_direct_csv", lambda series: pd.DataFrame(
        {"SYMBOL": ["SOUTHBANK"], "NAME OF COMPANY": ["SOUTH INDIAN BANK LIMITED"], " DATE OF LISTING": ["03-Jan-2000"]}) if series == "EQ" else pd.DataFrame())
    rows = universe._fetch_csv_rows()
    assert rows and rows[0]["symbol"] == "SOUTHBANK" and rows[0]["name"] == "South Indian Bank Limited"
    assert any(a.get("via") == "direct" and a.get("rows") == 1 for a in universe.diagnostics()["attempts"])
