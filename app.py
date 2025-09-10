# app.py
# Phase-2 Streamlit app: login -> payment (₹199) -> assessment -> Gemini report -> PDF -> store -> email -> admin
import streamlit as st
import json
from datetime import datetime
from fpdf import FPDF
from io import BytesIO
import base64
import os

# External SDKs
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore, storage as fb_storage
import razorpay
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

# ---------- Configuration & Secrets ----------
st.set_page_config(page_title="TAICC AI Readiness", layout="wide", page_icon="🤖")

# Load secrets from Streamlit secrets (template provided in repo). Replace placeholders with real values.
# Required secrets keys (see .streamlit/secrets.toml template):
# st.secrets["GEMINI"]["api_key"]
# st.secrets["RAZORPAY"]["key_id"], st.secrets["RAZORPAY"]["key_secret"]
# st.secrets["SENDGRID"]["api_key"]
# st.secrets["ADMIN"]["emails"] -> list of admin emails (optional)
# st.secrets["FIREBASE_SERVICE_ACCOUNT_JSON"] -> a JSON-string of the Firebase service account (or set GOOGLE_APPLICATION_CREDENTIALS in Cloud)
#
# Important security note: do NOT commit real credentials into a public repo.

# Validate presence of minimal secrets so we can show helpful errors
def require_secret(path):
    try:
        # path like ["GEMINI","api_key"]
        val = st.secrets
        for p in path:
            val = val[p]
        return True
    except Exception:
        return False

# ---------- Initialize Gemini ----------
if require_secret(["GEMINI", "api_key"]):
    genai.configure(api_key=st.secrets["GEMINI"]["api_key"])
else:
    st.warning("GEMINI API key missing in .streamlit/secrets.toml (add GEMINI.api_key) — report generation will fail until added.")

# ---------- Initialize Firebase Admin (Firestore + Storage) ----------
firebase_initialized = False
if "FIREBASE_SERVICE_ACCOUNT_JSON" in st.secrets:
    try:
        sa_json = st.secrets["FIREBASE_SERVICE_ACCOUNT_JSON"]
        # sa_json should be a JSON-string (the service account key file contents)
        cred = credentials.Certificate(json.loads(sa_json))
        firebase_admin.initialize_app(cred, {
            # set default storage bucket if provided
            'storageBucket': st.secrets.get("firebase", {}).get("storageBucket")
        })
        db = firestore.client()
        bucket = fb_storage.bucket()
        firebase_initialized = True
    except Exception as e:
        st.error(f"Failed to initialize Firebase Admin: {e}")
else:
    st.info("Firebase service account JSON not found in secrets. Firestore/Storage will be unavailable until configured.")

# ---------- Initialize Razorpay client ----------
razorpay_client = None
if require_secret(["RAZORPAY","key_id"]) and require_secret(["RAZORPAY","key_secret"]):
    try:
        razorpay_client = razorpay.Client(auth=(st.secrets["RAZORPAY"]["key_id"], st.secrets["RAZORPAY"]["key_secret"]))
    except Exception as e:
        st.error(f"Razorpay init error: {e}")
else:
    st.info("Razorpay keys missing in secrets. Payment will not work until configured.")

# ---------- SendGrid ----------
sendgrid_client = None
if require_secret(["SENDGRID","api_key"]):
    sendgrid_client = SendGridAPIClient(st.secrets["SENDGRID"]["api_key"])

# ---------- Load questions JSON ----------
QUESTIONS_FILE = "questions_full.json"
if os.path.exists(QUESTIONS_FILE):
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        QUESTIONS = json.load(f)
else:
    st.error(f"{QUESTIONS_FILE} not found in repo. Add questions_full.json.")
    QUESTIONS = {}

# ---------- Utility functions ----------
SCORE_MAP = {"Not at all":1, "Slightly":2, "Moderately":3, "Very":4, "Fully":5}
READINESS_LEVELS = [
    (0.0,1.0,"Beginner"),
    (1.1,2.0,"Emerging"),
    (2.1,3.0,"Established"),
    (3.1,4.0,"Advanced"),
    (4.1,5.0,"AI Leader")
]

def determine_maturity(avg):
    for low, high, label in READINESS_LEVELS:
        if low <= avg <= high:
            return label
    return "Undefined"

def generate_professional_summary(avg_score, maturity):
    # Ensure gemini present
    if not require_secret(["GEMINI","api_key"]):
        return "Gemini API key missing — cannot generate the professional report."
    prompt = f"""
You are an expert AI consultant preparing a professional AI readiness assessment report for a business.
Overall numeric score: {avg_score}
Maturity level: {maturity}

Provide:
1) A short executive summary (2-4 sentences).
2) Key weaknesses or challenges typically faced at this maturity level (bullet list).
3) Practical, prioritized recommendations (3–6 items) with short justifications.
4) A concluding call-to-action encouraging partnership with TAICC, including next steps (contact).
Write in professional, business-friendly tone suitable for C-suite readers.
"""
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Gemini generation failed: {e}"

def build_pdf_bytes(user_data, maturity, report_text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "TAICC AI Readiness Assessment Report", ln=True, align="C")
    pdf.ln(8)

    pdf.set_font("Arial", size=11)
    pdf.cell(0, 8, f"Name: {user_data.get('Name','')}", ln=True)
    pdf.cell(0, 8, f"Company: {user_data.get('Company','')}", ln=True)
    pdf.cell(0, 8, f"Email: {user_data.get('Email','')}", ln=True)
    pdf.cell(0, 8, f"Phone: {user_data.get('Phone','')}", ln=True)
    pdf.cell(0, 8, f"Domain: {user_data.get('Domain','')}", ln=True)
    pdf.cell(0, 8, f"Tier: {user_data.get('Tier','')}", ln=True)
    pdf.ln(6)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, f"AI Maturity Level: {maturity}", ln=True)
    pdf.ln(6)

    pdf.set_font("Arial", size=11)
    # wrap the report.
    # Replace unsupported characters
    safe_text = report_text.encode('latin-1', 'replace').decode('latin-1')
    pdf.multi_cell(0, 7, safe_text)
    pdf.ln(8)
    pdf.set_font("Arial", 'I', 9)
    pdf.cell(0, 8, "Report generated by TAICC AI Readiness Assessment", ln=True, align="C")

    return pdf.output(dest="S").encode("latin-1")

def send_email_with_attachment(to_email, subject, html_content, pdf_bytes, filename="TAICC_AI_Readiness_Report.pdf"):
    if not sendgrid_client:
        st.warning("SendGrid not configured; cannot send email.")
        return False
    try:
        encoded = base64.b64encode(pdf_bytes).decode()
        attachment = Attachment()
        attachment.file_content = FileContent(encoded)
        attachment.file_type = FileType('application/pdf')
        attachment.file_name = FileName(filename)
        attachment.disposition = Disposition('attachment')
        message = Mail(from_email="reports@taicc.co", to_emails=to_email, subject=subject, html_content=html_content)
        message.attachment = attachment
        resp = sendgrid_client.send(message)
        return resp.status_code in (200,202)
    except Exception as e:
        st.error(f"SendGrid send failed: {e}")
        return False

def upload_pdf_to_storage(pdf_bytes, email):
    if not firebase_initialized:
        st.warning("Firebase not configured; cannot upload PDF.")
        return None
    try:
        ts = datetime.utcnow().isoformat().replace(":", "-")
        path = f"reports/{email}_{ts}.pdf"
        blob = bucket.blob(path)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        # Make public link (or use signed URL in prod)
        public_url = f"https://storage.googleapis.com/{bucket.name}/{path}"
        return public_url
    except Exception as e:
        st.error(f"PDF upload failed: {e}")
        return None

def save_submission_to_firestore(user_data, avg_score, maturity, pdf_link, payment_info):
    if not firebase_initialized:
        st.warning("Firebase not configured; cannot save submission.")
        return None
    try:
        doc = {
            "user": user_data,
            "score": avg_score,
            "maturity": maturity,
            "pdf_link": pdf_link,
            "payment": payment_info,
            "created_at": datetime.utcnow().isoformat()
        }
        ref = db.collection("submissions").add(doc)
        return ref
    except Exception as e:
        st.error(f"Saving submission failed: {e}")
        return None

# ---------- UI Pages ----------
if "page" not in st.session_state:
    st.session_state.page = "home"
    st.session_state.user = {}
    st.session_state.payment_link_id = None
    st.session_state.payment_done = False
    st.session_state.answers = {}

# Simple top nav
st.sidebar.title("TAICC")
st.sidebar.write("AI Readiness Assessment")

pages = ["Home","Assessment","Results","Admin"]
choice = st.sidebar.selectbox("Go to", pages)

# ---- Home / Login / Payment ----
if choice == "Home":
    st.title("🚀 TAICC AI Readiness Assessment")
    st.markdown("Professional AI Readiness Assessment. Pay ₹199 to begin.")
    with st.form("user_form"):
        name = st.text_input("Full name")
        company = st.text_input("Company name")
        email = st.text_input("Email")
        phone = st.text_input("Phone")
        domain = st.selectbox("Select domain", list(QUESTIONS.keys()))
        # choose tier list from domain
        tier_list = list(QUESTIONS.get(domain, {}).keys())
        if not tier_list:
            tier_list = ["Tier 1","Tier 2","Tier 3","Tier 4","Tier 5"]
        tier = st.selectbox("Select tier", tier_list)
        submitted = st.form_submit_button("Continue to payment")

    if submitted:
        if not email:
            st.error("Please enter email.")
        else:
            st.session_state.user = {"Name":name,"Company":company,"Email":email,"Phone":phone,"Domain":domain,"Tier":tier}
            # create Razorpay Payment Link for ₹199
            if not razorpay_client:
                st.error("Razorpay not configured in secrets.")
            else:
                try:
                    amount_paise = 19900  # ₹199
                    payload = {
                        "amount": amount_paise,
                        "currency": "INR",
                        "accept_partial": False,
                        "description": f"TAICC AI Readiness Assessment - {domain} - {tier}",
                        "customer": {
                            "name": name or "Participant",
                            "email": email,
                            "contact": phone or ""
                        },
                        "notify": {
                            "sms": False,
                            "email": True
                        },
                        "reminder_enable": True,
                        "callback_url": "",  # optional: your callback URL
                        "callback_method": "get"
                    }
                    resp = razorpay_client.payment_link.create(payload)
                    link = resp.get("short_url") or resp.get("long_url")
                    link_id = resp.get("id")
                    st.session_state.payment_link_id = link_id
                    st.success("Payment link created. Click to pay and complete your assessment.")
                    st.markdown(f"[Pay ₹199 now]({link}){{:target=\"_blank\"}}", unsafe_allow_html=True)
                    st.info("After completing payment in the new tab, come back here and click 'Verify Payment' below.")
                    if st.button("Verify Payment"):
                        if not link_id:
                            st.error("No payment link created.")
                        else:
                            pl = razorpay_client.payment_link.fetch(link_id)
                            status = pl.get("status")
                            if status == "paid":
                                st.session_state.payment_done = True
                                st.success("Payment verified! You may now proceed to the Assessment page (select Assessment in sidebar).")
                            else:
                                st.warning(f"Payment not completed yet (status: {status}). If you just paid, wait a few seconds and try Verify Payment again.")
                except Exception as e:
                    st.error(f"Failed to create payment link: {e}")

# ---- Assessment Page ----
elif choice == "Assessment":
    if not st.session_state.payment_done:
        st.warning("Please complete payment first on the Home page.")
    else:
        st.title("🧠 AI Readiness Assessment")
        user = st.session_state.user
        domain = user.get("Domain")
        tier = user.get("Tier")
        st.markdown(f"**Domain:** {domain}   •   **Tier:** {tier}")
        qs = QUESTIONS.get(domain, {}).get(tier, [])
        if not qs:
            st.error("Questions not found for selected domain/tier.")
        else:
            with st.form("questions_form"):
                answers = {}
                for idx, q in enumerate(qs):
                    key = f"q{idx}"
                    ans = st.radio(q, list(SCORE_MAP.keys()), key=key, index=2)
                    answers[str(idx)] = SCORE_MAP[ans]
                submit_q = st.form_submit_button("Submit Assessment")
            if submit_q:
                st.session_state.answers = answers
                st.success("Assessment submitted. Go to Results page to see your report.")
                st.session_state.page = "results"
                # navigate to results via sidebar selection
                st.experimental_rerun()

# ---- Results Page ----
elif choice == "Results":
    if not st.session_state.answers:
        st.info("No answers found. Complete the assessment first.")
    else:
        st.title("📊 Results")
        answers = list(st.session_state.answers.values())
        avg = round(sum(answers)/len(answers),2) if answers else 0.0
        maturity = determine_maturity(avg)
        st.metric("Overall Score (avg)", avg)
        st.success(f"AI Maturity Level: {maturity}")

        # Generate Gemini report
        with st.spinner("Generating professional report (Gemini)..."):
            report_text = generate_professional_summary(avg, maturity)
        st.markdown("### Professional Report")
        st.write(report_text)

        # Build PDF and show download button
        user = st.session_state.user
        pdf_bytes = build_pdf_bytes(user, maturity, report_text)
        st.download_button("📥 Download PDF Report", data=pdf_bytes, file_name="TAICC_AI_Readiness_Report.pdf", mime="application/pdf")

        # Upload PDF to Storage and save submission + send email
        if st.button("Save & Email Report"):
            with st.spinner("Uploading PDF, saving submission and sending email..."):
                pdf_link = upload_pdf_to_storage(pdf_bytes, user.get("Email","unknown"))
                payment_info = {"method":"Razorpay PaymentLink", "link_id": st.session_state.payment_link_id, "status":"paid" if st.session_state.payment_done else "unknown"}
                save_submission_to_firestore(user, avg, maturity, pdf_link, payment_info)
                email_ok = False
                try:
                    email_ok = send_email_with_attachment(user.get("Email"), "Your TAICC AI Readiness Report", "<p>Attached is your TAICC AI Readiness report.</p>", pdf_bytes)
                except Exception as e:
                    st.error(f"Email sending failed: {e}")
                if email_ok:
                    st.success("Report emailed & submission saved.")
                else:
                    st.warning("Report saved but email failed (SendGrid not configured or failed).")

# ---- Admin Page ----
elif choice == "Admin":
    st.title("🔐 Admin Dashboard")
    admin_emails = st.secrets.get("ADMIN", {}).get("emails", [])
    admin_email = st.text_input("Enter admin email to view submissions")
    if admin_email and admin_email in admin_emails:
        if not firebase_initialized:
            st.error("Firebase not configured — admin cannot view submissions.")
        else:
            # Fetch submissions
            docs = db.collection("submissions").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
            rows = []
            for d in docs:
                data = d.to_dict()
                rows.append(data)
            if rows:
                st.write(f"Total submissions: {len(rows)}")
                st.dataframe(rows)
                # CSV export
                import pandas as pd, base64
                df = pd.json_normalize(rows)
                csv = df.to_csv(index=False)
                b64 = base64.b64encode(csv.encode()).decode()
                st.markdown(f"[Download CSV](data:text/csv;base64,{b64})", unsafe_allow_html=True)
            else:
                st.info("No submissions yet.")
    else:
        st.warning("Provide an admin email present in secrets.ADMIN.emails to view submissions.")
