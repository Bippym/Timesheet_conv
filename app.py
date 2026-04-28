import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re

st.set_page_config(page_title="Network Timesheet Generator", layout="wide")

st.title("Network (Catering Engineers) Ltd - Timesheet Converter")
st.markdown("Upload your work-style PDF. The app will extract the data, apply your continuity rules, and generate the manual-style PDF.")

debug_mode = st.checkbox("Debug Mode: Show Raw PDF Text")

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
            if hrs > 10: 
                return round(hrs - 14, 2) 
            return round(hrs, 2)
            
        return round(tdelta.total_seconds() / 3600, 2)
    except: return 0.0

uploaded_file = st.file_uploader("Upload Work-Style Timesheet (PDF)", type=["pdf"])

if uploaded_file is not None:
    extracted_data = []
    week_ending_str = ""
    week_number = "18"
    engineer_name = "UNKNOWN ENGINEER"
    raw_text_dump = ""
    
    with st.spinner("Extracting data from PDF..."):
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
                if "Engineer:" in text:
                    match = re.search(r"Engineer:\s*([A-Za-z\s]+)", text)
                    if match:
                        eng_str = match.group(1).strip()
                        eng_str = re.split(r"(Week|Date|Network)", eng_str)[0].strip()
                        if eng_str: engineer_name = eng_str

                for line in text.split('\n'):
                    raw_times = re.findall(r'[0-9Oo]{1,2}:[0-9Oo]{2}', line)
                    
                    if len(raw_times) >= 1: 
                        first_time_idx = line.find(raw_times[0])
                        raw_site = line[:first_time_idx].strip()
                        
                        date_match = re.search(r"^([A-Z]\s*)?(\d{1,2})\s+", raw_site)
                        date_num = date_match.group(2) if date_match else ""
                        
                        site_clean = re.split(r"\*+QUO|\*?QUOTE", raw_site, flags=re.IGNORECASE)[0].strip()
                        site_clean = re.split(r"£|R1 OA|\b[A-Z0-9]{3,}:", site_clean)[0].strip()
                        site_clean = re.sub(r"^([A-Z]\s*)?\d{1,2}\s+", "", site_clean) 
                        site_clean = re.sub(r"\s+\d+$", "", site_clean).strip() 
                        site_clean = re.sub(r"\s+\d+\.\d+$", "", site_clean).strip() 
                        site_clean = re.sub(r"\s+[A-Z0-9\s]{1,10}SC$", "", site_clean).strip() 
                        
                        # --- LOWERCASE NOTE ERADICATOR ---
                        # Instantly deletes typed notes appended to the end of site names
                        site_clean = re.sub(r"\s+[a-z].*", "", site_clean).strip()
                        
                        times = []
                        for t in raw_times:
                            t_clean = t.replace('O', '0').replace('o', '0')
                            hr, mn = t_clean.split(':')
                            if hr.isdigit() and int(hr) > 23: hr = hr[0]
                            times.append(f"{hr}:{mn}")
                        
                        if len(times) >= 3:
                            began, arrived, left = times[0], times[1], times[2]
                        elif len(times) == 2:
                            if "HOME" in site_clean.upper() or "BREAK" in site_clean.upper(): 
                                began, arrived, left = times[0], times[1], ""
                            else: 
                                began, arrived, left = "", times[0], times[1]
                        else:
                            began, arrived, left = "", times[0], ""
                        
                        extracted_data.append({
                            "Date Num": date_num,
                            "Original Row Info": line,
                            "Site & Ref No.": site_clean,
                            "Began Journey": began,
                            "Arrived On Site": arrived,
                            "Left Site": left
                        })

    if debug_mode:
        st.subheader("Raw Text from PDF")
        st.text_area("Raw Extracted Text", raw_text_dump, height=300)

    if extracted_data:
        st.success("Data extracted! The OCR Time Corrector and Continuity Auto-Snap are active.")
        
        col1, col2, col3 = st.columns(3)
        with col1: final_date = st.text_input("Week End Date", value=week_ending_str)
        with col2: final_week = st.text_input("Week Number", value=week_number)
        with col3: final_engineer = st.text_input("Engineer Name", value=engineer_name)
            
        df = pd.DataFrame(extracted_data)
        df = df[["Date Num", "Site & Ref No.", "Began Journey", "Arrived On Site", "Left Site", "Original Row Info"]]
        edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
        
        if st.button("Apply Rules & Generate PDF", type="primary"):
            processed_data = []
            has_weekend = False
            pending_break_mins = 0
            
            for raw_info in df["Original Row Info"].astype(str):
                if re.match(r"^[S]\s*\d{1,2}", raw_info): has_weekend = True
                    
            on_call_status = "Yes" if has_weekend else "No"
            
            for index, row in edited_df.iterrows():
                date_num, site, arrived, left, began = str(row["Date Num"]), str(row["Site & Ref No."]), str(row["Arrived On Site"]), str(row["Left Site"]), str(row["Began Journey"])
                
                if "BREAK" in site.upper():
                    b_mins = round(calc_hours(arrived, left) * 60)
                    if b_mins == 0: b_mins = round(calc_hours(began, left) * 60) 
                    if b_mins == 0: b_mins = round(calc_hours(began, arrived) * 60) 
                    
                    pending_break_mins += b_mins
                    continue 

                # --- STRICT CONTINUITY AUTO-SNAP ---
                if len(processed_data) > 0:
                    prev_left = processed_data[-1]["left"]
                    prev_date = processed_data[-1]["date"]
                    
                    # Overwrite start time to match previous end time if on the same day and no break occurred
                    if date_num == prev_date and prev_left != "" and pending_break_mins == 0:
                        began = prev_left

                if (began == "" or pending_break_mins > 0) and len(processed_data) > 0: 
                    began = processed_data[-1]["left"]
                    
                travel_time, work_time = calc_hours(began, arrived), calc_hours(arrived, left)
                
                rest_break_display = ""
                if pending_break_mins > 0:
                    rest_break_display = str(pending_break_mins)
                    travel_time = max(0.0, travel_time - (pending_break_mins / 60.0))
                    pending_break_mins = 0 
                
                processed_data.append({
                    "date": date_num, 
                    "site": site, 
                    "began": began, 
                    "arrived": arrived, 
                    "left": left, 
                    "work": work_time, 
                    "travel": travel_time,
                    "rest_break": rest_break_display
                })
                
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
                th, td {{ border: 1px solid #000; padding: 4px; text-align: center; }}
                th {{ background-color: #f2f2f2; font-size: 7pt; height: 35px; }}
                .day-row {{ background-color: #ddd; font-weight: bold; text-align: left; padding-left: 10px; }}
                .total-row {{ font-weight: bold; background-color: #f9f9f9; }}
            </style>
            </head>
            <body>
                <div class="header">
                    <div class="header-left"><strong>Engineer:</strong> {final_engineer}<br><strong>Network (Catering Engineers) Ltd</strong></div>
                    <div class="header-center"><strong>Week End Date:</strong> {final_date}<br><strong>Week:</strong> {final_week}</div>
                    <div class="header-right"><strong>On-call:</strong> {on_call_status}</div>
                </div>
                <table>
                    <thead><tr><th style="width: 22%;">Site & Ref No.</th><th>Multiple Jobs</th><th>Job Number</th><th>Began Journey</th><th>Arrived On Site</th><th>Left Site</th><th>Hours Worked</th><th>Rest Break (min)</th><th>Travel Time</th><th>TOTAL Hours</th></tr></thead>
                    <tbody>
            """
            
            df_processed = pd.DataFrame(processed_data)
            grand_total = 0
            
            for date, group in df_processed.groupby("date", sort=False):
                try:
                    end_date_obj = datetime.strptime(final_date, "%d %b %Y")
                    day_str = f"Date: {date}"
                    for i in range(7):
                        curr = end_date_obj - timedelta(days=6-i)
                        if str(curr.day) == str(date):
                            suffix = 'th' if 11 <= curr.day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(curr.day % 10, 'th')
                            day_str = curr.strftime(f"%A {curr.day}{suffix} %B")
                            break
                except:
                    day_str = f"Date: {date}"
                    
                html_content += f'<tr><td colspan="10" class="day-row">{day_str}</td></tr>'
                
                day_total = 0
                for _, row in group.iterrows():
                    row_total = row['work'] + row['travel']
                    html_content += f"<tr><td>{row['site']}</td><td></td><td></td><td>{row['began']}</td><td>{row['arrived']}</td><td>{row['left']}</td><td>{row['work']:.2f}</td><td>{row['rest_break']}</td><td>{row['travel']:.2f}</td><td>{row_total:.2f}</td></tr>"
                    day_total += row_total
                grand_total += day_total
            
            html_content += f"</tbody></table><div style='margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px;'>Weekly Total Hours: {grand_total:.2f}</div></body></html>"
            
            st.success("PDF Generated Successfully!")
            st.download_button(label="Download Timesheet PDF", data=HTML(string=html_content).write_pdf(), file_name=f"{final_engineer.replace(' ', '_')}_Timesheet_Week_{final_week}.pdf", mime="application/pdf")
