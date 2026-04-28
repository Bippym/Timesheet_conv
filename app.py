import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from weasyprint import HTML
import pdfplumber
import re
import io

st.set_page_config(page_title="Network Timesheet Generator", layout="wide")

st.title("Network (Catering Engineers) Ltd - Timesheet Converter")
st.markdown("Upload your work-style PDF. The app will extract the data, apply your continuity rules, and generate the manual-style PDF.")

# --- DEBUG TOGGLE ---
debug_mode = st.checkbox("Debug Mode: Show Raw PDF Text (Use if extraction fails)")

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

uploaded_file = st.file_uploader("Upload Work-Style Timesheet (PDF)", type=["pdf"])

if uploaded_file is not None:
    extracted_data = []
    week_ending_str = ""
    week_number = "18"
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

                # --- NEW TEXT SCRAPER LOGIC ---
                for line in text.split('\n'):
                    if "Wrkd:" in line or "Day:" in line or "Prod:" in line or "Site Miles" in line or "Hours" in line or "Batch" in line:
                        continue
                        
                    # Hunt for all times formatted as HH:MM
                    times = re.findall(r'\b\d{1,2}:\d{2}\b', line)
                    
                    if len(times) >= 2: 
                        first_time_idx = line.find(times[0])
                        raw_site = line[:first_time_idx].strip()
                        
                        # Clean up the site string
                        site_clean = raw_site.split("**QUOTE")[0].strip() # Remove the garbled quote data
                        site_clean = re.sub(r"^[A-Z]\s*\d{1,2}\s*", "", site_clean) # Remove the Day/Date prefix (e.g., M 20)
                        site_clean = re.sub(r"\s+\d+$", "", site_clean).strip() # Remove the miles digit just before the time
                        
                        extracted_data.append({
                            "Original Row Info": line,
                            "Site & Ref No.": site_clean,
                            "Began Journey": times[0],
                            "Arrived On Site": times[1],
                            "Left Site": times[2] if len(times) > 2 else ""
                        })

    if debug_mode:
        st.subheader("Raw Text from PDF")
        st.text_area("Raw Extracted Text", raw_text_dump, height=300)

    if extracted_data:
        st.success("Data extracted successfully! Please review and fix any missing days below.")
        col1, col2 = st.columns(2)
        with col1: final_date = st.text_input("Week End Date", value=week_ending_str)
        with col2: final_week = st.text_input("Week Number", value=week_number)
            
        df = pd.DataFrame(extracted_data)
        edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
        
        if st.button("Apply Rules & Generate PDF", type="primary"):
            processed_data = []
            has_weekend = False
            for site_str in df["Original Row Info"].astype(str):
                if re.match(r"^[S]\s*\d{1,2}", site_str): has_weekend = True # Check if line starts with S (Saturday/Sunday)
                    
            on_call_status = "Yes" if has_weekend else "No"
            
            for index, row in edited_df.iterrows():
                site, arrived, left, began, raw_info = str(row["Site & Ref No."]), str(row["Arrived On Site"]), str(row["Left Site"]), str(row["Began Journey"]), str(row["Original Row Info"])
                
                if began == "" and index > 0: began = processed_data[-1]["left"]
                travel_time, work_time = calc_hours(began, arrived), calc_hours(arrived, left)
                
                processed_data.append({"raw_info": raw_info, "site": site, "began": began, "arrived": arrived, "left": left, "work": work_time, "travel": travel_time})
                
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
                    <div class="header-left"><strong>Engineer:</strong> MARK GREEN (MGR)<br><strong>Network (Catering Engineers) Ltd</strong></div>
                    <div class="header-center"><strong>Week End Date:</strong> {final_date}<br><strong>Week:</strong> {final_week}</div>
                    <div class="header-right"><strong>On-call:</strong> {on_call_status}</div>
                </div>
                <table>
                    <thead><tr><th style="width: 22%;">Site & Ref No.</th><th>Multiple Jobs</th><th>Job Number</th><th>Began Journey</th><th>Arrived On Site</th><th>Left Site</th><th>Hours Worked</th><th>Rest Break (min)</th><th>Travel Time</th><th>TOTAL Hours</th></tr></thead>
                    <tbody>
            """
            
            grand_total = 0
            for row in processed_data:
                if re.match(r"^[A-Z]\s*\d{1,2}", str(row['raw_info'])):
                     day_letter = str(row['raw_info'])[0]
                     day_map = {"M": "MONDAY", "T": "TUESDAY", "W": "WEDNESDAY", "F": "FRIDAY", "S": "SAT/SUN"}
                     day_display = day_map.get(day_letter, day_letter)
                     html_content += f'<tr><td colspan="10" class="day-row">{day_display}</td></tr>'
                     
                day_total = row['work'] + row['travel']
                grand_total += day_total
                html_content += f"<tr><td>{row['site']}</td><td></td><td></td><td>{row['began']}</td><td>{row['arrived']}</td><td>{row['left']}</td><td>{row['work']:.2f}</td><td></td><td>{row['travel']:.2f}</td><td></td></tr>"
            
            html_content += f"</tbody></table><div style='margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px;'>Weekly Total Hours: {grand_total:.2f}</div></body></html>"
            
            st.success("PDF Generated Successfully!")
            st.download_button(label="Download Timesheet PDF", data=HTML(string=html_content).write_pdf(), file_name=f"MGR_Timesheet_Week_{final_week}.pdf", mime="application/pdf")
