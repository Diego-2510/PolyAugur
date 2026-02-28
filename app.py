import streamlit as st
import config

st.set_page_config(page_title="PolyAugur", layout="wide")

st.title("🧠 PolyAugur - Polymarket Insider Detector")
st.markdown("**Mistral-LLM powered anomaly detection & paper trading.** [file:1]")

if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.success("Setup complete. Ready for Phase 1-8.")

col1, col2 = st.columns(2)
col1.metric("Status", "Phase 0: Ready", "🚀")
col2.info("Next: `streamlit run app.py` → Phase 1 Data Fetcher.")
