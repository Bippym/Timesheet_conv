import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re
import plotly.express as px
import zipfile
import io

st.set_page_config(page_title="Network Timesheet Dashboard", layout="wide")

# --- SESSION STATE ---
if "saved_contract" not in st.session_state: st.session_state.saved_contract = 40
if "saved_rate" not in st.session_state: st.session_state.saved_rate = 0.00
if "saved_engineer" not in st.session_state: st.session_state.saved_engineer = "UNKNOWN ENGINEER"
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 

# --- SIDEBAR & DELETE FUNCTION ---
with st.sidebar:
    st.header("⚙️ Advanced Settings")
    if st.button("🗑️ Delete All Loaded Timesheets", type="primary"):
        st.session_state.uploader_key += 1
        st.rerun()
    st.markdown("---")
    debug_mode = st.checkbox("Enable Developer Debug Mode")

st.title("Network (Catering Engineers) Ltd - Timesheet Portal")
st.markdown("Upload multiple timesheets to process PDFs, view analytics, and project salary arrears.")

# --- CORE FUNCTIONS ---
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

def get_productivity_category(site_name):
    s = str(site_name).upper()
    if any(x in s for x in ["BYBOX", "SUPERVISOR", "TRAIN", "VEHICLE", "PARTS", "COLLECTING", "DEPOT"]): return "Non-Productive Work"
    return "Productive Work"

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
                
            prod_cat = "Ignored" if ("HOME" in site.upper() or "BREAK" in site.upper() or work_time == 0) else get_productivity_category(site)
            processed_data.append({"date": date_num, "site": site, "began": began, "arrived": arrived, "left": left, "work": work_time, "travel": travel_time, "rest_break": rest_break_display, "productivity": prod_cat})
        
    if missing_weekdays and missing_selections:
        daily_hrs = contract_hours / 5.0
        for d_num, d_name in missing_weekdays:
            reason = missing_selections.get(d_num, "Ignore")
            if reason != "Ignore":
                hrs = daily_hrs if reason == "Annual Leave" else 0.0
                processed_data.append({"date": d_num, "site": reason.upper(), "began": "", "arrived": "", "left": "", "work": hrs, "travel": 0.0, "rest_break": "", "productivity": "Non-Productive Work"})
                
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
    df_proc = pd.DataFrame(df_processed)
    grand_total = 0
    if not df_proc.empty:
        for date, group in df_proc.groupby("date", sort=False):
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

# --- MULTI-FILE EXTRACTION ROUTINE ---
uploaded_files = st.file_uploader("Upload Work-Style Timesheets (PDF)", type=["pdf"], accept_multiple_files=True, key=f"uploader_{st.session_state.uploader_key}")

if uploaded_files:
    c1, c2 = st.columns(2)
    def update_eng(): st.session_state.saved_engineer = st.session_state.eng_input
    def update_con(): st.session_state.saved_contract = st.session_state.con_input
    with c1: final_engineer = st.text_input("Engineer Name (Global)", value=st.session_state.saved_engineer, key="eng_input", on_change=update_eng)
    with c2: contract_hours = st.selectbox("Contracted Hours", options=[40, 45], index=0 if st.session_state.saved_contract == 40 else 1, key="con_input", on_change=update_con)
    
    datasets = {}
    master_analytics_data = []
    
    with st.spinner("Processing all uploaded timesheets..."):
        for uploaded_file in uploaded_files:
            extracted_data = []
            week_ending_str, week_number = "", "Unknown"
            raw_text_dump = ""
            
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text: raw_text_dump += text + "\n\n---PAGE BREAK---\n\n"
                    if "Week Ending:" in text:
                        match = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
                        if match: week_ending_str = match.group(1)
                    if "Week:" in text:
                        match = re.search(r"Week:\s*(\d+)", text)
                        if match: week_number = match.group(1)
                    if "Engineer:" in text and st.session_state.saved_engineer == "UNKNOWN ENGINEER":
                        match = re.search(r"Engineer:\s*([A-Za-z\s]+)", text)
                        if match:
                            eng_str = match.group(1).strip()
                            eng_str = re.split(r"(Week|Date|Network)", eng_str)[0].strip()
                            if eng_str: st.session_state.saved_engineer = eng_str

                    for line in text.split('\n'):
                        raw_times = re.findall(r'[0-9Oo]{1,2}:[0-9Oo]{2}', line)
                        if len(raw_times) >= 1: 
                            first_time_idx = line.find(raw_times[0])
                            raw_site = line[:first_time_idx].strip()
                            date_match = re.search(r"^([A-Za-z]{1,3}\s*)?(\d{1,2})\s+", raw_site)
                            date_num = date_match.group(2) if date_match else ""
                            
                            site_clean = re.split(r"\*+QUO|\*?QUOTE", raw_site, flags=re.IGNORECASE)[0].strip()
                            site_clean = re.split(r"£|R1 OA|\b[A-Z0-9]{3,}:", site_clean)[0].strip()
                            site_clean = re.sub(r"^([A-Za-z]{1,3}\s*)?\d{1,2}\s+", "", site_clean) 
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
                            extracted_data.append({"Date Num": date_num, "Original Row Info": line, "Site & Ref No.": site_clean, "Began Journey": began, "Arrived On Site": arrived, "Left Site": left})
            
            try:
                dt_obj = datetime.strptime(week_ending_str, "%d %b %Y")
                month_label = dt_obj.strftime("%B %Y")
            except:
                dt_obj, month_label = None, "Unknown Month"

            df_cols = ["Date Num", "Original Row Info", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site"]
            datasets[uploaded_file.name] = {
                "week_ending": week_ending_str, "week_number": week_number, "month_label": month_label,
                "dt_obj": dt_obj, "df": pd.DataFrame(extracted_data, columns=df_cols), "raw_text": raw_text_dump, "missing_selections": {}
            }

    # --- THE BLANK TIMESHEET INTERCEPTOR & DATE TRIANGULATION ---
    st.markdown("---")
    st.markdown("### ⚠️ Global Timesheet Resolution")
    any_missing = False
    
    for file_name, d_packet in datasets.items():
        if d_packet["df"].empty:
            any_missing = True
            st.warning(f"📄 **{file_name}** is completely blank (0 jobs logged).")
            
            guessed_wk = d_packet["week_number"]
            guessed_we = d_packet["week_ending"]
            
            # Smart Filename Parsing
            if not guessed_wk or guessed_wk == "Unknown":
                fm = re.search(r"[Ww](?:eek)?[_\s]*(\d{1,2})", file_name)
                if fm: guessed_wk = fm.group(1)
                
            # Date Triangulation (using timedelta from a valid uploaded sheet)
            if guessed_wk and guessed_wk.isdigit() and (not guessed_we or guessed_we == ""):
                ref_dt, ref_wk = None, None
                for other_pack in datasets.values():
                    if other_pack.get("dt_obj") and str(other_pack.get("week_number")).isdigit():
                        ref_dt = other_pack["dt_obj"]
                        ref_wk = int(other_pack["week_number"])
                        break
                if ref_dt and ref_wk is not None:
                    wk_diff = int(guessed_wk) - ref_wk
                    guessed_we = (ref_dt + timedelta(weeks=wk_diff)).strftime("%d %b %Y")

            c1, c2, c3 = st.columns(3)
            with c1: manual_we = st.text_input("Week End Date (e.g. 26 Apr 2026)", value=guessed_we, key=f"we_{file_name}")
            with c2: manual_wk = st.text_input("Week Number", value=guessed_wk, key=f"wk_{file_name}")
            with c3: full_week_reason = st.selectbox("Reason for Full Week Absence", ["Ignore", "Annual Leave", "Sick", "Unpaid Leave"], key=f"rsn_{file_name}")
            
            datasets[file_name]["week_ending"] = manual_we
            datasets[file_name]["week_number"] = manual_wk
            try:
                dt_obj = datetime.strptime(manual_we, "%d %b %Y")
                datasets[file_name]["dt_obj"] = dt_obj
                datasets[file_name]["month_label"] = dt_obj.strftime("%B %Y")
            except ValueError: datasets[file_name]["dt_obj"] = None
                
            if datasets[file_name]["dt_obj"] and full_week_reason != "Ignore":
                for i in range(7):
                    curr = datasets[file_name]["dt_obj"] - timedelta(days=6-i)
                    if curr.weekday() < 5: datasets[file_name]["missing_selections"][str(curr.day)] = full_week_reason
        else:
            if d_packet["dt_obj"]:
                expected_weekdays = [(str((d_packet["dt_obj"] - timedelta(days=6-i)).day), (d_packet["dt_obj"] - timedelta(days=6-i)).strftime("%A")) for i in range(7) if (d_packet["dt_obj"] - timedelta(days=6-i)).weekday() < 5]
                extracted_dates = d_packet["df"]["Date Num"].replace("", pd.NA).dropna().unique().tolist()
                missing_weekdays = [d for d in expected_weekdays if d[0] not in extracted_dates]
                
                if missing_weekdays:
                    any_missing = True
                    st.warning(f"Missing days detected in: **{file_name}** (Week Ending: {d_packet['week_ending']})")
                    cols = st.columns(len(missing_weekdays))
                    for idx, (d_num, d_name) in enumerate(missing_weekdays):
                        with cols[idx]: datasets[file_name]["missing_selections"][d_num] = st.selectbox(f"{d_name} ({d_num})", ["Ignore", "Sick", "Annual Leave", "Unpaid Leave"], key=f"miss_{file_name}_{d_num}")
                
    if not any_missing: st.success("No missing weekdays detected across all uploaded files. Ready to generate!")

    # 3. Compile Master Analytics Data
    for file_name, d_packet in datasets.items():
        missing_weekdays_info = []
        if d_packet["dt_obj"]:
            expected_weekdays = [(str((d_packet["dt_obj"] - timedelta(days=6-i)).day), (d_packet["dt_obj"] - timedelta(days=6-i)).strftime("%A")) for i in range(7) if (d_packet["dt_obj"] - timedelta(days=6-i)).weekday() < 5]
            extracted_dates = d_packet["df"]["Date Num"].replace("", pd.NA).dropna().unique().tolist()
            missing_weekdays_info = [d for d in expected_weekdays if d[0] not in extracted_dates]
            
        temp_processed = process_timesheet_data(d_packet["df"], d_packet["dt_obj"], missing_weekdays_info, d_packet["missing_selections"], contract_hours)
        for row in temp_processed:
            row["Week End"], row["Month"], row["File"] = d_packet["week_ending"], d_packet["month_label"], file_name
            master_analytics_data.append(row)

    df_master = pd.DataFrame(master_analytics_data)

    # --- TABBED INTERFACE ---
    tab1, tab2, tab3 = st.tabs(["📑 Individual Editor & Batch Export", "📈 Master Analytics", "💷 Salary & Arrears Breakdown"])

    # --- TAB 1: GENERATION ---
    with tab1:
        st.markdown("### 1️⃣ Single Timesheet Preview")
        selected_file = st.selectbox("Select timesheet to preview:", list(datasets.keys()))
        data_packet = datasets[selected_file]
        
        df_edit = data_packet["df"][["Date Num", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site", "Original Row Info"]]
        edited_df = st.data_editor(df_edit, num_rows="dynamic", use_container_width=True)

        if st.button(f"Generate PDF for {selected_file}", type="primary"):
            missing_weekdays_info = []
            if data_packet["dt_obj"]:
                expected_weekdays = [(str((data_packet["dt_obj"] - timedelta(days=6-i)).day), (data_packet["dt_obj"] - timedelta(days=6-i)).strftime("%A")) for i in range(7) if (data_packet["dt_obj"] - timedelta(days=6-i)).weekday() < 5]
                extracted_dates = edited_df["Date Num"].replace("", pd.NA).dropna().unique().tolist()
                missing_weekdays_info = [d for d in expected_weekdays if d[0] not in extracted_dates]
                
            processed_data = process_timesheet_data(edited_df, data_packet["dt_obj"], missing_weekdays_info, data_packet["missing_selections"], contract_hours)
            
            has_weekend = False
            if not edited_df.empty: has_weekend = any(re.match(r"^(S|SA|SAT|SU|SUN)\s*\d{1,2}", str(info).upper()) for info in edited_df["Original Row Info"])
            on_call_status = "Yes" if has_weekend else "No"
            
            html_content = generate_pdf_html(processed_data, final_engineer, data_packet["week_ending"], data_packet["week_number"], on_call_status)
            st.download_button(label=f"⬇️ Download PDF for {selected_file}", data=HTML(string=html_content).write_pdf(), file_name=f"{final_engineer.replace(' ', '_')}_{data_packet['week_number']}.pdf", mime="application/pdf")

        if len(datasets) > 1:
            st.markdown("---")
            st.markdown("### 2️⃣ Batch Operations")
            st.info("Generates all uploaded timesheets simultaneously using the missing days resolutions defined above.")
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                for file_name, d_pack in datasets.items():
                    missing_weekdays_info = []
                    if d_pack["dt_obj"]:
                        expected_weekdays = [(str((d_pack["dt_obj"] - timedelta(days=6-i)).day), (d_pack["dt_obj"] - timedelta(days=6-i)).strftime("%A")) for i in range(7) if (d_pack["dt_obj"] - timedelta(days=6-i)).weekday() < 5]
                        extracted_dates = d_pack["df"]["Date Num"].replace("", pd.NA).dropna().unique().tolist()
                        missing_weekdays_info = [d for d in expected_weekdays if d[0] not in extracted_dates]

                    batch_proc_data = process_timesheet_data(d_pack["df"], d_pack["dt_obj"], missing_weekdays_info, d_pack["missing_selections"], contract_hours)
                    
                    has_wknd = False
                    if not d_pack["df"].empty: has_wknd = any(re.match(r"^(S|SA|SAT|SU|SUN)\s*\d{1,2}", str(info).upper()) for info in d_pack["df"]["Original Row Info"])
                    oc_status = "Yes" if has_wknd else "No"
                    
                    b_html = generate_pdf_html(batch_proc_data, final_engineer, d_pack["week_ending"], d_pack["week_number"], oc_status)
                    pdf_bytes = HTML(string=b_html).write_pdf()
                    zip_file.writestr(f"{final_engineer.replace(' ', '_')}_Wk_{d_pack['week_number']}.pdf", pdf_bytes)
            
            st.download_button(label="📦 Download ALL Timesheets as ZIP", data=zip_buffer.getvalue(), file_name="Network_Timesheets_Batch.zip", mime="application/zip")

    # --- TAB 2: ANALYTICS ---
    with tab2:
        st.markdown("### 🎛️ Analytics Filter")
        f1, f2 = st.columns([1, 2])
        with f1: filter_type = st.radio("Time Period", ["All Time", "By Month", "By Week"])
        with f2:
            if filter_type == "By Month" and not df_master.empty:
                available_months = df_master["Month"].unique()
                sel_filter = st.selectbox("Select Month", available_months)
                df_filtered = df_master[df_master["Month"] == sel_filter]
            elif filter_type == "By Week" and not df_master.empty:
                available_weeks = df_master["Week End"].unique()
                sel_filter = st.selectbox("Select Week Ending", available_weeks)
                df_filtered = df_master[df_master["Week End"] == sel_filter]
            else: df_filtered = df_master

        if df_filtered.empty: st.warning("No data available for the selected period.")
        else:
            st.markdown("---")
            st.markdown("### 📈 Averages & Key Metrics")
            m1, m2, m3, m4 = st.columns(4)
            
            total_work_global = df_filtered['work'].sum()
            total_travel_global = df_filtered['travel'].sum()
            total_overall_global = total_work_global + total_travel_global
            
            unique_weeks = df_filtered['Week End'].nunique()
            unique_months = df_filtered['Month'].nunique()
            unique_days = df_filtered.groupby(['Week End', 'date']).ngroups
            
            avg_hrs_week = total_overall_global / unique_weeks if unique_weeks > 0 else 0
            avg_hrs_month = total_overall_global / unique_months if unique_months > 0 else 0
            avg_travel_day = total_travel_global / unique_days if unique_days > 0 else 0
            avg_work_day = total_work_global / unique_days if unique_days > 0 else 0
            
            m1.metric("Avg Hours per Week", f"{avg_hrs_week:.2f} hrs")
            m2.metric("Avg Hours per Month", f"{avg_hrs_month:.2f} hrs")
            m3.metric("Avg Travel per Day", f"{avg_travel_day:.2f} hrs")
            m4.metric("Avg On-Site per Day", f"{avg_work_day:.2f} hrs")

            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                if total_overall_global > 0:
                    pie_overall = pd.DataFrame({"Category": ["On-Site Work", "Travel Time"], "Hours": [total_work_global, total_travel_global]})
                    fig1 = px.pie(pie_overall, values='Hours', names='Category', hole=0.4, title="Overall Time: Work vs Travel", color_discrete_sequence=['#2e7b32', '#1976d2'])
                    fig1.update_traces(textposition='inside', textinfo='percent+label')
                    st.plotly_chart(fig1, use_container_width=True)

            with c2:
                prod_hours = df_filtered[df_filtered['productivity'] == 'Productive Work']['work'].sum()
                non_prod_hours = df_filtered[df_filtered['productivity'] == 'Non-Productive Work']['work'].sum()
                if prod_hours + non_prod_hours > 0:
                    pie_prod = pd.DataFrame({"Category": ["Productive Work", "Non-Productive Work"], "Hours": [prod_hours, non_prod_hours]})
                    fig2 = px.pie(pie_prod, values='Hours', names='Category', hole=0.4, title="Productivity Split (On-Site Hours)", color_discrete_sequence=['#ff9800', '#757575'])
                    fig2.update_traces(textposition='inside', textinfo='percent+label')
                    st.plotly_chart(fig2, use_container_width=True)

    # --- TAB 3: SALARY & ARREARS ---
    with tab3:
        st.markdown("### 💷 Annual Salary & Arrears Breakdown")
        st.info("In the UK, Basic Pay is calculated annually and paid in 12 equal monthly installments. Overtime and Double-Time are calculated per-week, and paid a month in arrears (e.g., March's overtime is paid in April).")

        p1, p2 = st.columns(2)
        def update_rate_tab3(): st.session_state.saved_rate = st.session_state.rate_input_tab3
        with p1: rate = st.number_input("Global Hourly Rate (£)", value=st.session_state.saved_rate, step=0.50, format="%.2f", key="rate_input_tab3", on_change=update_rate_tab3)
        
        annual_basic = rate * contract_hours * 52
        monthly_basic = annual_basic / 12
        
        st.markdown(f"**Annual Basic Pay:** £{annual_basic:,.2f} | **Monthly Basic Pay:** £{monthly_basic:,.2f}")

        if df_master.empty:
            st.warning("Upload timesheets to see your salary and arrears projections.")
        else:
            st.markdown("---")
            all_dt_strs = df_master["Week End"].unique()
            bh_options = []
            for we_str in all_dt_strs:
                try:
                    dt_obj = datetime.strptime(we_str, "%d %b %Y")
                    for i in range(7):
                        curr = dt_obj - timedelta(days=6-i)
                        bh_options.append(f"{curr.strftime('%A')} {curr.day} ({we_str})")
                except: pass
            
            bank_holidays_raw = st.multiselect("Select Bank Holidays for Double-Time Calculation:", options=list(set(bh_options)), key="bh_tab3")
            bank_holidays = [b.split(" ")[1] for b in bank_holidays_raw]

            # PAYROLL LEDGER ENGINE
            payroll_ledger = {}
            for week_str, week_group in df_master.groupby("Week End"):
                try:
                    week_dt_obj = datetime.strptime(week_str, "%d %b %Y")
                    worked_month_key = week_dt_obj.strftime("%Y-%m")
                    worked_month_label = week_dt_obj.strftime("%B %Y")

                    # Shift payment month forward by 1 for arrears logic
                    if week_dt_obj.month == 12: payment_month_dt = week_dt_obj.replace(year=week_dt_obj.year+1, month=1, day=1)
                    else: payment_month_dt = week_dt_obj.replace(month=week_dt_obj.month+1, day=1)

                    payment_month_key = payment_month_dt.strftime("%Y-%m")
                    payment_month_label = payment_month_dt.strftime("%B %Y")
                except: continue

                if worked_month_key not in payroll_ledger: payroll_ledger[worked_month_key] = {"label": worked_month_label, "basic_count": 0, "ot_hrs": 0.0, "dt_hrs": 0.0}
                if payment_month_key not in payroll_ledger: payroll_ledger[payment_month_key] = {"label": payment_month_label, "basic_count": 0, "ot_hrs": 0.0, "dt_hrs": 0.0}

                week_std_hrs, week_dt_hrs = 0.0, 0.0
                for _, row in week_group.iterrows():
                    day_total = float(row['work']) + float(row['travel'])
                    is_dt = False
                    
                    if str(row['date']) in bank_holidays: is_dt = True
                    try:
                        dt_obj = datetime.strptime(row['Week End'], "%d %b %Y")
                        for i in range(7):
                            curr = dt_obj - timedelta(days=6-i)
                            if str(curr.day) == str(row['date']) and curr.weekday() == 6: is_dt = True
                    except: pass
                    
                    if is_dt: week_dt_hrs += day_total
                    else: week_std_hrs += day_total

                week_ot = max(0, week_std_hrs - contract_hours)

                payroll_ledger[worked_month_key]["basic_count"] += 1
                payroll_ledger[payment_month_key]["ot_hrs"] += week_ot
                payroll_ledger[payment_month_key]["dt_hrs"] += week_dt_hrs

            payroll_data = []
            total_projected_gross = 0.0

            for m_key in sorted(payroll_ledger.keys()):
                m_data = payroll_ledger[m_key]
                basic_pay = monthly_basic if m_data["basic_count"] > 0 else 0.0
                ot_pay = m_data["ot_hrs"] * (rate * 1.5)
                dt_pay = m_data["dt_hrs"] * (rate * 2.0)
                gross = basic_pay + ot_pay + dt_pay
                total_projected_gross += gross

                payroll_data.append({
                    "Payroll Month": m_data["label"],
                    "Basic Pay (£)": f"£{basic_pay:,.2f}",
                    "Arrears OT (Hrs)": f"{m_data['ot_hrs']:.2f}",
                    "Arrears OT Pay (£)": f"£{ot_pay:,.2f}",
                    "Arrears DT (Hrs)": f"{m_data['dt_hrs']:.2f}",
                    "Arrears DT Pay (£)": f"£{dt_pay:,.2f}",
                    "Gross Pay (£)": f"£{gross:,.2f}"
                })

            st.dataframe(pd.DataFrame(payroll_data), use_container_width=True)
            
# --- ANNUAL EARNINGS PROJECTION ---
            actual_weeks_uploaded = df_master['Week End'].nunique()
            if actual_weeks_uploaded > 0:
                # Bypass the monthly ledger entirely to prevent month-spillover inflation.
                # Calculate the exact true gross for ONLY the weeks uploaded.
                true_total_gross = (total_base_hrs * rate) + (total_ot_hrs * (rate * 1.5)) + (total_dt_hrs * (rate * 2.0))
                
                avg_weekly_gross = true_total_gross / actual_weeks_uploaded
                est_annual_gross = avg_weekly_gross * 52
                
                st.markdown("---")
                st.markdown("### 📊 Annual Earnings Projection")
                st.info("This projection isolates the exact true gross pay of the uploaded timesheets to find your true weekly average, then extrapolates it across a full 52-week year.")
                
                a1, a2, a3 = st.columns(3)
                a1.metric("Avg. True Weekly Gross", f"£{avg_weekly_gross:,.2f}")
                a2.metric("Multiplier", "x 52 Weeks")
                a3.metric("Est. Annual Gross", f"£{est_annual_gross:,.2f}")

    if debug_mode:
        st.markdown("---")
        st.subheader("🛠️ Developer Diagnostic Text")
        for file_name, d_packet in datasets.items():
            st.markdown(f"**{file_name}**")
            st.text_area(f"Raw Output - {file_name}", d_packet["raw_text"], height=200, key=f"raw_{file_name}")
