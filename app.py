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

def get_suffix(day):
    if 11 <= day <= 13: return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')

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

# --- PDF GLOBAL EXTRACTION ---
uploaded_pdfs = st.file_uploader("Upload PDF Timesheets", type=["pdf"], accept_multiple_files=True, key=f"pdf_up_{st.session_state.uploader_key}")
all_uploads = {}
global_missing_files = []

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
        
        # Blank File Week Triangulation
        if not rows:
            fm = re.search(r"[Ww]eek[_\s]*(\d+)", f.name)
            wk = fm.group(1) if fm else wk

        dt_obj = None
        if we:
            try: dt_obj = datetime.strptime(we, "%d %b %Y")
            except: pass
        
        # CRITICAL: Missing Day Scanner
        missing_count = 0
        if dt_obj:
            expected = [str((dt_obj - timedelta(days=6-i)).day) for i in range(7) if (dt_obj - timedelta(days=6-i)).weekday() < 5]
            found = [str(x["Date Num"]) for x in rows if x["Date Num"]]
            if not all(d in found for d in expected) or not rows:
                global_missing_files.append(f"{f.name} (Week Ending: {we if we else 'Unknown'})")
                missing_count = 1

        all_uploads[f.name] = {"we": we, "wk": wk, "dt_obj": dt_obj, "df": pd.DataFrame(rows), "m_sel": {}, "is_missing": missing_count > 0}

# --- GLOBAL STATUS BOX ---
if global_missing_files:
    st.error(f"🚨 **Action Required!** Missing data detected in {len(global_missing_files)} file(s). You must resolve these in the **Editor** tab before syncing:\n\n* " + "\n* ".join(global_missing_files))
elif uploaded_pdfs:
    st.success("✅ All uploaded timesheets are complete and ready for sync.")

# --- TABS ---
t1, t2, t3, t4, t5 = st.tabs(["📑 Editor", "💷 Sync & Arrears", "🤒 Sickness", "🏖️ Annual Leave", "💾 Backup"])

with t1:
    if not all_uploads: st.info("Upload PDFs to begin.")
    else:
        sel = st.selectbox("Select Timesheet to Verify:", list(all_uploads.keys()))
        up = all_uploads[sel]
        
        final_we = st.text_input("Week End Date", value=up["we"], key=f"we_{sel}")
        final_wk = st.text_input("Week No", value=up["wk"], key=f"wk_{sel}")
        
        try: end_dt = datetime.strptime(final_we, "%d %b %Y")
        except: end_dt = None
        
        m_sel = {}
        if end_dt:
            expected_days = []
            for i in range(7):
                curr = end_dt - timedelta(days=6-i)
                if curr.weekday() < 5:
                    day_label = f"{curr.strftime('%A')} {curr.day}{get_suffix(curr.day)} {curr.strftime('%B %Y')}"
                    expected_days.append((str(curr.day), day_label))

            found = up["df"]["Date Num"].unique().tolist()
            missing = [d for d in expected_days if d[0] not in found]
            
            if missing or up["df"].empty:
                st.warning("⚠️ Manual Resolution Required for this week:")
                if up["df"].empty:
                    all_wk = st.selectbox("This timesheet is blank. Reason for absence:", ["Annual Leave", "Sick", "Unpaid Leave"], key=f"fw_{sel}")
                    for d in expected_days: m_sel[d[0]] = all_wk
                else:
                    cols = st.columns(len(missing))
                    for idx, (d_num, d_full) in enumerate(missing):
                        m_sel[d_num] = cols[idx].selectbox(f"{d_full}", ["Ignore", "Annual Leave", "Sick"], key=f"ms_{sel}_{d_num}")

        st.data_editor(up["df"], num_rows="dynamic", use_container_width=True, key=f"ed_{sel}")
        all_uploads[sel].update({"m_sel": m_sel, "final_we": final_we, "final_wk": final_wk, "dt_obj": end_dt})

with t2:
    st.markdown("### 💷 Database Sync")
    if all_uploads:
        if st.button("🚀 PROCESS & MERGE ALL TO JSON", type="primary"):
            for fn, data in all_uploads.items():
                p = process_timesheet_data(data["df"], data["dt_obj"], data.get("m_sel"), st.session_state.saved_contract)
                std, ot, dt, leave = 0.0, 0.0, 0.0, []
                for r in p:
                    tot = r['work'] + r['travel']
                    is_sun = False
                    try: 
                        if r['full_date'] and datetime.strptime(r['full_date'], "%Y-%m-%d").weekday() == 6: is_sun = True
                    except: pass
                    if is_sun: dt += tot
                    else: std += tot
                    if "ANNUAL" in str(r['site']): leave.append(f"{r['full_date']}:ANNUAL LEAVE")
                    if "SICK" in str(r['site']): leave.append(f"{r['full_date']}:SICK")
                
                st.session_state.user_db["weeks"][data["final_we"]] = {"wk": data["final_wk"], "std": min(std, st.session_state.saved_contract), "ot": max(0, std - st.session_state.saved_contract), "dt": dt, "leave": leave}
            st.success("Database Updated Successfully!")

    if st.session_state.user_db["weeks"]:
        st.write("Historical Data in JSON:")
        st.dataframe(pd.DataFrame.from_dict(st.session_state.user_db["weeks"], orient="index"), use_container_width=True)

with t3:
    st.markdown("### 🤒 Sickness Tracker")
    sicks = []
    for we, d in st.session_state.user_db["weeks"].items():
        for l in d.get("leave", []):
            if "SICK" in l: sicks.append(l.split(":")[0])
    if sicks:
        st.write("Sick Dates:", sorted(sicks, reverse=True))
    else: st.info("No sickness recorded in database.")

with t4:
    st.markdown("### 🏖️ Annual Leave Tracker")
    taken = sum(1 for we, d in st.session_state.user_db["weeks"].items() for l in d.get("leave", []) if "ANNUAL" in l)
    limit = 31 + (5 if st.session_state.saved_service_5yr else 0)
    st.metric("Annual Leave Days Taken", taken)
    st.metric("Days Remaining", limit - taken)

with t5:
    st.markdown("### 💾 Export")
    out = json.dumps({"name": st.session_state.saved_engineer, "rate": st.session_state.saved_rate, "contract": st.session_state.saved_contract, "service_5yr": st.session_state.saved_service_5yr, "weeks": st.session_state.user_db["weeks"]}, indent=4)
    st.download_button("📦 Download Database JSON", out, file_name=f"{st.session_state.saved_engineer}_Data.json")
