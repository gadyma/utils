#!/usr/bin/env python3
import sys
import argparse
from urllib.parse import quote

# Default country code
DEFAULT_COUNTRY_CODE = "972"

def create_whatsapp_link(phone: str, message: str, default_country_code: str = DEFAULT_COUNTRY_CODE) -> str:
    encoded_message = quote(message)

    # Handle "No Number" mode
    if not phone or phone.strip() == "":
        return f"https://wa.me/send?text={encoded_message}"
    
    # Clean phone number
    clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
    
    # Add country code logic
    if not clean_phone.startswith(default_country_code):
        if clean_phone.startswith('0'):
            clean_phone = clean_phone[1:]
        clean_phone = default_country_code + clean_phone
    
    return f"https://wa.me/{clean_phone}?text={encoded_message}"

def main():
    parser = argparse.ArgumentParser(description="Generate WhatsApp links via parameters or interactive mode.")
    parser.add_argument("-p", "--phone", help="Phone number (optional)", default=None)
    parser.add_argument("-m", "--message", help="Message text (optional)", default=None)
    
    args = parser.parse_args()

    # If parameters are provided via CLI
    if args.message is not None:
        phone = args.phone if args.phone else ""
        message = args.message
    else:
        # Fallback to Interactive TUI
        print("WhatsApp Link Generator (Interactive Mode)")
        print("-" * 40)
        phone = input("Enter phone (leave blank for no-number link): ").strip()
        print("Enter message (Ctrl+D/Ctrl+Z to finish):")
        lines = []
        try:
            while True:
                lines.append(input())
        except EOFError:
            message = '\n'.join(lines)

    if not message.strip():
        print("Error: Message is empty.")
        sys.exit(1)

    url = create_whatsapp_link(phone, message)
    
    print(f"\n{url}")

if __name__ == "__main__":
    main()