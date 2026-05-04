import streamlit as st

PORTFOLIO_URL = "https://ykaether.github.io/ai-portfolio/"

st.set_page_config(
    page_title="Redirecting...",
    page_icon="↗",
    layout="centered"
)

st.markdown(
    f"""
    <meta http-equiv="refresh" content="0; url={PORTFOLIO_URL}">
    <script>
      window.location.replace("{PORTFOLIO_URL}");
    </script>

    Redirecting to portfolio...
    """,
    unsafe_allow_html=True
)
