import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re
import json

st.set_page_config(page_title="Network Engineer Portal", layout="wide")

# --- INITIAL SESSION STATE ---
if "user_db" not in st.session_state: st.session_state.user_db = {"weeks": {}}
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 
if "saved_engineer" not in st.session_state: st.session_state.saved_engineer = ""
if "saved_rate" not in st.session_state: st.session_state.saved_rate = 0.0
if "saved_contract" not in st.session_state: st.session_state.saved_contract = 40
if "saved_service_5yr" not in st.session_state: st.session_state.saved_service_5yr = False
if "resolutions" not in st.session_state: st.session_state.resolutions = {}
if "selected_file_index" not in st.session_state: st.session_state.selected_file_index = 0

TS_COLS = ["Date Num", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site", "Original Row Info"]

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

# RESTORED FROM V1: Fixes OCR dropping the "1" in times like 14:15 -> 4:15
def fix_time_string(current_time, previous_time):
    if not current_time or not previous_time or current_time == "" or previous_time == "": 
        return current_time
    try:
        pt_str = "0" + previous_time if len(previous_time.split(":")[0]) == 1 else previous_time
        ct_str = "0" + current_time if len(current_time.split(":")[0]) == 1 else current_time
        
        pt = datetime.strptime(pt_str, "%H:%M")
        ct = datetime.strptime(ct_str, "%H:%M")
        
        if ct < pt and ct.hour < 10:
            new_hour = ct.hour + 10
            if new_hour < 24:
                new_ct = ct.replace(hour=new_hour)
                if new_ct >= pt:
                    return new_ct.strftime("%H:%M").lstrip("0") if new_ct.hour < 10 else new_ct.strftime("%H:%M")
    except: 
        pass
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
        
        if tdelta.days < 0: tdelta = timedelta(days=0, seconds=tdelta.seconds, microseconds=tdelta.microseconds)
        return round(tdelta.total_seconds() / 3600, 2)
    except: return 0.0

# RESTORED FROM V1: Re-implemented V1 Strict Continuity Snap and OCR fixes inside V2 loop
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

        # V1 Strict Continuity Auto-Snap
        if len(processed_data) > 0:
            prev_left = processed_data[-1]["left"]
            prev_date = processed_data[-1]["date"]
            if dn == prev_date and prev_left != "" and pending_break_mins == 0:
                if beg == "": beg = prev_left

        # V1 Smart Time String Fixer
        if len(processed_data) > 0:
            prev_left_time = processed_data[-1]["left"]
            beg = fix_time_string(beg, prev_left_time)
        arr = fix_time_string(arr, beg)
        lft = fix_time_string(lft, arr)

        # Break Handling
        is_break = "BREAK" in site.upper()
        if is_break:
            b_mins = round(calc_hours(arr, lft) * 60)
            if b_mins == 0: b_mins = round(calc_hours(beg, lft) * 60) 
            if b_mins == 0: b_mins = round(calc_hours(beg, arr) * 60) 
            pending_break_mins += b_mins
            continue
            
        # V1 Failsafe Continuity Check
        if (beg == "" or pending_break_mins > 0) and len(processed_data) > 0: 
            if processed_data[-1]["date"] == dn:
                beg = processed_data[-1]["left"]

        work = calc_hours(arr, lft)
        travel = calc_hours(beg, arr)
        
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
    
    # Process V2 Ghost Rows (Sick/Annual Leave)
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
            
    # Sort data by Date Num (to ensure Ghost rows appear in the right order)
    processed_data.sort(key=lambda x: int(x["date"]) if x["date"].isdigit() else 99)
    return processed_data

# RESTORED FROM V1: Exact HTML Layout
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
        th {{ background-color: #f2f2f2; font-size: 7pt; height: 35px; }}
        .day-row {{ background-color: #ddd; font-weight: bold; text-align: left; padding-left: 10px; }}
        .total-row td {{ background-color: #eef2f5; font-weight: bold; border-top: 1.5px solid #000; }}
        .site-col {{ width: 22%; text-align: left; }}
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
                # Try to use full_date if available
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
                    <td></td>
                    <td></td>
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
            
    html_content += f"</tbody></table><div style='margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px; font-size:10pt;'>Weekly Total Hours: {grand_total:.2f}</div></body></html>"
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

ref_dt, ref_wk = None, None
if uploaded_pdfs:
    for f in uploaded_pdfs:
        with pdfplumber.open(f) as pdf:
            text = "".join([p.extract_text() or "" for p in pdf.pages])
            m_we = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
            m_wk = re.search(r"Week:\s*(\d+)", text)
            if m_we and m_wk:
                ref_dt = datetime.strptime(m_we.group(1), "%d %b %Y")
                ref_wk = int(m_wk.group(1))
                break

    for idx, f in enumerate(uploaded_pdfs):
        we, wk, rows = "", "", []
        with pdfplumber.open(f) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                m_we = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
                if m_we: we = m_we.group(1)
                m_wk = re.search(r"Week:\s*(\d+)", text)
                if m_wk: wk = m_wk.group(1)
                
                for line in text.split('\n'):
                    raw_times = re.findall(r'[0-9Oo]{1,2}:[0-9Oo]{2}', line)
                    if len(raw_times) >= 1: 
                        first_time_idx = line.find(raw_times[0])
                        raw_site = line[:first_time_idx].strip()
                        
                        date_match = re.search(r"^([A-Z]\s*)?(\d{1,2})\s+", raw_site)
                        date_num = date_match.group(2) if date_match else ""
                        
                        # RESTORED FROM V1: Rigorous string cleaning
                        site_clean = re.split(r"\*+QUO|\*?QUOTE", raw_site, flags=re.IGNORECASE)[0].strip()
                        site_clean = re.split(r"£|R1 OA|\b[A-Z0-9]{3,}:", site_clean)[0].strip()
                        site_clean = re.sub(r"^([A-Z]\s*)?\d{1,2}\s+", "", site_clean) 
                        site_clean = re.sub(r"\s+\d+$", "", site_clean).strip() 
                        site_clean = re.sub(r"\s+\d+\.\d+$", "", site_clean).strip() 
                        site_clean = re.sub(r"\s+[A-Z0-9\s]{1,10}SC$", "", site_clean).strip() 
                        site_clean = re.sub(r"\s+[a-z].*", "", site_clean).strip()
                        
                        times = []
                        for t in raw_times:
                            t_clean = t.replace('O', '0').replace('o', '0')
                            hr, mn = t_clean.split(':')
                            if hr.isdigit() and int(hr) > 23: hr = hr[0]
                            times.append(f"{hr}:{mn}")
                            
                        if len(times) >= 3: began, arrived, left = times[0], times[1], times[2]
                        elif len(times) == 2:
                            if "HOME" in site_clean.upper() or "BREAK" in site_clean.upper(): began, arrived, left = times[0], times[1], ""
                            else: began, arrived, left = "", times[0], times[1]
                        else: began, arrived, left = "", times[0], ""
                            
                        rows.append({"Date Num": date_num, "Site & Ref No.": site_clean, "Began Journey": began, "Arrived On Site": arrived, "Left Site": left, "Original Row Info": line})
        
        if not wk:
            fm = re.search(r"[Ww]eek[_\s]*(\d+)", f.name)
            wk = fm.group(1) if fm else ""
        if not we and wk and ref_dt and ref_wk:
            try: we = (ref_dt + timedelta(weeks=int(wk) - ref_wk)).strftime("%d %b %Y")
            except: pass

        dt_obj = None
        try: dt_obj = datetime.strptime(we, "%d %b %Y")
        except: pass
        
        df_file = pd.DataFrame(rows)
        if df_file.empty: df_file = pd.DataFrame(columns=TS_COLS)

        if dt_obj:
            expected = [str((dt_obj - timedelta(days=6-i)).day) for i in range(7) if (dt_obj - timedelta(days=6-i)).weekday() < 5]
            found = df_file["Date Num"].unique().tolist() if "Date Num" in df_file.columns else []
            unresolved = [d for d in expected if d not in found]
            if (unresolved or rows == []) and f.name not in st.session_state.resolutions:
                global_missing_files.append({"name": f.name, "we": we, "index": idx})

        all_uploads[f.name] = {"we": we, "wk": wk, "dt_obj": dt_obj, "df": df_file, "idx": idx}

# --- GLOBAL ALERT ---
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
    if not all_uploads: st.info("Upload PDFs.")
    else:
        file_list = list(all_uploads.keys())
        sel_name = st.selectbox("Select Timesheet:", file_list, index=st.session_state.selected_file_index)
        up = all_uploads[sel_name]
        
        f_we = st.text_input("Week End Date", value=up["we"], key=f"we_in_{sel_name}")
        f_wk = st.text_input("Week No", value=up["wk"], key=f"wk_in_{sel_name}")
        
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
                st.warning("⚠️ Manual Resolution Required:")
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

        edited_df = st.data_editor(up["df"][["Date Num", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site"]], num_rows="dynamic", use_container_width=True, key=f"ed_{sel_name}")
        
        # Add the 'Original Row Info' back onto the edited dataframe for background parsing
        if "Original Row Info" in up["df"].columns:
            edited_df["Original Row Info"] = up["df"]["Original Row Info"]

        if st.button("🖨️ Generate & Download Resolved PDF"):
            res = st.session_state.resolutions.get(sel_name, {})
            proc = process_timesheet_data(edited_df, end_dt, res, st.session_state.saved_contract)
            has_weekend = any(re.search(r'^(SAT|SUN|S\s|SA\s|SU\s)', str(row.get('Original Row Info','')).upper()) for _, row in edited_df.iterrows())
            html = generate_pdf_html(proc, st.session_state.saved_engineer, f_we, f_wk, "Yes" if has_weekend else "No")
            st.download_button("⬇️ Download PDF", HTML(string=html).write_pdf(), file_name=f"{st.session_state.saved_engineer.replace(' ', '_')}_Timesheet_Week_{f_wk}.pdf")

with t2:
    if all_uploads:
        if st.button("🚀 SYNC ALL TO DATABASE", type="primary"):
            for fn, data in all_uploads.items():
                res = st.session_state.resolutions.get(fn, {})
                p = process_timesheet_data(data["df"], data["dt_obj"], res, st.session_state.saved_contract)
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
