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

st.set_page_config(page_title="SkyWatcher Pro 50K", layout="wide", page_icon="✈️")

# --- AUTO-REFRESH (15 Seconds) ---
st_autorefresh(interval=15000, key="radar_heartbeat")

# --- CSS FOR STYLING ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    div[data-testid="stMetricValue"] { font-size: 22px; color: #00d4ff; }
    .stTable { font-size: 12px; }
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
        return "❌ Poor", "Rain/Storms nearby.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "High cloud cover.", "orange"
    else:
        return "🌟 Excellent", "Clear skies!", "green"

# --- SIDEBAR FILTERS ---
st.sidebar.title("Radar Controls")
max_alt = st.sidebar.slider("Max Altitude (ft)", 0, 50000, 50000, step=1000)
st.sidebar.info("App refreshes every 15s to track live movement.")

# --- MAIN APP ---

st.title("✈️ SkyWatcher Pro: 50km Radar")

loc = get_geolocation()

if not loc:
    st.info("🛰️ Waiting for GPS... Ensure location is enabled in your browser.")
else:
    lat, lon = loc['coords']['latitude'], loc['coords']['longitude']
    
    # 1. Weather Section
    weather = get_weather(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temp", f"{weather['main']['temp']}°C")
        w2.metric("Clouds", f"{weather['clouds']['all']}%")
        w3.metric("Visibility", f"{weather['visibility']/1000}km")
        w4.metric("Spotter Index", status)
        st.info(f"**Spotter Note:** {advice}")

    st.divider()

    # 2. Flight Radar Logic
    fr_api = FlightRadar24API()
    # 50km Radius (50,000 meters)
    bounds = fr_api.get_bounds_by_point(lat, lon, 50000) 
    all_flights = fr_api.get_flights(bounds=bounds)

    # Filter by altitude immediately to save processing time
    flights = [f for f in all_flights if f.altitude <= max_alt]

    if flights:
        # Map Zoom 10 is perfect for a 50km radius
        m = folium.Map(location=[lat, lon], zoom_start=10, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        journey_data, tech_data = [], []

        for f in flights:
            try:
                # Re-fetch details for every refresh to ensure live stats
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                airline = f.airline_name or "Private"
                flight_no = f.number or f.callsign
                dest = f.destination_airport_name or "N/A"
                
                journey_data.append({"Airline": airline, "Flight": flight_no, "To": dest})
                tech_data.append({
                    "Flight": flight_no, 
                    "Alt (ft)": f"{f.altitude:,}", 
                    "Speed (kt)": f.ground_speed, 
                    "Heading": f"{f.heading}°"
                })

                folium.Marker(
                    [f.latitude, f.longitude],
                    popup=f"{flight_no} to {dest}",
                    icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
                ).add_to(m)
            except:
                continue

        # UI Layout - Tables now use use_container_width=True
        st_folium(m, width=1200, height=500, returned_objects=[], key="map_50k")
        
        st.write("### ✈️ Journey Details")
        st.dataframe(pd.DataFrame(journey_data), hide_index=True, use_container_width=True)
        
        st.write("### 📊 Live Technical Data")
        st.dataframe(pd.DataFrame(tech_data), hide_index=True, use_container_width=True)
            
    else:
        st.warning(f"No flights detected within 50km under {max_alt}ft.")
