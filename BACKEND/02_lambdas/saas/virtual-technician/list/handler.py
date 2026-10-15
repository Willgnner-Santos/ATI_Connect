import json
import boto3
from os import environ
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, isGlobalOrganizationRole
)

def handler(event, context):
    print("INFO: Starting virtual technician list...")
    try:
        # 1. Validate token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Authorize
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'virtual-technician', 'read', environ['REGION'])

        # 3. Parse query parameters
        query_params = event.get('queryStringParameters') or {}

        # 4. Process Request
        result = process_request(auth, query_params, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ações listadas com sucesso",
            "data": result
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

MAX_PAGES = 10

def paginate(table, method_name, kwargs):
    """Segue LastEvaluatedKey até MAX_PAGES páginas, sinalizando truncamento
    em vez de retornar uma listagem parcial sem aviso."""
    method = getattr(table, method_name)
    items = []
    truncated = False
    for _ in range(MAX_PAGES):
        response = method(**kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    else:
        truncated = 'LastEvaluatedKey' in response
    return items, truncated

def process_request(auth, query_params, region):
    equipment_id = query_params.get('equipmentId')
    action_type = query_params.get('type')
    status_filter = query_params.get('status')
    org_id_filter = query_params.get('organizationId')
    tenant_id_filter = query_params.get('tenantId')

    tenant_id = auth['tenantId']
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    org_id = auth['organizationId']

    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])

    actions = []
    truncated = False

    # Se for superadmin ou admin de org (sem tenant fixo)
    if has_global_scope or tenant_id == 'none':
        filter_expressions = []
        expression_values = {}

        if not has_global_scope:
            # Org admin deve ver apenas ações de sua organização
            filter_expressions.append('organizationId = :orgId')
            expression_values[':orgId'] = org_id
        elif org_id_filter:
            # Superadmin/collaborator pode filtrar por organização específica
            filter_expressions.append('organizationId = :orgId')
            expression_values[':orgId'] = org_id_filter

        if tenant_id_filter:
            filter_expressions.append('tenantId = :tenantId')
            expression_values[':tenantId'] = tenant_id_filter

        if equipment_id:
            filter_expressions.append('equipmentId = :equipmentId')
            expression_values[':equipmentId'] = equipment_id

        if action_type:
            filter_expressions.append('#type = :type')
            expression_values[':type'] = action_type

        if status_filter:
            filter_expressions.append('#status = :status')
            expression_values[':status'] = status_filter

        scan_kwargs = {}
        if filter_expressions:
            scan_kwargs['FilterExpression'] = ' AND '.join(filter_expressions)
            scan_kwargs['ExpressionAttributeValues'] = expression_values

            expr_names = {}
            if action_type:
                expr_names['#type'] = 'type'
            if status_filter:
                expr_names['#status'] = 'status'
            if expr_names:
                scan_kwargs['ExpressionAttributeNames'] = expr_names

        actions, truncated = paginate(table, 'scan', scan_kwargs)

    else:
        # Manager de tenant (busca otimizada pela PK do tenant)
        key_condition = 'pk = :pk'
        expression_values = {
            ':pk': f"TENANT#{tenant_id}#VIRTUAL_TECHNICIAN"
        }

        filter_expressions = []

        if equipment_id:
            filter_expressions.append('equipmentId = :equipmentId')
            expression_values[':equipmentId'] = equipment_id

        if action_type:
            filter_expressions.append('#type = :type')
            expression_values[':type'] = action_type

        if status_filter:
            filter_expressions.append('#status = :status')
            expression_values[':status'] = status_filter

        query_kwargs = {
            'KeyConditionExpression': key_condition,
            'ExpressionAttributeValues': expression_values
        }

        if filter_expressions:
            query_kwargs['FilterExpression'] = ' AND '.join(filter_expressions)
            query_kwargs['ExpressionAttributeNames'] = {}
            if action_type:
                query_kwargs['ExpressionAttributeNames']['#type'] = 'type'
            if status_filter:
                query_kwargs['ExpressionAttributeNames']['#status'] = 'status'

        actions, truncated = paginate(table, 'query', query_kwargs)

    # Format response
    formatted_actions = []
    for action in actions:
        formatted_action = {
            'actionId': action.get('actionId'),
            'name': action.get('name'),
            'type': action.get('type'),
            'equipmentId': action.get('equipmentId'),
            'equipmentName': action.get('equipmentName'),
            'status': action.get('status'),
            'createdAt': action.get('createdAt'),
            'createdBy': action.get('createdBy'),
            'updatedAt': action.get('updatedAt')
        }

        if action.get('type') == 'action':
            formatted_action['conditions'] = action.get('conditions', [])
            formatted_action['actions'] = action.get('actions', [])
            formatted_action['ruleName'] = action.get('ruleName')
        elif action.get('type') == 'notification':
            formatted_action['conditions'] = action.get('conditions', [])
            formatted_action['notification'] = action.get('notification', {})
            formatted_action['ruleName'] = action.get('ruleName')
        elif action.get('type') == 'schedule':
            formatted_action['schedule'] = action.get('schedule', {})
            formatted_action['actions'] = action.get('actions', [])
            formatted_action['scheduleName'] = action.get('scheduleName')

        formatted_actions.append(formatted_action)

    formatted_actions.sort(key=lambda x: x.get('createdAt') or '', reverse=True)

    return {
        "actions": formatted_actions,
        "total": len(formatted_actions),
        "truncated": truncated
    }
