import razorpay
import streamlit as st

client = razorpay.Client(
    auth=(st.secrets["RAZORPAY"]["key_id"], st.secrets["RAZORPAY"]["key_secret"])
)

def create_payment(amount_inr=199):
    amount = amount_inr * 100  # Razorpay uses paise
    order = client.order.create({"amount": amount, "currency": "INR", "payment_capture": "1"})
    return order
