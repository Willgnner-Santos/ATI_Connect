import json
import boto3
from os import environ
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope
)

def handler(event, context):
    """
    View Virtual Technician Action Details

    GET /saas/virtual-technician/{actionId}

    Returns: Complete action details
    """
    print("INFO: Starting virtual technician view...")
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
            'read',
            environ['REGION']
        )

        # 3. Parse path parameter
        if 'pathParameters' not in event or 'id' not in event['pathParameters']:
            raise Exception("400::Action ID não informado")

        action_id = event['pathParameters']['id']
        query_params = event.get('queryStringParameters') or {}
        target_scope = resolveSaaSTargetScope(
            auth,
            query_params.get('organizationId'),
            query_params.get('tenantId'),
            require_tenant=True
        )

        # 4. Get from DynamoDB
        dynamodb = boto3.resource('dynamodb', region_name=environ['REGION'])
        table = dynamodb.Table(environ['DYNAMODB_TABLE_VIRTUAL_TECHNICIAN'])

        response = table.get_item(
            Key={
                'pk': f"TENANT#{target_scope['tenantId']}#VIRTUAL_TECHNICIAN",
                'sk': f"ACTION#{action_id}"
            }
        )

        if 'Item' not in response:
            raise Exception("404::Ação não encontrada")

        action = response['Item']

        # 5. Verify tenant ownership
        if (action.get('tenantId') != target_scope['tenantId']
                or action.get('organizationId') != target_scope['organizationId']):
            raise Exception("403::Acesso negado")

        # 6. Format response
        action_data = {
            'actionId': action.get('actionId'),
            'name': action.get('name'),
            'type': action.get('type'),
            'equipmentId': action.get('equipmentId'),
            'equipmentName': action.get('equipmentName'),
            'tenantId': action.get('tenantId'),
            'organizationId': action.get('organizationId'),
            'status': action.get('status'),
            'createdAt': action.get('createdAt'),
            'createdBy': action.get('createdBy'),
            'updatedAt': action.get('updatedAt')
        }

        # Add type-specific fields
        if action.get('type') == 'action':
            action_data['conditions'] = action.get('conditions', [])
            action_data['actions'] = action.get('actions', [])
            action_data['ruleName'] = action.get('ruleName')
        elif action.get('type') == 'notification':
            action_data['conditions'] = action.get('conditions', [])
            action_data['notification'] = action.get('notification', {})
            action_data['ruleName'] = action.get('ruleName')
        elif action.get('type') == 'schedule':
            action_data['schedule'] = action.get('schedule', {})
            action_data['actions'] = action.get('actions', [])
            action_data['scheduleName'] = action.get('scheduleName')
            action_data['scheduleArn'] = action.get('scheduleArn')

        print(f"INFO: Action {action_id} retrieved successfully")

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ação encontrada",
            "data": action_data
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
