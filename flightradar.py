import streamlit as st
import requests
import pandas as pd
import folium
from FlightRadar24 import FlightRadar24API
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh

# --- CONFIGURATION ---
# Replace with your actual key or use st.secrets["WEATHER_API_KEY"]
WEATHER_API_KEY = "ca09bf0a26e46d745eeff8da704aa2e2" 

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="✈️")

# --- AUTO-REFRESH (Every 15 Seconds) ---
st_autorefresh(interval=15000, key="radar_pulse")

# --- CSS FOR STYLING ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    div[data-testid="stMetricValue"] { font-size: 20px; color: #00d4ff; }
    h3 { margin-top: 20px; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCTIONS ---

def get_weather(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        res = requests.get(url).json()
        return res if res.get("cod") == 200 else None
    except:
        return None

def get_spotting_advice(w_json):
    clouds = w_json.get("clouds", {}).get("all", 0)
    main = w_json.get("weather", [{}])[0].get("main", "")
    if main in ["Rain", "Thunderstorm", "Drizzle"]:
        return "❌ Poor", "Rain/Storms in area.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "Heavy cloud cover.", "orange"
    return ("🌟 Excellent", "Clear skies!", "green") if clouds < 20 else ("✅ Good", "Fair visibility.", "blue")

# --- MAIN APP ---

st.title("✈️ SkyWatcher Pro")

loc = get_geolocation()

if not loc:
    st.info("🛰️ Waiting for GPS... Please allow location access in your browser.")
else:
    lat, lon = loc['coords']['latitude'], loc['coords']['longitude']
    
    # 1. Weather Dashboard
    weather = get_weather(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temp", f"{weather['main']['temp']}°C")
        w2.metric("Clouds", f"{weather['clouds']['all']}%")
        vis = weather['visibility']/1000
        w3.metric("Visibility", "10+ km" if vis >= 10 else f"{vis} km")
        w4.metric("Spotter Index", status)
        st.info(f"**Spotter Note:** {advice}")

    st.divider()

    # 2. Flight Radar Logic
    fr_api = FlightRadar24API()
    # Scans 50km radius
    bounds = fr_api.get_bounds_by_point(lat, lon, 50000) 
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        # Map Zoom 10 for 50km radius
        m = folium.Map(location=[lat, lon], zoom_start=10, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        journey_data = []
        tech_data = []

        for f in flights:
            try:
                # Fetching details for photos and routes
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                # Image URL Extraction
                img_url = None
                if 'aircraft' in details and 'images' in details['aircraft']:
                    images = details['aircraft']['images']
                    if 'thumbnails' in images and len(images['thumbnails']) > 0:
                        img_url = images['thumbnails'][0]['src']

                airline = f.airline_name or "Private"
                flight_no = f.number or f.callsign
                dest = f.destination_airport_name or "N/A"

                # Table 1: Journey Data
                journey_data.append({
                    "Preview": img_url,
                    "Airline": airline,
                    "Flight": flight_no,
                    "To": dest
                })
                
                # Table 2: Tech Data
                tech_data.append({
                    "Flight": flight_no,
                    "Alt (ft)": f"{f.altitude:,}",
                    "Speed (kt)": f.ground_speed,
                    "Heading": f"{f.heading}°"
                })

                # Map Marker
                folium.Marker(
                    [f.latitude, f.longitude],
                    popup=f"<b>{flight_no}</b>",
                    icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
                ).add_to(m)
            except:
                continue

        # --- UI LAYOUT ---
        st_folium(m, width=1200, height=450, returned_objects=[], key="map_global")
        
        # Journey Table (With Photos)
        st.write("### ✈️ Journey Details")
        st.dataframe(
            pd.DataFrame(journey_data),
            column_config={
                "Preview": st.column_config.ImageColumn("Preview"),
            },
            hide_index=True,
            use_container_width=True
        )
        
        # Technical Table
        st.write("### 📊 Technical Intelligence")
        st.dataframe(
            pd.DataFrame(tech_data),
            hide_index=True, 
            use_container_width=True
        )
            
    else:
        st.warning("No flights detected within 50km. The sky is clear!")

st.sidebar.markdown("---")
st.sidebar.write("📡 **SkyWatcher Pro v3.0**")
st.sidebar.info("Auto-refresh is active. Tracking live transponder telemetry.")
