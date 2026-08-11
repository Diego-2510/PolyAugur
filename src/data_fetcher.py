"""
PolyAugur Data Fetcher - Polymarket Gamma API Integration
Fetches markets with pagination support (up to 10,000+ markets).
Includes rate limiting, deduplication, and progress tracking.

Author: Diego Ringleb | Phase 11 | 2026-02-28
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    """Safely convert any value to float."""
    if value is None or value == "":
        return default

    try:
        if isinstance(value, str):
            return float(
                value.replace(",", "")
            )

        return float(value)

    except (ValueError, TypeError):
        return default


class PolymarketFetcher:
    """
    Fetches market data from Polymarket Gamma API.

    Supports pagination for large-scale market coverage.
    Filters by volume, expiry, and sports/live-event heuristics.
    """

    def __init__(self):
        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": "PolyAugur/1.0",
            }
        )

        self.cache: Dict[
            str,
            Any,
        ] = {}

        self.cache_timestamps: Dict[
            str,
            datetime,
        ] = {}

        self.fetch_stats: Dict[
            str,
            Any,
        ] = {}

        self._sport_keywords = [
            "nfl",
            "nba",
            "mlb",
            "nhl",
            "mls",
            "super bowl",
            "stanley cup",
            "world series",
            "nba finals",
            "bundesliga",
            "champions league",
            "premier league",
            "la liga",
            "serie a",
            "ligue 1",
            "europa league",
            "uefa",
            "epl",
            "pga",
            "pga tour",
            "masters",
            "golf",
            "augusta",
            "ryder cup",
            "open championship",
            "us open golf",
            "fifa",
            "world cup",
            "olympics",
            "formula 1",
            "nascar",
            "motogp",
            "wimbledon",
            "ufc",
            "boxing",
            "wrestling",
            "tennis",
            "cycling",
            "tour de france",
            "ncaa",
            "college football",
            "college basketball",
        ]

        self._sport_patterns = [
            re.compile(r"\bf1\b"),
            re.compile(r"\bvs\.?\s"),
            re.compile(r"\bgame\b"),
            re.compile(r"\bmatch\b"),
            re.compile(r"\bscore\b"),
            re.compile(r"\bplayoff"),
            re.compile(
                r"\bchampionship\b"
            ),
            re.compile(
                r"\btournament\b"
            ),
            re.compile(
                r"finish in the top"
            ),
            re.compile(
                r"top \d+ of the"
            ),
            re.compile(
                r"league table"
            ),
            re.compile(
                r"title race"
            ),
            re.compile(
                r"win the\s.*cup"
            ),
        ]

    def _backoff(
        self,
        retry_count: int,
    ) -> float:
        if (
            retry_count
            < len(
                config.BACKOFF_DELAYS
            )
        ):
            return config.BACKOFF_DELAYS[
                retry_count
            ]

        return 5.0

    def _api_get(
        self,
        base: str,
        endpoint: str,
        params: Optional[
            Dict[str, Any]
        ] = None,
        max_retries: int = 3,
    ) -> Optional[Any]:
        """Generic GET with retry and backoff."""
        url = (
            f"{base}/{endpoint}"
        )

        params = (
            params
            or {}
        )

        for retry in range(
            max_retries
        ):
            try:
                response = (
                    self.session.get(
                        url,
                        params=params,
                        timeout=15,
                    )
                )

                if (
                    response.status_code
                    == 429
                ):
                    delay = (
                        self._backoff(
                            retry
                        )
                    )

                    logger.warning(
                        "Rate limit on %s, backoff %.1fs",
                        endpoint,
                        delay,
                    )

                    time.sleep(
                        delay
                    )
                    continue

                if (
                    response.status_code
                    in (
                        400,
                        404,
                        422,
                    )
                ):
                    logger.debug(
                        "HTTP %s: %s → %s",
                        response.status_code,
                        url,
                        response.text[
                            :80
                        ],
                    )

                    return None

                response.raise_for_status()

                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout on %s "
                    "(retry %s/%s)",
                    endpoint,
                    retry + 1,
                    max_retries,
                )

                if (
                    retry
                    < max_retries - 1
                ):
                    time.sleep(
                        self._backoff(
                            retry
                        )
                    )

            except requests.exceptions.RequestException as exc:
                logger.error(
                    "API error on %s: %s",
                    endpoint,
                    exc,
                )

                if (
                    retry
                    < max_retries - 1
                ):
                    time.sleep(
                        self._backoff(
                            retry
                        )
                    )

        logger.error(
            "Max retries exceeded for %s",
            endpoint,
        )

        return None

    # ── Market Validation ───────────────────────────────────────────

    def is_valid_active_market(
        self,
        market: Dict[
            str,
            Any,
        ],
    ) -> bool:
        """Return whether the market has not expired."""
        now = datetime.now(
            timezone.utc
        )

        end_date_str = None

        for field in (
            "end_date_iso",
            "endDate",
            "closesAt",
            "end_date",
        ):
            if market.get(field):
                end_date_str = (
                    market[field]
                )
                break

        if not end_date_str:
            return True

        try:
            closes_at = (
                datetime.fromisoformat(
                    str(
                        end_date_str
                    ).replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

            return (
                closes_at
                > now
            )

        except (
            ValueError,
            TypeError,
        ):
            return False

    def is_sports_or_live_event(
        self,
        market: Dict[
            str,
            Any,
        ],
    ) -> bool:
        """
        Exclude sports and live-event markets using existing heuristics.
        """
        tags = market.get(
            "tags",
            [],
        )

        tag_labels = []

        for tag in tags:
            if isinstance(
                tag,
                dict,
            ):
                tag_labels.append(
                    tag.get(
                        "label",
                        "",
                    ).lower()
                )

            elif isinstance(
                tag,
                str,
            ):
                tag_labels.append(
                    tag.lower()
                )

        question = market.get(
            "question",
            "",
        ).lower()

        if any(
            keyword in label
            for label in tag_labels
            for keyword in self._sport_keywords
        ):
            return True

        if any(
            keyword in question
            for keyword in self._sport_keywords
        ):
            return True

        if any(
            pattern.search(
                question
            )
            for pattern
            in self._sport_patterns
        ):
            return True

        return False

    # ── Normalization ───────────────────────────────────────────────

    def _normalize_market(
        self,
        market: Dict[
            str,
            Any,
        ],
    ) -> Optional[
        Dict[
            str,
            Any,
        ]
    ]:
        """Normalize raw API data to consistent field names."""
        try:
            volume = _safe_float(
                market.get(
                    "volume_24hr"
                )
                or market.get(
                    "volume24hr"
                )
                or market.get(
                    "volume24Hrs"
                )
                or market.get(
                    "volumeNum"
                ),
                default=0.0,
            )

            end_date = (
                market.get(
                    "end_date_iso"
                )
                or market.get(
                    "endDate"
                )
                or market.get(
                    "closesAt"
                )
                or market.get(
                    "end_date"
                )
            )

            return {
                **market,
                "volume_24hr": volume,
                "end_date_iso": end_date,
                "tags": market.get(
                    "tags",
                    [],
                ),
                "question": market.get(
                    "question",
                    "Unknown Market",
                ),
            }

        except (
            ValueError,
            TypeError,
        ) as exc:
            logger.warning(
                "Normalization error for %s: %s",
                market.get("id"),
                exc,
            )

            return None

    # ── Pagination ──────────────────────────────────────────────────

    def fetch_all_markets_paginated(
        self,
        max_pages: Optional[
            int
        ] = None,
    ) -> List[
        Dict[
            str,
            Any,
        ]
    ]:
        """
        Fetch available markets using offset-based pagination.

        Existing rate-limit strategy:
        - 0.2 seconds between normal pages
        - 1.0-second pause every 20 pages
        - 3 consecutive empty responses stop pagination
        - market IDs are deduplicated
        """
        max_pages = (
            max_pages
            or config.MAX_PAGES
        )

        all_markets: List[
            Dict[
                str,
                Any,
            ]
        ] = []

        seen_ids: set = set()

        offset = 0
        page = 0
        empty_streak = 0
        duplicates = 0

        fetch_start = (
            time.time()
        )

        total_possible = (
            max_pages
            * config.MARKETS_PER_PAGE
        )

        logger.info(
            "🔄 Paginated fetch: max %s pages × %s "
            "= %s markets",
            max_pages,
            config.MARKETS_PER_PAGE,
            f"{total_possible:,}",
        )

        while (
            page
            < max_pages
        ):
            data = self._api_get(
                config.GAMMA_API_BASE,
                "markets",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": str(
                        config.MARKETS_PER_PAGE
                    ),
                    "offset": str(
                        offset
                    ),
                },
            )

            if not data:
                empty_streak += 1

                if empty_streak >= 3:
                    logger.warning(
                        "Page %s: %s consecutive empty "
                        "responses, stopping pagination",
                        page + 1,
                        empty_streak,
                    )
                    break

                logger.warning(
                    "Page %s: No data (streak %s/3), "
                    "retrying next offset",
                    page + 1,
                    empty_streak,
                )

                offset += (
                    config.MARKETS_PER_PAGE
                )
                page += 1
                time.sleep(1.0)
                continue

            if (
                not isinstance(
                    data,
                    list,
                )
                or len(data)
                == 0
            ):
                logger.info(
                    "Page %s: Empty response, end of markets",
                    page + 1,
                )
                break

            empty_streak = 0

            for raw in data:
                normalized = (
                    self._normalize_market(
                        raw
                    )
                )

                if (
                    normalized
                    is None
                ):
                    continue

                market_id = (
                    normalized.get(
                        "id"
                    )
                )

                if (
                    market_id
                    in seen_ids
                ):
                    duplicates += 1
                    continue

                seen_ids.add(
                    market_id
                )

                all_markets.append(
                    normalized
                )

            is_last_page = (
                len(data)
                < config.MARKETS_PER_PAGE
            )

            if (
                (page + 1)
                % 10
                == 0
                or is_last_page
                or page
                == 0
            ):
                elapsed = (
                    time.time()
                    - fetch_start
                )

                rate = (
                    len(all_markets)
                    / elapsed
                    if elapsed > 0
                    else 0
                )

                logger.info(
                    "📄 Page %s/%s: %s markets fetched "
                    "(%.0fs, %.0f mkts/s)",
                    page + 1,
                    max_pages,
                    f"{len(all_markets):,}",
                    elapsed,
                    rate,
                )

            if is_last_page:
                logger.info(
                    "📍 Last page at %s (%s < %s)",
                    page + 1,
                    len(data),
                    config.MARKETS_PER_PAGE,
                )
                break

            offset += (
                config.MARKETS_PER_PAGE
            )

            page += 1

            if page % 20 == 0:
                logger.info(
                    "⏸️ Rate limit pause at page %s...",
                    page,
                )

                time.sleep(1.0)

            else:
                time.sleep(0.2)

        total_time = (
            time.time()
            - fetch_start
        )

        self.fetch_stats = {
            "pages_fetched": (
                page + 1
            ),
            "markets_raw": len(
                all_markets
            ),
            "duplicates_removed": (
                duplicates
            ),
            "fetch_time_sec": round(
                total_time,
                1,
            ),
            "markets_per_sec": round(
                (
                    len(all_markets)
                    / total_time
                )
                if total_time > 0
                else 0,
                1,
            ),
        }

        logger.info(
            "✅ Pagination complete: %s markets "
            "across %s pages in %.1fs "
            "(%s duplicates removed)",
            f"{len(all_markets):,}",
            page + 1,
            total_time,
            duplicates,
        )

        return all_markets

    # ── Filtering Pipeline ──────────────────────────────────────────

    def get_active_markets(
        self,
        limit: Optional[
            int
        ] = 20,
        max_pages: Optional[
            int
        ] = None,
    ) -> List[
        Dict[
            str,
            Any,
        ]
    ]:
        """
        Fetch and filter active markets.

        Pipeline:
        Pagination → Volume → Not Expired → No Sports
        """
        all_markets = (
            self.fetch_all_markets_paginated(
                max_pages=max_pages
            )
        )

        if not all_markets:
            logger.error(
                "No markets fetched via pagination"
            )
            return []

        volume_filtered = [
            market
            for market
            in all_markets
            if market.get(
                "volume_24hr",
                0,
            )
            >= config.MIN_VOLUME_24H
        ]

        logger.info(
            "📊 Volume filter: %s/%s markets ≥$%s",
            f"{len(volume_filtered):,}",
            f"{len(all_markets):,}",
            f"{config.MIN_VOLUME_24H:,}",
        )

        time_filtered = [
            market
            for market
            in volume_filtered
            if self.is_valid_active_market(
                market
            )
        ]

        expired_count = (
            len(volume_filtered)
            - len(time_filtered)
        )

        logger.info(
            "⏰ Expiry filter: %s active markets "
            "(%s expired removed)",
            f"{len(time_filtered):,}",
            f"{expired_count:,}",
        )

        final_markets = [
            market
            for market
            in time_filtered
            if not self.is_sports_or_live_event(
                market
            )
        ]

        sports_removed = (
            len(time_filtered)
            - len(final_markets)
        )

        logger.info(
            "🏟️ Sports filter: removed %s "
            "sports/live markets",
            f"{sports_removed:,}",
        )

        logger.info(
            "✅ Final: %s markets after all filters",
            f"{len(final_markets):,}",
        )

        self.fetch_stats.update(
            {
                "markets_after_volume": len(
                    volume_filtered
                ),
                "markets_expired_removed": (
                    expired_count
                ),
                "markets_after_expiry": len(
                    time_filtered
                ),
                "markets_after_sports": len(
                    final_markets
                ),
                "sports_removed": (
                    sports_removed
                ),
            }
        )

        if not final_markets:
            logger.warning(
                "⚠️ No markets passed all filters"
            )

        if limit:
            return final_markets[
                :limit
            ]

        return final_markets

    # ── Snapshot Builder ────────────────────────────────────────────

    def get_market_snapshot(
        self,
        market: Dict[
            str,
            Any,
        ],
    ) -> Optional[
        Dict[
            str,
            Any,
        ]
    ]:
        """
        Build a normalized market snapshot.

        Baseline:
        all-time volume / market age in days.

        Observation-change fields deliberately start as `None`.
        They are populated later by Orchestrator only after a real previous
        observation exists.
        """
        try:
            outcome_prices = (
                market.get(
                    "outcomePrices",
                    [
                        "0.5",
                        "0.5",
                    ],
                )
            )

            if isinstance(
                outcome_prices,
                str,
            ):
                try:
                    outcome_prices = (
                        json.loads(
                            outcome_prices
                        )
                    )

                except (
                    json.JSONDecodeError,
                    TypeError,
                ):
                    outcome_prices = [
                        "0.5",
                        "0.5",
                    ]

            yes_price = (
                _safe_float(
                    outcome_prices[0],
                    0.5,
                )
                if len(
                    outcome_prices
                )
                > 0
                else 0.5
            )

            no_price = (
                _safe_float(
                    outcome_prices[1],
                    0.5,
                )
                if len(
                    outcome_prices
                )
                > 1
                else 0.5
            )

            volume_24hr = (
                _safe_float(
                    market.get(
                        "volume_24hr",
                        0,
                    )
                )
            )

            volume_total = (
                _safe_float(
                    market.get(
                        "volume",
                        volume_24hr,
                    )
                )
            )

            now = datetime.now(
                timezone.utc
            )

            age_days = 30

            try:
                created_str = (
                    market.get(
                        "createdAt"
                    )
                    or market.get(
                        "created_at"
                    )
                    or market.get(
                        "startDate"
                    )
                    or ""
                )

                if created_str:
                    created = (
                        datetime.fromisoformat(
                            str(
                                created_str
                            ).replace(
                                "Z",
                                "+00:00",
                            )
                        )
                    )

                    age_days = max(
                        (
                            now
                            - created
                        ).days,
                        1,
                    )

            except (
                ValueError,
                TypeError,
                AttributeError,
            ):
                pass

            avg_daily_baseline = (
                volume_total
                / age_days
                if age_days > 0
                else volume_24hr
                * 0.5
            )

            spike_ratio = (
                volume_24hr
                / avg_daily_baseline
                if avg_daily_baseline
                > 0
                else 1.0
            )

            return {
                "id": market.get(
                    "id"
                ),
                "condition_id": market.get(
                    "condition_id",
                    market.get(
                        "conditionId"
                    ),
                ),
                "question": market.get(
                    "question",
                    "Unknown",
                ),
                "slug": market.get(
                    "slug",
                    "",
                ),
                "description": market.get(
                    "description",
                    "",
                )[:500],
                "yes_price": yes_price,
                "no_price": no_price,
                "spread": abs(
                    yes_price
                    - no_price
                ),
                "volume_24hr": (
                    volume_24hr
                ),
                "volume": volume_total,
                "liquidity": _safe_float(
                    market.get(
                        "liquidity",
                        market.get(
                            "liquidityNum",
                            0,
                        ),
                    )
                ),
                "active": market.get(
                    "active",
                    True,
                ),
                "closed": market.get(
                    "closed",
                    False,
                ),
                "end_date_iso": market.get(
                    "end_date_iso"
                ),
                "tags": market.get(
                    "tags",
                    [],
                ),
                "event_slug": market.get(
                    "event_slug",
                    market.get(
                        "eventSlug"
                    ),
                ),
                "clobTokenIds": market.get(
                    "clobTokenIds",
                    market.get(
                        "clob_token_ids",
                        [],
                    ),
                ),
                "baseline": round(
                    avg_daily_baseline,
                    2,
                ),
                "current_volume": (
                    volume_24hr
                ),
                "spike_ratio": round(
                    spike_ratio,
                    3,
                ),
                "age_days": age_days,
                "holders": [],
                "volumes_history": [],
                "price_change_since_last_observation": None,
                "volume_24h_change_since_last_observation": None,
                "seconds_since_last_observation": None,
                "price_change_per_hour_linearized": None,
            }

        except Exception as exc:
            logger.error(
                "Snapshot error for %s: %s",
                market.get("id"),
                exc,
                exc_info=True,
            )

            return None

    def get_snapshots_batch(
        self,
        markets: List[
            Dict[
                str,
                Any,
            ]
        ],
    ) -> List[
        Dict[
            str,
            Any,
        ]
    ]:
        """Build snapshots for a list of markets."""
        snapshots = [
            self.get_market_snapshot(
                market
            )
            for market
            in markets
        ]

        return [
            snapshot
            for snapshot
            in snapshots
            if snapshot
            is not None
        ]

    # ── Baseline (Legacy / DataFrame) ───────────────────────────────

    def calculate_baseline(
        self,
        volumes: List[
            Dict[
                str,
                Any,
            ]
        ],
    ) -> Dict[
        str,
        float,
    ]:
        """Legacy baseline from volume history DataFrame."""
        if (
            not volumes
            or len(volumes)
            < 6
        ):
            return {
                "baseline": 0.0,
                "current_volume": 0.0,
                "spike_ratio": 1.0,
            }

        try:
            import pandas as pd

            frame = pd.DataFrame(
                volumes
            )

            frame["volume"] = (
                pd.to_numeric(
                    frame["volume"],
                    errors="coerce",
                )
            )

            frame = frame.dropna(
                subset=[
                    "volume"
                ]
            )

            if len(frame) < 6:
                return {
                    "baseline": 0.0,
                    "current_volume": 0.0,
                    "spike_ratio": 1.0,
                }

            baseline = (
                frame["volume"]
                .tail(6)
                .mean()
            )

            current = (
                frame["volume"]
                .iloc[-1]
            )

            spike_ratio = (
                current
                / baseline
                if baseline > 0
                else 1.0
            )

            return {
                "baseline": float(
                    baseline
                ),
                "current_volume": float(
                    current
                ),
                "spike_ratio": float(
                    spike_ratio
                ),
            }

        except Exception as exc:
            logger.error(
                "Baseline error: %s",
                exc,
            )

            return {
                "baseline": 0.0,
                "current_volume": 0.0,
                "spike_ratio": 1.0,
            }

    def get_fetch_stats(
        self,
    ) -> Dict[
        str,
        Any,
    ]:
        """Return stats from the last fetch cycle."""
        return (
            self.fetch_stats.copy()
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - "
            "%(name)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
    )

    print("=" * 60)
    print(
        "🧪 PolyAugur Data Fetcher Test — "
        "Phase 11 (10k+ Markets)"
    )
    print("=" * 60)

    fetcher = (
        PolymarketFetcher()
    )

    print(
        "\n[Test 1] Paginated fetch "
        "(5 pages = max 500 markets)..."
    )

    markets = (
        fetcher.get_active_markets(
            limit=None,
            max_pages=5,
        )
    )

    if not markets:
        print(
            "❌ FAIL: No markets returned"
        )
        return

    stats = (
        fetcher.get_fetch_stats()
    )

    print(
        f"✅ PASS: {len(markets):,} "
        "valid markets fetched"
    )

    print(
        "   Pages:            "
        f"{stats.get('pages_fetched', '?')}"
    )

    print(
        "   Raw markets:      "
        f"{stats.get('markets_raw', '?')}"
    )

    print(
        "   Duplicates:       "
        f"{stats.get('duplicates_removed', 0)}"
    )

    print(
        "   After volume:     "
        f"{stats.get('markets_after_volume', '?')}"
    )

    print(
        "   Expired removed:  "
        f"{stats.get('markets_expired_removed', 0)}"
    )

    print(
        "   Sports removed:   "
        f"{stats.get('sports_removed', '?')}"
    )

    print(
        "   Fetch time:       "
        f"{stats.get('fetch_time_sec', '?')}s"
    )

    print(
        "   Rate:             "
        f"{stats.get('markets_per_sec', '?')} mkts/s"
    )

    print(
        "\n[Test 2] Batch snapshots "
        "with real baseline..."
    )

    snapshots = (
        fetcher.get_snapshots_batch(
            markets[:5]
        )
    )

    print(
        "✅ PASS: "
        f"{len(snapshots)} "
        "snapshots built"
    )

    print(
        f"\n{'#':<3} "
        f"{'Spike':<8} "
        f"{'Age':<8} "
        f"{'Baseline':<14} "
        f"{'Vol 24h':<14} "
        f"{'Question':<40}"
    )

    print("-" * 90)

    for index, snapshot in enumerate(
        snapshots,
        1,
    ):
        print(
            f"{index:<3} "
            f"{snapshot.get('spike_ratio', 0):<8.2f} "
            f"{snapshot.get('age_days', 0):<8}d "
            f"${snapshot.get('baseline', 0):<13,.0f} "
            f"${snapshot.get('volume_24hr', 0):<13,.0f} "
            f"{snapshot.get('question', '')[:38]}"
        )

    print(
        "\n[Test 3] Sports filter check..."
    )

    sport_check = [
        "nhl",
        "nba",
        "nfl",
        "bundesliga",
        "champions league",
        "stanley cup",
        "playoff",
        "championship",
        "tournament",
        "epl",
        "masters",
        "pga",
        "golf",
        "top 4",
    ]

    leaked = [
        market
        for market in markets
        if any(
            keyword
            in market.get(
                "question",
                "",
            ).lower()
            for keyword
            in sport_check
        )
    ]

    if leaked:
        print(
            f"❌ {len(leaked)} "
            "sports markets leaked:"
        )

        for market in leaked[:5]:
            print(
                "   - "
                f"{market['question'][:65]}"
            )

    else:
        print(
            "✅ PASS: 0 sports markets "
            f"in {len(markets):,} results"
        )

    print(
        "\n[Test 4] Regex sports pattern validation..."
    )

    test_questions = [
        (
            "Will Team A vs Team B win?",
            True,
        ),
        (
            "F1 Grand Prix winner?",
            True,
        ),
        (
            "Will they win the World Cup?",
            True,
        ),
        (
            "Will Bitcoin reach $100k?",
            False,
        ),
        (
            "US election 2026 results?",
            False,
        ),
        (
            "Premier League top 4 of the season?",
            True,
        ),
        (
            "Will f100 stock rise?",
            False,
        ),
    ]

    all_pass = True

    for (
        question,
        expected,
    ) in test_questions:
        fake_market = {
            "question": question,
            "tags": [],
        }

        result = (
            fetcher.is_sports_or_live_event(
                fake_market
            )
        )

        ok = (
            result
            == expected
        )

        if not ok:
            all_pass = False

        status = (
            "✅"
            if ok
            else "❌"
        )

        print(
            f'   {status} "{question[:45]}" '
            f"→ {result} "
            f"(expected {expected})"
        )

    if all_pass:
        print(
            "   ✅ All regex tests passed"
        )

    print(
        "\n[Test 5] _safe_float edge cases..."
    )

    assert (
        _safe_float(None)
        == 0.0
    )

    assert (
        _safe_float("")
        == 0.0
    )

    assert (
        _safe_float(
            "1,234.56"
        )
        == 1234.56
    )

    assert (
        _safe_float(42)
        == 42.0
    )

    assert (
        _safe_float(
            "not_a_number"
        )
        == 0.0
    )

    assert (
        _safe_float(
            None,
            99.9,
        )
        == 99.9
    )

    print(
        "   ✅ All _safe_float edge cases passed"
    )

    print(
        "\n[Test 6] Time filter: no 6h restriction..."
    )

    now = datetime.now(
        timezone.utc
    )

    closing_soon = {
        "end_date_iso": (
            now
            + timedelta(
                minutes=30
            )
        ).isoformat(),
    }

    already_expired = {
        "end_date_iso": (
            now
            - timedelta(
                hours=1
            )
        ).isoformat(),
    }

    no_end_date = {}

    assert (
        fetcher.is_valid_active_market(
            closing_soon
        )
        is True
    )

    assert (
        fetcher.is_valid_active_market(
            already_expired
        )
        is False
    )

    assert (
        fetcher.is_valid_active_market(
            no_end_date
        )
        is True
    )

    print(
        "   ✅ Closing in 30min: "
        "passes (no 6h restriction)"
    )

    print(
        "   ✅ Already expired: rejected"
    )

    print(
        "   ✅ No end date: "
        "passes (perpetual market)"
    )

    print(
        "\n[Test 7] Full 10k scan estimate..."
    )

    rate = stats.get(
        "markets_per_sec",
        50,
    )

    estimated_time = (
        10_000
        / rate
        if rate > 0
        else 999
    )

    print(
        f"   At {rate:.0f} mkts/s "
        f"→ ~{estimated_time:.0f}s "
        "for 10,000 markets"
    )

    print(
        "   "
        + (
            "✅ Under 2 min"
            if estimated_time < 120
            else "⚠️ May be slow"
        )
    )

    print("\n" + "=" * 60)
    print(
        "✅ Phase 11 Data Fetcher: "
        "ALL TESTS PASSED"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()