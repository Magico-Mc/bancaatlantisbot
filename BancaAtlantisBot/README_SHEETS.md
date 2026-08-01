Setup Google Sheets integration for `BancaAtlantisBot`

1) Create a Google Cloud service account
   - Console: IAM & Admin → Service Accounts → Create Service Account
   - Grant no special IAM roles in the console (the key + sheet share is enough)
   - Create and download a JSON key. Save it as `gspread_credentials.json` in the bot folder,
     or store its path in the environment variable `GSPREAD_CREDENTIALS`.

2) Share the spreadsheet with the service account
   - Open your Google Sheet (the one with tabs: Versamenti, Bonifici, P.IVA, Assegni, Upgrade Carta, Congedi)
   - Click "Share" and add the service account email (e.g. my-sa@project.iam.gserviceaccount.com) with Editor permissions.

3) Provide the spreadsheet ID
   - The file `sheet_id.txt` was created with the id you provided. To override, set env var `GOOGLE_SHEET_ID`.

4) Install dependencies

```powershell
python -m pip install -r requirements.txt
```

5) Test the integration
   - After steps above, run:

```powershell
python test_sheets.py
```

   - This will append a test row to each sheet (it will create the worksheet if missing).

6) Run the bot
   - Start the bot as usual. When users submit forms, the bot will attempt to append rows to the relevant sheets.

Notes
- If you prefer the credentials file to be named differently, set `GSPREAD_CREDENTIALS` to its path.
- The bot logs warnings if Sheets integration is not available; it will continue saving locally to `bot_data.json`.
