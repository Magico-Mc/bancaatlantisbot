import logging
logging.basicConfig(level=logging.DEBUG)
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception as e:
    print("Import failed:", e)
    raise

cred_path = "gspread_credentials.json"
sheet_id = "1QOHyoaLiliIIlLWvHxoHsZfpSBEDC9CskyyC2IgRlck"

scopes = ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(cred_path, scopes=scopes)
client = gspread.authorize(creds)
wb = client.open_by_key(sheet_id)

print("Worksheets in workbook:")
for ws in wb.worksheets():
    print(f"  - {ws.title}")

print("\nTrying to add PIVA worksheet...")
try:
    ws = wb.add_worksheet(title="PIVA", rows=1000, cols=20)
    print(f"Created worksheet: {ws.title}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    
print("\nTrying to get PIVA worksheet...")
try:
    ws = wb.worksheet("PIVA")
    print(f"Found worksheet: {ws.title}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
