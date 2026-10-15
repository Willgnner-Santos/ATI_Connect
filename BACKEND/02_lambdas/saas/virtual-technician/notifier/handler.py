import json
import boto3
import time
from os import environ
from decimal import Decimal
from notifications import NotificationManager

def decimal_default(obj):
    """JSON encoder for Decimal"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def is_duplicate_message(tenant_id, action_id, message_id, region):
    """Faz claim atômico da mensagem em item separado com TTL."""
    if not message_id:
        return False

    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])
    key = {
        'pk': f"DEDUPE#NOTIFIER#{tenant_id}#{action_id}",
        'sk': f"MESSAGE#{message_id}"
    }

    try:
        now = int(time.time())
        table.put_item(
            Item={**key, 'status': 'processing', 'expiresAt': now + 300},
            ConditionExpression='attribute_not_exists(pk) OR expiresAt < :now',
            ExpressionAttributeValues={':now': now}
        )
        return False
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        existing = table.get_item(Key=key).get('Item', {})
        if existing.get('status') == 'completed':
            print(f"WARNING: Mensagem {message_id} já concluída para ação {action_id}")
            return True
        raise Exception(f"Message {message_id} is already being processed")


def finish_message(tenant_id, action_id, message_id, region, success):
    if not message_id:
        return
    table = boto3.resource('dynamodb', region_name=region).Table(
        environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN']
    )
    key = {'pk': f"DEDUPE#NOTIFIER#{tenant_id}#{action_id}", 'sk': f"MESSAGE#{message_id}"}
    if success:
        table.update_item(
            Key=key,
            UpdateExpression='SET #status = :completed, expiresAt = :expiresAt',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':completed': 'completed', ':expiresAt': int(time.time()) + 86400
            }
        )
    else:
        table.delete_item(Key=key)

def replace_variables(text, equipment_name, telemetry):
    """Replace template variables in text"""
    replacements = {
        '{{equipmentName}}': equipment_name,
        '{{equipmentId}}': telemetry.get('equipmentId', 'N/A'),
        '{{timestamp}}': str(telemetry.get('timestamp', 'N/A'))
    }

    # Replace sensor variables — 'equipmentName' é excluído de propósito: o valor
    # correto já foi definido acima a partir do cadastro do equipamento, e não deve
    # ser sobrescrito por um campo de mesmo nome vindo da telemetria do dispositivo
    # (que poderia ser usado para falsificar o conteúdo da notificação)
    for key, value in telemetry.items():
        if key not in ['equipmentId', 'tenantId', 'actionId', 'timestamp', 'equipmentName']:
            replacements[f'{{{{{key}}}}}'] = str(value)

    result = text
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    return result

def handler(event, context):
    """
    Virtual Technician Notifier

    Triggered by: SQS (from Validator)

    Process:
    1. Receive validated notification request
    2. Replace template variables
    3. Send notifications via NotificationManager (Email, SMS, Telegram)
    """
    print("INFO: Starting notifier...")

    processed = 0
    batch_item_failures = []

    manager = NotificationManager()

    for record in event['Records']:
        dedupe_claimed = False
        tenant_id = action_id = source_message_id = None
        try:
            # Parse message
            message = json.loads(record['body'])
            print(f"INFO: Processando mensagem SQS id={record.get('messageId', 'unknown')}")

            tenant_id = message.get('tenantId')
            action_id = message.get('actionId')
            source_message_id = message.get('sourceMessageId')
            notification = message.get('notification', {})
            equipment_name = message.get('equipmentName', 'Unknown')
            telemetry = message.get('telemetry', {})

            subject = notification.get('subject')
            body = notification.get('message')
            recipients = notification.get('recipients', [])

            if not subject or not body or not recipients:
                raise Exception("Missing notification data")

            # Deduplicação: evita reenviar a mesma notificação se o SQS reentregar
            # a mensagem original do validator
            if action_id and is_duplicate_message(tenant_id, action_id, source_message_id, environ['REGION']):
                processed += 1
                continue
            dedupe_claimed = bool(action_id and source_message_id)

            # Replace template variables
            subject = replace_variables(subject, equipment_name, telemetry)
            body = replace_variables(body, equipment_name, telemetry)

            print(f"INFO: Notificação preparada recipients={len(recipients)}")

            # Send via all configured providers
            result = manager.send(recipients, subject, body, environ['REGION'])

            if result['processed'] == 0:
                raise Exception(f"All providers failed to send notification ({result['failed']} failed)")

            processed += 1
            if dedupe_claimed:
                finish_message(tenant_id, action_id, source_message_id, environ['REGION'], True)

        except Exception as e:
            if dedupe_claimed:
                finish_message(tenant_id, action_id, source_message_id, environ['REGION'], False)
            print(f"ERROR: Failed to process record {record.get('messageId')}: {e}")
            batch_item_failures.append({'itemIdentifier': record['messageId']})

    print(f"INFO: Notifier finished - Processed: {processed}, Failed: {len(batch_item_failures)}")

    return {'batchItemFailures': batch_item_failures}
