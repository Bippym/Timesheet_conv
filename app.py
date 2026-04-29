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
    st.error("🚨 Missing GEMINI_API_KEY! Please add it to your .streamlit/secrets.toml file.")
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
    fmt = "%H:%M"
    try:
        t1 = datetime.strptime(start_str.rjust(5, '0'), fmt)
        t2 = datetime.strptime(end_str.rjust(5, '0'), fmt)
        tdelta = t2 - t1
        hrs = tdelta.total_seconds() / 3600
        if hrs < 0: hrs += 24 # Handle overnight shifts
        if hrs > 12: hrs -= 14 # Safety catch for deep overnight issues
        return round(hrs, 2)
    except: return 0.0

def process_timesheet_data(df, end_date_obj=None, missing_selections=None, contract_hours=40):
    processed_data = []
    pending_break_mins = 0
    
    for index, row in df.iterrows():
        dn = str(row.get("Date Num", ""))
        site = str(row.get("Site & Ref No.", ""))
        beg = str(row.get("Began Journey", ""))
        arr = str(row.get("Arrived On Site", ""))
        lft = str(row.get("Left Site", ""))
        
        if not beg and not arr and not lft: continue

        # Continuity Auto-Snap for blanks (e.g. breaks)
        if len(processed_data) > 0:
            prev_left = processed_data[-1]["left"]
            prev_date = processed_data[-1]["date"]
            if dn == prev_date and prev_left != "" and pending_break_mins == 0:
                if beg == "": beg = prev_left

        is_break = "BREAK" in site.upper()
        if is_break:
            b_mins = round(calc_hours(arr, lft) * 60)
            if b_mins == 0: b_mins = round(calc_hours(beg, lft) * 60) 
            if b_mins == 0: b_mins = round(calc_hours(beg, arr) * 60) 
            pending_break_mins += b_mins
            continue
            
        if (beg == "" or pending_break_mins > 0) and len(processed_data) > 0: 
            if processed_data[-1]["date"] == dn:
                beg = processed_data[-1]["left"]

        work, travel = calc_hours(arr, lft), calc_hours(beg, arr)
        
        rest_display = ""
        if pending_break_mins > 0:
            rest_display = str(pending_break_mins)
            travel = max(0.0, travel - (pending_break_mins / 60.0))
            pending_break_mins = 0
            
        f_date = ""
        if end_date_obj and dn:
            for i in range(7):
                curr = end_date_obj - timedelta(days=6-i)
                if str(curr.day) == dn:
                    f_date = curr.strftime("%Y-%m-%d")
                    break
                    
        processed_data.append({"date": dn, "full_date": f_date, "site": site, "began": beg, "arrived": arr, "left": lft, "work": work, "travel": travel, "rest_break": rest_display})
    
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
            processed_data.append({"date": str(d_num), "full_date": f_date, "site": reason.upper(), "began": "", "arrived": "", "left": "", "work": daily if reason == "Annual Leave" else 0.0, "travel": 0.0, "rest_break": ""})
            
    processed_data.sort(key=lambda x: int(x["date"]) if x["date"].isdigit() else 99)
    return processed_data

def generate_pdf_html(df_processed, engineer, week_end_date, week_number, on_call):
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body {{ font-family: Arial, sans-serif; font-size: 8pt; margin: 0; padding: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 15px; border-bottom: 1.5px solid #000; padding-bottom: 10px; }}
        .header div {{ flex: 1; }}
        .header-center {{ text-align: center; }}
        .header-right {{ text-align: right; }}
        table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
        th, td {{ border: 1px solid #000; padding: 4px; text-align: center; word-wrap: break-word; }}
        th {{ background-color: #f2f2f2; font-size: 7pt; height: 35px; text-transform: uppercase; }}
        .day-row {{ background-color: #ddd; font-weight: bold; text-align: left; padding-left: 10px; font-size: 9pt; }}
        .total-row td {{ background-color: #eef2f5; font-weight: bold; border-top: 1.5px solid #000; }}
        .site-col {{ width: 22%; text-align: left; font-weight: bold; }}
    </style>
    </head>
    <body>
        <div class="header">
            <div class="header-left"><strong>Engineer:</strong> {engineer}<br><strong>Network (Catering Engineers) Ltd</strong></div>
            <div class="header-center"><strong>Week End Date:</strong> {week_end_date}<br><strong>Week:</strong> {week_number}</div>
            <div class="header-right"><strong>On-call:</strong> {on_call}</div>
        </div>
        <table>
            <thead><tr><th class="site-col">Site & Ref No.</th><th>Multiple Jobs</th><th>Job Number</th><th>Began Journey</th><th>Arrived On Site</th><th>Left Site</th><th>Hours Worked</th><th>Rest Break (min)</th><th>Travel Time</th><th>TOTAL Hours</th></tr></thead>
            <tbody>
    """
    df_p = pd.DataFrame(df_processed)
    grand_total = 0
    
    if not df_p.empty:
        for date_val, group in df_p.groupby("date", sort=False):
            day_header = f"Date: {date_val}"
            try:
                fd = group.iloc[0].get("full_date", "")
                if fd:
                    dt = datetime.strptime(fd, "%Y-%m-%d")
                    day_header = f"{dt.strftime('%A')} {dt.day}{get_suffix(dt.day)} {dt.strftime('%B')}"
            except: pass
            
            html_content += f'<tr><td colspan="10" class="day-row">{day_header}</td></tr>'
            day_total = 0
            
            for _, row in group.iterrows():
                work, travel = row.get('work', 0.0), row.get('travel', 0.0)
                row_total = work + travel
                day_total += row_total
                html_content += f"""
                <tr>
                    <td class="site-col">{str(row.get('site','')).upper()}</td>
                    <td></td><td></td>
                    <td>{row.get('began','')}</td>
                    <td>{row.get('arrived','')}</td>
                    <td>{row.get('left','')}</td>
                    <td>{work:.2f}</td>
                    <td>{row.get('rest_break','')}</td>
                    <td>{travel:.2f}</td>
                    <td>{row_total:.2f}</td>
                </tr>"""
                
            grand_total += day_total
            html_content += f'<tr class="total-row"><td colspan="9" style="text-align: right;"><strong>Daily Total:</strong></td><td><strong>{day_total:.2f}</strong></td></tr>'
            
    html_content += f"</tbody></table><div style='margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px; font-size:10pt;'>WEEKLY TOTAL HOURS: {grand_total:.2f}</div></body></html>"
    return html_content


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

uploaded_pdfs = st.file_uploader("Upload PDF Timesheets", type=["pdf"], accept_multiple_files=True, key=f"pdf_up_{st.session_state.uploader_key}")
all_uploads = {}
global_missing_files = []

if uploaded_pdfs:
    with st.spinner("AI is analyzing and extracting data from your PDFs..."):
        for idx, f in enumerate(uploaded_pdfs):
            
            # Use cached extraction if we already processed this file
            if f.name in st.session_state.extracted_files_cache:
                all_uploads[f.name] = st.session_state.extracted_files_cache[f.name]
                continue
                
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                temp_pdf.write(f.getvalue())
                temp_path = temp_pdf.name
                
            try:
                # Send strictly to AI
                gemini_file = genai.upload_file(temp_path, mime_type="application/pdf")
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = """
                You are a strict data extraction tool. Read this timesheet PDF.
                Extract the data and output ONLY a raw JSON object (NO markdown tags, NO backticks).
                
                Format requirements:
                {
                  "Week End Date": "Extract the 'Week Ending' date (e.g. '1 Mar 2026'). Blank if not found.",
                  "Week Number": "Extract the 'Week:' number (e.g. '9'). Blank if not found.",
                  "Rows": [
                    {
                      "Date Num": "The numeric date only (e.g., '23'). Ignore the day letter.",
                      "Site & Ref No.": "Cleaned site name. Remove any text involving '**QUOTE**' or monetary amounts like 'OVER £1000'. Ignore the summary calculation rows at the bottom.",
                      "Began Journey": "HH:MM format. Fix obvious OCR time typos using common sense. Leave blank '' if empty.",
                      "Arrived On Site": "HH:MM format. Leave blank '' if empty.",
                      "Left Site": "HH:MM format. Leave blank '' if empty."
                    }
                  ]
                }
                """
                
                response = model.generate_content([gemini_file, prompt])
                
                # Parse AI response
                json_text = response.text.strip().replace("```json", "").replace("```", "")
                ai_data = json.loads(json_text)
                
                we = ai_data.get("Week End Date", "")
                wk = ai_data.get("Week Number", "")
                df_file = pd.DataFrame(ai_data.get("Rows", []))
                
                # Cleanup dataframe
                if df_file.empty: 
                    df_file = pd.DataFrame(columns=TS_COLS)
                else:
                    # Enforce correct column names just in case
                    df_file = df_file.reindex(columns=TS_COLS)
                    
                dt_obj = None
                try: dt_obj = datetime.strptime(we, "%d %b %Y")
                except: pass
                
                upload_data = {"we": we, "wk": wk, "dt_obj": dt_obj, "df": df_file, "idx": idx}
                
                # Save to cache so re-runs don't hit the API again
                st.session_state.extracted_files_cache[f.name] = upload_data
                all_uploads[f.name] = upload_data
                
            except Exception as e:
                st.error(f"Failed to process {f.name}: {e}")
            finally:
                os.remove(temp_path)

# --- Check for Missing Days ---
for f_name, data in all_uploads.items():
    if data["dt_obj"]:
        expected = [str((data["dt_obj"] - timedelta(days=6-i)).day) for i in range(7) if (data["dt_obj"] - timedelta(days=6-i)).weekday() < 5]
        found = data["df"]["Date Num"].unique().tolist() if not data["df"].empty else []
        unresolved = [d for d in expected if d not in found]
        if (unresolved or data["df"].empty) and f_name not in st.session_state.resolutions:
            global_missing_files.append({"name": f_name, "we": data["we"], "index": data["idx"]})

if global_missing_files:
    st.error("🚨 **Action Required!** Missing days in some files. Click to resolve:")
    cols = st.columns(len(global_missing_files))
    for i, file_info in enumerate(global_missing_files):
        if cols[i].button(f"🛠️ Fix {file_info['name']}", key=f"jump_{file_info['name']}"):
            st.session_state.selected_file_index = file_info['index']
            st.rerun()

# --- TABS ---
t1, t2, t3, t4, t5 = st.tabs(["📑 Editor", "💷 Sync", "🤒 Sickness", "🏖️ Leave", "💾 Backup"])

with t1:
    if not all_uploads: 
        st.info("Upload PDFs.")
    else:
        file_list = list(all_uploads.keys())
        # Safety catch if selected index is out of bounds
        if st.session_state.selected_file_index >= len(file_list):
            st.session_state.selected_file_index = 0
            
        sel_name = st.selectbox("Select Timesheet:", file_list, index=st.session_state.selected_file_index)
        up = all_uploads[sel_name]
        
        c1, c2 = st.columns(2)
        with c1: f_we = st.text_input("Week End Date", value=up["we"], key=f"we_in_{sel_name}")
        with c2: f_wk = st.text_input("Week No", value=up["wk"], key=f"wk_in_{sel_name}")
        
        try: end_dt = datetime.strptime(f_we, "%d %b %Y")
        except: end_dt = None
        
        if end_dt:
            expected_days = []
            for i in range(7):
                curr = end_dt - timedelta(days=6-i)
                if curr.weekday() < 5:
                    day_label = f"{curr.strftime('%A')} {curr.day}{get_suffix(curr.day)} {curr.strftime('%B %Y')}"
                    expected_days.append((str(curr.day), day_label))

            found = up["df"]["Date Num"].unique().tolist() if "Date Num" in up["df"].columns else []
            missing = [d for d in expected_days if d[0] not in found]
            
            if missing or up["df"].empty:
                st.warning("⚠️ Manual Resolution Required for missing weekdays:")
                m_sel = {}
                if up["df"].empty:
                    all_wk = st.selectbox("Reason for absence:", ["Annual Leave", "Sick", "Unpaid Leave"], key=f"fw_{sel_name}")
                    for d in expected_days: m_sel[d[0]] = all_wk
                else:
                    cols = st.columns(len(missing))
                    for idx, (d_num, d_full) in enumerate(missing):
                        m_sel[d_num] = cols[idx].selectbox(f"{d_full}", ["Ignore", "Annual Leave", "Sick"], key=f"ms_{sel_name}_{d_num}")
                
                if st.button("✅ Save Resolution for this Week"):
                    st.session_state.resolutions[sel_name] = m_sel
                    st.success("Resolved!")
                    st.rerun()

        edited_df = st.data_editor(up["df"], num_rows="dynamic", use_container_width=True, key=f"ed_{sel_name}")

        if st.button("🖨️ Generate & Download Resolved PDF", type="primary"):
            res = st.session_state.resolutions.get(sel_name, {})
            proc = process_timesheet_data(edited_df, end_dt, res, st.session_state.saved_contract)
            
            # Check for weekends mathematically instead of using Regex
            has_weekend = False
            for r in proc:
                if r['full_date']:
                    try:
                        if datetime.strptime(r['full_date'], "%Y-%m-%d").weekday() >= 5:
                            has_weekend = True
                            break
                    except: pass
                    
            html = generate_pdf_html(proc, st.session_state.saved_engineer, f_we, f_wk, "Yes" if has_weekend else "No")
            st.download_button("⬇️ Download PDF", HTML(string=html).write_pdf(), file_name=f"{st.session_state.saved_engineer.replace(' ', '_')}_Timesheet_Week_{f_wk}.pdf")

with t2:
    if all_uploads:
        if st.button("🚀 SYNC ALL TO DATABASE", type="primary"):
            for fn, data in all_uploads.items():
                res = st.session_state.resolutions.get(fn, {})
                # Use the edited df if the user made changes in the UI, otherwise fallback to the cache
                current_df = st.session_state.get(f"ed_{fn}", data["df"])
                p = process_timesheet_data(current_df, data["dt_obj"], res, st.session_state.saved_contract)
                
                std, ot, dt, leave = 0.0, 0.0, 0.0, []
                for r in p:
                    tot = r['work'] + r['travel']
                    is_sun = False
                    try: 
                        if r['full_date'] and datetime.strptime(r['full_date'], "%Y-%m-%d").weekday() == 6: is_sun = True
                    except: pass
                    
                    if is_sun: dt += tot
                    else: std += tot
                    
                    if any(x in str(r['site']) for x in ["ANNUAL", "SICK"]): leave.append(f"{r['full_date']}:{r['site']}")
                    
                st.session_state.user_db["weeks"][fn] = {"std": min(std, st.session_state.saved_contract), "ot": max(0, std - st.session_state.saved_contract), "dt": dt, "leave": leave}
            st.success("Synced!")
            
    if st.session_state.user_db["weeks"]:
        st.dataframe(pd.DataFrame.from_dict(st.session_state.user_db["weeks"], orient="index"), use_container_width=True)

with t3:
    st.markdown("### 🤒 Sickness Tracker")
    sicks = [l.split(":")[0] for we, d in st.session_state.user_db["weeks"].items() for l in d.get("leave", []) if "SICK" in l]
    if sicks: st.write("Sick Dates:", sorted(sicks, reverse=True))
    else: st.info("No records.")

with t4:
    st.markdown("### 🏖️ Annual Leave")
    taken = sum(1 for we, d in st.session_state.user_db["weeks"].items() for l in d.get("leave", []) if "ANNUAL" in l)
    limit = 31 + (5 if st.session_state.saved_service_5yr else 0)
    st.metric("Remaining", limit - taken)

with t5:
    out = json.dumps({"name": st.session_state.saved_engineer, "rate": st.session_state.saved_rate, "contract": st.session_state.saved_contract, "service_5yr": st.session_state.saved_service_5yr, "weeks": st.session_state.user_db["weeks"]}, indent=4)
    st.download_button("📦 Download JSON", out, file_name=f"{st.session_state.saved_engineer.replace(' ', '_')}_Data.json")
