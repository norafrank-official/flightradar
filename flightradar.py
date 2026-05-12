import logging
import math

import folium
import folium.plugins
import pandas as pd
import requests
import streamlit as st
from FlightRadar24 import FlightRadar24API
from streamlit_autorefresh import st_autorefresh
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
    "7700": ("[EMERGENCY] General Emergency — Squawk 7700 active", "error"),
    "7600": ("[RADIO] Radio Failure — Squawk 7600 active", "warning"),
    "7500": ("[HIJACK] Hijack Alert — Squawk 7500 active", "error"),
}

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="radar")

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

def project_position(lat: float, lon: float, heading_deg: float, speed_kts: float, minutes: float = 5) -> tuple:
    """Project aircraft position using bearing and ground speed (haversine forward)."""
    if heading_deg is None or speed_kts is None or speed_kts == 0:
        return None

    dist_km = speed_kts * 1.852 * (minutes / 60)
    R = 6371.0
    d = dist_km / R
    bearing = math.radians(heading_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)

    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)

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
        return "LEVEL"
    if vs < -200:
        return "DESCENDING"
    if vs > 200:
        return "CLIMBING"
    return "LEVEL"

def calc_eta_to_airport(altitude_ft: float, vertical_speed_ft_per_min: float) -> float:
    """Rough ETA to runway in minutes. Returns None if not descending."""
    if not altitude_ft or not vertical_speed_ft_per_min or vertical_speed_ft_per_min >= -100:
        return None
    mins_to_ground = altitude_ft / abs(vertical_speed_ft_per_min)
    return mins_to_ground if mins_to_ground > 0 else None

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
        return "Poor", "Precipitation detected. Visibility is low and gear might get wet.", "red"
    if clouds > 80:
        return "Marginal", "Heavy cloud cover. Planes mostly obscured by the ceiling.", "orange"
    if 20 <= clouds <= 80:
        return "Good", "Mixed clouds. Dynamic lighting — great for photography.", "blue"
    return "Excellent", "Clear skies! Perfect visibility for high-altitude spotting.", "green"

# --- APP ---

st.title("SkyWatcher Pro")
st.markdown("### Real-time Flight & Weather Intelligence")

if not WEATHER_API_KEY:
    st.error("Weather API key missing. Add `WEATHER_API_KEY` to `.streamlit/secrets.toml`.")

# Sidebar
st.sidebar.title("Controls")
refresh_interval = st.sidebar.selectbox("Auto-Refresh", ["Manual", "15s", "30s", "60s"], index=2)

# Non-blocking auto-refresh (smooth live updates without UI freeze)
_refresh_map = {"Manual": None, "15s": 15, "30s": 30, "60s": 60}
_refresh_secs = _refresh_map.get(refresh_interval)
if _refresh_secs:
    st_autorefresh(interval=_refresh_secs * 1000, key="data_refresh")

st.sidebar.divider()
density_slot = st.sidebar.empty()
st.sidebar.title("About")
st.sidebar.info("ADS-B transponder data + meteorological data for close-range spotting (~10 km).")
st.sidebar.markdown("""
**Data Stack:**
- **Radar:** FlightRadar24 API
- **Weather:** OpenWeather API
- **UI:** Streamlit & Folium
""")

# Initialize session state for heatmap
if "heatmap_points" not in st.session_state:
    st.session_state.heatmap_points = []

# 1. Location
loc = get_geolocation()

if not loc:
    st.info("Accessing GPS... Please allow location permissions in your browser.")
else:
    lat = loc["coords"]["latitude"]
    lon = loc["coords"]["longitude"]

    # 2. Weather panel
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, _ = get_spotting_advice(weather)
        st.subheader(f"Conditions at {weather.get('name', 'Your Location')}")
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
    st.subheader("Live Radar (10 km Radius)")

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
    status = "OK" if count <= 3 else ("BUSY" if count <= 8 else "CRITICAL")
    density_slot.markdown(f"### Aircraft in Radius: **{count}** [{status}]")

    if flights:
        # Squawk alert scan (before map renders)
        for f in flights:
            sq = str(getattr(f, "squawk", "") or "")
            if sq in SQUAWK_ALERTS:
                msg, level = SQUAWK_ALERTS[sq]
                cs = getattr(f, "callsign", "UNKNOWN")
                if level == "error":
                    st.error(f"{msg} — Aircraft: **{cs}**")
                else:
                    st.warning(f"{msg} — Aircraft: **{cs}**")

        # Process flights
        journey_rows = []
        tech_rows = []
        nearest_km = float("inf")
        nearest_info = None

        for f in flights:
            image_url = None
            try:
                details = fr_api.get_flight_details(f)

                # Extract aircraft image before set_flight_details
                try:
                    thumbs = details.get("aircraft", {}).get("images", {}).get("thumbnails", [])
                    if thumbs:
                        image_url = thumbs[0].get("src")
                    if not image_url:
                        large = details.get("aircraft", {}).get("images", {}).get("large", [])
                        if large:
                            image_url = large[0].get("src")
                except Exception:
                    pass

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
            heading = getattr(f, "heading", None)
            sq = str(getattr(f, "squawk", "") or "")
            f_lat = getattr(f, "latitude", None)
            f_lon = getattr(f, "longitude", None)

            alt_str = f"{alt:,} ft" if alt else "N/A"
            heading_str = f"{heading}°" if heading is not None else "N/A"
            phase = flight_phase(vs)
            color = altitude_color(alt)

            # Journey Details table
            journey_rows.append({
                "Preview": image_url,
                "Airline": airline,
                "Flight": flight_no,
                "To": dest,
            })

            # ETA calculation for descending aircraft
            eta_str = "N/A"
            if vs is not None and vs < -100 and alt:
                eta_min = calc_eta_to_airport(alt, vs)
                if eta_min and eta_min > 0:
                    eta_str = f"~{int(eta_min)} min"

            # Technical Intelligence table
            tech_rows.append({
                "Flight": flight_no,
                "Alt (ft)": alt_str,
                "Speed (kt)": spd if spd else "N/A",
                "Heading": heading_str,
                "Est. Descent": eta_str,
            })

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

                # Accumulate for heatmap
                st.session_state.heatmap_points.append([f_lat, f_lon])

        # Nearest aircraft callout
        if nearest_info:
            st.info(
                f"Closest Aircraft: {nearest_info['airline']} ({nearest_info['flight']}) — "
                f"**{nearest_info['dist']:.2f} km** away · "
                f"{nearest_info['alt']} · {nearest_info['phase']}"
            )

        # Tab interface: Live Radar vs Session Heatmap
        tab_live, tab_heat = st.tabs(["Live Radar", "Session Heatmap"])

        with tab_live:
            # Live Map with projected flight paths
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

            # Airport markers
            try:
                raw_airports = fr_api.get_airports()
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

            # Flight markers with projected paths (B1 feature)
            for f in flights:
                f_lat = getattr(f, "latitude", None)
                f_lon = getattr(f, "longitude", None)
                heading = getattr(f, "heading", None)
                spd = getattr(f, "ground_speed", None)
                alt = getattr(f, "altitude", None)
                sq = str(getattr(f, "squawk", "") or "")
                flight_no = getattr(f, "number", None) or getattr(f, "callsign", "?")
                airline = getattr(f, "airline_name", "Unknown")

                if f_lat and f_lon:
                    color = altitude_color(alt)

                    # Emergency pulse ring
                    if sq in SQUAWK_ALERTS:
                        folium.CircleMarker(
                            [f_lat, f_lon],
                            radius=22,
                            color="red",
                            fill=True,
                            fill_opacity=0.25,
                            tooltip=f"SQUAWK {sq} — {flight_no}",
                        ).add_to(fmap)

                    # Projected flight path (5-min lookahead)
                    proj = project_position(f_lat, f_lon, heading, spd, minutes=5)
                    if proj:
                        folium.PolyLine(
                            [[f_lat, f_lon], proj],
                            color="cyan",
                            weight=2,
                            opacity=0.6,
                            dash_array="5, 5",
                        ).add_to(fmap)
                        folium.CircleMarker(
                            proj,
                            radius=6,
                            color="cyan",
                            fill=True,
                            fill_opacity=0.5,
                            tooltip=f"Projected: {flight_no} in 5 min",
                        ).add_to(fmap)

                    folium.Marker(
                        [f_lat, f_lon],
                        popup=(
                            f"<b>{airline}</b><br>"
                            f"Flight: {flight_no}<br>"
                            f"Alt: {f'{alt:,} ft' if alt else 'N/A'}<br>"
                            f"Speed: {spd} kts"
                        ),
                        tooltip=f"{airline} — {flight_no}",
                        icon=folium.Icon(color=color, icon="plane", prefix="fa"),
                    ).add_to(fmap)

            st_folium(fmap, width=1400, height=600, returned_objects=[], key="flight_map")

        with tab_heat:
            # Session traffic heatmap (B2 feature)
            if st.session_state.heatmap_points:
                hmap = folium.Map(location=[lat, lon], zoom_start=13, tiles="CartoDB dark_matter")
                folium.Marker([lat, lon], tooltip="You", icon=folium.Icon(color="red", icon="user", prefix="fa")).add_to(hmap)

                folium.plugins.HeatMap(
                    st.session_state.heatmap_points,
                    radius=50,
                    blur=15,
                    max_zoom=1,
                ).add_to(hmap)

                st_folium(hmap, width=1400, height=600, returned_objects=[], key="heatmap")
                st.caption(f"Heatmap: {len(st.session_state.heatmap_points)} aircraft positions recorded this session")
            else:
                st.info("Heatmap will populate after a few radar updates. Refresh and wait to see traffic density.")

        st.divider()

        # Journey Details Table
        st.subheader("Journey Details")
        st.dataframe(
            pd.DataFrame(journey_rows),
            column_config={
                "Preview": st.column_config.ImageColumn("Preview", width="small"),
            },
            hide_index=True,
            use_container_width=True,
        )

        st.divider()

        # Technical Intelligence Table
        st.subheader("Technical Intelligence")
        st.dataframe(pd.DataFrame(tech_rows), hide_index=True, use_container_width=True)

        st.markdown("""
**Altitude Color Key:**
[LOW] `< 3,000 ft` — Final approach / departure
[MEDIUM] `3K – 15K ft` — Climbing or descending
[HIGH] `> 15,000 ft` — High cruise

**Heading:** Compass bearing in degrees (0° = North)

**Projected Path:** Dashed cyan line shows where the aircraft will be in 5 minutes
""")

    else:
        st.warning("The sky is quiet! No flights detected within 10 km.")

    # Live update indicator
    if refresh_interval != "Manual":
        st.caption(f"Live data — auto-refreshing every {refresh_interval}")
