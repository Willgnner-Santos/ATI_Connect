from notifications.email import EmailProvider
from notifications.sms import SMSProvider
from notifications.telegram import TelegramProvider
from notifications.push import PushProvider

class NotificationManager:
    def __init__(self):
        # Register all available providers here
        self.providers = [
            EmailProvider(),
            SMSProvider(),
            TelegramProvider(),
            PushProvider(),
        ]
        
    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        """
        Routes the message to the appropriate providers based on recipient format.
        Returns a dict with total processed and failed counts.
        """
        total_processed = 0
        total_failed = 0
        
        for provider in self.providers:
            # Filter recipients supported by this provider
            supported_recipients = [r for r in recipients if provider.supports(r)]
            
            if supported_recipients:
                result = provider.send(supported_recipients, subject, message, region)
                total_processed += result.get('processed', 0)
                total_failed += result.get('failed', 0)
                
        # If there are recipients that don't match any provider, log a warning
        all_supported = [r for p in self.providers for r in recipients if p.supports(r)]
        unsupported = list(set(recipients) - set(all_supported))
        
        if unsupported:
            print(f"WARNING: The following recipients are not supported by any provider and were skipped: {unsupported}")
            total_failed += len(unsupported)
            
        return {'processed': total_processed, 'failed': total_failed}
