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
    if not start_str or not end_str: return 0.0
    try:
        fmt = "%H:%M"
        t1 = datetime.strptime(start_str.rjust(5, '0'), fmt)
        t2 = datetime.strptime(end_str.rjust(5, '0'), fmt)
        tdelta = t2 - t1
        hrs = tdelta.total_seconds() / 3600
        if hrs < 0: hrs += 24
        if hrs > 12: hrs -= 14 # Handling specific overnight shift log patterns[cite: 2]
        return round(hrs, 2)
    except: return 0.0

def generate_pdf_html(df_processed, engineer, week_end_date, week_number, on_call):
    html_content = f"""
    <!DOCTYPE html><html><head><style>
        body {{ font-family: Arial, sans-serif; font-size: 8.5pt; margin: 0; padding: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 15px; border-bottom: 1.5px solid #000; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
        th, td {{ border: 1px solid #000; padding: 5px; text-align: center; }}
        th {{ background-color: #f2f2f2; font-size: 7.5pt; text-transform: uppercase; }}
        .day-row {{ background-color: #ddd; font-weight: bold; text-align: left; padding-left: 10px; font-size: 9pt; }}
        .total-row td {{ background-color: #f9f9f9; font-weight: bold; border-top: 1.5px solid #000; }}
        .site-cell {{ text-align: left; font-weight: bold; width: 30%; }}
    </style></head><body>
        <div class="header">
            <div><strong>Engineer:</strong> {engineer}<br>Network (Catering Engineers) Ltd</div>
            <div style="text-align:center;"><strong>Week End Date:</strong> {week_end_date}<br><strong>Week:</strong> {week_number}</div>
            <div style="text-align:right;"><strong>On-call:</strong> {on_call}</div>
        </div>
        <table>
            <thead>
                <tr>
                    <th class="site-cell">Site & Ref No.</th>
                    <th>Began</th><th>Arrived</th><th>Left</th><th>Work</th><th>Travel</th><th>Rest (m)</th><th>Total</th>
                </tr>
            </thead>
            <tbody>
    """
    df_p = pd.DataFrame(df_processed)
    grand_total = 0
    if not df_p.empty:
        # Grouping by formatted date to create the individual blocks[cite: 1]
        for date_val, group in df_p.groupby("full_date", sort=False):
            try:
                dt = datetime.strptime(date_val, "%Y-%m-%d")
                day_header = f"{dt.strftime('%A')} {dt.day}{get_suffix(dt.day)} {dt.strftime('%B')}"
            except: day_header = f"Date: {date_val}"
            
            html_content += f'<tr><td colspan="8" class="day-row">{day_header}</td></tr>'
            day_total = 0
            for _, row in group.iterrows():
                work, travel = row.get('work',0), row.get('travel',0)
                row_total = work + travel
                day_total += row_total
                html_content += f"""
                <tr>
                    <td class="site-cell">{str(row.get('site','')).upper()}</td>
                    <td>{row.get('began','')}</td>
                    <td>{row.get('arrived','')}</td>
                    <td>{row.get('left','')}</td>
                    <td>{work:.2f}</td>
                    <td>{travel:.2f}</td>
                    <td>{row.get('rest_break','')}</td>
                    <td>{row_total:.2f}</td>
                </tr>"""
            grand_total += day_total
            html_content += f'<tr class="total-row"><td colspan="7" style="text-align: right;">Daily Total:</td><td>{day_total:.2f}</td></tr>'
            
    html_content += f"</tbody></table><div style='text-align:right; margin-top:20px; font-weight:bold; font-size:10pt;'>WEEKLY TOTAL HOURS: {grand_total:.2f}</div></body></html>"
    return html_content

def process_timesheet_data(df, end_date_obj=None, missing_selections=None, contract_hours=40):
    processed_data = []
    pending_break_mins = 0
    
    # Process Base Rows with Break Subtraction[cite: 2]
    for _, row in df.iterrows():
        dn, site, beg, arr, lft = str(row.get("Date Num","")), str(row.get("Site & Ref No.","")), str(row.get("Began Journey","")), str(row.get("Arrived On Site","")), str(row.get("Left Site",""))
        if not beg and not arr and not lft: continue
        
        is_break = "BREAK" in site.upper()
        if is_break:
            pending_break_mins += round(calc_hours(arr, lft) * 60)
            if pending_break_mins == 0: pending_break_mins += round(calc_hours(beg, lft) * 60)
            continue
            
        work, travel = calc_hours(arr, lft), calc_hours(beg, arr)
        
        # Deduct breaks from travel time[cite: 2]
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
    
    # Process Ghost Rows
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
            processed_data.append({"date": d_num, "full_date": f_date, "site": reason.upper(), "work": daily if reason == "Annual Leave" else 0.0, "travel": 0.0, "rest_break": ""})
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

uploaded_pdfs = st.file_uploader("Upload PDF Timesheets", type=["pdf"], accept_multiple_files=True, key=f"pdf_up_{st.session_state.uploader_key}")
all_uploads = {}
global_missing_files = []

# Reference date for triangulation
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
                    times = re.findall(r'\d{1,2}:\d{2}', line)
                    if times:
                        site_raw = line.split(times[0])[0].strip()
                        d_m = re.search(r"(\d{1,2})\s+", site_raw)
                        rows.append({"Date Num": d_m.group(1) if d_m else "", "Site & Ref No.": re.sub(r"^[A-Z]?\s?\d{1,2}\s+", "", site_raw), "Began Journey": times[0], "Arrived On Site": times[1] if len(times)>1 else "", "Left Site": times[2] if len(times)>2 else "", "Original Row Info": line})
        
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

        edited_df = st.data_editor(up["df"], num_rows="dynamic", use_container_width=True, key=f"ed_{sel_name}")
        
        if st.button("🖨️ Generate & Download Resolved PDF"):
            res = st.session_state.resolutions.get(sel_name, {})
            proc = process_timesheet_data(edited_df, end_dt, res, st.session_state.saved_contract)
            has_weekend = any(re.search(r'^(SAT|SUN|S\s|SA\s|SU\s)', str(row.get('Original Row Info','')).upper()) for _, row in edited_df.iterrows())
            html = generate_pdf_html(proc, st.session_state.saved_engineer, f_we, f_wk, "Yes" if has_weekend else "No")
            st.download_button("⬇️ Download PDF", HTML(string=html).write_pdf(), file_name=f"{sel_name}.pdf")

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
    st.download_button("📦 Download JSON", out, file_name=f"{st.session_state.saved_engineer}_Data.json")
