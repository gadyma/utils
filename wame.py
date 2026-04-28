#!/usr/bin/env python3
from urllib.parse import quote

# Default country code (change this for your region)
DEFAULT_COUNTRY_CODE = "972"

def create_whatsapp_link(phone: str, message: str, default_country_code: str = DEFAULT_COUNTRY_CODE) -> str:
    """
    Create a WhatsApp Web link with URL-encoded message.
    
    Args:
        phone: Phone number (e.g., 050-1234567 or 972501234567)
        message: Message text, can include newlines
        default_country_code: Country code to use if phone doesn't have one (default: 972)
    
    Returns:
        Complete WhatsApp Web URL
    """
    # Remove any + or - characters from phone number
    clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
    
    # Check if phone starts with country code
    if not clean_phone.startswith(default_country_code):
        # Remove leading 0 if present
        if clean_phone.startswith('0'):
            clean_phone = clean_phone[1:]
        # Add country code
        clean_phone = default_country_code + clean_phone
    
    # URL encode the message
    encoded_message = quote(message)
    
    # Build the WhatsApp URL
    url = f"https://wa.me/{clean_phone}?text={encoded_message}"
    
    return url


def main():
    print("WhatsApp Link Generator")
    print("-" * 40)
    
    # Get phone number
    phone = input("Enter phone number (e.g., 050-1234567 or 972501234567): ").strip()
    
    # Get message (supports multiline input)
    print("Enter message (press Ctrl+D or Ctrl+Z when done):")
    print("(You can include newlines by pressing Enter)")
    
    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass
    
    message = '\n'.join(lines)
    
    # Generate link
    url = create_whatsapp_link(phone, message)
    
    print("\n" + "=" * 40)
    print("Your WhatsApp link:")
    print(url)
    print("=" * 40)


if __name__ == "__main__":
    main()