import json
import boto3
import time
from os import environ
from decimal import Decimal
from plataforma_ati import authorizeCommandExecution, coerceCommandValue

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
        'pk': f"DEDUPE#EXECUTOR#{tenant_id}#{action_id}",
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
    key = {'pk': f"DEDUPE#EXECUTOR#{tenant_id}#{action_id}", 'sk': f"MESSAGE#{message_id}"}
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

def get_equipment(equipment_id, tenant_id, region):
    """Carrega a configuração corrente do equipamento."""
    print(f"INFO: Getting topic for equipment {equipment_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_EQUIPMENT'])

    try:
        response = table.get_item(
            Key={
                'pk': f"TENANT#{tenant_id}#EQUIPMENT",
                'sk': f"EQUIPMENT#{equipment_id}"
            }
        )

        if 'Item' not in response:
            print(f"ERROR: Equipment {equipment_id} not found")
            return None

        return response['Item']

    except Exception as e:
        print(f"ERROR: Failed to get equipment: {e}")
        return None

def build_mqtt_payload(action, equipment_id, command_info, execution_id):
    """Build MQTT payload for action"""
    variable = action.get('variable')
    mqtt_value = coerceCommandValue(command_info, action.get('value'))

    payload = {
        'type': 'send',
        'token': equipment_id,
        'recurso': variable,
        'valor': mqtt_value,
        'commandExecutionId': execution_id
    }

    print(f"INFO: MQTT command prepared variable={variable} executionId={execution_id}")
    return payload

def publish_mqtt(topic, payload, region):
    """Publish message to IoT Core MQTT"""
    print(f"INFO: Publishing to topic {topic}...")
    iot_client = boto3.client('iot-data', region_name=region)

    try:
        iot_client.publish(
            topic=topic,
            qos=1,
            payload=json.dumps(payload, default=decimal_default)
        )
        print("INFO: Message published successfully")
        return True
    except Exception as e:
        print(f"ERROR: Failed to publish: {e}")
        return False

def execute_actions(actions, equipment, execution_id, region, tenant_role=None):
    """Execute list of actions with delays"""
    print(f"INFO: Executing {len(actions)} actions...")

    if equipment.get('status') != 'active':
        raise Exception("Equipment is inactive")
    topic = equipment.get('topic')
    if not topic:
        raise Exception("Equipment has no MQTT topic")

    success_count = 0
    failed_count = 0

    for i, action in enumerate(actions):
        print(f"INFO: Executing action {i+1}/{len(actions)}")

        # Apply delay if specified
        delay = float(action.get('delay', 0))
        if delay > 0:
            print(f"INFO: Waiting {delay} seconds...")
            time.sleep(delay)

        # Build payload
        variable = action.get('variable')
        command_info = (equipment.get('commands') or {}).get(variable)
        if not command_info:
            raise Exception(f"Command '{variable}' no longer exists")
        if tenant_role:
            command_info = authorizeCommandExecution(
                equipment, variable,
                {'role': tenant_role, 'tenantRole': tenant_role, 'tenantId': equipment.get('tenantId')}
            )
        payload = build_mqtt_payload(
            action, equipment['equipmentId'], command_info, f"{execution_id}:{i}"
        )

        # Publish
        if publish_mqtt(topic, payload, region):
            success_count += 1
        else:
            failed_count += 1

    print(f"INFO: Execution complete - Success: {success_count}, Failed: {failed_count}")
    return success_count, failed_count


def get_current_action(tenant_id, action_id, region):
    table = boto3.resource('dynamodb', region_name=region).Table(
        environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN']
    )
    response = table.get_item(Key={
        'pk': f"TENANT#{tenant_id}#VIRTUAL_TECHNICIAN",
        'sk': f"ACTION#{action_id}"
    })
    action = response.get('Item')
    if not action or action.get('status') != 'active':
        raise Exception("Virtual technician action is missing or inactive")
    return action

def handler(event, context):
    """
    Virtual Technician Executor

    Triggered by: SQS (from Validator or EventBridge)

    Process:
    1. Receive validated action execution request
    2. Get equipment MQTT topic
    3. Execute actions sequentially with delays
    4. Publish MQTT messages
    """
    print("INFO: Starting executor...")

    # Este handler é invocado tanto via SQS (event['Records']) quanto diretamente
    # pelo EventBridge Scheduler (payload plano, sem 'Records') para ações do tipo
    # 'schedule'. O formato batchItemFailures só se aplica ao caminho SQS.
    if 'Records' not in event:
        print("INFO: Invocação direta via EventBridge Scheduler (sem 'Records')")
        try:
            tenant_id = event.get('tenantId')
            equipment_id = event.get('equipmentId')
            action_id = event.get('actionId')
            if not tenant_id or not equipment_id or not action_id:
                raise Exception("Missing required fields in scheduled event")
            current_action = get_current_action(tenant_id, action_id, environ['REGION'])
            equipment_id = current_action.get('equipmentId')
            if not equipment_id:
                raise Exception("Virtual technician action has no equipment")
            equipment = get_equipment(equipment_id, tenant_id, environ['REGION'])
            if not equipment:
                raise Exception(f"Could not get equipment {equipment_id}")
            success, failures = execute_actions(
                current_action.get('actions', []), equipment,
                f"{action_id}:{int(time.time() // 60)}",
                environ['REGION'], current_action.get('createdTenantRole')
            )
            return {'statusCode': 200 if failures == 0 else 500, 'body': json.dumps({'success': success, 'failed': failures})}
        except Exception as e:
            print(f"ERROR: Scheduled execution failed: {e}")
            return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}

    processed = 0
    batch_item_failures = []

    for record in event['Records']:
        dedupe_claimed = False
        tenant_id = action_id = source_message_id = None
        try:
            # Parse message
            message = json.loads(record['body'])
            print(f"INFO: Processando mensagem SQS id={record.get('messageId', 'unknown')}")

            tenant_id = message.get('tenantId')
            action_id = message.get('actionId')
            equipment_id = message.get('equipmentId')
            actions = message.get('action', [])
            source_message_id = message.get('sourceMessageId')

            if not tenant_id or not equipment_id or not actions:
                raise Exception("Missing required fields in message")

            # Deduplicação: evita publicar o mesmo comando MQTT duas vezes se o SQS
            # reentregar a mensagem original do validator
            if action_id and is_duplicate_message(tenant_id, action_id, source_message_id, environ['REGION']):
                processed += 1
                continue
            dedupe_claimed = bool(action_id and source_message_id)

            current_action = get_current_action(tenant_id, action_id, environ['REGION']) if action_id else None
            if current_action:
                actions = current_action.get('actions', [])
                equipment_id = current_action.get('equipmentId')
                if not equipment_id:
                    raise Exception("Virtual technician action has no equipment")
            equipment = get_equipment(equipment_id, tenant_id, environ['REGION'])
            if not equipment:
                raise Exception(f"Could not get equipment {equipment_id}")

            # Execute actions
            success, failures = execute_actions(
                actions, equipment, source_message_id or record['messageId'],
                environ['REGION'], (current_action or {}).get('createdTenantRole')
            )

            if failures > 0:
                raise Exception(f"{failures} of {failures + success} actions failed to publish")

            processed += 1
            if dedupe_claimed:
                finish_message(tenant_id, action_id, source_message_id, environ['REGION'], True)

        except Exception as e:
            if dedupe_claimed:
                finish_message(tenant_id, action_id, source_message_id, environ['REGION'], False)
            print(f"ERROR: Failed to process record {record.get('messageId')}: {e}")
            batch_item_failures.append({'itemIdentifier': record['messageId']})

    print(f"INFO: Executor finished - Processed: {processed}, Failed: {len(batch_item_failures)}")

    return {'batchItemFailures': batch_item_failures}
