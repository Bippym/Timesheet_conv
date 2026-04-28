import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re
import plotly.express as px
import zipfile
import io
import json

st.set_page_config(page_title="Network Engineer Portal", layout="wide")

# --- INITIAL SESSION STATE ---
if "saved_contract" not in st.session_state: st.session_state.saved_contract = 40
if "saved_rate" not in st.session_state: st.session_state.saved_rate = 0.00
if "saved_engineer" not in st.session_state: st.session_state.saved_engineer = ""
if "saved_service_5yr" not in st.session_state: st.session_state.saved_service_5yr = False
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 
if "user_db" not in st.session_state: st.session_state.user_db = {"weeks": {}}

# --- UTILITY FUNCTIONS ---
def fix_time_string(current_time, previous_time):
    if not current_time or not previous_time or current_time == "" or previous_time == "": return current_time
    try:
        pt_str = "0" + previous_time if len(previous_time.split(":")[0]) == 1 else previous_time
        ct_str = "0" + current_time if len(current_time.split(":")[0]) == 1 else current_time
        pt = datetime.strptime(pt_str, "%H:%M")
        ct = datetime.strptime(ct_str, "%H:%M")
        if ct < pt and ct.hour < 10:
            new_hour = ct.hour + 10
            if new_hour < 24:
                new_ct = ct.replace(hour=new_hour)
                if new_ct >= pt: return new_ct.strftime("%H:%M").lstrip("0") if new_ct.hour < 10 else new_ct.strftime("%H:%M")
    except: pass
    return current_time

def calc_hours(start_str, end_str):
    if not start_str or not end_str or pd.isna(start_str) or pd.isna(end_str): return 0.0
    start_str, end_str = str(start_str).strip(), str(end_str).strip()
    if not start_str or not end_str: return 0.0
    fmt = "%H:%M"
    try:
        if len(start_str.split(":")[0]) == 1: start_str = "0" + start_str
        if len(end_str.split(":")[0]) == 1: end_str = "0" + end_str
        tdelta = datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)
        if tdelta.days < 0:
            hrs = (timedelta(days=1) + tdelta).total_seconds() / 3600
            if hrs > 10: return round(hrs - 14, 2) 
            return round(hrs, 2)
        return round(tdelta.total_seconds() / 3600, 2)
    except: return 0.0

def process_timesheet_data(df, end_date_obj=None, missing_weekdays=None, missing_selections=None, contract_hours=40):
    processed_data = []
    pending_break_mins = 0
    if not df.empty:
        for index, row in df.iterrows():
            date_num, site, arrived, left, began = str(row["Date Num"]), str(row["Site & Ref No."]), str(row["Arrived On Site"]), str(row["Left Site"]), str(row["Began Journey"])
            is_break = "BREAK" in site.upper()
            if not is_break and len(processed_data) > 0:
                prev_left, prev_date = processed_data[-1]["left"], processed_data[-1]["date"]
                if date_num == prev_date and prev_left != "" and pending_break_mins == 0: began = prev_left
            if len(processed_data) > 0: began = fix_time_string(began, processed_data[-1]["left"])
            arrived = fix_time_string(arrived, began)
            left = fix_time_string(left, arrived)
            if is_break:
                b_mins = round(calc_hours(arrived, left) * 60)
                if b_mins == 0: b_mins = round(calc_hours(began, left) * 60) 
                if b_mins == 0: b_mins = round(calc_hours(began, arrived) * 60) 
                pending_break_mins += b_mins
                continue 
            if began == "" and len(processed_data) > 0:
                if date_num == processed_data[-1]["date"]: began = processed_data[-1]["left"]
            travel_time, work_time = calc_hours(began, arrived), calc_hours(arrived, left)
            rest_break_display = ""
            if pending_break_mins > 0:
                rest_break_display = str(pending_break_mins)
                travel_time = max(0.0, travel_time - (pending_break_mins / 60.0))
                pending_break_mins = 0 
            
            full_date_str = ""
            if end_date_obj and date_num:
                try:
                    for i in range(7):
                        curr = end_date_obj - timedelta(days=6-i)
                        if str(curr.day) == str(date_num):
                            full_date_str = curr.strftime("%Y-%m-%d")
                            break
                except: pass

            processed_data.append({"date": date_num, "full_date": full_date_str, "site": site, "began": began, "arrived": arrived, "left": left, "work": work_time, "travel": travel_time, "rest_break": rest_break_display})
        
    if missing_weekdays and missing_selections:
        daily_hrs = contract_hours / 5.0
        for d_num, d_name in missing_weekdays:
            reason = missing_selections.get(d_num, "Ignore")
            if reason != "Ignore":
                hrs = daily_hrs if reason == "Annual Leave" else 0.0
                full_date_str = ""
                if end_date_obj:
                    try:
                        for i in range(7):
                            curr = end_date_obj - timedelta(days=6-i)
                            if str(curr.day) == str(d_num):
                                full_date_str = curr.strftime("%Y-%m-%d")
                                break
                    except: pass
                processed_data.append({"date": d_num, "full_date": full_date_str, "site": reason.upper(), "began": "", "arrived": "", "left": "", "work": hrs, "travel": 0.0, "rest_break": ""})
                
    if end_date_obj:
        def get_sort_date(d_num):
            for i in range(7):
                curr = end_date_obj - timedelta(days=i)
                if str(curr.day) == str(d_num): return curr
            return end_date_obj
        processed_data.sort(key=lambda x: get_sort_date(x["date"]))
    return processed_data

def generate_pdf_html(df_processed, engineer, week_end_date, week_number, on_call):
    html_content = f"""
    <!DOCTYPE html><html><head><style>
        body {{ font-family: Arial, sans-serif; font-size: 8pt; margin: 0; padding: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 15px; border-bottom: 1.5px solid #000; padding-bottom: 10px; }}
        .header div {{ flex: 1; }} .header-center {{ text-align: center; }} .header-right {{ text-align: right; }}
        table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
        th, td {{ border: 1px solid #000; padding: 4px; text-align: center; }}
        th {{ background-color: #f2f2f2; font-size: 7pt; height: 35px; }}
        .day-row {{ background-color: #ddd; font-weight: bold; text-align: left; padding-left: 10px; }}
        .total-row td {{ background-color: #eef2f5; font-weight: bold; border-top: 1.5px solid #000; }}
    </style></head><body>
        <div class="header">
            <div class="header-left"><strong>Engineer:</strong> {engineer}<br><strong>Network (Catering Engineers) Ltd</strong></div>
            <div class="header-center"><strong>Week End Date:</strong> {week_end_date}<br><strong>Week:</strong> {week_number}</div>
            <div class="header-right"><strong>On-call:</strong> {on_call}</div>
        </div>
        <table>
            <thead><tr><th style="width: 22%;">Site & Ref No.</th><th>Multiple Jobs</th><th>Job Number</th><th>Began Journey</th><th>Arrived On Site</th><th>Left Site</th><th>Hours Worked</th><th>Rest Break (min)</th><th>Travel Time</th><th>TOTAL Hours</th></tr></thead>
            <tbody>
    """
    df_p = pd.DataFrame(df_processed)
    grand_total = 0
    if not df_p.empty:
        for date, group in df_p.groupby("date", sort=False):
            try:
                end_date_obj_html = datetime.strptime(week_end_date, "%d %b %Y")
                day_str = f"Date: {date}"
                for i in range(7):
                    curr = end_date_obj_html - timedelta(days=6-i)
                    if str(curr.day) == str(date):
                        suffix = 'th' if 11 <= curr.day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(curr.day % 10, 'th')
                        day_str = curr.strftime(f"%A {curr.day}{suffix} %B")
                        break
            except: day_str = f"Date: {date}"
            html_content += f'<tr><td colspan="10" class="day-row">{day_str}</td></tr>'
            day_total = 0
            for _, row in group.iterrows():
                row_total = row['work'] + row['travel']
                html_content += f"<tr><td>{row['site']}</td><td></td><td></td><td>{row['began']}</td><td>{row['arrived']}</td><td>{row['left']}</td><td>{row['work']:.2f}</td><td>{row['rest_break']}</td><td>{row['travel']:.2f}</td><td>{row_total:.2f}</td></tr>"
                day_total += row_total
            grand_total += day_total
            html_content += f'<tr class="total-row"><td colspan="9" style="text-align: right;"><strong>Daily Total:</strong></td><td><strong>{day_total:.2f}</strong></td></tr>'
    html_content += f"</tbody></table><div style='margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px;'>Weekly Total Hours: {grand_total:.2f}</div></body></html>"
    return html_content

# --- MAIN APP LAYOUT ---
with st.sidebar:
    st.header("⚙️ System Control")
    if st.button("🗑️ Reset All Data (JSON & PDFs)", type="primary"):
        st.session_state.uploader_key += 1
        st.session_state.user_db = {"weeks": {}}
        st.session_state.saved_engineer = ""
        st.rerun()
    st.markdown("---")
    debug_mode = st.checkbox("Enable Engineer Debug Mode")

st.title("Network Engineer Portal")

# --- CORE PROFILE & JSON LOADING (FRONT & CENTRE) ---
with st.expander("👤 Step 1: Establish Your Profile & Database", expanded=True if not st.session_state.saved_engineer else False):
    c1, c2 = st.columns([2, 1])
    with c1:
        db_file = st.file_uploader("📂 Drop your Local Database (.json) here to restore your history", type=["json"], key=f"db_{st.session_state.uploader_key}")
        if db_file:
            try:
                data = json.loads(db_file.getvalue().decode("utf-8"))
                st.session_state.saved_engineer = data.get("name", "")
                st.session_state.saved_rate = float(data.get("rate", 0.0))
                st.session_state.saved_contract = int(data.get("contract", 40))
                st.session_state.saved_service_5yr = bool(data.get("service_5yr", False))
                st.session_state.user_db["weeks"] = data.get("weeks", {})
                st.success("Welcome back! Database restored.")
            except: st.error("Error reading JSON.")

    with c2:
        st.session_state.saved_engineer = st.text_input("Full Name", value=st.session_state.saved_engineer)
        st.session_state.saved_rate = st.number_input("Hourly Rate (£)", value=st.session_state.saved_rate, step=0.5)
        st.session_state.saved_contract = st.selectbox("Contract Hours", [40, 45], index=0 if st.session_state.saved_contract == 40 else 1)
        st.session_state.saved_service_5yr = st.checkbox("> 5 Years Service", value=st.session_state.saved_service_5yr)

# BLOCKER: Critical info check
if not st.session_state.saved_engineer or st.session_state.saved_rate == 0:
    st.warning("Please provide your Name and Hourly Rate in Step 1 above to unlock timesheet tools.")
    st.stop()

# --- TABBED INTERFACE ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📑 Timesheet Editor", "💷 Salary & Arrears", "🤒 Sickness Tracker", "🏖️ Annual Leave", "💾 Database Backup"])

# --- PDF UPLOADER & EXTRACTION (GLOBAL) ---
uploaded_files = st.file_uploader("Upload Work-Style Timesheets (PDF)", type=["pdf"], accept_multiple_files=True, key=f"pdfs_{st.session_state.uploader_key}")
all_extracted_datasets = {}

if uploaded_files:
    for f in uploaded_files:
        extracted = []
        we_str, wk_num, raw_dump = "", "", ""
        with pdfplumber.open(f) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                raw_dump += text + "\n"
                m_we = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
                if m_we: we_str = m_we.group(1)
                m_wk = re.search(r"Week:\s*(\d+)", text)
                if m_wk: wk_num = m_wk.group(1)
                for line in text.split('\n'):
                    raw_times = re.findall(r'[0-9Oo]{1,2}:[0-9Oo]{2}', line)
                    if len(raw_times) >= 1: 
                        first_idx = line.find(raw_times[0])
                        raw_site = line[:first_idx].strip()
                        date_match = re.search(r"^([A-Za-z]{1,3}\s*)?(\d{1,2})\s+", raw_site)
                        date_n = date_match.group(2) if date_match else ""
                        site_c = re.split(r"\*+QUO|\*?QUOTE|£|R1 OA|\b[A-Z0-9]{3,}:", raw_site, flags=re.IGNORECASE)[0].strip()
                        site_c = re.sub(r"^([A-Za-z]{1,3}\s*)?\d{1,2}\s+", "", site_c)
                        times = [t.replace('O','0').replace('o','0') for t in raw_times]
                        if len(times) >= 3: beg, arr, lft = times[0], times[1], times[2]
                        elif len(times) == 2:
                            if "HOME" in site_c.upper(): beg, arr, lft = times[0], times[1], ""
                            else: beg, arr, lft = "", times[0], times[1]
                        else: beg, arr, lft = "", times[0], ""
                        extracted.append({"Date Num": date_n, "Site & Ref No.": site_c, "Began Journey": beg, "Arrived On Site": arr, "Left Site": lft, "Original Row Info": line})
        
        try: dt_obj = datetime.strptime(we_str, "%d %b %Y")
        except: dt_obj = None
        
        all_extracted_datasets[f.name] = {
            "we": we_str, "wk": wk_num, "dt": dt_obj, "df": pd.DataFrame(extracted), "raw": raw_dump, "missing": {}
        }

# --- TAB 1: EDITOR ---
with tab1:
    if not all_extracted_datasets:
        st.info("Upload Timesheet PDFs to begin.")
    else:
        sel_f = st.selectbox("Select timesheet to edit:", list(all_extracted_datasets.keys()))
        data = all_extracted_datasets[sel_f]
        
        e1, e2 = st.columns(2)
        final_we = e1.text_input("Week End Date", value=data["we"], key=f"we_in_{sel_f}")
        final_wk = e2.text_input("Week Number", value=data["wk"], key=f"wk_in_{sel_f}")
        
        try: end_dt = datetime.strptime(final_we, "%d %b %Y")
        except: end_dt = None
        
        # Internal Missing Days Logic
        missing_selections = {}
        if end_dt:
            expected = [(str((end_dt - timedelta(days=6-i)).day), (end_dt - timedelta(days=6-i)).strftime("%A")) for i in range(7) if (end_dt - timedelta(days=6-i)).weekday() < 5]
            found = data["df"]["Date Num"].dropna().unique().tolist()
            missing = [d for d in expected if d[0] not in found]
            if missing:
                st.warning("Missing Weekdays Detected")
                cols = st.columns(len(missing))
                for idx, (dn, dname) in enumerate(missing):
                    missing_selections[dn] = cols[idx].selectbox(f"{dname} ({dn})", ["Ignore", "Annual Leave", "Sick", "Unpaid Leave"], key=f"m_{sel_f}_{dn}")

        edited_df = st.data_editor(data["df"], num_rows="dynamic", use_container_width=True, key=f"ed_{sel_f}")
        
        # Disable print if dates missing
        btn_disabled = True if not final_we or not final_wk else False
        if st.button("Generate & Download PDF", type="primary", disabled=btn_disabled):
            proc = process_timesheet_data(edited_df, end_dt, missing if end_dt else None, missing_selections, st.session_state.saved_contract)
            on_call = "Yes" if any(re.match(r"^(S|SA|SAT|SU|SUN)\s*\d{1,2}", str(info).upper()) for info in edited_df["Original Row Info"]) else "No"
            html = generate_pdf_html(proc, st.session_state.saved_engineer, final_we, final_wk, on_call)
            st.download_button("⬇️ Download PDF", HTML(string=html).write_pdf(), file_name=f"{st.session_state.saved_engineer}_{final_wk}.pdf")

# --- TAB 2: SALARY ---
with tab3: # Using Tab numbers from User request logic
    st.markdown("### 💷 Salary Arrears & Tax Year Tracker")
    annual_b = st.session_state.saved_rate * st.session_state.saved_contract * 52
    st.metric("Annual Basic Pay", f"£{annual_b:,.2f}")
    
    if all_extracted_datasets:
        if st.button("💾 Save All Uploaded Weeks to Database", type="primary"):
            for fn, d in all_extracted_datasets.items():
                # Automatic processing for DB
                p_data = process_timesheet_data(d["df"], d["dt"], None, None, st.session_state.saved_contract)
                std, ot, dt_hrs = 0.0, 0.0, 0.0
                lv_days = []
                for row in p_data:
                    day_tot = row['work'] + row['travel']
                    is_sunday = False
                    try:
                        if datetime.strptime(row['full_date'], "%Y-%m-%d").weekday() == 6: is_sunday = True
                    except: pass
                    if is_sunday: dt_hrs += day_tot
                    else: std += day_tot
                    
                    s_up = str(row['site']).upper()
                    if any(x in s_up for x in ["ANNUAL LEAVE", "SICK", "UNPAID"]):
                        lv_days.append(f"{row['full_date']}:{s_up}")
                
                st.session_state.user_db["weeks"][d["we"]] = {
                    "wk": d["wk"], "std": min(std, st.session_state.saved_contract), 
                    "ot": max(0, std - st.session_state.saved_contract), "dt": dt_hrs, "leave": lv_days
                }
            st.success("Database Updated.")

# --- TAB 3: SICKNESS ---
with tab3:
    st.markdown("### 🤒 Sickness & Bradford Factor")
    sick_ledger = []
    for we, d in st.session_state.user_db["weeks"].items():
        for l in d.get("leave", []):
            if "SICK" in l:
                dt, rsn = l.split(":")
                sick_ledger.append({"Date": dt, "Reason": rsn})
    
    if sick_ledger:
        sdf = pd.DataFrame(sick_ledger).sort_values("Date", ascending=False)
        st.table(sdf)
        # BF Calc Logic (Simplified Cluster)
        total_days = len(sick_ledger)
        spells = 0
        if total_days > 0:
            spells = 1
            dates = sorted([datetime.strptime(x['Date'], "%Y-%m-%d") for x in sick_ledger])
            for i in range(1, len(dates)):
                if (dates[i] - dates[i-1]).days > 3: spells += 1
        score = (spells**2) * total_days
        st.metric("Bradford Factor Score", score)
    else: st.info("No sickness recorded.")

# --- TAB 4: ANNUAL LEAVE ---
with tab4:
    st.markdown("### 🏖️ Annual Leave Tracker (Apr-Mar)")
    total_al = 31 + (5 if st.session_state.saved_service_5yr else 0)
    al_taken = 0
    for we, d in st.session_state.user_db["weeks"].items():
        for l in d.get("leave", []):
            if "ANNUAL LEAVE" in l: al_taken += 1
    
    st.metric("Remaining Annual Leave", f"{total_al - al_taken} Days")
    st.progress(max(0, min(1.0, (total_al - al_taken)/total_al)))

# --- TAB 5: BACKUP ---
with tab5:
    st.markdown("### 💾 Data Persistence")
    db_json = json.dumps({
        "name": st.session_state.saved_engineer,
        "rate": st.session_state.saved_rate,
        "contract": st.session_state.saved_contract,
        "service_5yr": st.session_state.saved_service_5yr,
        "weeks": st.session_state.user_db["weeks"]
    }, indent=4)
    st.download_button("📦 Download Local Backup (.json)", db_json, file_name=f"{st.session_state.saved_engineer}_Data.json")
