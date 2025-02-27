from slack_sdk.webhook import WebhookClient
from typing import Dict, Optional
from ..core.config import settings
from datetime import datetime

class NotificationService:
    def __init__(self):
        self.webhook_url = settings.SLACK_WEBHOOK_URL
        self.webhook_client = WebhookClient(url=self.webhook_url) if self.webhook_url else None
        
    async def send_notification(self, 
                              subject: str,
                              sender: str,
                              category: Optional[str] = None,
                              importance: str = "normal",
                              date: Optional[datetime] = None) -> bool:
        """Send a notification to Slack about an important email."""
        if not self.webhook_client:
            print("Slack webhook URL not configured, skipping notification")
            return False
            
        try:
            # Format the message
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "📧 New Important Email",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*From:*\n{sender}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Subject:*\n{subject}"
                        }
                    ]
                }
            ]
            
            # Add category if available
            if category:
                blocks.append({
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Category:*\n{category}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Importance:*\n{importance}"
                        }
                    ]
                })
            
            # Add date if available
            if date:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Received at: {date.strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    ]
                })
            
            # Send the message
            response = self.webhook_client.send(
                text="New Important Email",
                blocks=blocks
            )
            
            if response.status_code == 200:
                print(f"Successfully sent notification for email: {subject}")
                return True
            else:
                print(f"Failed to send notification. Status: {response.status_code}, Body: {response.body}")
                return False
                
        except Exception as e:
            print(f"Error sending Slack notification: {str(e)}")
            return False
            
    async def send_error_notification(self, error_message: str, context: Dict = None) -> bool:
        """Send a notification about system errors."""
        if not self.webhook_client:
            print("Slack webhook URL not configured, skipping error notification")
            return False
            
        try:
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "⚠️ System Alert",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Error:*\n{error_message}"
                    }
                }
            ]
            
            # Add context if available
            if context:
                context_fields = []
                for key, value in context.items():
                    context_fields.append({
                        "type": "mrkdwn",
                        "text": f"*{key}:*\n{value}"
                    })
                
                blocks.append({
                    "type": "section",
                    "fields": context_fields
                })
            
            # Add timestamp
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            })
            
            response = self.webhook_client.send(
                text="System Alert",
                blocks=blocks
            )
            
            if response.status_code == 200:
                print(f"Successfully sent error notification: {error_message}")
                return True
            else:
                print(f"Failed to send error notification. Status: {response.status_code}, Body: {response.body}")
                return False
                
        except Exception as e:
            print(f"Error sending error notification: {str(e)}")
            return False

notification_service = NotificationService() 