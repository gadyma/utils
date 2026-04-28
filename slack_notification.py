import requests
import json
import os
import sys

# Get the full path to the directory containing config.py
config_dir = os.path.expanduser('~')

# Add this directory to the system path
sys.path.append(config_dir)
from config import WEBHOOK_URL

# Your unique Slack Webhook URL
#webhook_url = WEBHOOK_URL # "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    

def send_slack_notification(message: str):
    """
    Sends a notification to a Slack channel.
    
    Args:
        message (str): The message to send.
    """
    
    # The message payload
    slack_data = {'text': message}
    
    # Send the POST request
    response = requests.post(
        WEBHOOK_URL,
        data=json.dumps(slack_data),
        headers={'Content-Type': 'application/json'}
    )
    
    # Check for errors
    if response.status_code != 200:
        raise ValueError(
            f'Request to slack returned an error {response.status_code}, the response is:\n{response.text}'
        )

# --- Example Usage ---
if __name__ == "__main__":
    try:
        # A successful notification
        send_slack_notification("Hello from Python! 🐍 Your script has finished running successfully.")

        # Simulating an error notification
        # In a real app, you would call this in a try...except block
        # raise ValueError("Something went wrong!")
    except Exception as e:
        send_slack_notification(f"🚨 An error occurred: {e}")