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
    print("INFO: Starting forgot password flow...")
    try:
        # 1. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 2. Validação de Input
        validate = validateInput([
            {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'}
        ], body)

        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 3. Execução
        process_request(body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Se o e-mail estiver cadastrado, você receberá um código de recuperação"
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
    email = body['email']

    # 1. Obter Config do Cognito do Layer
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")

    client = boto3.client('cognito-idp', region_name=region)

    # 2. Preparar parâmetros
    params = {
        'ClientId': config['clientId'],
        'Username': email
    }

    # Adicionar SECRET_HASH se necessário via layer
    if config.get('clientSecret'):
        params['SecretHash'] = genSecretHash(config['clientId'], config['clientSecret'], email)

    # 3. Solicitar reset
    try:
        client.forgot_password(**params)
        print(f"INFO: Password reset code sent to {email}")
    except client.exceptions.UserNotFoundException:
        # Por segurança, não informamos se o usuário existe
        print("WARNING: User not found, but returning success for security")
    except Exception as e:
        # Qualquer outra falha do Cognito também retorna sucesso genérico,
        # para não expor via status code se o e-mail existe ou não
        print(f"WARNING: Erro ao processar forgot-password para {email}, retornando sucesso genérico: {e}")
