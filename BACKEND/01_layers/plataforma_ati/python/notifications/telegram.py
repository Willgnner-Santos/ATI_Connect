import requests
from os import environ
from notifications.base import NotificationProvider

class TelegramProvider(NotificationProvider):
    
    def supports(self, recipient: str) -> bool:
        # Telegram recipients must start with tg: prefix
        return recipient.startswith('tg:') or recipient.startswith('telegram:')
        
    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        print(f"INFO: TelegramProvider sending to {len(recipients)} recipients...")
        if not recipients:
            return {'processed': 0, 'failed': 0}
            
        bot_token = environ.get('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN environment variable is not set")
            return {'processed': 0, 'failed': len(recipients)}
            
        success_count = 0
        failed_count = 0
        
        # Format message with markdown/HTML for better display in Telegram
        # We will use HTML mode
        telegram_body = f"<b>{subject}</b>\n\n{message}"
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        for recipient in recipients:
            # Clean the recipient to get just the chat id
            chat_id = recipient.replace('tg:', '').replace('telegram:', '').strip()
            
            try:
                payload = {
                    'chat_id': chat_id,
                    'text': telegram_body,
                    'parse_mode': 'HTML'
                }
                
                response = requests.post(url, json=payload, timeout=10)
                
                if response.status_code == 200:
                    print(f"INFO: Telegram message sent successfully to chat_id: {chat_id}")
                    success_count += 1
                else:
                    print(f"ERROR: Failed to send Telegram message to {chat_id}. HTTP {response.status_code}: {response.text}")
                    failed_count += 1

            except Exception as e:
                print(f"ERROR: Exception sending Telegram message to {chat_id}: {e}")
                failed_count += 1

        return {'processed': success_count, 'failed': failed_count}
