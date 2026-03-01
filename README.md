# 🔮 PolyAugur

**Polymarket Insider Signal Detection System**

Detects anomalous trading activity on [Polymarket](https://polymarket.com) that may indicate informed/insider trading. Combines multi-layer statistical anomaly detection, LLM analysis (Mistral), on-chain trade intelligence (CLOB), wallet profiling, and real-time Telegram alerts.

---

## Architecture

```
Gamma API (10,000+ markets)
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Statistical Pre-Filter (free, all markets)          │
│  Volume spikes · Price conviction · Topic sensitivity · Time   │
│  → score ≥ 0.40 passed to LLM                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │  ~10–15 flagged markets
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — Mistral LLM Validation (batched, 4/prompt)          │
│  Structured JSON reasoning · Confidence ≥ 0.60 confirmed       │
└────────────────────────┬────────────────────────────────────────┘
                         │  ~3–8 confirmed signals
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — CLOB On-Chain Analysis + Wallet Profiling           │
│  Whale detection · Wallet concentration · Directional bias     │
│  Trader classification (INSIDER/SMART_MONEY/GAMBLER/REGULAR)   │
│  → Confidence boost up to +15%                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
              SQLite  ·  Telegram  ·  HTML Dashboard
```

## Pipeline (9-Step Cycle)

| Step | Component | Description |
|------|-----------|-------------|
| 1 | `data_fetcher.py` | Fetch active markets from Gamma API (paginated, sports filtered) |
| 2 | `data_fetcher.py` | Build market snapshots with real baseline volumes |
| 3 | `orchestrator.py` | Price velocity enrichment (cross-cycle delta tracking) |
| 4 | `anomaly_detector.py` | Multi-layer anomaly scoring (volume spike, price conviction, two-tier topic sensitivity) |
| 5 | `orchestrator.py` | Filter markets with score ≥ 0.40 for LLM analysis |
| 6 | `mistral_analyzer.py` | Mistral LLM validation (batched 4/prompt, JSON-mode, whale context) |
| 7 | `trade_analyzer.py` | CLOB on-chain trade analysis (whale detection, wallet concentration, burst timing) |
| 8 | `orchestrator.py` | Whale confidence boost → deduplicate → store → Telegram notify |
| 9 | `performance_tracker.py` | Automatic outcome resolution & P&L tracking (every 10 cycles) |

## Detection Capabilities

### Two-Tier Insider Topic System

PolyAugur distinguishes between markets where insider knowledge is **definitely possible** vs. merely **plausible**:

**Critical Topics (×1.40 multiplier)** — Someone definitely knows the outcome first:
- Military operations (Pentagon, NSC decisions)
- Central bank decisions (FOMC rate decisions, emergency cuts)
- Regulatory rulings (SEC/FDA approvals, ETF decisions)
- Executive decisions (pardons, nominations, executive orders)
- Corporate M&A (mergers, acquisitions, CEO changes)

**Elevated Topics (×1.15 multiplier)** — Insider info is plausible:
- Geopolitical negotiations (ceasefire, peace deals, treaties)
- Trade policy (tariffs, sanctions, trade deals)
- Legal/DOJ (indictments, arrests, impeachment)
- Energy decisions (OPEC production cuts)
- Tech regulation (antitrust, bans)

**No Boost** — Generic markets without insider edge:
- Crypto price predictions, weather bets, entertainment, general elections

### Other Detection Layers

- **Volume Spikes**: 2x–50x baseline volume surges (1.5x scores minimally)
- **Price Conviction**: Extreme YES/NO prices with high volume-to-liquidity pressure
- **Time Horizon Filter**: Markets >365 days penalized (no insider advantage on long-term speculation)
- **Sudden Volume Surge**: 24h volume >60% of all-time volume → highly suspicious
- **Whale Detection**: Trades >$5k, wallet concentration >40%, directional bias >85%
- **Timing Bursts**: Last-hour volume vs historical hourly average (3x+ = suspicious)

### Wallet Profiler

Each whale's trading history is analyzed and classified:
- 🧠 **INSIDER**: Win rate >65% OR new account with large bets → confidence +5% per whale
- 🐋 **SMART_MONEY**: Win rate >60%, significant capital invested → confidence +3%
- 🎰 **GAMBLER**: Win rate <40% with ≥10 resolved bets → confidence −5%
- 👤 **REGULAR**: Neutral impact on signal confidence

## Quick Start

```bash
# Clone & setup
git clone https://github.com/Diego-2510/PolyAugur.git
cd PolyAugur
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: add MISTRAL_API_KEY (required), TELEGRAM_BOT_TOKEN + CHAT_ID (optional)

# Run
python run.py --once          # Single detection cycle
python run.py                 # Continuous polling (30s intervals)
python run.py --cycles 10     # Run 10 cycles
python run.py --stats         # Show DB statistics
python run.py --check         # Check signal outcomes
python run.py --health        # Pre-flight system check
```

## Dashboard & Exports

```bash
python -m src.dashboard                    # Last 24h signals (CLI table)
python -m src.dashboard --hours 72         # Last 72h
python -m src.dashboard --whales           # Only whale-flagged signals
python -m src.dashboard --performance      # Win/loss breakdown
python -m src.dashboard --export csv       # Export to CSV
python -m src.dashboard --export html      # Export dark-mode HTML report
python -m src.dashboard --all --export html # Full history HTML report
```

The HTML report is a self-contained dark-mode dashboard with:
- Stats grid (total signals, win rate, avg confidence, signal volume)
- Trade distribution bar (BUY YES / BUY NO / HOLD)
- Interactive signal table with confidence bars, outcome badges, and direct Polymarket links

## Telegram Alerts

Signals are pushed to Telegram in real-time with:
- Trade direction (BUY_YES / BUY_NO / HOLD)
- Confidence score with whale boost indicator
- Risk level, suggested holding period, position size
- Anomaly type classification and market context
- Daily performance reports with win rate

Setup: Create a bot via [@BotFather](https://t.me/BotFather), get your chat ID, add both to `.env`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MISTRAL_API_KEY` | Yes | Mistral AI API key ([console.mistral.ai](https://console.mistral.ai)) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for signal alerts |
| `SIGNAL_DB_PATH` | No | SQLite database path (default: `data/signals.db`) |

## Cost Profile

| Resource | Per Cycle | Per Day (24h @ 30s) |
|----------|-----------|---------------------|
| Gamma API | ~10 calls | ~2,880 calls (free) |
| Mistral API | 3–12 calls | ~860–3,400 calls |
| CLOB API | 0–15 calls | ~0–4,320 calls (free) |
| **Estimated cost** | ~$0.01 | **~$3–8/day** |

## Signal Flow Example

```
1. Gamma API returns 1,200 active markets (volume ≥ $8,000)
2. Anomaly Detector scores all 1,200 → 12 flagged (score ≥ 0.40)
3. Mistral validates 12 in 3 batched calls → 5 confirmed (confidence ≥ 0.60)
4. CLOB analyzes 5 confirmed → 1 has whale activity (3 whales, 89% directional BUY)
5. Wallet Profiler: 2/3 whales classified as INSIDER (win rate >65%)
6. Whale boost: confidence 0.72 → 0.82 (+0.10)
7. Signal saved to SQLite, pushed to Telegram
8. After market resolves: outcome checked, P&L recorded
```

## Project Structure

```
PolyAugur/
├── run.py                      # Production entrypoint (--once, --cycles, --stats, --check, --health)
├── config.py                   # All configuration & thresholds
├── requirements.txt            # Python dependencies
├── polyaugur.service           # systemd service for 24/7 deployment
├── .env.example                # Environment variable template
│
├── src/
│   ├── __init__.py
│   ├── data_fetcher.py         # Gamma API client + snapshot builder
│   ├── anomaly_detector.py     # Multi-layer anomaly scoring (volume, price, two-tier topics)
│   ├── mistral_analyzer.py     # Mistral LLM signal validation (batched, JSON-mode)
│   ├── trade_analyzer.py       # CLOB on-chain whale detection
│   ├── wallet_profiler.py      # Wallet history analysis & trader classification
│   ├── signal_store.py         # SQLite persistence + dedup + schema migration
│   ├── telegram_notifier.py    # Telegram push notifications + daily reports
│   ├── performance_tracker.py  # Automatic outcome resolution & P&L tracking
│   ├── dashboard.py            # CLI signal explorer + CSV/HTML export
│   ├── health.py               # Pre-flight checks, health monitoring, error tracking
│   └── retry.py                # Exponential backoff decorator for API resilience
│
├── data/                       # SQLite database (gitignored)
├── logs/                       # Log files (gitignored)
└── exports/                    # CSV/HTML exports (gitignored)
```

## Key Design Decisions

- **Two-tier detection**: Free statistical pre-screening on all markets, costly LLM only on flagged candidates → ~95% cost reduction vs. analyzing every market with LLM
- **Two-tier topic system**: Critical insider topics (×1.40) vs. elevated (×1.15) vs. no boost — prevents false positives from generic crypto/weather/entertainment markets
- **Wallet profiling**: Not all whales are equal — classifying traders by historical performance avoids boosting signals driven by known gamblers
- **Batched Mistral calls**: 4 markets per prompt → fewer API calls, structured JSON output
- **Time horizon filter**: Markets >365 days automatically penalized (insider info decays over time)
- **Whale confidence boost**: On-chain evidence increases confidence by up to +15%, never decreases it
- **Deduplication**: 4-hour window prevents repeat signals for the same market
- **Graceful degradation**: Rule-based fallback when Mistral API is unavailable
- **Production-ready**: systemd service file, health monitoring, exponential backoff, auto-restart on errors

## Tech Stack

- **Python 3.11+**
- **Mistral AI** (`mistral-large-latest`) — LLM signal validation
- **SQLite** — Signal persistence, deduplication, outcome tracking
- **Telegram Bot API** — Real-time alerts & daily reports
- **Polymarket Gamma API** — Market data (10,000+ markets)
- **Polymarket CLOB API** — On-chain trade data (whale detection)

## Acknowledgments

This project was built during the **Mistral AI Hackathon** (March 2026) and uses the following open-source libraries and APIs:

- **[Mistral AI](https://mistral.ai)** — LLM signal validation via `mistral-large-latest` ([mistralai](https://pypi.org/project/mistralai/) Python SDK)
- **[Polymarket](https://polymarket.com)** — Market data via [Gamma API](https://gamma-api.polymarket.com) and [CLOB API](https://clob.polymarket.com)
- **[Requests](https://docs.python-requests.org)** — HTTP client for API communication
- **[NumPy](https://numpy.org)** — Numerical computations for anomaly scoring
- **[python-dotenv](https://github.com/theskumar/python-dotenv)** — Environment variable management
- **[Pandas](https://pandas.pydata.org)** — Data manipulation
- **[Plotly](https://plotly.com/python/)** — Visualization library
- **[Streamlit](https://streamlit.io)** — Web app framework

## Author

**Diego Ringleb** — Berlin, 2026

## License

MIT

---

> **Disclaimer**: This is a research/educational tool built for a hackathon. It monitors publicly available market data for statistical anomalies — it does not facilitate, encourage, or automate any trading. Signals are not financial advice. Use at your own risk. Always do your own research before trading on prediction markets.