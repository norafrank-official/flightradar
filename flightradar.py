import streamlit as st
import requests
import pandas as pd
import folium
from FlightRadar24 import FlightRadar24API
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh

# --- CONFIGURATION ---
WEATHER_API_KEY = "ca09bf0a26e46d745eeff8da704aa2e2" 

st.set_page_config(page_title="SkyWatcher Pro Max", layout="wide", page_icon="✈️")

# Refresh every 15 seconds
st_autorefresh(interval=15000, key="radar_heartbeat")

# --- CSS FOR STYLING ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    div[data-testid="stMetricValue"] { font-size: 22px; color: #00d4ff; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCTIONS ---

def get_weather(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        res = requests.get(url).json()
        return res if res.get("cod") == 200 else None
    except: return None

def get_spotting_advice(w_json):
    clouds = w_json.get("clouds", {}).get("all", 0)
    main = w_json.get("weather", [{}])[0].get("main", "")
    if main in ["Rain", "Thunderstorm"]: return "❌ Poor", "Bad weather.", "red"
    return ("🌟 Excellent", "Clear skies!", "green") if clouds < 20 else ("✅ Good", "Fair skies.", "blue")

# --- MAIN APP ---

st.title("✈️ SkyWatcher Pro: 50km Live Radar")

loc = get_geolocation()

if not loc:
    st.info("🛰️ Waiting for GPS location...")
else:
    lat, lon = loc['coords']['latitude'], loc['coords']['longitude']
    
    # 1. Weather
    weather = get_weather(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temp", f"{weather['main']['temp']}°C")
        w2.metric("Clouds", f"{weather['clouds']['all']}%")
        vis = weather['visibility']/1000
        w3.metric("Visibility", "10+ km" if vis >= 10 else f"{vis} km")
        w4.metric("Spotter Index", status)

    st.divider()

    # 2. Flight Radar
    fr_api = FlightRadar24API()
    bounds = fr_api.get_bounds_by_point(lat, lon, 50000) 
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        m = folium.Map(location=[lat, lon], zoom_start=10, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        display_data = []

        for f in flights:
            try:
                # Fetch full details including images
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                # Get the thumbnail URL
                # The details object usually has an 'aircraft' -> 'images' -> 'thumbnails' structure
                img_url = None
                if 'aircraft' in details and 'images' in details['aircraft']:
                    images = details['aircraft']['images']
                    if 'thumbnails' in images and len(images['thumbnails']) > 0:
                        img_url = images['thumbnails'][0]['src']
                    elif 'large' in images and len(images['large']) > 0:
                        img_url = images['large'][0]['src']

                display_data.append({
                    "Preview": img_url,
                    "Airline": f.airline_name or "Private",
                    "Flight": f.number or f.callsign,
                    "To": f.destination_airport_name or "N/A",
                    "Alt (ft)": f"{f.altitude:,}",
                    "Speed (kt)": f.ground_speed
                })

                folium.Marker(
                    [f.latitude, f.longitude],
                    popup=f"{f.callsign}",
                    icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
                ).add_to(m)
            except: continue

        # UI Layout
        st_folium(m, width=1200, height=450, returned_objects=[], key="map_50k")
        
        st.write("### 📡 Live Air Traffic Details")
        
        # PRO FEATURE: Render images in the dataframe
        df = pd.DataFrame(display_data)
        st.dataframe(
            df,
            column_config={
                "Preview": st.column_config.ImageColumn("Preview", help="Actual aircraft photo"),
            },
            hide_index=True,
            use_container_width=True
        )
            
    else:
        st.warning("Scanning for flights in your 50km radius...")

st.sidebar.markdown("---")
st.sidebar.write("Project by Nora Frank")
