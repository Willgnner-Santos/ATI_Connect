import boto3
from os import environ
from plataforma_ati import validateTokenSaaS, lambdaReturn


def handler(event, context):
    print("INFO: Starting push subscription delete...")
    try:
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        subscription_id = (event.get('pathParameters') or {}).get('id')
        if not subscription_id:
            raise Exception("400::subscriptionId não informado")

        user = token_data['data']
        tenant_id = user.get('tenantId')
        user_id = user.get('userId') or user.get('sub')

        if not tenant_id or not user_id:
            raise Exception("400::Usuário sem tenantId ou userId no token")

        process_request(tenant_id, user_id, subscription_id, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Push subscription removida com sucesso"
        })

    except Exception as e:
        print(f"ERROR: {str(e)}")
        try:
            err = str(e).split("::")
            return lambdaReturn({"statusCode": int(err[0]), "message": err[1]})
        except:
            return lambdaReturn({"statusCode": 500, "message": f"Erro interno: {str(e)}"})


def process_request(tenant_id, user_id, subscription_id, region):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_PUSH_SUBSCRIPTIONS'])

    pk = f"TENANT#{tenant_id}#USER#{user_id}"
    sk = f"SUBSCRIPTION#{subscription_id}"

    item = table.get_item(Key={'pk': pk, 'sk': sk}).get('Item')
    if not item:
        raise Exception("404::Push subscription não encontrada")

    table.delete_item(Key={'pk': pk, 'sk': sk})
