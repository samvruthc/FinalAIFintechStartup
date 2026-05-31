import requests
import json

def test_endpoints():
    base_url = "http://localhost:8000/api/orion"
    
    # 1. Test Quotes (Watchlist)
    print("--- Testing /quotes (Watchlist) ---")
    tickers = "AAPL,TSLA,MSFT"
    try:
        r = requests.get(f"{base_url}/quotes?tickers={tickers}")
        print(f"Status: {r.status_code}")
        print(f"Response: {json.dumps(r.json(), indent=2)}")
    except Exception as e:
        print(f"Quotes failed: {e}")

    # 2. Test Trending (Hot Picks)
    print("\n--- Testing /trending (Hot Picks) ---")
    try:
        r = requests.get(f"{base_url}/trending?limit=5")
        print(f"Status: {r.status_code}")
        # Only print first pick to keep it short
        data = r.json()
        if data.get("picks"):
            print(f"First Pick: {json.dumps(data['picks'][0], indent=2)}")
        else:
            print("No picks found")
    except Exception as e:
        print(f"Trending failed: {e}")

if __name__ == "__main__":
    test_endpoints()
