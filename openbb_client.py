import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import pandas as pd

# Set OpenBB user directory to project folder BEFORE importing any openbb modules
project_dir = Path(__file__).parent.absolute()
obb_dir = project_dir / ".openbb_platform"
obb_dir.mkdir(parents=True, exist_ok=True)

os.environ["OPENBB_USER_SETTINGS_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_SYSTEM_SETTINGS_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_APPLICATION_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_HOME_DIRECTORY"] = str(obb_dir)
os.environ["HOME"] = str(project_dir)

try:
    from openbb import obb
except ImportError:
    obb = None
except Exception as e:
    print(f"CRITICAL: OpenBB Import Failed: {e}")
    obb = None

class OpenBBClient:
    def __init__(self):
        self._setup_keys()

    def _setup_keys(self):
        """Configure API keys for OpenBB providers if available in environment."""
        if not obb: return
        
        # Mapping of env vars to OpenBB provider keys
        key_map = {
            "FMP_API_KEY": "fmp",
            "POLYGON_API_KEY": "polygon",
            "TIINGO_API_KEY": "tiingo",
            "INTRINIO_API_KEY": "intrinio"
        }
        
        for env_var, provider in key_map.items():
            key = os.environ.get(env_var)
            if not key and provider == "fmp":
                key = "demo" # Use FMP demo key as a last resort
            if key:
                try:
                    obb.account.set_credentials(provider=provider, key=key)
                    print(f"INFO: OpenBB configured with {provider} API key.")
                except: pass

    def _to_dict(self, obb_object: Any) -> Any:
        """Robust conversion of OpenBB results to serializable formats."""
        if obb_object is None:
            return None
            
        # Extract results if it's an Obbject
        data = getattr(obb_object, "results", obb_object)
        
        # Handle DataFrame
        if hasattr(data, "to_dict") and not isinstance(data, list):
            try:
                # orient="records" for list of dicts
                res = data.to_dict(orient="records")
                # Sometimes it returns a dict of lists depending on pandas version/orient
                if isinstance(res, dict) and len(res) > 0:
                    # Check if it's column-oriented
                    first_val = next(iter(res.values()))
                    if isinstance(first_val, dict):
                        # Convert from {col: {index: val}} to [{col: val}]
                        keys = list(res.keys())
                        indices = list(first_val.keys())
                        return [{k: res[k][i] for k in keys} for i in indices]
                return res
            except:
                try:
                    import pandas as pd
                    if isinstance(data, pd.DataFrame):
                        import json
                        return json.loads(data.to_json(orient="records"))
                except: pass
            
        # Handle List of Pydantic models or dicts
        if isinstance(data, list):
            out = []
            for item in data:
                if hasattr(item, "model_dump"):
                    out.append(item.model_dump())
                elif hasattr(item, "dict"):
                    out.append(item.dict())
                elif isinstance(item, dict):
                    out.append(item)
                else:
                    # Try to convert custom objects to dict
                    try:
                        out.append(vars(item))
                    except:
                        out.append(item)
            return out
            
        return data

    async def get_quote(self, ticker: str) -> Dict[str, Any]:
        """Fetch real-time quote."""
        if not obb: return {}
        ticker = ticker.upper().replace("-", ".")
        try:
            # Try yfinance first for free data, then others
            res = None
            for p in ["yfinance", "fmp", "polygon", "intrinio", "tiingo"]:
                try:
                    res = await asyncio.to_thread(obb.equity.price.quote, symbol=ticker, provider=p)
                    if res and res.results: 
                        # Check if we actually got a price
                        data = self._to_dict(res)
                        if data and isinstance(data, list) and len(data) > 0:
                            q = data[0]
                            if q.get("last_price") or q.get("price") or q.get("close"):
                                print(f"INFO: Fetched {ticker} quote via {p}")
                                break
                except: continue
            
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                q = data[0]
                price = q.get("last_price") or q.get("price") or q.get("close")
                prev_close = q.get("prev_close") or q.get("previous_close")
                
                change = q.get("change")
                change_pct = q.get("change_percent") or q.get("change_pct")
                
                # Manual calculation if missing (common in yfinance)
                if change is None and price and prev_close:
                    change = price - prev_close
                if change_pct is None and price and prev_close and prev_close != 0:
                    change_pct = (change / prev_close) * 100

                return {
                    "price": price,
                    "change": change or 0,
                    "change_percent": change_pct or 0,
                    "volume": q.get("volume"),
                    "name": q.get("name"),
                    "fifty_two_week_high": q.get("year_high") or q.get("fifty_two_week_high"),
                    "fifty_two_week_low": q.get("year_low") or q.get("fifty_two_week_low"),
                    "open": q.get("open"),
                    "high": q.get("high"),
                    "low": q.get("low"),
                }
            
            # LAST RESORT: Direct yfinance call if OpenBB fails (helps with Invalid Crumb)
            try:
                import yfinance as yf
                ticker_yf = ticker.replace(".", "-")
                t = yf.Ticker(ticker_yf)
                info = t.fast_info
                if info and info.get("last_price"):
                    print(f"INFO: Fetched {ticker} quote via DIRECT yfinance fallback")
                    price = info["last_price"]
                    prev_close = info.get("previous_close")
                    change = price - prev_close if prev_close else 0
                    change_pct = (change / prev_close) * 100 if prev_close else 0
                    return {
                        "price": price,
                        "change": change,
                        "change_percent": change_pct,
                        "volume": info.get("last_volume"),
                        "name": ticker,
                        "fifty_two_week_high": info.get("year_high"),
                        "fifty_two_week_low": info.get("year_low"),
                    }
            except: pass

            return {}
        except Exception as e:
            print(f"DEBUG: Quote Error {ticker}: {e}")
            return {}

    async def get_quotes_batch(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch multiple quotes at once."""
        if not obb or not tickers: return {}
        
        # OpenBB Platform doesn't have a universal batch quote yet across all providers,
        # but we can fetch them in parallel with a limited semaphore to avoid hitting limits.
        semaphore = asyncio.Semaphore(10)
        
        async def fetch_one(t):
            async with semaphore:
                return t, await self.get_quote(t)
                
        tasks = [fetch_one(t) for t in tickers]
        results = await asyncio.gather(*tasks)
        return {t: q for t, q in results if q}

    async def get_profile(self, ticker: str) -> Dict[str, Any]:
        """Fetch company profile (sector, industry, description)."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.profile, symbol=ticker)
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                p = data[0]
                # Normalize yfinance fields
                return {
                    "name": p.get("name"),
                    "sector": p.get("sector"),
                    "industry": p.get("industry") or p.get("industry_category"),
                    "description": p.get("long_description") or p.get("short_description") or p.get("description"),
                    "market_cap": p.get("market_cap"),
                    "website": p.get("company_url"),
                    "employees": p.get("employees"),
                    "ceo": p.get("ceo"),
                }
            
            # Fallback to yfinance directly if OpenBB provider fails
            try:
                import yfinance as yf
                ticker_yf = ticker.replace(".", "-")
                t = yf.Ticker(ticker_yf)
                info = t.info
                if info:
                    print(f"INFO: Fetched {ticker} profile via DIRECT yfinance fallback")
                    return {
                        "name": info.get("longName"),
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                        "description": info.get("longBusinessSummary"),
                        "website": info.get("website"),
                        "city": info.get("city"),
                        "state": info.get("state"),
                        "country": info.get("country"),
                        "fullTimeEmployees": info.get("fullTimeEmployees")
                    }
            except Exception as e:
                print(f"DEBUG: YF PROFILE FALLBACK ERROR {ticker}: {e}")
                
            return {}
        except Exception as e:
            print(f"DEBUG: Profile Error {ticker}: {e}")
            return {}

    async def get_fundamentals(self, ticker: str) -> Dict[str, Any]:
        """Fetch fundamental metrics."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.fundamental.metrics, symbol=ticker)
            data = self._to_dict(res)
            
            # If OpenBB fails, try direct yfinance fallback
            if not data:
                try:
                    import yfinance as yf
                    ticker_yf = ticker.replace(".", "-")
                    t = yf.Ticker(ticker_yf)
                    info = t.info
                    if info:
                        print(f"INFO: Fetched {ticker} fundamentals via DIRECT yfinance fallback")
                        return {
                            "market_cap": info.get("marketCap"),
                            "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                            "dividend_yield": info.get("dividendYield"),
                            "revenue_growth": info.get("revenueGrowth"),
                            "operating_margin": info.get("operatingMargins"),
                            "return_on_equity": info.get("returnOnEquity"),
                            "debt_to_equity": info.get("debtToEquity"),
                            "current_ratio": info.get("currentRatio"),
                            "quick_ratio": info.get("quickRatio"),
                            "eps_ttm": info.get("trailingEps"),
                            "eps_forward": info.get("forwardEps"),
                            "peg_ratio": info.get("pegRatio"),
                            "price_to_book": info.get("priceToBook"),
                            "price_to_sales": info.get("priceToSales"),
                            "free_cash_flow": info.get("freeCashflow"),
                            "payout_ratio": info.get("payoutRatio"),
                            "enterprise_value": info.get("enterpriseValue"),
                            "ebitda": info.get("ebitda"),
                            "revenue_per_share": info.get("revenuePerShare"),
                            "book_value_per_share": info.get("bookValue"),
                        }
                except: pass

            if data and isinstance(data, list) and len(data) > 0:
                m = data[0]
                return {
                    "market_cap": m.get("market_cap"),
                    "pe_ratio": m.get("pe_ratio") or m.get("forward_pe") or m.get("trailing_pe"),
                    "fifty_two_week_high": m.get("fifty_two_week_high") or m.get("year_high"),
                    "fifty_two_week_low": m.get("fifty_two_week_low") or m.get("year_low"),
                    "dividend_yield": m.get("dividend_yield"),
                    "revenue_growth": m.get("revenue_growth"),
                    "operating_margin": m.get("operating_margin"),
                    "return_on_equity": m.get("return_on_equity"),
                    "debt_to_equity": m.get("debt_to_equity"),
                    "current_ratio": m.get("current_ratio"),
                    "quick_ratio": m.get("quick_ratio"),
                    "eps_ttm": m.get("eps_ttm"),
                    "eps_forward": m.get("eps_forward") or m.get("forward_eps"),
                    "peg_ratio": m.get("peg_ratio"),
                    "price_to_book": m.get("price_to_book"),
                    "price_to_sales": m.get("price_to_sales") or m.get("enterprise_to_revenue"),
                    "free_cash_flow": m.get("free_cash_flow"),
                    "payout_ratio": m.get("payout_ratio"),
                    "enterprise_value": m.get("enterprise_value"),
                    "ebitda": m.get("ebitda"),
                    "revenue_per_share": m.get("revenue_per_share"),
                    "book_value_per_share": m.get("book_value_per_share") or m.get("book_value"),
                    "net_income_per_share": m.get("net_income_per_share"),
                    "cash_per_share": m.get("cash_per_share"),
                }
            return {}
        except Exception as e:
            return {}

    async def get_multiples(self, ticker: str) -> Dict[str, Any]:
        """Fetch valuation multiples."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.fundamental.multiples, symbol=ticker)
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
            return {}
        except:
            return {}

    async def get_dividends(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch historical dividends."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.fundamental.dividends, symbol=ticker)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_ratios(self, ticker: str) -> Dict[str, Any]:
        """Extra financial ratios if available."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.fundamental.ratios, symbol=ticker)
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
            return {}
        except:
            return {}

    async def get_technical_indicators(self, ticker: str) -> Dict[str, Any]:
        """Fetch technicals via historical data processing."""
        ticker = ticker.upper().replace("-", ".")
        try:
            hist_data = await self.get_historical(ticker)
            if not hist_data or not hist_data.get("closes"):
                return {}

            # Convert back to a list of dicts for OpenBB technicals
            closes = hist_data["closes"]
            if len(closes) < 50:
                return {}

            # Simple manual calculation as a fallback if obb.technical fails
            # RSI (14)
            def calculate_rsi(data, window=14):
                if len(data) < window + 1: return None
                deltas = [data[i+1] - data[i] for i in range(len(data)-1)]
                gains = [d if d > 0 else 0 for d in deltas]
                losses = [-d if d < 0 else 0 for d in deltas]
                avg_gain = sum(gains[-window:]) / window
                avg_loss = sum(losses[-window:]) / window
                if avg_loss == 0: return 100
                rs = avg_gain / avg_loss
                return 100 - (100 / (1 + rs))

            # SMA (50)
            def calculate_sma(data, window=50):
                if len(data) < window: return None
                return sum(data[-window:]) / window

            return {
                "rsi": calculate_rsi(closes),
                "sma_50": calculate_sma(closes),
            }
        except Exception as e:
            print(f"DEBUG: Technicals Error {ticker}: {e}")
            return {}

    async def get_historical(self, ticker: str, range_str: str = "6mo") -> Dict[str, Any]:
        """Standardized historical data for charts + returns."""
        if not obb: return {"timestamps": [], "closes": [], "returns": {}}
        ticker = ticker.upper().replace("-", ".")
        try:
            # Map range to start_date and interval
            interval = "1d"
            days = 180
            
            # Handle numeric range strings like "30d"
            if range_str.endswith("d"):
                try:
                    days = int(range_str.replace("d", ""))
                except:
                    days = 180
            elif range_str == "1mo": days = 30
            elif range_str == "3mo": days = 90
            elif range_str == "6mo": days = 180
            elif range_str == "1y": days = 365
            elif range_str == "3y": days = 1095
            elif range_str == "5y": days = 1825
            
            start_date = (datetime.now() - timedelta(days=days + 15)).strftime("%Y-%m-%d")
            print(f"DEBUG: Fetching historical for {ticker} | Days: {days} | Start: {start_date}")
            
            # Try a few providers
            providers = ["yfinance", "fmp", "polygon", "tiingo"]
            res = None
            
            for p in providers:
                try:
                    res = await asyncio.to_thread(obb.equity.price.historical, symbol=ticker, interval=interval, start_date=start_date, provider=p)
                    if res and res.results: break
                except: continue

            data = self._to_dict(res)
            
            # LAST RESORT: Direct yfinance historical fetch if OpenBB fails
            if not data or not isinstance(data, list):
                try:
                    import yfinance as yf
                    print(f"INFO: Fetching {ticker} historical via DIRECT yfinance fallback")
                    ticker_yf = ticker.replace(".", "-")
                    df = await asyncio.to_thread(yf.download, ticker_yf, start=start_date, progress=False)
                    if not df.empty:
                        # Convert df to list of dicts
                        data = []
                        for index, row in df.iterrows():
                            # Row might be a Series with MultiIndex if using newer yfinance
                            # Handle both cases
                            d = {"date": index.strftime("%Y-%m-%d")}
                            if hasattr(row, "Close"): d["close"] = float(row["Close"])
                            elif "Close" in row: d["close"] = float(row["Close"])
                            
                            if "Volume" in row: d["volume"] = float(row["Volume"])
                            if "High" in row: d["high"] = float(row["High"])
                            if "Low" in row: d["low"] = float(row["Low"])
                            if "Open" in row: d["open"] = float(row["Open"])
                            
                            if "close" in d: data.append(d)
                except Exception as e:
                    print(f"DEBUG: Direct yfinance historical fallback failed: {e}")
            
            if not data or not isinstance(data, list):
                print(f"WARNING: No historical data returned for {ticker} from any provider.")
                return {"timestamps": [], "closes": [], "returns": {}}
            
            print(f"DEBUG: Historical data received for {ticker} | Entries: {len(data)}")
            
            ts, cls, vls, highs, lows, opens = [], [], [], [], [], []
            for d in data:
                date_val = d.get("date") or d.get("timestamp")
                close_val = d.get("close")
                vol_val = d.get("volume")
                high_val = d.get("high")
                low_val = d.get("low")
                open_val = d.get("open")
                if date_val and close_val is not None:
                    try:
                        ts.append(int(pd.to_datetime(date_val).timestamp()))
                        cls.append(float(close_val))
                        vls.append(float(vol_val) if vol_val else 0)
                        highs.append(float(high_val) if high_val else float(close_val))
                        lows.append(float(low_val) if low_val else float(close_val))
                        opens.append(float(open_val) if open_val else float(close_val))
                    except: continue
            
            # Calculate returns
            rets = {}
            if cls:
                now_p = cls[-1]
                if len(cls) > 1: rets["1D"] = (now_p / cls[-2] - 1)
                if len(cls) > 21: rets["1M"] = (now_p / cls[-21] - 1)
                if len(cls) > 252: rets["1Y"] = (now_p / cls[-252] - 1)
                if len(cls) > 1260: rets["5Y"] = (now_p / cls[-1260] - 1)
                
            return {
                "timestamps": ts,
                "closes": cls,
                "volumes": vls,
                "highs": highs,
                "lows": lows,
                "opens": opens,
                "returns": rets
            }
        except Exception as e:
            return {"timestamps": [], "closes": [], "returns": {}}

    async def get_news(self, ticker: str = None, limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch company or global news."""
        try:
            if ticker:
                ticker = ticker.upper().replace("-", ".")
                res = await asyncio.to_thread(obb.news.company, symbol=ticker, limit=limit)
            else:
                # Default to market news if no ticker provided
                res = await asyncio.to_thread(obb.news.company, symbol="SPY", limit=limit)
            
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except Exception as e:
            return []

    async def get_filings(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch SEC filings."""
        ticker = ticker.upper().replace("-", ".")
        try:
            # Try multiple providers for filings
            res = None
            for p in ["sec", "fmp", "intrinio"]:
                try:
                    res = await asyncio.to_thread(obb.equity.fundamental.filings, symbol=ticker, provider=p)
                    if res and res.results:
                        print(f"INFO: Fetched {ticker} filings via {p}")
                        break
                except: continue
            
            data = self._to_dict(res)
            if not data or not isinstance(data, list):
                # Fallback to direct SEC fetch if OpenBB fails
                return await self._get_filings_direct_sec(ticker)
            
            out = []
            for d in data[:15]:
                out.append({
                    "form": d.get("report_type") or d.get("form_type") or "SEC",
                    "date": str(d.get("filing_date") or d.get("date") or ""),
                    "description": d.get("title") or d.get("description") or "Financial Filing",
                    "url": d.get("url") or d.get("edgar_url") or ""
                })
            return out
        except Exception as e:
            return await self._get_filings_direct_sec(ticker)

    async def _get_filings_direct_sec(self, ticker: str) -> List[Dict[str, Any]]:
        """Directly fetch filings from SEC EDGAR API as a last resort."""
        try:
            import httpx
            # SEC requires a descriptive User-Agent
            headers = {"User-Agent": "ORION Research Assistant (contact@orion-ai.com)"}
            
            # 1. Get CIK mapping
            mapping_url = "https://www.sec.gov/files/company_tickers.json"
            async with httpx.AsyncClient(verify=False) as client: # verify=False to bypass SSL issues if they persist
                resp = await client.get(mapping_url, headers=headers)
                if resp.status_code != 200: return []
                mapping = resp.json()
            
            cik = None
            for item in mapping.values():
                if item["ticker"] == ticker:
                    cik = str(item["cik_str"]).zfill(10)
                    break
            
            if not cik: return []
            
            # 2. Get filings for CIK
            filings_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(filings_url, headers=headers)
                if resp.status_code != 200: return []
                data = resp.json()
            
            recent = data.get("filings", {}).get("recent", {})
            if not recent: return []
            
            out = []
            # Map the columns
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            
            for i in range(min(15, len(forms))):
                acc = accessions[i].replace("-", "")
                doc = primary_docs[i]
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
                
                out.append({
                    "form": forms[i],
                    "date": dates[i],
                    "description": f"SEC Form {forms[i]} Filing",
                    "url": url
                })
            
            print(f"INFO: Fetched {ticker} filings via DIRECT SEC FALLBACK")
            return out
        except Exception as e:
            print(f"ERROR: Direct SEC fetch failed: {e}")
            return []


    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Search tickers."""
        try:
            res = await asyncio.to_thread(obb.equity.search, query=query)
            data = self._to_dict(res)
            if data and isinstance(data, list):
                return [{
                    "ticker": d.get("symbol"),
                    "name": d.get("name"),
                    "sector": d.get("sector"),
                    "exchange": d.get("exchange")
                } for d in data if d.get("symbol")]
            return []
        except Exception as e:
            return []

    async def get_top_movers(self, mode: str = "gainers") -> List[Dict[str, Any]]:
        """Fetch gainers or losers."""
        try:
            if mode == "gainers":
                res = await asyncio.to_thread(obb.equity.discovery.gainers)
            else:
                res = await asyncio.to_thread(obb.equity.discovery.losers)
                
            data = self._to_dict(res)
            if data and isinstance(data, list):
                return [{
                    "ticker": d.get("symbol"),
                    "price": d.get("last_price") or d.get("price"),
                    "change_pct": d.get("change_percent") or d.get("change_pct"),
                    "name": d.get("name")
                } for d in data[:50]]
            return []
        except:
            return []

    async def get_ownership(self, ticker: str) -> Dict[str, Any]:
        """Fetch institutional and major holders."""
        ticker = ticker.upper().replace("-", ".")
        try:
            tasks = [
                asyncio.to_thread(obb.equity.ownership.institutional, symbol=ticker),
                asyncio.to_thread(obb.equity.ownership.major_holders, symbol=ticker)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            out = {"institutional": [], "major_holders": []}
            if not isinstance(results[0], Exception):
                out["institutional"] = self._to_dict(results[0])[:15]
            if not isinstance(results[1], Exception):
                out["major_holders"] = self._to_dict(results[1])
            return out
        except:
            return {"institutional": [], "major_holders": []}

    async def get_etf_info(self, symbol: str) -> Dict[str, Any]:
        """Fetch ETF information."""
        symbol = symbol.upper()
        try:
            res = await asyncio.to_thread(obb.etf.info, symbol=symbol)
            data = self._to_dict(res)
            return data[0] if data and isinstance(data, list) else {}
        except:
            return {}

    async def get_etf_holdings(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch ETF holdings."""
        symbol = symbol.upper()
        try:
            res = await asyncio.to_thread(obb.etf.holdings, symbol=symbol)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_insider_trading(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch latest insider trades."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.ownership.insider_trading, symbol=ticker)
            data = self._to_dict(res)
            return data[:15] if isinstance(data, list) else []
        except:
            return []

    async def get_financials(self, ticker: str, statement: str = "income") -> List[Dict[str, Any]]:
        """Fetch income, balance, or cash flow statements."""
        ticker = ticker.upper().replace("-", ".")
        try:
            if statement == "income":
                res = await asyncio.to_thread(obb.equity.fundamental.income, symbol=ticker)
            elif statement == "balance":
                res = await asyncio.to_thread(obb.equity.fundamental.balance, symbol=ticker)
            else:
                res = await asyncio.to_thread(obb.equity.fundamental.cash, symbol=ticker)
            
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_estimates(self, ticker: str) -> Dict[str, Any]:
        """Fetch consensus estimates and forward looking data."""
        ticker = ticker.upper().replace("-", ".")
        try:
            tasks = [
                asyncio.to_thread(obb.equity.estimates.consensus, symbol=ticker),
                asyncio.to_thread(obb.equity.estimates.forward_eps, symbol=ticker),
                asyncio.to_thread(obb.equity.estimates.forward_ebitda, symbol=ticker)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            out = {}
            if not isinstance(results[0], Exception):
                cons = self._to_dict(results[0])
                if cons and isinstance(cons, list): out["consensus"] = cons[0]
            
            if not isinstance(results[1], Exception):
                f_eps = self._to_dict(results[1])
                if f_eps and isinstance(f_eps, list): out["forward_eps"] = f_eps[0]

            if not isinstance(results[2], Exception):
                f_ebitda = self._to_dict(results[2])
                if f_ebitda and isinstance(f_ebitda, list): out["forward_ebitda"] = f_ebitda[0]

            return out
        except:
            return {}

    async def get_analyst_recommendations(self, ticker: str) -> Dict[str, Any]:
        """Fetch targets."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.estimates.price_target, symbol=ticker)
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                return {
                    "target_mean": latest.get("target_mean"),
                    "target_high": latest.get("target_high"),
                    "target_low": latest.get("target_low"),
                    "recommendation": latest.get("recommendation")
                }
            return {}
        except:
            return {}

    async def get_peers(self, ticker: str) -> List[str]:
        """Fetch company peers."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.compare.peers, symbol=ticker)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_calendar_events(self, ticker: str) -> Dict[str, Any]:
        """Fetch earnings and dividend dates."""
        ticker = ticker.upper().replace("-", ".")
        try:
            tasks = [
                asyncio.to_thread(obb.equity.calendar.earnings, symbol=ticker),
                asyncio.to_thread(obb.equity.calendar.dividend, symbol=ticker)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            out = {}
            if not isinstance(results[0], Exception):
                e = self._to_dict(results[0])
                if e and isinstance(e, list): out["earnings"] = e[0]
            if not isinstance(results[1], Exception):
                d = self._to_dict(results[1])
                if d and isinstance(d, list): out["dividend"] = d[0]
            return out
        except:
            return {}

    async def get_short_interest(self, ticker: str) -> Dict[str, Any]:
        """Fetch short interest data."""
        ticker = ticker.upper().replace("-", ".")
        try:
            res = await asyncio.to_thread(obb.equity.shorts.fails_to_deliver, symbol=ticker)
            data = self._to_dict(res)
            if data and isinstance(data, list) and len(data) > 0:
                return {"fails_to_deliver": data[:10]}
            return {}
        except:
            return {}

    async def get_revenue_segments(self, ticker: str) -> Dict[str, Any]:
        """Fetch revenue breakdown by segment and geography."""
        ticker = ticker.upper().replace("-", ".")
        try:
            tasks = [
                asyncio.to_thread(obb.equity.fundamental.revenue_per_segment, symbol=ticker),
                asyncio.to_thread(obb.equity.fundamental.revenue_per_geography, symbol=ticker)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            out = {}
            if not isinstance(results[0], Exception):
                out["segments"] = self._to_dict(results[0])
            if not isinstance(results[1], Exception):
                out["geography"] = self._to_dict(results[1])
            return out
        except:
            return {}

    async def get_economy_calendar(self) -> List[Dict[str, Any]]:
        """Fetch upcoming economic events."""
        try:
            res = await asyncio.to_thread(obb.economy.calendar)
            data = self._to_dict(res)
            return data[:10] if isinstance(data, list) else []
        except:
            return []

    async def get_yield_curve(self) -> List[Dict[str, Any]]:
        """Fetch US Treasury yield curve."""
        try:
            res = await asyncio.to_thread(obb.fixedincome.government.treasury, series="yield")
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_cpi(self) -> List[Dict[str, Any]]:
        """Fetch Consumer Price Index data."""
        try:
            res = await asyncio.to_thread(obb.economy.cpi, country="united_states")
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_gdp(self) -> List[Dict[str, Any]]:
        """Fetch GDP data."""
        try:
            res = await asyncio.to_thread(obb.economy.gdp, country="united_states")
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_unemployment(self) -> List[Dict[str, Any]]:
        """Fetch unemployment data."""
        try:
            res = await asyncio.to_thread(obb.economy.unemployment, country="united_states")
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_indicators(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch various economic indicators."""
        try:
            res = await asyncio.to_thread(obb.economy.indicators, symbol=symbol)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_crypto_historical(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch historical crypto prices."""
        try:
            res = await asyncio.to_thread(obb.crypto.price.historical, symbol=symbol)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

    async def get_forex_historical(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch historical forex prices."""
        try:
            res = await asyncio.to_thread(obb.currency.price.historical, symbol=symbol)
            data = self._to_dict(res)
            return data if isinstance(data, list) else []
        except:
            return []

openbb_client = OpenBBClient()
