"""PolyAugur runtime configuration.

Phase 15 uses blacklist-style pre-filtering: markets are screened with
quantitative gates and explicit category exclusions, while topic matches act
as score boosters rather than hard inclusion gates.

The Mistral threshold is an optional LLM-assisted review threshold. Model
confidence is not treated as a calibrated probability.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# API endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Optional Mistral-assisted review
MISTRAL_MODEL = "mistral-large-latest"
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# Optional Telegram notifications
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Detection thresholds
CONFIDENCE_THRESHOLD = 0.45
MISTRAL_THRESHOLD = 0.45
MISTRAL_CONFIRM_MIN = 0.80
MAX_POSITION_SIZE_PCT = 0.10

# Polling
POLL_INTERVAL_SEC = 30

# Cache
CACHE_TTL_MIN = 5

# Rate limits / retry delays
DATA_API_RATE_LIMIT = 15
BACKOFF_DELAYS = [0.3, 0.6, 1.2, 2.4, 5.0]

# Gamma keyset scan cap: 100 pages x 100 markets = at most 10,000 per scan.
MARKETS_PER_PAGE = 100
MAX_PAGES = 100

# Minimum 24h volume in USD.
MIN_VOLUME_24H = 30_000

# Optional LLM-assisted review budget.
MAX_MISTRAL_CALLS_PER_CYCLE = 8
MISTRAL_BATCH_SIZE = 4

# Elite gates
MIN_SPIKE_RATIO = 2.5
MIN_VOL_LIQ_RATIO = 1.5
MAX_DAYS_TO_CLOSE = 90
MIN_RECENCY_RATIO = 0.15

# Topic multiplier remains a score booster; it is not a hard gate.
REQUIRE_CRITICAL_TOPIC = False

# Trade analysis
TRADE_ANALYSIS_ENABLED = True
MAX_TRADE_ANALYSIS_PER_CYCLE = 10

# Wallet profiler
WALLET_PROFILING_ENABLED = True
MAX_WALLET_PROFILES_PER_CYCLE = 10
WALLET_CACHE_TTL_HOURS = 24

# Signal store
SIGNAL_DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")

# Health monitoring
HEALTH_PING_EVERY_N_CYCLES = 100
MAX_CONSECUTIVE_ERRORS = 5
