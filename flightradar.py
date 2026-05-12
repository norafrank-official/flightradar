import logging
import math
import time

import folium
import pandas as pd
import requests
import streamlit as st
from FlightRadar24 import FlightRadar24API
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# --- CONFIGURATION ---
try:
    WEATHER_API_KEY = st.secrets.get("WEATHER_API_KEY", "")
except Exception:
    WEATHER_API_KEY = ""

SEARCH_RADIUS_M = 10_000  # 10 km — close-range spotting

SQUAWK_ALERTS = {
    "7700": ("🚨 GENERAL EMERGENCY — Squawk 7700 active", "error"),
    "7600": ("📻 RADIO FAILURE — Squawk 7600 active", "warning"),
    "7500": ("✈️ HIJACK ALERT — Squawk 7500 active", "error"),
}

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="✈️")

st.markdown("""
    <style>
    .main { background-color: #0a0a0a; }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #00d4ff; }
    </style>
""", unsafe_allow_html=True)

# --- UTILITIES ---

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

def altitude_color(alt) -> str:
    if not alt:
        return "gray"
    if alt < 3000:
        return "green"
    if alt < 15000:
        return "orange"
    return "red"

def flight_phase(vs) -> str:
    if vs is None:
        return "→ Cruising"
    if vs < -200:
        return "↓ Descending"
    if vs > 200:
        return "↑ Climbing"
    return "→ Cruising"

# --- API FUNCTIONS ---

@st.cache_data(ttl=60)
def get_weather_data(lat: float, lon: float):
    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        )
        data = requests.get(url, timeout=10).json()
        return data if data.get("cod") == 200 else None
    except Exception as e:
        logging.warning(f"Weather fetch failed: {e}")
        return None

def get_spotting_advice(w: dict) -> tuple:
    clouds = w.get("clouds", {}).get("all", 0)
    main_wx = w.get("weather", [{}])[0].get("main", "")
    if main_wx in ["Rain", "Thunderstorm", "Drizzle", "Snow"]:
        return "❌ Poor", "Precipitation detected. Visibility is low and gear might get wet.", "red"
    if clouds > 80:
        return "⚠️ Marginal", "Heavy cloud cover. Planes mostly obscured by the ceiling.", "orange"
    if 20 <= clouds <= 80:
        return "✅ Good", "Mixed clouds. Dynamic lighting — great for photography.", "blue"
    return "🌟 Excellent", "Clear skies! Perfect visibility for high-altitude spotting.", "green"

# --- APP ---

st.title("✈️ SkyWatcher Pro")
st.markdown("### Real-time Flight & Weather Intelligence")

if not WEATHER_API_KEY:
    st.error("⚠️ Weather API key missing. Add `WEATHER_API_KEY` to `.streamlit/secrets.toml`.")

# Sidebar
st.sidebar.title("⚙️ Controls")
refresh_interval = st.sidebar.selectbox("Auto-Refresh", ["Manual", "15s", "30s", "60s"], index=0)
st.sidebar.divider()
density_slot = st.sidebar.empty()
st.sidebar.title("ℹ️ About")
st.sidebar.info("ADS-B transponder data + meteorological data for close-range spotting (~10 km).")
st.sidebar.markdown("""
**Data Stack:**
- **Radar:** FlightRadar24 API
- **Weather:** OpenWeather API
- **UI:** Streamlit & Folium
""")

# 1. Location
loc = get_geolocation()

if not loc:
    st.info("🛰️ Accessing GPS... Please allow location permissions in your browser.")
else:
    lat = loc["coords"]["latitude"]
    lon = loc["coords"]["longitude"]

    # 2. Weather panel
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, _ = get_spotting_advice(weather)
        st.subheader(f"📍 Conditions at {weather.get('name', 'Your Location')}")
        m1, m2, m3, m4 = st.columns(4)
        temp = weather.get("main", {}).get("temp", "N/A")
        cloud_pct = weather.get("clouds", {}).get("all", "N/A")
        vis_raw = weather.get("visibility")
        vis = f"{vis_raw / 1000:.1f} km" if vis_raw else "N/A"
        m1.metric("Temperature", f"{temp}°C" if temp != "N/A" else "N/A")
        m2.metric("Cloud Cover", f"{cloud_pct}%" if cloud_pct != "N/A" else "N/A")
        m3.metric("Visibility", vis)
        m4.metric("Spotter Index", status)
        st.info(f"**Spotter's Note:** {advice}")

    st.divider()

    # 3. Radar
    st.subheader("📡 Live Radar (10 km Radius)")

    try:
        fr_api = FlightRadar24API()
        bounds = fr_api.get_bounds_by_point(lat, lon, SEARCH_RADIUS_M)
        flights = fr_api.get_flights(bounds=bounds)
    except Exception as e:
        logging.warning(f"FlightRadar24 fetch failed: {e}")
        st.error("Could not reach FlightRadar24. Check your connection or try again.")
        flights = []

    # Sky density badge
    count = len(flights) if flights else 0
    badge = "🟢" if count <= 3 else ("🟠" if count <= 8 else "🔴")
    density_slot.markdown(f"### {badge} Aircraft in Radius: **{count}**")

    if flights:
        # Squawk alert scan (before map renders — maximum visibility)
        for f in flights:
            sq = str(getattr(f, "squawk", "") or "")
            if sq in SQUAWK_ALERTS:
                msg, level = SQUAWK_ALERTS[sq]
                cs = getattr(f, "callsign", "UNKNOWN")
                if level == "error":
                    st.error(f"{msg} — Aircraft: **{cs}**")
                else:
                    st.warning(f"{msg} — Aircraft: **{cs}**")

        col_map, col_list = st.columns([2, 1])

        # Map setup
        fmap = folium.Map(location=[lat, lon], zoom_start=13, tiles="CartoDB dark_matter")
        folium.Marker(
            [lat, lon],
            tooltip="You are here",
            icon=folium.Icon(color="red", icon="user", prefix="fa")
        ).add_to(fmap)
        folium.Circle(
            location=[lat, lon],
            radius=SEARCH_RADIUS_M,
            color="#00d4ff",
            weight=1,
            fill=True,
            fill_opacity=0.04,
        ).add_to(fmap)

        # Airport markers (within ~20 km for context)
        try:
            raw_airports = fr_api.get_airports()
            # API may return a list directly or a dict with an "airports" key
            if isinstance(raw_airports, dict):
                airport_list = raw_airports.get("airports", [])
            elif isinstance(raw_airports, list):
                airport_list = raw_airports
            else:
                airport_list = []

            for ap in airport_list:
                try:
                    ap_lat = float(ap.latitude)
                    ap_lon = float(ap.longitude)
                    if haversine_km(lat, lon, ap_lat, ap_lon) > 20:
                        continue
                    iata = getattr(ap, "iata", None) or getattr(ap, "code", "?")
                    name = getattr(ap, "name", "Airport")
                    city = getattr(ap, "city", "")
                    folium.Marker(
                        [ap_lat, ap_lon],
                        popup=f"<b>{iata}</b><br>{name}<br>{city}",
                        tooltip=iata,
                        icon=folium.Icon(color="darkblue", icon="building", prefix="fa"),
                    ).add_to(fmap)
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"Airport markers failed: {e}")

        # Flight loop
        rows = []
        nearest_km = float("inf")
        nearest_info = None

        for f in flights:
            try:
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                airline = f.airline_name or "Private/Unknown"
                flight_no = f.number or f.callsign
                origin = f.origin_airport_name or "Unknown"
                dest = f.destination_airport_name or "Unknown"
            except Exception as e:
                logging.warning(f"Detail fetch failed {getattr(f, 'callsign', '?')}: {e}")
                airline = "N/A"
                flight_no = getattr(f, "callsign", "N/A")
                origin = "N/A"
                dest = "N/A"

            alt = getattr(f, "altitude", None)
            vs = getattr(f, "vertical_speed", None)
            spd = getattr(f, "ground_speed", None)
            sq = str(getattr(f, "squawk", "") or "")
            f_lat = getattr(f, "latitude", None)
            f_lon = getattr(f, "longitude", None)

            alt_str = f"{alt:,} ft" if alt else "N/A"
            phase = flight_phase(vs)
            color = altitude_color(alt)

            if f_lat and f_lon:
                d = haversine_km(lat, lon, f_lat, f_lon)
                if d < nearest_km:
                    nearest_km = d
                    nearest_info = {
                        "airline": airline,
                        "flight": flight_no,
                        "dist": d,
                        "alt": alt_str,
                        "phase": phase,
                    }

                # Emergency pulse ring
                if sq in SQUAWK_ALERTS:
                    folium.CircleMarker(
                        [f_lat, f_lon],
                        radius=22,
                        color="red",
                        fill=True,
                        fill_opacity=0.25,
                        tooltip=f"⚠️ SQUAWK {sq} — {flight_no}",
                    ).add_to(fmap)

                folium.Marker(
                    [f_lat, f_lon],
                    popup=(
                        f"<b>{airline}</b><br>"
                        f"Flight: {flight_no}<br>"
                        f"From: {origin}<br>"
                        f"To: {dest}<br>"
                        f"Alt: {alt_str}<br>"
                        f"Speed: {spd} kts<br>"
                        f"{phase}"
                    ),
                    tooltip=f"{airline} — {flight_no}",
                    icon=folium.Icon(color=color, icon="plane", prefix="fa"),
                ).add_to(fmap)

            rows.append({
                "Airline": airline,
                "Flight": flight_no,
                "From": origin,
                "To": dest,
                "Altitude": alt_str,
                "Speed (kts)": spd if spd else "N/A",
                "Phase": phase,
            })

        # Nearest aircraft callout
        if nearest_info:
            st.info(
                f"🎯 **Closest Aircraft:** {nearest_info['airline']} ({nearest_info['flight']}) — "
                f"**{nearest_info['dist']:.2f} km** away · "
                f"{nearest_info['alt']} · {nearest_info['phase']}"
            )

        with col_map:
            st_folium(fmap, width=800, height=500, returned_objects=[], key="flight_map")

        with col_list:
            st.write("**Aircraft Details**")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.markdown("""
**Altitude Color Key:**
🟢 `< 3,000 ft` — Final approach / departure
🟠 `3K – 15K ft` — Climbing or descending
🔴 `> 15,000 ft` — High cruise
""")

    else:
        st.warning("The sky is quiet! No flights detected within 10 km.")

    # Auto-refresh countdown
    if refresh_interval != "Manual":
        seconds = int(refresh_interval.rstrip("s"))
        ph = st.empty()
        for i in range(seconds, 0, -1):
            ph.caption(f"🔄 Refreshing in {i}s...")
            time.sleep(1)
        ph.empty()
        st.rerun()
