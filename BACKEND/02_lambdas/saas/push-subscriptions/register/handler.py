import json
import boto3
import uuid
from os import environ
from datetime import datetime, timezone
from plataforma_ati import validateTokenSaaS, lambdaReturn, getAllDynamoItems


def handler(event, context):
    print("INFO: Starting push subscription register...")
    try:
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        body = json.loads(event.get('body') or '{}')
        device_token = body.get('deviceToken')
        platform = body.get('platform')

        if not device_token:
            raise Exception("400::deviceToken é obrigatório")
        if platform not in ('android', 'ios'):
            raise Exception("400::platform deve ser 'android' ou 'ios'")

        user = token_data['data']
        tenant_id = user.get('tenantId')
        user_id = user.get('userId') or user.get('sub')

        if not tenant_id or not user_id:
            raise Exception("400::Usuário sem tenantId ou userId no token")

        result = process_request(tenant_id, user_id, device_token, platform, environ['REGION'])

        return lambdaReturn({
            "statusCode": 201,
            "message": "Push subscription registrada com sucesso",
            "data": result
        })

    except Exception as e:
        print(f"ERROR: {str(e)}")
        try:
            err = str(e).split("::")
            return lambdaReturn({"statusCode": int(err[0]), "message": err[1]})
        except:
            return lambdaReturn({"statusCode": 500, "message": f"Erro interno: {str(e)}"})


def process_request(tenant_id, user_id, device_token, platform, region):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_PUSH_SUBSCRIPTIONS'])

    pk = f"TENANT#{tenant_id}#USER#{user_id}"

    # Update existing subscription for this device token if it exists
    existing = getAllDynamoItems(table,
        KeyConditionExpression='pk = :pk',
        FilterExpression='deviceToken = :token',
        ExpressionAttributeValues={':pk': pk, ':token': device_token}
    )

    if existing:
        sub = existing[0]
        table.update_item(
            Key={'pk': pk, 'sk': sub['sk']},
            UpdateExpression='SET updatedAt = :ts',
            ExpressionAttributeValues={':ts': datetime.now(timezone.utc).isoformat()}
        )
        return {'subscriptionId': sub['subscriptionId'], 'platform': platform}

    subscription_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    table.put_item(Item={
        'pk': pk,
        'sk': f"SUBSCRIPTION#{subscription_id}",
        'subscriptionId': subscription_id,
        'tenantId': tenant_id,
        'userId': user_id,
        'deviceToken': device_token,
        'platform': platform,
        'createdAt': now,
        'updatedAt': now,
    })

    return {'subscriptionId': subscription_id, 'platform': platform}
