import asyncio
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from openbb_client import openbb_client
from fred_client import fred_client

CACHE_DIR = Path(__file__).parent / "data" / "router_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

class DataRouter:
    """
    Unified Data Router for ORION.
    Handles all external data requests, normalization, and caching.
    Ensures ORION never directly calls data providers.
    """
    
    def __init__(self, cache_ttl: int = 3600):
        self.cache_ttl = cache_ttl

    def _get_cache_path(self, key: str) -> Path:
        v_key = f"v2:{key}"
        hashed = hashlib.md5(v_key.encode()).hexdigest()
        return CACHE_DIR / f"{hashed}.json"

    def _get_cached(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        path = self._get_cache_path(key)
        if not path.exists():
            return None
        
        try:
            with open(path, 'r') as f:
                cached = json.load(f)
            
            ts = cached.get("_timestamp", 0)
            limit = ttl if ttl is not None else self.cache_ttl
            if (datetime.now().timestamp() - ts) > limit:
                return None
            
            return cached.get("data")
        except:
            return None

    def _set_cached(self, key: str, data: Any):
        if not data: # Don't cache empty or None data
            return
        path = self._get_cache_path(key)
        try:
            with open(path, 'w') as f:
                json.dump({
                    "_timestamp": datetime.now().timestamp(),
                    "data": data
                }, f)
        except:
            pass

    async def get_macro_indicator(self, series_id: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get latest value for a FRED macro indicator.
        """
        return await fred_client.get_series_latest(series_id, force_refresh=force_refresh)

    async def get_macro_history(self, series_id: str, days: int = 365, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get historical data for a FRED macro indicator.
        """
        return await fred_client.get_series_history(series_id, days=days, force_refresh=force_refresh)

    async def get_fx_rate(self, base: str = "USD", target: str = "INR", force_refresh: bool = False) -> Optional[float]:
        """
        Get latest FX rate from FRED (DEX series).
        """
        series_id = f"DEX{base}{target}" if base == "USD" else f"DEX{target}{base}"
        # Some special cases
        if base == "USD" and target == "INR": series_id = "DEXINUS"
        elif base == "USD" and target == "EUR": series_id = "DEXUSEU"
        elif base == "USD" and target == "JPY": series_id = "DEXJPUS"
        
        data = await fred_client.get_series_latest(series_id, force_refresh=force_refresh)
        if data and data.get("value") and data["value"] != ".":
            try:
                return float(data["value"])
            except ValueError:
                return None
        return None

    async def get_fx_history(self, base: str = "USD", target: str = "INR", days: int = 365) -> List[Dict[str, Any]]:
        """
        Get historical FX rates from FRED.
        """
        series_id = "DEXINUS" if base == "USD" and target == "INR" else f"DEX{base}{target}"
        if base == "USD" and target == "EUR": series_id = "DEXUSEU"
        elif base == "USD" and target == "JPY": series_id = "DEXJPUS"
        
        history = await fred_client.get_series_history(series_id, days=days)
        # Filter out "." values which FRED uses for holidays
        return [h for h in history if h.get("value") and h["value"] != "."]

    async def get_ticker_info_basic(self, ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fast version of get_ticker_info: Quote, Profile, Fundamentals only.
        """
        ticker = ticker.upper()
        cache_key = f"ticker_info_basic:{ticker}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached: return cached

        tasks = [
            openbb_client.get_quote(ticker),
            openbb_client.get_profile(ticker),
            openbb_client.get_fundamentals(ticker)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        def safe_get(idx, default):
            res = results[idx]
            if isinstance(res, Exception): return default
            return res or default

        quote = safe_get(0, {})
        profile = safe_get(1, {})
        fundamentals = safe_get(2, {})
        
        # If quote is missing but fundamentals has a price (common in some providers), use it
        if not quote.get("price") and fundamentals.get("price"):
            quote["price"] = fundamentals["price"]
            
        if not quote.get("price") and not profile.get("name"):
            return {"ticker": ticker, "error": "No data found", "price": None}

        hi_52 = quote.get("fifty_two_week_high") or fundamentals.get("fifty_two_week_high")
        lo_52 = quote.get("fifty_two_week_low") or fundamentals.get("fifty_two_week_low")
        m_cap = fundamentals.get("market_cap") or profile.get("market_cap")
        pe = fundamentals.get("pe_ratio") or profile.get("pe_ratio")
        div = fundamentals.get("dividend_yield") or profile.get("dividend_yield")

        normalized = {
            "ticker": ticker,
            "name": profile.get("name") or quote.get("name") or ticker,
            "price": quote.get("price"),
            "change": quote.get("change") or 0,
            "change_pct": quote.get("change_percent") or quote.get("change_pct") or 0,
            "changePct": quote.get("change_percent") or quote.get("change_pct") or 0,
            "volume": quote.get("volume") or fundamentals.get("volume"),
            "market_cap": m_cap,
            "marketCap": m_cap,
            "pe_ratio": pe,
            "peRatio": pe,
            "fifty_two_week_high": hi_52,
            "fiftyTwoWeekHigh": hi_52,
            "fifty_two_week_low": lo_52,
            "fiftyTwoWeekLow": lo_52,
            "dividend_yield": div,
            "dividendYield": div,
            "sector": profile.get("sector") or "Unknown",
            "industry": profile.get("industry") or "Unknown",
            "ratios": {
                "peRatio": pe,
                "marketCap": m_cap,
                "dividendYield": div,
                "fiftyTwoWeekHigh": hi_52,
                "fiftyTwoWeekLow": lo_52,
                "pegRatio": fundamentals.get("peg_ratio"),
                "priceToSales": fundamentals.get("price_to_sales"),
                "priceToBook": fundamentals.get("price_to_book"),
                "revenueGrowth": fundamentals.get("revenue_growth"),
                "operatingMargins": fundamentals.get("operating_margin"),
                "returnOnEquity": fundamentals.get("return_on_equity"),
            },
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "last_updated": datetime.now().isoformat()
        }
        
        self._set_cached(cache_key, normalized)
        return normalized

    async def get_ticker_info(self, ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch complete ticker intelligence: Quote, Profile, Fundamentals, Technicals.
        Normalized into ORION's schema.
        """
        ticker = ticker.upper()
        cache_key = f"ticker_info:{ticker}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached

        # Fetch all data in parallel via OpenBB, handling exceptions gracefully
        tasks = [
            openbb_client.get_quote(ticker),
            openbb_client.get_profile(ticker),
            openbb_client.get_fundamentals(ticker),
            openbb_client.get_technical_indicators(ticker),
            openbb_client.get_analyst_recommendations(ticker),
            openbb_client.get_historical(ticker),
            openbb_client.get_multiples(ticker)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Safely extract results or use empty defaults
        def safe_get(idx, default):
            res = results[idx]
            if isinstance(res, Exception):
                print(f"ROUTER ERROR for {ticker} task {idx}: {res}")
                return default
            return res or default

        quote = safe_get(0, {})
        profile = safe_get(1, {})
        fundamentals = safe_get(2, {})
        technicals = safe_get(3, {})
        estimates = safe_get(4, {})
        historical = safe_get(5, {"timestamps": [], "closes": [], "returns": {}})
        multiples = safe_get(6, {})
        
        # Check if we got ANY meaningful data. If not, don't return an empty dict as success
        if not quote and not profile and not fundamentals:
            return {"ticker": ticker, "error": "No data found for ticker"}

        # Normalize into ORION schema
        # Merge fields from quote and fundamentals carefully
        hi_52 = quote.get("fifty_two_week_high") or fundamentals.get("fifty_two_week_high")
        lo_52 = quote.get("fifty_two_week_low") or fundamentals.get("fifty_two_week_low")
        m_cap = fundamentals.get("market_cap") or profile.get("market_cap")
        pe = fundamentals.get("pe_ratio") or profile.get("pe_ratio")

        normalized = {
            "ticker": ticker,
            "name": profile.get("name") or quote.get("name") or ticker,
            "price": quote.get("price") or (historical["closes"][-1] if historical.get("closes") else None),
            "change": quote.get("change") or 0,
            "change_pct": quote.get("change_percent") or quote.get("change_pct") or 0,
            "changePct": quote.get("change_percent") or quote.get("change_pct") or 0, # Alias for frontend
            "volume": quote.get("volume") or (historical["volumes"][-1] if historical.get("volumes") else 0),
            "market_cap": m_cap,
            "marketCap": m_cap, # Alias
            "pe_ratio": pe,
            "peRatio": pe, # Alias
            "fifty_two_week_high": hi_52,
            "fiftyTwoWeekHigh": hi_52, # Alias
            "fifty_two_week_low": lo_52,
            "fiftyTwoWeekLow": lo_52, # Alias
            "dividend_yield": fundamentals.get("dividend_yield") or profile.get("dividend_yield"),
            "dividendYield": fundamentals.get("dividend_yield") or profile.get("dividend_yield"), # Alias
            "sector": profile.get("sector") or "Unknown",
            "industry": profile.get("industry") or "Unknown",
            "description": profile.get("description") or "",
            "rsi": technicals.get("rsi"),
            "sma_50": technicals.get("sma_50"),
            "recommendation": estimates.get("recommendation"),
            "target_mean": estimates.get("target_mean"),
            "targetMean": estimates.get("target_mean"), # Alias
            "returns": historical.get("returns", {}),
            "ratios": {
                "eps_ttm": fundamentals.get("eps_ttm"),
                "epsTTM": fundamentals.get("eps_ttm"),
                "peg_ratio": fundamentals.get("peg_ratio"),
                "pegRatio": fundamentals.get("peg_ratio"),
                "debt_to_equity": fundamentals.get("debt_to_equity"),
                "debtToEquity": fundamentals.get("debt_to_equity"),
                "roe": fundamentals.get("return_on_equity"),
                "returnOnEquity": fundamentals.get("return_on_equity"), # Alias
                "revenue_growth": fundamentals.get("revenue_growth"),
                "revenueGrowth": fundamentals.get("revenue_growth"),
                "operating_margin": fundamentals.get("operating_margin"),
                "operatingMargin": fundamentals.get("operating_margin"),
                "operatingMargins": fundamentals.get("operating_margin"), # Alias
                "current_ratio": fundamentals.get("current_ratio"),
                "currentRatio": fundamentals.get("current_ratio"),
                "quick_ratio": fundamentals.get("quick_ratio"),
                "quickRatio": fundamentals.get("quick_ratio"),
                "price_to_book": fundamentals.get("price_to_book"),
                "priceToBook": fundamentals.get("price_to_book"),
                "price_to_sales": fundamentals.get("price_to_sales"),
                "priceToSales": fundamentals.get("price_to_sales"),
                "free_cash_flow": fundamentals.get("free_cash_flow"),
                "freeCashFlow": fundamentals.get("free_cash_flow"),
                "payout_ratio": fundamentals.get("payout_ratio"),
                "payoutRatio": fundamentals.get("payout_ratio"),
                "enterprise_value": fundamentals.get("enterprise_value"),
                "enterpriseValue": fundamentals.get("enterprise_value"),
                "ev_to_ebitda": multiples.get("ev_to_ebitda"),
                "evToEbitda": multiples.get("ev_to_ebitda"),
                "enterpriseToEbitda": multiples.get("ev_to_ebitda"), # Alias
                "ev_to_sales": multiples.get("ev_to_revenue"),
                "evToSales": multiples.get("ev_to_revenue"),
                "pb_ratio": multiples.get("pb_ratio"),
                "pbRatio": multiples.get("pb_ratio"),
                "ps_ratio": multiples.get("ps_ratio"),
                "psRatio": multiples.get("ps_ratio"),
            },
            "last_updated": datetime.now().isoformat()
        }
        
        if normalized.get("price") is not None:
            self._set_cached(cache_key, normalized)
        return normalized

    async def get_historical(self, ticker: str, range_str: str = "6mo", force_refresh: bool = False) -> Dict[str, List]:
        """Fetch historical OHLC data."""
        ticker = ticker.upper()
        cache_key = f"historical:{ticker}:{range_str}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=14400) # 4 hours for historical
            if cached:
                return cached
        
        data = await openbb_client.get_historical(ticker, range_str=range_str)
        self._set_cached(cache_key, data)
        return data

    async def get_news(self, ticker: str = None, limit: int = 15, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch company news."""
        ticker_key = ticker.upper() if ticker else "GLOBAL"
        cache_key = f"news:{ticker_key}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=1800) # 30 mins for news
            if cached:
                return cached
        
        raw_news = await openbb_client.get_news(ticker, limit=limit)
        
        # Normalize news schema
        normalized = []
        for n in raw_news:
            normalized.append({
                "title": n.get("title") or n.get("headline"),
                "url": n.get("url") or n.get("link"),
                "source": n.get("source"),
                "date": n.get("date") or n.get("published"),
                "summary": n.get("summary") or n.get("description") or ""
            })
            
        self._set_cached(cache_key, normalized)
        return normalized

    async def get_filings(self, ticker: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch SEC filings."""
        ticker = ticker.upper()
        cache_key = f"filings:{ticker}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=86400) # 24 hours for filings
            if cached:
                return cached
                
        data = await openbb_client.get_filings(ticker)
        self._set_cached(cache_key, data)
        return data

    async def get_deep_ticker_analysis(self, ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Deep analysis including ownership, full financials, and estimates.
        """
        ticker = ticker.upper()
        cache_key = f"deep_analysis:{ticker}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=86400) # 24h
            if cached: return cached
            
        tasks = [
            openbb_client.get_ownership(ticker),
            openbb_client.get_insider_trading(ticker),
            openbb_client.get_estimates(ticker),
            openbb_client.get_financials(ticker, "income"),
            openbb_client.get_financials(ticker, "balance"),
            openbb_client.get_financials(ticker, "cash"),
            openbb_client.get_peers(ticker),
            openbb_client.get_calendar_events(ticker),
            openbb_client.get_revenue_segments(ticker),
            openbb_client.get_short_interest(ticker),
            openbb_client.get_dividends(ticker)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        def safe_get(idx, default):
            res = results[idx]
            if isinstance(res, Exception): return default
            return res or default
            
        deep_data = {
            "ownership": safe_get(0, {}),
            "insider_trading": safe_get(1, []),
            "estimates": safe_get(2, {}),
            "financials": {
                "income": safe_get(3, []),
                "balance": safe_get(4, []),
                "cash": safe_get(5, [])
            },
            "peers": safe_get(6, []),
            "calendar": safe_get(7, {}),
            "revenue_mix": safe_get(8, {}),
            "short_interest": safe_get(9, {}),
            "dividends": safe_get(10, [])
        }
        
        self._set_cached(cache_key, deep_data)
        return deep_data

    async def get_economy_data(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch broad economic data."""
        cache_key = "economy_data"
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=86400)
            if cached: return cached
            
        tasks = [
            openbb_client.get_economy_calendar(),
            openbb_client.get_yield_curve(),
            openbb_client.get_cpi(),
            openbb_client.get_gdp(),
            openbb_client.get_unemployment()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        def safe_get(idx, default):
            res = results[idx]
            if isinstance(res, Exception): return default
            return res or default
            
        econ_data = {
            "calendar": safe_get(0, []),
            "yield_curve": safe_get(1, []),
            "cpi": safe_get(2, []),
            "gdp": safe_get(3, []),
            "unemployment": safe_get(4, [])
        }
        
        self._set_cached(cache_key, econ_data)
        return econ_data

    async def get_etf_deep_analysis(self, symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch deep ETF data."""
        symbol = symbol.upper()
        cache_key = f"etf_analysis:{symbol}"
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=86400)
            if cached: return cached
            
        tasks = [
            openbb_client.get_etf_info(symbol),
            openbb_client.get_etf_holdings(symbol)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        def safe_get(idx, default):
            res = results[idx]
            if isinstance(res, Exception): return default
            return res or default
            
        etf_data = {
            "info": safe_get(0, {}),
            "holdings": safe_get(1, [])
        }
        
        self._set_cached(cache_key, etf_data)
        return etf_data

    async def get_top_movers(self, mode: str = "gainers", force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch gainers or losers."""
        cache_key = f"top_movers:{mode}"
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl=900) # 15 min
            if cached: return cached
        
        # To avoid hitting the API twice for gainers and losers in the same dashboard load,
        # we could cache the raw result from openbb_client.get_top_movers if it returned everything,
        # but currently get_top_movers filters by mode.
        data = await openbb_client.get_top_movers(mode)
        # Ensure camelCase for frontend
        for item in data:
            item["changePct"] = item.get("change_pct")
            
        self._set_cached(cache_key, data)
        return data

    async def search_tickers(self, query: str) -> List[Dict[str, Any]]:
        """Search for tickers."""
        # Search is usually not cached or cached very briefly
        return await openbb_client.search(query)

# Singleton instance
data_router = DataRouter()
