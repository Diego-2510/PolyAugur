"""
PolyAugur Configuration
Author: Diego Ringleb | 2026-02-28
"""

import os
from dotenv import load_dotenv

load_dotenv()

# API Endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

# Mistral [file:1] Abschnitt 6
MISTRAL_MODEL = "mistral-large-latest"
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# Detection thresholds [file:1] Abschnitt 5.5
CONFIDENCE_THRESHOLD = 0.45   # Pre-screen threshold (lowered - Phase 3 has no holder data yet)
MISTRAL_THRESHOLD = 0.45      # Score required to send to Mistral
MAX_POSITION_SIZE_PCT = 0.10

# Polling
POLL_INTERVAL_SEC = 30

# Cache
CACHE_TTL_MIN = 5

# Rate Limits
DATA_API_RATE_LIMIT = 15
BACKOFF_DELAYS = [0.3, 0.6, 1.2, 2.4, 5.0]

# ── Scaling Parameters ──────────────────────────────────────────────────
# Gamma API: max 100 per request, pagination via offset
MARKETS_PER_PAGE = 100            # Max per API request
MAX_PAGES = 10                    # Fetch up to 10 pages = 1000 markets
MIN_VOLUME_24H = 10000            # $10k minimum 24h volume filter

# Two-tier filtering:
# Tier 1: AnomalyDetector (fast, free) – runs on ALL fetched markets
# Tier 2: Mistral (slow, costs $) – runs only on Tier-1 flagged markets
MAX_MISTRAL_CALLS_PER_CYCLE = 10  # Budget: max 10 Mistral calls per 30s cycle
MISTRAL_BATCH_SIZE = 3            # Batch 3 markets per Mistral prompt (3x cheaper [file:1])
