import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re
import zipfile
import io
import json

st.set_page_config(page_title="Network Engineer Portal", layout="wide")

# --- INITIAL SESSION STATE ---
if "user_db" not in st.session_state: st.session_state.user_db = {"weeks": {}}
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 
if "saved_engineer" not in st.session_state: st.session_state.saved_engineer = ""
if "saved_rate" not in st.session_state: st.session_state.saved_rate = 0.0
if "saved_contract" not in st.session_state: st.session_state.saved_contract = 40
if "saved_service_5yr" not in st.session_state: st.session_state.saved_service_5yr = False

# --- UTILITIES ---
def sync_json_to_state(data):
    st.session_state.saved_engineer = data.get("name", "")
    st.session_state.saved_rate = float(data.get("rate", 0.0))
    st.session_state.saved_contract = int(data.get("contract", 40))
    st.session_state.saved_service_5yr = bool(data.get("service_5yr", False))
    st.session_state.user_db["weeks"] = data.get("weeks", {})

def calc_hours(start_str, end_str):
    if not start_str or not end_str: return 0.0
    try:
        fmt = "%H:%M"
        tdelta = datetime.strptime(end_str.rjust(5, '0'), fmt) - datetime.strptime(start_str.rjust(5, '0'), fmt)
        hrs = tdelta.total_seconds() / 3600
        return round(hrs + 24 if hrs < 0 else hrs, 2)
    except: return 0.0

def process_timesheet_data(df, end_date_obj=None, missing_selections=None, contract_hours=40):
    processed_data = []
    if df.empty and not missing_selections: return []
    
    # Process existing rows
    for _, row in df.iterrows():
        dn, site, beg, arr, lft = str(row["Date Num"]), str(row["Site & Ref No."]), str(row["Began Journey"]), str(row["Arrived On Site"]), str(row["Left Site"])
        work, travel = calc_hours(arr, lft), calc_hours(beg, arr)
        
        f_date = ""
        if end_date_obj and dn:
            for i in range(7):
                curr = end_date_obj - timedelta(days=6-i)
                if str(curr.day) == dn:
                    f_date = curr.strftime("%Y-%m-%d")
                    break
        processed_data.append({"date": dn, "full_date": f_date, "site": site, "work": work, "travel": travel})
    
    # Process "Ghost" Rows (Leave/Sick)
    if missing_selections:
        daily = contract_hours / 5.0
        for d_num, reason in missing_selections.items():
            if reason == "Ignore": continue
            f_date = ""
            if end_date_obj:
                for i in range(7):
                    curr = end_date_obj - timedelta(days=6-i)
                    if str(curr.day) == str(d_num):
                        f_date = curr.strftime("%Y-%m-%d")
                        break
            processed_data.append({"date": d_num, "full_date": f_date, "site": reason.upper(), "work": daily if reason == "Annual Leave" else 0.0, "travel": 0.0})
            
    return processed_data

# --- APP FLOW ---
st.title("Network Engineer Portal")

# 1. DATABASE & PROFILE
with st.expander("📂 Profile & Database Management", expanded=not st.session_state.saved_engineer):
    c1, c2 = st.columns([2, 1])
    with c1:
        db_file = st.file_uploader("Restore JSON Database", type=["json"], key=f"db_up_{st.session_state.uploader_key}")
        if db_file:
            sync_json_to_state(json.loads(db_file.getvalue().decode("utf-8")))
            st.success("Database Restored.")
    with c2:
        st.session_state.saved_engineer = st.text_input("Name", value=st.session_state.saved_engineer)
        st.session_state.saved_rate = st.number_input("Rate (£)", value=st.session_state.saved_rate, step=0.5)
        st.session_state.saved_contract = st.selectbox("Contract", [40, 45], index=0 if st.session_state.saved_contract == 40 else 1)
        st.session_state.saved_service_5yr = st.checkbox("> 5 Years Service", value=st.session_state.saved_service_5yr)

if not st.session_state.saved_engineer or st.session_state.saved_rate == 0:
    st.warning("Please setup profile above.")
    st.stop()

# 2. PDF EXTRACTION
uploaded_pdfs = st.file_uploader("Upload Timesheets", type=["pdf"], accept_multiple_files=True, key=f"pdf_up_{st.session_state.uploader_key}")
all_uploads = {}

if uploaded_pdfs:
    for f in uploaded_pdfs:
        we, wk, rows = "", "", []
        with pdfplumber.open(f) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                m_we = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
                if m_we: we = m_we.group(1)
                m_wk = re.search(r"Week:\s*(\d+)", text)
                if m_wk: wk = m_wk.group(1)
                for line in text.split('\n'):
                    times = re.findall(r'\d{1,2}:\d{2}', line)
                    if times:
                        site_raw = line.split(times[0])[0].strip()
                        d_m = re.search(r"(\d{1,2})\s+", site_raw)
                        rows.append({"Date Num": d_m.group(1) if d_m else "", "Site & Ref No.": re.sub(r"^[A-Z]?\s?\d{1,2}\s+", "", site_raw), "Began Journey": times[0], "Arrived On Site": times[1] if len(times)>1 else "", "Left Site": times[2] if len(times)>2 else ""})
        
        # BLANK INTERCEPTOR
        if not rows:
            fm = re.search(r"[Ww]eek[_\s]*(\d+)", f.name)
            wk = fm.group(1) if fm else wk
            
        all_uploads[f.name] = {"we": we, "wk": wk, "df": pd.DataFrame(rows), "status": "Exists" if we in st.session_state.user_db["weeks"] else "New"}

# --- TABS ---
t1, t2, t3, t4, t5 = st.tabs(["📑 Editor", "💷 Sync & Arrears", "🤒 Sickness", "🏖️ Annual Leave", "💾 Backup"])

with t1:
    if not all_uploads: st.info("Upload PDFs.")
    else:
        sel = st.selectbox("Select Timesheet:", list(all_uploads.keys()))
        up = all_uploads[sel]
        
        # Conflict Warning
        if up["status"] == "Exists":
            st.error(f"⚠️ This week ({up['we']}) is already in your JSON database.")
            
        c1, c2 = st.columns(2)
        final_we = c1.text_input("Week End", value=up["we"], key=f"we_{sel}")
        final_wk = c2.text_input("Week No", value=up["wk"], key=f"wk_{sel}")
        
        try: dt_obj = datetime.strptime(final_we, "%d %b %Y")
        except: dt_obj = None
        
        m_sel = {}
        if dt_obj:
            expected = [str((dt_obj - timedelta(days=6-i)).day) for i in range(7) if (dt_obj - timedelta(days=6-i)).weekday() < 5]
            found = up["df"]["Date Num"].unique().tolist()
            missing = [d for d in expected if d not in found]
            if missing or up["df"].empty:
                st.warning("Missing Day Resolution Required:")
                cols = st.columns(len(missing) if missing else 1)
                if up["df"].empty: # Full week blank
                    all_wk = st.selectbox("Full Week Reason:", ["Annual Leave", "Sick", "Unpaid Leave"], key=f"fw_{sel}")
                    for d in expected: m_sel[d] = all_wk
                else:
                    for idx, d in enumerate(missing):
                        m_sel[d] = cols[idx].selectbox(f"Day {d}:", ["Ignore", "Annual Leave", "Sick"], key=f"ms_{sel}_{d}")

        st.data_editor(up["df"], num_rows="dynamic", use_container_width=True, key=f"ed_{sel}")
        all_uploads[sel].update({"m_sel": m_sel, "final_we": final_we, "final_wk": final_wk, "dt_obj": dt_obj})

with t2:
    st.markdown("### 💷 Database Synchronization")
    if all_uploads:
        if st.button("🚀 SYNC ALL TO DATABASE", type="primary"):
            for fn, data in all_uploads.items():
                p = process_timesheet_data(data["df"], data["dt_obj"], data.get("m_sel"), st.session_state.saved_contract)
                std, ot, dt, leave = 0.0, 0.0, 0.0, []
                for r in p:
                    tot = r['work'] + r['travel']
                    is_sun = False
                    try: 
                        if datetime.strptime(r['full_date'], "%Y-%m-%d").weekday() == 6: is_sun = True
                    except: pass
                    if is_sun: dt += tot
                    else: std += tot
                    if any(x in str(r['site']) for x in ["ANNUAL", "SICK"]): leave.append(f"{r['full_date']}:{r['site']}")
                
                st.session_state.user_db["weeks"][data["final_we"]] = {"wk": data["final_wk"], "std": min(std, st.session_state.saved_contract), "ot": max(0, std - st.session_state.saved_contract), "dt": dt, "leave": leave}
            st.success("Synced!")

    # ARREARS CALC
    db = st.session_state.user_db["weeks"]
    if db:
        st.dataframe(pd.DataFrame.from_dict(db, orient="index"), use_container_width=True)

with t3:
    st.markdown("### 🤒 Bradford Factor Tracking")
    sicks = []
    for we, d in st.session_state.user_db["weeks"].items():
        for l in d.get("leave", []):
            if "SICK" in l: sicks.append(l.split(":")[0])
    if sicks:
        st.write("Sick Dates:", sorted(sicks, reverse=True))
        st.metric("Bradford Score", (1**2)*len(sicks)) # Placeholder logic
    else: st.info("No sickness recorded.")

with t4:
    st.markdown("### 🏖️ Annual Leave Tracker")
    taken = sum(1 for we, d in st.session_state.user_db["weeks"].items() for l in d.get("leave", []) if "ANNUAL" in l)
    limit = 31 + (5 if st.session_state.saved_service_5yr else 0)
    st.metric("Days Remaining", limit - taken)

with t5:
    st.markdown("### 💾 Export")
    out = json.dumps({"name": st.session_state.saved_engineer, "rate": st.session_state.saved_rate, "contract": st.session_state.saved_contract, "service_5yr": st.session_state.saved_service_5yr, "weeks": st.session_state.user_db["weeks"]}, indent=4)
    st.download_button("Download JSON", out, file_name=f"{st.session_state.saved_engineer}_Data.json")
