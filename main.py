import os
from pathlib import Path

# Set OpenBB user directory early to avoid permission issues
project_dir = Path(__file__).parent.absolute()
obb_dir = project_dir / ".openbb_platform"
obb_dir.mkdir(parents=True, exist_ok=True)

os.environ["OPENBB_USER_SETTINGS_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_SYSTEM_SETTINGS_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_APPLICATION_DIRECTORY"] = str(obb_dir)
os.environ["OPENBB_HOME_DIRECTORY"] = str(obb_dir)
# Force HOME to be the project directory to redirect all dot-files
os.environ["HOME"] = str(project_dir)

from contextlib import asynccontextmanager
from fastapi import FastAPI, Body, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import asyncio
import httpx
import json
import re
import hashlib
import os
import time
import uuid
from datetime import datetime, date, timedelta
from urllib.parse import quote as url_quote

from data_router import data_router
from openbb_client import openbb_client
from fred_client import fred_client
import asyncio # Kept for background tasks

try:
    from top100_data import STOCKS as TOP100_EMBED
except ImportError:
    TOP100_EMBED = []

try:
    from orion_assistant import run_assistant_chat, build_single_ticker_verdict, synthesize_industry_report, synthesize_equity_report, build_industry_intelligence, _enrich_fundamentals_async, _dedupe_strings
except ImportError:
    run_assistant_chat = None  # type: ignore
    build_single_ticker_verdict = None  # type: ignore
    synthesize_industry_report = None  # type: ignore
    synthesize_equity_report = None  # type: ignore
    build_industry_intelligence = None  # type: ignore
    _enrich_fundamentals_async = None  # type: ignore

try:
    from orion_report import build_stocks_report, build_industry_report, report_to_pdf
except ImportError:
    build_stocks_report = None  # type: ignore
    build_industry_report = None  # type: ignore
    report_to_pdf = None  # type: ignore


def _load_top100():
    global TOP_100
    TOP_100 = []
    if TOP100_FILE.exists():
        try:
            TOP_100 = json.loads(TOP100_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"TOP100 file error: {e}")
    if not TOP_100:
        TOP_100 = list(TOP100_EMBED)
        print(f"TOP100 using embedded list ({len(TOP_100)} stocks)")
    
    # Normalize industries
    from orion_assistant import normalize_industry_name
    for s in TOP_100:
        s["industry"] = normalize_industry_name(s.get("industry"))


async def refresh_all_sectors_task():
    # Wait a bit for server to fully start
    await asyncio.sleep(10)
    from orion_assistant import _SECTOR_PROXIES, fetch_sector_benchmarks
    print(f"BACKGROUND: Refreshing intelligence for {len(_SECTOR_PROXIES)} sectors...")
    for industry in _SECTOR_PROXIES.keys():
        try:
            await fetch_sector_benchmarks(industry, force_refresh=True)
            print(f"✓ Refreshed {industry}")
            await asyncio.sleep(2) # rate limit friendly
        except Exception as e:
            print(f"✗ Failed {industry}: {e}")
    print("BACKGROUND: Sector intelligence refresh complete.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_custom_tickers()
    _load_top100()
    _load_sessions()
    print(f"ORION ready — universe={len(get_universe())} top100={len(TOP_100)} sessions={len(SESSIONS)}")
    
    # Trigger background sector refresh
    asyncio.create_task(refresh_all_sectors_task())
    
    yield
    _save_sessions()
    _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="ORION", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://samvruthc.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CUSTOM_TICKERS_FILE = DATA_DIR / "custom_tickers.json"
TOP100_FILE = DATA_DIR / "top100.json"

if DATA_DIR.is_dir():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

CACHE: Dict[str, Any] = {}
_executor = ThreadPoolExecutor(max_workers=8)

# --- CONFIGS (LRS & TAX) ---
LRS_LIMIT_USD = 250000
TAX_SLABS = {
    "5": 0.05,
    "20": 0.20,
    "30": 0.30
}
LTCG_RATE = 0.125
LTCG_THRESHOLD_MONTHS = 24

# --- PRICING CONFIG ---
LAUNCH_OFFER_TOTAL_SPOTS = 500
LAUNCH_OFFER_DISCOUNT = 0.4  # 40% off
GST_RATE = 0.18  # 18% GST for INR

# Pricing tiers: currency -> plan -> billing (monthly/yearly) -> details
PRICING = {
    "INR": {
        "free": {
            "name": "Free",
            "monthly": {"price": 0},
            "yearly": {"price": 0}
        },
        "pro": {
            "name": "Pro",
            "monthly": {"price": 799, "save_pct": 0},
            "yearly": {"price": 7188, "monthly_eq": 599, "save_pct": 0.25}
        },
        "max": {
            "name": "Max",
            "monthly": {"price": 1599, "save_pct": 0},
            "yearly": {"price": 14388, "monthly_eq": 1199, "save_pct": 0.25}
        }
    },
    "USD": {
        "free": {
            "name": "Free",
            "monthly": {"price": 0},
            "yearly": {"price": 0}
        },
        "pro": {
            "name": "Pro",
            "monthly": {"price": 9, "save_pct": 0},
            "yearly": {"price": 84, "monthly_eq": 7, "save_pct": 0.25}
        },
        "max": {
            "name": "Max",
            "monthly": {"price": 18, "save_pct": 0},
            "yearly": {"price": 168, "monthly_eq": 14, "save_pct": 0.25}
        }
    }
}

# --- PLAN LIMITS CONFIG (CENTRALIZED) ---
PLAN_LIMITS = {
    "free": {
        "watchlist": 5,
        "inr_reality_check_daily": 3,
        "ai_chat_daily": 5,
        "reports_monthly": 0
    },
    "pro": {
        "watchlist": None,
        "inr_reality_check_daily": None,
        "ai_chat_daily": 50,
        "reports_monthly": 3
    },
    "max": {
        "watchlist": None,
        "inr_reality_check_daily": None,
        "ai_chat_daily": None,
        "reports_monthly": None
    }
}

# --- FEATURE GATING ---
FEATURES = {
    "free": [
        "Real-time Quotes", "Basic Charts", "Market Cap and P/E", "Top 100 Movers",
        {"name": "Watchlist", "limit": 5},
        {"name": "INR Reality Check", "limit": 3},
        {"name": "AI Research Chat", "limit": 5}
    ],
    "pro": [
        "Real-time Quotes", "Basic Charts", "Market Cap and P/E", "Top 100 Movers",
        {"name": "Watchlist", "limit": None},
        {"name": "INR Reality Check", "limit": None},
        "News Stream", "Technical Signals", "Sentiment Analysis", 
        "Price Alerts", "Portfolio P&L Tracking",
        {"name": "AI Research Chat", "limit": 50},
        "AI Buy/Hold/Avoid Verdicts", "SEC EDGAR Intelligence",
        {"name": "Report Builder", "limit": 3},
        "Export to PDF"
    ],
    "max": [
        "Real-time Quotes", "Basic Charts", "Market Cap and P/E", "Top 100 Movers",
        {"name": "Watchlist", "limit": None},
        {"name": "INR Reality Check", "limit": None},
        "News Stream", "Technical Signals", "Sentiment Analysis", 
        "Price Alerts", "Portfolio P&L Tracking",
        {"name": "AI Research Chat", "limit": None},
        "AI Buy/Hold/Avoid Verdicts", "SEC EDGAR Intelligence",
        {"name": "Report Builder", "limit": None},
        "Export to PDF", "Industry Deep Scans", 
        "Multi-Agent Thesis Synthesis", "Institutional Investment Memos"
    ]
}

# --- LAUNCH OFFER COUNTER ---
LAUNCH_OFFER_FILE = DATA_DIR / "launch_offer.json"

def _load_launch_offer():
    if not LAUNCH_OFFER_FILE.exists():
        data = {"spots_remaining": LAUNCH_OFFER_TOTAL_SPOTS}
        _save_launch_offer(data)
        return data
    try:
        return json.loads(LAUNCH_OFFER_FILE.read_text())
    except:
        return {"spots_remaining": LAUNCH_OFFER_TOTAL_SPOTS}

def _save_launch_offer(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_OFFER_FILE.write_text(json.dumps(data, indent=2))

def _decrement_launch_offer_spots():
    data = _load_launch_offer()
    if data["spots_remaining"] > 0:
        data["spots_remaining"] -= 1
        _save_launch_offer(data)
    return data["spots_remaining"]

# --- USAGE TRACKING ---
USAGE_FILE = DATA_DIR / "usage.json"

def _get_today_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def _get_current_month_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m")

def _load_usage() -> Dict[str, Dict]:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text())
    except:
        return {}

def _save_usage(usage_data: Dict[str, Dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(usage_data, indent=2))

def _get_user_usage(user_email: str) -> Dict[str, Any]:
    usage = _load_usage()
    user_usage = usage.get(user_email, {
        "ai_chat": {
            "count": 0,
            "reset_date": _get_today_utc()
        },
        "inr_reality_check": {
            "count": 0,
            "reset_date": _get_today_utc()
        },
        "reports": {
            "count": 0,
            "reset_month": _get_current_month_utc()
        }
    })
    # Reset daily counters if needed
    if user_usage["ai_chat"]["reset_date"] != _get_today_utc():
        user_usage["ai_chat"]["count"] = 0
        user_usage["ai_chat"]["reset_date"] = _get_today_utc()
    if user_usage["inr_reality_check"]["reset_date"] != _get_today_utc():
        user_usage["inr_reality_check"]["count"] = 0
        user_usage["inr_reality_check"]["reset_date"] = _get_today_utc()
    # Reset monthly counters if needed
    if user_usage["reports"]["reset_month"] != _get_current_month_utc():
        user_usage["reports"]["count"] = 0
        user_usage["reports"]["reset_month"] = _get_current_month_utc()
    return user_usage

def _update_user_usage(user_email: str, user_usage: Dict[str, Any]):
    usage = _load_usage()
    usage[user_email] = user_usage
    _save_usage(usage)

def _check_and_increment_usage(user_email: str, feature: str, plan: str) -> Dict[str, Any]:
    limits = PLAN_LIMITS[plan]
    user_usage = _get_user_usage(user_email)
    
    if feature == "ai_chat":
        limit = limits["ai_chat_daily"]
        current = user_usage["ai_chat"]["count"]
        if limit is not None and current >= limit:
            return {"allowed": False, "current": current, "limit": limit, "feature": "AI Research Chat"}
        user_usage["ai_chat"]["count"] += 1
        _update_user_usage(user_email, user_usage)
        return {"allowed": True, "current": current + 1, "limit": limit, "feature": "AI Research Chat"}
    elif feature == "inr_reality_check":
        limit = limits["inr_reality_check_daily"]
        current = user_usage["inr_reality_check"]["count"]
        if limit is not None and current >= limit:
            return {"allowed": False, "current": current, "limit": limit, "feature": "INR Reality Check"}
        user_usage["inr_reality_check"]["count"] += 1
        _update_user_usage(user_email, user_usage)
        return {"allowed": True, "current": current + 1, "limit": limit, "feature": "INR Reality Check"}
    elif feature == "report":
        limit = limits["reports_monthly"]
        current = user_usage["reports"]["count"]
        if limit is not None and current >= limit:
            return {"allowed": False, "current": current, "limit": limit, "feature": "Report Builder"}
        user_usage["reports"]["count"] += 1
        _update_user_usage(user_email, user_usage)
        return {"allowed": True, "current": current + 1, "limit": limit, "feature": "Report Builder"}
    return {"allowed": True}


# --- AUTH & PLANS ---
USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SESSIONS: Dict[str, Dict] = {} # token -> user_data

def _load_sessions():
    global SESSIONS
    if SESSIONS_FILE.exists():
        try:
            SESSIONS = json.loads(SESSIONS_FILE.read_text())
        except:
            SESSIONS = {}

def _save_sessions():
    try:
        SESSIONS_FILE.write_text(json.dumps(SESSIONS, indent=2))
    except:
        pass

class LoginRequest(BaseModel):
    email: str
    password: str

class SignupRequest(BaseModel):
    email: str
    password: str
    name: str

def _load_users() -> List[Dict]:
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text())
    except:
        return []

def _save_users(users: List[Dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2))

def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    return SESSIONS.get(token)

def check_pro_access(user: Optional[Dict]):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("plan") not in ["pro", "max"]:
        raise HTTPException(status_code=403, detail="Pro plan required for this feature")
    return user

def check_max_access(user: Optional[Dict]):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("plan") != "max":
        raise HTTPException(status_code=403, detail="ORION Max required for this feature")
    return user

@app.get("/api/orion/sector/refresh")
async def trigger_refresh(user: Optional[Dict] = Depends(get_current_user)):
    check_pro_access(user)
    asyncio.create_task(refresh_all_sectors_task())
    return {"status": "refresh_started"}

@app.get("/api/orion/sector/{industry}")
async def get_sector_intel(industry: str):
    from orion_assistant import fetch_sector_benchmarks
    data = await fetch_sector_benchmarks(industry)
    if not data or data.get("status") == "error":
        raise HTTPException(status_code=404, detail=f"Sector '{industry}' not found or data unavailable")
    return data


@app.get("/api/orion/economy")
async def get_economy(user: Optional[Dict] = Depends(get_current_user)):
    check_pro_access(user)
    return await data_router.get_economy_data()


@app.get("/api/orion/etf/{symbol}")
async def get_etf_deep(symbol: str, user: Optional[Dict] = Depends(get_current_user)):
    check_pro_access(user)
    return await data_router.get_etf_deep_analysis(symbol)


@app.get("/api/orion/ticker/{ticker}/intelligence")
async def get_ticker_intelligence(ticker: str, user: Optional[Dict] = Depends(get_current_user)):
    check_max_access(user)
    # This returns literally every data point we can get
    ticker = ticker.upper()
    return await data_router.get_deep_ticker_analysis(ticker)

# --------------------

DEFAULT_UNIVERSE = [
    {"ticker": "NVDA", "name": "NVIDIA Corporation", "sector": "Semiconductors"},
    {"ticker": "AAPL", "name": "Apple Inc", "sector": "Consumer Tech"},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "sector": "Software"},
    {"ticker": "AMZN", "name": "Amazon.com Inc", "sector": "E-Commerce"},
    {"ticker": "META", "name": "Meta Platforms", "sector": "Internet"},
    {"ticker": "GOOGL", "name": "Alphabet Inc", "sector": "Internet"},
    {"ticker": "TSLA", "name": "Tesla Inc", "sector": "Automotive"},
    {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Semiconductors"},
    {"ticker": "NFLX", "name": "Netflix Inc", "sector": "Streaming"},
    {"ticker": "CRM", "name": "Salesforce Inc", "sector": "Software"},
]

CUSTOM_TICKERS: List[Dict] = []
TOP_100: List[Dict] = []


def _cache_get(key: str, ttl: int = 30):
    item = CACHE.get(key)
    if not item:
        return None
    value, ts = item
    if time.time() - ts > ttl:
        del CACHE[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    CACHE[key] = (value, time.time())


def _load_custom_tickers():
    global CUSTOM_TICKERS
    if not CUSTOM_TICKERS_FILE.exists():
        CUSTOM_TICKERS = []
        return
    try:
        CUSTOM_TICKERS = json.loads(CUSTOM_TICKERS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        CUSTOM_TICKERS = []


def _save_custom_tickers():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_TICKERS_FILE.write_text(json.dumps(CUSTOM_TICKERS, indent=2))


def get_universe() -> List[Dict]:
    seen = set()
    result = []
    for c in DEFAULT_UNIVERSE + CUSTOM_TICKERS:
        t = c["ticker"]
        if t not in seen:
            seen.add(t)
            result.append(c)
    return result


def _fmt_cap(cap: Optional[float]) -> str:
    if not cap or cap <= 0:
        return ""
    if cap >= 1e12:
        return f"${cap / 1e12:.2f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.1f}B"
    if cap >= 1e6:
        return f"${cap / 1e6:.1f}M"
    return f"${cap:,.0f}"


# Legacy logic removed for Finnhub migration

# --- Unified Data Router Integration ---

# Limit concurrency for batch operations to prevent server hang
BATCH_SEMAPHORE = asyncio.Semaphore(15)

async def fetch_quotes(tickers: List[str]) -> Dict[str, Dict]:
    """Fetch quotes for multiple tickers using the Data Router."""
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}

    async def fetch_one(t):
        async with BATCH_SEMAPHORE:
            return await data_router.get_ticker_info_basic(t)

    tasks = [fetch_one(t) for t in tickers]
    results = await asyncio.gather(*tasks)
    
    out = {}
    # Initialize with empty data for all tickers to ensure frontend has something
    for t in tickers:
        out[t] = {"ticker": t, "price": None, "changePct": 0, "name": t}

    for r in results:
        if not r or "ticker" not in r: continue
        ticker = r["ticker"]
        # Map router schema back to what legacy parts of the app expect
        # Flatten ratios for easier access in _enrich_pick
        ratios = r.get("ratios", {})
        out[ticker] = {
            **r,
            **ratios, # Flattened ratios
            "changePct": r.get("changePct") or r.get("change_pct") or 0,
            "fiftyTwoWeekHigh": r.get("fiftyTwoWeekHigh") or r.get("fifty_two_week_high"),
            "fiftyTwoWeekLow": r.get("fiftyTwoWeekLow") or r.get("fifty_two_week_low"),
            "peRatio": r.get("peRatio") or r.get("pe_ratio"),
            "marketCap": r.get("marketCap") or r.get("market_cap"),
        }
    return out


async def fetch_quotes_complete(tickers: List[str]) -> Dict[str, Dict]:
    """Fetch complete ticker intelligence for multiple tickers."""
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}

    async def fetch_one(t):
        async with BATCH_SEMAPHORE:
            return await data_router.get_ticker_info(t)

    tasks = [fetch_one(t) for t in tickers]
    results = await asyncio.gather(*tasks)
    
    out = {}
    for t in tickers:
        out[t] = {"ticker": t, "price": None, "changePct": 0, "name": t}

    for r in results:
        if not r or "ticker" not in r: continue
        ticker = r["ticker"]
        ratios = r.get("ratios", {})
        out[ticker] = {
            **r,
            **ratios,
            "changePct": r.get("changePct") or r.get("change_pct") or 0,
            "fiftyTwoWeekHigh": r.get("fiftyTwoWeekHigh") or r.get("fifty_two_week_high"),
            "fiftyTwoWeekLow": r.get("fiftyTwoWeekLow") or r.get("fifty_two_week_low"),
            "peRatio": r.get("peRatio") or r.get("pe_ratio"),
            "marketCap": r.get("marketCap") or r.get("market_cap"),
        }
    return out


async def _fetch_news_async(ticker: str) -> List[Dict]:
    return await data_router.get_news(ticker)


async def _search_async(query: str) -> List[Dict]:
    return await data_router.search_tickers(query)


# --- Legacy functions removed in favor of Data Router ---
# _fetch_quote_openbb removed
# _fetch_news_openbb removed
# _search_openbb removed



def _compute_signals(q: Dict) -> Dict:
    price = q.get("price") or 0
    pe = q.get("peRatio")
    cap = q.get("marketCap") or 0
    hi52 = q.get("fiftyTwoWeekHigh") or 0
    lo52 = q.get("fiftyTwoWeekLow") or 0
    chg_pct = q.get("changePct") or 0

    score = 50
    signals = []

    if pe and pe < 20:
        score += 10
        signals.append({
            "type": "VALUATION",
            "label": f"Attractive P/E of {pe:.1f}x — below market average",
            "confidence": 0.78,
            "severity": "info",
        })
    elif pe and pe > 40:
        score -= 8
        signals.append({
            "type": "VALUATION",
            "label": f"Elevated P/E of {pe:.1f}x — premium priced",
            "confidence": 0.72,
            "severity": "warn",
        })

    if hi52 and lo52 and price:
        range_pct = (price - lo52) / (hi52 - lo52) * 100 if hi52 != lo52 else 50
        if range_pct > 80:
            score += 8
            signals.append({
                "type": "MOMENTUM",
                "label": f"Near 52W high — strong momentum ({range_pct:.0f}th percentile)",
                "confidence": 0.81,
                "severity": "info",
            })
        elif range_pct < 20:
            score -= 5
            signals.append({
                "type": "MOMENTUM",
                "label": "Near 52W low — potential value or continued weakness",
                "confidence": 0.65,
                "severity": "warn",
            })

    if chg_pct and chg_pct > 2:
        score += 6
        signals.append({
            "type": "PRICE ACTION",
            "label": f"Strong session: +{chg_pct:.2f}% today",
            "confidence": 0.70,
            "severity": "info",
        })
    elif chg_pct and chg_pct < -2:
        score -= 6
        signals.append({
            "type": "PRICE ACTION",
            "label": f"Weak session: {chg_pct:.2f}% today — watch for follow-through",
            "confidence": 0.70,
            "severity": "warn",
        })

    if cap and cap > 1e12:
        score += 5
        signals.append({
            "type": "SIZE",
            "label": f"Mega-cap {_fmt_cap(cap)} — institutional liquidity premium",
            "confidence": 0.90,
            "severity": "info",
        })

    # Technical Signals (OpenBB)
    rsi = q.get("rsi")
    if rsi:
        if rsi < 30:
            score += 10
            signals.append({
                "type": "TECHNICAL",
                "label": f"RSI Oversold ({rsi:.1f}) — potential reversal candidate",
                "confidence": 0.85,
                "severity": "info",
            })
        elif rsi > 70:
            score -= 10
            signals.append({
                "type": "TECHNICAL",
                "label": f"RSI Overbought ({rsi:.1f}) — caution on entry",
                "confidence": 0.85,
                "severity": "warn",
            })

    sma_50 = q.get("sma_50")
    if sma_50 and price:
        if price > sma_50:
            score += 5
            signals.append({
                "type": "TECHNICAL",
                "label": "Trading above 50-day SMA — bullish trend confirmed",
                "confidence": 0.75,
                "severity": "info",
            })
        else:
            score -= 5
            signals.append({
                "type": "TECHNICAL",
                "label": "Trading below 50-day SMA — bearish pressure",
                "confidence": 0.75,
                "severity": "warn",
            })

    score = max(10, min(95, score))
    return {"score": score, "signals": signals}


def _signal_score_only(q: Dict) -> float:
    return _compute_signals(q)["score"]


def _recommendation_label(score: float) -> str:
    if score >= 70:
        return "BUY"
    if score >= 55:
        return "HOLD"
    return "SELL"


def _matches_industry(meta: Dict, quote: Dict, industry: str) -> bool:
    needle = industry.lower().strip()
    if not needle:
        return False
    for field in (meta.get("industry"), quote.get("sector"), meta.get("sector")):
        if field and needle in str(field).lower():
            return True
    return False


async def _fetch_top100_quotes() -> Dict[str, Dict]:
    key = "top100:quotes"
    cached = _cache_get(key, 600)
    if cached:
        return cached

    if not TOP_100:
        _load_top100()

    tickers = [s["ticker"] for s in TOP_100]
    merged = await fetch_quotes(tickers)

    by_ticker = {s["ticker"]: s for s in TOP_100}
    out = {}
    for t in tickers:
        q = merged.get(t, {})
        meta = by_ticker.get(t, {})
        if q.get("price") is None:
            continue
        out[t] = {
            **q,
            "ticker": t,
            "name": q.get("name") or meta.get("name") or t,
            "industry": meta.get("industry") or q.get("sector") or "Unknown",
            "peRatio": q.get("peRatio"),
        }
    _cache_set(key, out)
    return out


def _enrich_pick(ticker: str, q: Dict, meta: Dict) -> Dict:
    score = _signal_score_only(q)
    chg = q.get("changePct") or q.get("change_pct") or 0
    return {
        "ticker": ticker,
        "name": q.get("name") or meta.get("name") or ticker,
        "industry": q.get("industry") or meta.get("industry") or q.get("sector") or meta.get("sector") or "Unknown",
        "price": q.get("price"),
        "changePct": chg,
        "marketCap": q.get("marketCap") or q.get("market_cap"),
        "peRatio": q.get("peRatio") or q.get("pe_ratio"),
        "forwardPE": q.get("forwardPE") or q.get("pe_ratio"),
        "pegRatio": q.get("pegRatio") or q.get("peg_ratio"),
        "priceToSales": q.get("priceToSales") or q.get("price_to_sales"),
        "priceToBook": q.get("priceToBook") or q.get("price_to_book"),
        "enterpriseToEbitda": q.get("enterpriseToEbitda") or q.get("ev_to_ebitda"),
        "revenueGrowth": q.get("revenueGrowth") or q.get("revenue_growth"),
        "operatingMargins": q.get("operatingMargins") or q.get("operating_margin"),
        "returnOnEquity": q.get("returnOnEquity") or q.get("roe"),
        "instOwnership": q.get("instOwnership"),
        "shortPercentOfFloat": q.get("shortPercentOfFloat"),
        "rsi": q.get("rsi"),
        "volatility": q.get("volatility"),
        "drawdown": q.get("drawdown"),
        "ret_ytd": q.get("ret_ytd"),
        "ret_1y": q.get("ret_1y"),
        "score": score,
        "recommendation": _recommendation_label(score),
        "rationale": _pick_rationale(q, score),
    }


def _pick_rationale(q: Dict, score: float) -> str:
    pe = q.get("peRatio")
    chg = q.get("changePct") or 0
    parts = []
    if pe and pe < 22:
        parts.append(f"attractive P/E ({pe:.1f}x)")
    elif pe and pe > 35:
        parts.append(f"premium P/E ({pe:.1f}x)")
    if chg > 1.5:
        parts.append(f"+{chg:.1f}% session momentum")
    elif chg < -1.5:
        parts.append(f"{chg:.1f}% pullback")
    hi, lo, price = q.get("fiftyTwoWeekHigh"), q.get("fiftyTwoWeekLow"), q.get("price")
    if hi and lo and price and hi != lo:
        pct = (price - lo) / (hi - lo) * 100
        if pct > 75:
            parts.append("near 52W high")
        elif pct < 30:
            parts.append("near 52W low")
    if not parts:
        parts.append(f"ORION signal {score:.0f}/100")
    return "; ".join(parts).capitalize()


def _build_analysis(q: Dict) -> Dict:
    ticker = q.get("ticker", "")
    price = q.get("price") or 0
    pe = q.get("peRatio")
    fpe = q.get("forwardPE")
    cap = q.get("marketCap") or 0
    hi52 = q.get("fiftyTwoWeekHigh") or 0
    lo52 = q.get("fiftyTwoWeekLow") or 0
    chg_pct = q.get("changePct") or 0
    name = q.get("name") or ticker
    sector = q.get("sector") or "Unknown"
    div = q.get("dividendYield")

    range_pct = (
        (price - lo52) / (hi52 - lo52) * 100
        if hi52 and lo52 and hi52 != lo52
        else 50
    )

    score = 50
    if pe and pe < 20:
        score += 15
    elif pe and pe > 50:
        score -= 15
    if range_pct > 70:
        score += 10
    elif range_pct < 30:
        score += 5
    if chg_pct and chg_pct > 0:
        score += 5
    score = max(10, min(95, score))

    reco = "BUY" if score >= 70 else "HOLD" if score >= 55 else "SELL"
    conviction = round(score / 10)

    bull = [
        f"Trading at {range_pct:.0f}th percentile of 52W range — "
        f"{'near highs showing momentum' if range_pct > 60 else 'potential value entry point'}",
    ]
    if cap and cap > 0:
        bull.append(
            f"Market cap {_fmt_cap(cap)} — "
            f"{'mega-cap stability and institutional coverage' if cap > 1e11 else 'growth profile with room to scale'}"
        )
    if pe and pe < 25:
        bull.append(f"Reasonable trailing P/E of {pe:.1f}x — valuation not stretched")
    if fpe and pe and fpe < pe:
        bull.append(f"Forward P/E of {fpe:.1f}x below trailing {pe:.1f}x — earnings growth expected")
    if div:
        bull.append(f"Dividend yield of {div * 100:.2f}% provides income floor")

    bear = []
    if hi52 and price:
        bear.append(
            f"Near 52W high at ${price:.2f} — limited upside to ${hi52:.2f}"
            if range_pct > 80
            else f"Below 52W high of ${hi52:.2f} — recovery thesis unproven"
        )
    if pe and pe > 30:
        bear.append(f"Elevated P/E of {pe:.1f}x requires continued earnings execution")
    bear.append(f"Macro rate environment poses headwind for {sector} sector valuations")

    cap_str = _fmt_cap(cap) if cap else "N/A"
    pe_str = f"trailing P/E of {pe:.1f}x and " if pe else ""
    thesis = (
        f"{name} ({ticker}) is trading at ${price:.2f}, at the {range_pct:.0f}th percentile "
        f"of its 52-week range (${lo52:.2f}–${hi52:.2f}). "
        f"With {pe_str}market cap {cap_str}, the stock "
        f"{'presents a compelling risk/reward' if score >= 60 else 'warrants caution at current levels'}."
    )

    key_insight = bull[0] if bull else thesis[:200]
    if range_pct > 85:
        key_insight = (
            f"Price is {range_pct:.0f}% through its 52-week range — crowded long; "
            f"limited upside to ${hi52:.2f} unless estimates rise."
        )
    elif range_pct < 25:
        key_insight = (
            f"Near the low end of its 52-week band — asymmetric rebound potential "
            f"toward ${hi52:.2f} if earnings hold."
        )

    entry = lo52 * 1.03 if lo52 else price * 0.9
    if reco == "BUY":
        action = f"Scale in toward ${entry:.2f}; avoid chasing above ${price * 1.04:.2f}."
    elif reco == "SELL":
        action = f"Avoid new money at ${price:.2f}; revisit near ${entry:.2f} only if fundamentals improve."
    else:
        action = f"Hold only at ${price:.2f}; add on pullback toward ${entry:.2f}, not on strength."

    catalysts = [
        f"Next earnings / guidance for {name} (margins, outlook, capital return)",
        f"{sector} sector flows vs interest rates and demand",
        f"Technical: hold above ${lo52:.2f} support; ${hi52:.2f} is the key ceiling" if hi52 and lo52 else "Technical trend vs 20-day average",
        "Estimate revisions and institutional positioning",
    ]

    risks = list(bear)
    if pe and pe > 35:
        risks.append(f"At {pe:.1f}x P/E, multiple compression on any growth scare.")
    if lo52:
        risks.append(f"Close below ${lo52:.2f} invalidates a constructive range view.")
    risks.append("Macro shock (rates, recession) can override stock-specific strength.")

    return {
        "generated_at": str(datetime.utcnow()),
        "ticker": ticker,
        "name": name,
        "score": score,
        "memo": {
            "recommendation": reco,
            "conviction": conviction,
            "thesis": thesis,
            "keyInsight": key_insight,
            "action": action,
            "bull_case": bull,
            "bear_case": bear,
            "catalysts": catalysts,
            "risks": risks[:5],
        },
    }


# ---------- ROUTES ----------


@app.get("/api/orion/macro/strip")
async def get_macro_strip():
    """
    Returns the 5 key macro indicators for the stock page context strip.
    Includes inflation calculation for CPI series.
    """
    indicators = ["DEXINUS", "FEDFUNDS", "DGS10", "VIXCLS", "INDCPIALLMINMEI"]
    results = {}
    
    # 1. Basic indicators
    for sid in ["DEXINUS", "FEDFUNDS", "DGS10", "VIXCLS"]:
        val = await data_router.get_macro_indicator(sid)
        if val and val.get("value") and val["value"] != ".":
            results[sid] = val["value"]
        else:
            # Fallbacks for critical indicators if FRED fails
            fallbacks = {"DEXINUS": "83.50", "FEDFUNDS": "5.33", "DGS10": "4.45", "VIXCLS": "13.50"}
            results[sid] = fallbacks.get(sid)

    # 2. India CPI Inflation calculation
    cpi_latest = await data_router.get_macro_indicator("INDCPIALLMINMEI")
    if cpi_latest and cpi_latest.get("value") and cpi_latest["value"] != ".":
        try:
            curr_val = float(cpi_latest["value"])
            curr_date_str = cpi_latest.get("date") # format "YYYY-MM-DD"
            if curr_date_str:
                curr_date = datetime.strptime(curr_date_str, "%Y-%m-%d")
                # Get value exactly 1 year ago from the observation date
                prev_date = (curr_date - timedelta(days=365)).strftime("%Y-%m-%d")
                # Use a small window around the target date to ensure we find the monthly point
                old_val = await fred_client.get_series_at_date("INDCPIALLMINMEI", prev_date)
                
                if old_val:
                    inflation = ((curr_val / old_val) - 1) * 100
                    results["INDCPIALLMINMEI"] = f"{inflation:.1f}"
                else:
                    results["INDCPIALLMINMEI"] = "5.2" # Reasonable fallback
            else:
                results["INDCPIALLMINMEI"] = "5.2"
        except Exception as e:
            print(f"DEBUG: CPI Calc Error: {e}")
            results["INDCPIALLMINMEI"] = "5.2"
    else:
        results["INDCPIALLMINMEI"] = "5.2"

    return results

@app.get("/api/orion/fx/rate")
async def get_fx_rate(base: str = "USD", target: str = "INR"):
    rate = await data_router.get_fx_rate(base, target)
    return {"base": base, "target": target, "rate": rate}

@app.get("/api/orion/fx/history")
async def get_fx_history(base: str = "USD", target: str = "INR", days: int = 365):
    history = await data_router.get_fx_history(base, target, days=days)
    return {"base": base, "target": target, "history": history}

@app.get("/api/orion/india/macro")
async def get_india_macro():
    indicators = ["DEXINUS", "INDCPIALLMINMEI", "INTDSRINM193N"]
    results = {}
    for sid in indicators:
        val = await data_router.get_macro_indicator(sid)
        results[sid] = val.get("value") if val else None
    return results

@app.post("/api/orion/reality-check")
async def reality_check(payload: Dict[str, Any], user: Optional[Dict] = Depends(get_current_user)):
    """
    Calculates the INR Reality Check data for a stock.
    Supports both historical performance and user-entered buy price.
    """
    # Check that user is authenticated
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Check usage limit
    user_plan = user.get("plan", "free")
    usage_result = _check_and_increment_usage(user["email"], "inr_reality_check", user_plan)
    if not usage_result["allowed"]:
        raise HTTPException(status_code=429, detail=f"{usage_result['feature']} limit reached: {usage_result['current']}/{usage_result['limit']}")
    
    ticker = payload.get("ticker", "AAPL").upper()
    try:
        amount_inr = float(payload.get("amount_inr") or 100000)
    except (ValueError, TypeError):
        amount_inr = 100000
        
    try:
        remitted_inr = float(payload.get("remitted_inr") or 0)
    except (ValueError, TypeError):
        remitted_inr = 0
        
    holding_months = int(payload.get("holding_months") or 12)
    tax_slab = str(payload.get("tax_slab") or "30")
    
    # Custom User Input for Buy Price
    buy_price_usd = None
    if payload.get("buy_price_usd"):
        try:
            buy_price_usd = float(payload.get("buy_price_usd"))
        except (ValueError, TypeError):
            buy_price_usd = None
            
    print(f"DEBUG: Reality Check START | Ticker: {ticker} | INR: {amount_inr} | Remitted: {remitted_inr} | Months: {holding_months} | Buy Price: {buy_price_usd}")

    # 1. Get current FX rate and stock price
    fx_rate = await data_router.get_fx_rate("USD", "INR")
    if not fx_rate:
        print("WARNING: FRED FX rate failed, using fallback 83.5")
        fx_rate = 83.5 
    
    ticker_info = await data_router.get_ticker_info_basic(ticker)
    curr_price_usd = ticker_info.get("price")
    if not curr_price_usd:
        print(f"ERROR: No current price for {ticker}")
        raise HTTPException(status_code=404, detail=f"Price not found for {ticker}")
    
    print(f"DEBUG: Current Context | Price: ${curr_price_usd} | FX: ₹{fx_rate}")

    # 2. Return Calculations (1M, 3M, 6M, 1Y, 3Y)
    periods = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}
    returns_data = []
    
    # Get Nifty 50 current price
    nifty_info = await data_router.get_ticker_info_basic("^NSEI")
    curr_nifty = nifty_info.get("price") or nifty_info.get("open") or nifty_info.get("high")
    nifty_available = True
    if not curr_nifty:
        print("WARNING: Nifty price unavailable, using fallback 23900")
        curr_nifty = 23900 
        nifty_available = False
    
    for label, days in periods.items():
        target_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        # a) Stock return
        # Ensure we don't reuse the same hist_stock object
        hist_stock = await openbb_client.get_historical(ticker, range_str=f"{days}d")
        old_price_usd = None
        if hist_stock and hist_stock.get("closes") and len(hist_stock["closes"]) > 0:
            # Most providers return ascending. [0] is oldest.
            # But let's verify. If [0] date is newer than [-1] date, we need to flip.
            # However, our openbb_client processes them into lists.
            old_price_usd = hist_stock["closes"][0]
            print(f"DEBUG: {ticker} | {label} | Days: {days} | Old Price: ${old_price_usd} | Current: ${curr_price_usd}")
        else:
            old_price_usd = curr_price_usd
            print(f"WARNING: {ticker} | {label} | No history found, using current price.")
            
        usd_ret = ((curr_price_usd / old_price_usd) - 1) * 100 if old_price_usd else 0
        
        # b) FX movement
        old_fx = await fred_client.get_series_at_date("DEXINUS", target_date)
        if not old_fx:
            print(f"  WARNING: No historical FX for {target_date}, using current ₹{fx_rate}")
            old_fx = fx_rate
        else:
            print(f"  DEBUG: FX Rate {days}d ago: ₹{old_fx}")
        
        fx_mov = ((fx_rate / old_fx) - 1) * 100 if old_fx else 0
        inr_ret = ((1 + usd_ret/100) * (1 + fx_mov/100) - 1) * 100

        # c) Nifty comparison
        hist_nifty = await openbb_client.get_historical("^NSEI", range_str=f"{days}d")
        old_nifty = None
        if hist_nifty and hist_nifty.get("closes") and len(hist_nifty["closes"]) > 0:
            old_nifty = hist_nifty["closes"][0]
            print(f"DEBUG: NIFTY | {label} | Old: {old_nifty} | Current: {curr_nifty}")
        else:
            old_nifty = curr_nifty
            print(f"WARNING: NIFTY | {label} | No history found.")
            
        nifty_ret = ((curr_nifty / old_nifty) - 1) * 100 if old_nifty else 0
        
        returns_data.append({
            "label": label,
            "usd_return": usd_ret,
            "inr_return": inr_ret,
            "nifty_return": nifty_ret,
            "fx_impact": inr_ret - usd_ret
        })

    # 3. Custom Calculation if Buy Price Provided
    user_calc = None
    if buy_price_usd:
        # For simplicity, we assume they bought at current FX unless we add a Buy FX field
        # But wait, the user might have bought it long ago.
        # If no buy_fx is provided, we use the FX rate from 'holding_months' ago
        buy_date = (datetime.now() - timedelta(days=holding_months * 30)).strftime("%Y-%m-%d")
        effective_buy_fx = await fred_client.get_series_at_date("DEXINUS", buy_date) or fx_rate
        
        units = (amount_inr / effective_buy_fx) / buy_price_usd
        curr_value_inr = units * curr_price_usd * fx_rate
        
        user_usd_ret = ((curr_price_usd / buy_price_usd) - 1) * 100
        user_fx_mov = ((fx_rate / effective_buy_fx) - 1) * 100
        user_inr_ret = ((1 + user_usd_ret/100) * (1 + user_fx_mov/100) - 1) * 100
        
        user_calc = {
            "buy_price_usd": buy_price_usd,
            "buy_fx_rate": effective_buy_fx,
            "units": units,
            "curr_value_inr": curr_value_inr,
            "usd_return_pct": user_usd_ret,
            "inr_return_pct": user_inr_ret,
            "profit_inr": curr_value_inr - amount_inr
        }
        print(f"DEBUG: User Custom Calc | Units: {units:.4f} | Profit INR: ₹{user_calc['profit_inr']:.2f}")

    # 4. LRS Calculation
    remitted_usd = remitted_inr / fx_rate
    investment_usd = amount_inr / fx_rate
    remaining_lrs = LRS_LIMIT_USD - remitted_usd
    remaining_after_investment = remaining_lrs - investment_usd
    
    lrs_error = None
    lrs_warning = None
    
    # Logic 4) Validation error if remitted > limit
    if remitted_usd > LRS_LIMIT_USD:
        lrs_error = "Amount exceeds annual LRS limit"
    # Logic 3) Exhausted if remitted == limit
    elif remitted_usd >= LRS_LIMIT_USD:
        lrs_error = "You have exhausted your LRS limit for this financial year"
    # Logic 2) Exceeds if investment > remaining
    elif remaining_after_investment < 0:
        exceeds_amount = abs(remaining_after_investment)
        lrs_warning = f"This investment exceeds your remaining LRS limit by ${exceeds_amount:,.0f}"

    # Logic 1) If remitted is 0, remaining_lrs will be full $250,000 automatically
    
    consumption_pct = ((remitted_usd + investment_usd) / LRS_LIMIT_USD) * 100
    
    # 5. Tax Calculation
    is_ltcg = holding_months >= LTCG_THRESHOLD_MONTHS
    # Explicitly state the duration for post-tax calculation (based on holding_months)
    duration_label = f"{holding_months} Months" if holding_months < 12 else f"{holding_months/12:.1f} Years"
    tax_type = "LTCG (12.5%)" if is_ltcg else f"STCG ({tax_slab}%)"
    tax_rate = LTCG_RATE if is_ltcg else TAX_SLABS.get(tax_slab, 0.30)
    
    # 6. Summary Card
    # Map holding_months to the closest return timeframe
    if holding_months <= 2: mapped_label = "1M"
    elif holding_months <= 5: mapped_label = "3M"
    elif holding_months <= 11: mapped_label = "6M"
    elif holding_months <= 35: mapped_label = "1Y"
    else: mapped_label = "3Y"
    
    # Use the returns data matching the mapped period
    base_summary = next((r for r in returns_data if r["label"] == mapped_label), returns_data[-1])
    
    final_inr_ret_pct = user_calc["inr_return_pct"] if user_calc else base_summary["inr_return"]
    final_nifty_ret_pct = base_summary["nifty_return"]
    
    est_profit_inr = (user_calc["profit_inr"] if user_calc else (amount_inr * (final_inr_ret_pct / 100)))
    tax_liability_inr = max(0, est_profit_inr * tax_rate)
    post_tax_return_inr = est_profit_inr - tax_liability_inr
    post_tax_return_pct = (post_tax_return_inr / amount_inr) * 100 if amount_inr else 0
    
    diff_vs_nifty = post_tax_return_pct - final_nifty_ret_pct
    
    # 7. Macro Context for strip
    macro_strip = await get_macro_strip()

    return {
        "ticker": ticker,
        "fx_rate": fx_rate,
        "user_calc": user_calc,
        "returns": returns_data,
        "lrs": {
            "limit_usd": LRS_LIMIT_USD,
            "remitted_usd": remitted_usd,
            "remitted_inr": remitted_inr,
            "investment_usd": investment_usd,
            "investment_inr": amount_inr,
            "remaining_usd": max(0, remaining_lrs),
            "remaining_after_usd": remaining_after_investment,
            "consumption_pct": consumption_pct,
            "error": lrs_error,
            "warning": lrs_warning,
            "is_exhausted": remitted_usd >= LRS_LIMIT_USD
        },
        "tax": {
            "tax_type": tax_type,
            "taxable_gain_inr": est_profit_inr,
            "estimated_tax_inr": tax_liability_inr,
            "post_tax_gain_inr": post_tax_return_inr,
            "duration": duration_label
        },
        "summary": {
            "inr_return_pct": final_inr_ret_pct,
            "nifty_return_pct": final_nifty_ret_pct,
            "post_tax_inr_return_pct": post_tax_return_pct,
            "nifty_available": nifty_available,
            "diff_vs_nifty": diff_vs_nifty,
            "duration": mapped_label,
            "verdict": f"{ticker} {'outperformed' if diff_vs_nifty > 0 else 'underperformed'} the Nifty 50 over a {mapped_label} horizon after currency and estimated tax adjustments."
        },
        "macro": macro_strip
    }

@app.get("/terms")
async def get_terms():
    return FileResponse(BASE_DIR / "terms.html")

@app.get("/privacy")
async def get_privacy():
    return FileResponse(BASE_DIR / "privacy.html")

@app.get("/refund-policy")
async def get_refund_policy():
    return FileResponse(BASE_DIR / "refund-policy.html")

@app.get("/disclaimer")
async def get_disclaimer():
    return FileResponse(BASE_DIR / "disclaimer.html")

@app.get("/pricing")
async def get_pricing():
    return FileResponse(BASE_DIR / "pricing.html")

@app.get("/api/orion/health")
async def health():
    return {
        "status": "ok",
        "universe": len(get_universe()),
        "features": {
            "chat": run_assistant_chat is not None,
            "verdict": build_single_ticker_verdict is not None,
            "brain": "v2",
            "reports": build_stocks_report is not None,
        },
    }

# --- NEW AUTH & BILLING ENDPOINTS ---

def get_password_hash(password: str) -> str:
    """Simple SHA256 hashing for dev environment security."""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return get_password_hash(plain_password) == hashed_password


@app.post("/api/orion/auth/signup")
async def signup(req: SignupRequest):
    users = _load_users()
    if any(u["email"] == req.email for u in users):
        raise HTTPException(status_code=400, detail="Email already registered")
    
    new_user = {
        "id": str(uuid.uuid4()),
        "email": req.email,
        "password": get_password_hash(req.password),
        "name": req.name,
        "plan": "free",
        "currency": "USD",
        "subscription": {
            "status": "none",  # none, active, canceled, past_due
            "plan": "free",
            "billing": "monthly",
            "current_period_end": None,
            "launch_offer": False,
            "payment_method": None,
            "stripe_subscription_id": None,
            "razorpay_subscription_id": None
        },
        "usage": {},
        "created_at": str(datetime.now())
    }
    users.append(new_user)
    _save_users(users)
    
    token = str(uuid.uuid4())
    SESSIONS[token] = new_user
    _save_sessions()
    return {"token": token, "user": {"email": req.email, "name": req.name, "plan": "free"}}

@app.post("/api/orion/auth/login")
async def login(req: LoginRequest):
    users = _load_users()
    # Try both hashed and plain (for backward compatibility during migration)
    user = next((u for u in users if u["email"] == req.email), None)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    stored_password = user.get("password", "")
    if verify_password(req.password, stored_password) or req.password == stored_password:
        # Migration: if plain password matches, hash it now
        if req.password == stored_password and req.password != get_password_hash(req.password):
            user["password"] = get_password_hash(req.password)
            _save_users(users)
            
        token = str(uuid.uuid4())
        SESSIONS[token] = user
        _save_sessions()
        return {"token": token, "user": {"email": user["email"], "name": user["name"], "plan": user["plan"]}}
    
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/orion/auth/me")
async def me(user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        return {"authenticated": False}
    user_plan = user.get("plan", "free")
    return {
        "authenticated": True,
        "user": {
            "email": user["email"],
            "name": user["name"],
            "plan": user_plan
        },
        "features": FEATURES[user_plan]
    }

@app.get("/api/orion/usage")
async def get_usage(user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_usage = _get_user_usage(user["email"])
    user_plan = user.get("plan", "free")
    limits = PLAN_LIMITS[user_plan]
    return {
        "ai_chat": {
            "current": user_usage["ai_chat"]["count"],
            "limit": limits["ai_chat_daily"],
            "reset_date": user_usage["ai_chat"]["reset_date"]
        },
        "inr_reality_check": {
            "current": user_usage["inr_reality_check"]["count"],
            "limit": limits["inr_reality_check_daily"],
            "reset_date": user_usage["inr_reality_check"]["reset_date"]
        },
        "reports": {
            "current": user_usage["reports"]["count"],
            "limit": limits["reports_monthly"],
            "reset_month": user_usage["reports"]["reset_month"]
        }
    }

@app.get("/api/orion/features")
async def get_user_features(user: Optional[Dict] = Depends(get_current_user)):
    user_plan = user.get("plan", "free") if user else "free"
    return {
        "plan": user_plan,
        "features": FEATURES[user_plan]
    }

@app.get("/api/orion/billing/plans")
async def get_plans(currency: Optional[str] = None, billing: str = "yearly"):
    # Auto-detect currency if not provided (default USD)
    if not currency:
        currency = "USD"
    
    # Get launch offer info
    launch_offer = _load_launch_offer()
    is_launch_offer_active = launch_offer["spots_remaining"] > 0
    
    # Get currency symbol
    symbol = "₹" if currency == "INR" else "$"
    
    plans = []
    for plan_id in ["free", "pro", "max"]:
        base_plan = PRICING[currency][plan_id]
        billing_data = base_plan[billing]
        
        # Apply launch offer discount if applicable
        final_price = billing_data["price"]
        original_price = billing_data["price"]
        monthly_eq_price = billing_data.get("monthly_eq", billing_data["price"])
        
        if is_launch_offer_active and plan_id != "free":
            original_price = billing_data["price"]
            discount_factor = 1 - LAUNCH_OFFER_DISCOUNT
            
            # Use exact launch prices as requested
            if currency == "INR":
                if plan_id == "pro":
                    if billing == "monthly":
                        final_price = 479
                        monthly_eq_price = 479
                    else:  # yearly
                        final_price = 4308  # 359 * 12
                        monthly_eq_price = 359
                elif plan_id == "max":
                    if billing == "monthly":
                        final_price = 959
                        monthly_eq_price = 959
                    else:  # yearly
                        final_price = 8628  # 719 *12
                        monthly_eq_price = 719
            else:  # USD
                if plan_id == "pro":
                    if billing == "monthly":
                        final_price = 5.40
                        monthly_eq_price = 5.40
                    else:  # yearly
                        final_price = 50.40  # 4.20 * 12
                        monthly_eq_price = 4.20
                elif plan_id == "max":
                    if billing == "monthly":
                        final_price = 10.80
                        monthly_eq_price = 10.80
                    else:  # yearly
                        final_price = 100.80  # 8.40 * 12
                        monthly_eq_price = 8.40
        
        plans.append({
            "id": plan_id,
            "name": base_plan["name"],
            "price": final_price,
            "original_price": original_price,
            "monthly_eq": monthly_eq_price,
            "currency": currency,
            "symbol": symbol,
            "billing": billing,
            "save_pct": billing_data.get("save_pct", 0),
            "features": FEATURES[plan_id],
            "recommended": plan_id == "pro"
        })
    
    return {
        "currency": currency,
        "billing": billing,
        "launch_offer": {
            "active": is_launch_offer_active,
            "spots_remaining": launch_offer["spots_remaining"],
            "total_spots": LAUNCH_OFFER_TOTAL_SPOTS,
            "discount_pct": LAUNCH_OFFER_DISCOUNT * 100
        },
        "plans": plans
    }

@app.get("/api/orion/billing/launch-offer")
async def get_launch_offer():
    return _load_launch_offer()

class CheckoutRequest(BaseModel):
    plan_id: str
    currency: str = "USD"
    billing: str = "yearly"

@app.post("/api/orion/billing/checkout")
async def create_checkout(req: CheckoutRequest, user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    plan_id = req.plan_id
    currency = req.currency
    billing = req.billing

    if plan_id not in ["pro", "max"]:
        raise HTTPException(status_code=400, detail="Invalid plan")

    # Get pricing
    launch_offer = _load_launch_offer()
    base_plan = PRICING[currency][plan_id]
    billing_data = base_plan[billing]
    is_launch_offer = launch_offer["spots_remaining"] > 0

    # Create checkout session (placeholder logic)
    # In production, integrate Stripe/Razorpay here
    checkout_url = f"/pricing?checkout_success=true"
    
    return {
        "checkout_url": checkout_url,
        "plan_id": plan_id,
        "currency": currency,
        "billing": billing
    }

@app.post("/api/orion/billing/webhook/stripe")
async def stripe_webhook():
    # Stripe webhook handler (placeholder)
    return {"status": "ok"}

@app.post("/api/orion/billing/webhook/razorpay")
async def razorpay_webhook():
    # Razorpay webhook handler (placeholder)
    return {"status": "ok"}

@app.post("/api/orion/billing/upgrade")
async def upgrade(plan: str = Body(..., embed=True), user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if plan not in ["pro", "max"]:
        raise HTTPException(status_code=400, detail="Invalid plan")
        
    users = _load_users()
    for u in users:
        if u["id"] == user["id"]:
            u["plan"] = plan
            user["plan"] = plan
            # Sync to active sessions
            for token, session_user in SESSIONS.items():
                if session_user.get("id") == user["id"]:
                    session_user["plan"] = plan
            break
    _save_users(users)
    _save_sessions()
    return {"status": "success", "plan": plan}

@app.get("/api/orion/billing/subscription")
async def get_subscription(user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    users = _load_users()
    u = next((u for u in users if u["id"] == user["id"]), None)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "plan": u.get("plan", "free"),
        "currency": u.get("currency", "USD"),
        "subscription": u.get("subscription", {}),
        "usage": u.get("usage", {})
    }

@app.post("/api/orion/billing/cancel")
async def cancel_subscription(user: Optional[Dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    users = _load_users()
    for u in users:
        if u["id"] == user["id"]:
            # Mark subscription as canceled (access remains until period end)
            if "subscription" in u:
                u["subscription"]["status"] = "canceled"
            # Sync to session
            for token, session_user in SESSIONS.items():
                if session_user.get("id") == user["id"]:
                    session_user["subscription"] = u.get("subscription", {})
            break
    _save_users(users)
    _save_sessions()
    return {"status": "success"}


@app.get("/")
async def spa():
    index = BASE_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"status": "ORION backend online — place index.html next to main.py"}


@app.get("/api/orion/universe")
async def universe(user: Optional[Dict] = Depends(get_current_user)):
    if user and "watchlist" in user:
        return {"companies": user["watchlist"]}
    return {"companies": get_universe()}


@app.get("/api/orion/universe/add")
async def add_ticker(ticker: str, user: Optional[Dict] = Depends(get_current_user)):
    ticker = ticker.upper().strip()
    if not ticker:
        return {"status": "invalid", "ticker": ticker}

    # If no user, use global CUSTOM_TICKERS (backward compatible)
    if not user:
        existing = {c["ticker"] for c in get_universe()}
        if ticker in existing:
            return {"status": "already_exists", "ticker": ticker}

        q = await fetch_quotes([ticker])
        info = q.get(ticker, {})
        if not info.get("price") and not info.get("name"):
            return {"status": "not_found", "ticker": ticker}

        company = {
            "ticker": ticker,
            "name": info.get("name") or ticker,
            "sector": info.get("sector") or "Unknown",
        }
        CUSTOM_TICKERS.append(company)
        _save_custom_tickers()
        return {"status": "added", "company": company}
    
    # If user is authenticated, use PER-USER watchlist!
    user_plan = user.get("plan", "free")
    user_watchlist = user.get("watchlist", [])
    
    # Check if already exists in user's watchlist
    existing = {c["ticker"] for c in user_watchlist}
    if ticker in existing:
        return {"status": "already_exists", "ticker": ticker}
    
    # Check watchlist limit!
    watchlist_limit = PLAN_LIMITS[user_plan]["watchlist"]
    if watchlist_limit is not None and len(user_watchlist) >= watchlist_limit:
        raise HTTPException(status_code=429, detail=f"Watchlist limit reached: {len(user_watchlist)}/{watchlist_limit}")

    # Fetch ticker info
    q = await fetch_quotes([ticker])
    info = q.get(ticker, {})
    if not info.get("price") and not info.get("name"):
        return {"status": "not_found", "ticker": ticker}
    
    company = {
        "ticker": ticker,
        "name": info.get("name") or ticker,
        "sector": info.get("sector") or "Unknown",
    }
    
    # Add to user's watchlist and save to users.json
    users = _load_users()
    user_idx = next((i for i, u in enumerate(users) if u.get("email") == user["email"]), -1)
    if user_idx != -1:
        if "watchlist" not in users[user_idx]:
            users[user_idx]["watchlist"] = []
        users[user_idx]["watchlist"].append(company)
        _save_users(users)
        # Update the current user object in SESSIONS too!
        for token, sess in SESSIONS.items():
            if sess.get("email") == user["email"]:
                if "watchlist" not in SESSIONS[token]:
                    SESSIONS[token]["watchlist"] = []
                SESSIONS[token]["watchlist"].append(company)
                _save_sessions()
                break
        
    return {"status": "added", "company": company}


# Also update /api/orion/universe to return user's watchlist when authenticated!
@app.get("/api/orion/universe")
async def universe(user: Optional[Dict] = Depends(get_current_user)):
    if user and "watchlist" in user:
        return {"companies": user["watchlist"]}
    return {"companies": get_universe()}


@app.get("/api/orion/search")
async def search(q: str):
    q = (q or "").strip()
    if not q:
        return {"results": []}

    q_upper = q.upper()
    local = [
        c
        for c in get_universe()
        if q_upper in c["ticker"] or q_upper in c["name"].upper()
    ]

    remote = await data_router.search_tickers(q)

    seen = {c["ticker"] for c in local}
    merged = list(local)
    for r in remote:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            merged.append(r)

    # Exact ticker in query (e.g. "NVDA" or "is nvda a buy") — validate via live quote
    tick_guess = re.findall(r"\b[A-Z]{1,5}(?:-[A-Z])?\b", q_upper)
    if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", q_upper):
        tick_guess = [q_upper] + tick_guess
    for sym in tick_guess[:4]:
        if sym in seen:
            continue
        try:
            data = await fetch_quotes([sym])
            info = data.get(sym, {})
            if info.get("price") is not None or info.get("name"):
                merged.insert(
                    0,
                    {
                        "ticker": sym,
                        "name": info.get("name") or sym,
                        "sector": info.get("sector") or "Equity",
                    },
                )
                seen.add(sym)
        except Exception:
            pass

    return {"results": merged[:15]}


@app.get("/api/orion/quotes")
async def quotes(tickers: str):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    data = await fetch_quotes(ticker_list)
    out = []
    for t in ticker_list:
        q = data.get(t, {"ticker": t})
        out.append({
            "ticker": t,
            "price": q.get("price"),
            "change": q.get("change"),
            "changePct": q.get("changePct"),
            "marketCap": q.get("marketCap"),
            "peRatio": q.get("peRatio"),
            "volume": q.get("volume"),
            "fiftyTwoWeekHigh": q.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": q.get("fiftyTwoWeekLow"),
            "name": q.get("name"),
            "sector": q.get("sector"),
        })
    return {"quotes": out}


@app.get("/api/orion/quote/{ticker}")
async def quote(ticker: str):
    data = await fetch_quotes([ticker.upper()])
    q = data.get(ticker.upper(), {})
    if not q:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
    return q


@app.get("/api/orion/chart/{ticker}")
async def chart(ticker: str, rng: str = "6mo"):
    return await data_router.get_historical(ticker.upper(), range_str=rng)


@app.get("/api/orion/dashboard/{ticker}")
async def dashboard(ticker: str, rng: str = "6mo", user: Optional[Dict] = Depends(get_current_user)):
    ticker = ticker.upper()
    
    # 1. Fetch core quote and profile
    q = await data_router.get_ticker_info(ticker)
    
    # 2. Fetch chart
    c = await data_router.get_historical(ticker, range_str=rng)

    # 3. SEC Filings (Pro/Max plan)
    filings = []
    if user and user.get("plan") in ["pro", "max"]:
        filings = await data_router.get_filings(ticker)
    
    # 4. Deep Intelligence (Pro/Max)
    deep_intel = {}
    if user and user.get("plan") in ["pro", "max"]:
        deep_intel = await data_router.get_deep_ticker_analysis(ticker)

    return {
        "quote": q,
        "chart": c,
        "signals": _compute_signals(q),
        "filings": filings,
        "deep_intel": deep_intel,
        "multiples": q.get("ratios", {}),
        "last_updated": datetime.now().isoformat(),
        "filings_locked": not (user and user.get("plan") in ["pro", "max"])
    }


@app.get("/api/orion/analysis/{ticker}")
async def analysis(ticker: str, user: Optional[Dict] = Depends(get_current_user)):
    check_max_access(user)
    ticker = ticker.upper()
    data = await fetch_quotes([ticker])
    q = data.get(ticker, {"ticker": ticker})
    return _build_analysis(q)


@app.get("/api/orion/news")
async def news(ticker: str = None):
    items = await data_router.get_news(ticker.upper() if ticker else None)
    return {"news": items}


@app.get("/api/orion/agents/activity")
async def agents():
    return {
        "events": [
            {
                "agent": "CRAWLER",
                "action": "Synced intelligence via OpenBB Platform",
                "target": "UNIVERSE",
                "level": "info",
                "ts": str(datetime.utcnow()),
            },
            {
                "agent": "SIGNAL",
                "action": "Computed multi-factor signal scores",
                "target": "SELECTED",
                "level": "info",
                "ts": str(datetime.utcnow()),
            },
            {
                "agent": "SYNTHESIS",
                "action": "Built institutional research memo",
                "target": "SELECTED",
                "level": "info",
                "ts": str(datetime.utcnow()),
            },
        ]
    }


@app.get("/api/orion/memo/{ticker}")
async def memo(ticker: str, user: Optional[Dict] = Depends(get_current_user)):
    check_max_access(user)
    return await analysis(ticker, user=user)


# ---------- REPORTS (multi-stock / industry + PDF) ----------


class ReportRequestIn(BaseModel):
    mode: str = Field(..., description="'stocks' or 'industry'")
    tickers: List[str] = Field(default_factory=list)
    industry: Optional[str] = None
    limit: int = Field(default=24, ge=1, le=100)


async def _generate_report_payload(body: ReportRequestIn) -> Dict[str, Any]:
    if build_stocks_report is None or build_industry_report is None:
        return {"error": "Report module unavailable", "report": None}

    mode = (body.mode or "").strip().lower()
    if mode == "stocks":
        tickers = list(dict.fromkeys(t.strip().upper() for t in body.tickers if t and t.strip()))[:25]
        if not tickers:
            return {"error": "Provide at least one ticker", "report": None}
        
        # 1. Fetch complete quotes (Fundamentals + Technicals + Ratios)
        enriched_quotes = await fetch_quotes_complete(tickers)
        
        # 2. Build qualitative analysis for each ticker
        sections_data = []
        for t in tickers:
            q = enriched_quotes.get(t)
            if not q or q.get("price") is None:
                continue
            
            # Fetch news for this ticker
            ticker_news = await _fetch_news_async(t)

            # Synthesize institutional report
            analysis_data = {}
            if synthesize_equity_report:
                try:
                    analysis_data = await synthesize_equity_report(t, q, ticker_news)
                except Exception as e:
                    print(f"SYNTHESIS ERROR {t}: {e}")
            
            # Signal score & Conviction
            sig_data = _compute_signals(q)
            score = sig_data["score"]
            conviction = max(1, min(10, round(score / 10)))

            # Calculate Range Position
            price = q.get("price") or 0
            hi52 = q.get("fiftyTwoWeekHigh") or 0
            lo52 = q.get("fiftyTwoWeekLow") or 0
            range_pct = ((price - lo52) / (hi52 - lo52) * 100) if hi52 and lo52 and hi52 != lo52 else 50
            
            sections_data.append({
                "ticker": t,
                "quote": q,
                "news": ticker_news,
                "analysis": analysis_data,
                "score": score,
                "conviction": conviction,
                "range_pct": range_pct
            })

        if not sections_data:
            return {"error": "Could not load quotes for any ticker", "report": None}
            
        report = build_stocks_report(tickers, sections_data)
        return {"report": report}

    if mode == "industry":
        industry = (body.industry or "").strip()
        from orion_assistant import _SECTOR_PROXIES, fetch_sector_benchmarks
        
        if industry not in _SECTOR_PROXIES:
            return {"error": f"Invalid industry. Must be one of: {', '.join(sorted(_SECTOR_PROXIES.keys()))}", "report": None}
        
        # FORCE REFRESH to bypass any bad cache
        yahoo_intel = await fetch_sector_benchmarks(industry, force_refresh=True)
        if not yahoo_intel or yahoo_intel.get("status") == "error":
            return {"error": f"Failed to fetch live data for {industry}", "report": None}
            
        metrics = yahoo_intel.get("metrics", {})
        yahoo_tickers = yahoo_intel.get("tickers", [])
        
        # 2. Build Sector Stats (Institutional Grade)
        # Fetch quotes for ALL tickers we found to ensure we have data
        constituent_quotes = await fetch_quotes(yahoo_tickers)
        candidates = []
        for t, q in constituent_quotes.items():
            if q.get("price") is not None:
                candidates.append({
                    "ticker": t,
                    "score": _signal_score_only(q),
                    "price": q.get("price"),
                    "changePct": q.get("changePct"),
                    "peRatio": q.get("peRatio"),
                    "name": q.get("name") or t,
                    "marketCap": q.get("marketCap"),
                    "revenueGrowth": q.get("revenueGrowth", 0),
                    "operatingMargins": q.get("operatingMargins", 0),
                    "instOwnership": q.get("instOwnership", 0)
                })

        # SORT BY SCORE to ensure we show meaningful leaders
        candidates = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)

        advancers = [c for c in candidates if (c.get("changePct") or 0) > 0]
        all_pes = [c["peRatio"] for c in candidates if c.get("peRatio") and c["peRatio"] > 0]
        all_growths = [c["revenueGrowth"] for c in candidates if c.get("revenueGrowth")]
        all_scores = [c["score"] for c in candidates if c.get("score")]
        
        # CRITICAL: If candidates is empty, use the industry name itself to fetch at least one quote
        if not candidates:
            fallback_q = await fetch_quotes([industry])
            # (Logic to handle industry-as-ticker if applicable, but usually handled by assistant's fallback)
        
        avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 50
        
        sector_stats = {
            "industry": industry,
            "sampleSize": len(candidates),
            "advancers": len(advancers),
            "decliners": len(candidates) - len(advancers),
            "avgScore": avg_score,
            "sentiment": "BULLISH" if avg_score > 65 else "BEARISH" if avg_score < 45 else "NEUTRAL",
            "breadth": round(len(advancers) / len(candidates) * 100, 1) if candidates else 0,
            "avgChange": round(sum(c.get("changePct", 0) for c in candidates) / len(candidates), 2) if candidates else 0,
            "avgPe": round(sum(all_pes) / len(all_pes), 1) if all_pes else 0,
            "avgGrowth": round(sum(all_growths) / len(all_growths) * 100, 1) if all_growths else 0,
            
            # Direct Yahoo Metrics - Use fallback if missing
            "marketCap": metrics.get("market_cap") or "—",
            "marketWeight": metrics.get("market_weight") or "—",
            "industriesCount": metrics.get("industries_count") or 0,
            "companiesCount": metrics.get("companies_count") or len(candidates),
            "returnProfile": metrics.get("returns", {}),
            "topMovers": [m["ticker"] for m in metrics.get("top_movers", [])] if metrics.get("top_movers") else [c["ticker"] for c in candidates[:5]],
            "holdings": metrics.get("holdings", []) if metrics.get("holdings") else [{"ticker": c["ticker"], "weight": "—"} for c in candidates[:5]],
            "sub_industries": yahoo_intel.get("sub_industries", []),
            "sourceUrl": yahoo_intel.get("source_url"),
            "fetchedAt": metrics.get("timestamp")
        }
        
        # 3. Fetch Benchmark ETFs
        etf_tickers = _INDUSTRY_ETFS.get(industry, [])
        if etf_tickers:
            sector_stats["benchmarkEtfs"] = await fetch_quotes(etf_tickers)

        # 4. Generate Report (Pure Data Mode)
        report = build_industry_report(industry, sector_stats, {}, candidates)
        return {"report": report}

    return {"error": "mode must be 'stocks' or 'industry'", "report": None}


@app.post("/api/orion/report")
async def generate_report(body: ReportRequestIn = Body(...), user: Optional[Dict] = Depends(get_current_user)):
    # Check plan (pro or max)
    user_plan = user.get("plan", "free") if user else "free"
    if user_plan not in ["pro", "max"]:
        raise HTTPException(status_code=403, detail="Pro plan required for this feature")
    
    # Check usage limit
    usage_result = _check_and_increment_usage(user["email"], "report", user_plan)
    if not usage_result["allowed"]:
        raise HTTPException(status_code=429, detail=f"{usage_result['feature']} limit reached: {usage_result['current']}/{usage_result['limit']}")
    
    return await _generate_report_payload(body)


@app.post("/api/orion/report/pdf")
async def generate_report_pdf(body: ReportRequestIn = Body(...), user: Optional[Dict] = Depends(get_current_user)):
    # Check plan (pro or max)
    user_plan = user.get("plan", "free") if user else "free"
    if user_plan not in ["pro", "max"]:
        raise HTTPException(status_code=403, detail="Pro plan required for this feature")
    
    # Check usage limit
    usage_result = _check_and_increment_usage(user["email"], "report", user_plan)
    if not usage_result["allowed"]:
        raise HTTPException(status_code=429, detail=f"{usage_result['feature']} limit reached: {usage_result['current']}/{usage_result['limit']}")
    if report_to_pdf is None:
        return Response(content=b"PDF unavailable", status_code=503)
    payload = await _generate_report_payload(body)
    if payload.get("error") or not payload.get("report"):
        return Response(
            content=(payload.get("error") or "Report failed").encode(),
            status_code=400,
            media_type="text/plain",
        )
    pdf_bytes = report_to_pdf(payload["report"])
    fname = f"orion-report-{datetime.utcnow().strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/orion/report/pdf/render")
async def render_report_pdf(body: dict = Body(...), user: Optional[Dict] = Depends(get_current_user)):
    """Render PDF from an existing report JSON (e.g. client-built report)."""
    check_pro_access(user)
    if report_to_pdf is None:
        return Response(content=b"PDF unavailable", status_code=503)
    report = body.get("report")
    if not report or not report.get("sections"):
        return Response(content=b"Missing report sections", status_code=400)
    pdf_bytes = report_to_pdf(report)
    fname = f"orion-report-{datetime.utcnow().strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- TOP 100 · TRENDING · INDUSTRY ----------


@app.get("/api/orion/top100")
async def top100_list():
    return {"stocks": TOP_100, "count": len(TOP_100)}


@app.get("/api/orion/industries")
async def industries():
    from orion_assistant import _YAHOO_SECTOR_MAP
    return {"industries": sorted(_YAHOO_SECTOR_MAP.keys())}


async def _fetch_top_movers_async(mode: str = "gainers", count: int = 50) -> List[Dict]:
    """Top movers via Data Router."""
    try:
        return await data_router.get_top_movers(mode)
    except Exception as e:
        print(f"ERROR: Failed to fetch {mode}: {e}")
        return []


@app.get("/api/orion/trending")
async def trending(limit: int = 50):
    """Hot picks: real-time top movers (gainers + losers) across the market."""
    limit = max(1, min(limit, 100))
    
    # 1. Fetch real-time gainers and losers
    gainers_task = _fetch_top_movers_async("gainers", count=50)
    most_active_task = _fetch_top_movers_async("losers", count=50)
    
    gainers, actives = await asyncio.gather(gainers_task, most_active_task)
    
    # 2. Merge and deduplicate tickers
    seen = set()
    movers_data = {}
    for p in gainers + actives:
        ticker = p["ticker"]
        if ticker not in seen:
            seen.add(ticker)
            # Store basic mover data (price, change_pct) as fallback
            movers_data[ticker] = p
            
    # 3. Fetch full quotes for these tickers to get fundamentals/scores
    tickers_to_fetch = list(movers_data.keys())
    quotes = await fetch_quotes(tickers_to_fetch)
    
    picks = []
    for t in tickers_to_fetch:
        q = quotes.get(t)
        if q and q.get("price") is not None:
            picks.append(_enrich_pick(t, q, q))
        else:
            # Last resort: use mover data if fetch_quotes failed for this ticker
            m = movers_data[t]
            # Ensure changePct exists for _enrich_pick
            m["changePct"] = m.get("change_pct", 0)
            picks.append(_enrich_pick(t, m, m))
            
    # 4. If we still need more, fallback to TOP_100 quotes
    if len(picks) < 20:
        top100_quotes = await _fetch_top100_quotes()
        meta_by = {s["ticker"]: s for s in TOP_100}
        for t, q in top100_quotes.items():
            if t not in seen and q.get("price") is not None:
                seen.add(t)
                picks.append(_enrich_pick(t, q, meta_by.get(t, {})))

    picks.sort(key=lambda x: abs(x.get("changePct") or 0), reverse=True)
    return {
        "picks": picks[:limit],
        "universe": "Live US Market Movers",
        "updatedAt": str(datetime.utcnow()),
    }


@app.get("/api/orion/recommendations")
async def recommendations(industry: str, limit: int = 24):
    """Industry recommendations ranked by ORION signal score."""
    limit = max(1, min(limit, 50))
    if not industry.strip():
        return {"error": "industry query required", "picks": []}

    # 1. Start with TOP_100
    top100_quotes = await _fetch_top100_quotes()
    meta_by = {s["ticker"]: s for s in TOP_100}
    candidates = []
    seen = set()
    
    for t, q in top100_quotes.items():
        meta = meta_by.get(t, {})
        if _matches_industry(meta, q, industry) and q.get("price") is not None:
            seen.add(t)
            candidates.append(_enrich_pick(t, q, meta))

    # 2. Add more from real-time movers if they match the industry
    gainers = await _fetch_top_movers_async("gainers", count=100)
    movers_to_fetch = []
    for p in gainers:
        ticker = p["ticker"]
        if ticker not in seen:
            # We don't know the industry of these movers yet, so we have to fetch them
            movers_to_fetch.append(ticker)
    
    if movers_to_fetch:
        mover_quotes = await fetch_quotes(movers_to_fetch)
        for t, q in mover_quotes.items():
            if _matches_industry(q, q, industry) and q.get("price") is not None:
                seen.add(t)
                candidates.append(_enrich_pick(t, q, q))

    candidates.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {
        "industry": industry,
        "picks": candidates[:limit],
        "scanned": len(candidates),
        "updatedAt": str(datetime.utcnow()),
    }


# ---------- PORTFOLIO ----------


class PortfolioPositionIn(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    avgCost: float = Field(ge=0)


class PortfolioEvaluateIn(BaseModel):
    positions: List[PortfolioPositionIn] = []


class ChatMessageIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    ticker: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None


async def _evaluate_portfolio(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not positions:
        return {
            "positions": [],
            "summary": {
                "totalValue": 0,
                "totalCost": 0,
                "totalPnL": 0,
                "totalPnLPct": None,
                "dayPnL": 0,
                "positionCount": 0,
            },
            "updatedAt": str(datetime.utcnow()),
        }

    tickers = list({p["ticker"].upper() for p in positions})
    quotes = await fetch_quotes(tickers)

    evaluated: List[Dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    total_day_pnl = 0.0

    for p in positions:
        t = p["ticker"].upper()
        shares = float(p["shares"])
        avg_cost = float(p.get("avgCost", p.get("avg_cost", 0)))
        q = quotes.get(t, {})
        price = q.get("price") or 0.0
        change = q.get("change") or 0.0

        market_value = shares * price
        cost_basis = shares * avg_cost
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else None
        day_pnl = shares * change

        total_value += market_value
        total_cost += cost_basis
        total_day_pnl += day_pnl

        evaluated.append({
            "ticker": t,
            "name": q.get("name") or t,
            "shares": shares,
            "avgCost": avg_cost,
            "price": price,
            "marketValue": market_value,
            "costBasis": cost_basis,
            "unrealizedPnL": unrealized_pnl,
            "unrealizedPnLPct": unrealized_pnl_pct,
            "dayPnL": day_pnl,
            "changePct": q.get("changePct"),
            "weight": 0.0,
        })

    if total_value > 0:
        for row in evaluated:
            row["weight"] = row["marketValue"] / total_value * 100

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else None

    return {
        "positions": evaluated,
        "summary": {
            "totalValue": total_value,
            "totalCost": total_cost,
            "totalPnL": total_pnl,
            "totalPnLPct": total_pnl_pct,
            "dayPnL": total_day_pnl,
            "positionCount": len(evaluated),
        },
        "updatedAt": str(datetime.utcnow()),
    }


# --- News helper removed, using definition above ---


@app.get("/api/orion/verdict/{ticker}")
async def orion_verdict(ticker: str, q: str = ""):
    """
    ORION brain — direct BUY/HOLD/AVOID for one ticker.
    Example: /api/orion/verdict/SNPS?q=is+SNPS+a+good+buy
    """
    if build_single_ticker_verdict is None:
        return {"error": "assistant_unavailable", "reply": "Deploy orion_assistant.py on the server."}
    if not TOP_100:
        _load_top100()
    question = (q or "").strip() or f"Is {ticker.upper()} a good buy?"
    return await build_single_ticker_verdict(
        ticker.upper(),
        question,
        top100=TOP_100 or list(TOP100_EMBED),
        fetch_quotes_fn=fetch_quotes,
        fetch_news_fn=_fetch_news_async,
        compute_signals_fn=_compute_signals,
        build_analysis_fn=_build_analysis,
    )


@app.get("/api/orion/chat/suggestions")
async def chat_suggestions(ticker: Optional[str] = None):
    t = (ticker or "NVDA").upper()
    return {
        "suggestions": [
            f"What is the valuation and risk profile for {t}?",
            f"Summarize {t} with latest news and confidence score",
            f"Compare {t} to industry peers today",
            "What are semiconductor industry trends right now?",
            "Top movers in the top 100 today",
            "Is the P/E attractive and what are key downside risks?",
        ],
    }


@app.get("/api/orion/chat")
async def chat_research_get(message: str, ticker: Optional[str] = None, user: Optional[Dict] = Depends(get_current_user)):
    """GET fallback for clients that cannot POST (or older proxies)."""
    # Check plan first
    user_plan = user.get("plan", "free") if user else "free"
    if user_plan not in ["pro", "max"]:
        raise HTTPException(status_code=403, detail="Pro plan required for this feature")
    
    # Check usage limit
    usage_result = _check_and_increment_usage(user["email"], "ai_chat", user_plan)
    if not usage_result["allowed"]:
        raise HTTPException(status_code=429, detail=f"{usage_result['feature']} limit reached: {usage_result['current']}/{usage_result['limit']}")
    
    return await chat_research(ChatMessageIn(message=message, ticker=ticker), user=user)


@app.post("/api/orion/chat")
async def chat_research(body: ChatMessageIn = Body(...), user: Optional[Dict] = Depends(get_current_user)):
    """AI stock research assistant — live quotes, valuation, risk, news, confidence."""
    # Check plan first
    user_plan = user.get("plan", "free") if user else "free"
    if user_plan not in ["pro", "max"]:
        raise HTTPException(status_code=403, detail="Pro plan required for this feature")
    
    # Check usage limit
    usage_result = _check_and_increment_usage(user["email"], "ai_chat", user_plan)
    if not usage_result["allowed"]:
        raise HTTPException(status_code=429, detail=f"{usage_result['feature']} limit reached: {usage_result['current']}/{usage_result['limit']}")
    
    if run_assistant_chat is None:
        return {
            "error": "assistant_unavailable",
            "reply": "Research assistant module not loaded on server.",
        }

    explicit = (body.ticker or "").upper().strip() or None
    return await run_assistant_chat(
        body.message,
        ticker=explicit,
        history=body.history,
        universe=get_universe(),
        top100=TOP_100 or list(TOP100_EMBED),
        fetch_quotes_fn=fetch_quotes,
        fetch_news_fn=_fetch_news_async,
        compute_signals_fn=_compute_signals,
        build_analysis_fn=_build_analysis,
        fetch_top100_quotes_fn=_fetch_top100_quotes,
    )


@app.post("/api/orion/portfolio/evaluate")
async def portfolio_evaluate(payload: PortfolioEvaluateIn = Body(...), user: Optional[Dict] = Depends(get_current_user)):
    """Live P&L for a list of holdings (shares + average cost per share)."""
    check_pro_access(user)
    positions = [
        {"ticker": p.ticker.upper(), "shares": p.shares, "avgCost": p.avgCost}
        for p in payload.positions
    ]
    return await _evaluate_portfolio(positions)


@app.get("/api/orion/portfolio/evaluate")
async def portfolio_evaluate_get(
    tickers: str,
    shares: str,
    costs: str,
    user: Optional[Dict] = Depends(get_current_user)
):
    """GET fallback: tickers=NVDA,AAPL shares=10,5 costs=100,200"""
    check_pro_access(user)
    t_list = [x.strip().upper() for x in tickers.split(",") if x.strip()]
    s_list = [float(x) for x in shares.split(",") if x.strip()]
    c_list = [float(x) for x in costs.split(",") if x.strip()]
    if len(t_list) != len(s_list) or len(t_list) != len(c_list):
        return {"error": "tickers, shares, and costs must have same length"}
    positions = [
        {"ticker": t, "shares": s, "avgCost": c}
        for t, s, c in zip(t_list, s_list, c_list)
    ]
    return await _evaluate_portfolio(positions)


if __name__ == "__main__":
    import uvicorn

    # Programmatic start avoids the uvicorn Click CLI (fixes Railway crashes in click/core.py).
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
