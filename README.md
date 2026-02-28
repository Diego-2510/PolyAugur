# PolyAugur - Polymarket Insider Detection

Mistral-LLM powered tool detecting anomalies (volume spikes, whale bets) on Polymarket. Simulates paper trades with live Streamlit dashboard & P&L tracking. 48h Hackathon MVP [file:1].

## Quickstart
1. `git clone https://github.com/Diego-2510/PolyAugur`
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` → `.env`, add MISTRAL_API_KEY
5. `streamlit run app.py`

## Architecture
- **Data**: Gamma/Data API (markets, holders, volumes)
- **Anomaly**: Multi-layer scoring → Mistral JSON-mode
- **Trading**: Paper portfolio, PL metrics
- **UI**: Streamlit tabs (Signals, Portfolio)

**Disclaimer**: Simulation only. No financial advice.

See `mache-es-sehr-viel-ausfuhrlicher.md` for full spec [file:1].
