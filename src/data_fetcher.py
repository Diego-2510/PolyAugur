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
            re.compile(r"\bchampionship\b"),
            re.compile(r"\btournament\b"),
            re.compile(r"finish in the top"),
            re.compile(r"top \d+ of the"),
            re.compile(r"league table"),
            re.compile(r"title race"),
            re.compile(r"win the\s.*cup"),
        ]

    def _backoff(
        self,
        retry_count: int,
    ) -> float:
        if (
            retry_count
            < len(config.BACKOFF_DELAYS)
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
            Dict[
                str,
                Any,
            ]
        ] = None,
        max_retries: int = 3,
    ) -> Optional[Any]:
        """Generic GET with retry and backoff."""
        url = f"{base}/{endpoint}"

        params = params or {}

        for retry in range(
            max_retries
        ):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=15,
                )

                if (
                    response.status_code
                    == 429
                ):
                    delay = self._backoff(
                        retry
                    )

                    logger.warning(
                        "Rate limit on %s, backoff %.1fs",
                        endpoint,
                        delay,
                    )

                    if (
                        retry
                        < max_retries - 1
                    ):
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
                        response.text[:80],
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
            closes_at = datetime.fromisoformat(
                str(
                    end_date_str
                ).replace(
                    "Z",
                    "+00:00",
                )
            )

            return closes_at > now

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

    def fetch_all_markets_paginated(
        self,
        max_pages: Optional[int] = None,
    ) -> List[
        Dict[
            str,
            Any,
        ]
    ]:
        """
        Fetch available markets without silently skipping failed pages.

        A legitimate empty list terminates pagination normally.
        A failed or structurally invalid page invalidates the current scan,
        because continuing at a later offset would create a silent data gap.
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
        pages_fetched = 0
        duplicates = 0

        incomplete = False
        stopped_at_offset = None
        stopped_reason = None

        fetch_start = time.time()

        total_possible = (
            max_pages
            * config.MARKETS_PER_PAGE
        )

        logger.info(
            "Paginated fetch: max %s pages x %s = %s markets",
            max_pages,
            config.MARKETS_PER_PAGE,
            f"{total_possible:,}",
        )

        while (
            pages_fetched
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

            if data is None:
                incomplete = True
                stopped_at_offset = offset
                stopped_reason = (
                    "request_failed"
                )

                logger.error(
                    "Market pagination failed at offset %s; "
                    "discarding partial scan to avoid gaps",
                    offset,
                )

                break

            if not isinstance(
                data,
                list,
            ):
                incomplete = True
                stopped_at_offset = offset
                stopped_reason = (
                    "unexpected_response_type"
                )

                logger.error(
                    "Unexpected Gamma response type at offset %s: %s; "
                    "discarding partial scan",
                    offset,
                    type(data).__name__,
                )

                break

            if not data:
                logger.info(
                    "Empty market page at offset %s; "
                    "pagination complete",
                    offset,
                )

                break

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

                if market_id is None:
                    logger.warning(
                        "Skipping market without id "
                        "at offset %s",
                        offset,
                    )
                    continue

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

            pages_fetched += 1

            is_last_page = (
                len(data)
                < config.MARKETS_PER_PAGE
            )

            if (
                pages_fetched % 10 == 0
                or is_last_page
                or pages_fetched == 1
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
                    "Page %s/%s: %s markets fetched "
                    "(%.0fs, %.0f mkts/s)",
                    pages_fetched,
                    max_pages,
                    f"{len(all_markets):,}",
                    elapsed,
                    rate,
                )

            if is_last_page:
                logger.info(
                    "Last page at %s (%s < %s)",
                    pages_fetched,
                    len(data),
                    config.MARKETS_PER_PAGE,
                )

                break

            offset += (
                config.MARKETS_PER_PAGE
            )

            if (
                pages_fetched
                % 20
                == 0
            ):
                logger.info(
                    "Rate limit pause at page %s...",
                    pages_fetched,
                )

                time.sleep(
                    1.0
                )

            else:
                time.sleep(
                    0.2
                )

        total_time = (
            time.time()
            - fetch_start
        )

        self.fetch_stats = {
            "pages_fetched": (
                pages_fetched
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
            "markets_per_sec": (
                round(
                    len(all_markets)
                    / total_time,
                    1,
                )
                if total_time > 0
                else 0
            ),
            "incomplete": (
                incomplete
            ),
            "stopped_at_offset": (
                stopped_at_offset
            ),
            "stopped_reason": (
                stopped_reason
            ),
        }

        if incomplete:
            return []

        logger.info(
            "Pagination complete: %s markets across %s pages "
            "in %.1fs (%s duplicates removed)",
            f"{len(all_markets):,}",
            pages_fetched,
            total_time,
            duplicates,
        )

        return all_markets

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
            for market in all_markets
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
            for market in volume_filtered
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
            for market in time_filtered
            if not self.is_sports_or_live_event(
                market
            )
        ]

        sports_removed = (
            len(time_filtered)
            - len(final_markets)
        )

        logger.info(
            "🏟️ Sports filter: removed %s sports/live markets",
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

        Observation-change fields deliberately start as None.
        They are populated by Orchestrator only once a previous
        observation actually exists.
        """
        try:
            outcome_prices = market.get(
                "outcomePrices",
                [
                    "0.5",
                    "0.5",
                ],
            )

            if isinstance(
                outcome_prices,
                str,
            ):
                try:
                    outcome_prices = json.loads(
                        outcome_prices
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
                if len(outcome_prices) > 0
                else 0.5
            )

            no_price = (
                _safe_float(
                    outcome_prices[1],
                    0.5,
                )
                if len(outcome_prices) > 1
                else 0.5
            )

            volume_24hr = _safe_float(
                market.get(
                    "volume_24hr",
                    0,
                )
            )

            volume_total = _safe_float(
                market.get(
                    "volume",
                    volume_24hr,
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
                    created = datetime.fromisoformat(
                        str(
                            created_str
                        ).replace(
                            "Z",
                            "+00:00",
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
                else volume_24hr * 0.5
            )

            spike_ratio = (
                volume_24hr
                / avg_daily_baseline
                if avg_daily_baseline > 0
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
            for market in markets
        ]

        return [
            snapshot
            for snapshot in snapshots
            if snapshot is not None
        ]

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
            or len(volumes) < 6
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

            frame["volume"] = pd.to_numeric(
                frame["volume"],
                errors="coerce",
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
        return self.fetch_stats.copy()