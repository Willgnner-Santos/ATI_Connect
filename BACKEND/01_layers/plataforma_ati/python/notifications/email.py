import boto3
from os import environ
from notifications.base import NotificationProvider

class EmailProvider(NotificationProvider):
    
    def supports(self, recipient: str) -> bool:
        # Standard email format check
        return '@' in recipient and not recipient.startswith('tg:')
        
    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        print(f"INFO: EmailProvider sending to {len(recipients)} recipients...")
        if not recipients:
            return {'processed': 0, 'failed': 0}
            
        ses_client = boto3.client('ses', region_name=region)
        success_count = 0
        failed_count = 0

        try:
            sender_email = environ.get('SENDER_EMAIL', 'noreply@plataforma-ati.com')

            response = ses_client.send_email(
                Source=sender_email,
                Destination={
                    'ToAddresses': recipients
                },
                Message={
                    'Subject': {
                        'Data': subject,
                        'Charset': 'UTF-8'
                    },
                    'Body': {
                        'Text': {
                            'Data': message,
                            'Charset': 'UTF-8'
                        }
                    }
                }
            )

            print(f"INFO: Email sent successfully - MessageId: {response['MessageId']}")
            success_count = len(recipients)

        except Exception as e:
            print(f"ERROR: Failed to send email: {e}")
            failed_count = len(recipients)
            
        return {'processed': success_count, 'failed': failed_count}
