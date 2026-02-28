"""
PolyAugur - Streamlit Dashboard
Author: Diego Ringleb | Phase 5 | 2026-02-28
"""

import streamlit as st
import threading
import time
from datetime import datetime, timezone
import config
from src.orchestrator import Orchestrator

st.set_page_config(page_title="PolyAugur", page_icon="🧠", layout="wide")

# ── Init ──────────────────────────────────────────────────────────────────
if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator()
    st.session_state.running = False
    st.session_state.summaries = []
    st.session_state.all_signals = []

orch: Orchestrator = st.session_state.orchestrator

# ── Header ────────────────────────────────────────────────────────────────
st.title("🧠 PolyAugur – Polymarket Insider Detector")
st.caption(f"Mistral-LLM powered anomaly detection | Poll: {config.POLL_INTERVAL_SEC}s | Threshold: {config.MISTRAL_THRESHOLD}")

# ── Controls ──────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("▶ Run One Cycle", use_container_width=True):
        with st.spinner("Running cycle..."):
            summary = orch.run_cycle()
            st.session_state.summaries.append(summary)
            st.session_state.all_signals.extend(summary.get('signals', []))
        st.rerun()

with col2:
    cycle_count = len(st.session_state.summaries)
    st.metric("Cycles Run", cycle_count)

with col3:
    total_signals = len(st.session_state.all_signals)
    st.metric("Total Signals", total_signals)

with col4:
    total_markets = sum(s.get('markets_fetched', 0) for s in st.session_state.summaries)
    st.metric("Markets Analyzed", total_markets)

st.divider()

# ── Last Cycle Summary ────────────────────────────────────────────────────
if st.session_state.summaries:
    last = st.session_state.summaries[-1]
    st.subheader(f"Last Cycle #{last['cycle']} – {last['cycle_time_sec']}s")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Markets Fetched", last['markets_fetched'])
    m2.metric("Anomalies Flagged", last['anomalies_detected'])
    m3.metric("Signals", last['signal_count'])
    m4.metric("Mistral Calls", last['mistral_calls'])

    st.divider()

# ── Active Signals ────────────────────────────────────────────────────────
st.subheader("🚨 Active Signals")

if not st.session_state.all_signals:
    st.info("No signals yet. Click 'Run One Cycle' to start.")
else:
    for sig in reversed(st.session_state.all_signals[-20:]):
        with st.expander(
            f"{'🔴' if sig.get('confidence_score', 0) >= 0.80 else '🟡'} "
            f"{sig.get('question', 'Unknown')[:70]} | "
            f"{sig.get('recommended_trade')} | Conf: {sig.get('confidence_score', 0):.2f}"
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("Trade", sig.get('recommended_trade', 'HOLD'))
            c2.metric("Confidence", f"{sig.get('confidence_score', 0):.0%}")
            c3.metric("Risk", sig.get('risk_level', 'N/A'))

            st.write(f"**Reasoning:** {sig.get('reasoning', 'N/A')}")
            st.write(f"**Anomaly Type:** `{sig.get('anomaly_type', 'N/A')}`")
            st.write(f"**Holding Period:** {sig.get('holding_period_hours', 0)}h")

            if sig.get('supporting_evidence'):
                st.write("**Evidence:** " + " | ".join(sig['supporting_evidence']))

            st.caption(f"Detected: {sig.get('detected_at', 'N/A')} | Cycle #{sig.get('cycle')}")

# ── Status ────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    f"PolyAugur Phase 5 | "
    f"Mistral: {'✅' if config.MISTRAL_API_KEY else '❌'} | "
    f"Threshold: {config.MISTRAL_THRESHOLD} | "
    f"Max pages: {config.MAX_PAGES}"
)
