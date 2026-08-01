"""
Test script for Google Sheets integration.
Run this after placing your service account JSON as `gspread_credentials.json` (or set env GSPREAD_CREDENTIALS)
and ensuring `sheet_id.txt` contains the spreadsheet id (already created).

It will try to append a single test row to each sheet used by the bot.
"""
import os
import logging
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception as e:
    print("gspread or google-auth not installed:", e)
    raise

logging.basicConfig(level=logging.INFO)

cred_path = os.getenv('GSPREAD_CREDENTIALS') or 'gspread_credentials.json'
if not os.path.exists(cred_path):
    print(f"Credentials file not found at {cred_path}. Place your service account JSON there or set GSPREAD_CREDENTIALS env var.")
    raise SystemExit(1)

sheet_id = os.getenv('GOOGLE_SHEET_ID')
if not sheet_id and os.path.exists('sheet_id.txt'):
    with open('sheet_id.txt','r',encoding='utf-8') as f:
        sheet_id = f.read().strip()
if not sheet_id:
    print('No sheet id provided. Put it in sheet_id.txt or set GOOGLE_SHEET_ID env var.')
    raise SystemExit(1)

scopes = ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(cred_path, scopes=scopes)
client = gspread.authorize(creds)
wb = client.open_by_key(sheet_id)

sheets = [
    ('Versamenti', ['TEST_DIP', 'TEST_CLIENTE', '100', 'Provenienza test', 'TEST_DATE']),
    ('Bonifici', ['TEST_DIP', 'TEST_MITT', 'TEST_BENEF', '200', 'Commissioni: 0 | Causale test', 'TEST_DATE']),
    ('Assegni', ['TEST_DIP', 'TEST_MITT', 'TEST_BENEF', '150', 'Causale assegno', 'TEST_DATE']),
    ('PIva', ['TEST_DIP', 'TEST_DIRETTORE', 'NOME_PIVA', 'TEST_DATE']),
    ('Upgrade Carta', ['TEST_DIP', 'TEST_CLIENTE', 'Conto corallo', 'TEST_DATE']),
    ('Congedi', ['TEST_DIP', '01/01/2026', '02/01/2026', 'Motivo test'])
]

for name, row in sheets:
    try:
        try:
            ws = wb.worksheet(name)
        except Exception:
            try:
                ws = wb.add_worksheet(title=name, rows=1000, cols=20)
            except Exception:
                # If add_worksheet fails because the sheet already exists,
                # try to fetch it again; otherwise raise.
                try:
                    ws = wb.worksheet(name)
                except Exception as e:
                    raise
        ws.append_row(row, value_input_option='USER_ENTERED')
        print(f"Appended test row to {name}")
    except Exception as e:
        print(f"Failed to append to {name}: {e}")

print("\nSide-by-side append verification for Versamenti...")
try:
    from BancaAtlantisBot import append_row_side_by_side, init_sheets, sheets_read_records, sheets_count_modules
    init_sheets()
    import datetime as _dt
    lr, rr = append_row_side_by_side('Versamenti', ['TEST_DIP_DUAL','TEST_CLIENTE_DUAL','123','Prova','0', _dt.date.today().isoformat()], right_start_col=9)
    print(f"Side-by-side rows appended at right={rr} (Archivio aggiornato)")
    print("Read totali (first 2):", sheets_read_records("Versamenti","totali")[:2])
    print("Read settimanali (first 2):", sheets_read_records("Versamenti","settimanali")[:2])
    print("Count moduli Totali:", sheets_count_modules("totali"))
    print("Count moduli Settimanali:", sheets_count_modules("settimanali"))
except Exception as e:
    print(f"Side-by-side append failed or not available: {e}")
