import streamlit as st
import json
from fpdf import FPDF
import google.generativeai as genai
from utils import firebase_utils, drive_utils, payment_utils

# Configure Gemini API
genai.configure(api_key=st.secrets["GEMINI"]["api_key"])

st.title("TAICC AI Readiness Assessment")

# ---- Step 1: User Login ----
email = st.text_input("Enter your email")
if st.button("Send OTP"):
    st.success(f"OTP sent to {email}")
    st.session_state.user_email = email
    st.session_state.payment_done = False

# ---- Step 2: Payment ----
if st.session_state.get("user_email") and not st.session_state.get("payment_done"):
    order = payment_utils.create_payment(amount_inr=199)
    st.write("Pay ₹199 to start your assessment")
    if st.button("Pay Now"):
        st.session_state.payment_done = True
        st.success("Payment successful!")

# ---- Step 3: Assessment ----
if st.session_state.get("payment_done"):
    with open("questions_full.json") as f:
        questions = json.load(f)
    
    domain = st.selectbox("Select Domain", list(questions.keys()))
    tier = st.selectbox("Select Tier", list(questions[domain].keys()))
    
    user_answers = {}
    for q in questions[domain][tier]:
        ans = st.slider(q, min_value=1, max_value=5)
        user_answers[q] = ans
    
    if st.button("Submit Assessment"):
        avg_score = sum(user_answers.values())/len(user_answers)
        # Original maturity levels
        if avg_score <= 1.5: maturity = "Beginner"
        elif avg_score <= 2.5: maturity = "Emerging"
        elif avg_score <= 3.5: maturity = "Established"
        elif avg_score <= 4.5: maturity = "Advanced"
        else: maturity = "AI Leader"
        
        # Gemini Professional Report
        prompt = f"""
        You are an expert AI consultant. Analyze this AI readiness score: {avg_score}.
        Provide:
        1. Short summary paragraph for on-screen display
        2. Weaknesses/challenges
        3. Practical recommendations
        4. Concluding call to action encouraging contact with TAICC
        """
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        report_text = response.text.strip()
        sections = report_text.split("\n")
        
        st.success(f"Your AI Maturity Level: {maturity}")
        st.markdown(sections[0])
        
        # PDF generation
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "TAICC AI Readiness Assessment Report", ln=True, align="C")
        pdf.ln(10)
        pdf.set_font("Arial", size=12)
        pdf.cell(0,8,f"Email: {email}", ln=True)
        pdf.cell(0,8,f"Domain: {domain}", ln=True)
        pdf.cell(0,8,f"Tier: {tier}", ln=True)
        pdf.cell(0,8,f"AI Maturity Level: {maturity}", ln=True)
        pdf.ln(10)
        for s in sections[1:]:
            pdf.multi_cell(0,8,s.encode('latin-1','replace').decode('latin-1'))
            pdf.ln(5)
        
        pdf_bytes = pdf.output(dest="S").encode("latin-1")
        st.download_button("Download PDF", data=pdf_bytes, file_name="TAICC_AI_Report.pdf", mime="application/pdf")
        
        # Upload PDF to Google Drive
        pdf_link = drive_utils.upload_pdf_to_drive(pdf_bytes, f"{email}_AI_Report.pdf")
        
        # Save submission to Firebase
        firebase_utils.save_submission({"email": email, "domain": domain, "tier": tier},
                                       avg_score, maturity, pdf_link, payment_status="Success")
