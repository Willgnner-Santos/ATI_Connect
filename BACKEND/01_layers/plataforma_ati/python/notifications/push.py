import json
import boto3
from os import environ
from notifications.base import NotificationProvider

_firebase_app = None


def _get_firebase_app(region: str):
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    import firebase_admin
    from firebase_admin import credentials

    ssm_param = environ.get('FIREBASE_SERVICE_ACCOUNT_SSM_PARAM')
    if not ssm_param:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_SSM_PARAM not set")

    ssm = boto3.client('ssm', region_name=region)
    response = ssm.get_parameter(Name=ssm_param, WithDecryption=True)
    service_account = json.loads(response['Parameter']['Value'])

    cred = credentials.Certificate(service_account)
    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


class PushProvider(NotificationProvider):

    def supports(self, recipient: str) -> bool:
        return recipient.startswith('push:')

    def send(self, recipients: list, subject: str, message: str, region: str) -> dict:
        print(f"INFO: PushProvider sending to {len(recipients)} recipients...")
        if not recipients:
            return {'processed': 0, 'failed': 0}

        table_name = environ.get('DYNAMODB_TABLE_PUSH_SUBSCRIPTIONS')
        if not table_name:
            print("ERROR: DYNAMODB_TABLE_PUSH_SUBSCRIPTIONS not set")
            return {'processed': 0, 'failed': len(recipients)}

        try:
            _get_firebase_app(region)
        except Exception as e:
            print(f"ERROR: Failed to initialize Firebase: {e}")
            return {'processed': 0, 'failed': len(recipients)}

        from firebase_admin import messaging

        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(table_name)

        success_count = 0
        failed_count = 0

        for recipient in recipients:
            parts = recipient.split(':', 2)
            if len(parts) != 3:
                print(f"WARNING: Invalid push recipient format '{recipient}', expected push:{{tenantId}}:{{userId}}")
                failed_count += 1
                continue

            tenant_id = parts[1]
            user_id = parts[2]
            pk = f"TENANT#{tenant_id}#USER#{user_id}"

            try:
                response = table.query(
                    KeyConditionExpression='pk = :pk',
                    ExpressionAttributeValues={':pk': pk}
                )
                subscriptions = response.get('Items', [])

                if not subscriptions:
                    print(f"WARNING: No push subscriptions found for {recipient}")
                    failed_count += 1
                    continue

                tokens = [sub['deviceToken'] for sub in subscriptions if sub.get('deviceToken')]
                if not tokens:
                    failed_count += 1
                    continue

                multicast = messaging.MulticastMessage(
                    notification=messaging.Notification(title=subject, body=message),
                    tokens=tokens,
                )
                result = messaging.send_each_for_multicast(multicast)
                success_count += result.success_count
                failed_count += result.failure_count

            except Exception as e:
                print(f"ERROR: Failed to send push to {recipient}: {e}")
                failed_count += 1

        return {'processed': success_count, 'failed': failed_count}
