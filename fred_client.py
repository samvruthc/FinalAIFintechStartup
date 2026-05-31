import asyncio
import httpx
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

# --- CONFIG ---
# Priority: Env Var > Hardcoded fallback from user
FRED_API_KEY = os.environ.get("FRED_API_KEY", "a13971ac077b1b898941a25561e4c26f")
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

CACHE_DIR = Path(__file__).parent / "data" / "fred_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

class FredClient:
    """
    Client for Federal Reserve Economic Data (FRED).
    Handles requests with caching for macro indicators and FX rates.
    """
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    def _get_cache_path(self, series_id: str) -> Path:
        return CACHE_DIR / f"{series_id}.json"

    def _get_cached(self, series_id: str, ttl_seconds: int) -> Optional[Any]:
        path = self._get_cache_path(series_id)
        if not path.exists():
            return None
        
        try:
            with open(path, 'r') as f:
                cached = json.load(f)
            
            ts = cached.get("_timestamp", 0)
            if (datetime.now().timestamp() - ts) > ttl_seconds:
                return None
            
            return cached.get("data")
        except:
            return None

    def _set_cached(self, series_id: str, data: Any):
        path = self._get_cache_path(series_id)
        try:
            with open(path, 'w') as f:
                json.dump({
                    "_timestamp": datetime.now().timestamp(),
                    "data": data
                }, f)
        except:
            pass

    async def get_series_latest(self, series_id: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get the latest observation for a given series ID.
        Daily series cache for 1 hour, others for 24 hours.
        """
        # Define TTL: 1 hour for FX and daily rates, 24 hours for others
        is_daily = series_id.startswith("DEX") or series_id in ["FEDFUNDS", "DGS10", "DGS2", "VIXCLS", "SP500", "DCOILWTICO"]
        ttl = 3600 if is_daily else 86400

        if not force_refresh:
            cached = self._get_cached(series_id, ttl)
            if cached:
                return cached

        # We request 5 observations to ensure we skip over any "." (holidays)
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5
        }

        try:
            print(f"DEBUG: Fetching latest FRED {series_id}...")
            resp = await self.client.get(FRED_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            observations = data.get("observations", [])
            # Filter out "." values
            valid_observations = [o for o in observations if o.get("value") and o["value"] != "."]
            
            if not valid_observations:
                print(f"WARNING: No valid observations for FRED {series_id} in last 5 entries.")
                return None
            
            result = valid_observations[0]
            print(f"DEBUG: FRED {series_id} latest valid value: {result.get('value')} on {result.get('date')}")
            self._set_cached(series_id, result)
            return result
        except Exception as e:
            print(f"FRED ERROR ({series_id}): {e}")
            if hasattr(e, 'response'):
                print(f"DEBUG: FRED Raw Error Response: {e.response.text}")
            return None

    async def get_series_history(self, series_id: str, days: int = 365, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get historical observations for a given series ID.
        """
        cache_key = f"{series_id}_h{days}"
        if not force_refresh:
            cached = self._get_cached(cache_key, 86400) # 24h cache for history
            if cached:
                return cached

        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_start": start_date,
            "sort_order": "asc"
        }

        try:
            resp = await self.client.get(FRED_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            result = data.get("observations", [])
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            print(f"FRED HISTORY ERROR ({series_id}): {e}")
            return []

    async def get_series_at_date(self, series_id: str, target_date: str) -> Optional[float]:
        """
        Get the value of a series at or before a specific date.
        """
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_end": target_date,
            "sort_order": "desc",
            "limit": 5 # Look at last 5 to skip holidays
        }
        try:
            resp = await self.client.get(FRED_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            
            # Find first non-dot value
            for obs in observations:
                val = obs.get("value")
                if val and val != ".":
                    return float(val)
            return None
        except Exception as e:
            print(f"DEBUG: FRED get_series_at_date error ({series_id} at {target_date}): {e}")
            return None

    async def close(self):
        await self.client.aclose()

fred_client = FredClient()
