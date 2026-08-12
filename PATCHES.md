# PolyAugur Sprint 0 — Slice 2 integration patches

## `src/mistral_analyzer.py`

Add this import:

```python
from src.llm_contract import LLMOutputContractError, parse_signal_response
```

Replace `_parse_and_validate` with:

```python
def _parse_and_validate(
    self,
    raw: str,
    expected_count: int = 1,
    snapshots: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Validate the raw LLM response before applying local safety overrides."""
    try:
        validated = parse_signal_response(raw, expected_count=expected_count)
    except LLMOutputContractError as exc:
        logger.error("Invalid Mistral output: %s", exc)
        return None

    for idx, item in enumerate(validated):
        # Preserve the existing conservative confidence cap after schema validation.
        item["confidence_score"] = min(0.95, item["confidence_score"])

        yes_price = None
        if snapshots and idx < len(snapshots):
            yes_price = snapshots[idx].get("yes_price", 0.5)

        if yes_price is None:
            continue

        trade = item["recommended_trade"]
        if yes_price < 0.01 and trade == "BUY_NO":
            logger.info(
                "Price override: BUY_NO -> HOLD (yes_price=%.4f)",
                yes_price,
            )
            item["recommended_trade"] = "HOLD"
            item["recommended_position_size_pct"] = 0.0
            item["holding_period_hours"] = 0
            item["counter_evidence"].append(
                f"Price override: yes_price={yes_price:.4f} — "
                "BUY_NO payout < $0.01 per dollar"
            )
        elif yes_price > 0.99 and trade == "BUY_YES":
            logger.info(
                "Price override: BUY_YES -> HOLD (yes_price=%.4f)",
                yes_price,
            )
            item["recommended_trade"] = "HOLD"
            item["recommended_position_size_pct"] = 0.0
            item["holding_period_hours"] = 0
            item["counter_evidence"].append(
                f"Price override: yes_price={yes_price:.4f} — "
                "BUY_YES has no upside remaining"
            )

    return validated
```

The existing `analyze_single()` and `analyze_batch()` already fall back when `_parse_and_validate()` returns `None`, so no additional fallback branch is required.

## `src/data_fetcher.py`

In `fetch_all_markets_paginated`, remove the old `empty_streak` logic and use the following function body. Keep the existing function signature.

```python
def fetch_all_markets_paginated(self, max_pages: int = None) -> List[Dict[str, Any]]:
    """Fetch all available markets without silently skipping failed pages."""
    max_pages = max_pages or config.MAX_PAGES
    all_markets: List[Dict[str, Any]] = []
    seen_ids: set = set()
    offset = 0
    pages_fetched = 0
    duplicates = 0
    incomplete = False
    stopped_at_offset = None
    stopped_reason = None
    fetch_start = time.time()

    total_possible = max_pages * config.MARKETS_PER_PAGE
    logger.info(
        "Paginated fetch: max %s pages x %s = %s markets",
        max_pages,
        config.MARKETS_PER_PAGE,
        f"{total_possible:,}",
    )

    while pages_fetched < max_pages:
        data = self._api_get(
            config.GAMMA_API_BASE,
            "markets",
            {
                "active": "true",
                "closed": "false",
                "limit": str(config.MARKETS_PER_PAGE),
                "offset": str(offset),
            },
        )

        if data is None:
            incomplete = True
            stopped_at_offset = offset
            stopped_reason = "request_failed"
            logger.error(
                "Market pagination failed at offset %s; "
                "discarding partial scan to avoid gaps",
                offset,
            )
            break

        if not isinstance(data, list):
            incomplete = True
            stopped_at_offset = offset
            stopped_reason = "unexpected_response_type"
            logger.error(
                "Unexpected Gamma response type at offset %s: %s; "
                "discarding partial scan",
                offset,
                type(data).__name__,
            )
            break

        if not data:
            logger.info("Empty market page at offset %s; pagination complete", offset)
            break

        for raw in data:
            normalized = self._normalize_market(raw)
            if normalized is None:
                continue

            market_id = normalized.get("id")
            if market_id in seen_ids:
                duplicates += 1
                continue

            seen_ids.add(market_id)
            all_markets.append(normalized)

        pages_fetched += 1
        is_last_page = len(data) < config.MARKETS_PER_PAGE

        if (
            pages_fetched % 10 == 0
            or is_last_page
            or pages_fetched == 1
        ):
            elapsed = time.time() - fetch_start
            rate = len(all_markets) / elapsed if elapsed > 0 else 0
            logger.info(
                "Page %s/%s: %s markets fetched (%.0fs, %.0f mkts/s)",
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

        offset += config.MARKETS_PER_PAGE

        if pages_fetched % 20 == 0:
            logger.info("Rate limit pause at page %s...", pages_fetched)
            time.sleep(1.0)
        else:
            time.sleep(0.2)

    total_time = time.time() - fetch_start
    self.fetch_stats = {
        "pages_fetched": pages_fetched,
        "markets_raw": len(all_markets),
        "duplicates_removed": duplicates,
        "fetch_time_sec": round(total_time, 1),
        "markets_per_sec": (
            round(len(all_markets) / total_time, 1)
            if total_time > 0
            else 0
        ),
        "incomplete": incomplete,
        "stopped_at_offset": stopped_at_offset,
        "stopped_reason": stopped_reason,
    }

    if incomplete:
        return []

    logger.info(
        "Pagination complete: %s markets across %s pages in %.1fs "
        "(%s duplicates removed)",
        f"{len(all_markets):,}",
        pages_fetched,
        total_time,
        duplicates,
    )
    return all_markets
```
