"""
PolyAugur Configuration
Author: Diego Ringleb | Phase 7 | 2026-02-28
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Endpoints ────────────────────────────────────────────────────────
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE  = "https://data-api.polymarket.com"
CLOB_API_BASE  = "https://clob.polymarket.com"

# ── Mistral ──────────────────────────────────────────────────────────────
MISTRAL_MODEL   = "mistral-large-latest"
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# ── Telegram ─────────────────────────────────────────────────────────────
# Get token:   @BotFather → /newbot
# Get chat_id: send a message to your bot, then
#              GET https://api.telegram.org/bot{TOKEN}/getUpdates
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Detection Thresholds ─────────────────────────────────────────────────
CONFIDENCE_THRESHOLD      = 0.45   # AnomalyDetector pre-screen
MISTRAL_THRESHOLD         = 0.45   # Min score to send to Mistral
MAX_POSITION_SIZE_PCT     = 0.10

# ── Polling ──────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30

# ── Cache ────────────────────────────────────────────────────────────────
CACHE_TTL_MIN = 5

# ── Rate Limits ──────────────────────────────────────────────────────────
DATA_API_RATE_LIMIT = 15
BACKOFF_DELAYS      = [0.3, 0.6, 1.2, 2.4, 5.0]

# ── Scaling ──────────────────────────────────────────────────────────────
MARKETS_PER_PAGE            = 100
MAX_PAGES                   = 10
MIN_VOLUME_24H              = 10_000

MAX_MISTRAL_CALLS_PER_CYCLE = 10
MISTRAL_BATCH_SIZE          = 3

# ── Trade Analysis (Phase 7) ─────────────────────────────────────────────
TRADE_ANALYSIS_ENABLED       = True
MAX_TRADE_ANALYSIS_PER_CYCLE = 15   # Max CLOB API calls per cycle

# ── Signal Store ─────────────────────────────────────────────────────────
SIGNAL_DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")
