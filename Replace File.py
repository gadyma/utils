import os
import mimetypes
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
import csv

def replace_google_drive_file(api_key_file_path, local_file_path, drive_file_id):
    """
    Replace a file in Google Drive with a local file while keeping the same URL.
    
    Args:
        api_key_file_path (str): Path to your Google service account JSON key file
        local_file_path (str): Path to the local file you want to upload
        drive_file_id (str): The Google Drive file ID of the file to replace
    
    Returns:
        dict: Response from Google Drive API with file information
        
    Raises:
        FileNotFoundError: If local file or API key file doesn't exist
        Exception: For other API errors
    """
    
    # Expand ~ and environment vars

    # Check if files exist
    if not os.path.exists(api_key_file_path):
        raise FileNotFoundError(f"API key file not found: {api_key_file_path}")
    
    if not os.path.exists(local_file_path):
        raise FileNotFoundError(f"Local file not found: {local_file_path}")
    
    try:
        # Set up credentials using service account
        credentials = service_account.Credentials.from_service_account_file(
            api_key_file_path,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        # Build the Drive service
        drive_service = build('drive', 'v3', credentials=credentials)
        service_account_email = getattr(credentials, 'service_account_email', None)
        
        # Resolve shortcuts to their target file ID
        file_id_to_update, _ = _resolve_actual_file_id(drive_service, drive_file_id)
        
        # Determine MIME type automatically
        mime_type, _ = mimetypes.guess_type(local_file_path)
        if mime_type is None:
            # Default to binary if can't determine
            mime_type = 'application/octet-stream'
        
        # Create media upload object
        media = MediaFileUpload(
            local_file_path,
            mimetype=mime_type,
            resumable=True
        )
        
        # Update the file in Google Drive
        # This keeps the same file ID and URL
        updated_file = drive_service.files().update(
            fileId=file_id_to_update,
            media_body=media,
            supportsAllDrives=True
        ).execute()
        
        print("File replaced.")
        print(f"ID: {updated_file['id']}")
        print(f"Name: {updated_file['name']}")
        print(f"Size: {updated_file.get('size', 'Unknown')} bytes")
        
        return updated_file
        
    except HttpError as e:
        if e.resp.status == 404:
            hint = ""
            if service_account_email:
                hint = f" Ensure the file exists and is shared with: {service_account_email}"
            print(f"Not found.{hint}")
        else:
            print(f"Drive API error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise

def get_file_info(api_key_file_path, drive_file_id):
    """
    Get information about a file in Google Drive.
    
    Args:
        api_key_file_path (str): Path to your Google service account JSON key file
        drive_file_id (str): The Google Drive file ID
    
    Returns:
        dict: File information from Google Drive
    """
    
    try:
        # Expand ~ and environment vars
        api_key_file_path = os.path.expanduser(os.path.expandvars(api_key_file_path))
        
        # Set up credentials
        credentials = service_account.Credentials.from_service_account_file(
            api_key_file_path,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        # Build the Drive service
        drive_service = build('drive', 'v3', credentials=credentials)
        
        # Resolve shortcuts to their target file ID
        file_id_to_fetch, _ = _resolve_actual_file_id(drive_service, drive_file_id)
        
        # Get file metadata
        file_info = drive_service.files().get(
            fileId=file_id_to_fetch,
            fields='id,name,size,mimeType,webViewLink,createdTime,modifiedTime',
            supportsAllDrives=True
        ).execute()
        
        return file_info
        
    except HttpError as e:
        if e.resp.status == 404:
            service_account_email = getattr(credentials, 'service_account_email', None)
            hint = ""
            if service_account_email:
                hint = f" Ensure the file exists and is shared with: {service_account_email}"
            print(f"Not found.{hint}")
        else:
            print(f"Drive API error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise

def _resolve_actual_file_id(drive_service, file_id):
    """
    Resolve a file ID to the actual target if it's a shortcut. Returns (resolved_id, metadata).
    """
    meta = drive_service.files().get(
        fileId=file_id,
        fields='id,name,mimeType,shortcutDetails',
        supportsAllDrives=True
    ).execute()
    if meta.get('mimeType') == 'application/vnd.google-apps.shortcut':
        target = meta.get('shortcutDetails', {}).get('targetId')
        if target:
            meta = drive_service.files().get(
                fileId=target,
                fields='id,name,mimeType',
                supportsAllDrives=True
            ).execute()
            return meta['id'], meta
    return meta['id'], meta

def replace_google_sheet_with_csv(api_key_file_path, csv_file_path, sheet_file_id):
    """
    Replace the contents of an existing Google Sheets file with the contents of a CSV file.
    Keeps the same file ID and URL.
    """
    # Expand ~ and environment vars
    api_key_file_path = os.path.expanduser(os.path.expandvars(api_key_file_path))
    csv_file_path = os.path.expanduser(os.path.expandvars(csv_file_path))

    if not os.path.exists(api_key_file_path):
        raise FileNotFoundError(f"API key file not found: {api_key_file_path}")
    if not os.path.exists(csv_file_path):
        raise FileNotFoundError(f"CSV file not found: {csv_file_path}")

    try:
        credentials = service_account.Credentials.from_service_account_file(
            api_key_file_path,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        drive_service = build('drive', 'v3', credentials=credentials)
        service_account_email = getattr(credentials, 'service_account_email', None)

        # Resolve shortcuts
        resolved_id, meta = _resolve_actual_file_id(drive_service, sheet_file_id)

        # Validate target is a Google Sheet
        if meta.get('mimeType') != 'application/vnd.google-apps.spreadsheet':
            raise ValueError(
                f"Target file is not a Google Sheet. mimeType={meta.get('mimeType')}"
            )

        # Upload CSV to replace the sheet's contents
        media = MediaFileUpload(
            csv_file_path,
            mimetype='text/csv',
            resumable=True
        )

        updated = drive_service.files().update(
            fileId=resolved_id,
            media_body=media,
            supportsAllDrives=True
        ).execute()

        print("Sheet replaced from CSV.")
        print(f"ID: {updated['id']}")
        print(f"Name: {updated['name']}")
        return updated

    except HttpError as e:
        if e.resp.status == 404:
            hint = ""
            if service_account_email:
                hint = f" Ensure the sheet exists and is shared with: {service_account_email}"
            print(f"Not found.{hint}")
        else:
            print(f"Drive API error: {e}")
        raise

def replace_specific_sheet_tab_with_csv(api_key_file_path, csv_file_path, spreadsheet_id, sheet_name):
    """
    Replace the contents of a specific sheet/tab within an existing Google Sheets spreadsheet
    with the contents of a CSV file. Keeps the same spreadsheet link.
    """
    # Expand ~ and environment vars
    api_key_file_path = os.path.expanduser(os.path.expandvars(api_key_file_path))
    csv_file_path = os.path.expanduser(os.path.expandvars(csv_file_path))

    if not os.path.exists(api_key_file_path):
        raise FileNotFoundError(f"API key file not found: {api_key_file_path}")
    if not os.path.exists(csv_file_path):
        raise FileNotFoundError(f"CSV file not found: {csv_file_path}")

    try:
        credentials = service_account.Credentials.from_service_account_file(
            api_key_file_path,
            scopes=[
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/spreadsheets'
            ]
        )

        # Build both Drive and Sheets services
        drive_service = build('drive', 'v3', credentials=credentials)
        sheets_service = build('sheets', 'v4', credentials=credentials)
        service_account_email = getattr(credentials, 'service_account_email', None)

        # Resolve shortcuts and validate spreadsheet type
        resolved_id, meta = _resolve_actual_file_id(drive_service, spreadsheet_id)
        if meta.get('mimeType') != 'application/vnd.google-apps.spreadsheet':
            raise ValueError(
                f"Target file is not a Google Sheet. mimeType={meta.get('mimeType')}"
            )

        # Fetch spreadsheet metadata to find the sheet/tab by name
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=resolved_id
        ).execute()
        sheets = spreadsheet.get('sheets', [])
        target_sheet = next(
            (s for s in sheets if s.get('properties', {}).get('title') == sheet_name),
            None
        )
        if not target_sheet:
            raise ValueError(f"Sheet/tab named '{sheet_name}' not found in spreadsheet")

        # Read CSV file
        with open(csv_file_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            values = [row for row in reader]

        # Determine range to write starting at A1 of the target sheet
        write_range = f"{sheet_name}!A1"

        # Clear existing contents of the target sheet
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=resolved_id,
            range=f"{sheet_name}",
            body={}
        ).execute()

        # Write CSV values to the target sheet
        sheets_service.spreadsheets().values().update(
            spreadsheetId=resolved_id,
            range=write_range,
            valueInputOption='RAW',
            body={
                'values': values
            }
        ).execute()

        print(f"Sheet '{sheet_name}' successfully replaced from CSV!")
        return {
            'spreadsheetId': resolved_id,
            'sheetName': sheet_name,
            'rows': len(values)
        }

    except HttpError as e:
        if e.resp.status == 404:
            hint = ""
            if service_account_email:
                hint = f" Ensure the spreadsheet exists and is shared with: {service_account_email}"
            print(f"Error replacing specific sheet tab (not found).{hint}")
        else:
            print(f"Sheets/Drive API error replacing specific sheet tab: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error replacing specific sheet tab: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error replacing Google Sheet: {e}")
        raise

# Example usage
if __name__ == "__main__":
    # Example configuration
    API_KEY_FILE = os.path.expanduser(os.path.expandvars("~/service.json"))
    LOCAL_FILE = os.path.expanduser(os.path.expandvars("~/Downloads/Findings.csv"))
    DRIVE_FILE_ID = "1-cWUoNSmhu1AwHdatDCpDhq3ieHJKkbYzQ8T7YPNKOI" 

    try:
        # Get current file info
        print("Current file information:")
        current_info = get_file_info(API_KEY_FILE, DRIVE_FILE_ID)
        print(f"Name: {current_info['name']}")
        print(f"Size: {current_info.get('size', 'Unknown')} bytes")
        print(f"URL: {current_info.get('webViewLink', 'No web link')}")
        print()
        
        # Option A: Replace Google Drive binary file (keeps the same link)
        print("Replacing file...")
        result = replace_google_drive_file(API_KEY_FILE, LOCAL_FILE, DRIVE_FILE_ID)

        # Get updated file info
        print("\nUpdated file information:")
        updated_info = get_file_info(API_KEY_FILE, DRIVE_FILE_ID)
        print(f"Name: {updated_info['name']}")
        print(f"Size: {updated_info.get('size', 'Unknown')} bytes")
        print(f"URL: {updated_info.get('webViewLink', 'No web link')}")
        
    except Exception as e:
        print(f"Error: {str(e)}")