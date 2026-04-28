from wame import create_whatsapp_link
phone = "972501234567"
message = """Hi there!
This is a test message
with multiple lines."""
url = create_whatsapp_link(phone, message)
print(url)
