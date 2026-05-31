"""
ORION AI Stock Research Assistant — Powered by OpenBB Platform.
Optional OpenAI enhancement when OPENAI_API_KEY is set.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_router import data_router
import httpx

# Tickers: 1–5 letters, optional -B / -A share class
_TICKER_PATTERN = re.compile(r"\b[A-Z]{1,5}(?:-[A-Z])?\b")

_FORBIDDEN_NARRATIVE_PHRASES = (
    "market participant",
    "its sector",
    "industry peers",
    "sector-wide growth",
    "standard industry model",
    "none roadmap",
    "institutional rebalancing",
    "operational scale",
)

_WEAK_NARRATIVE_PATTERNS = (
    "industry landscape",
    "market scale",
    "deep customer integration",
    "market-aligned levels",
    "technical milestones",
    "re-rating triggers",
    "value chain",
    "sector demand cycle",
)

_CORP_SUFFIXES = {
    "inc",
    "corp",
    "corporation",
    "company",
    "companies",
    "ltd",
    "limited",
    "plc",
    "holdings",
    "group",
    "co",
    "class",
    "common",
    "stock",
}

_PRODUCT_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "these",
    "those",
    "its",
    "their",
    "inc",
    "corp",
    "corporation",
    "company",
    "group",
    "united",
    "states",
    "north",
    "america",
    "europe",
    "asia",
    "pacific",
}

_THEME_RULES = [
    (("ai", "gpu", "accelerator", "inference", "training", "datacenter", "hyperscaler"), [
        "AI infrastructure demand",
        "hyperscaler capital spending",
        "inference scaling",
    ]),
    (("semiconductor", "chip", "eda", "foundry", "node", "silicon"), [
        "semiconductor design complexity",
        "advanced-node transitions",
        "silicon content growth",
    ]),
    (("cloud", "software", "platform", "saas", "subscription"), [
        "software seat expansion",
        "cloud workload growth",
        "recurring revenue scaling",
    ]),
    (("defense", "government", "federal", "intelligence", "military"), [
        "government program funding",
        "defense modernization budgets",
        "mission-critical software adoption",
    ]),
    (("advertising", "ads", "search", "social", "commerce"), [
        "digital ad demand",
        "commerce conversion growth",
        "user engagement monetization",
    ]),
    (("payments", "fintech", "merchant", "transaction"), [
        "payment volume growth",
        "merchant adoption",
        "consumer transaction activity",
    ]),
    (("drug", "therapy", "biotech", "clinical", "trial", "pharma"), [
        "clinical readouts",
        "drug-launch execution",
        "payer and prescribing uptake",
    ]),
]

_RISK_RULES = [
    (("china", "export", "restriction", "tariff", "sanction"), [
        "China-related export controls",
        "trade-policy restrictions",
    ]),
    (("competition", "competitor", "pricing", "price"), [
        "pricing pressure",
        "competitive share loss",
    ]),
    (("cyclical", "demand", "inventory", "slowdown"), [
        "demand normalization",
        "inventory digestion",
    ]),
    (("regulation", "regulatory", "antitrust", "privacy"), [
        "regulatory intervention",
        "compliance costs",
    ]),
    (("contract", "government"), [
        "program timing risk",
        "contract concentration",
    ]),
]

_STOP_WORDS = frozenset({
    "A", "AN", "THE", "AND", "OR", "FOR", "TO", "OF", "IN", "ON", "AT", "IS", "IT",
    "AI", "US", "UK", "EU", "VS", "EPS", "PE", "CEO", "CFO", "IPO", "ETF", "SEC",
    "BUY", "SELL", "HOLD", "WHY", "HOW", "WHAT", "WHEN", "WHERE", "WHO", "ARE",
    "WAS", "BE", "DO", "IF", "AS", "BY", "UP", "DOWN", "RISK", "NEWS", "STOCK",
    "STOCKS", "MARKET", "PRICE", "TELL", "ABOUT", "COMPARE", "WITH", "FROM",
    "THIS", "THAT", "THESE", "THOSE", "CAN", "WILL", "SHOULD", "WOULD", "COULD",
    "NOT", "BUT", "ALL", "ANY", "MORE", "MOST", "SOME", "THAN", "THEN", "THEM",
    "INTO", "OVER", "ALSO", "JUST", "ONLY", "VERY", "NOW", "NEW", "OLD", "HIGH",
    "LOW", "DAY", "YTD", "QOQ", "YOY", "GDP", "FED", "RATE", "RATES",
})

_INDUSTRY_TOPOLOGY = {
    "Technology": {
        "Software": ["MSFT", "ORCL", "CRM", "NOW", "ADBE"],
        "Semiconductors": ["NVDA", "AVGO", "TSM", "AMD", "ASML"],
        "Hardware & Equipment": ["AAPL", "CSCO", "ANET", "HPQ"],
        "IT Services": ["ACN", "IBM", "INFY"]
    },
    "Financial Services": {
        "Banks": ["JPM", "BAC", "WFC", "C", "HSBC"],
        "Capital Markets": ["GS", "MS", "BLK", "BX"],
        "Payments & Fintech": ["V", "MA", "PYPL", "SQ", "COIN"],
        "Insurance": ["BRK-B", "PGR", "MET", "CB"]
    },
    "Consumer Cyclical": {
        "Automotive": ["TSLA", "TM", "RACE", "F", "GM"],
        "E-Commerce": ["AMZN", "MELI", "PDD", "BABA"],
        "Retail & Apparel": ["HD", "LOW", "NKE", "TJX"],
        "Travel & Hospitality": ["BKNG", "ABNB", "MAR", "HLT"]
    },
    "Communication Services": {
        "Internet Content & Ads": ["META", "GOOGL", "GOOG", "NFLX"],
        "Telecom": ["T", "VZ", "TMUS", "ORAN"],
        "Entertainment": ["DIS", "WBD", "PARA"]
    },
    "Healthcare": {
        "Pharmaceuticals": ["LLY", "NVO", "JNJ", "PFE", "MRK"],
        "Biotechnology": ["AMGN", "VRTX", "GILD", "REGN"],
        "Medical Devices": ["MDT", "ABT", "SYK", "BSX"],
        "Healthcare Services": ["UNH", "ELV", "CVS", "CI"]
    },
    "Industrials": {
        "Aerospace & Defense": ["BA", "LMT", "RTX", "GD", "NOC"],
        "Logistics & Transport": ["UPS", "FDX", "UNP", "CP"],
        "Machinery & Electrical": ["CAT", "DE", "GE", "HON"],
        "Waste & Environment": ["WM", "RSG"]
    },
    "Consumer Defensive": {
        "Beverages & Food": ["KO", "PEP", "MDLZ", "COST"],
        "Household Products": ["PG", "UL", "CL"],
        "Discount Stores": ["WMT", "TGT", "COST"]
    },
    "Energy": {
        "Integrated Oil & Gas": ["XOM", "CVX", "SHEL", "TTE"],
        "Oilfield Services": ["SLB", "HAL", "BKR"],
        "Midstream": ["ET", "KMI", "WMB"]
    },
    "Basic Materials": {
        "Chemicals": ["LIN", "APD", "SHW"],
        "Metals & Mining": ["BHP", "RIO", "FCX", "NEM"],
        "Agricultural Inputs": ["CTVA", "NTR"]
    },
    "Real Estate": {
        "REITs": ["PLD", "AMT", "EQIX", "WELL", "SPG"],
        "Real Estate Services": ["CBRE", "Z"]
    },
    "Utilities": {
        "Electric Utilities": ["NEE", "DUK", "SO", "AEP"],
        "Multi-Utilities": ["SRE", "PEG", "WEC"],
        "Renewable Power": ["BEP", "AY", "HASI"]
    }
}

_SECTOR_COGNITION = {
    "Technology": {
        "overview": "Software, semiconductors, cloud infrastructure, and IT services.",
        "structure": "Platform-driven with strong network effects and high operating leverage.",
        "themes": ["Generative AI ROI", "Cloud workload migration", "Semiconductor sovereignty", "Cybersecurity consolidation"]
    },
    "Financial Services": {
        "overview": "Banking, investment management, insurance, and payment processing.",
        "structure": "Regulated oligopoly with high switching costs and sensitivity to interest rates.",
        "themes": ["Digital banking migration", "Instant payment rails", "Private credit expansion", "Yield curve positioning"]
    },
    "Consumer Cyclical": {
        "overview": "Automotive, e-commerce, retail, and travel services.",
        "structure": "Highly sensitive to consumer confidence and discretionary spending cycles.",
        "themes": ["EV adoption rates", "E-commerce penetration", "Luxury brand resilience", "Travel demand normalization"]
    },
    "Communication Services": {
        "overview": "Internet content, social media, entertainment, and telecommunications.",
        "structure": "Dominated by mega-cap platforms and capital-intensive infrastructure.",
        "themes": ["Digital ad market recovery", "Streaming profitability", "5G monetization", "Content moderation AI"]
    },
    "Healthcare": {
        "overview": "Pharmaceuticals, biotech, medical devices, and healthcare delivery.",
        "structure": "Regulated growth industry with high R&D intensity and patent-based moats.",
        "themes": ["GLP-1/Obesity drug expansion", "Gene therapy", "Medicare reimbursement shifts", "Healthcare AI"]
    },
    "Industrials": {
        "overview": "Aerospace, defense, logistics, and heavy machinery.",
        "structure": "Capital-intensive with long cycle orders and sensitivity to global PMI.",
        "themes": ["Defense modernization", "Supply chain reshoring", "Automation/Robotics", "Infrastructure spending"]
    },
    "Consumer Defensive": {
        "overview": "Consumer staples, food, beverages, and household products.",
        "structure": "Non-cyclical with consistent demand and strong brand pricing power.",
        "themes": ["Private label competition", "Input cost deflation", "Emerging market growth", "Health-conscious shifts"]
    },
    "Energy": {
        "overview": "Oil, gas, and renewable energy production and services.",
        "structure": "Cyclical, capital-intensive with significant geopolitical exposure.",
        "themes": ["Energy transition", "LNG export growth", "Shale consolidation", "Capital discipline"]
    },
    "Basic Materials": {
        "overview": "Chemicals, metals, mining, and agricultural inputs.",
        "structure": "Upstream commodity producers sensitive to global industrial demand.",
        "themes": ["Critical mineral security", "Green steel transition", "Precision agriculture", "Lithium demand cycles"]
    },
    "Real Estate": {
        "overview": "Property ownership, management, and development via REITs.",
        "structure": "Yield-sensitive with contractual cash flows and physical asset moats.",
        "themes": ["Datacenter REIT demand", "Office sector restructuring", "Residential rent growth", "Interest rate pivot"]
    },
    "Utilities": {
        "overview": "Electric, gas, and water services for residential and industrial use.",
        "structure": "Regulated monopolies with stable dividends and high debt levels.",
        "themes": ["Grid modernization", "Renewable integration", "Nuclear renaissance", "AI datacenter power demand"]
    }
}

_SECTOR_MACRO_WEIGHTS = {
    "Technology": ["10Y Treasury Yield", "AI Capex cycles", "Software seat growth", "Foundry utilization"],
    "Financial Services": ["NIM trends", "Yield Curve Slope", "Credit Default Swaps", "Loan growth"],
    "Consumer Cyclical": ["Consumer Confidence", "Personal Savings Rate", "Mortgage Rates", "Inventory levels"],
    "Communication Services": ["Digital Ad spend", "Streaming ARPU", "Spectrum auctions", "User engagement"],
    "Healthcare": ["FDA Approval Cycles", "Medicare rates", "Patent cliffs", "Elective volumes"],
    "Industrials": ["Global PMI", "Infrastructure outlays", "Defense budgets", "Freight rates"],
    "Consumer Defensive": ["Input commodity costs", "Real wage growth", "FX headwinds", "Pricing power"],
    "Energy": ["Crude Oil prices", "OPEC+ quotas", "Refining spreads", "Natural Gas prices"],
    "Basic Materials": ["Industrial metal prices", "Fertilizer spreads", "USD strength", "China PMI"],
    "Real Estate": ["10Y Treasury Yield", "Occupancy rates", "Cap rates", "Construction starts"],
    "Utilities": ["Federal Funds Rate", "EPA standards", "Power demand", "Natural Gas feedstock"]
}

_SECTOR_PROXIES = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Communication Services": "XLC",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU"
}

_SECTOR_CONSTITUENTS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "CRM", "ADBE", "AMD", "INTC"],
    "Financial Services": ["JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "BLK", "AXP", "SPGI"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "LOW", "SBUX", "BKNG", "TJX", "NKE", "NVR"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "TMUS", "VZ", "T", "CHTR", "CMCSA"],
    "Healthcare": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN"],
    "Industrials": ["GE", "CAT", "UNP", "HON", "UPS", "RTX", "LMT", "DE", "BA", "MMM"],
    "Consumer Defensive": ["PG", "WMT", "COST", "KO", "PEP", "PM", "EL", "MO", "ADM", "TGT"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "WMB", "HES"],
    "Basic Materials": ["LIN", "SHW", "APD", "FCX", "CTVA", "NEM", "ECL", "DOW", "ALB", "VMC"],
    "Real Estate": ["AMT", "PLD", "EQIX", "CCI", "PSA", "DLR", "O", "VICI", "SBAC", "WY"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "D", "AEP", "SRE", "PCG", "PEG", "ED"]
}


SECTOR_DATA_FILE = Path(__file__).parent / "data" / "sector_intelligence.json"

def _normalize_percentage(val: Optional[str]) -> Optional[float]:
    if not val: return None
    try:
        clean = val.replace("%", "").replace("+", "").replace(",", "").strip()
        return float(clean)
    except:
        return None

def _normalize_market_cap(val: Optional[str]) -> Optional[float]:
    if not val: return None
    try:
        clean = val.replace("$", "").replace(",", "").strip()
        multiplier = 1.0
        if clean.endswith("T"): multiplier = 1e12
        elif clean.endswith("B"): multiplier = 1e9
        elif clean.endswith("M"): multiplier = 1e6
        
        num_part = re.search(r"[\d\.]+", clean)
        if num_part:
            return float(num_part.group()) * multiplier
    except:
        pass
    return None

async def fetch_sector_benchmarks(industry: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Institutional sector intelligence via Data Router.
    Uses proxy ETFs and constituent aggregation.
    """
    proxy = _SECTOR_PROXIES.get(industry)
    if not proxy:
        for k, v in _SECTOR_PROXIES.items():
            if industry.lower() in k.lower():
                proxy = v
                industry = k
                break
    
    if not proxy:
        return {"tickers": [], "metrics": {}, "sub_industries": []}

    # Caching Layer (4 hour)
    if not force_refresh and SECTOR_DATA_FILE.exists():
        try:
            cache = json.loads(SECTOR_DATA_FILE.read_text())
            if industry in cache:
                data = cache[industry]
                ts = datetime.fromisoformat(data["metrics"]["timestamp"])
                if (datetime.utcnow() - ts).total_seconds() < 14400:
                    return data
        except:
            pass

    try:
        # Fetch proxy ETF data via Data Router
        etf_info = await data_router.get_ticker_info(proxy, force_refresh=force_refresh)
        
        mcap_val = etf_info.get('market_cap')
        mcap_str = f"${mcap_val:,.0f}" if mcap_val is not None else "—"
        
        metrics = {
            "timestamp": str(datetime.utcnow()),
            "market_cap": mcap_str,
            "market_weight": "Proxy via " + proxy,
            "industries_count": 0,
            "companies_count": len(_SECTOR_CONSTITUENTS.get(industry, [])),
            "returns": {
                "day_return": f"{etf_info.get('change_pct', 0):+.2f}%",
                "ytd_return": f"{etf_info.get('returns', {}).get('YTD', 0)*100:+.2f}%" if etf_info.get('returns', {}).get('YTD') else "—",
                "1y_return": f"{etf_info.get('returns', {}).get('1Y', 0)*100:+.2f}%" if etf_info.get('returns', {}).get('1Y') else "—",
            }
        }
        
        # Add raw returns for internal sorting/logic
        for k in list(metrics["returns"].keys()):
            val = metrics["returns"][k]
            metrics["returns"][f"{k}_raw"] = _normalize_percentage(val)

        tickers = _SECTOR_CONSTITUENTS.get(industry, [])
        
        result = {
            "industry": industry,
            "tickers": tickers,
            "metrics": metrics,
            "sub_industries": [], 
            "status": "success"
        }
        
        # Save to cache
        cache = {}
        if SECTOR_DATA_FILE.exists():
            try: cache = json.loads(SECTOR_DATA_FILE.read_text())
            except: pass
        cache[industry] = result
        SECTOR_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_DATA_FILE.write_text(json.dumps(cache))
        
        return result
        
    except Exception as e:
        print(f"ROUTER SECTOR ERROR {industry}: {e}")
        return {"tickers": [], "metrics": {}, "sub_industries": [], "status": "error"}
_INDUSTRY_NORMALIZATION = {
    "Information Technology": "Technology",
    "Technology": "Technology",
    "Financials": "Financial Services",
    "Financial Services": "Financial Services",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Cyclical": "Consumer Cyclical",
    "Communication Services": "Communication Services",
    "Healthcare": "Healthcare",
    "Industrials": "Industrials",
    "Consumer Staples": "Consumer Defensive",
    "Consumer Defensive": "Consumer Defensive",
    "Energy": "Energy",
    "Materials": "Basic Materials",
    "Basic Materials": "Basic Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities"
}

def normalize_industry_name(name: str) -> str:
    if not name: return "Unknown"
    name = name.strip()
    return _INDUSTRY_NORMALIZATION.get(name, name)


_INDUSTRY_ETFS = {
    "Technology": ["XLK", "QQQ", "VGT"],
    "Financial Services": ["XLF", "VFH", "KBE"],
    "Consumer Cyclical": ["XLY", "VCR"],
    "Communication Services": ["XLC", "VOX"],
    "Healthcare": ["XLV", "IBB", "VHT"],
    "Industrials": ["XLI", "VIS", "ITA"],
    "Consumer Defensive": ["XLP", "VDC"],
    "Energy": ["XLE", "VDE", "XOP"],
    "Basic Materials": ["XLB", "VAW", "XME"],
    "Real Estate": ["XLRE", "VNQ"],
    "Utilities": ["XLU", "VPU"]
}

# Common names → tickers (improves “is Synopsys a good buy?”)
_COMPANY_ALIASES = {
    "synopsys": "SNPS",
    "nvidia": "NVDA",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "amd": "AMD",
    "intel": "INTC",
    "netflix": "NFLX",
    "salesforce": "CRM",
    "palantir": "PLTR",
    "coinbase": "COIN",
    "uber": "UBER",
    "broadcom": "AVGO",
    "jpmorgan": "JPM",
    "berkshire": "BRK-B",
}

_VERDICT_PATTERNS = (
    r"good\s+buy",
    r"bad\s+buy",
    r"should\s+i\s+buy",
    r"should\s+i\s+sell",
    r"should\s+i\s+hold",
    r"worth\s+buying",
    r"worth\s+it",
    r"buy\s+now",
    r"good\s+investment",
    r"invest\s+in",
    r"\ba\s+buy\b",
    r"\ba\s+sell\b",
    r"your\s+(take|view|opinion|call)",
    r"what\s+do\s+you\s+think",
    r"would\s+you\s+buy",
    r"recommend",
    r"too\s+late\s+to",
    r"still\s+buy",
    r"good\s+entry",
)


def _fmt_cap(cap: Optional[float]) -> str:
    if not cap or cap <= 0:
        return "N/A"
    if cap >= 1e12:
        return f"${cap / 1e12:.2f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.1f}B"
    if cap >= 1e6:
        return f"${cap / 1e6:.1f}M"
    return f"${cap:,.0f}"


def _clean_company_field(value: Any) -> Optional[str]:
    if value in (None, "", "null", "undefined", "N/A", "Unknown", "-", "—"):
        return None
    text = str(value).strip()
    return text or None


def _dedupe_strings(items: List[str], limit: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        text = " ".join(str(item or "").strip().split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit and len(out) >= limit:
            break
    return out


def _split_sentences(text: str) -> List[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]


def _sentence_fragment(text: str, limit: int = 220) -> Optional[str]:
    sentences = _split_sentences(text)
    if not sentences:
        return None
    sentence = sentences[0].strip()
    sentence = sentence.rstrip(".")
    return sentence[:limit].strip() or None


def _extract_product_candidates(*texts: str) -> List[str]:
    candidates: List[str] = []
    patterns = [
        r"\b[A-Z]{2,}(?:[-/][A-Z0-9]{1,})?\b",
        r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z0-9][a-zA-Z0-9.+/-]+){0,2}\b",
        r"\b[A-Z0-9]{2,}[A-Za-z0-9.-]*\b",
    ]
    for text in texts:
        if not text:
            continue
        for pattern in patterns:
            for match in re.findall(pattern, text):
                token = " ".join(match.split()).strip(" ,.;:()[]")
                if len(token) < 3:
                    continue
                words = [w.lower() for w in re.split(r"[\s/-]+", token) if w]
                if words and all(w in _PRODUCT_STOPWORDS for w in words):
                    continue
                if token.lower() in _CORP_SUFFIXES:
                    continue
                candidates.append(token)
    return _dedupe_strings(candidates, limit=8)


def _infer_business_model(summary: str) -> Optional[str]:
    lower = summary.lower()
    if not lower:
        return None
    if "subscription" in lower or "saas" in lower:
        return "subscription software and platform revenue"
    if "license" in lower or "licensing" in lower:
        return "software licensing and recurring maintenance revenue"
    if "advertis" in lower:
        return "advertising-driven monetization"
    if "marketplace" in lower or "transaction" in lower:
        return "transaction and marketplace fee revenue"
    if "manufactur" in lower and "sell" in lower:
        return "product sales tied to hardware shipments"
    if "cloud" in lower or "platform" in lower:
        return "platform and cloud-service monetization"
    if "provides" in lower or "develops" in lower or "offers" in lower:
        fragment = _sentence_fragment(summary)
        return fragment.lower() if fragment else None
    return None


def _infer_core_business(summary: str, industry: Optional[str], sector: Optional[str]) -> Optional[str]:
    fragment = _sentence_fragment(summary)
    if fragment:
        lowered = fragment.lower()
        match = re.search(
            r"\b(?:provides|develops|designs|manufactures|offers|operates|sells|delivers)\b\s+(.*)",
            lowered,
        )
        if match:
            core = match.group(1)
            core = re.split(r"\b(?:in|throughout|across)\b", core, 1)[0].strip(" .")
            if len(core) > 8:
                return core
        if len(fragment) > 12:
            return fragment
    return industry or sector


def _collect_rule_matches(text: str, rules: List[tuple]) -> List[str]:
    lower = text.lower()
    out: List[str] = []
    for keywords, values in rules:
        if any(keyword in lower for keyword in keywords):
            out.extend(values)
    return _dedupe_strings(out, limit=6)


def _infer_competitive_advantages(summary: str, products: List[str]) -> List[str]:
    lower = summary.lower()
    advantages: List[str] = []
    if "ecosystem" in lower:
        advantages.append("ecosystem lock-in")
    if "proprietary" in lower or "patent" in lower or "ip" in lower:
        advantages.append("proprietary IP")
    if "platform" in lower:
        advantages.append("platform integration")
    if "subscription" in lower or "recurring" in lower:
        advantages.append("recurring revenue base")
    if "network" in lower:
        advantages.append("network effects")
    if "switching cost" in lower or "mission-critical" in lower:
        advantages.append("high switching costs")
    if products:
        advantages.append(f"product depth around {products[0]}")
    return _dedupe_strings(advantages, limit=4)


def _infer_strategic_role(
    core_business: Optional[str],
    thematics: List[str],
    industry: Optional[str],
    products: List[str],
) -> Optional[str]:
    if thematics and products:
        return f"direct exposure to {thematics[0].lower()} through {products[0]}"
    if thematics and core_business:
        return f"a levered way to express {thematics[0].lower()} via {core_business}"
    if core_business and industry:
        return f"a differentiated operator inside {industry} through {core_business}"
    return core_business or industry


def _merge_intelligence(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, list):
            merged[key] = _dedupe_strings(value, limit=6) if value else merged.get(key, [])
        elif value not in (None, "", "null", "undefined"):
            merged[key] = value
    return merged


def _build_heuristic_company_intelligence(
    ticker: str,
    normalized_data: Dict[str, Any],
    news: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    summary = normalized_data.get("businessSummary", "") or ""
    news_titles = [n.get("title", "") for n in (news or []) if n.get("title")]
    source_text = " ".join([summary] + news_titles)
    industry = normalized_data.get("industry")
    sector = normalized_data.get("sector")
    products = _extract_product_candidates(summary, *news_titles)
    thematics = _collect_rule_matches(source_text, _THEME_RULES)
    risks = _collect_rule_matches(source_text, _RISK_RULES)
    advantages = _infer_competitive_advantages(summary, products)
    core_business = _infer_core_business(summary, industry, sector)
    business_model = _infer_business_model(summary)
    strategic_role = _infer_strategic_role(core_business, thematics, industry, products)
    growth_drivers = _dedupe_strings(thematics + products[:2], limit=4)
    bear_factors = _dedupe_strings(risks[:3], limit=3)
    bull_factors = _dedupe_strings(advantages + growth_drivers, limit=3)

    return {
        "company": normalized_data.get("name") or ticker,
        "ticker": ticker,
        "industry": industry,
        "sector": sector,
        "core_business": core_business,
        "business_model": business_model,
        "strategic_role": strategic_role,
        "key_products": products,
        "growth_drivers": growth_drivers,
        "thematic_exposure": ", ".join(growth_drivers[:3]) if growth_drivers else None,
        "risks": risks,
        "competitive_advantages": advantages,
        "competitive_moat": "; ".join(advantages[:2]) if advantages else None,
        "main_competitors": [],
        "bull_factors": bull_factors,
        "bear_factors": bear_factors,
        "news_themes": _dedupe_strings(news_titles[:4], limit=4),
    }


def _format_metric_sentence(label: str, value: Optional[str], suffix: str = "") -> Optional[str]:
    if not value:
        return None
    return f"{label} {value}{suffix}."


def _list_clause(items: List[str], prefix: str, max_items: int = 3) -> Optional[str]:
    values = _dedupe_strings(items, limit=max_items)
    if not values:
        return None
    return f"{prefix}{', '.join(values)}."


def _compose_sentences(*sentences: Optional[str]) -> str:
    cleaned: List[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        text = sentence.strip()
        if not text:
            continue
        if text[-1] not in ".!?":
            text += "."
        cleaned.append(text)
    return " ".join(cleaned)


def _specificity_tokens(ticker: str, normalized: Dict[str, Any], intel: Dict[str, Any]) -> List[str]:
    raw_tokens: List[str] = [ticker.lower()]
    for value in (
        normalized.get("name"),
        normalized.get("industry"),
        normalized.get("sector"),
        intel.get("core_business"),
        intel.get("business_model"),
        intel.get("thematic_exposure"),
        intel.get("competitive_moat"),
    ):
        if value:
            raw_tokens.extend(re.findall(r"[a-zA-Z0-9][a-zA-Z0-9.+/-]{3,}", str(value).lower()))
    for key in ("key_products", "growth_drivers", "risks", "competitive_advantages", "main_competitors"):
        for item in intel.get(key, []) or []:
            raw_tokens.extend(re.findall(r"[a-zA-Z0-9][a-zA-Z0-9.+/-]{3,}", str(item).lower()))
    return _dedupe_strings(raw_tokens, limit=30)


def _is_generic_narrative(
    text: Optional[str],
    *,
    ticker: str,
    normalized: Dict[str, Any],
    intel: Dict[str, Any],
) -> bool:
    if not text or not text.strip():
        return True
    lower = text.lower()
    if any(phrase in lower for phrase in _FORBIDDEN_NARRATIVE_PHRASES):
        return True
    if any(pattern in lower for pattern in _WEAK_NARRATIVE_PATTERNS):
        return True
    if "sector" in lower and not normalized.get("sector"):
        return True
    tokens = _specificity_tokens(ticker, normalized, intel)
    specific_hits = sum(1 for token in tokens if token and token in lower)
    if specific_hits == 0:
        return True
    if len(lower.split()) < 12:
        return True
    return False


def _build_deterministic_equity_report(
    ticker: str,
    normalized: Dict[str, Any],
    intel: Dict[str, Any],
) -> Dict[str, Any]:
    name = normalized.get("name") or ticker
    industry = normalized.get("industry") or normalized.get("sector") or "Technology"
    
    # Extract description from metadata
    summary = normalized.get("description") or normalized.get("businessSummary") or ""
    
    # If intel is missing, use summary to extract a core business description
    if not intel and summary:
        first_sent = summary.split('.')[0]
        if len(first_sent) > 20:
            core_business = first_sent
        else:
            core_business = f"a specialized provider within the {industry} industry"
    else:
        core_business = intel.get("core_business") or f"a key player in the {industry} sector"

    business_model = intel.get("business_model") or f"High-value {industry} solutions and specialized services."
    moat = intel.get("moat") or f"Deep domain expertise and technical integration in {industry} ecosystems."
    thematic = intel.get("thematic_exposure") or f"Growth in {industry} and digital transformation trends."
    
    financial_sentences = [
        _format_metric_sentence("Revenue growth is currently at", normalized.get("revenueGrowth")),
        _format_metric_sentence("Operating margin stands at", normalized.get("operatingMargins")),
        _format_metric_sentence("Return on Equity (ROE) is", normalized.get("returnOnEquity")),
        _format_metric_sentence("The stock trades at a P/E of", normalized.get("peRatio")),
        _format_metric_sentence("Institutional ownership is", normalized.get("instOwnership")),
    ]

    return {
        "executive_summary": _compose_sentences(
            f"{name} is best understood as {core_business}.",
            f"The company plays a strategic role in the {industry} ecosystem, focused on long-term value creation.",
            "The investment thesis is anchored in its market leadership and ability to capture structural growth."
        ),
        "business_overview": _compose_sentences(
            f"The company's business model is centered on {business_model}.",
            f"It maintains a focus on high-margin product delivery and customer success within {industry}."
        ),
        "competitive_positioning": _compose_sentences(
            f"{name} operates in a complex competitive environment in the {industry} space.",
            f"Its competitive moat is primarily driven by {moat}.",
            "High switching costs and deep integration provide significant barriers to entry for competitors."
        ),
        "financial_quality": _compose_sentences(*financial_sentences),
        "growth_drivers": _compose_sentences(
            f"Primary growth catalysts include {thematic}.",
            "Continuous innovation and market expansion are expected to drive future revenue scaling."
        ),
        "risks": _compose_sentences(
            "Primary downside factors include macroeconomic volatility, competitive intensity, and potential regulatory changes.",
            "Risk mitigation is focused on product diversification and sustained R&D investment."
        ),
        "catalysts": _compose_sentences(
            "Upcoming catalysts include quarterly earnings milestones and the rollout of new technical capabilities.",
            "A re-rating trigger could emerge from margin expansion or significant market share gains."
        )
    }


def _sanitize_equity_report(
    report: Dict[str, Any],
    *,
    ticker: str,
    normalized: Dict[str, Any],
    intel: Dict[str, Any],
) -> Dict[str, Any]:
    cleaned = dict(report or {})
    fallback = _build_deterministic_equity_report(ticker, normalized, intel)
    for key in (
        "executive_summary",
        "business_overview",
        "competitive_positioning",
        "financial_quality",
        "growth_drivers",
        "risks",
        "catalysts",
        "intelligence_summary",
    ):
        value = cleaned.get(key)
        if _is_generic_narrative(value, ticker=ticker, normalized=normalized, intel=intel):
            cleaned[key] = fallback.get(key, "")
    cleaned["bull_factors"] = _dedupe_strings(
        cleaned.get("bull_factors") or fallback.get("bull_factors") or intel.get("bull_factors", []),
        limit=3,
    )
    cleaned["bear_factors"] = _dedupe_strings(
        cleaned.get("bear_factors") or fallback.get("bear_factors") or intel.get("bear_factors", []),
        limit=3,
    )
    return cleaned


def extract_tickers(
    query: str,
    explicit: Optional[str] = None,
    universe: Optional[List[Dict]] = None,
    top100: Optional[List[Dict]] = None,
) -> List[str]:
    if explicit:
        t = explicit.upper().strip()
        if t:
            return [t]

    found: List[str] = []
    q_upper = query.upper()
    pool = (universe or []) + (top100 or [])
    name_to_ticker = {}
    for item in pool:
        tick = item.get("ticker", "").upper()
        if tick:
            name_to_ticker[tick] = tick
            nm = (item.get("name") or "").upper()
            if nm:
                name_to_ticker[nm] = tick
                for word in nm.split():
                    if len(word) > 3:
                        name_to_ticker[word] = tick

    for m in _TICKER_PATTERN.finditer(q_upper):
        sym = m.group()
        if sym in _STOP_WORDS:
            continue
        if sym not in found:
            found.append(sym)

    q_lower = query.lower()
    for alias, tick in _COMPANY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", q_lower) and tick not in found:
            found.append(tick)
    for name, tick in name_to_ticker.items():
        if len(name) > 4 and name.lower() in query.lower():
            if tick not in found:
                found.append(tick)

    return found[:4]


def detect_industry(query: str) -> Optional[str]:
    q = query.lower()
    
    # Map common search terms to our 11 sectors
    mapping = {
        "tech": "Technology",
        "software": "Technology",
        "semi": "Technology",
        "chip": "Technology",
        "finance": "Financial Services",
        "bank": "Financial Services",
        "insur": "Financial Services",
        "payment": "Financial Services",
        "cyclical": "Consumer Cyclical",
        "auto": "Consumer Cyclical",
        "retail": "Consumer Cyclical",
        "ecommerce": "Consumer Cyclical",
        "communication": "Communication Services",
        "telecom": "Communication Services",
        "media": "Communication Services",
        "internet": "Communication Services",
        "health": "Healthcare",
        "pharma": "Healthcare",
        "biotech": "Healthcare",
        "industrial": "Industrials",
        "aerospace": "Industrials",
        "defense": "Industrials",
        "staple": "Consumer Defensive",
        "defensive": "Consumer Defensive",
        "energy": "Energy",
        "oil": "Energy",
        "gas": "Energy",
        "material": "Basic Materials",
        "mining": "Basic Materials",
        "chemical": "Basic Materials",
        "real estate": "Real Estate",
        "reit": "Real Estate",
        "utility": "Utilities",
        "power": "Utilities"
    }
    
    for term, industry in mapping.items():
        if term in q:
            return industry
            
    return None


def is_verdict_question(query: str) -> bool:
    q = query.lower()
    return any(re.search(p, q) for p in _VERDICT_PATTERNS)


def classify_intent(query: str, tickers: List[str]) -> str:
    q = query.lower()
    if any(w in q for w in ("compare", "versus", " vs ", " vs.", "better than")):
        return "compare"
    if tickers and is_verdict_question(query):
        return "verdict"
    if tickers:
        if re.search(r"\bnews\b", q) and not re.search(
            r"\b(valuation|risk|research|summar|analy|outlook)\b", q
        ):
            return "news"
        if re.search(r"\b(risk|downside|volatility)\b", q) and not re.search(
            r"\b(valuation|summar|research|overview)\b", q
        ):
            return "risk"
        if re.search(r"\b(valuation|p/e| pe |expensive|cheap|fair value)\b", q) and not re.search(
            r"\b(risk|news)\b", q
        ):
            return "valuation"
        return "research"
    if any(w in q for w in ("industry", "sector", "trend", "peers", "landscape")):
        return "industry"
    if detect_industry(query):
        return "industry"
    if any(w in q for w in ("market", "today", "movers", "trending", "hot")):
        return "market"
    if any(w in q for w in ("risk", "downside", "volatility")):
        return "risk"
    if any(w in q for w in ("valuation", "news")):
        return "valuation" if "valuation" in q or "p/e" in q else "news"
    return "general"


def _calculate_technical_indicators(ticker: str) -> Dict[str, Any]:
    """Removed in favor of Data Router technicals."""
    return {}


async def enrich_fundamentals_router(ticker: str, base: Dict[str, Any]) -> Dict[str, Any]:
    """Async fundamentals enrichment using Data Router (OpenBB)."""
    out = dict(base)
    ticker = ticker.upper()
    try:
        info = await data_router.get_ticker_info(ticker)
        
        # Core Market Data
        out.update({
            "peRatio": info.get("pe_ratio"),
            "marketCap": info.get("market_cap"),
            "name": info.get("name") or out.get("name"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "dividendYield": info.get("dividend_yield"),
            "rsi": info.get("rsi"),
            "sma_50": info.get("sma_50"),
            "targetMeanPrice": info.get("target_mean"),
            "recommendation": info.get("recommendation"),
            "businessSummary": info.get("description"),
        })
        
        # Ratios
        ratios = info.get("ratios", {})
        out.update({
            "operatingMargins": ratios.get("operating_margin"),
            "revenueGrowth": ratios.get("revenue_growth"),
            "returnOnEquity": ratios.get("roe"),
            "debtToEquity": ratios.get("debt_to_equity"),
            "pegRatio": ratios.get("peg_ratio"),
            "epsTTM": ratios.get("eps_ttm"),
        })
        
        # Returns
        rets = info.get("returns", {})
        out.update({
            "ret_1d": rets.get("1D"),
            "ret_1m": rets.get("1M"),
            "ret_1y": rets.get("1Y"),
            "ret_5y": rets.get("5Y"),
        })
        
        return out
    except Exception as e:
        print(f"ROUTER FUNDAMENTALS ERROR {ticker}: {e}")
        return base


async def _enrich_fundamentals_async(ticker: str, base: Dict[str, Any]) -> Dict[str, Any]:
    """Async Data Router fundamentals enrichment."""
    try:
        info = await data_router.get_ticker_info(ticker)
        
        out = dict(base)
        
        # Map fields from Router
        out.update({
            "peRatio": info.get("pe_ratio"),
            "marketCap": info.get("market_cap"),
            "operatingMargins": info.get("ratios", {}).get("operating_margin"),
            "revenueGrowth": info.get("ratios", {}).get("revenue_growth"),
            "returnOnEquity": info.get("ratios", {}).get("roe"),
            "dividendYield": info.get("dividend_yield"),
            "targetMeanPrice": info.get("target_mean"),
            "recommendation": info.get("recommendation"),
            "rsi": info.get("rsi"),
            "sma_50": info.get("sma_50"),
            "returns": info.get("returns", {})
        })
        
        return out
    except Exception as e:
        print(f"ENRICH ROUTER ERROR {ticker}: {e}")
        return base


def build_valuation_metrics(q: Dict[str, Any]) -> Dict[str, Any]:
    price = q.get("price")
    pe = q.get("peRatio")
    fpe = q.get("forwardPE")
    cap = q.get("marketCap")
    hi = q.get("fiftyTwoWeekHigh")
    lo = q.get("fiftyTwoWeekLow")
    target = q.get("targetMeanPrice")

    range_pct = None
    if price and hi and lo and hi != lo:
        range_pct = round((price - lo) / (hi - lo) * 100, 1)

    upside = None
    if price and target:
        upside = round((target - price) / price * 100, 1)

    pe_label = "N/A"
    if pe is not None:
        if pe < 15:
            pe_label = "Undervalued vs growth norms"
        elif pe < 28:
            pe_label = "Fair to market"
        elif pe < 45:
            pe_label = "Premium valuation"
        else:
            pe_label = "Stretched multiple"

    return {
        "price": price,
        "marketCap": cap,
        "marketCapFmt": _fmt_cap(cap),
        "enterpriseValue": q.get("enterpriseValue"),
        "trailingPE": pe,
        "forwardPE": fpe,
        "pegRatio": q.get("pegRatio"),
        "peAssessment": pe_label,
        "range52w": {"low": lo, "high": hi, "positionPct": range_pct},
        "analystTarget": target,
        "upsideToTargetPct": upside,
        "dividendYield": q.get("dividendYield"),
        "profitMargin": q.get("profitMargin"),
        "operatingMargins": q.get("operatingMargins"),
        "ebitdaMargins": q.get("ebitdaMargins"),
        "returnOnEquity": q.get("returnOnEquity"),
        "revenueGrowth": q.get("revenueGrowth"),
        "earningsGrowth": q.get("earningsGrowth"),
        "freeCashflow": q.get("freeCashflow"),
        "priceToSales": q.get("priceToSales"),
        "priceToBook": q.get("priceToBook"),
        "enterpriseToRevenue": q.get("enterpriseToRevenue"),
        "enterpriseToEbitda": q.get("enterpriseToEbitda"),
        "instOwnership": q.get("instOwnership"),
        "shortPercentOfFloat": q.get("shortPercentOfFloat"),
        "rsi": q.get("rsi"),
        "volatility": q.get("volatility"),
        "drawdown": q.get("drawdown"),
        "ret_1w": q.get("ret_1w"),
        "ret_1m": q.get("ret_1m"),
        "ret_3m": q.get("ret_3m"),
        "ret_1y": q.get("ret_1y"),
        "ret_ytd": q.get("ret_ytd"),
    }


def build_risk_analysis(q: Dict[str, Any], signals: Dict[str, Any]) -> Dict[str, Any]:
    score = signals.get("score", 50)
    price = q.get("price") or 0
    hi = q.get("fiftyTwoWeekHigh") or 0
    lo = q.get("fiftyTwoWeekLow") or 0
    pe = q.get("peRatio")
    beta = q.get("beta")
    chg = q.get("changePct") or 0

    risks: List[Dict[str, str]] = []
    level = "MODERATE"

    if hi and lo and price and hi != lo:
        rp = (price - lo) / (hi - lo) * 100
        if rp > 85:
            risks.append({
                "factor": "Drawdown / mean-reversion",
                "detail": f"Trading {rp:.0f}th percentile of 52-week range — limited cushion to highs.",
                "severity": "high",
            })
            level = "ELEVATED"
        elif rp < 20:
            risks.append({
                "factor": "Value trap",
                "detail": "Near 52-week lows — trend may still be weak despite apparent discount.",
                "severity": "medium",
            })

    if pe and pe > 40:
        risks.append({
            "factor": "Earnings multiple risk",
            "detail": f"Trailing P/E {pe:.1f}x — sensitive to guidance cuts.",
            "severity": "high",
        })
        level = "ELEVATED"
    elif pe and pe < 0:
        risks.append({
            "factor": "Profitability",
            "detail": "Negative earnings — valuation anchored on future expectations.",
            "severity": "high",
        })

    if beta and beta > 1.3:
        risks.append({
            "factor": "Market beta",
            "detail": f"Beta {beta:.2f} — amplifies index volatility.",
            "severity": "medium",
        })

    if abs(chg) > 3:
        risks.append({
            "factor": "Session volatility",
            "detail": f"Large daily move ({chg:+.2f}%) — event or sentiment driven.",
            "severity": "medium",
        })

    if q.get("debtToEquity") and q["debtToEquity"] > 150:
        risks.append({
            "factor": "Leverage",
            "detail": f"Debt/equity {q['debtToEquity']:.0f}% — refinancing and rate exposure.",
            "severity": "medium",
        })

    short_r = q.get("shortRatio")
    if short_r and short_r > 0.08:
        risks.append({
            "factor": "High short interest",
            "detail": f"Short interest at {short_r*100:.1f}% of float — elevates volatility and squeeze potential.",
            "severity": "medium",
        })

    current_r = q.get("currentRatio")
    if current_r and current_r < 1.0:
        risks.append({
            "factor": "Liquidity cushion",
            "detail": f"Current ratio of {current_r:.2f} suggests tight short-term liquidity.",
            "severity": "medium",
        })

    if not risks:
        risks.append({
            "factor": "Macro / sector",
            "detail": f"Standard {q.get('sector', 'market')} headwinds — rates, regulation, demand cycles.",
            "severity": "low",
        })

    risk_score = max(10, min(90, 100 - score + (10 if level == "ELEVATED" else 0)))

    return {
        "level": level,
        "riskScore": risk_score,
        "factors": risks[:6],
        "summary": (
            f"Overall risk is {level.lower()} with a risk index of {risk_score}/100 "
            f"(higher = more risk). Key watchpoints: "
            + "; ".join(r["factor"] for r in risks[:3])
            + "."
        ),
    }


async def build_industry_trends(
    industry: str,
    top100: List[Dict],
    fetch_quotes_fn,
    limit: int = 8,
) -> Dict[str, Any]:
    peers = [s for s in top100 if (s.get("industry") or "").lower() == industry.lower()]
    if not peers:
        return {"industry": industry, "peerCount": 0, "leaders": [], "laggards": [], "avgChangePct": None}

    tickers = [p["ticker"] for p in peers[:40]]
    quotes = await fetch_quotes_fn(tickers)

    rows = []
    for p in peers:
        t = p["ticker"]
        q = quotes.get(t, {})
        if q.get("price") is None:
            continue
        rows.append({
            "ticker": t,
            "name": q.get("name") or p.get("name"),
            "price": q.get("price"),
            "changePct": q.get("changePct") or 0,
            "peRatio": q.get("peRatio"),
            "score": None,
        })

    if not rows:
        return {"industry": industry, "peerCount": 0, "leaders": [], "laggards": [], "avgChangePct": None}

    avg_chg = sum(r["changePct"] for r in rows) / len(rows)
    sorted_rows = sorted(rows, key=lambda x: x["changePct"], reverse=True)

    return {
        "industry": industry,
        "peerCount": len(rows),
        "avgChangePct": round(avg_chg, 2),
        "leaders": sorted_rows[:limit],
        "laggards": sorted_rows[-limit:][::-1],
        "summary": (
            f"{industry}: {len(rows)} names tracked, average session move {avg_chg:+.2f}%. "
            f"Leader: {sorted_rows[0]['ticker']} ({sorted_rows[0]['changePct']:+.2f}%). "
            f"Laggard: {sorted_rows[-1]['ticker']} ({sorted_rows[-1]['changePct']:+.2f}%)."
        ),
    }


def _score_news_sentiment(news: List[Dict]) -> Tuple[float, List[str]]:
    """Return sentiment -1..+1 and human-readable themes from headlines."""
    pos = (
        "beat", "surge", "upgrade", "raises", "record", "partnership", "win", "strong",
        "growth", "profit", "buyback", "dividend", "approval", "breakthrough",
    )
    neg = (
        "miss", "cut", "downgrade", "probe", "lawsuit", "weak", "delay", "warn",
        "fall", "drop", "layoff", "recall", "fraud", "investigation", "loss",
    )
    score = 0.0
    themes: List[str] = []
    for n in news[:10]:
        t = (n.get("title") or "").lower()
        p_hit = [w for w in pos if w in t]
        n_hit = [w for w in neg if w in t]
        if p_hit:
            score += 0.15 * len(p_hit)
            themes.append(f"Positive tone: “{n.get('title', '')[:70]}”")
        if n_hit:
            score -= 0.2 * len(n_hit)
            themes.append(f"Cautionary headline: “{n.get('title', '')[:70]}”")
    score = max(-1.0, min(1.0, score))
    return score, themes[:4]


def _peer_context(q: Dict[str, Any], peer_block: Optional[Dict]) -> Dict[str, Any]:
    if not peer_block or not peer_block.get("leaders"):
        return {"hasPeers": False, "summary": "", "peVsMedian": None, "momentumRank": None}

    ticker = q.get("ticker", "")
    my_pe = q.get("peRatio")
    my_chg = q.get("changePct") or 0
    rows = (peer_block.get("leaders") or []) + (peer_block.get("laggards") or [])
    seen = set()
    peers = []
    for r in rows:
        t = r.get("ticker")
        if t and t not in seen:
            seen.add(t)
            peers.append(r)

    pes = [p["peRatio"] for p in peers if p.get("peRatio")]
    chgs = sorted(
        [(p["ticker"], p.get("changePct") or 0) for p in peers if p.get("ticker")],
        key=lambda x: x[1],
        reverse=True,
    )

    pe_vs = None
    if my_pe and pes:
        med = sorted(pes)[len(pes) // 2]
        pe_vs = "cheaper" if my_pe < med * 0.9 else "richer" if my_pe > med * 1.15 else "in-line"
        pe_vs = f"{pe_vs} than {peer_block.get('industry')} peers (your P/E {my_pe:.1f}x vs ~{med:.1f}x median)"

    mom_rank = None
    if chgs:
        rank = next((i + 1 for i, (t, _) in enumerate(chgs) if t == ticker), None)
        if rank:
            mom_rank = f"Today's momentum ranks **{rank}/{len(chgs)}** in {peer_block.get('industry')} — {my_chg:+.2f}% vs group avg {peer_block.get('avgChangePct', 0):+.2f}%"

    leaders = peer_block.get("leaders") or []
    summary = ""
    if pe_vs or mom_rank:
        summary = " · ".join(x for x in [pe_vs, mom_rank] if x)

    return {
        "hasPeers": True,
        "summary": summary,
        "peVsMedian": pe_vs,
        "momentumRank": mom_rank,
        "industry": peer_block.get("industry"),
        "topPeer": leaders[0]["ticker"] if leaders else None,
    }


def build_investment_verdict(
    query: str,
    ticker: str,
    q: Dict[str, Any],
    signals: Dict[str, Any],
    val: Dict[str, Any],
    risk: Dict[str, Any],
    news: List[Dict],
    peer_block: Optional[Dict],
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ORION brain: synthesize a direct BUY / HOLD / AVOID view from live facts.
    Every bullet is tied to a measurable input — not generic filler.
    """
    name = q.get("name") or ticker
    price = q.get("price")
    chg = q.get("changePct") or 0
    pe = val.get("trailingPE") or q.get("peRatio")
    fpe = val.get("forwardPE") or q.get("forwardPE")
    range_pct = (val.get("range52w") or {}).get("positionPct")
    upside = val.get("upsideToTargetPct")
    rev_g = q.get("revenueGrowth")
    roe = q.get("returnOnEquity")
    margin = q.get("profitMargin")
    street_key = (q.get("recommendationKey") or "").lower()
    signal_score = signals.get("score", 50)

    news_sent, news_themes = _score_news_sentiment(news)
    peers = _peer_context(q, peer_block)

    composite = float(signal_score)
    reasons_for: List[str] = []
    reasons_against: List[str] = []

    # --- Quant adjustments ---
    if range_pct is not None:
        if range_pct > 82:
            composite -= 7
            reasons_against.append(
                f"Price is {range_pct:.0f}% through its 52-week range — you're paying near the ceiling; upside is compressed unless earnings re-accelerate."
            )
        elif range_pct < 25:
            composite += 4
            reasons_for.append(
                f"Stock sits at only {range_pct:.0f}% of its 52-week range — if fundamentals are intact, you have more margin of safety than chasing highs."
            )
        elif 40 <= range_pct <= 65:
            reasons_for.append(
                f"Mid-range ({range_pct:.0f}% of 52W) — not obvious overpaying or catching a falling knife."
            )

    if pe is not None:
        if pe < 18 and (rev_g is None or (rev_g and rev_g > 0.05)):
            composite += 6
            reasons_for.append(f"Trailing P/E {pe:.1f}x is reasonable for the growth profile — valuation isn't the main objection.")
        elif pe > 42:
            composite -= 9
            reasons_against.append(
                f"P/E {pe:.1f}x prices in perfection — any guidance miss or multiple compression would hurt disproportionately."
            )
        elif 28 <= pe <= 42:
            reasons_against.append(
                f"P/E {pe:.1f}x is a premium multiple; you need sustained beats to justify it."
            )

    if fpe and pe and fpe < pe * 0.85:
        composite += 5
        reasons_for.append(f"Forward P/E ({fpe:.1f}x) below trailing ({pe:.1f}x) — the market expects earnings to inflect higher.")

    if upside is not None:
        if upside > 12:
            composite += 6
            reasons_for.append(f"Consensus target implies ~{upside:+.1f}% upside from ${price:.2f} — Street isn't openly negative.")
        elif upside < -8:
            composite -= 8
            reasons_against.append(f"Trading ~{abs(upside):.0f}% above mean Street target — expectations may be ahead of the tape.")

    if rev_g is not None:
        if rev_g > 0.12:
            composite += 5
            reasons_for.append(f"Revenue growth ~{rev_g * 100:.0f}% YoY — demand backdrop still working.")
        elif rev_g < 0:
            composite -= 6
            reasons_against.append("Negative revenue growth — the story is fighting macro or share loss.")

    if roe is not None and roe > 0.18:
        composite += 3
        reasons_for.append(f"ROE ~{roe * 100:.0f}% — capital efficiency supports a quality premium.")

    if margin is not None and margin > 0.2:
        reasons_for.append(f"Profit margin ~{margin * 100:.0f}% — pricing power / software-like economics if sustainable.")

    if chg > 2.5:
        composite += 3
        reasons_for.append(f"Momentum today (+{chg:.2f}%) — buyers in control short-term.")
    elif chg < -2.5:
        composite -= 4
        reasons_against.append(f"Session weakness ({chg:.2f}%) — wait for stabilization unless you're scaling in deliberately.")

    if news_sent > 0.25:
        composite += 4
        reasons_for.append("Recent headlines skew positive — sentiment tailwind.")
    elif news_sent < -0.25:
        composite -= 5
        reasons_against.append("News flow is cautious — let headlines clear before sizing up.")

    if peers.get("summary"):
        if "cheaper" in (peers.get("peVsMedian") or ""):
            composite += 3
            reasons_for.append(peers["peVsMedian"])
        elif "richer" in (peers.get("peVsMedian") or ""):
            composite -= 4
            reasons_against.append(peers["peVsMedian"])
        if peers.get("momentumRank") and "rank **1/" not in peers["momentumRank"]:
            reasons_against.append(peers["momentumRank"])

    for s in signals.get("signals", [])[:3]:
        if s.get("severity") == "warn":
            reasons_against.append(s.get("label", ""))
        else:
            reasons_for.append(s.get("label", ""))

    composite = max(12, min(92, composite))

    if composite >= 78:
        verdict = "STRONG_BUY"
    elif composite >= 66:
        verdict = "BUY"
    elif composite >= 50:
        verdict = "HOLD"
    else:
        verdict = "AVOID"

    conviction = max(1, min(10, round(composite / 10)))

    # --- Direct answer to “good buy?” style questions ---
    q_lower = query.lower()
    asks_buy = bool(re.search(r"(good\s+buy|should\s+i\s+buy|worth\s+buy|buy\s+now|invest\s+in|would\s+you\s+buy)", q_lower))
    asks_sell = bool(re.search(r"(should\s+i\s+sell|good\s+sell|dump|exit)", q_lower))

    if asks_sell:
        if verdict in ("AVOID", "HOLD"):
            direct = (
                f"**If you own {ticker}:** we'd lean toward **trimming or exiting** on rallies — "
                f"ORION composite {composite:.0f}/100 doesn't support aggressive ownership."
            )
        else:
            direct = (
                f"**Not an urgent sell** on our numbers — trend and score still justify holding a core line, "
                f"but take profits into strength given {range_pct or 'N/A'}% of the 52-week range."
            )
    elif asks_buy or is_verdict_question(query):
        if verdict == "STRONG_BUY":
            direct = (
                f"**Yes — {ticker} is a strong buy candidate right now** (conviction {conviction}/10). "
                f"At ${price:.2f}, the risk/reward stacks in your favor: "
                + (reasons_for[0] if reasons_for else "signals and fundamentals align.")
            )
        elif verdict == "BUY":
            direct = (
                f"**Leaning yes — {ticker} is buyable**, but not a blind max-conviction entry (conviction {conviction}/10). "
                f"We'd scale in rather than go all-in at ${price:.2f}."
            )
        elif verdict == "HOLD":
            direct = (
                f"**Not clearly a good buy today.** We'd **hold off** on new money into {ticker} at ${price:.2f} — "
                f"wait for a better entry or clearer catalyst. Existing holders: hold, don't add."
            )
        else:
            direct = (
                f"**No — we would not buy {ticker} here.** At ${price:.2f} the setup is unfavorable "
                f"(ORION composite {composite:.0f}/100). Patience beats forcing a position."
            )
    else:
        direct = (
            f"**Our call on {name} ({ticker}): {verdict.replace('_', ' ')}** "
            f"(conviction {conviction}/10, composite {composite:.0f}/100)."
        )

    change_mind = []
    lo_52 = (val.get("range52w") or {}).get("low")
    if range_pct and range_pct > 70 and lo_52:
        change_mind.append(
            f"A pullback toward the low-${lo_52:.0f} zone would improve entry quality."
        )
    if pe and pe > 30:
        change_mind.append("Two consecutive earnings beats with guide-up could re-rate the multiple.")
    if not change_mind:
        change_mind.append("Watch next earnings + sector breadth; we'd revisit if composite clears 65.")

    catalyst_watch = (analysis.get("memo") or {}).get("catalysts", [])[:2]
    if news_themes:
        catalyst_watch = catalyst_watch + news_themes[:2]

    bottom = (
        f"Bottom line: **{verdict.replace('_', ' ')}** on {ticker} — "
        + (
            "agree with adding exposure on dips."
            if verdict in ("BUY", "STRONG_BUY")
            else "don't initiate; reassess after the triggers above."
            if verdict == "HOLD"
            else "stay away until the tape improves."
        )
    )

    return {
        "verdict": verdict,
        "conviction": conviction,
        "compositeScore": round(composite, 1),
        "directAnswer": direct,
        "whyWeLike": reasons_for[:4] or ["No standout positives in live data — thesis is thin."],
        "whyWeCaution": reasons_against[:4] or ["No major red flags flagged, but edge is limited."],
        "peerTake": peers.get("summary") or f"Limited peer set for {q.get('industry') or q.get('sector') or 'this name'}.",
        "catalystWatch": catalyst_watch[:4],
        "changeMind": change_mind[:3],
        "bottomLine": bottom,
        "newsSentiment": round(news_sent, 2),
        "streetConsensus": street_key or None,
    }


def format_brain_reply(
    query: str,
    intent: str,
    contexts: List[Dict[str, Any]],
    industry_block: Optional[Dict],
    market_note: Optional[str],
) -> str:
    """Verdict-first narrative — answers the question, then supports with data tuning."""
    parts: List[str] = []

    if intent == "market" and market_note:
        parts.append(market_note)

    if industry_block and industry_block.get("summary") and intent == "industry":
        parts.append(f"## Industry pulse · {industry_block.get('industry')}\n\n{industry_block['summary']}")

    for ctx in contexts:
        t = ctx["ticker"]
        q = ctx["quote"]
        name = q.get("name") or t
        v = ctx.get("verdict") or {}
        val = ctx["valuation"]
        price = q.get("price")
        chg = q.get("changePct") or 0

        parts.append(f"## {name} ({t})")

        if v:
            badge = v.get("verdict", "HOLD").replace("_", " ")
            parts.append(
                f"### ORION call: **{badge}** · Conviction **{v.get('conviction', '—')}/10** "
                f"· Score **{v.get('compositeScore', '—')}/100**"
            )
            parts.append(v.get("directAnswer", ""))
            parts.append("**Why we like it**")
            for b in v.get("whyWeLike", []):
                parts.append(f"- {b}")
            parts.append("**What gives us pause**")
            for b in v.get("whyWeCaution", []):
                parts.append(f"- {b}")
            if v.get("peerTake"):
                parts.append(f"**Vs peers:** {v['peerTake']}")
            if v.get("catalystWatch"):
                parts.append("**Watch next**")
                for c in v["catalystWatch"][:3]:
                    parts.append(f"- {c}")
            parts.append(f"**What would change our mind**")
            for c in v.get("changeMind", []):
                parts.append(f"- {c}")
            parts.append(f"*{v.get('bottomLine', '')}*")
        else:
            parts.append(ctx.get("summary", ""))

        if price is not None:
            parts.append(
                f"\n---\n*Live: ${price:.2f} ({chg:+.2f}% today) · "
                f"P/E {val.get('trailingPE') or '—'} · Cap {val.get('marketCapFmt', '—')} · "
                f"52W range position {val.get('range52w', {}).get('positionPct', '—')}%*"
            )

        news = ctx.get("news") or []
        if news and intent in ("news", "verdict", "research"):
            parts.append("**Headlines driving sentiment**")
            for n in news[:4]:
                parts.append(f"- {n.get('title', '')[:130]}")

    if not parts:
        return (
            "Ask me something decisive — e.g. *Is SNPS a good buy?*, "
            "*Should I sell NVDA?*, or *Compare AMD vs INTC*."
        )

    parts.append("\n*ORION research brain · live Yahoo data · not investment advice.*")
    return "\n\n".join(parts)


def _confidence_from_signals(signals: Dict[str, Any], has_price: bool) -> Dict[str, Any]:
    score = signals.get("score", 50) if has_price else 35
    sig_list = signals.get("signals", [])
    avg_conf = (
        sum(s.get("confidence", 0.7) for s in sig_list) / len(sig_list)
        if sig_list
        else 0.65
    )
    blended = round(score * 0.7 + avg_conf * 30, 1)
    blended = max(15, min(92, blended))

    if blended >= 75:
        label = "High"
    elif blended >= 58:
        label = "Moderate"
    else:
        label = "Low"

    return {
        "score": blended,
        "label": label,
        "signalCount": len(sig_list),
        "rationale": (
            f"Blended ORION confidence {blended}/100 ({label}) from "
            f"quant score {score:.0f} and {len(sig_list)} live signal(s)."
        ),
    }


def _synthesize_reply(
    query: str,
    intent: str,
    contexts: List[Dict[str, Any]],
    industry_block: Optional[Dict],
    market_note: Optional[str],
) -> str:
    parts: List[str] = []

    if intent == "market" and market_note:
        parts.append(market_note)

    if industry_block and industry_block.get("summary"):
        parts.append(f"**Industry · {industry_block.get('industry')}**\n{industry_block['summary']}")

    for ctx in contexts:
        t = ctx["ticker"]
        q = ctx["quote"]
        name = q.get("name") or t
        price = q.get("price")
        chg = q.get("changePct") or 0
        conf = ctx["confidence"]
        val = ctx["valuation"]
        risk = ctx["risk"]
        summary = ctx["summary"]

        parts.append(f"### {name} ({t})")
        if price is not None:
            parts.append(
                f"**Live quote:** ${price:.2f} ({chg:+.2f}% today) · "
                f"Confidence **{conf['score']}/100** ({conf['label']})"
            )
        parts.append(summary)

        if intent in ("valuation", "research", "compare", "general"):
            pe = val.get("trailingPE")
            pe_s = f"{pe:.1f}x" if pe else "N/A"
            parts.append(
                f"**Valuation:** P/E {pe_s} — {val.get('peAssessment')}. "
                f"Market cap {val.get('marketCapFmt')}. "
                f"52W position: {val.get('range52w', {}).get('positionPct', '—')}% of range."
            )
            if val.get("upsideToTargetPct") is not None:
                parts.append(
                    f"Street target ${val.get('analystTarget'):.2f} "
                    f"({val['upsideToTargetPct']:+.1f}% vs spot)."
                )

        if intent in ("risk", "research", "compare", "general"):
            parts.append(f"**Risk:** {risk.get('summary')}")

        news = ctx.get("news") or []
        if news and intent in ("news", "research", "general"):
            parts.append("**Recent headlines:**")
            for n in news[:4]:
                parts.append(f"- {n.get('title', '')[:120]}")

        sigs = ctx.get("signals", {}).get("signals", [])
        if sigs and intent == "research":
            parts.append("**Signals:** " + " · ".join(s["label"][:80] for s in sigs[:3]))

    if not parts:
        return (
            "I couldn't resolve a ticker or industry from your question. "
            "Try: *What's the risk on NVDA?*, *Compare AAPL vs MSFT*, or *Semiconductor industry trends today*."
        )

    parts.append(
        "\n*Data: live Yahoo Finance quotes & fundamentals. Not investment advice.*"
    )
    return "\n\n".join(parts)


async def _optional_llm_reply(
    query: str,
    payload: Dict[str, Any],
) -> Optional[str]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    system = (
        "You are ORION, a senior equity research analyst with your own opinion. "
        "The user wants a DIRECT answer (e.g. is this a good buy?) — lead with BUY/HOLD/AVOID in bold. "
        "Use ONLY the JSON facts provided; never invent prices. "
        "Write like a human analyst: decisive opening, then 3-4 specific bullets why, "
        "2 bullets of risk, peer context, what would change your mind, one-sentence bottom line. "
        "No generic filler. Every claim must cite a number from the data."
    )
    user_content = (
        f"User question: {query}\n\n"
        f"Verified live data (JSON):\n{json.dumps(payload, default=str)[:12000]}"
    )

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0.35,
                    "max_tokens": 900,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                },
            )
        if r.status_code != 200:
            print(f"OPENAI ERROR {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"OPENAI REQUEST ERROR: {e}")
        return None


def validate_and_normalize_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    STRICT VALIDATION: Zero-Placeholder Tolerance.
    Purges all nulls, undefined, and placeholders before synthesis.
    """
    normalized = {}
    
    # Define metric mappings with strict normalization logic
    metrics = {
        "marketCap": lambda v: f"${v:,.0f}" if v and v > 0 else None,
        "enterpriseValue": lambda v: f"${v:,.0f}" if v and v > 0 else None,
        "sharesOutstanding": lambda v: f"{v:,.0f}" if v and v > 0 else None,
        "floatShares": lambda v: f"{v:,.0f}" if v and v > 0 else None,
        "averageVolume": lambda v: f"{v:,.0f}" if v and v > 0 else None,
        
        "peRatio": lambda v: f"{v:.1f}x" if v and v > 0 else None,
        "forwardPE": lambda v: f"{v:.1f}x" if v and v > 0 else None,
        "pegRatio": lambda v: f"{v:.2f}" if v and v > 0 else None,
        "priceToSales": lambda v: f"{v:.2f}x" if v and v > 0 else None,
        "priceToBook": lambda v: f"{v:.2f}x" if v and v > 0 else None,
        "enterpriseToRevenue": lambda v: f"{v:.2f}x" if v and v > 0 else None,
        "enterpriseToEbitda": lambda v: f"{v:.2f}x" if v and v > 0 else None,
        
        "revenueGrowth": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "earningsGrowth": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "operatingMargins": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "ebitdaMargins": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "profitMargin": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "returnOnEquity": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "returnOnAssets": lambda v: f"{v*100:.1f}%" if v is not None else None,
        
        "freeCashflow": lambda v: f"${v:,.0f}" if v and abs(v) > 0 else None,
        "operatingCashflow": lambda v: f"${v:,.0f}" if v and abs(v) > 0 else None,
        "totalCash": lambda v: f"${v:,.0f}" if v and v > 0 else None,
        "totalDebt": lambda v: f"${v:,.0f}" if v and v > 0 else None,
        "debtToEquity": lambda v: f"{v:.1f}%" if v is not None else None,
        "currentRatio": lambda v: f"{v:.2f}" if v and v > 0 else None,
        "quickRatio": lambda v: f"{v:.2f}" if v and v > 0 else None,
        
        "dividendYield": lambda v: f"{v*100:.2f}%" if v and v > 0 else None,
        "instOwnership": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "insiderOwnership": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "shortPercentOfFloat": lambda v: f"{v*100:.2f}%" if v is not None else None,
        "daysToCover": lambda v: f"{v:.1f}" if v and v > 0 else None,
        
        "rsi": lambda v: f"{v:.1f}" if v is not None else None,
        "volatility": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "drawdown": lambda v: f"{v*100:.1f}%" if v is not None else None,
        "ret_1w": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "ret_1m": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "ret_3m": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "ret_1y": lambda v: f"{v*100:+.1f}%" if v is not None else None,
        "ret_ytd": lambda v: f"{v*100:+.1f}%" if v is not None else None,
    }

    for key, formatter in metrics.items():
        val = data.get(key)
        # Strict exclusion of all placeholder types
        if val in (None, "null", "undefined", "N/A", "Unknown", 0, 0.0, "—", "-", ""):
            continue
        try:
            formatted = formatter(val)
            if formatted:
                normalized[key] = formatted
        except Exception:
            continue

    # Identity validation (NEVER allow null in these fields)
    normalized["ticker"] = str(data.get("ticker") or "").upper()
    normalized["name"] = data.get("name") or normalized["ticker"]
    sector = _clean_company_field(data.get("sector"))
    industry = _clean_company_field(data.get("industry"))
    if sector:
        normalized["sector"] = sector
    if industry:
        normalized["industry"] = industry
    elif sector:
        normalized["industry"] = sector
    
    # Business Summary: Ensure it's substantial
    summary = str(data.get("businessSummary") or "")
    if len(summary) > 60 and "is a" in summary.lower():
        normalized["businessSummary"] = summary
    
    # Clean recommendation
    reco = data.get("recommendationKey")
    if reco and reco not in ("none", "null", "undefined"):
        normalized["recommendationKey"] = str(reco).upper()

    return {k: v for k, v in normalized.items() if v not in (None, "null", "undefined", "N/A", "Unknown", "—")}


# NARRATIVE KNOWLEDGE BASE: High-conviction intelligence for major constituents.
# Ensures zero generic filler for key tickers.
_NARRATIVE_KB = {
    "NVDA": {
        "core_business": "AI Infrastructure & Accelerator Computing",
        "business_model": "High-margin GPU hardware & CUDA software integration",
        "key_products": ["H100/H200 Tensor Core GPUs", "Blackwell Architecture", "CUDA Ecosystem", "InfiniBand Networking"],
        "moat": "CUDA software ecosystem (developer lock-in) + Networking vertical integration",
        "thematic_exposure": "AI Infrastructure Cycle, Generative AI Training, Inference Scaling",
        "strategic_importance": "Dominant supplier of the compute engine for the global AI buildout",
        "main_competitors": ["AMD (Instinct)", "Intel (Gaudi)", "TPUs (Google)", "Custom Silicon (AWS/Azure)"],
        "bull_factors": ["Blackwell rollout acceleration", "Inference demand surge", "Software-driven recurring revenue"],
        "bear_factors": ["China export restrictions", "Hyperscaler custom silicon transition", "Supply chain concentration"]
    },
    "AAPL": {
        "core_business": "Consumer Technology & Services Ecosystem",
        "business_model": "High-margin hardware (iPhone) + Recurring Services revenue",
        "key_products": ["iPhone", "Services (App Store/iCloud)", "Mac", "iPad", "Apple Watch"],
        "moat": "High switching costs via hardware/software ecosystem + Brand premium",
        "thematic_exposure": "Consumer spending, Edge AI (Apple Intelligence), Services growth",
        "strategic_importance": "Largest install base of high-value consumers globally",
        "main_competitors": ["Samsung", "Google (Pixel)", "Huawei", "Spotify", "Netflix"],
        "bull_factors": ["Apple Intelligence driving upgrade cycle", "Services margin expansion", "Massive share buyback program"],
        "bear_factors": ["China market share loss", "Regulatory pressure on App Store", "iPhone saturation"]
    },
    "MSFT": {
        "core_business": "Enterprise Cloud & Productivity Software",
        "business_model": "SaaS subscriptions (Office 365) + Consumption-based Cloud (Azure)",
        "key_products": ["Azure Cloud", "Office 365", "LinkedIn", "GitHub", "Windows"],
        "moat": "High enterprise switching costs + Distribution dominance + OpenAI partnership",
        "thematic_exposure": "Enterprise AI adoption, Cloud transition, Cybersecurity",
        "strategic_importance": "Primary software layer for the modern global enterprise",
        "main_competitors": ["Amazon (AWS)", "Google (GCP)", "Salesforce", "Oracle"],
        "bull_factors": ["Azure AI services acceleration", "Copilot monetization scaling", "Commercial cloud margin stability"],
        "bear_factors": ["Azure growth deceleration", "Capex intensity for AI", "Regulatory scrutiny of OpenAI link"]
    },
    "AMZN": {
        "core_business": "E-commerce Logistics & Cloud Infrastructure",
        "business_model": "Retail marketplace + High-margin Cloud (AWS) + Advertising",
        "key_products": ["AWS Cloud", "Amazon Marketplace", "Prime Membership", "Advertising Services"],
        "moat": "Logistics scale/speed + AWS technical lead + Prime ecosystem lock-in",
        "thematic_exposure": "Cloud computing, E-commerce penetration, Digital advertising",
        "strategic_importance": "Critical infrastructure for both physical and digital commerce",
        "main_competitors": ["Walmart", "Shopify", "Microsoft (AWS competitor)", "Google (Ads competitor)"],
        "bull_factors": ["AWS re-acceleration via AI", "Retail margin improvement", "Advertising growth outperforming market"],
        "bear_factors": ["AWS competitive pressure", "FTC antitrust litigation", "Labor cost inflation"]
    },
    "GOOGL": {
        "core_business": "Digital Advertising & AI Research",
        "business_model": "Search-driven advertising + YouTube + Google Cloud (GCP)",
        "key_products": ["Google Search", "YouTube", "Android", "GCP", "Gemini AI"],
        "moat": "Search dominance (90%+ share) + Massive data advantage + YouTube network effects",
        "thematic_exposure": "Generative AI Search, Digital ads, Cloud infrastructure",
        "strategic_importance": "Primary gateway to information and digital video globally",
        "main_competitors": ["Meta", "Microsoft (Bing/OpenAI)", "Amazon (Ads)", "TikTok"],
        "bull_factors": ["YouTube Shorts monetization", "GCP reaching scale/profitability", "Deep AI research integration"],
        "bear_factors": ["Search disruption from GenAI", "Antitrust rulings on Search/Chrome", "Ad-market cyclicality"]
    },
    "META": {
        "core_business": "Social Media & Generative AI Models",
        "business_model": "Ad-supported social platforms (Instagram/FB/WhatsApp)",
        "key_products": ["Instagram", "Facebook", "WhatsApp", "Reels", "Llama Models"],
        "moat": "Massive network effects (3B+ users) + Proprietary social graph + Llama open-source lead",
        "thematic_exposure": "Social commerce, Generative AI (Llama), Metaverse (long-term)",
        "strategic_importance": "Dominant platform for global digital attention and communication",
        "main_competitors": ["TikTok", "Google", "Snap", "Apple (privacy changes)"],
        "bull_factors": ["Reels monetization efficiency", "Advantage+ AI ad tools driving ROAS", "WhatsApp monetization potential"],
        "bear_factors": ["Massive Reality Labs losses", "Regulatory privacy headwinds", "TikTok competition for youth attention"]
    },
    "TSLA": {
        "core_business": "Electric Vehicles & Autonomy",
        "business_model": "EV hardware sales + FSD software subscriptions + Energy storage",
        "key_products": ["Model 3/Y", "Model S/X", "Cybertruck", "FSD (Full Self-Driving)", "Megapack"],
        "moat": "Manufacturing cost advantage + Supercharger network + Data for autonomy",
        "thematic_exposure": "EV adoption, Robotaxi/Autonomy, Energy storage scaling",
        "strategic_importance": "Leader in the transition to sustainable transport and robotics",
        "main_competitors": ["BYD", "Rivian", "Ford/GM", "Waymo (Robotaxi)"],
        "bull_factors": ["FSD licensing potential", "Energy storage margin expansion", "Next-gen low-cost vehicle platform"],
        "bear_factors": ["EV demand slowdown", "Margin compression from price cuts", "CEO key-man risk"]
    },
    "AMD": {
        "core_business": "High-Performance Computing & Graphics",
        "business_model": "Fabless semiconductor design (CPU/GPU/FPGA)",
        "key_products": ["EPYC Server CPUs", "Ryzen PC CPUs", "Instinct MI300 GPUs", "Xilinx FPGAs"],
        "moat": "Chiplet architecture lead + x86 license + Strong execution vs Intel",
        "thematic_exposure": "AI accelerator demand, Server CPU market share, PC recovery",
        "strategic_importance": "The primary alternative to NVDA in AI and INTC in data centers",
        "main_competitors": ["NVIDIA", "Intel", "ARM-based custom silicon"],
        "bull_factors": ["Instinct GPU revenue scaling", "EPYC market share gains", "Xilinx synergy in industrial/auto"],
        "bear_factors": ["NVDA dominance in software (CUDA)", "Intel turnaround risk", "PC market stagnation"]
    },
    "AVGO": {
        "core_business": "Semiconductor Connectivity & Infrastructure Software",
        "business_model": "Custom AI silicon + Networking chips + Enterprise software (VMWare)",
        "key_products": ["Custom AI ASICs (TPU)", "Networking Switches (Tomahawk)", "VMWare", "Symantec"],
        "moat": "High technical complexity + Sticky enterprise software + Custom silicon partnerships",
        "thematic_exposure": "Custom AI silicon, Datacenter networking, Enterprise software consolidation",
        "strategic_importance": "Key provider of the 'plumbing' for AI datacenters and hybrid cloud",
        "main_competitors": ["Marvell", "NVIDIA (Networking)", "Cisco", "Microsoft (Cloud software)"],
        "bull_factors": ["VMWare margin expansion", "Custom AI silicon demand from Google/Meta", "Networking content growth"],
        "bear_factors": ["Integration risk for large acquisitions", "Concentrated customer base", "Debt levels from VMWare deal"]
    },
    "TSM": {
        "core_business": "Advanced Semiconductor Foundry",
        "business_model": "Contract manufacturing for fabless designers (pure-play foundry)",
        "key_products": ["3nm/5nm manufacturing nodes", "CoWoS Packaging", "FinFET Technology"],
        "moat": "Massive technical lead in advanced nodes + Ecosystem scale + high capex barrier",
        "thematic_exposure": "AI chip manufacturing, Advanced node transition (2nm), Geopolitical risk",
        "strategic_importance": "The single most critical node in the global electronics supply chain",
        "main_competitors": ["Samsung Foundry", "Intel Foundry"],
        "bull_factors": ["AI-driven demand for 3nm/CoWoS", "Intel outsourcing more to TSM", "Strong pricing power"],
        "bear_factors": ["Geopolitical tension (Taiwan/China)", "Capex intensity", "Concentration of production in Taiwan"]
    },
    "ASML": {
        "core_business": "Lithography Equipment for Semiconductors",
        "business_model": "High-value equipment sales & long-term maintenance",
        "key_products": ["EUV (Extreme Ultraviolet) systems", "High-NA EUV", "DUV systems"],
        "moat": "Monopoly on EUV technology + Massive R&D barrier + Integration into fabs",
        "thematic_exposure": "Advanced semiconductor manufacturing, Moore's Law extension",
        "strategic_importance": "Sole supplier of the machines required to make the world's most advanced chips",
        "main_competitors": ["Nikon", "Canon (only in older DUV/Nanoimprint)"],
        "bull_factors": ["High-NA EUV adoption for 2nm", "Foundry capacity expansion", "Large order backlog"],
        "bear_factors": ["China export restrictions", "Cyclical fab spending", "Technical complexity risks of High-NA"]
    },
    "NFLX": {
        "core_business": "Streaming Entertainment",
        "business_model": "Subscription-based streaming + Ad-supported tier",
        "key_products": ["Original Content", "Global Streaming Platform", "Gaming"],
        "moat": "Massive content library + Scale (270M+ subs) + Recommendation algorithm",
        "thematic_exposure": "Streaming consolidation, Connected TV ads, Password sharing monetization",
        "strategic_importance": "Leader in the transition from linear TV to digital streaming",
        "main_competitors": ["Disney+", "Amazon Prime Video", "YouTube", "Max"],
        "bull_factors": ["Ad-tier sub growth", "Live sports integration", "Free cash flow inflection"],
        "bear_factors": ["Content spend inflation", "Churn in mature markets", "Competitive bidding for live rights"]
    },
    "CRM": {
        "core_business": "Customer Relationship Management (CRM)",
        "business_model": "Cloud-based SaaS subscriptions",
        "key_products": ["Sales Cloud", "Service Cloud", "Slack", "Tableau", "Agentforce (AI)"],
        "moat": "Category dominance + High switching costs + Massive enterprise data set",
        "thematic_exposure": "Enterprise AI agents, SaaS consolidation, Workflow automation",
        "strategic_importance": "The system of record for customer data in most large enterprises",
        "main_competitors": ["Microsoft (Dynamics)", "Oracle", "SAP", "HubSpot"],
        "bull_factors": ["Agentforce AI agent adoption", "Slack integration synergies", "Margin expansion focus"],
        "bear_factors": ["SaaS spending fatigue", "Front-office growth deceleration", "Integration of large acquisitions"]
    },
    "SNPS": {
        "core_business": "Semiconductor EDA (Electronic Design Automation)",
        "business_model": "Software licensing & IP integration services",
        "key_products": ["Fusion Design Platform", "DSO.ai (AI-driven design)", "Interface IP Portfolio"],
        "moat": "Extremely high switching costs + integration into foundry PDKs",
        "thematic_exposure": "Advanced-node complexity (3nm/2nm), AI chip-design automation",
        "strategic_importance": "Critical infrastructure for designing all complex global semiconductors",
        "main_competitors": ["Cadence Design Systems (CDNS)", "Siemens EDA (Mentor)"],
        "bull_factors": ["AI-driven chip design surge", "System-on-Chip (SoC) complexity increasing", "3D-IC adoption"],
        "bear_factors": ["China semiconductor design restrictions", "R&D expense escalation", "Industry consolidation risk"]
    },
    "PLTR": {
        "core_business": "AI Operating Systems & Data Integration",
        "business_model": "Enterprise & Government software platforms (Foundry/Gotham/AIP)",
        "key_products": ["AIP (AI Platform)", "Foundry", "Gotham", "Apollo"],
        "moat": "Ontology-driven data architecture + High-stakes government integration",
        "thematic_exposure": "Enterprise AI adoption, Sovereign AI, Defense software digitization",
        "strategic_importance": "Infrastructure layer for integrating LLMs into legacy enterprise workflows",
        "main_competitors": ["Snowflake", "Databricks", "C3.ai", "Custom in-house solutions"],
        "bull_factors": ["AIP Bootcamps driving commercial acceleration", "S&P 500 inclusion sentiment", "Defense budget allocation for AI"],
        "bear_factors": ["Concentrated customer base", "Stock-based compensation dilution", "Long sales cycles for gov contracts"]
    }
}


async def build_company_intelligence(
    ticker: str,
    normalized_data: Dict[str, Any],
    news: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    PRE-GENERATION INTELLIGENCE ASSEMBLER:
    Builds a structured narrative profile before report generation.
    Checks Narrative KB first, then uses LLM for deep extraction.
    """
    ticker = ticker.upper()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    summary = normalized_data.get("businessSummary") or normalized_data.get("description") or ""
    heuristic = _build_heuristic_company_intelligence(ticker, normalized_data, news)
    
    # 1. Check Gold-Standard Knowledge Base
    if ticker in _NARRATIVE_KB:
        return _merge_intelligence(heuristic, _NARRATIVE_KB[ticker])
    
    # 2. Extract from Summary if KB misses
    if not api_key or not summary:
        return heuristic

    system = (
        "You are a Lead Strategic Equity Analyst. Your task is to extract a HIGH-SPECIFICITY narrative profile. "
        "CRITICAL: Avoid generic finance language. No 'market participant', no 'its sector', no 'standard model'. "
        "IDENTIFY: Actual product names (e.g., 'iPhone', 'H100', 'Falcon 9'), specific moats (e.g., 'PDK integration', 'CUDA ecosystem'), "
        "and concrete thematic drivers (e.g., '3nm node transition', 'GLP-1 adoption'). "
        "If you don't find specific products, use the most granular business units mentioned. "
        "Return a JSON object with: core_business, business_model, strategic_role, industry, sector, "
        "key_products (list), key_customers, competitive_moat, main_competitors (list), thematic_exposure, "
        "growth_drivers (list), risks (list), competitive_advantages (list), "
        "bull_factors (list of 3 specific strengths), bear_factors (list of 3 specific risks)."
    )
    headlines = [n.get("title") for n in (news or []) if n.get("title")]
    user_content = (
        f"Ticker: {ticker}\n"
        f"Industry: {normalized_data.get('industry')}\n"
        f"Sector: {normalized_data.get('sector')}\n"
        f"Summary: {summary}\n"
        f"Recent headlines: {json.dumps(headlines[:6])}\n"
        f"Baseline heuristic intelligence: {json.dumps(heuristic, default=str)}"
    )
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    "temperature": 0.1,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                    "response_format": {"type": "json_object"}
                },
            )
        if r.status_code == 200:
            llm_intel = json.loads(r.json()["choices"][0]["message"]["content"])
            return _merge_intelligence(heuristic, llm_intel)
    except Exception as e:
        print(f"INTEL ASSEMBLER ERROR: {e}")
        
    return heuristic


async def synthesize_equity_report(
    ticker: str,
    q: Dict[str, Any],
    news: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Institutional Report Engine: Synthesizes structured thematic intelligence.
    Pipeline: Validation -> Intel Assembly -> Narrative Synthesis.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    
    # 1. DATA VALIDATION
    normalized = validate_and_normalize_data(q)
    
    # 2. INTELLIGENCE ASSEMBLY
    intel = await build_company_intelligence(ticker, normalized, news)
    
    # 3. CONTEXTUAL SYNTHESIS
    context = {
        "ticker": ticker,
        "metrics": {k: v for k, v in normalized.items() if k != "businessSummary"},
        "intel": intel,
        "news": [n.get("title") for n in news[:8]]
    }

    if api_key:
        system = (
            "You are ORION, a Senior Equity Research Analyst at a top-tier investment bank. "
            "Generate a deep, thematic institutional research report. "
            "PRIMARY DIRECTIVE: NARRATIVE INTELLIGENCE > TEMPLATE FILLER. "
            "SPECIFICITY IS MANDATORY. "
            "\nCRITICAL RULES:\n"
            "1. FORBIDDEN PHRASES: 'Market participant', 'Sector-wide growth', 'Industry peers', 'Standard industry model', 'Operational scale', 'its sector', 're-rating triggers', 'deep customer integration'.\n"
            "2. DATA INTEGRITY: Anchor claims in the provided metrics (P/E, Growth, Margins, P/S, Inst Ownership). Reference actual numbers.\n"
            "3. SPECIFICITY TEST: Before writing every paragraph, ask: 'Could this apply to 500 other stocks?' If YES, you MUST rewrite it to be specific to THIS company's products, moat, and role.\n"
            "4. USE THE INTEL: Explicitly reference product names (e.g., Blackwell, Azure, iPhone), moats (e.g., CUDA, high switching costs), and thematic drivers (e.g., AI inference, Edge AI).\n"
            "5. NO PLACEHOLDERS: Never mention missing data or use 'null/undefined'.\n\n"
            "Return a JSON object with 8 sections (each 4-6 DENSE, INSIGHTFUL sentences):\n"
            "A. executive_summary (Thematic thesis: WHY this company matters in the current market cycle)\n"
            "B. business_overview (Specific products, business model mechanics, and ecosystem role)\n"
            "C. competitive_positioning (Direct rivals, specific moats, and defensive barriers)\n"
            "D. financial_quality (Metric deep-dive: growth, margins, P/S, and valuation context with numbers)\n"
            "E. growth_drivers (Concrete catalysts: product roadmaps, thematic tailwinds, market expansion)\n"
            "F. risks (Company-specific headwinds: regulatory, technical, or competitive shifts)\n"
            "G. catalysts (Specific future triggers: earnings, product launches, or macro inflection points)\n"
            "H. intelligence_summary (Final synthesis weighing the Bull/Bear factors against the live tape).\n"
            "bull_factors: list of 3 specific strengths.\n"
            "bear_factors: list of 3 specific risks."
        )
        user_content = f"Synthesize thematic institutional intelligence for {ticker} using this assembled context: {json.dumps(context, default=str)}"
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                        "temperature": 0.25,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                        "response_format": {"type": "json_object"}
                    },
                )
            if r.status_code == 200:
                raw = json.loads(r.json()["choices"][0]["message"]["content"])
                return _sanitize_equity_report(raw, ticker=ticker, normalized=normalized, intel=intel)
        except Exception as e:
            print(f"EQUITY SYNTHESIS ERROR: {e}")

    return _sanitize_equity_report({}, ticker=ticker, normalized=normalized, intel=intel)


async def run_assistant_chat(
    message: str,
    *,
    ticker: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    universe: List[Dict],
    top100: List[Dict],
    fetch_quotes_fn,
    fetch_news_fn,
    compute_signals_fn,
    build_analysis_fn,
    fetch_top100_quotes_fn=None,
) -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return {"error": "empty message", "reply": "Please enter a research question."}

    tickers = extract_tickers(message, ticker, universe, top100)
    industry = detect_industry(message)
    intent = classify_intent(message, tickers)

    if not tickers and ticker:
        tickers = [ticker.upper()]

    contexts: List[Dict[str, Any]] = []
    industry_block = None
    market_note = None

    if intent == "market" and fetch_top100_quotes_fn:
        try:
            all_q = await fetch_top100_quotes_fn()
            picks = sorted(
                [v for v in all_q.values() if v.get("changePct") is not None],
                key=lambda x: x.get("changePct", 0),
                reverse=True,
            )[:5]
            losers = sorted(
                [v for v in all_q.values() if v.get("changePct") is not None],
                key=lambda x: x.get("changePct", 0),
            )[:3]
            market_note = (
                "**Top 100 movers today:** "
                + ", ".join(f"{p['ticker']} {p['changePct']:+.2f}%" for p in picks)
                + ". Laggards: "
                + ", ".join(f"{l['ticker']} {l['changePct']:+.2f}%" for l in losers)
                + "."
            )
        except Exception as e:
            print(f"MARKET SNAPSHOT ERROR: {e}")

    if industry and (intent == "industry" or intent == "research" or not tickers):
        industry_block = await build_industry_trends(industry, top100, fetch_quotes_fn)

    for t in tickers[:3]:
        quotes = await fetch_quotes_fn([t])
        q = quotes.get(t, {"ticker": t})
        q = await _enrich_fundamentals_async(t, q)
        news = await fetch_news_fn(t)
        signals = compute_signals_fn(q)
        analysis = build_analysis_fn(q)
        val = build_valuation_metrics(q)
        risk = build_risk_analysis(q, signals)
        conf = _confidence_from_signals(signals, q.get("price") is not None)

        memo = analysis.get("memo", {})
        summary = memo.get("thesis") or (
            f"{q.get('name', t)} — ORION signal {signals.get('score', 50):.0f}/100, "
            f"recommendation {memo.get('recommendation', 'HOLD')}."
        )

        peer_industry = industry or q.get("industry") or q.get("sector")
        peer_block = None
        if peer_industry and top100:
            peer_block = await build_industry_trends(peer_industry, top100, fetch_quotes_fn, limit=5)

        verdict = build_investment_verdict(
            message, t, q, signals, val, risk, news, peer_block, analysis
        )
        conf["rationale"] = (
            f"Verdict {verdict['verdict']} — composite {verdict['compositeScore']}/100 "
            f"(conviction {verdict['conviction']}/10)."
        )

        contexts.append({
            "ticker": t,
            "quote": q,
            "news": news[:8],
            "signals": signals,
            "valuation": val,
            "risk": risk,
            "confidence": conf,
            "summary": summary,
            "recommendation": memo.get("recommendation"),
            "industryPeers": peer_block,
            "verdict": verdict,
        })

    if not contexts and industry_block:
        intent = "industry"

    structured = {
        "intent": intent,
        "tickers": [c["ticker"] for c in contexts],
        "industry": industry_block,
        "contexts": [
            {
                "ticker": c["ticker"],
                "name": c["quote"].get("name"),
                "price": c["quote"].get("price"),
                "changePct": c["quote"].get("changePct"),
                "verdict": c["verdict"],
                "valuation": c["valuation"],
                "risk": c["risk"],
                "news": c["news"],
                "signals": c["signals"],
                "summary": c["summary"],
                "confidence": c["confidence"],
                "industryPeers": c["industryPeers"],
            }
            for c in contexts
        ],
    }

    # 4. Final synthesis
    if api_key:
        reply = await _optional_llm_reply(message, structured)
        if reply:
            return {"reply": reply, "structured": structured}

    return {"reply": format_brain_reply(message, intent, contexts, industry_block, market_note), "structured": structured}


async def build_single_ticker_verdict(
    ticker: str,
    fetch_quotes_fn,
    fetch_news_fn,
    compute_signals_fn,
    build_analysis_fn,
    top100: List[Dict],
) -> Dict[str, Any]:
    quotes = await fetch_quotes_fn([ticker])
    q = quotes.get(ticker, {"ticker": ticker})
    q = await _enrich_fundamentals_async(ticker, q)
    news = await fetch_news_fn(ticker)
    signals = compute_signals_fn(q)
    analysis = build_analysis_fn(q)
    val = build_valuation_metrics(q)
    risk = build_risk_analysis(q, signals)
    
    industry = q.get("industry") or q.get("sector")
    peer_block = None
    if industry and top100:
        peer_block = await build_industry_trends(industry, top100, fetch_quotes_fn, limit=5)
        
    verdict = build_investment_verdict(
        f"Analyze {ticker}", ticker, q, signals, val, risk, news, peer_block, analysis
    )
    return verdict


async def get_dynamic_sector_context(industry: str) -> Dict[str, Any]:
    """
    Uses LLM to decompose a novel industry into subsegments and identify sector cognition.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {
            "topology": {industry: []},
            "macro_weights": ["Interest Rates", "Liquidity"],
            "cognition": {"moats": ["Scale"], "risks": ["Competition"], "regulatory": ["Standards"]}
        }

    system = (
        "You are a Senior Industry Analyst. Decompose the requested industry into its core subsegments. "
        "Also identify the top 5 specific macro drivers, 3 core moat types, 3 industry-wide risks, and 3 regulatory themes. "
        "Return JSON with 'topology', 'macro_weights', and 'cognition' (object with moats, risks, regulatory lists)."
    )
    user_content = f"Industry: {industry}"
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                    "response_format": {"type": "json_object"}
                },
            )
        if r.status_code == 200:
            return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"DYNAMIC SECTOR CONTEXT ERROR: {e}")
        
    return {
        "topology": {industry: []},
        "macro_weights": ["Interest Rates", "Liquidity"],
        "cognition": {"moats": ["Scale"], "risks": ["Competition"], "regulatory": ["Standards"]}
    }


def _sanitize_news_headlines(headlines: List[str], industry: str) -> Dict[str, Any]:
    """
    Cleans headlines and clusters them into semantic themes.
    Returns a list of thematic summaries.
    """
    cleaned = []
    ticker_soup_pattern = re.compile(r"^[A-Z,\s&/]{2,}:\s?|^[A-Z,\s&/]{2,}\s?[-–]\s?")
    
    for h in headlines:
        if not h: continue
        h = ticker_soup_pattern.sub("", h)
        h = re.sub(r"\s?\|\s?Yahoo Finance.*$", "", h, flags=re.IGNORECASE)
        h = re.sub(r"\s?\|\s?Reuters.*$", "", h, flags=re.IGNORECASE)
        h = re.sub(r"\s?-\s?Bloomberg.*$", "", h, flags=re.IGNORECASE)
        h = " ".join(h.split())
        if len(h) > 25 and not h.lower().startswith("null"):
            cleaned.append(h)
            
    # Deduplicate
    unique = _dedupe_strings(cleaned)
    if not unique:
        return {"themes": [], "raw": []}

    # For now, we provide the unique headlines as raw material for the LLM
    # In a more complex version, we could use an LLM here to summarize them into 3-5 themes
    return {
        "themes": [], # Placeholder for future semantic clustering
        "raw": unique[:25]
    }


async def build_industry_intelligence(
    industry: str,
    stats: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    fetch_news_fn: Any,
    fetch_quotes_fn: Any,
    sector_intel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    GLOBAL INDUSTRY INTELLIGENCE ENGINE:
    Dynamically identifies topology, macro weights, and aggregates metrics across constituents.
    Incorporates real-time sector data from OpenBB if provided.
    """
    # 1. Get Sector Context (Static or Dynamic)
    topology = _INDUSTRY_TOPOLOGY.get(industry)
    macro_weights = _SECTOR_MACRO_WEIGHTS.get(industry)
    cognition = _SECTOR_COGNITION.get(industry)
    
    if not topology or not macro_weights or not cognition:
        dyn = await get_dynamic_sector_context(industry)
        topology = topology or dyn.get("topology", {industry: []})
        macro_weights = macro_weights or dyn.get("macro_weights", ["Interest Rates", "Liquidity"])
        cognition = cognition or dyn.get("cognition", {"moats": ["Scale"], "risks": ["Competition"], "regulatory": ["Standards"]})

    # 2. Map ETFs and fetch live performance
    etf_tickers = _INDUSTRY_ETFS.get(industry, [])
    etf_data = {}
    if etf_tickers:
        try:
            etf_quotes = await fetch_quotes_fn(etf_tickers)
            for t in etf_tickers:
                q = etf_quotes.get(t)
                if q and q.get("price"):
                    etf_data[t] = {
                        "price": q.get("price"),
                        "changePct": q.get("changePct"),
                        "name": q.get("name") or t
                    }
        except Exception as e:
            print(f"INDUSTRY INTEL ETF ERROR: {e}")

    # 3. Aggregate Representative Constituents (minimum 10-15)
    representative_tickers = []
    if sector_intel and sector_intel.get("tickers"):
        representative_tickers.extend(sector_intel["tickers"][:15])
    
    for subsegment, tickers in topology.items():
        representative_tickers.extend(tickers[:3])
    
    # Merge with candidates and dedupe
    final_targets = _dedupe_strings(representative_tickers + [c["ticker"] for c in candidates[:15]])[:25]
    
    # 4. Aggregate Metric Engine
    metrics_to_agg = [
        "peRatio", "forwardPE", "revenueGrowth", "operatingMargins", 
        "grossMargins", "ebitdaMargins", "returnOnEquity", "debtToEquity"
    ]
    
    agg_data = {m: [] for m in metrics_to_agg}
    all_news_headlines = []
    
    # Fetch news and metrics for targets
    for ticker in final_targets[:15]:
        try:
            cand = next((c for c in candidates if c["ticker"] == ticker), None)
            if cand:
                for m in metrics_to_agg:
                    val = cand.get(m)
                    if val is not None and isinstance(val, (int, float)) and val > -100:
                        agg_data[m].append(val)
            
            n = await fetch_news_fn(ticker)
            all_news_headlines.extend([item.get("title") for item in n[:5] if item.get("title")])
        except Exception:
            continue
            
    # Calculate Medians
    medians = {}
    for m, vals in agg_data.items():
        if vals:
            sorted_vals = sorted(vals)
            medians[m] = sorted_vals[len(vals)//2]
        else:
            medians[m] = None

    # 5. Sanitize News
    news_intel = _sanitize_news_headlines(all_news_headlines, industry)
    
    # 6. Final Assembler Object
    res = {
        "industry": industry,
        "benchmark_etfs": etf_data,
        "topology": topology,
        "sub_industries": sector_intel.get("sub_industries", []) if sector_intel else [],
        "macro_weighting": macro_weights,
        "cognition": cognition,
        "aggregate_metrics": medians,
        "metrics": {
            "avg_orion_score": f"{stats.get('avgScore', 50):.1f}/100",
            "market_breadth": f"{stats.get('breadth', 0)}% positive",
            "median_pe": f"{medians.get('peRatio', '—')}x",
            "session_perf": f"{stats.get('avgChange', 0):+.2f}%",
            "sample_size": len(final_targets),
            "total_mkt_cap": sector_intel.get("metrics", {}).get("total_mkt_cap", "—") if sector_intel else "—",
            "market_weight": sector_intel.get("metrics", {}).get("market_weight", "—") if sector_intel else "—",
            "returns": sector_intel.get("metrics", {}).get("returns", {}) if sector_intel else {}
        },
        "market_leaders": final_targets[:15],
        "largest_companies": sector_intel.get("metrics", {}).get("largest_companies", []) if sector_intel else [],
        "thematic_headlines": news_intel["raw"][:25],
        "institutional_sentiment": "BULLISH" if stats.get("avgScore", 50) > 65 else "BEARISH" if stats.get("avgScore", 50) < 45 else "NEUTRAL"
    }
    return res


async def synthesize_equity_report(ticker: str, norm: Dict[str, Any], news: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Synthesizes an institutional equity report.
    Includes an Industry Intelligence Layer for the company's specific sector.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    name = norm.get("name") or ticker
    intel = _NARRATIVE_KB.get(ticker, {})
    
    # 1. Fetch Sector Context for the Industry Layer
    industry = norm.get("industry") or norm.get("sector") or "Technology"
    topology = _INDUSTRY_TOPOLOGY.get(industry)
    macro_weights = _SECTOR_MACRO_WEIGHTS.get(industry)
    cognition = _SECTOR_COGNITION.get(industry)
    
    if not topology or not macro_weights or not cognition:
        dyn = await get_dynamic_sector_context(industry)
        topology = topology or dyn.get("topology", {industry: []})
        macro_weights = macro_weights or dyn.get("macro_weights", ["Interest Rates", "Liquidity"])
        cognition = cognition or dyn.get("cognition", {"moats": ["Scale"], "risks": ["Competition"], "regulatory": ["Standards"]})

    # 2. Build Context for the Analyst
    context = {
        "ticker": ticker,
        "name": name,
        "industry": industry,
        "metrics": {k: v for k, v in norm.items() if k != "businessSummary"},
        "intel": intel,
        "news": [n.get("title") for n in news[:8]],
        "sector_cognition": cognition,
        "macro_weights": macro_weights,
        "topology": topology
    }

    if api_key:
        system = (
            "You are ORION, a Senior Macro Equity Strategist. "
            "Generate a deep, thematic equity report with a dedicated INDUSTRY INTELLIGENCE LAYER. "
            "The goal is an institutional brief similar to Goldman Sachs or Bloomberg Intelligence. "
            "\nCRITICAL RULES:\n"
            "1. NO GENERIC AI FILLER. Do not use phrases like 'Interest rate expectations', 'global liquidity', 'valuation floors' unless specifically tied to the sector (e.g. NIM for Banks).\n"
            "2. INDUSTRY INTELLIGENCE LAYER (9 Sections). You must explain: what the industry is, how it structurally works, value chain positioning, and the ORION Industry Outlook Score.\n"
            "3. SECTOR COGNITION. Use the provided cognition framework (moats, risks, regulatory) to anchor the industry section. "
            "For Healthcare, discuss FDA approvals and patent cliffs. For Energy, discuss OPEC+ and refining spreads.\n"
            "4. NO HEADLINE FRAGMENTS. Never repeat raw fragmented headlines. Synthesize them into structural trends.\n"
            "5. CAUSALITY. Explain WHY factors matter, not just WHAT they are.\n"
            "6. NO PLACEHOLDERS. Never use 'null', 'undefined', or 'N/A'.\n\n"
            "Return a JSON object with these two primary fields:\n"
            "industry_intelligence: (A JSON object with these 9 sections: industry_overview, market_structure, value_chain_position, key_industry_drivers, industry_risks, competitive_landscape, industry_cycle_position, structural_themes, and industry_outlook (object with score, confidence, bullish_factors[], bearish_factors[]))\n"
            "company_analysis: (A JSON object with 8 sections A-H: executive_summary, business_overview, competitive_positioning, financial_quality, growth_drivers, risks, catalysts, intelligence_summary (with bull_factors[] and bear_factors[]))."
        )
        
        user_content = f"Generate full institutional report for {ticker} ({name}) in the {industry} industry. Data: {json.dumps(context, default=str)}"
        
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                        "temperature": 0.25,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                        "response_format": {"type": "json_object"}
                    },
                )
            if r.status_code == 200:
                res = json.loads(r.json()["choices"][0]["message"]["content"])
                # Map company analysis sections back to the "layers" format expected by the report builder
                comp = res.get("company_analysis", {})
                layers = []
                titles = {
                    "executive_summary": "I. Executive Summary & Investment Thesis",
                    "business_overview": "II. Business Model & Strategic Overview",
                    "competitive_positioning": "III. Competitive Landscape & Moat Analysis",
                    "financial_quality": "IV. Financial Quality & Valuation Context",
                    "growth_drivers": "V. Key Growth Drivers & Strategic Themes",
                    "risks": "VI. Risk Assessment & Mitigation Factors",
                    "catalysts": "VII. Catalyst Engine & Re-rating Triggers",
                    "intelligence_summary": "VIII. ORION Intelligence Synthesis"
                }
                for k, t in titles.items():
                    if comp.get(k):
                        layers.append({"title": t, "content": comp[k]})
                
                return {
                    "industry_intelligence": res.get("industry_intelligence"),
                    "layers": layers,
                    "bull_factors": comp.get("intelligence_summary", {}).get("bull_factors", []),
                    "bear_factors": comp.get("intelligence_summary", {}).get("bear_factors", [])
                }
        except Exception as e:
            print(f"EQUITY SYNTHESIS ERROR {ticker}: {e}")

    # Fallback to deterministic report
    return _build_deterministic_equity_report(ticker, norm, intel)


async def synthesize_industry_report(
    industry: str,
    stats: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    fetch_news_fn: Any,
    fetch_quotes_fn: Any,
    sector_intel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Institutional Industry Report Engine: Synthesizes structured sector intelligence.
    Architecture: Snapshot -> Structure -> Cycle -> Sensitivity -> Capital Flow -> Outlook.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    
    # 1. INTELLIGENCE ASSEMBLY (Live ETF data + Thematic news + Medians)
    intel = await build_industry_intelligence(
        industry, stats, candidates, fetch_news_fn, fetch_quotes_fn, sector_intel
    )
    
    # 2. Add S&P 500 Context for Relative Valuation
    sp500_pe = 22.5 # Approximate live baseline
    sp500_growth = 0.08
    
    if api_key:
        system = (
            "You are ORION, a Senior Macro Equity Strategist. "
            "Generate a deep, thematic INSTITUTIONAL INDUSTRY REPORT. "
            "Tone: Bloomberg Intelligence + Goldman Sachs Strategy. "
            "\nCRITICAL RULES:\n"
            "1. NO FILLER. Every paragraph must contain analytical insight.\n"
            "2. DATA ANCHORING. Use the provided medians, P/E ratios, growth rates, and return profiles to anchor the thesis.\n"
            "3. PERFORMANCE CONTEXT. Explicitly discuss the sector's performance (Day, YTD, 1Y, 3Y, 5Y) and its Market Weight relative to the broader market.\n"
            "4. SUB-INDUSTRY ANALYSIS. Mention key sub-industries and their relative weights/importance within the sector.\n"
            "5. NO HEADLINE FRAGMENTS. Synthesize news into institutional narratives.\n"
            "6. NO PLACEHOLDERS. Never use 'null', 'undefined', or 'N/A'.\n\n"
            "Return a JSON object with these exact sections:\n"
            "industry_snapshot: (JSON object with 'market_cap', 'market_weight', 'avg_pe', 'avg_growth', 'breadth', 'relative_perf', 'returns' (Day, YTD, 1Y, 3Y, 5Y), 'risk_classification')\n"
            "industry_structure: (How it makes money, sub-industries breakdown, value chain, business models, margin structure)\n"
            "cycle_positioning: (Phase: early/mid/late/euphoria/capitulation/recovery and why based on capex/valuations)\n"
            "institutional_narrative: (What Wall Street believes: 'AI inference demand', 'Rate cut duration play', etc.)\n"
            "macro_sensitivity: (Object mapping 'Rates', 'Inflation', 'Oil', 'USD', 'China' to LOW/MEDIUM/HIGH)\n"
            "competitive_landscape: (Market share dynamics, moats, pricing power, regulatory barriers, and Largest Companies listed)\n"
            "capital_flow_intel: (ETF flows, institutional accumulation, short interest trends)\n"
            "valuation_framework: (Comparison vs S&P 500, historical premium/discount, bubble/compression risk)\n"
            "risk_matrix: (Sector-specific risks only: China export controls, IRA pricing, inventory glut)\n"
            "strategic_outlook: (Base/Bull/Bear cases for 6-18 months, rerating triggers)\n"
            "real_time_intelligence: (Synthesis of recent earnings commentary, M&A activity, and regulatory actions)\n"
            "orion_synthesis: (JSON object with 'stance' (STRONG BUY/BUY/NEUTRAL/CAUTION/RISK-OFF), 'score', 'catalysts'[], 'risks'[], 'best_positioned'[], 'overextended'[])."
        )
        
        user_content = (
            f"Synthesize institutional industry report for {industry}. "
            f"Context: {json.dumps(intel, default=str)}. "
            f"S&P 500 Baseline: P/E {sp500_pe}, Growth {sp500_growth*100}%."
        )
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                        "temperature": 0.25,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                        "response_format": {"type": "json_object"}
                    },
                )
            if r.status_code == 200:
                return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"INDUSTRY SYNTHESIS ERROR: {e}")

    # Fallback logic remains as backup...

    # Fallback (Using Intel)
    m = intel["metrics"]
    s = intel["institutional_sentiment"]
    leaders = ", ".join(intel["market_leaders"][:3])
    etfs = ", ".join(intel["benchmark_etfs"].keys()) or "sector benchmarks"

    return {
        "industry_overview": f"The {industry} sector encompasses companies involved in {intel.get('topology', {}).keys() or 'core industry operations'}. It is a critical component of the global economy, driving {s.lower()} institutional interest.",
        "market_structure": f"The sector currently exhibits a {s.lower()} bias with a sample breadth of {m['market_breadth']}. Barriers to entry are defined by technical scale and capital intensity among leaders like {leaders}.",
        "value_chain_position": f"Market participants are positioned across a complex value chain, from infrastructure to end-user applications, with primary benchmarks tracked via {etfs}.",
        "key_industry_drivers": f"Primary drivers are currently centered on {intel['thematic_headlines'][0] if intel['thematic_headlines'] else 'secular demand trends'} and institutional rotation into {leaders}.",
        "industry_risks": f"Structural risks include multiple compression and potential cyclical headwinds. A median P/E of {m['median_pe']} suggests valuation levels are {s == 'BULLISH' and 'stretched' or 'aligned'} with current growth.",
        "competitive_landscape": f"The landscape is dominated by {leaders}, where scale and technical integration serve as primary moats. Market concentration remains high relative to historical benchmarks.",
        "industry_cycle_position": f"The sector is in a {s.lower()} cycle phase, characterized by {m['session_perf']} session performance and selective institutional accumulation.",
        "structural_themes": f"Long-term structural growth is predicated on {intel['thematic_headlines'][1] if len(intel['thematic_headlines']) > 1 else 'ongoing digital transformation'} and the evolution of {industry} ecosystems.",
        "industry_outlook": {
            "score": stats.get("avgScore", 50),
            "confidence": "Medium",
            "bullish_factors": ["Institutional accumulation", "Positive session breadth"],
            "bearish_factors": ["Valuation levels", "Macro uncertainty"]
        }
    }
