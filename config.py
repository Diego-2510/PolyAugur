"""
PolyAugur Configuration
Author: Diego Ringleb | Phase 13 | 2026-03-01
MODIFIED: Elite Quality Filter - max precision insider detection
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

# ── Detection Thresholds (ELITE QUALITY FILTER) ──────────────────────────
# Raised from 0.55 → 0.70: only signals where ALL layers converge pass
CONFIDENCE_THRESHOLD  = 0.70
# Raised from 0.55 → 0.65: only strong anomalies go to Mistral
MISTRAL_THRESHOLD     = 0.65
# Mistral final bar raised from 0.65 → 0.75
MISTRAL_CONFIRM_MIN   = 0.75

MAX_POSITION_SIZE_PCT = 0.10

# ── Polling ──────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30

# ── Cache ────────────────────────────────────────────────────────────────
CACHE_TTL_MIN = 5

# ── Rate Limits ──────────────────────────────────────────────────────────
DATA_API_RATE_LIMIT = 15
BACKOFF_DELAYS      = [0.3, 0.6, 1.2, 2.4, 5.0]

# ── Scaling ──────────────────────────────────────────────────────────────
MARKETS_PER_PAGE = 100
MAX_PAGES        = 100
# Raised from 15_000 → 50_000: eliminates illiquid markets
MIN_VOLUME_24H   = 50_000

MAX_MISTRAL_CALLS_PER_CYCLE = 8
MISTRAL_BATCH_SIZE          = 4

# ── Trade Analysis ───────────────────────────────────────────────────────
TRADE_ANALYSIS_ENABLED       = True
MAX_TRADE_ANALYSIS_PER_CYCLE = 15

# ── Wallet Profiler ──────────────────────────────────────────────────────
WALLET_PROFILING_ENABLED      = True
MAX_WALLET_PROFILES_PER_CYCLE = 10
WALLET_CACHE_TTL_HOURS        = 24

# ── Elite Quality Filters (NEW) ──────────────────────────────────────────
# Minimum volume spike ratio required — below 5x ignored entirely
MIN_SPIKE_RATIO          = 5.0
# Minimum vol/liquidity pressure ratio
MIN_VOL_LIQ_RATIO        = 3.0
# Only CRITICAL topics pass (not ELEVATED) — set to False to allow ELEVATED too
REQUIRE_CRITICAL_TOPIC   = True
# Maximum days-to-close to consider (imminent events only)
MAX_DAYS_TO_CLOSE        = 14
# Recency surge: min % of total volume that must be from last 24h
MIN_RECENCY_RATIO        = 0.60
# Require at least 1 INSIDER or SMART_MONEY wallet among top traders
REQUIRE_SMART_WALLET     = True

# ── Signal Store ─────────────────────────────────────────────────────────
SIGNAL_DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")

# ── Health Monitoring ────────────────────────────────────────────────────
HEALTH_PING_EVERY_N_CYCLES = 100
MAX_CONSECUTIVE_ERRORS     = 5
