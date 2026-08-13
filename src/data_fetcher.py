"""Polymarket Gamma API ingestion and normalized market snapshots."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        if isinstance(value, str):
            return float(value.replace(",", ""))
        return float(value)
    except (TypeError, ValueError):
        return default


class PolymarketFetcher:
    """Fetch, normalize, filter, and snapshot Polymarket Gamma markets."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.cache: dict[str, Any] = {}
        self.cache_timestamps: dict[str, datetime] = {}
        self.fetch_stats: dict[str, Any] = {}

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

    def _backoff(self, retry_count: int) -> float:
        if retry_count < len(config.BACKOFF_DELAYS):
            return config.BACKOFF_DELAYS[retry_count]
        return 5.0

    def _api_get(
        self,
        base: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> Any | None:
        """GET JSON with bounded retry/backoff."""
        url = f"{base}/{endpoint}"
        params = params or {}
        for retry in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=15)
                if response.status_code == 429:
                    delay = self._backoff(retry)
                    logger.warning("Rate limit on %s, backoff %.1fs", endpoint, delay)
                    if retry < max_retries - 1:
                        time.sleep(delay)
                    continue
                if response.status_code in (400, 404, 422):
                    logger.debug("HTTP %s: %s -> %s", response.status_code, url, response.text[:80])
                    return None
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                logger.warning("Timeout on %s (retry %s/%s)", endpoint, retry + 1, max_retries)
                if retry < max_retries - 1:
                    time.sleep(self._backoff(retry))
            except requests.exceptions.RequestException as exc:
                logger.error("API error on %s: %s", endpoint, exc)
                if retry < max_retries - 1:
                    time.sleep(self._backoff(retry))
        logger.error("Max retries exceeded for %s", endpoint)
        return None

    def is_valid_active_market(self, market: dict[str, Any]) -> bool:
        if market.get("closed") is True or market.get("active") is False:
            return False

        end_date = next(
            (
                market[field]
                for field in ("end_date_iso", "endDateIso", "endDate", "closesAt", "end_date")
                if market.get(field)
            ),
            None,
        )
        if not end_date:
            return True
        try:
            closes_at = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
            return closes_at > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False

    def is_sports_or_live_event(self, market: dict[str, Any]) -> bool:
        tags = market.get("tags", [])
        tag_labels: list[str] = []
        for tag in tags:
            if isinstance(tag, dict):
                tag_labels.append(str(tag.get("label", "")).lower())
            elif isinstance(tag, str):
                tag_labels.append(tag.lower())

        question = str(market.get("question", "")).lower()
        if any(keyword in label for label in tag_labels for keyword in self._sport_keywords):
            return True
        if any(keyword in question for keyword in self._sport_keywords):
            return True
        return any(pattern.search(question) for pattern in self._sport_patterns)

    def _normalize_market(self, market: dict[str, Any]) -> dict[str, Any] | None:
        try:
            volume = _safe_float(
                market.get("volume_24hr")
                or market.get("volume24hr")
                or market.get("volume24Hrs")
                or market.get("volumeNum"),
                default=0.0,
            )
            end_date = (
                market.get("end_date_iso")
                or market.get("endDateIso")
                or market.get("endDate")
                or market.get("closesAt")
                or market.get("end_date")
            )
            return {
                **market,
                "volume_24hr": volume,
                "end_date_iso": end_date,
                "tags": market.get("tags", []),
                "question": market.get("question", "Unknown Market"),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Normalization error for %s: %s", market.get("id"), exc)
            return None

    def fetch_all_markets_paginated(self, max_pages: int | None = None) -> list[dict[str, Any]]:
        """Fetch markets via Gamma keyset pagination and fail closed on gaps."""
        max_pages = max_pages or config.MAX_PAGES
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")

        all_markets: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        pages_fetched = 0
        duplicates = 0
        incomplete = False
        truncated = False
        stopped_at_cursor: str | None = None
        stopped_reason: str | None = None
        fetch_start = time.time()

        logger.info(
            "Gamma keyset fetch: max %s pages x %s = %s markets",
            max_pages,
            config.MARKETS_PER_PAGE,
            f"{max_pages * config.MARKETS_PER_PAGE:,}",
        )

        while pages_fetched < max_pages:
            params: dict[str, Any] = {
                "closed": "false",
                "limit": str(config.MARKETS_PER_PAGE),
                "include_tag": "true",
            }
            if cursor is not None:
                params["after_cursor"] = cursor

            payload = self._api_get(config.GAMMA_API_BASE, "markets/keyset", params)
            if payload is None:
                incomplete = True
                stopped_at_cursor = cursor
                stopped_reason = "request_failed"
                break
            if not isinstance(payload, dict):
                incomplete = True
                stopped_at_cursor = cursor
                stopped_reason = "unexpected_response_type"
                break

            page = payload.get("markets")
            if not isinstance(page, list):
                incomplete = True
                stopped_at_cursor = cursor
                stopped_reason = "invalid_markets_field"
                break

            next_cursor = payload.get("next_cursor")
            if next_cursor is not None and (
                not isinstance(next_cursor, str) or not next_cursor.strip()
            ):
                incomplete = True
                stopped_at_cursor = cursor
                stopped_reason = "invalid_next_cursor"
                break

            for raw in page:
                if not isinstance(raw, dict):
                    logger.warning("Skipping non-object market record")
                    continue
                normalized = self._normalize_market(raw)
                if normalized is None:
                    continue
                market_id = normalized.get("id")
                if market_id is None:
                    logger.warning("Skipping market without id at cursor %r", cursor)
                    continue
                if market_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(market_id)
                all_markets.append(normalized)

            pages_fetched += 1
            if pages_fetched == 1 or pages_fetched % 10 == 0 or not next_cursor:
                elapsed = time.time() - fetch_start
                rate = len(all_markets) / elapsed if elapsed > 0 else 0.0
                logger.info(
                    "Keyset page %s/%s: %s unique markets (%.0fs, %.0f mkts/s)",
                    pages_fetched,
                    max_pages,
                    f"{len(all_markets):,}",
                    elapsed,
                    rate,
                )

            if not next_cursor:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                incomplete = True
                stopped_at_cursor = next_cursor
                stopped_reason = "cursor_loop"
                break

            seen_cursors.add(next_cursor)
            cursor = next_cursor

            if pages_fetched < max_pages:
                time.sleep(1.0 if pages_fetched % 20 == 0 else 0.2)

        if not incomplete and pages_fetched >= max_pages and next_cursor:
            truncated = True

        total_time = time.time() - fetch_start
        self.fetch_stats = {
            "pagination": "keyset",
            "pages_fetched": pages_fetched,
            "markets_raw": len(all_markets),
            "duplicates_removed": duplicates,
            "fetch_time_sec": round(total_time, 1),
            "markets_per_sec": (round(len(all_markets) / total_time, 1) if total_time > 0 else 0),
            "incomplete": incomplete,
            "truncated": truncated,
            "stopped_at_cursor": stopped_at_cursor,
            "stopped_reason": stopped_reason,
        }

        if incomplete:
            logger.error(
                "Gamma keyset scan incomplete (reason=%s, cursor=%r); discarding partial scan",
                stopped_reason,
                stopped_at_cursor,
            )
            return []

        logger.info(
            "Gamma keyset scan complete: %s markets across %s pages%s",
            f"{len(all_markets):,}",
            pages_fetched,
            " (configured page cap reached)" if truncated else "",
        )
        return all_markets

    def get_active_markets(
        self,
        limit: int | None = 20,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        all_markets = self.fetch_all_markets_paginated(max_pages=max_pages)
        if self.fetch_stats.get("incomplete"):
            raise RuntimeError(
                "Incomplete Gamma market scan "
                f"(reason={self.fetch_stats.get('stopped_reason')}, "
                f"cursor={self.fetch_stats.get('stopped_at_cursor')!r})"
            )
        if not all_markets:
            logger.warning("Gamma API returned no markets")
            return []

        volume_filtered = [
            market
            for market in all_markets
            if market.get("volume_24hr", 0) >= config.MIN_VOLUME_24H
        ]
        time_filtered = [
            market for market in volume_filtered if self.is_valid_active_market(market)
        ]
        final_markets = [
            market for market in time_filtered if not self.is_sports_or_live_event(market)
        ]

        self.fetch_stats.update(
            {
                "markets_after_volume": len(volume_filtered),
                "markets_expired_removed": len(volume_filtered) - len(time_filtered),
                "markets_after_expiry": len(time_filtered),
                "markets_after_sports": len(final_markets),
                "sports_removed": len(time_filtered) - len(final_markets),
            }
        )
        if not final_markets:
            logger.warning("No markets passed all filters")
        return final_markets[:limit] if limit else final_markets

    def get_market_snapshot(self, market: dict[str, Any]) -> dict[str, Any] | None:
        try:
            outcome_prices = market.get("outcomePrices", ["0.5", "0.5"])
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = ["0.5", "0.5"]
            if not isinstance(outcome_prices, list):
                outcome_prices = ["0.5", "0.5"]

            yes_price = _safe_float(outcome_prices[0], 0.5) if outcome_prices else 0.5
            no_price = _safe_float(outcome_prices[1], 0.5) if len(outcome_prices) > 1 else 0.5
            volume_24hr = _safe_float(market.get("volume_24hr", 0))
            volume_total = _safe_float(market.get("volume", volume_24hr))
            now = datetime.now(timezone.utc)
            age_days = 30

            created_str = (
                market.get("createdAt") or market.get("created_at") or market.get("startDate") or ""
            )
            if created_str:
                try:
                    created = datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
                    age_days = max((now - created).days, 1)
                except (TypeError, ValueError, AttributeError):
                    pass

            avg_daily_baseline = volume_total / age_days if age_days > 0 else volume_24hr * 0.5
            spike_ratio = volume_24hr / avg_daily_baseline if avg_daily_baseline > 0 else 1.0

            return {
                "id": market.get("id"),
                "condition_id": market.get("condition_id", market.get("conditionId")),
                "question": market.get("question", "Unknown"),
                "slug": market.get("slug", ""),
                "description": str(market.get("description", ""))[:500],
                "yes_price": yes_price,
                "no_price": no_price,
                "spread": abs(yes_price - no_price),
                "volume_24hr": volume_24hr,
                "volume": volume_total,
                "liquidity": _safe_float(market.get("liquidity", market.get("liquidityNum", 0))),
                "active": market.get("active", True),
                "closed": market.get("closed", False),
                "end_date_iso": market.get("end_date_iso"),
                "tags": market.get("tags", []),
                "event_slug": market.get("event_slug", market.get("eventSlug")),
                "clobTokenIds": market.get("clobTokenIds", market.get("clob_token_ids", [])),
                "baseline": round(avg_daily_baseline, 2),
                "current_volume": volume_24hr,
                "spike_ratio": round(spike_ratio, 3),
                "age_days": age_days,
                "holders": [],
                "volumes_history": [],
                "price_change_since_last_observation": None,
                "volume_24h_change_since_last_observation": None,
                "seconds_since_last_observation": None,
                "price_change_per_hour_linearized": None,
            }
        except Exception as exc:
            logger.error("Snapshot error for %s: %s", market.get("id"), exc, exc_info=True)
            return None

    def get_snapshots_batch(self, markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshots = [self.get_market_snapshot(market) for market in markets]
        return [snapshot for snapshot in snapshots if snapshot is not None]

    def calculate_baseline(self, volumes: list[dict[str, Any]]) -> dict[str, float]:
        if not volumes or len(volumes) < 6:
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}
        try:
            import pandas as pd

            frame = pd.DataFrame(volumes)
            frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
            frame = frame.dropna(subset=["volume"])
            if len(frame) < 6:
                return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}
            baseline = frame["volume"].tail(6).mean()
            current = frame["volume"].iloc[-1]
            spike_ratio = current / baseline if baseline > 0 else 1.0
            return {
                "baseline": float(baseline),
                "current_volume": float(current),
                "spike_ratio": float(spike_ratio),
            }
        except Exception as exc:
            logger.error("Baseline error: %s", exc)
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}

    def get_fetch_stats(self) -> dict[str, Any]:
        return self.fetch_stats.copy()
