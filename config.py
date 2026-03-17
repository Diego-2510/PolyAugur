"""
PolyAugur Configuration
Author: Diego Ringleb | Phase 15 — Blacklist Mode | 2026-03-17

Filter philosophy (Phase 15):
  BLACKLIST statt Whitelist.
  Alle Märkte werden analysiert, AUSSER explizit ausgeschlossene Kategorien.

  Vorher (Phase 14): nur CRITICAL/ELEVATED Topics → Mistral
  Jetzt  (Phase 15): alle Märkte mit Spike/Recency → Mistral,
                     außer EXCLUSION_KEYWORDS (Tweets, Krypto-Preise, Wetter, Sport)

  Qualitäts-Gate ist jetzt Mistral selbst (≥ 0.80),
  nicht mehr der Topic-Gate im Orchestrator.

  Topic-Multiplier bleibt: gibt Insider-Märkten höheren Score → bevorzugte
  Sortierung vor Mistral. Kein Hard-Gate mehr.
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
# Phase 15: Blacklist-Modus
#
# base_score max = 0.60 (Volume 0.35 + Price 0.25)
# Ohne Topic-Boost: multiplier = 1.0 → max score = 0.60
# Mit Recency-Surge (×1.40): max score = 0.84
# Mit Topic-Boost CRITICAL (×1.40) + Surge (×1.40): max = 1.18 → cap 1.80
#
# CONFIDENCE_THRESHOLD = 0.45:
#   → Erreichbar ohne Topic-Boost bei 3.5x Spike + Preisdruck
#   → Zwingt trotzdem zu messbarem Spike + aktivem Surge
#   → Mistral bei 0.80 ist der eigentliche Qualitäts-Gate
CONFIDENCE_THRESHOLD = 0.45

# Identisch — kein redundanter zweiter Filter
MISTRAL_THRESHOLD    = 0.45

# Mistral Confirmation: unveränderter hoher Standard
MISTRAL_CONFIRM_MIN  = 0.80

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

# Mindestvolumen 24h: 30.000 USD
MIN_VOLUME_24H = 30_000

# Phase 15: Mehr Märkte zu Mistral möglich, da kein Topic-Gate
# 8 Calls × Batch 4 = max 32 Märkte pro Zyklus
MAX_MISTRAL_CALLS_PER_CYCLE = 8
MISTRAL_BATCH_SIZE          = 4

# ── Elite Gates ──────────────────────────────────────────────────────────
MIN_SPIKE_RATIO   = 2.5   # Volume >= 2.5x Baseline
MIN_VOL_LIQ_RATIO = 1.5
MAX_DAYS_TO_CLOSE = 90    # FOMC 3-Meeting-Zyklen bis 90 Tage
MIN_RECENCY_RATIO = 0.15  # Surge muss noch aktiv sein

# Phase 15: KEIN Topic-Gate mehr — Blacklist-Modus
# Topic-Multiplier im AnomalyDetector bleibt als Score-Booster aktiv,
# wird aber nicht mehr als Hard-Gate verwendet.
REQUIRE_CRITICAL_TOPIC = False

# ── Trade Analysis ───────────────────────────────────────────────────────
TRADE_ANALYSIS_ENABLED       = True
MAX_TRADE_ANALYSIS_PER_CYCLE = 10

# ── Wallet Profiler ──────────────────────────────────────────────────────
WALLET_PROFILING_ENABLED      = True
MAX_WALLET_PROFILES_PER_CYCLE = 10
WALLET_CACHE_TTL_HOURS        = 24

# ── Signal Store ─────────────────────────────────────────────────────────
SIGNAL_DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")

# ── Health Monitoring ────────────────────────────────────────────────────
HEALTH_PING_EVERY_N_CYCLES = 100
MAX_CONSECUTIVE_ERRORS     = 5
