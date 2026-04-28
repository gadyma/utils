import os
import csv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account


def replace_sheet_tab_with_csv(api_key_file_path, csv_file_path, spreadsheet_id, sheet_name):
    """
    Replace the contents of a specific tab within a Google Sheets spreadsheet
    with the contents of a local CSV file. Keeps the same spreadsheet link.
    """
    # Expand ~ and environment vars
    api_key_file_path = os.path.expanduser(os.path.expandvars(api_key_file_path))
    csv_file_path = os.path.expanduser(os.path.expandvars(csv_file_path))

    if not os.path.exists(api_key_file_path):
        raise FileNotFoundError(f"API key file not found: {api_key_file_path}")
    if not os.path.exists(csv_file_path):
        raise FileNotFoundError(f"CSV file not found: {csv_file_path}")

    credentials = service_account.Credentials.from_service_account_file(
        api_key_file_path,
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets'
        ]
    )
    sheets_service = build('sheets', 'v4', credentials=credentials)

    # Ensure the sheet/tab exists
    try:
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise PermissionError(
                "Access denied. Share the spreadsheet with the service account email in your JSON."
            ) from e
        raise

    sheets = spreadsheet.get('sheets', [])
    target_sheet = next(
        (s for s in sheets if s.get('properties', {}).get('title') == sheet_name),
        None
    )
    if not target_sheet:
        raise ValueError(f"Sheet/tab named '{sheet_name}' not found in spreadsheet")

    # Read CSV data
    with open(csv_file_path, 'r', newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        values = [row for row in reader]

    # Clear the sheet and write values starting at A1
    write_range = f"{sheet_name}!A1"

    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=sheet_name,
        body={}
    ).execute()

    if values:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=write_range,
            valueInputOption='RAW',
            body={'values': values}
        ).execute()

    print(f"Sheet '{sheet_name}' successfully replaced from CSV. Rows written: {len(values)}")


if __name__ == "__main__":
    # Example configuration
    API_KEY_FILE = os.path.expanduser(os.path.expandvars("~/service.json"))
    LOCAL_FILE = os.path.expanduser(os.path.expandvars("~/Downloads/Findings.csv"))
    SPREADSHEET_ID = "1OeQWnN0H77iQ5ekOyIafQLav8Ivp_viq"
    SHEETNAME = "Mysheet"

    try:
        replace_sheet_tab_with_csv(API_KEY_FILE, LOCAL_FILE, SPREADSHEET_ID, SHEETNAME)
    except Exception as e:
        print(f"Error: {e}")