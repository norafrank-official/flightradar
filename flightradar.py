import streamlit as st
import requests
import pandas as pd
import folium
import math
from FlightRadar24 import FlightRadar24API
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium

# --- CONFIGURATION ---
# Replace with your free API key from https://openweathermap.org/
WEATHER_API_KEY = "ca09bf0a26e46d745eeff8da704aa2e2"

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="✈️")

# --- FUNCTIONS ---

def get_weather_data(lat, lon):
    """Fetches weather and cloud data."""
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url).json()
        if response.get("cod") == 200:
            return response
    except:
        return None

def get_spotting_advice(weather_json):
    """Logic to determine if plane spotting is ideal."""
    clouds = weather_json.get("clouds", {}).get("all", 0) # Cloud cover %
    visibility = weather_json.get("visibility", 10000) / 1000 # Convert to km
    main_weather = weather_json.get("weather", [{}])[0].get("main", "")

    if main_weather in ["Rain", "Thunderstorm", "Snow"]:
        return "❌ Poor", "Stay inside! Precipitation will ruin your gear and visibility.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "High cloud cover. You'll hear them, but you might not see them.", "orange"
    elif 20 <= clouds <= 80:
        return "✅ Good", "Partly cloudy. Great for dramatic lighting, but planes may duck behind clouds.", "blue"
    else:
        return "🌟 Excellent", "Clear skies and high visibility. Perfect for spotting!", "green"

# --- APP LAYOUT ---

st.title("✈️ SkyWatcher Pro")
st.markdown("Independent Global Flight Tracker & Weather Station")

# Get User Location
loc = get_geolocation()

if not loc:
    st.info("🛰️ Waiting for GPS location... Please allow location access in your browser.")
else:
    lat = loc['coords']['latitude']
    lon = loc['coords']['longitude']
    
    # 1. WEATHER & SPOTTER SECTION
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        
        st.subheader(f"📍 Current Conditions: {weather['name']}")
        
        # Metric Row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Temperature", f"{weather['main']['temp']}°C")
        m2.metric("Cloud Cover", f"{weather['clouds']['all']}%")
        m3.metric("Visibility", f"{weather['visibility']/1000} km")
        m4.metric("Spotting Status", status)
        
        st.info(f"**Pro Tip:** {advice}")

    st.divider()

    # 2. FLIGHT TRACKER SECTION
    st.subheader("📡 Live Radar (10km Radius)")
    
    fr_api = FlightRadar24API()
    bounds = fr_api.get_bounds_by_point(lat, lon, 10000) # 10,000 meters
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        col_map, col_list = st.columns([2, 1])
        
        # Initialize Map
        m = folium.Map(location=[lat, lon], zoom_start=12, tiles='CartoDB positron')
        folium.Marker([lat, lon], tooltip="You", icon=folium.Icon(color='red', icon='home')).add_to(m)

        flight_list_for_table = []

        for f in flights:
            # Add to Map
            folium.Marker(
                [f.latitude, f.longitude],
                popup=f"Flight: {f.callsign}\nAltitude: {f.altitude}ft",
                icon=folium.Icon(color='blue', icon='plane', prefix='fa')
            ).add_to(m)
            
            # Detailed Info for Table
            flight_list_for_table.append({
                "Callsign": f.callsign,
                "Altitude": f"{f.altitude} ft",
                "Speed": f"{f.ground_speed} kt",
                "Heading": f"{f.heading}°"
            })

        with col_map:
            st_folium(m, width=800, height=500)
        
        with col_list:
            st.write("**Aircraft in Range**")
            st.table(pd.DataFrame(flight_list_for_table))
            
    else:
        st.warning("No flights currently within 10km of your location.")

st.sidebar.markdown("### How it works")
st.sidebar.info(
    "1. **GPS:** Gets your exact location via the browser.\n"
    "2. **OpenWeather:** Checks cloud cover & visibility.\n"
    "3. **FlightRadar24:** Fetches ADS-B data for nearby planes."
)
