import boto3
from notifications.base import NotificationProvider

class SMSProvider(NotificationProvider):
    
    def supports(self, recipient: str) -> bool:
        # Standard phone number starts with +
        return recipient.startswith('+')
        
    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        print(f"INFO: SMSProvider sending to {len(recipients)} recipients...")
        if not recipients:
            return {'processed': 0, 'failed': 0}
            
        sns_client = boto3.client('sns', region_name=region)
        success_count = 0
        failed_count = 0
        
        # Combine subject and message for SMS
        sms_body = f"{subject}\n\n{message}"

        for phone in recipients:
            try:
                response = sns_client.publish(
                    PhoneNumber=phone,
                    Message=sms_body,
                    MessageAttributes={
                        'AWS.SNS.SMS.SMSType': {
                            'DataType': 'String',
                            'StringValue': 'Transactional'
                        }
                    }
                )

                print(f"INFO: SMS sent to {phone} - MessageId: {response['MessageId']}")
                success_count += 1

            except Exception as e:
                print(f"ERROR: Failed to send SMS to {phone}: {e}")
                failed_count += 1

        return {'processed': success_count, 'failed': failed_count}
