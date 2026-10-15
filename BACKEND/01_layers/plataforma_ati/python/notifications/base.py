class NotificationProvider:
    """Base class for all notification providers"""
    
    def supports(self, recipient: str) -> bool:
        """
        Check if this provider supports the given recipient format.
        """
        raise NotImplementedError("supports() must be implemented by subclasses")

    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        """
        Send a notification to the specified recipients.
        
        Args:
            recipients: List of recipient identifiers (e.g. email addresses, phone numbers, chat IDs)
            subject: Notification subject
            message: Notification body
            region: AWS Region if needed by the provider
            
        Returns:
            dict with 'processed' (int) and 'failed' (int) counts.
        """
        raise NotImplementedError("send() must be implemented by subclasses")
