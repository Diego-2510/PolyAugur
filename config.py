import os
from dotenv import load_dotenv

load_dotenv()

# API Endpoints (Abschnitt 4 [file:1])
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

# Mistral (Abschnitt 6 [file:1])
MISTRAL_MODEL = "mistral-large-latest"
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# Konstanten (Abschnitt 3.2, 5 [file:1])
MIN_VOLUME_24H = 50000
CONFIDENCE_THRESHOLD = 0.7
MAX_POSITION_SIZE_PCT = 0.10
POLL_INTERVAL_SEC = 30
CACHE_TTL_MIN = 5

# Rate Limits Data API: 150 req/10s [file:1]
DATA_API_RATE_LIMIT = 15  # req per 10s safe
BACKOFF_DELAYS = [0.3, 0.6, 1.2, 2.4, 5.0]  # sec
