"""
PolyAugur Signal Store - SQLite Persistence Layer
Phase 8: Extended schema with trade analysis columns + performance tracking.
Author: Diego Ringleb | Phase 8 | 2026-02-28
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import os

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SIGNAL_DB_PATH", "data/signals.db")


class SignalStore:
    """
    SQLite-based signal persistence.
    Phase 8: whale columns, outcome tracking, confidence boost log.
    """

    DEDUP_WINDOW_HOURS = 4

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._migrate_schema()
        logger.info(f"📦 SignalStore initialized → {self.db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id           TEXT NOT NULL,
                    question            TEXT,
                    trade               TEXT,
                    confidence          REAL,
                    confidence_raw      REAL,
                    confidence_boost    REAL DEFAULT 0,
                    anomaly_score       REAL,
                    anomaly_type        TEXT,
                    risk_level          TEXT,
                    yes_price           REAL,
                    volume_24hr         REAL,
                    spike_ratio         REAL,
                    days_to_close       INTEGER,
                    holding_hours       INTEGER,
                    position_size_pct   REAL,
                    reasoning           TEXT,
                    cycle               INTEGER,
                    detected_at         TEXT NOT NULL,
                    source              TEXT,
                    -- Telegram
                    sent_telegram       INTEGER DEFAULT 0,
                    telegram_sent_at    TEXT,
                    -- Trade Analysis (Phase 7/8)
                    whale_count         INTEGER DEFAULT 0,
                    whale_volume_pct    REAL DEFAULT 0,
                    top_wallet_pct      REAL DEFAULT 0,
                    unique_wallets      INTEGER DEFAULT 0,
                    directional_bias    REAL DEFAULT 0.5,
                    dominant_side       TEXT DEFAULT 'NONE',
                    burst_score         REAL DEFAULT 1.0,
                    trade_suspicious    INTEGER DEFAULT 0,
                    suspicious_reasons  TEXT DEFAULT '[]',
                    -- Performance Tracking (Phase 8)
                    outcome             TEXT DEFAULT 'pending',
                    outcome_price       REAL,
                    outcome_checked_at  TEXT,
                    profit_loss_pct     REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_market_id
                ON signals (market_id, detected_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_detected_at
                ON signals (detected_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outcome
                ON signals (outcome, days_to_close)
            """)
            conn.commit()

    def _migrate_schema(self):
        """Add columns that may be missing from older databases."""
        migrations = [
            ("whale_count", "INTEGER DEFAULT 0"),
            ("whale_volume_pct", "REAL DEFAULT 0"),
            ("top_wallet_pct", "REAL DEFAULT 0"),
            ("unique_wallets", "INTEGER DEFAULT 0"),
            ("directional_bias", "REAL DEFAULT 0.5"),
            ("dominant_side", "TEXT DEFAULT 'NONE'"),
            ("burst_score", "REAL DEFAULT 1.0"),
            ("trade_suspicious", "INTEGER DEFAULT 0"),
            ("suspicious_reasons", "TEXT DEFAULT '[]'"),
            ("outcome", "TEXT DEFAULT 'pending'"),
            ("outcome_price", "REAL"),
            ("outcome_checked_at", "TEXT"),
            ("profit_loss_pct", "REAL"),
            ("confidence_raw", "REAL"),
            ("confidence_boost", "REAL DEFAULT 0"),
        ]
        with self._get_conn() as conn:
            for col_name, col_type in migrations:
                try:
                    conn.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")
                    logger.debug(f"Migrated: added column {col_name}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()

    def is_duplicate(self, market_id: str) -> bool:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self.DEDUP_WINDOW_HOURS)
        ).isoformat()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE market_id = ? AND detected_at >= ?",
                (market_id, cutoff)
            ).fetchone()
        return row['cnt'] > 0

    def save(self, signal: Dict[str, Any]) -> int:
        import json

        spike_ratio = (
            signal.get('spike_ratio') or
            signal.get('breakdown', {}).get('volume_spike', {}).get('spike_ratio', 1.0)
        )

        days_to_close = signal.get('days_to_close')
        if days_to_close is None:
            end_date = signal.get('end_date_iso')
            try:
                if end_date:
                    closes = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    days_to_close = (closes - datetime.now(timezone.utc)).days
            except Exception:
                pass

        suspicious_reasons = signal.get('suspicious_reasons', [])
        if isinstance(suspicious_reasons, list):
            suspicious_reasons = json.dumps(suspicious_reasons)

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    market_id, question, trade, confidence, confidence_raw,
                    confidence_boost, anomaly_score, anomaly_type, risk_level,
                    yes_price, volume_24hr, spike_ratio, days_to_close,
                    holding_hours, position_size_pct, reasoning, cycle,
                    detected_at, source, sent_telegram,
                    whale_count, whale_volume_pct, top_wallet_pct,
                    unique_wallets, directional_bias, dominant_side,
                    burst_score, trade_suspicious, suspicious_reasons,
                    outcome
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending'
                )
                """,
                (
                    signal.get('market_id', ''),
                    signal.get('question', ''),
                    signal.get('recommended_trade', 'HOLD'),
                    signal.get('confidence_score', 0.0),
                    signal.get('confidence_raw', signal.get('confidence_score', 0.0)),
                    signal.get('confidence_boost', 0.0),
                    signal.get('score', signal.get('anomaly_score', 0.0)),
                    signal.get('anomaly_type', 'unknown'),
                    signal.get('risk_level', 'medium'),
                    signal.get('yes_price', 0.5),
                    signal.get('volume_24hr', 0.0),
                    spike_ratio,
                    days_to_close,
                    signal.get('holding_period_hours', 0),
                    signal.get('recommended_position_size_pct', 0.0),
                    signal.get('reasoning', ''),
                    signal.get('cycle', 0),
                    signal.get('detected_at', datetime.now(timezone.utc).isoformat()),
                    signal.get('source', 'unknown'),
                    # Trade analysis
                    signal.get('whale_count', 0),
                    signal.get('whale_volume_pct', 0),
                    signal.get('top_wallet_pct', 0),
                    signal.get('unique_wallets', 0),
                    signal.get('directional_bias', 0.5),
                    signal.get('dominant_side', 'NONE'),
                    signal.get('burst_score', 1.0),
                    1 if signal.get('trade_suspicious') else 0,
                    suspicious_reasons,
                )
            )
            row_id = cursor.lastrowid
            conn.commit()

        logger.info(f"💾 Signal saved (id={row_id}): {signal.get('question', '')[:50]}")
        return row_id

    def mark_telegram_sent(self, row_id: int):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE signals SET sent_telegram = 1, telegram_sent_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row_id)
            )
            conn.commit()

    def update_outcome(self, row_id: int, outcome: str, outcome_price: float, pnl_pct: float):
        """Update signal with actual market outcome."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE signals
                SET outcome = ?, outcome_price = ?, profit_loss_pct = ?,
                    outcome_checked_at = ?
                WHERE id = ?
                """,
                (outcome, outcome_price, pnl_pct,
                 datetime.now(timezone.utc).isoformat(), row_id)
            )
            conn.commit()

    def get_pending_outcomes(self) -> List[Dict[str, Any]]:
        """Get signals that need outcome checking (pending + past close date)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM signals
                WHERE outcome = 'pending'
                  AND days_to_close IS NOT NULL
                  AND days_to_close <= 0
                ORDER BY detected_at ASC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent(self, hours: int = 24) -> List[Dict[str, Any]]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE detected_at >= ? ORDER BY detected_at DESC",
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) as n FROM signals").fetchone()['n']
            today_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            today = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE detected_at >= ?",
                (today_cutoff,)
            ).fetchone()['n']
            unsent = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE sent_telegram = 0"
            ).fetchone()['n']
            whale_count = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE trade_suspicious = 1"
            ).fetchone()['n']
            wins = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE outcome = 'win'"
            ).fetchone()['n']
            losses = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE outcome = 'loss'"
            ).fetchone()['n']
            resolved = wins + losses

        return {
            'total_signals': total,
            'signals_24h': today,
            'telegram_unsent': unsent,
            'whale_signals': whale_count,
            'wins': wins,
            'losses': losses,
            'win_rate': round(wins / resolved, 3) if resolved > 0 else None,
        }
