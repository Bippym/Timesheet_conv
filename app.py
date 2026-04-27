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

# Helper function to calculate decimal hours
def calc_hours(start_str, end_str):
    if not start_str or not end_str or pd.isna(start_str) or pd.isna(end_str):
        return 0.0
    start_str = str(start_str).strip()
    end_str = str(end_str).strip()
    if not start_str or not end_str: return 0.0
    
    fmt = "%H:%M"
    try:
        # Handle formats like "6:30" or "06:30"
        if len(start_str.split(":")[0]) == 1: start_str = "0" + start_str
        if len(end_str.split(":")[0]) == 1: end_str = "0" + end_str
        
        tdelta = datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)
        # Handle crossing midnight (just in case)
        if tdelta.days < 0:
            tdelta = timedelta(days=0, seconds=tdelta.seconds, microseconds=tdelta.microseconds)
        return round(tdelta.total_seconds() / 3600, 2)
    except:
        return 0.0

# --- 1. UPLOADER & EXTRACTION ---
uploaded_file = st.file_uploader("Upload Work-Style Timesheet (PDF)", type=["pdf"])

if uploaded_file is not None:
    extracted_data = []
    week_ending_str = ""
    week_number = "18" # Default fallback
    
    with st.spinner("Extracting data from PDF..."):
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                
                # Try to find Week Ending Date and Week Number
                if "Week Ending:" in text:
                    match = re.search(r"Week Ending:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text)
                    if match: week_ending_str = match.group(1)
                if "Week:" in text:
                    match = re.search(r"Week:\s*(\d+)", text)
                    if match: week_number = match.group(1)

                # Extract Tables
                tables = page.extract_tables()
                for table in tables:
                    # Look for the main data table
                    if table and len(table[0]) >= 7 and "Site" in str(table[0][0]):
                        for row in table[1:]: # Skip header
                            if not row or not row[0]: continue
                            
                            site_raw = str(row[0]).replace("\n", " ")
                            # Skip summary rows
                            if "Wrkd:" in site_raw or "Day:" in site_raw or "Prod:" in site_raw:
                                continue
                                
                            # Extract Began, Arrived, Left (Indices based on your provided PDF structure)
                            # Site(0), Miles(1), Begin Journey(2), Travel Time(3), Arrived(4), On Site(5), Left Site(6)
                            try:
                                began = str(row[2]).replace("\n", "").strip() if row[2] else ""
                                arrived = str(row[4]).replace("\n", "").strip() if row[4] else ""
                                left = str(row[6]).replace("\n", "").strip() if row[6] else ""
                                
                                # Clean up site name (Remove the "M 27" prefix for cleaner look)
                                site_clean = re.sub(r"^[A-Z]{1,3}\s*\d{1,2}\s*", "", site_raw)
                                
                                # Basic check to ensure it's a time row
                                if ":" in began or ":" in arrived or ":" in left:
                                    extracted_data.append({
                                        "Original Row Info": site_raw, # Kept for date tracking
                                        "Site & Ref No.": site_clean,
                                        "Began Journey": began,
                                        "Arrived On Site": arrived,
                                        "Left Site": left
                                    })
                            except IndexError:
                                pass

    # Load data into editable dataframe
    if extracted_data:
        st.success("Data extracted successfully! Please review and fix any missing days below.")
        
        col1, col2 = st.columns(2)
        with col1:
            final_date = st.text_input("Week End Date", value=week_ending_str)
        with col2:
            final_week = st.text_input("Week Number", value=week_number)
            
        df = pd.DataFrame(extracted_data)
        edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
        
        # --- 2. LOGIC ENGINE & PDF GENERATION ---
        if st.button("Apply Rules & Generate PDF", type="primary"):
            processed_data = []
            has_weekend = False
            
            # Simple check for weekend entries in the raw site string
            for site_str in df["Original Row Info"].astype(str):
                if " S " in site_str or " S" in site_str or "SAT" in site_str or "SUN" in site_str:
                    has_weekend = True
                    
            on_call_status = "Yes" if has_weekend else "No"
            
            # Apply Timeline Continuity
            for index, row in edited_df.iterrows():
                site = str(row["Site & Ref No."])
                arrived = str(row["Arrived On Site"])
                left = str(row["Left Site"])
                began = str(row["Began Journey"])
                raw_info = str(row["Original Row Info"])
                
                # Continuity Rule
                if began == "" and index > 0:
                    began = processed_data[-1]["left"]
                    
                travel_time = calc_hours(began, arrived)
                work_time = calc_hours(arrived, left)
                
                processed_data.append({
                    "raw_info": raw_info,
                    "site": site,
                    "began": began,
                    "arrived": arrived,
                    "left": left,
                    "work": work_time,
                    "travel": travel_time
                })
                
            # HTML Template Generation
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
                    <div class="header-left">
                        <strong>Engineer:</strong> MARK GREEN (MGR)<br>
                        <strong>Network (Catering Engineers) Ltd</strong>
                    </div>
                    <div class="header-center">
                        <strong>Week End Date:</strong> {final_date}<br>
                        <strong>Week:</strong> {final_week}
                    </div>
                    <div class="header-right">
                        <strong>On-call:</strong> {on_call_status}
                    </div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 22%;">Site & Ref No.</th>
                            <th>Multiple Jobs</th>
                            <th>Job Number</th>
                            <th>Began Journey</th>
                            <th>Arrived On Site</th>
                            <th>Left Site</th>
                            <th>Hours Worked</th>
                            <th>Rest Break (min)</th>
                            <th>Travel Time</th>
                            <th>TOTAL Hours</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            # Render Rows
            grand_total = 0
            for row in processed_data:
                # Add a dummy day header if the row contains a day indicator
                if re.match(r"^[A-Z]{1,3}\s*\d{1,2}", str(row['raw_info'])):
                     day_indicator = re.match(r"^([A-Z]{1,3}\s*\d{1,2})", str(row['raw_info'])).group(1)
                     html_content += f'<tr><td colspan="10" class="day-row">{day_indicator}</td></tr>'
                     
                day_total = row['work'] + row['travel']
                grand_total += day_total
                
                html_content += f"""
                    <tr>
                        <td>{row['site']}</td>
                        <td></td>
                        <td></td>
                        <td>{row['began']}</td>
                        <td>{row['arrived']}</td>
                        <td>{row['left']}</td>
                        <td>{row['work']:.2f}</td>
                        <td></td>
                        <td>{row['travel']:.2f}</td>
                        <td></td>
                    </tr>
                """
            
            html_content += f"""
                    </tbody>
                </table>
                <div style="margin-top: 20px; font-weight: bold; text-align: right; border-top: 1px solid #000; padding-top: 5px;">
                    Weekly Total Hours: {grand_total:.2f}
                </div>
            </body>
            </html>
            """
            
            # Generate PDF
            pdf_bytes = HTML(string=html_content).write_pdf()
            
            st.success("PDF Generated Successfully!")
            st.download_button(
                label="Download Timesheet PDF",
                data=pdf_bytes,
                file_name=f"MGR_Timesheet_Week_{final_week}.pdf",
                mime="application/pdf"
            )
    else:
        st.warning("Could not extract table data. Ensure this is the correct Work-Style PDF.")
