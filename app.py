import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import google.generativeai as genai
import json
import re
import tempfile
import os

st.set_page_config(page_title="Network Engineer Portal", layout="wide")

# --- SECRETS SETUP ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except KeyError:
    st.error("🚨 Missing GEMINI_API_KEY! Please add it to your Streamlit Cloud Secrets.")
    st.stop()

# --- INITIAL SESSION STATE ---
if "user_db" not in st.session_state: st.session_state.user_db = {"weeks": {}}
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 
if "saved_engineer" not in st.session_state: st.session_state.saved_engineer = ""
if "saved_rate" not in st.session_state: st.session_state.saved_rate = 0.0
if "saved_contract" not in st.session_state: st.session_state.saved_contract = 40
if "saved_service_5yr" not in st.session_state: st.session_state.saved_service_5yr = False
if "resolutions" not in st.session_state: st.session_state.resolutions = {}
if "selected_file_index" not in st.session_state: st.session_state.selected_file_index = 0
if "extracted_files_cache" not in st.session_state: st.session_state.extracted_files_cache = {}

TS_COLS = ["Date Num", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site"]

# --- UTILITIES ---
def sync_json_to_state(data):
    st.session_state.saved_engineer = data.get("name", "")
    st.session_state.saved_rate = float(data.get("rate", 0.0))
    st.session_state.saved_contract = int(data.get("contract", 40))
    st.session_state.saved_service_5yr = bool(data.get("service_5yr", False))
    st.session_state.user_db["weeks"] = data.get("weeks", {})

def get_suffix(day):
    if 11 <= day <= 13: return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')

def calc_hours(start_str, end_str):
    if not start_str or not end_str or pd.isna(start_str) or pd.isna(end_str): return 0.0
    start_str, end_str = str(start_str).strip(), str(end_str).strip()
    if not start_str or not end_str: return 0.0
    fm
