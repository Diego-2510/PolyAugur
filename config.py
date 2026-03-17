"""
PolyAugur Configuration
Author: Diego Ringleb | Phase 14 — Precision Insider Filter | 2026-03-17

Filter philosophy:
  - Lieber 0 Signals als 1 falsches Signal
  - Nur Märkte, wo JEMAND mit Insider-Wissen die Antwort kennt
  - Hoher Volumenschwellwert eliminiert Noise-Märkte
  - Mistral als zweite Schicht: confirm >= 0.80 = sehr hohe Hürde
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
# AnomalyDetector: score = base_score × topic_multiplier
# base_score: Volume (max 0.35) + Price (max 0.25) + Holder (0.0) = max 0.60
# topic_multiplier: 0.20 (long-term) bis 1.80 (critical + imminent + surge)
#
# CONFIDENCE_THRESHOLD = 0.70:
#   → Ohne Topic-Boost (mult 1.0): base >= 0.70 nötig → unmöglich (max 0.60)
#     → Zwingt zwingend einen Topic-Match
#   → Mit CRITICAL (×1.40) + imminent (×1.25) = ×1.75: base >= 0.40 → erreichbar
#   → Effekt: KEIN Signal ohne gleichzeitig starkem Volume UND Insider-Topic
CONFIDENCE_THRESHOLD = 0.70   # Anomaly-Score → Mistral weiterleiten

# Mistral Pre-Filter: nur Score >= 0.65 kommt zu Mistral
# (redundant zu 0.70 als extra Sicherheitspuffer)
MISTRAL_THRESHOLD    = 0.65

# Mistral Confirmation: Mistral muss >= 0.80 Konfidenz vergeben
# → "Bin ich sehr sicher, dass hier Insider aktiv sind?"
# → 0.80 lässt kaum Zweifel zu — sehr wenige Signals kommen durch
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
# → Insider-Trades hinterlassen messbare Spuren erst ab diesem Volumen
# → Eliminiert thin markets mit zufälligem Noise
MIN_VOLUME_24H = 30_000

# Mistral: 5 Calls × Batch 4 = max 20 Märkte pro Zyklus
# → Zwingt das System, nur die allerbesten Anomalien weiterzugeben
# → Weniger API-Kosten bei höherer Trefferquote
MAX_MISTRAL_CALLS_PER_CYCLE = 5
MISTRAL_BATCH_SIZE          = 4

# ── Elite Gates ──────────────────────────────────────────────────────────
# Mindest-Spike-Ratio: Volume muss >= 2.5x Baseline sein
# → 1.5x–2.0x ist normales Markt-Rauschen, 2.5x ist auffällig
MIN_SPIKE_RATIO = 2.5

# Vol/Liquidität: Kaufdruck auf den Markt muss spürbar sein
MIN_VOL_LIQ_RATIO = 1.5

# Märkte die in > 60 Tagen schließen: Insider-Vorteil nimmt stark ab
MAX_DAYS_TO_CLOSE = 60

# Recency: min. 25% des gesamten Volumens muss in letzten 24h sein
# → Surge muss JETZT passieren, nicht historisch irgendwann
MIN_RECENCY_RATIO = 0.25

# Nur CRITICAL oder ELEVATED Topics können Anomalie-Status erreichen
# → Kein Bitcoin-Preis, kein Sport, kein Wetter
REQUIRE_CRITICAL_TOPIC = True

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
