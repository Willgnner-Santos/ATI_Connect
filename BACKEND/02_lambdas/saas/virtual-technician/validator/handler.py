import json
import boto3
from os import environ
from decimal import Decimal

def decimal_default(obj):
    """JSON encoder for Decimal"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def get_action_config(tenant_id, action_id, region):
    """Get action configuration from DynamoDB"""
    print(f"INFO: Getting action {action_id} config...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])

    try:
        response = table.get_item(
            Key={
                'pk': f"TENANT#{tenant_id}#VIRTUAL_TECHNICIAN",
                'sk': f"ACTION#{action_id}"
            }
        )

        if 'Item' not in response:
            print(f"WARNING: Action {action_id} not found")
            return None

        action = response['Item']

        # Check if action is active
        if action.get('status') != 'active':
            print(f"INFO: Action {action_id} is inactive, skipping")
            return None

        return action

    except Exception as e:
        print(f"ERROR: Failed to get action config: {e}")
        return None

def evaluate_single_condition(condition, telemetry_data):
    """Evaluate a single condition against telemetry data"""
    variable = condition.get('variable')
    operator = condition.get('operator')
    expected_value = condition.get('value')
    condition_type = condition.get('type')

    # Get actual value from telemetry
    actual_value = telemetry_data.get(variable)

    if actual_value is None:
        print(f"WARNING: Variable {variable} not found in telemetry")
        return False

    # Convert values for comparison
    try:
        if condition_type == 'number':
            actual_value = float(actual_value)
            expected_value = float(expected_value)
        elif condition_type == 'boolean':
            BOOL_TRUE  = {True,  'true',  'True',  'TRUE'}
            BOOL_FALSE = {False, 'false', 'False', 'FALSE'}
            if actual_value not in BOOL_TRUE and actual_value not in BOOL_FALSE:
                print(f"WARNING: Valor booleano inválido para '{variable}': {repr(actual_value)}")
                return False
            if expected_value not in BOOL_TRUE and expected_value not in BOOL_FALSE:
                print(f"WARNING: Valor esperado booleano inválido para '{variable}': {repr(expected_value)}")
                return False
            actual_value   = actual_value   in BOOL_TRUE
            expected_value = expected_value in BOOL_TRUE
        else:  # string
            actual_value = str(actual_value)
            expected_value = str(expected_value)
    except Exception as e:
        print(f"ERROR: Failed to convert values: {e}")
        return False

    # Evaluate operator
    if operator == '=':
        result = actual_value == expected_value
    elif operator == '>':
        result = actual_value > expected_value
    elif operator == '<':
        result = actual_value < expected_value
    elif operator == '>=':
        result = actual_value >= expected_value
    elif operator == '<=':
        result = actual_value <= expected_value
    elif operator == '!=':
        result = actual_value != expected_value
    else:
        print(f"ERROR: Unknown operator {operator}")
        return False

    print(f"INFO: Condition {variable} {operator} {expected_value}: {actual_value} -> {result}")
    return result

def evaluate_conditions(conditions, telemetry_data):
    """Evaluate all conditions with AND/OR logic, aplicado da esquerda para a direita
    (sem precedência especial entre AND/OR — mesmo comportamento que a string
    montada anteriormente via eval() produzia). Não usa eval(): o operador lógico
    de cada condição é comparado contra uma allowlist, nunca executado como código.
    """
    if not conditions:
        return True

    accumulated = evaluate_single_condition(conditions[0], telemetry_data)
    for i in range(1, len(conditions)):
        logical_op = str(conditions[i - 1].get('condition', 'AND')).strip().upper()
        if logical_op not in ('AND', 'OR'):
            print(f"WARNING: Operador lógico desconhecido '{logical_op}', assumindo AND")
            logical_op = 'AND'

        current = evaluate_single_condition(conditions[i], telemetry_data)
        if logical_op == 'AND':
            accumulated = accumulated and current
        else:
            accumulated = accumulated or current

    print(f"INFO: Final result: {accumulated}")
    return accumulated

def send_to_executor(message, queue_url, region):
    """Send message to executor queue"""
    print(f"INFO: Sending to executor queue: {queue_url}")
    sqs = boto3.client('sqs', region_name=region)

    try:
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message, default=decimal_default)
        )
        print("INFO: Message sent to executor")
        return True
    except Exception as e:
        print(f"ERROR: Failed to send to executor: {e}")
        return False

def handler(event, context):
    """
    Virtual Technician Validator

    Triggered by: SQS (from IoT Rule)

    Process:
    1. Receive message from IoT Rule (conditions matched)
    2. Get action configuration
    3. Validate conditions are still true
    4. If valid, forward to executor queue
    """
    print("INFO: Starting validator...")

    processed = 0
    batch_item_failures = []

    for record in event['Records']:
        try:
            # Parse message
            message = json.loads(record['body'])
            print(f"INFO: Processando mensagem SQS id={record.get('messageId', 'unknown')}")

            tenant_id = message.get('tenantId')
            action_id = message.get('actionId')
            equipment_id = message.get('equipmentId')

            if not tenant_id or not action_id or not equipment_id:
                raise Exception("Missing required fields in message")

            # Get action configuration
            action = get_action_config(tenant_id, action_id, environ['REGION'])

            if not action:
                # Ação não existe/inativa: não é um erro transitório, retry não ajuda.
                # Não é adicionada a batch_item_failures — a mensagem é descartada.
                print(f"WARNING: Action {action_id} not found or inactive, discarding message")
                processed += 1
                continue

            # Get conditions
            conditions = action.get('conditions', [])
            action_type = action.get('type')

            # Validate conditions against current telemetry
            conditions_met = evaluate_conditions(conditions, message)

            if conditions_met:
                print(f"INFO: Conditions met for action {action_id}")

                # Determine target queue
                if action_type == 'action':
                    target_queue = environ.get('EXECUTOR_QUEUE_URL')
                elif action_type == 'notification':
                    target_queue = environ.get('NOTIFIER_QUEUE_URL')
                else:
                    raise Exception(f"Unknown action type: {action_type}")

                # Send to executor/notifier — inclui o messageId original para permitir
                # deduplicação no executor/notifier em caso de reentrega do SQS
                executor_message = {
                    'tenantId': tenant_id,
                    'actionId': action_id,
                    'equipmentId': equipment_id,
                    'action': action.get('actions', []) if action_type == 'action' else [],
                    'notification': action.get('notification', {}) if action_type == 'notification' else {},
                    'equipmentName': action.get('equipmentName'),
                    'telemetry': message,
                    'sourceMessageId': record.get('messageId')
                }

                if not send_to_executor(executor_message, target_queue, environ['REGION']):
                    raise Exception("Failed to send to executor/notifier queue")

            else:
                print(f"INFO: Conditions not met for action {action_id}, skipping")

            processed += 1

        except Exception as e:
            print(f"ERROR: Failed to process record {record.get('messageId')}: {e}")
            batch_item_failures.append({'itemIdentifier': record['messageId']})

    print(f"INFO: Validator finished - Processed: {processed}, Failed: {len(batch_item_failures)}")

    return {'batchItemFailures': batch_item_failures}
