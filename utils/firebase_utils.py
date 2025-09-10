import pyrebase
from datetime import datetime

firebase = pyrebase.initialize_app({
    "apiKey": st.secrets["firebase"]["apiKey"],
    "authDomain": st.secrets["firebase"]["authDomain"],
    "projectId": st.secrets["firebase"]["projectId"],
    "storageBucket": st.secrets["firebase"]["storageBucket"],
    "messagingSenderId": st.secrets["firebase"]["messagingSenderId"],
    "appId": st.secrets["firebase"]["appId"]
})

auth = firebase.auth()
db = firebase.database()

def save_submission(user_data, score, maturity, pdf_link, payment_status):
    data = {
        "user": user_data,
        "score": score,
        "maturity": maturity,
        "pdf_link": pdf_link,
        "payment_status": payment_status,
        "timestamp": datetime.now().isoformat()
    }
    db.child("submissions").push(data)
