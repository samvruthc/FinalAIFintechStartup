from openbb import obb
import json

def check_news():
    print("Checking obb.news.world...")
    try:
        res = obb.news.world(limit=5)
        print("Success!")
    except Exception as e:
        print(f"Failed: {e}")
    
    print("\nChecking obb.news.company...")
    try:
        res = obb.news.company(symbol="AAPL", limit=5)
        print("Success!")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    check_news()
