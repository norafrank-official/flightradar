import streamlit as st
import requests
import pandas as pd
import folium
from FlightRadar24 import FlightRadar24API
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh # New Import

# --- CONFIGURATION ---
# Replace with your key from https://openweathermap.org/
WEATHER_API_KEY = "ca09bf0a26e46d745eeff8da704aa2e2" 

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="✈️")

# --- AUTO-REFRESH LOGIC (FIX FOR STATIC DATA) ---
# This refreshes the app every 15,000 milliseconds (15 seconds)
# We give it a key so it doesn't lose track of state
st_autorefresh(interval=15000, key="datarefresh")

# --- CSS FOR STYLING ---
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    div[data-testid="stMetricValue"] {
        font-size: 22px;
        color: #00d4ff;
    }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCTIONS ---

def get_weather_data(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url).json()
        return response if response.get("cod") == 200 else None
    except:
        return None

def get_spotting_advice(weather_json):
    clouds = weather_json.get("clouds", {}).get("all", 0)
    main_weather = weather_json.get("weather", [{}])[0].get("main", "")
    if main_weather in ["Rain", "Thunderstorm", "Drizzle", "Snow"]:
        return "❌ Poor", "Rain/Storm detected.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "Heavy clouds.", "orange"
    else:
        return "🌟 Excellent", "Clear skies!", "green"

# --- MAIN APP LOGIC ---

st.title("✈️ SkyWatcher Pro (Live)")

loc = get_geolocation()

if not loc:
    st.info("🛰️ Accessing GPS... Please allow location permissions.")
else:
    lat = loc['coords']['latitude']
    lon = loc['coords']['longitude']
    
    # Weather Section (Updates every 15s with the rest of the app)
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temp", f"{weather['main']['temp']}°C")
        w2.metric("Clouds", f"{weather['clouds']['all']}%")
        w3.metric("Visibility", f"{weather['visibility']/1000} km")
        w4.metric("Spotter Index", status)

    st.divider()

    # Flight Radar Section
    fr_api = FlightRadar24API()
    bounds = fr_api.get_bounds_by_point(lat, lon, 10000) 
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        m = folium.Map(location=[lat, lon], zoom_start=13, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        journey_data = []
        tech_data = []

        for f in flights:
            try:
                # We re-fetch details every refresh to get live Speed/Altitude
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                airline = f.airline_name or "Private"
                flight_no = f.number or f.callsign
                origin = f.origin_airport_name or "Unknown"
                dest = f.destination_airport_name or "Unknown"
            except:
                airline, flight_no, origin, dest = "N/A", f.callsign, "N/A", "N/A"

            journey_data.append({"Airline": airline, "Flight": flight_no, "From": origin, "To": dest})
            tech_data.append({
                "Flight": flight_no, 
                "Alt (ft)": f"{f.altitude:,}", 
                "Speed (kt)": f.ground_speed, 
                "Heading": f"{f.heading}°"
            })

            folium.Marker(
                [f.latitude, f.longitude],
                popup=f"<b>{flight_no}</b>",
                icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
            ).add_to(m)

        # UI Layout
        st_folium(m, width=1200, height=450, returned_objects=[], key=f"map_{f.callsign}")
        
        st.write("### ✈️ Journey Details")
        st.dataframe(pd.DataFrame(journey_data), hide_index=True, use_container_width=True)
        
        st.write("### 📊 Live Technical Data")
        st.dataframe(pd.DataFrame(tech_data), hide_index=True, use_container_width=True)
            
    else:
        st.warning("Scanning for flights...")
