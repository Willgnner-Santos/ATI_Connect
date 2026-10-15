import json
import boto3
from os import environ
from plataforma_ati import lambdaReturn

def handler(event, context):
    print("INFO: Starting SaaS logout...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        # 2. Execução
        process_request(auth_header, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Logout realizado com sucesso"
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

def process_request(authorization, region):
    if not authorization.startswith('Bearer '):
        raise Exception("400::Formato de token inválido")

    token = authorization.replace('Bearer ', '').strip()

    # Global sign-out no Cognito
    client = boto3.client('cognito-idp', region_name=region)

    try:
        client.global_sign_out(AccessToken=token)
        print("INFO: User signed out successfully")
    except client.exceptions.NotAuthorizedException:
        # Token já estava inválido - não é um erro crítico
        print("WARNING: Token already invalid, continuing...")
        pass
    except Exception as e:
        raise Exception(f"500::Erro ao realizar logout: {str(e)}")
