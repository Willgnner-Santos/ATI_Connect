import json
import base64
import boto3
from os import environ
from datetime import datetime, timezone
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    validateInput,
    findSaaSEquipment,
    authorizeCommandExecution,
    coerceCommandValue,
    DecimalEncoder
)

def publish_action(topic, equipment_id, variable, value, region):
    """Publica o comando no tópico MQTT do equipamento.

    Mesmo formato usado pelo executor do técnico virtual
    (`{type, token, recurso, valor}`): é o contrato que o firmware já entende, e o
    router de telemetria descarta mensagens com `type == 'send'` para não gravar o
    próprio comando como leitura de sensor.
    """
    payload = {
        'type': 'send',
        'token': equipment_id,
        'recurso': variable,
        'valor': value
    }
    print(f"INFO: Publicando comando em topic={topic} variable={variable}")

    iot_client = boto3.client('iot-data', region_name=region)
    try:
        iot_client.publish(
            topic=topic,
            qos=1,
            payload=json.dumps(payload, cls=DecimalEncoder)
        )
    except Exception as e:
        print(f"ERROR: Falha ao publicar comando: {e}")
        raise Exception(f"502::Falha ao enviar o comando ao equipamento: {str(e)}")

    return payload

def handler(event, context):
    print("INFO: Iniciando envio de Ação para Equipamento...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'action', 'execute', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Equipment ID não informado")
        equipment_id = path_params['id']

        # 4. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 5. Validação de Input ('value' não entra no validateInput: o tipo aceito
        # depende do dataType do comando e é validado por coerceCommandValue)
        validate = validateInput([
            {'field': 'command', 'type': 'str', 'mandatory': True}
        ], body)

        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        if 'value' not in body:
            raise Exception("400::Campo 'value' é obrigatório")

        # 6. Execução
        result = process_request(auth, equipment_id, body, token_data['data'], environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Comando enviado com sucesso",
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

def process_request(auth, equipment_id, body, token_claims, region):
    command_name = body['command']

    # 1. Escopo: só acha o equipamento se ele estiver no alcance do chamador
    equipment = findSaaSEquipment(environ['DYNAMODB_TABLE_EQUIPMENT'], equipment_id, auth, region)

    if equipment.get('status') != 'active':
        raise Exception("400::Equipamento inativo não aceita comandos")

    # 2. Grant: o comando existe no equipamento e a role do tenant pode enviá-lo
    action_info = authorizeCommandExecution(equipment, command_name, auth)

    # 3. Valor conforme o dataType declarado no comando
    value = coerceCommandValue(action_info, body['value'])

    topic = equipment.get('topic')
    if not topic:
        raise Exception("500::Equipamento sem tópico MQTT configurado")

    # 4. Publica no IoT Core
    payload = publish_action(topic, equipment_id, action_info['variable'], value, region)

    print(
        f"INFO: Comando '{command_name}' enviado por user={token_claims.get('sub')} "
        f"role={auth.get('role')} equipment={equipment_id}"
    )

    return {
        "equipmentId": equipment_id,
        "command": action_info.get('resource', command_name),
        "value": payload['valor'],
        "topic": topic,
        "sentAt": datetime.now(timezone.utc).isoformat(),
        "sentBy": token_claims.get('sub')
    }
