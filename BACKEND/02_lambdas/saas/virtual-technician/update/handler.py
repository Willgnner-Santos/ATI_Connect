import json
import re
import boto3
from os import environ
from datetime import datetime
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope,
    authorizeScheduledActions
)

VALID_OPERATORS = ('=', '>', '<', '>=', '<=', '!=')
VALID_VARIABLE_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
VALID_LOGICAL_OPERATORS = ('AND', 'OR')

def validate_conditions(conditions):
    """Impede injeção na query SQL da IoT Rule: variable segue um padrão restrito
    de identificador, e operator só pode ser um dos comparadores conhecidos."""
    for i, condition in enumerate(conditions):
        variable = condition.get('variable')
        operator = condition.get('operator')
        if not variable or not VALID_VARIABLE_PATTERN.match(str(variable)):
            raise Exception(f"400::Nome de variável inválido: '{variable}'")
        if operator not in VALID_OPERATORS:
            raise Exception(f"400::Operador inválido: '{operator}'")
        if i < len(conditions) - 1 and condition.get('condition', 'AND').upper() not in VALID_LOGICAL_OPERATORS:
            raise Exception("400::Conector lógico deve ser AND ou OR")


def sql_literal(value, value_type):
    if value_type == 'boolean':
        return str(value).lower()
    if value_type == 'number':
        try:
            return str(float(value))
        except (TypeError, ValueError):
            raise Exception("400::Valor numérico inválido")
    if value_type != 'string':
        raise Exception("400::Tipo de condição inválido")
    return json.dumps(str(value), ensure_ascii=False)

def delete_old_iot_rule(rule_name, region):
    """Delete old IoT Rule if exists"""
    if not rule_name:
        return

    print(f"INFO: Deleting old IoT Rule: {rule_name}")
    iot_client = boto3.client('iot', region_name=region)

    try:
        iot_client.delete_topic_rule(ruleName=rule_name)
        print(f"INFO: Old IoT Rule deleted: {rule_name}")
    except iot_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Rule {rule_name} not found, skipping deletion")
    except Exception as e:
        print(f"WARNING: Failed to delete old rule: {e}")

def set_iot_rule_enabled(rule_name, enabled, region):
    """Habilita/desabilita a IoT Rule sem recriá-la, para refletir o campo
    'status' da ação sem deixar o gatilho externo ativo com a ação 'inactive'."""
    if not rule_name:
        return
    iot_client = boto3.client('iot', region_name=region)
    try:
        current = iot_client.get_topic_rule(ruleName=rule_name)
        payload = current['rule']
        payload['ruleDisabled'] = not enabled
        iot_client.replace_topic_rule(ruleName=rule_name, topicRulePayload=payload)
        print(f"INFO: IoT Rule {rule_name} {'habilitada' if enabled else 'desabilitada'}")
    except iot_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Rule {rule_name} not found, skipping enable/disable")
    except Exception as e:
        print(f"WARNING: Failed to set enabled={enabled} on rule {rule_name}: {e}")

def set_schedule_enabled(schedule_name, enabled, region):
    """Habilita/desabilita o EventBridge Schedule sem recriá-lo."""
    if not schedule_name:
        return
    scheduler_client = boto3.client('scheduler', region_name=region)
    try:
        current = scheduler_client.get_schedule(Name=schedule_name)
        scheduler_client.update_schedule(
            Name=schedule_name,
            ScheduleExpression=current['ScheduleExpression'],
            ScheduleExpressionTimezone=current.get('ScheduleExpressionTimezone', 'America/Sao_Paulo'),
            FlexibleTimeWindow=current['FlexibleTimeWindow'],
            Target=current['Target'],
            State='ENABLED' if enabled else 'DISABLED'
        )
        print(f"INFO: Schedule {schedule_name} {'habilitado' if enabled else 'desabilitado'}")
    except scheduler_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Schedule {schedule_name} not found, skipping enable/disable")
    except Exception as e:
        print(f"WARNING: Failed to set enabled={enabled} on schedule {schedule_name}: {e}")

def delete_old_schedule(schedule_name, region):
    """Delete old EventBridge Schedule if exists"""
    if not schedule_name:
        return

    print(f"INFO: Deleting old schedule: {schedule_name}")
    scheduler_client = boto3.client('scheduler', region_name=region)

    try:
        scheduler_client.delete_schedule(Name=schedule_name)
        print(f"INFO: Old schedule deleted: {schedule_name}")
    except scheduler_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Schedule {schedule_name} not found, skipping deletion")
    except Exception as e:
        print(f"WARNING: Failed to delete old schedule: {e}")

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
    if not topic or "'" in topic or any(ord(char) < 32 for char in topic):
        raise Exception("400::Tópico MQTT inválido para criação da regra")
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

        formatted_value = sql_literal(value, cond_type)

        condition_parts.append(f"{variable} {operator} {formatted_value}")

    query_conditions = []
    for i, condition in enumerate(conditions):
        query_conditions.append(condition_parts[i])
        if i < len(conditions) - 1:
            logical_op = condition.get('condition', 'AND').upper()
            query_conditions.append(logical_op)

    query += ' '.join(query_conditions) + ')'
    return query

def create_iot_rule(action_id, equipment_id, tenant_id, conditions, equipment_info, action_type, region):
    """Create IoT Rule for action or notification"""
    print("INFO: Creating new IoT Rule...")

    sql_query = build_iot_rule_query(
        equipment_info['topic'],
        conditions,
        tenant_id,
        action_id,
        equipment_id
    )

    rule_name = f"ati_{tenant_id}_{action_type}_{action_id}".replace('-', '_')[:128]

    if action_type == 'action':
        queue_url = environ.get('ACTION_QUEUE_URL')
    else:
        queue_url = environ.get('NOTIFICATION_QUEUE_URL')

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

    if day_of_month != '*' and day_of_week != '?':
        day_of_month = '?'

    return f"{minute} {hour} {day_of_month} {month} {day_of_week} *"

def create_eventbridge_schedule(action_id, tenant_id, equipment_id, schedule_config, actions, region):
    """Create EventBridge schedule for timed actions"""
    print("INFO: Creating new EventBridge schedule...")

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

    schedule_name = f"ati_{tenant_id}_{action_id}".replace('-', '_')[:64]

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

def handler(event, context):
    """
    Update Virtual Technician Action

    PUT /saas/virtual-technician/{actionId}
    Body: {
      "name": "New name (optional)",
      "status": "active|inactive (optional)",
      "conditions": [...],      // For action/notification (optional)
      "actions": [...],         // For action/schedule (optional)
      "notification": {...},    // For notification (optional)
      "schedule": {...}         // For schedule (optional)
    }

    Returns: Updated action details
    """
    print("INFO: Starting virtual technician update...")
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

        # 3. Parse path parameter
        if 'pathParameters' not in event or 'id' not in event['pathParameters']:
            raise Exception("400::Action ID não informado")

        action_id = event['pathParameters']['id']

        # 4. Parse body
        if 'body' not in event or not event['body']:
            raise Exception("400::Body não informado")

        body = json.loads(event['body'])
        target_scope = resolveSaaSTargetScope(
            auth,
            body.get('organizationId'),
            body.get('tenantId'),
            require_tenant=True
        )
        target_org_id = target_scope['organizationId']
        target_tenant_id = target_scope['tenantId']

        # 5. Get existing action
        dynamodb = boto3.resource('dynamodb', region_name=environ['REGION'])
        table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])

        response = table.get_item(
            Key={
                'pk': f"TENANT#{target_tenant_id}#VIRTUAL_TECHNICIAN",
                'sk': f"ACTION#{action_id}"
            }
        )

        if 'Item' not in response:
            raise Exception("404::Ação não encontrada")

        existing_action = response['Item']

        # 6. Verify tenant ownership
        if (existing_action.get('tenantId') != target_tenant_id
                or existing_action.get('organizationId') != target_org_id):
            raise Exception("403::Acesso negado")

        # 7. Update fields
        action_type = existing_action['type']
        equipment_id = existing_action['equipmentId']
        now = datetime.utcnow().isoformat() + 'Z'

        # Update basic fields
        name = body.get('name', existing_action.get('name'))
        status = body.get('status', existing_action.get('status'))

        if status not in ['active', 'inactive']:
            raise Exception("400::Status deve ser: active ou inactive")

        # 8. Handle type-specific updates
        rule_name = existing_action.get('ruleName')
        schedule_name = existing_action.get('scheduleName')
        schedule_arn = existing_action.get('scheduleArn')

        if action_type in ['action', 'notification']:
            # Check if conditions or actions changed
            conditions_changed = 'conditions' in body
            actions_changed = 'actions' in body and action_type == 'action'
            notification_changed = 'notification' in body and action_type == 'notification'

            if conditions_changed or actions_changed or notification_changed:
                # Need to recreate IoT Rule
                print("INFO: Conditions/actions changed, recreating IoT Rule...")

                # Validar condições ANTES de qualquer chamada à AWS (evita SQL
                # injection na IoT Rule e evita deletar a regra antiga por causa
                # de um erro de validação que só seria percebido depois)
                new_conditions = body.get('conditions', existing_action.get('conditions', []))
                validate_conditions(new_conditions)

                # Get equipment info
                equipment_info = get_equipment_info(equipment_id, target_tenant_id, environ['REGION'])

                # Grant de execução das novas ações, também antes de tocar na AWS:
                # agendar é enviar comando, então vale o mesmo 'executeRoles'
                if actions_changed:
                    authorizeScheduledActions(equipment_info, body.get('actions'), auth)

                # Delete old rule
                delete_old_iot_rule(rule_name, environ['REGION'])

                # Create new rule with updated conditions
                rule_name = create_iot_rule(
                    action_id,
                    equipment_id,
                    target_tenant_id,
                    new_conditions,
                    equipment_info,
                    action_type,
                    environ['REGION']
                )

            # Update stored data
            conditions = body.get('conditions', existing_action.get('conditions', []))
            actions = body.get('actions', existing_action.get('actions', []))
            notification = body.get('notification', existing_action.get('notification', {}))

            # Se só o status mudou (sem recriar a regra), refletir active/inactive
            # no gatilho externo — senão a IoT Rule continua disparando mesmo com
            # a ação marcada como 'inactive' no DynamoDB
            status_changed = status != existing_action.get('status')
            if status_changed and not (conditions_changed or actions_changed or notification_changed):
                set_iot_rule_enabled(rule_name, status == 'active', environ['REGION'])

        elif action_type == 'schedule':
            # Check if schedule or actions changed
            schedule_changed = 'schedule' in body
            actions_changed = 'actions' in body

            if schedule_changed or actions_changed:
                # Need to recreate schedule
                print("INFO: Schedule/actions changed, recreating EventBridge schedule...")

                new_schedule = body.get('schedule', existing_action.get('schedule', {}))
                new_actions = body.get('actions', existing_action.get('actions', []))

                # Grant de execução antes de deletar o schedule antigo: um comando não
                # liberado para a role não pode entrar pela automação
                if actions_changed:
                    equipment_info = get_equipment_info(equipment_id, target_tenant_id, environ['REGION'])
                    authorizeScheduledActions(equipment_info, new_actions, auth)

                # Delete old schedule
                delete_old_schedule(schedule_name, environ['REGION'])

                schedule_name, schedule_arn = create_eventbridge_schedule(
                    action_id,
                    target_tenant_id,
                    equipment_id,
                    new_schedule,
                    new_actions,
                    environ['REGION']
                )

            # Update stored data
            conditions = []
            actions = body.get('actions', existing_action.get('actions', []))
            notification = {}

            # Se só o status mudou (sem recriar o schedule), refletir active/inactive
            status_changed = status != existing_action.get('status')
            if status_changed and not (schedule_changed or actions_changed):
                set_schedule_enabled(schedule_name, status == 'active', environ['REGION'])

        # 9. Update DynamoDB
        updated_action = {
            'pk': f"TENANT#{target_tenant_id}#VIRTUAL_TECHNICIAN",
            'sk': f"ACTION#{action_id}",
            'actionId': action_id,
            'tenantId': target_tenant_id,
            'organizationId': target_org_id,
            'equipmentId': equipment_id,
            'equipmentName': existing_action.get('equipmentName'),
            'name': name,
            'type': action_type,
            'status': status,
            'conditions': conditions,
            'actions': actions,
            'notification': notification,
            'schedule': body.get('schedule', existing_action.get('schedule', {})),
            'ruleName': rule_name,
            'scheduleName': schedule_name,
            'scheduleArn': schedule_arn,
            'createdAt': existing_action.get('createdAt'),
            'createdBy': existing_action.get('createdBy'),
            'createdTenantRole': existing_action.get('createdTenantRole', auth.get('tenantRole')),
            'updatedAt': now
        }

        table.put_item(Item=updated_action)

        print(f"INFO: Action {action_id} updated successfully")

        # 10. Return response
        response_data = {
            'actionId': action_id,
            'name': name,
            'type': action_type,
            'status': status,
            'updatedAt': now
        }

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ação atualizada com sucesso",
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
