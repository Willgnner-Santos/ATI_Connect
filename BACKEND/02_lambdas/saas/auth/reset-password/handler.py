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
    print("INFO: Starting password reset confirmation...")
    try:
        # 1. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 2. Validação de Input
        validate = validateInput([
            {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
            {'field': 'code', 'type': 'str', 'mandatory': True},
            {'field': 'newPassword', 'type': 'str', 'mandatory': True}
        ], body)

        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 3. Execução
        process_request(body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Senha alterada com sucesso"
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
    code = body['code']
    new_password = body['newPassword']

    # 1. Validar força da senha (mínimo)
    if len(new_password) < 8:
        raise Exception("400::Senha deve ter no mínimo 8 caracteres")

    # 2. Obter Config do Cognito do Layer
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")

    client = boto3.client('cognito-idp', region_name=region)

    # 3. Preparar parâmetros
    params = {
        'ClientId': config['clientId'],
        'Username': email,
        'ConfirmationCode': code,
        'Password': new_password
    }

    # Adicionar SECRET_HASH se necessário via layer
    if config.get('clientSecret'):
        params['SecretHash'] = genSecretHash(config['clientId'], config['clientSecret'], email)

    # 4. Confirmar reset
    try:
        client.confirm_forgot_password(**params)
        print(f"INFO: Password reset successfully for {email}")
    except client.exceptions.CodeMismatchException:
        raise Exception("400::Código de verificação inválido")
    except client.exceptions.ExpiredCodeException:
        raise Exception("400::Código de verificação expirado. Solicite um novo código.")
    except client.exceptions.InvalidPasswordException as e:
        raise Exception(f"400::Senha inválida: {str(e)}")
    except client.exceptions.UserNotFoundException:
        # Mesma resposta de "código inválido" para não revelar se o e-mail existe
        raise Exception("400::Código de verificação inválido")
    except Exception as e:
        raise Exception(f"500::Erro ao confirmar alteração de senha: {str(e)}")

    # 5. Revogar sessões antigas (tokens emitidos antes do reset não devem continuar válidos)
    try:
        client.admin_user_global_sign_out(
            UserPoolId=config['userPoolId'],
            Username=email
        )
        print(f"INFO: Sessões antigas revogadas para {email}")
    except Exception as e:
        print(f"WARNING: Falha ao revogar sessões antigas de {email}: {e}")
