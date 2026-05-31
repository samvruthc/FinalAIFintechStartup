# ORION deploy checklist

## GitHub Pages (`https://samvruthc.github.io/1orionmax/`)

Upload these files to the **1orionmax** repo (same paths):

- `index.html` (root)
- `data/top100.json` (required for discover paths)

Push to `main`; Pages must be enabled for that repo.

## Railway backend (`https://1orionmax-production.up.railway.app`)

**Required files in the deploy root:**

- `main.py`
- `orion_report.py` ← PDF / multi-stock reports
- `top100_data.py` ← if missing, the app crashes on import → **502**
- `data/top100.json`, `data/custom_tickers.json`
- `requirements.txt`, `Procfile`, `runtime.txt`, `nixpacks.toml`

**Start command:** `python main.py` (see `Procfile` / `nixpacks.toml`)

**Verify after deploy:**

```bash
curl -sS https://1orionmax-production.up.railway.app/api/orion/health
```

Expect: `{"status":"ok","universe":...}`

If you see **502** or timeout: open Railway → Deployments → **View logs** and fix the traceback (often `ModuleNotFoundError: top100_data` or missing dependencies).

## Frontend ↔ backend

`index.html` points at:

`PRODUCTION_API = 'https://1orionmax-production.up.railway.app/api'`

When the backend is down, the UI uses **limited mode** (embedded names only; browsers cannot call Yahoo directly due to CORS). Full prices, memos, news, and charts require the API.

### AI Research Assistant

- **Endpoint:** `POST /api/orion/chat` with `{ "message": "...", "ticker": "NVDA" }` (ticker optional)
- Uses **live Yahoo Finance** data: quotes, P/E, 52-week range, news, industry peers, risk factors, confidence score
- **Optional:** set `OPENAI_API_KEY` (and `OPENAI_MODEL`, default `gpt-4o-mini`) on Railway for natural-language answers grounded in the same live JSON facts
- Without OpenAI, ORION uses deterministic synthesis (no hallucinated prices)

### Research brain (built into the page — no server)

**ASK ORION** runs entirely in the browser. It does **not** call Railway or `/api/orion/chat`.

1. Fetches live prices from Yahoo (chart API) in your browser  
2. Runs the ORION verdict engine locally  
3. Answers directly: *"Is SNPS a good buy?"* → **HOLD / BUY / AVOID** with conviction and reasoning  

Push the latest **`index.html`** to GitHub Pages — no backend deploy required for the assistant.

Optional: redeploy Railway for the rest of the dashboard (watchlist, memos, SEC). The chat works without it.

### Report builder + PDF export

- **UI:** scroll to **REPORTS · EXPORT** or click **BUILD REPORT** in the hero
- **Modes:**
  - **Selected stocks** — comma-separated tickers (or + Watchlist / + Selected)
  - **Whole industry** — ranks Top 100 names in that sector (e.g. Semiconductors, Automotive)
- **API:** `POST /api/orion/report` with `{ "mode": "stocks", "tickers": ["NVDA","TSLA"] }` or `{ "mode": "industry", "industry": "Automotive", "limit": 12 }`
- **PDF:** `POST /api/orion/report/pdf` (same body) → downloads `orion-report-*.pdf` via **fpdf2**
- If the server PDF fails, the UI falls back to **html2pdf** in the browser

Requires Railway deploy with `fpdf2` in `requirements.txt`.

Questions like *"Is SNPS a good buy?"* get:

- **ORION VERDICT** banner (STRONG BUY / BUY / HOLD / AVOID)
- A plain-English yes/no/maybe answer first
- **Why it could work** vs **What worries us** (specific to that stock's numbers)
- Live price, P/E, 52-week position in one line

### Run locally (recommended for dev)

```bash
cd "orion ai"
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Open **http://127.0.0.1:8000/** (not the raw `index.html` file). The app serves the UI and fetches quotes from Yahoo on the server.
