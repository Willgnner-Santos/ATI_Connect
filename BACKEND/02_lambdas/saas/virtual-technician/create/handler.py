import json
import re
import base64
import boto3
from os import environ
from datetime import datetime
from uuid import uuid4
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope,
    authorizeScheduledActions
)

VALID_OPERATORS = ('=', '>', '<', '>=', '<=', '!=')
VALID_VARIABLE_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
VALID_TYPES = ('number', 'boolean', 'string')
VALID_BOOLEAN_VALUES = {True, False, 'true', 'false', 'True', 'False'}
VALID_LOGICAL_OPERATORS = ('AND', 'OR')

def validate_conditions(conditions):
    """Valida variável, operador, tipo e valor de cada condição."""
    for i, condition in enumerate(conditions):
        variable = condition.get('variable')
        operator = condition.get('operator')
        cond_type = condition.get('type')
        value = condition.get('value')

        if not variable or not VALID_VARIABLE_PATTERN.match(str(variable)):
            raise Exception(f"400::Condição {i+1}: nome de variável inválido '{variable}'")
        if operator not in VALID_OPERATORS:
            raise Exception(f"400::Condição {i+1}: operador inválido '{operator}'")
        if cond_type not in VALID_TYPES:
            raise Exception(f"400::Condição {i+1}: tipo inválido '{cond_type}'. Use: number, boolean, string")
        if value is None or str(value).strip() == '':
            raise Exception(f"400::Condição {i+1}: valor não informado")
        if cond_type == 'boolean' and value not in VALID_BOOLEAN_VALUES:
            raise Exception(f"400::Condição {i+1}: valor booleano inválido '{value}'. Use: true ou false")
        if cond_type == 'number':
            try:
                float(value)
            except (TypeError, ValueError):
                raise Exception(f"400::Condição {i+1}: valor numérico inválido '{value}'")
        if i < len(conditions) - 1 and condition.get('condition', 'AND').upper() not in VALID_LOGICAL_OPERATORS:
            raise Exception(f"400::Condição {i+1}: conector lógico deve ser AND ou OR")


def sql_literal(value, value_type):
    if value_type == 'boolean':
        return str(value).lower()
    if value_type == 'number':
        return str(float(value))
    return json.dumps(str(value), ensure_ascii=False)

def get_equipment_info(equipment_id, tenant_id, region):
    """Get equipment topic and endpoint for IoT rules"""
    print(f"INFO: Getting equipment info for {equipment_id}...")
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
            raise Exception("404::Equipamento não encontrado")

        equipment = response['Item']
        return {
            'topic': equipment.get('topic'),
            'name': equipment.get('name'),
            'thingId': equipment.get('thingId'),
            'organizationId': equipment.get('organizationId'),
            'commands': equipment.get('commands', {})
        }
    except Exception as e:
        if "404::" in str(e):
            raise
        raise Exception(f"500::Erro ao buscar equipamento: {str(e)}")

def build_iot_rule_query(topic, conditions, tenant_id, action_id, equipment_id):
    """Build IoT Rule SQL query from conditions"""
    print("INFO: Building IoT Rule query...")

    if not topic or "'" in topic or any(ord(char) < 32 for char in topic):
        raise Exception("400::Tópico MQTT inválido para criação da regra")

    # Constantes usam literais JSON, aceitos pela linguagem SQL do AWS IoT.
    query = (
        f"SELECT *, {sql_literal(tenant_id, 'string')} as tenantId, "
        f"{sql_literal(action_id, 'string')} as actionId, "
        f"{sql_literal(equipment_id, 'string')} as equipmentId, "
        f"timestamp() as timestamp FROM '{topic}' WHERE type <> 'send' AND ("
    )

    condition_parts = []
    for condition in conditions:
        variable = condition['variable']
        operator = condition['operator']
        value = condition['value']
        cond_type = condition['type']

        # Format value based on type
        formatted_value = sql_literal(value, cond_type)

        condition_parts.append(f"{variable} {operator} {formatted_value}")

    # Join conditions with AND/OR
    query_conditions = []
    for i, condition in enumerate(conditions):
        query_conditions.append(condition_parts[i])
        if i < len(conditions) - 1:
            logical_op = condition.get('condition', 'AND').upper()
            query_conditions.append(logical_op)

    query += ' '.join(query_conditions) + ')'

    print(f"INFO: IoT Rule query: {query}")
    return query

def create_iot_rule(action_id, equipment_id, tenant_id, conditions, equipment_info, action_type, region):
    """Create IoT Rule for action or notification"""
    print("INFO: Creating IoT Rule...")

    # Build SQL query
    sql_query = build_iot_rule_query(
        equipment_info['topic'],
        conditions,
        tenant_id,
        action_id,
        equipment_id
    )

    # Rule name (IoT rules can't have special chars)
    rule_name = f"ati_{tenant_id}_{action_type}_{action_id}".replace('-', '_')[:128]

    # Determine SQS queue based on action type
    if action_type == 'action':
        queue_url = environ.get('ACTION_QUEUE_URL')
        queue_arn = environ.get('ACTION_QUEUE_ARN')
    else:  # notification
        queue_url = environ.get('NOTIFICATION_QUEUE_URL')
        queue_arn = environ.get('NOTIFICATION_QUEUE_ARN')

    # Create IoT Rule
    iot_client = boto3.client('iot', region_name=region)

    try:
        iot_client.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload={
                'sql': sql_query,
                'description': f'Virtual Technician - Tenant {tenant_id}',
                'actions': [
                    {
                        'sqs': {
                            'roleArn': environ.get('IOT_RULE_ROLE_ARN'),
                            'queueUrl': queue_url
                        }
                    }
                ],
                'ruleDisabled': False,
                'awsIotSqlVersion': '2016-03-23'
            }
        )

        print(f"INFO: IoT Rule created: {rule_name}")
        return rule_name

    except iot_client.exceptions.ResourceAlreadyExistsException:
        print(f"WARNING: Rule {rule_name} already exists, recreating...")
        # Delete and recreate
        iot_client.delete_topic_rule(ruleName=rule_name)
        return create_iot_rule(action_id, equipment_id, tenant_id, conditions, equipment_info, action_type, region)
    except Exception as e:
        raise Exception(f"500::Erro ao criar IoT Rule: {str(e)}")

def cron_from_interval(interval, week_days, month_days, time_str):
    """Convert interval schedule to cron expression"""
    if not isinstance(time_str, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', time_str):
        raise Exception("400::Horário inválido. Use HH:MM")
    if interval == 'weekly' and (not week_days or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 or d > 6 for d in week_days)):
        raise Exception("400::weekDay deve conter valores entre 0 e 6")
    if interval == 'monthly' and (not month_days or any(isinstance(d, bool) or not isinstance(d, int) or d < 1 or d > 31 for d in month_days)):
        raise Exception("400::monthDay deve conter valores entre 1 e 31")
    time_parts = time_str.split(":")
    minute = int(time_parts[1])
    hour = int(time_parts[0])

    if interval == 'daily':
        return f"{minute} {hour} * * ? *"
    elif interval == 'weekly':
        # Convert 0-6 (Sunday-Saturday) to cron format
        days = ','.join([str((d + 1) % 7 or 7) for d in week_days])
        return f"{minute} {hour} ? * {days} *"
    elif interval == 'monthly':
        days = ','.join([str(d) for d in month_days])
        return f"{minute} {hour} {days} * ? *"
    else:
        raise Exception("400::Tipo de intervalo inválido")

def cron_from_cron_obj(cron_obj):
    """Convert cron object to AWS EventBridge cron expression"""
    if not isinstance(cron_obj, dict):
        raise Exception("400::cron deve ser um objeto")
    allowed_cron = re.compile(r'^[0-9*?,/\-]+$')
    if any(not isinstance(value, (str, int)) or not allowed_cron.fullmatch(str(value)) for value in cron_obj.values()):
        raise Exception("400::Expressão cron contém caracteres inválidos")
    minute = cron_obj.get('minute', '*')
    hour = cron_obj.get('hour', '*')
    day_of_month = cron_obj.get('dayOfMonth', '*')
    month = cron_obj.get('month', '*')
    day_of_week = cron_obj.get('dayOfWeek', '?')

    # AWS EventBridge requires ? for day of week if day of month is specified
    if day_of_month != '*' and day_of_week != '?':
        day_of_month = '?'

    return f"{minute} {hour} {day_of_month} {month} {day_of_week} *"

def create_eventbridge_schedule(action_id, tenant_id, equipment_id, schedule_config, actions, region):
    """Create EventBridge schedule for timed actions"""
    print("INFO: Creating EventBridge schedule...")

    # Build cron expression
    if schedule_config.get('type') == 'cron':
        cron_expr = cron_from_cron_obj(schedule_config['cron'])
    elif schedule_config.get('type') == 'interval':
        cron_expr = cron_from_interval(
            schedule_config['interval'],
            schedule_config.get('weekDay', []),
            schedule_config.get('monthDay', []),
            schedule_config['time']
        )
    else:
        raise Exception("400::Tipo de schedule inválido")

    # Schedule name
    schedule_name = f"ati_{tenant_id}_{action_id}".replace('-', '_')[:64]

    # Create schedule
    scheduler_client = boto3.client('scheduler', region_name=region)

    try:
        response = scheduler_client.create_schedule(
            Name=schedule_name,
            State='ENABLED',
            ScheduleExpression=f"cron({cron_expr})",
            ScheduleExpressionTimezone="America/Sao_Paulo",
            FlexibleTimeWindow={'Mode': 'OFF'},
            Target={
                'Arn': environ.get('SCHEDULE_EXECUTOR_LAMBDA_ARN'),
                'RoleArn': environ.get('EVENTBRIDGE_ROLE_ARN'),
                'Input': json.dumps({
                    'tenantId': tenant_id,
                    'equipmentId': equipment_id,
                    'actionId': action_id,
                    'actions': actions
                })
            }
        )

        print(f"INFO: EventBridge schedule created: {schedule_name}")
        return schedule_name, response['ScheduleArn']

    except Exception as e:
        raise Exception(f"500::Erro ao criar schedule: {str(e)}")

def save_action_to_dynamodb(action_data, region):
    """Save action configuration to DynamoDB"""
    print("INFO: Saving action to DynamoDB...")

    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])

    try:
        table.put_item(Item=action_data)
        print(f"INFO: Action {action_data['actionId']} saved to DynamoDB")
        return True
    except Exception as e:
        raise Exception(f"500::Erro ao salvar ação: {str(e)}")

def handler(event, context):
    """
    Create Virtual Technician Action

    POST /saas/virtual-technician
    Body: {
      "name": "Action name",
      "type": "action|notification|schedule",
      "equipmentId": "uuid",
      "status": "active|inactive",
      "conditions": [...],      // For action/notification
      "actions": [...],         // For action/schedule
      "notification": {...},    // For notification
      "schedule": {...}         // For schedule
    }

    Returns: Created action details
    """
    print("INFO: Starting virtual technician create...")
    try:
        # 1. Validate token
        if "Authorization" not in event['headers']:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(
            event['headers']['Authorization'],
            environ['REGION']
        )

        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Authorize
        auth = authorizeSaaS(
            token_data['data'],
            environ['DYNAMODB_TABLE_ROLES'],
            'virtual-technician',
            'write',
            environ['REGION']
        )

        # 3. Parse body
        if 'body' not in event or not event['body']:
            raise Exception("400::Body não informado")

        body_raw = event['body']
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')


        body = json.loads(body_raw)
        target_scope = resolveSaaSTargetScope(
            auth,
            body.get('organizationId'),
            body.get('tenantId'),
            require_tenant=True
        )
        target_org_id = target_scope['organizationId']
        target_tenant_id = target_scope['tenantId']

        # 4. Validate required fields
        name = body.get('name', '').strip()
        action_type = body.get('type', '').strip()
        equipment_id = body.get('equipmentId', '').strip()
        status = body.get('status', 'active').strip()

        if not name or len(name) < 3 or len(name) > 80:
            raise Exception("400::Nome deve ter entre 3 e 80 caracteres")

        if action_type not in ['action', 'notification', 'schedule']:
            raise Exception("400::Tipo deve ser: action, notification ou schedule")

        if not equipment_id:
            raise Exception("400::ID do equipamento não informado")

        if status not in ['active', 'inactive']:
            raise Exception("400::Status deve ser: active ou inactive")

        # 5. Get equipment info
        equipment_info = get_equipment_info(equipment_id, target_tenant_id, environ['REGION'])
        if equipment_info.get('organizationId') != target_org_id:
            raise Exception("403::Equipamento não pertence à organização informada")

        # 6. Generate action ID
        action_id = str(uuid4())
        now = datetime.utcnow().isoformat() + 'Z'

        # 7. Process based on type
        rule_name = None
        schedule_name = None
        schedule_arn = None

        if action_type in ['action', 'notification']:
            # Validate conditions
            conditions = body.get('conditions', [])
            if not conditions or not isinstance(conditions, list):
                raise Exception("400::Condições não informadas")
            validate_conditions(conditions)

            # Validate actions (for action type)
            if action_type == 'action':
                actions = body.get('actions', [])
                if not actions or not isinstance(actions, list):
                    raise Exception("400::Ações não informadas")
                authorizeScheduledActions(equipment_info, actions, auth)
            else:
                actions = []

            # Validate notification (for notification type)
            if action_type == 'notification':
                notification = body.get('notification', {})
                if not notification or not isinstance(notification, dict):
                    raise Exception("400::Dados de notificação não informados")
                if not notification.get('subject') or not notification.get('message'):
                    raise Exception("400::Subject e message são obrigatórios")
                if not notification.get('recipients') or not isinstance(notification['recipients'], list):
                    raise Exception("400::Recipients deve ser uma lista")
            else:
                notification = {}

            # Create IoT Rule
            rule_name = create_iot_rule(
                action_id,
                equipment_id,
                target_tenant_id,
                conditions,
                equipment_info,
                action_type,
                environ['REGION']
            )

        elif action_type == 'schedule':
            # Validate schedule
            schedule_config = body.get('schedule', {})
            if not schedule_config or not isinstance(schedule_config, dict):
                raise Exception("400::Configuração de schedule não informada")

            # Validate actions
            actions = body.get('actions', [])
            if not actions or not isinstance(actions, list):
                raise Exception("400::Ações não informadas")
            authorizeScheduledActions(equipment_info, actions, auth)

            conditions = []
            notification = {}

            # Create EventBridge schedule
            schedule_name, schedule_arn = create_eventbridge_schedule(
                action_id,
                target_tenant_id,
                equipment_id,
                schedule_config,
                actions,
                environ['REGION']
            )

        # 8. Save to DynamoDB
        action_data = {
            'pk': f"TENANT#{target_tenant_id}#VIRTUAL_TECHNICIAN",
            'sk': f"ACTION#{action_id}",
            'actionId': action_id,
            'tenantId': target_tenant_id,
            'organizationId': target_org_id,
            'equipmentId': equipment_id,
            'equipmentName': equipment_info['name'],
            'name': name,
            'type': action_type,
            'status': status,
            'conditions': conditions,
            'actions': actions,
            'notification': notification,
            'schedule': body.get('schedule', {}),
            'ruleName': rule_name,
            'scheduleName': schedule_name,
            'scheduleArn': schedule_arn,
            'createdAt': now,
            'createdBy': token_data['data']['sub'],
            'createdTenantRole': auth.get('tenantRole'),
            'updatedAt': now
        }

        try:
            save_action_to_dynamodb(action_data, environ['REGION'])
        except Exception as e:
            # Rollback do recurso externo já criado, para não deixar uma IoT Rule ou
            # EventBridge Schedule órfã disparando para um actionId que não existe
            print(f"WARNING: Falha ao salvar ação, desfazendo recursos externos criados: {e}")
            if rule_name:
                try:
                    boto3.client('iot', region_name=environ['REGION']).delete_topic_rule(ruleName=rule_name)
                except Exception as cleanup_err:
                    print(f"ERROR: Falha ao desfazer IoT Rule {rule_name}: {cleanup_err}")
            if schedule_name:
                try:
                    boto3.client('scheduler', region_name=environ['REGION']).delete_schedule(Name=schedule_name)
                except Exception as cleanup_err:
                    print(f"ERROR: Falha ao desfazer schedule {schedule_name}: {cleanup_err}")
            raise

        print(f"INFO: Action {action_id} created successfully")

        # 9. Return response
        response_data = {
            'actionId': action_id,
            'name': name,
            'type': action_type,
            'equipmentId': equipment_id,
            'equipmentName': equipment_info['name'],
            'status': status,
            'createdAt': now
        }

        if rule_name:
            response_data['ruleName'] = rule_name
        if schedule_name:
            response_data['scheduleName'] = schedule_name

        return lambdaReturn({
            "statusCode": 201,
            "message": "Ação criada com sucesso",
            "data": response_data
        })

    except Exception as e:
        print(f"ERROR: {str(e)}")
        try:
            err = str(e).split("::")
            return lambdaReturn({
                "statusCode": int(err[0]),
                "message": err[1]
            })
        except:
            return lambdaReturn({
                "statusCode": 500,
                "message": f"Erro interno: {str(e)}"
            })
