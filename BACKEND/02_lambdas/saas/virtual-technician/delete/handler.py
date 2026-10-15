import json
import boto3
from os import environ
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope
)

def delete_iot_rule(rule_name, region):
    """Delete IoT Rule"""
    if not rule_name:
        print("WARNING: No rule name provided, skipping deletion")
        return

    print(f"INFO: Deleting IoT Rule: {rule_name}")
    iot_client = boto3.client('iot', region_name=region)

    try:
        iot_client.delete_topic_rule(ruleName=rule_name)
        print(f"INFO: IoT Rule deleted: {rule_name}")
    except iot_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Rule {rule_name} not found, already deleted")
    except Exception as e:
        print(f"ERROR: Failed to delete rule {rule_name}: {e}")
        raise Exception(f"500::Erro ao deletar IoT Rule: {str(e)}")

def delete_eventbridge_schedule(schedule_name, region):
    """Delete EventBridge Schedule"""
    if not schedule_name:
        print("WARNING: No schedule name provided, skipping deletion")
        return

    print(f"INFO: Deleting EventBridge schedule: {schedule_name}")
    scheduler_client = boto3.client('scheduler', region_name=region)

    try:
        scheduler_client.delete_schedule(Name=schedule_name)
        print(f"INFO: Schedule deleted: {schedule_name}")
    except scheduler_client.exceptions.ResourceNotFoundException:
        print(f"WARNING: Schedule {schedule_name} not found, already deleted")
    except Exception as e:
        print(f"ERROR: Failed to delete schedule {schedule_name}: {e}")
        raise Exception(f"500::Erro ao deletar schedule: {str(e)}")

def handler(event, context):
    """
    Delete Virtual Technician Action

    DELETE /saas/virtual-technician/{actionId}

    Effects:
    - Removes action from DynamoDB
    - Deletes IoT Rule (if type action/notification)
    - Deletes EventBridge Schedule (if type schedule)

    Returns: Success message
    """
    print("INFO: Starting virtual technician delete...")
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
            'delete',
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

        # 4. Get action from DynamoDB
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

        # 6. Delete associated resources based on type
        action_type = action.get('type')
        action_name = action.get('name')

        if action_type in ['action', 'notification']:
            # Delete IoT Rule
            rule_name = action.get('ruleName')
            if rule_name:
                delete_iot_rule(rule_name, environ['REGION'])
            else:
                print("WARNING: No rule name found in action")

        elif action_type == 'schedule':
            # Delete EventBridge Schedule
            schedule_name = action.get('scheduleName')
            if schedule_name:
                delete_eventbridge_schedule(schedule_name, environ['REGION'])
            else:
                print("WARNING: No schedule name found in action")

        # 7. Delete from DynamoDB
        print(f"INFO: Deleting action {action_id} from DynamoDB...")
        table.delete_item(
            Key={
                'pk': f"TENANT#{target_scope['tenantId']}#VIRTUAL_TECHNICIAN",
                'sk': f"ACTION#{action_id}"
            }
        )

        print(f"INFO: Action {action_id} deleted successfully")

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ação deletada com sucesso",
            "data": {
                "actionId": action_id,
                "name": action_name,
                "type": action_type
            }
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
