from googleapiclient.discovery import build
from google.oauth2 import service_account
from io import BytesIO
from googleapiclient.http import MediaIoBaseUpload
import base64

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SERVICE_ACCOUNT_INFO = st.secrets["google_drive"]["credentials_json"]

credentials = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO, scopes=SCOPES
)
drive_service = build("drive", "v3", credentials=credentials)

def upload_pdf_to_drive(file_bytes, filename):
    file_metadata = {"name": filename}
    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype="application/pdf")
    file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = file.get("id")
    link = f"https://drive.google.com/uc?id={file_id}&export=download"
    return link
