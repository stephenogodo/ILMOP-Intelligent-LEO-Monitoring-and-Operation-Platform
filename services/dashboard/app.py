"""
services/dashboard/app.py

ILMOP Mission Control Dashboard — Streamlit real-time operator interface.

Start with:
    streamlit run services/dashboard/app.py

The dashboard calls the ILMOP FastAPI (Option A architecture): it is a
pure API consumer and never connects to TimescaleDB directly.  All data
flows through the REST layer, so any business logic, caching, or access
control added to the API is automatically inherited by the dashboard.
"""

import time
import requests
import pandas as pd
import streamlit as st

from shared.config import settings

# ── Page configuration ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title = "ILMOP Mission Control",
    page_icon  = "🛰️",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

API = settings.api_base_url


# ── Helpers ────────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> dict | list | None:
    """Call the ILMOP API. Returns parsed JSON or None on failure."""
    try:
        r = requests.get(f"{API}{path}", params=params, timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error(f"⚠️  Cannot reach API at {API}. Is the API server running?")
        return None
    except requests.exceptions.HTTPError as e:
        if e.response.status_code != 404:
            st.warning(f"API error: {e}")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None


def status_colour(value: float, warn: float, crit: float, invert: bool = False) -> str:
    """Return a colour string based on threshold comparison."""
    if invert:
        return "🔴" if value <= crit else "🟡" if value <= warn else "🟢"
    return "🔴" if value >= crit else "🟡" if value >= warn else "🟢"


# ── Sidebar ────────────────────────────────────────────────────────────────────

st.sidebar.title("🛰️ ILMOP Mission Control")
st.sidebar.markdown("---")

# Satellite selector — populated from /satellites endpoint
satellites_data = api_get("/satellites")
if satellites_data:
    sat_ids = [s["satellite_id"] for s in satellites_data]
else:
    sat_ids = [settings.simulator_satellite_id]

selected_sat = st.sidebar.selectbox("Satellite", sat_ids)

st.sidebar.markdown("---")
history_hours = st.sidebar.slider("History window (hours)", 1, 24, 2)
refresh_s     = st.sidebar.slider("Refresh interval (s)", 1, 10, 2)

st.sidebar.markdown("---")
st.sidebar.caption(f"API: `{API}`")
st.sidebar.caption(f"Schema: v2.0")

# ── Main layout ────────────────────────────────────────────────────────────────

st.title(f"🛰️  {selected_sat} — Live Telemetry")

# ── Current status ─────────────────────────────────────────────────────────────

latest = api_get(f"/telemetry/{selected_sat}/latest")

if latest:
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    batt_icon = status_colour(latest["battery_pct"], 30, 15, invert=True)
    temp_icon = status_colour(latest["temperature_c"], 50, 60)

    col1.metric("🔋 Battery",      f"{latest['battery_pct']:.1f}%",
                delta=f"{latest['battery_voltage_v']:.2f} V")
    col2.metric("🌡️ Temperature",  f"{latest['temperature_c']:.1f} °C")
    col3.metric("☀️ Solar Power",  f"{latest['solar_panel_power_w']:.0f} W")
    col4.metric("🖥️ CPU",          f"{latest['cpu_utilization_pct']:.1f}%")
    col5.metric("🌑 Eclipse",      "YES" if latest["in_eclipse"] else "NO")
    col6.metric("📡 Contact",      "YES" if latest["in_contact"] else "NO")

    st.markdown("---")

    # ── Ground track ──────────────────────────────────────────────────────────
    st.subheader("🌍 Orbital Position")
    gcol1, gcol2, gcol3 = st.columns(3)
    gcol1.metric("Latitude",  f"{latest['latitude_deg']:+.2f}°")
    gcol2.metric("Longitude", f"{latest['longitude_deg']:+.2f}°")
    gcol3.metric("Altitude",  f"{latest['altitude_km']:.1f} km")

    # Map
    map_df = pd.DataFrame(
        [[latest["latitude_deg"], latest["longitude_deg"]]],
        columns=["lat", "lon"]
    )
    st.map(map_df, zoom=1)

    st.markdown("---")
else:
    st.warning("No live telemetry available. Is the simulator and sink running?")

# ── Historical charts ──────────────────────────────────────────────────────────

st.subheader("📈 Telemetry Trends")

summary = api_get(
    f"/telemetry/{selected_sat}/summary",
    params={"from": pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=history_hours),
            "to":   pd.Timestamp.now(tz="UTC")}
)

if summary:
    df = pd.DataFrame(summary)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("**🔋 Battery State of Charge (%)**")
        st.line_chart(df[["avg_battery_pct"]])

        st.markdown("**☀️ Solar Panel Power (W)**")
        st.line_chart(df[["avg_solar_power_w"]])

    with chart_col2:
        st.markdown("**🌡️ Temperature (°C)**")
        st.line_chart(df[["avg_temperature_c"]])

        st.markdown("**🖥️ CPU Utilisation (%)**")
        st.line_chart(df[["avg_cpu_pct"]])

else:
    # Fallback: try raw telemetry history
    history = api_get(
        f"/telemetry/{selected_sat}",
        params={"from": pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=history_hours),
                "to":   pd.Timestamp.now(tz="UTC"), "limit": 1000}
    )
    if history:
        df = pd.DataFrame(history)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.markdown("**🔋 Battery State of Charge (%)**")
            st.line_chart(df[["battery_pct"]])
            st.markdown("**☀️ Solar Panel Power (W)**")
            st.line_chart(df[["solar_panel_power_w"]])
        with chart_col2:
            st.markdown("**🌡️ Temperature (°C)**")
            st.line_chart(df[["temperature_c"]])
            st.markdown("**🖥️ CPU Utilisation (%)**")
            st.line_chart(df[["cpu_utilization_pct"]])
    else:
        st.info("No historical data available yet. Start the simulator and sink.")

# ── Alarm panel (placeholder for Sprint 5) ────────────────────────────────────

st.markdown("---")
st.subheader("🚨 Alarms")
st.info("Anomaly detection alarms will appear here in Sprint 5.")

# ── Timestamp and auto-refresh ─────────────────────────────────────────────────

st.caption(f"Last updated: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M:%S UTC')}")

time.sleep(refresh_s)
st.rerun()
