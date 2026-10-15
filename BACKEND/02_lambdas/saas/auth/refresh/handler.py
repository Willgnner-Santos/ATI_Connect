import json
import boto3
import base64
from os import environ
from plataforma_ati import (
    lambdaReturn, 
    validateInput, 
    getSaaSConfig, 
    genSecretHash
)

def handler(event, context):
    print("INFO: Starting SaaS token refresh...")
    try:
        # 1. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 2. Validação de Input
        validate = validateInput([
            {'field': 'refreshToken', 'type': 'str', 'mandatory': True},
            {'field': 'username', 'type': 'str', 'mandatory': True}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 3. Execução
        result = process_request(body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Token renovado com sucesso",
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

def process_request(body, region, stage):
    refresh_token = body['refreshToken']
    username = body['username']

    # 1. Obter Config do Cognito do Layer
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")

    client = boto3.client('cognito-idp', region_name=region)

    # 2. Preparar parâmetros de autenticação
    auth_params = {
        'REFRESH_TOKEN': refresh_token
    }

    # Se o client tem secret, adicionar SECRET_HASH via layer
    if config.get('clientSecret'):
        auth_params['SECRET_HASH'] = genSecretHash(config['clientId'], config['clientSecret'], username)

    # 3. Renovar token
    try:
        response = client.admin_initiate_auth(
            UserPoolId=config['userPoolId'],
            ClientId=config['clientId'],
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters=auth_params
        )
    except client.exceptions.NotAuthorizedException:
        raise Exception("401::Refresh token inválido ou expirado")
    except Exception as e:
        raise Exception(f"500::Erro ao renovar token: {str(e)}")

    return {
        "access_token": response['AuthenticationResult']['AccessToken'],
        "id_token": response['AuthenticationResult']['IdToken'],
        "token_type": response['AuthenticationResult']['TokenType'],
        "expires_in": response['AuthenticationResult']['ExpiresIn']
    }
