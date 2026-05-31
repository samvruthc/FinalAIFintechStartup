"""ORION multi-stock and industry report builders + PDF export."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional

from fpdf import FPDF


def build_stocks_report(
    tickers: List[str],
    sections_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build an institutional-grade multi-stock report.
    Each section contains deep qualitative layers.
    """
    sections: List[Dict[str, Any]] = []
    
    titles = {
        "executive_summary": "I. Executive Summary & Investment Thesis",
        "business_overview": "II. Business Model & Strategic Overview",
        "competitive_positioning": "III. Competitive Landscape & Moat Analysis",
        "financial_quality": "IV. Financial Quality & Valuation Context",
        "growth_drivers": "V. Key Growth Drivers & Strategic Themes",
        "risks": "VI. Risk Assessment & Mitigation Factors",
        "catalysts": "VII. Catalyst Engine & Re-rating Triggers",
        "intelligence_summary": "VIII. ORION Intelligence Synthesis",
    }

    for data in sections_data:
        t = data["ticker"]
        q = data["quote"]
        analysis = data["analysis"]
        
        # Qualitative Layers
        layers = []
        for key, title in titles.items():
            if analysis.get(key):
                layers.append({"title": title, "content": analysis[key]})

        sections.append(
            {
                "ticker": t,
                "name": q.get("name") or t,
                "score": data.get("score"),
                "conviction": data.get("conviction"),
                "range_pct": data.get("range_pct"),
                "price": q.get("price"),
                "changePct": q.get("changePct"),
                "peRatio": q.get("peRatio"),
                "forwardPE": q.get("forwardPE"),
                "pegRatio": q.get("pegRatio"),
                "priceToSales": q.get("priceToSales"),
                "priceToBook": q.get("priceToBook"),
                "enterpriseToEbitda": q.get("enterpriseToEbitda"),
                "revenueGrowth": q.get("revenueGrowth"),
                "operatingMargins": q.get("operatingMargins"),
                "returnOnEquity": q.get("returnOnEquity"),
                "instOwnership": q.get("instOwnership"),
                "shortPercentOfFloat": q.get("shortPercentOfFloat"),
                "rsi": q.get("rsi"),
                "sma_50": q.get("sma_50"),
                "volatility": q.get("volatility"),
                "drawdown": q.get("drawdown"),
                "ret_ytd": q.get("ret_ytd"),
                "ret_1y": q.get("ret_1y"),
                "marketCap": q.get("marketCap"),
                "fiftyTwoWeekHigh": q.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow": q.get("fiftyTwoWeekLow"),
                "industry_intel": analysis.get("industry_intelligence"),
                "layers": layers,
                "bull_factors": analysis.get("bull_factors", []),
                "bear_factors": analysis.get("bear_factors", []),
                "news": data.get("news", [])[:4],
            }
        )

    scores = [s["score"] for s in sections if s.get("score") is not None]
    
    # Generate overall executive summary
    exec_summary = ""
    if sections:
        best = max(sections, key=lambda x: x.get("score") or 0)
        
        exec_summary = f"ORION Institutional Research has completed its multi-layer synthesis of {len(sections)} assets. "
        exec_summary += f"The highest signal conviction is identified in {best['ticker']} ({best['score']}/100), "
        exec_summary += "reflecting superior business quality and valuation support relative to the current basket."

    return {
        "reportType": "stocks",
        "title": f"ORION Equity Research - {', '.join(tickers[:5])}"
        + ("..." if len(tickers) > 5 else ""),
        "subtitle": f"Institutional Grade Analysis: {len(sections)} constituents",
        "generatedAt": str(datetime.utcnow()),
        "tickers": tickers,
        "sections": sections,
        "executiveSummary": exec_summary,
        "summary": {
            "count": len(sections),
            "avgScore": round(sum(scores) / len(scores), 1) if scores else None,
        },
    }


def build_industry_report(
    industry: str,
    stats: Dict[str, Any],
    analysis: Dict[str, Any],
    leaders: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a comprehensive industry report focusing exclusively on live data from Yahoo Finance.
    All qualitative categories and narrative sections have been removed.
    """
    exec_summary = (
        f"ORION Strategy Note: {industry}. "
        f"Real-time analysis of {stats.get('sampleSize', 0)} sector constituents. "
        f"Market breadth is {stats.get('breadth')}% positive across analyzed leaders."
    )

    return {
        "reportType": "industry",
        "title": f"ORION Industry Report: {industry}",
        "subtitle": f"Institutional Strategy & Sector Intelligence Dashboard",
        "industry": industry,
        "generatedAt": str(datetime.utcnow()),
        "industryStats": stats,
        "snapshot": {
            "market_cap": stats.get("marketCap"),
            "market_weight": stats.get("marketWeight"),
            "industries_count": stats.get("industriesCount"),
            "companies_count": stats.get("companiesCount"),
            "returns": stats.get("returnProfile", {}),
            "top_movers": stats.get("topMovers", []),
            "holdings": stats.get("holdings", []),
            "avg_pe": stats.get("avgPe"),
            "avg_growth": f"{stats.get('avgGrowth', 0):+.1f}%",
            "breadth": stats.get("breadth"),
            "advancers": stats.get("advancers", 0),
            "decliners": stats.get("decliners", 0),
            "avgScore": stats.get("avgScore", 50),
            "sentiment": stats.get("sentiment", "NEUTRAL"),
            "avg_change": stats.get("avgChange")
        },
        "sub_industries": stats.get("sub_industries", []),
        "topology": stats.get("topology", {}),
        "benchmark_etfs": stats.get("benchmarkEtfs", {}),
        "leaders": leaders,
        "executiveSummary": exec_summary,
        "sections": [], # REMOVED ALL CATEGORIES
    }


def _safe_text(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    s = str(text).replace("\u2014", "-").replace("\u2013", "-")
    s = "".join(c if ord(c) < 256 else " " for c in s)
    return s[:max_len]


def _fmt_compact(n: Optional[float]) -> str:
    if n is None or n == 0:
        return ""
    n = float(n)
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"${n / 1e3:.2f}K"
    return f"${n:,.0f}"


class OrionReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(14, 116, 144)
        self.cell(0, 6, "ORION / Autonomous Financial Intelligence", ln=True)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 4, datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), ln=True)
        self.ln(1)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"Page {self.page_no()} / {{nb}}  |  Not investment advice", align="C")

    def section_heading(self, label: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, _safe_text(label.upper(), 80), ln=True)

    def metrics_grid(self, sec: Dict[str, Any]):
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(100, 116, 139)
        
        groups = [
            ("Performance", [
                ("RSI", f"{sec.get('rsi'):.1f}" if sec.get("rsi") else "N/A"),
                ("Vol (30d)", f"{sec.get('volatility', 0)*100:.1f}%" if sec.get("volatility") else "N/A"),
                ("1Y Ret", f"{sec.get('ret_1y', 0)*100:.1f}%" if sec.get("ret_1y") else "N/A"),
                ("52W Range", f"{sec.get('range_pct', 0):.0f}%" if sec.get("range_pct") is not None else "N/A"),
            ]),
            ("Valuation", [
                ("P/E (T)", f"{sec.get('peRatio'):.1f}x" if sec.get("peRatio") else "N/A"),
                ("Fwd P/E", f"{sec.get('forwardPE'):.1f}x" if sec.get("forwardPE") else "N/A"),
                ("P/S", f"{sec.get('priceToSales'):.2f}x" if sec.get("priceToSales") else "N/A"),
                ("Mkt Cap", _fmt_compact(sec.get("marketCap"))),
            ]),
            ("Quality", [
                ("Rev Growth", f"{sec.get('revenueGrowth', 0)*100:.1f}%" if sec.get("revenueGrowth") else "N/A"),
                ("Op Margin", f"{sec.get('operatingMargins', 0)*100:.1f}%" if sec.get("operatingMargins") else "N/A"),
                ("ROE", f"{sec.get('returnOnEquity', 0)*100:.1f}%" if sec.get("returnOnEquity") else "N/A"),
                ("Inst Own", f"{sec.get('instOwnership', 0)*100:.1f}%" if sec.get("instOwnership") else "N/A"),
            ])
        ]
        
        y_start = self.get_y()
        col_w = self.epw / 3
        
        for i, (title, items) in enumerate(groups):
            self.set_xy(self.l_margin + (i * col_w), y_start)
            self.set_font("Helvetica", "B", 7)
            self.set_text_color(100, 116, 139)
            self.cell(col_w, 5, title.upper(), ln=True)
            
            self.set_font("Helvetica", "", 7)
            self.set_text_color(51, 65, 85)
            for label, val in items:
                self.set_x(self.l_margin + (i * col_w))
                self.cell(col_w * 0.6, 4, label)
                self.set_font("Helvetica", "B", 7)
                self.set_text_color(14, 116, 144)
                self.cell(col_w * 0.4, 4, val, ln=True)
                self.set_font("Helvetica", "", 7)
                self.set_text_color(51, 65, 85)
        
        self.ln(2)

    def body_text(self, text: str, size: int = 9):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", size)
        self.set_text_color(40, 40, 40)
        self.multi_cell(self.epw, 4.5, _safe_text(text, 900))

    def bullet_block(self, label: str, items: List[str], r: int, g: int, b: int):
        if not items:
            return
        self.section_heading(label)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(71, 85, 105)
        for item in items[:5]:
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 4, f"  - {_safe_text(item, 320)}")
        self.ln(1)


def report_to_pdf(report: Dict[str, Any]) -> bytes:
    pdf = OrionReportPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    epw = pdf.epw

    # Cover
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(epw, 10, _safe_text(report.get("title", "ORION Research Report"), 100))
    pdf.ln(1)

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 116, 139)
    sub = report.get("subtitle") or ""
    if sub:
        pdf.multi_cell(epw, 5, _safe_text(sub, 200))

    summary = report.get("summary") or {}
    meta_parts = [f"Generated {_safe_text(str(report.get('generatedAt', ''))[:19], 30)} UTC"]
    if summary.get("avgScore") is not None:
        meta_parts.append(f"Avg ORION score {summary['avgScore']}/100")
    
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(epw, 4, _safe_text(" | ".join(meta_parts), 240))
    pdf.ln(2)

    is_industry = report.get("reportType") == "industry"

    # Sector Stats for Industry Reports
    stats = report.get("industryStats")
    if stats:
        # High-Density Industry Snapshot
        snapshot = report.get("snapshot", {})
        if snapshot:
            pdf.section_heading("Industry Snapshot")
            pdf.set_fill_color(248, 250, 252)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(100, 116, 139)
            
            snap_row1 = [
                f"Market Cap: {snapshot.get('market_cap', '—')}",
                f"Market Weight: {snapshot.get('market_weight', '—')}",
                f"Avg P/E: {snapshot.get('avg_pe', '—')}",
                f"Avg Growth: {snapshot.get('avg_growth', '—')}"
            ]
            pdf.cell(0, 6, "  |  ".join(snap_row1), ln=True, fill=True)
            
            # Return Profile Table
            rets = snapshot.get("returns", {})
            pdf.ln(1)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 5, "SECTOR PERFORMANCE TABLE:", ln=True)
            
            pdf.set_fill_color(241, 245, 249)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(20, 5, "Period", fill=True, align="C")
            pdf.cell(20, 5, "Return", fill=True, align="C")
            pdf.cell(20, 5, "Period", fill=True, align="C")
            pdf.cell(20, 5, "Return", fill=True, align="C")
            pdf.cell(20, 5, "Period", fill=True, align="C")
            pdf.cell(20, 5, "Return", fill=True, align="C")
            pdf.ln(5)
            
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(20, 5, "Day", align="C")
            pdf.set_text_color(22, 163, 74) if "+" in rets.get("day_return", "") else pdf.set_text_color(220, 38, 38)
            pdf.cell(20, 5, rets.get("day_return", "—"), align="C")
            
            pdf.set_text_color(60, 60, 60)
            pdf.cell(20, 5, "YTD", align="C")
            pdf.set_text_color(22, 163, 74) if "+" in rets.get("ytd_return", "") else pdf.set_text_color(220, 38, 38)
            pdf.cell(20, 5, rets.get("ytd_return", "—"), align="C")
            
            pdf.set_text_color(60, 60, 60)
            pdf.cell(20, 5, "1-Year", align="C")
            pdf.set_text_color(22, 163, 74) if "+" in rets.get("1y_return", "") else pdf.set_text_color(220, 38, 38)
            pdf.cell(20, 5, rets.get("1y_return", "—"), align="C")
            pdf.ln(5)
            
            pdf.set_text_color(60, 60, 60)
            pdf.cell(20, 5, "3-Year", align="C")
            pdf.set_text_color(22, 163, 74) if "+" in rets.get("3y_return", "") else pdf.set_text_color(220, 38, 38)
            pdf.cell(20, 5, rets.get("3y_return", "—"), align="C")
            
            pdf.set_text_color(60, 60, 60)
            pdf.cell(20, 5, "5-Year", align="C")
            pdf.set_text_color(22, 163, 74) if "+" in rets.get("5y_return", "") else pdf.set_text_color(220, 38, 38)
            pdf.cell(20, 5, rets.get("5y_return", "—"), align="C")
            pdf.ln(7)

        # Top Movers & Holdings
        top_movers = snapshot.get("top_movers", [])
        holdings = snapshot.get("holdings", [])
        if top_movers or holdings:
            pdf.section_heading("Sector Dynamics & Holdings")
            y_start = pdf.get_y()
            col_w = epw / 2
            
            # Top Movers Column
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(col_w, 5, "TOP MOVERS (SYMBOLS):", ln=False)
            
            # Holdings Column
            pdf.set_x(pdf.l_margin + col_w)
            pdf.cell(col_w, 5, "LARGEST HOLDINGS / WEIGHTS:", ln=True)
            
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            max_rows = max(len(top_movers), len(holdings))
            for i in range(max_rows):
                if i < len(top_movers):
                    pdf.set_x(pdf.l_margin)
                    pdf.cell(col_w, 5, f"  • {top_movers[i]}", ln=False)
                if i < len(holdings):
                    pdf.set_x(pdf.l_margin + col_w)
                    h = holdings[i]
                    pdf.cell(col_w, 5, f"  • {h['ticker']} ({h['weight']})", ln=True)
                else:
                    pdf.ln(5)
            pdf.ln(2)

        # Sub-Industry Weightings
        sub_industries = report.get("sub_industries", [])
        if sub_industries:
            pdf.section_heading("Sub-Industry Weightings")
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 5, "Internal sector concentration and weighting distribution:", ln=True)
            pdf.ln(1)
            
            # Grid layout for sub-industries
            for i in range(0, len(sub_industries), 2):
                row = sub_industries[i:i+2]
                for item in row:
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.set_text_color(14, 116, 144)
                    pdf.cell(60, 6, f"{item['name']}:", ln=False)
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(60, 60, 60)
                    pdf.cell(20, 6, item['weight'], ln=False)
                pdf.ln(6)
            pdf.ln(2)

    exec_sum = report.get("executiveSummary") or summary.get("executiveSummary") or ""
    if exec_sum:
        pdf.section_heading("Executive summary")
        pdf.body_text(exec_sum, 10)
        pdf.ln(4)

    if is_industry:
        # Benchmark ETFs
        etfs = report.get("benchmark_etfs", {})
        if etfs:
            pdf.section_heading("Sector Benchmarks & Capital Flow")
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 5, "Live performance of primary industry-tracking instruments:", ln=True)
            pdf.ln(1)
            for t, data in etfs.items():
                pdf.set_font("Helvetica", "B", 10)
                pdf.set_text_color(14, 116, 144)
                pdf.cell(20, 6, f"{t}", ln=False)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(60, 60, 60)
                chg = data.get('changePct', 0)
                pdf.cell(0, 6, f"{data.get('name', '')}  |  Price: ${data.get('price', 0):.2f}  |  Session: {chg:+.2f}%", ln=True)
            pdf.ln(3)

        # Leaders summary
        leaders = report.get("leaders") or []
        if leaders:
            pdf.section_heading("Industry Leaders & Benchmark Table")
            
            # Table Header
            pdf.set_fill_color(241, 245, 249)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(100, 116, 139)
            cols = [
                ("Ticker", 15), ("Signal", 12), ("Growth", 15), 
                ("Margin", 15), ("P/E", 12), ("Inst", 12), ("Momentum", 15)
            ]
            for label, w in cols:
                pdf.cell(w, 6, label, ln=False, fill=True, align="C")
            pdf.ln(6)
            
            # Table Body
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(60, 60, 60)
            for l in leaders[:15]:
                pdf.cell(15, 5, l.get("ticker", ""), ln=False, align="C")
                pdf.cell(12, 5, f"{l.get('score', 0):.0f}", ln=False, align="C")
                pdf.cell(15, 5, f"{l.get('revenueGrowth', 0)*100:+.1f}%", ln=False, align="C")
                pdf.cell(15, 5, f"{l.get('operatingMargins', 0)*100:.1f}%", ln=False, align="C")
                pdf.cell(12, 5, f"{l.get('peRatio', '—')}x", ln=False, align="C")
                pdf.cell(12, 5, f"{l.get('instOwnership', 0)*100:.0f}%", ln=False, align="C")
                pdf.cell(15, 5, f"{l.get('changePct', 0):+.1f}%", ln=True, align="C")
            pdf.ln(4)
            
    else:
        # Institutional Stock-by-Stock sections
        for i, sec in enumerate(report.get("sections", []), 1):
            if i > 1:
                pdf.add_page()

            pdf.ln(5)
            # Section Header
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(15, 23, 42)
            ticker = sec.get("ticker", "N/A")
            name = sec.get("name", "")
            pdf.cell(pdf.get_string_width(ticker) + 2, 8, _safe_text(ticker), ln=False)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 8, f"  {_safe_text(name)}", ln=True)
            
            # Signal & Price Subheader
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(14, 116, 144)
            score = sec.get("score") or 50
            conviction = sec.get("conviction") or (round(score/10))
            pdf.cell(40, 5, f"SIGNAL SCORE: {score:.0f}/100", ln=False)
            pdf.cell(40, 5, f"CONVICTION: {conviction:.0f}/10", ln=False)
            
            price = sec.get("price")
            chg = sec.get("changePct") or 0
            if price:
                pdf.set_text_color(15, 23, 42)
                pdf.cell(0, 5, f"PRICE: ${price:,.2f} ({chg:+.2f}%)", align="R", ln=True)
            else:
                pdf.ln(5)

            pdf.ln(1)
            pdf.metrics_grid(sec)

            # Industry Intelligence Layer (Dedicated Section)
            industry_intel = sec.get("industry_intel")
            if industry_intel:
                pdf.section_heading("Industry Intelligence Layer")
                pdf.set_fill_color(241, 245, 249)
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(15, 23, 42)
                
                # We map the 9 sections
                ind_titles = {
                    "industry_overview": "I. Industry Overview",
                    "market_structure": "II. Market Structure",
                    "value_chain_position": "III. Value Chain Position",
                    "key_industry_drivers": "IV. Key Industry Drivers",
                    "industry_risks": "V. Industry Risks",
                    "competitive_landscape": "VI. Competitive Landscape",
                    "industry_cycle_position": "VII. Industry Cycle Position",
                    "structural_themes": "VIII. Structural Themes"
                }
                
                for key, label in ind_titles.items():
                    content = industry_intel.get(key)
                    if content:
                        pdf.set_font("Helvetica", "B", 9)
                        pdf.set_text_color(14, 116, 144)
                        pdf.cell(0, 6, label, ln=True)
                        pdf.set_font("Helvetica", "", 9)
                        pdf.set_text_color(60, 60, 60)
                        pdf.multi_cell(0, 4, _safe_text(content, 500))
                        pdf.ln(1)
                
                # Industry Outlook Score
                outlook = industry_intel.get("industry_outlook")
                if outlook:
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.set_text_color(14, 116, 144)
                    pdf.cell(0, 6, "IX. ORION Industry Outlook Score", ln=True)
                    
                    pdf.set_fill_color(248, 250, 252)
                    pdf.set_font("Helvetica", "B", 10)
                    pdf.set_text_color(15, 23, 42)
                    pdf.cell(0, 8, f"Score: {outlook.get('score', 0)}/100  |  Confidence: {outlook.get('confidence', '')}", ln=True, fill=True)
                    
                    pdf.set_font("Helvetica", "", 8)
                    pdf.set_text_color(22, 163, 74)
                    pdf.cell(epw/2, 5, f"Bulls: {', '.join(outlook.get('bullish_factors', []))}", ln=False)
                    pdf.set_text_color(220, 38, 38)
                    pdf.cell(epw/2, 5, f"Bears: {', '.join(outlook.get('bearish_factors', []))}", ln=True)
                pdf.ln(4)

            # Qualitative Layers (A-H)
            layers = sec.get("layers") or []
            for layer in layers:
                pdf.section_heading(layer["title"])
                pdf.body_text(layer["content"], 10)
                pdf.ln(1)

            # Bull/Bear
            pdf.bullet_block("Bull Drivers", sec.get("bull_factors", []), 52, 211, 153)
            pdf.bullet_block("Risk Factors", sec.get("bear_factors", []), 251, 113, 133)

            # News
            news = sec.get("news", [])
            if news:
                pdf.section_heading("Recent Intelligence Headlines")
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(71, 85, 105)
                for n in news[:4]:
                    title = n.get("title") or n.get("headline") or ""
                    source = n.get("source") or ""
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(epw, 4, f"  - {_safe_text(title, 140)} ({_safe_text(source, 30)})")
            pdf.ln(2)

    pdf.add_page()
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        epw,
        4,
        "Disclaimer: This report is generated from live public market data and ORION quantitative signals. "
        "It is for informational purposes only and is not investment advice. Past performance does not "
        "guarantee future results. Verify all figures before making investment decisions.",
    )

    out = BytesIO()
    pdf.output(out)
    return out.getvalue()
