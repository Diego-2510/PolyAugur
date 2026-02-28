"""
PolyAugur Configuration
Author: Diego Ringleb | Phase 11 | 2026-02-28
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Detection Thresholds ─────────────────────────────────────────────────
CONFIDENCE_THRESHOLD      = 0.45
MISTRAL_THRESHOLD         = 0.45
MAX_POSITION_SIZE_PCT     = 0.10

# ── Polling ──────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30

# ── Cache ────────────────────────────────────────────────────────────────
CACHE_TTL_MIN = 5

# ── Rate Limits ──────────────────────────────────────────────────────────
DATA_API_RATE_LIMIT = 15
BACKOFF_DELAYS      = [0.3, 0.6, 1.2, 2.4, 5.0]

# ── Scaling (Phase 11: 10.000+ markets) ──────────────────────────────────
MARKETS_PER_PAGE            = 100
MAX_PAGES                   = 100     # 100 × 100 = 10,000 markets max
MIN_VOLUME_24H              = 10_000  # Filter stays: nur relevante Märkte

MAX_MISTRAL_CALLS_PER_CYCLE = 10
MISTRAL_BATCH_SIZE          = 3

# ── Trade Analysis ───────────────────────────────────────────────────────
TRADE_ANALYSIS_ENABLED       = True
MAX_TRADE_ANALYSIS_PER_CYCLE = 15

# ── Wallet Profiler (Phase 11) ───────────────────────────────────────────
WALLET_PROFILING_ENABLED     = True
MAX_WALLET_PROFILES_PER_CYCLE = 10    # Max wallet profile API calls
WALLET_CACHE_TTL_HOURS       = 24     # Profile cache lifetime

# ── Signal Store ─────────────────────────────────────────────────────────
SIGNAL_DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")

# ── Health Monitoring ────────────────────────────────────────────────────
HEALTH_PING_EVERY_N_CYCLES = 100
MAX_CONSECUTIVE_ERRORS     = 5
