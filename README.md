# 🔮 PolyAugur

**Polymarket Insider Signal Detection System**

Detects anomalous trading activity on [Polymarket](https://polymarket.com) that may indicate informed/insider trading. Combines statistical anomaly detection, LLM analysis (Mistral), on-chain whale intelligence (CLOB), and real-time Telegram alerts.

---

## Architecture

```
Gamma API (markets) → Anomaly Detector → Mistral LLM → CLOB Trade Analyzer → Signal Store → Telegram
      ↓                     ↓                 ↓               ↓                    ↓
  1000+ markets        Score & filter    Validate top     Whale detection     SQLite + alerts
  per cycle            (free, fast)      candidates       (on-chain)          + performance
```

## Pipeline (9-Step Cycle)

| Step | Component | Description |
|------|-----------|-------------|
| 1 | `data_fetcher.py` | Fetch active markets from Gamma API (paginated, sports filtered) |
| 2 | `data_fetcher.py` | Build market snapshots with real baseline volumes |
| 3 | `orchestrator.py` | Price velocity enrichment (cross-cycle delta) |
| 4 | `anomaly_detector.py` | Statistical anomaly detection (volume spike, price conviction, topic sensitivity) |
| 5 | `orchestrator.py` | Filter markets with score ≥ 0.45 for LLM analysis |
| 6 | `mistral_analyzer.py` | Mistral LLM validation (batched 3/prompt, JSON-mode, whale context) |
| 7 | `trade_analyzer.py` | CLOB on-chain trade analysis (whale detection, wallet concentration) |
| 8 | `orchestrator.py` | Whale confidence boost → deduplicate → store → Telegram notify |
| 9 | `performance_tracker.py` | Automatic outcome resolution & P&L tracking (every 10 cycles) |

## Detection Capabilities

- **Volume Spikes**: 3x–50x baseline volume surges
- **Price Conviction**: Extreme YES/NO prices with high volume
- **Topic Sensitivity**: Geopolitical, central bank, regulatory markets weighted higher
- **Time Horizon Filter**: Markets >365 days auto-penalized (no insider advantage)
- **Whale Detection**: Trades >$5k, wallet concentration, directional bias
- **Timing Bursts**: Last-hour volume vs historical average
- **Coordinated Buying**: Multiple whales with aligned directional bias

## Quick Start

```bash
# Clone & setup
git clone https://github.com/Diego-2510/PolyAugur.git
cd PolyAugur
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: add MISTRAL_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Run
python run.py --once          # Single detection cycle
python run.py                 # Continuous polling (30s intervals)
python run.py --cycles 10     # Run 10 cycles
python run.py --stats         # Show DB statistics
python run.py --check         # Check signal outcomes
```

## Dashboard

```bash
python -m src.dashboard                    # Last 24h signals (table)
python -m src.dashboard --hours 72         # Last 72h
python -m src.dashboard --whales           # Only whale-flagged signals
python -m src.dashboard --performance      # Win/loss breakdown
python -m src.dashboard --export csv       # Export to CSV
python -m src.dashboard --export html      # Export dark-mode HTML report
python -m src.dashboard --all --export html # Full history HTML report
```

## Telegram Alerts

Signals are pushed to Telegram in real-time with:
- Trade direction (BUY_YES / BUY_NO / HOLD)
- Confidence score with whale boost indicator
- Risk level, holding period, position size
- 🐋 On-chain intelligence (whale count, top wallet %, directional bias, burst score)
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

| Resource | Per Cycle | Per Day (24h, 30s interval) |
|----------|-----------|---------------------------|
| Gamma API | ~10 calls | ~2,880 calls (free) |
| Mistral API | 3–10 calls | ~860–2,880 calls |
| CLOB API | 0–15 calls | ~0–4,320 calls (free) |
| **Estimated cost** | ~$0.01 | **~$3–8/day** |

## Signal Flow Example

```
1. Gamma API returns 1,200 active markets
2. Anomaly Detector scores all 1,200 → 15 flagged (score ≥ 0.45)
3. Mistral validates 15 in 5 batched calls → 4 confirmed (confidence ≥ 0.65)
4. CLOB analyzes 4 confirmed → 1 has whale activity (3 whales, 89% directional BUY)
5. Whale boost: confidence 0.78 → 0.88 (+0.10)
6. Signal saved to SQLite, pushed to Telegram with 🐋 tag
7. After market resolves: outcome checked, P&L recorded
```

## Project Structure

```
PolyAugur/
├── run.py                      # Production entrypoint (--once, --cycles, --stats, --check)
├── app.py                      # Streamlit dashboard (legacy)
├── config.py                   # All configuration & thresholds
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
│
├── src/
│   ├── __init__.py
│   ├── data_fetcher.py         # Gamma API client + snapshot builder
│   ├── anomaly_detector.py     # Statistical anomaly scoring (volume, price, topic)
│   ├── mistral_analyzer.py     # Mistral LLM signal validation (batched, whale context)
│   ├── trade_analyzer.py       # CLOB on-chain whale detection
│   ├── signal_store.py         # SQLite persistence + dedup + migration
│   ├── telegram_notifier.py    # Telegram push notifications + daily reports
│   ├── performance_tracker.py  # Automatic outcome resolution & P&L tracking
│   └── dashboard.py            # CLI signal explorer + CSV/HTML export
│
├── config/
│   └── __init__.py
├── tests/
│   └── __init__.py
├── data/                       # SQLite database (gitignored)
├── logs/                       # Daily log files (gitignored)
└── exports/                    # CSV/HTML exports (gitignored)
```

## Key Design Decisions

- **Two-tier detection**: Free statistical pre-screening on all markets, costly LLM only on flagged candidates → 95% cost reduction
- **Batched Mistral calls**: 3 markets per prompt → 3x fewer API calls
- **Time horizon filter**: Markets >365 days automatically penalized (insider info has no advantage on long-term speculation)
- **Whale confidence boost**: On-chain evidence increases confidence by up to +15%, never decreases it
- **Deduplication**: 4-hour window prevents repeat signals for the same market
- **Graceful degradation**: Rule-based fallback when Mistral API unavailable

## Tech Stack

- **Python 3.11+**
- **Mistral AI** — LLM signal validation (mistral-small-latest)
- **SQLite** — Signal persistence
- **Telegram Bot API** — Real-time alerts
- **Polymarket Gamma API** — Market data
- **Polymarket CLOB API** — On-chain trade data

## Author

**Diego Ringleb** — Berlin, 2026

## License

MIT

---

> **Disclaimer**: This is a research/educational tool. Signals are not financial advice. Use at your own risk. Always do your own research before trading on prediction markets.