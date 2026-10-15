import json
import re
import boto3
import hmac
import hashlib
import unicodedata
import time
from os import environ
from datetime import datetime
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from decimal import Decimal
from base64 import b64encode
from bleach import clean

def isDate(value: str):
    """Verifica se uma entrada de dados está no formato de data ISO (yyyy-mm-dd)
    
    Requisitos:
        NA
    Parâmetros:
        value: [str] Entrada de valor que será validada
    """
    try:
        if not isinstance(value, str):
            raise Exception(isinstance(value).__name__)
        d = datetime.strptime(value, "%Y-%m-%d")
        return { "success": True, "message": "Ok", "data": { "in": value, "type": "Valid ISO Date", "accept_format": "yyyy-mm-dd" }}
    except ValueError as err:
        return { "success": False, "message": str(err), "data": { "in": value, "type": "Invalid ISO Date", "accept_format": "yyyy-mm-dd" }}
    except Exception as err:
        return { "success": False, "message": "Invalid value type", "data": { "in": value, "type": str(err), "accept_format": "yyyy-mm-dd" }}

def isDateTime(value: str):
    """Verifica se uma entrada de dados está no formato de datatime ISO (yyyy-mm-dd hh:mm:ss)
    
    Requisitos:
        NA
    Parâmetros:
        value: [str] Entrada de valor que será validada
    """
    try:
        if not isinstance(value, str):
            raise Exception(isinstance(value).__name__)
        d = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return { "success": True, "message": "Ok", "data": { "in": value, "type": "Valid ISO Datetime", "accept_format": "yyyy-mm-dd hh:mm:ss" }}
    except ValueError as err:
        return { "success": False, "message": str(err), "data": { "in": value, "type": "Invalid ISO Datetime", "accept_format": "yyyy-mm-dd hh:mm:ss" }}
    except Exception as err:
        return { "success": False, "message": "Invalid value type", "data": { "in": value, "type": str(err), "accept_format": "yyyy-mm-dd hh:mm:ss" }}

def validateInput(fields: list, field: dict):
    """Valida a entrada de dados de acordo com a lista e tipo de entrada obrigatório
    
    Requisitos:
        NA
    
    Parâmetros:
        fields: [list] Lista com os nomes dos campos obrigatórios no formato {"field": "xpto", "type": int|str|list|float|date|number|dict|datetime|bool, "mandatory": true|false, "pattern": "REGEX"}
        field: [dict] Dicionário com os campos infomados
    """
    try:
        if not field:
            raise Exception(f"Campos obrigatórios não informados.")
        for f in fields:
            # verifica se o campo foi informado
            if (f['mandatory'] and (f['field'] not in field.keys() or (not field[f['field']] and field[f['field']] != 0 and field[f['field']] != False))):
                # raise Exception(f"Campo obrigatório '{f['field']}' não informado.")
                raise Exception(f"Campo '{f['field']}' incorreto ou inválido.")
            try:
                # verifica se o campo foi informado com o tipo correto
                if f['field'] in field and f['type'] == "int" and (isinstance(field[f['field']], bool) or not isinstance(field[f['field']], int)):
                    raise Exception(f"{f['field']}|int|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "str" and not isinstance(field[f['field']], str):
                    raise Exception(f"{f['field']}|str|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "float" and not isinstance(field[f['field']], float):
                    raise Exception(f"{f['field']}|float|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "list" and not isinstance(field[f['field']], list):
                    raise Exception(f"{f['field']}|list|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "dict" and not isinstance(field[f['field']], dict):
                    raise Exception(f"{f['field']}|dict|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "bool" and not isinstance(field[f['field']], bool):
                    raise Exception(f"{f['field']}|bool|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "number" and (isinstance(field[f['field']], bool) or not isinstance(field[f['field']], (int, float))):
                    raise Exception(f"{f['field']}|number|{type(field[f['field']]).__name__}")
                if f['field'] in field and f['type'] == "datetime":
                    d = isDateTime(field[f['field']])
                    if not d['success']:
                        raise Exception(f"{f['field']}|{d['data']['accept_format']}|{d['data']['type']}")
                if f['field'] in field and f['type'] == "date":
                    d = isDate(field[f['field']])
                    if not d['success']:
                        raise Exception(f"{f['field']}|{d['data']['accept_format']}|{d['data']['type']}")
            except Exception as err:
                err = str(err).split('|')
                # raise Exception(f"Campo '{err[0]}' informado com o tipo incorreto. Tipo esperado '{err[1]}', tipo enviado '{err[2]}'.")
                raise Exception(f"Campo '{f['field']}' incorreto ou inválido.")
            if f['field'] in field and f['type'] == "str" and len(field[f['field']]) > 0 and "pattern" in f and f['pattern']:
                if "patternMultiline" in f and f['patternMultiline']:
                    pattern = re.compile(f['pattern'], re.MULTILINE)
                else:
                    pattern = re.compile(f['pattern'])
                if not pattern.match(field[f['field']]):
                    # raise Exception(f"Campo '{f['field']}' não obedece o padrão definido para entrada '{f['pattern']}'")
                    raise Exception(f"Campo '{f['field']}' incorreto ou inválido.")
        return { "success": True, "message": "Ok", "data": {} }
    except Exception as err:
        # algum erro foi reportado, retorna erro
        return { "success": False, "message": str(err), "data": {} }
    
class DecimalEncoder(json.JSONEncoder):
    """JSON encoder que serializa Decimal (retornado pelo DynamoDB) como int/float."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            # Mantém inteiros como int; caso contrário, float
            return int(obj) if obj == obj.to_integral_value() else float(obj)
        return super().default(obj)

def lambdaReturn(retorno: dict):
    """Retorna o formato aceito pela API Gateway de retorno do lambda proxy
    
    Requisitos:
        NA
    
    Parâmetros:
        retorno: [dict] Retorno da API
    """
    if "statusCode" in retorno:
        statusCode = retorno['statusCode']
        del retorno["statusCode"]
    else:
        statusCode = 200
    if statusCode >= 500:
        retorno['message'] = 'Erro interno. Tente novamente mais tarde.'
    # envia o retorno para o lambda proxy
    return {
        "statusCode": statusCode,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": environ.get('CORS_ALLOW_ORIGIN', '*'),
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        },
        "body": json.dumps(retorno, cls=DecimalEncoder)
    }

def roundFloat(o):
    """ Define o formato padrao para um decimal: preserva int quando o valor é
    inteiro, caso contrário converte para float."""
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError

def getDynamoData(table: str, primaryKey: str, key: str, sortKey: dict = None, region: str = 'sa-east-1'):
    """Busca dados em uma tabela do DynamoDB

    Permissao:
        dynamodb:GetItem

    Parametros:
        table: ``str`` [Obrigatório] Tabela a ser consultada
        primaryKey: ``str`` [Obrigatório] Chave da tabela que será consultada
        key: ``str`` [Obrigatório] Valor da chave que será consultado
        sortKey: ``dict`` [Opcional] Sort key da consulta no formato { "key": "xpto", "value": "xyz" }
        region: ``str`` [Opcional] Região da AWS onde a tabela foi criada. Default sa-east-1
    """
    try:
        dynamo = boto3.client('dynamodb', region_name=region)
        s = TypeSerializer()
        key = { primaryKey: key }
        if sortKey:
            key[sortKey['key']] = sortKey['value']
        r = dynamo.get_item(
            TableName=table,
            Key={ k: s.serialize(v) for k, v in key.items() if v != "" }
        )
        d = TypeDeserializer()
        if "Item" not in r:
            return { "success": True, "message": "Nenhum dado encontrado", "data":  {} }
        return { "success": True, "message": "Ok", "data": json.loads(json.dumps({k: d.deserialize(value=v) for k, v in r['Item'].items()},default=roundFloat)) }
    except Exception as err:
        print("ERROR: Falha ao obter dados do DynamoDB")
        print("ERROR: Table: ", table)
        print("ERROR: PrimaryKey: ", primaryKey)
        print("ERROR: Key: ", key)
        print("ERROR: Region: ", region)
        print("ERROR:", err)
        return { "success": False, "message": str(err), "data": {} }

def genSecretHash(clientId: str, clientSecret: str, username: str):
    """Gera um secret hash para usuários do cognito

    Permissao:
        N/A

    Retorno:
        [str] Secret hash
    """
    # gera o secret hash
    print("INFO: Gerando secret hash...")
    key = bytes(clientSecret, 'utf-8')
    message = bytes(username+clientId, 'utf-8')
    return b64encode(hmac.new(key, message, digestmod=hashlib.sha256).digest()).decode()

def getSaaSConfig(region: str, stage: str = 'sandbox'):
    """Busca as credenciais do App Client SaaS no DynamoDB (Tabela de Tenants)

    Permissão:
        dynamodb:GetItem
    """
    print("INFO: Buscando configurações do Cognito SaaS...")
    table_name = f"plataforma-ati-{stage}-tenants"
    try:
        client = boto3.client('dynamodb', region_name=region)
        response = client.get_item(
            TableName=table_name,
            Key={
                'pk': {'S': 'CONFIG'},
                'sk': {'S': 'COGNITO#SAAS'}
            }
        )
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return {
            'userPoolId': item['userPoolId']['S'],
            'clientId': item['clientId']['S'],
            'clientSecret': item.get('clientSecret', {}).get('S')
        }
    except Exception as e:
        print(f"ERROR: Falha ao buscar config em {table_name}: {e}")
        return None

_SENSOR_CATALOG_CACHE = None
_SENSOR_CATALOG_CACHE_AT = 0
SENSOR_CATALOG_CACHE_TTL_SECONDS = 300


def getAllDynamoItems(table, operation='query', **kwargs):
    """Percorre todas as páginas DynamoDB preservando o contrato de lista atual."""
    items = []
    request = dict(kwargs)
    while True:
        response = getattr(table, operation)(**request)
        items.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            return items
        request['ExclusiveStartKey'] = last_key

def _queryGlobalCatalog(region: str, table_name: str, sk_prefix: str):
    """Consulta uma faixa do catálogo global (pk SYSTEM#CATALOG) filtrando pelo
    prefixo do sk."""
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    items = getAllDynamoItems(table,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq('SYSTEM#CATALOG') &
                               boto3.dynamodb.conditions.Key('sk').begins_with(sk_prefix)
    )
    return {item['typeId']: item for item in items}

def getSensorCatalog(region: str, table_name: str):
    """Busca o catálogo global de sensores com cache em memória"""
    global _SENSOR_CATALOG_CACHE, _SENSOR_CATALOG_CACHE_AT
    if _SENSOR_CATALOG_CACHE is not None and time.monotonic() - _SENSOR_CATALOG_CACHE_AT < SENSOR_CATALOG_CACHE_TTL_SECONDS:
        return _SENSOR_CATALOG_CACHE

    print("INFO: Buscando catálogo de sensores no DynamoDB...")
    try:
        _SENSOR_CATALOG_CACHE = _queryGlobalCatalog(region, table_name, 'SENSORTYPE#')
        _SENSOR_CATALOG_CACHE_AT = time.monotonic()
        return _SENSOR_CATALOG_CACHE
    except Exception as e:
        print(f"ERROR: Falha ao buscar catálogo de sensores: {e}")
        return {}

VALID_STATE_DATA_TYPES = frozenset({'int', 'string', 'bool'})
VALID_COMMAND_DATA_TYPES = frozenset({'int', 'float', 'string', 'bool'})
TENANT_ROLE_ALIASES = {
    'manager': 'gestor',
    'technician': 'tecnico',
    'client': 'user'
}
TENANT_ROLES = frozenset({'gestor', 'tecnico', 'user', *TENANT_ROLE_ALIASES})


def canonicalTenantRole(role: str):
    """Normaliza aliases legados sem alterar o contrato aceito nas APIs."""
    return TENANT_ROLE_ALIASES.get(role, role)

# ─── Grant de visualização por role de tenant ─────────────────────────────────
#
# 'viewRoles' (sensors/states) e 'allowedRoles' (commands) listam roleIds de tenant.
# sensor/state sem 'viewRoles' -> visível a qualquer role.

def validateGrantRoles(kind: str, var_name: str, field: str, value):
    """Valida uma lista de grant ('viewRoles'/'executeRoles') e devolve normalizada.

    Não confere se a role existe: o grant é gravado no equipamento/modelo e as roles
    de tenant são criadas por tenant, então uma role listada aqui pode legitimamente
    existir só em parte dos tenants que usam o mesmo modelo.
    """
    if not isinstance(value, list):
        raise Exception(f"400::'{field}' d{'a' if kind == 'ação' else 'o'} {kind} '{var_name}' deve ser uma lista de roles")
    for role in value:
        if not isinstance(role, str) or not role.strip():
            raise Exception(f"400::'{field}' d{'a' if kind == 'ação' else 'o'} {kind} '{var_name}' contém uma role inválida")
    # dedup preservando ordem, pra não gravar ["tecnico","tecnico"]
    return list(dict.fromkeys(role.strip() for role in value))

def validateAllowedRoles(var_name: str, value):
    if not isinstance(value, list) or any(role not in TENANT_ROLES for role in value):
        raise Exception(f"400::allowedRoles inválido para '{var_name}'. Use apenas: gestor, tecnico, user, manager, technician, client")
    return list(dict.fromkeys(canonicalTenantRole(role) for role in value))

def validateSensors(sensors: dict, catalog: dict):
    """Valida e normaliza o dict de 'sensors' contra o catálogo global de sensores.

    Preenche 'variable' e herda 'name'/'unit' do catálogo quando não informados, e
    valida o grant opcional 'viewRoles'. Levanta Exception('400::...') em entrada
    inválida. Fonte única usada por equipment/new, equipment/update e pelos
    handlers de equipment-model — o formato do sensor tem que ser idêntico nos dois
    níveis, senão um modelo gera equipamento que não valida.
    """
    if not sensors:
        return
    for var_name, sensor_info in sensors.items():
        if not isinstance(sensor_info, dict):
            raise Exception(f"400::Sensor '{var_name}' deve ser um objeto")
        if 'sensorType' not in sensor_info:
            raise Exception(f"400::Sensor '{var_name}' deve conter 'sensorType'")
        if sensor_info['sensorType'] not in catalog:
            raise Exception(f"400::Tipo de sensor '{sensor_info['sensorType']}' não encontrado no catálogo global para a variável '{var_name}'.")
        sensor_info['variable'] = var_name
        # Copiar metadados do catálogo se não fornecidos
        if 'name' not in sensor_info:
            sensor_info['name'] = catalog[sensor_info['sensorType']].get('name', 'Sensor Desconhecido')
        if 'unit' not in sensor_info and 'defaultUnit' in catalog[sensor_info['sensorType']]:
            sensor_info['unit'] = catalog[sensor_info['sensorType']]['defaultUnit']
        if 'viewRoles' in sensor_info:
            sensor_info['viewRoles'] = validateGrantRoles('sensor', var_name, 'viewRoles', sensor_info['viewRoles'])
        if 'allowedRoles' in sensor_info:
            sensor_info['allowedRoles'] = validateAllowedRoles(var_name, sensor_info['allowedRoles'])

def validateStates(states: dict):
    """Valida e normaliza um dict de 'states' (interpretação de sensores, ex: 'andar',
    'subindo' — não é leitura bruta, é estado derivado). Variável livre igual sensor,
    mas sem catálogo global: só exige um 'dataType' primitivo (int/string/bool).
    Levanta Exception('400::...') em entrada inválida; preenche 'variable' e, se
    ausente, 'name' (default = a própria chave, já que não há catálogo pra puxar)."""
    if not states:
        return
    for var_name, state_info in states.items():
        if not isinstance(state_info, dict):
            raise Exception(f"400::Estado '{var_name}' deve ser um objeto")
        if 'dataType' not in state_info:
            raise Exception(f"400::Estado '{var_name}' deve conter 'dataType'")
        if state_info['dataType'] not in VALID_STATE_DATA_TYPES:
            raise Exception(f"400::dataType '{state_info['dataType']}' inválido para o estado '{var_name}'. Use int, string ou bool.")
        state_info['variable'] = var_name
        if 'name' not in state_info:
            state_info['name'] = var_name
        if 'viewRoles' in state_info:
            state_info['viewRoles'] = validateGrantRoles('estado', var_name, 'viewRoles', state_info['viewRoles'])
        if 'allowedRoles' in state_info:
            state_info['allowedRoles'] = validateAllowedRoles(var_name, state_info['allowedRoles'])

def validateCommands(commands: dict):
    """Valida comandos declarados diretamente no modelo/equipamento."""
    if not commands:
        return
    allowed = {'name', 'dataType', 'resource', 'required', 'min', 'max', 'allowedValues', 'allowedRoles', 'color', 'viewRoles', 'executeRoles'}
    for command_name, info in commands.items():
        if not isinstance(info, dict):
            raise Exception(f"400::Comando '{command_name}' deve ser um objeto")
        unknown = set(info) - allowed
        if unknown:
            raise Exception(f"400::Propriedade desconhecida no comando '{command_name}'")
        for field in ('name', 'resource'):
            if not isinstance(info.get(field), str) or not info[field].strip():
                raise Exception(f"400::Comando '{command_name}' deve conter '{field}'")
        if info.get('dataType') not in VALID_COMMAND_DATA_TYPES:
            raise Exception(f"400::dataType '{info.get('dataType')}' inválido para o comando '{command_name}'. Use int, float, string ou bool.")
        if 'required' in info and not isinstance(info['required'], bool):
            raise Exception(f"400::'required' do comando '{command_name}' deve ser booleano")
        if ('min' in info or 'max' in info) and info['dataType'] not in {'int', 'float'}:
            raise Exception(f"400::'min' e 'max' só podem ser usados em comandos numéricos ('{command_name}')")
        for bound in ('min', 'max'):
            if bound in info and (isinstance(info[bound], bool) or not isinstance(info[bound], (int, float))):
                raise Exception(f"400::'{bound}' do comando '{command_name}' deve ser numérico")
        if 'min' in info and 'max' in info and info['min'] > info['max']:
            raise Exception(f"400::'min' não pode ser maior que 'max' no comando '{command_name}'")
        if 'allowedValues' in info and (not isinstance(info['allowedValues'], list) or not info['allowedValues']):
            raise Exception(f"400::'allowedValues' do comando '{command_name}' deve ser uma lista não vazia")
        if 'allowedValues' in info:
            for value in info['allowedValues']:
                valid = ((info['dataType'] == 'bool' and isinstance(value, bool)) or
                         (info['dataType'] == 'int' and isinstance(value, int) and not isinstance(value, bool)) or
                         (info['dataType'] == 'float' and isinstance(value, (int, float)) and not isinstance(value, bool)) or
                         (info['dataType'] == 'string' and isinstance(value, str)))
                if not valid:
                    raise Exception(f"400::'allowedValues' do comando '{command_name}' contém valor incompatível com dataType")
        if 'allowedRoles' in info:
            info['allowedRoles'] = validateAllowedRoles(command_name, info['allowedRoles'])
        if 'color' in info:
            if not isinstance(info['color'], str) or not info['color'].strip():
                raise Exception(f"400::'color' do comando '{command_name}' deve ser uma string não vazia")
        if 'viewRoles' in info:
            info['viewRoles'] = validateGrantRoles('comando', command_name, 'viewRoles', info['viewRoles'])
        if 'executeRoles' in info:
            info['executeRoles'] = validateAllowedRoles(command_name, info['executeRoles'])
        info['variable'] = command_name

def coerceCommandValue(command_info: dict, value):
    """Converte e valida o valor de um comando conforme o 'dataType'.

    Rejeita em vez de adivinhar: um comando com valor mal interpretado atua no mundo
    físico. bool não aceita 0/1 nem "true", e int não aceita bool.
    """
    data_type = command_info.get('dataType')
    variable = command_info.get('variable', '?')

    if data_type == 'bool':
        if not isinstance(value, bool):
            raise Exception(f"400::Valor do comando '{variable}' deve ser booleano (true ou false)")
        coerced = value
    elif data_type == 'int':
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise Exception(f"400::Valor do comando '{variable}' deve ser numérico")
        if value != int(value):
            raise Exception(f"400::Valor do comando '{variable}' deve ser um inteiro")
        coerced = int(value)
    elif data_type == 'float':
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise Exception(f"400::Valor do comando '{variable}' deve ser numérico")
        coerced = float(value)
    elif data_type == 'string':
        if not isinstance(value, str):
            raise Exception(f"400::Valor do comando '{variable}' deve ser uma string")
        coerced = value
    else:
        raise Exception(f"500::dataType '{data_type}' não suportado para o comando '{variable}'")

    if data_type != 'bool' and 'min' in command_info and coerced < command_info['min']:
        raise Exception(f"400::Valor do comando '{variable}' deve ser maior ou igual a {command_info['min']}")
    if data_type != 'bool' and 'max' in command_info and coerced > command_info['max']:
        raise Exception(f"400::Valor do comando '{variable}' deve ser menor ou igual a {command_info['max']}")
    if 'allowedValues' in command_info and coerced not in command_info['allowedValues']:
        raise Exception(f"400::Valor do comando '{variable}' não está entre os valores permitidos")
    return coerced

def sanitizeInput(payload: dict, fields: list = None):
    """Faz a sanitização de uma entrada de dados recebidas através de um REST API
    
    Requisitos: 
        NA
    
    Parâmetros:
        payload: [dict] Dados recebidos no payload do REST API
        fields: [list] Opcional - lista de campos a serem devolvidos

    Retorno: 
        payload com os dados sanitizados e com apenas os campos 
    """
    try:
        # instancia o retorno
        retorno = None
        # verifica se a entrada é um dicionário
        if isinstance(payload, dict):
            retorno = {}
            # se for, valida de forma recursiva
            for k, v in payload.items():
                retorno[k] = sanitizeInput(v)
        # verifica se a entrada é uma lista
        elif isinstance(payload, list):
            # se for, percorre a lista adicionando ao retorno os dados sanitizados de forma recursiva
            retorno = []
            for item in payload:
                retorno.append(sanitizeInput(item))
        # verifica se a entrada é uma string
        elif isinstance(payload, str):
            # se for, faz o sanitize
            retorno = clean(payload)
        # demais entradas não são passiveis de conter dados maliciosos
        else:
            retorno = payload
        # devolve os dados sanitizados
        if not fields:
            return retorno
        else:
            ret = {}
            for f in fields:
                if f['field'] in retorno:
                    if not f['sanitize']:
                        ret[f['field']] = retorno[f['field']]
                    else:
                        ret[f['field']] = re.sub(re.compile(f['sanitize']), '', retorno[f['field']])
            return ret
    except Exception as err:
        print("ERROR: Houve uma falha ao sanitizar os dados de entrada >>", err)
        return None

def insertDynamoDB(table: str, item: dict, replace: bool = False, region: str = 'sa-east-1'):
    """Adiciona um registro a uma tabela do DynamoDB

    Requisitos:
        IAM Policy: DynamoDB:PutItem

    Parâmetros:
        table: [str] Tabela onde será inserido o registro
        item: [dict] Dicionário com o item que será adicionado à tabela
        replace: [bool] Opcional - Indica se os dados podem ser sobrescrevidos. (default: false)
        region: [str] Opcional - Região da AWS onde o DynamoDB está alocado (default: sa-east-1)
    """
    # cria o client do boto3
    client = boto3.client('dynamodb', region_name=region)
    # tenta inserir os dados
    try:
        print(f"INFO: Adicionando dados a tabela {table}...")
        # estancia o serializer do dynamoDB
        s = TypeSerializer()
        # insere os dados
        params = {
            "TableName": table,
            "Item": { k: s.serialize(v) for k, v in item.items() if v != "" }
        }
        if not replace:
            params["ConditionExpression"] = "attribute_not_exists (#pk) AND attribute_not_exists (#sk)"
            params["ExpressionAttributeNames"] = { "#pk": "pk", "#sk": "sk" }
        response = client.put_item(**params)
        # tudo ok
        print(f"INFO: Dados inseridos na tabela {table}!")
        return { "success": True, "message": "Ok", "data": response }
    except Exception as err:
        # erro ao inserir, registra o log e retorna falha
        print(f"ERROR: Falha ao gravar na tabela {table} na região {region}!")
        print("ERROR: Chaves do item de insert >> ", sorted(item.keys()))
        print("ERROR: Erro retornado >> ", err)
        return { "success": False, "message": f"Falha ao gravar dados.", "data": { "errors": [str(err)] } }
    
def stripAccents(text: str):
    """Remove os acentos de uma string.

    Parametros:
        text ``str``: [Obrigatório] String de entrada
    
    Retorno:
        ``str``: String processada
    """
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore')
    text = text.decode("utf-8")
    return str(text)

# ─── RBAC: Verificação de Permissões ──────────────────────────────────────────

def checkPermission(role_data: dict, module: str, action: str):
    """Verifica se um role tem permissão para executar uma ação em um módulo.

    Suporta dois formatos:
    1. Legacy (dict): {"module": {"action": bool}}
    2. SaaS (list): ["module:action", "module:*", "*:*"]

    Parametros:
        role_data: ``dict`` [Obrigatório] Campo 'data' retornado por getSaaSRole
        module: ``str`` [Obrigatório] Módulo a verificar
        action: ``str`` [Obrigatório] Ação a verificar

    Retorno:
        [bool] True se o role possui a permissão, False caso contrário
    """
    permissions = role_data.get('permissions')
    
    # Se permissions for None ou vazio, nega
    if not permissions:
        return False

    # Formato SaaS (lista de strings)
    if isinstance(permissions, list):
        # Superadmin: *:*
        if '*:*' in permissions:
            return True
        # Module wildcard: module:*
        if f'{module}:*' in permissions:
            return True
        # Specific permission: module:action
        if f'{module}:{action}' in permissions:
            return True
        return False

    # Formato Legacy (dict)
    if isinstance(permissions, dict):
        module_perms = permissions.get(module, {})
        return bool(module_perms.get(action, False))
        
    return False

def validateTokenSaaS(authorization: str, region: str = 'sa-east-1'):
    """Valida o token SaaS e devolve os atributos normalizados do usuário.

    Requisitos:
        IAM Policy: cognito-idp:GetUser
    """
    token = authorization.split(' ')
    if len(token) != 2 or token[0] != 'Bearer':
        return { 'success': False, 'message': 'Não foi possível validar o token de acesso.', 'data': { 'errors': ['Invalid token type'] } }

    client = boto3.client('cognito-idp', region_name=region)
    try:
        response = client.get_user(AccessToken=token[1])
        userAtt = {}
        for attribute in response['UserAttributes']:
            userAtt[attribute['Name'].replace('custom:', '')] = attribute['Value']
        userAtt['username'] = response['Username']
        return { 'success': True, 'message': 'OK', 'data': userAtt }
    except client.exceptions.NotAuthorizedException as err:
        print(f"DEBUG: validateTokenSaaS - NotAuthorizedException: {err}")
        return { 'success': False, 'message': 'Usuário não autorizado.', 'data': { 'errors': [str(err)] } }
    except client.exceptions.InvalidParameterException as err:
        print(f"DEBUG: validateTokenSaaS - InvalidParameterException: {err}")
        return { 'success': False, 'message': 'Parâmetro inválido.', 'data': { 'errors': [str(err)] } }
    except Exception as err:
        print(f"DEBUG: validateTokenSaaS - Exception: {err}")
        return { 'success': False, 'message': 'Token inválido.', 'data': { 'errors': [str(err)] } }


def getSaaSRole(table: str, organization_id: str, role: str, region: str = 'sa-east-1'):
    """Obtém a definição de role dinâmica da organização no DynamoDB SaaS."""
    if not organization_id:
        return { 'success': False, 'message': 'organizationId não informado', 'data': {} }
    return getDynamoData(table, 'pk', f'ORG#{organization_id}#ROLES', {'key': 'sk', 'value': f'ROLE#{role}'}, region)


def getSaaSTenantRole(table: str, tenant_id: str, role: str, region: str = 'sa-east-1'):
    """Obtém a definição de role dinâmica do tenant no DynamoDB SaaS."""
    if not hasTenantScope(tenant_id):
        return { 'success': False, 'message': 'tenantId não informado', 'data': {} }
    return getDynamoData(table, 'pk', f'TENANT#{tenant_id}#ROLES', {'key': 'sk', 'value': f'ROLE#{role}'}, region)


def resolveSaaSRoleDefinition(table: str, organization_id: str, tenant_id: str, role: str,
                              region: str = 'sa-east-1'):
    """Resolve a definição de role no escopo correto, de forma estrita.

    Usuário com tenant (`tenantId != 'none'`) só usa role do tenant
    (`TENANT#{tenantId}#ROLES`); usuário de organização (`tenantId == 'none'`) só usa
    role da organização. Sem fallback entre os dois escopos: fallback faria um roleId
    de tenant inexistente resolver silenciosamente para a role homônima da
    organização, que é mais permissiva — exatamente a escalada que o escopo por tenant
    existe para impedir.

    Exceção: roles globais (superadmin/collaborator) sempre resolvem na organização.
    O superadmin é criado com `custom:tenantId = 'PLATFORM'`, que não é um tenant real
    — sem esta exceção ele passaria a ser procurado em `TENANT#PLATFORM#ROLES` e
    perderia acesso a tudo. Não é uma brecha de escalada: a role global só existe em
    `ORG#PLATFORM#ROLES`, então um usuário de outra organização que reivindique
    'superadmin' não resolve role nenhuma.
    """
    if hasTenantScope(tenant_id) and not isGlobalOrganizationRole(role):
        return getSaaSTenantRole(table, tenant_id, role, region)
    return getSaaSRole(table, organization_id, role, region)


GLOBAL_ORGANIZATION_ROLES = frozenset({'superadmin', 'collaborator'})
PLATFORM_ORGANIZATION_ID = 'PLATFORM'
# Roles padrão semeadas em cada tenant novo (`TENANT#{tenantId}#ROLES`). São o
# escopo em que 'viewRoles'/'executeRoles' são avaliados: a organização configura
# no equipamento quais destas roles veem o sensor ou enviam o comando.
#
# A lista é extensível — a organização pode criar outras roles de tenant no mesmo
# padrão de item; estas três são o conjunto mínimo criado automaticamente.
# Nenhuma delas recebe permissão de 'tenant': quem administra o tenant é a
# organização, não o próprio tenant.
TENANT_DEFAULT_ROLES = {
    "gestor": {
        "roleName": "Gestor",
        "description": "Gerencia técnicos e usuários do tenant, sem operar equipamentos",
        "permissions": [
            "user:read",
            "user:write",
            "equipment:read",
            "dashboard:read",
            "report:read",
            "catalog:read"
        ],
        "isSystemRole": True
    },
    "tecnico": {
        "roleName": "Técnico",
        "description": "Opera o equipamento: envia comandos liberados para a role",
        "permissions": [
            "equipment:read",
            "equipment:update",
            "action:read",
            "action:execute",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read",
            "virtual-technician:write",
            "virtual-technician:delete"
        ],
        "isSystemRole": True
    },
    "user": {
        "roleName": "Usuário",
        "description": "Apenas observa: vê sensores liberados, sem enviar comandos",
        "permissions": [
            "equipment:read",
            "action:read",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read"
        ],
        "isSystemRole": True
    }
}


def buildTenantRoleItem(tenant_id: str, organization_id: str, role_id: str, role_data: dict,
                        timestamp: str):
    """Monta o item DynamoDB de uma role de tenant.

    'organizationId' fica no item para permitir auditar a quem o tenant pertence sem
    uma segunda consulta — a chave em si é só do tenant, porque é por tenant que a
    role é resolvida em runtime.
    """
    return {
        "pk": f"TENANT#{tenant_id}#ROLES",
        "sk": f"ROLE#{role_id}",
        "tenantId": tenant_id,
        "organizationId": organization_id,
        "roleId": role_id,
        "roleName": role_data['roleName'],
        "description": role_data['description'],
        "permissions": role_data['permissions'],
        "isSystemRole": role_data.get('isSystemRole', False),
        "createdAt": timestamp,
        "updatedAt": timestamp
    }


def isGlobalOrganizationRole(role: str):
    """Indica se a role possui escopo operacional sobre todas as organizações."""
    return role in GLOBAL_ORGANIZATION_ROLES


def hasTenantScope(tenant_id: str):
    """Indica se o identificador representa um tenant real ('none' é a ausência)."""
    return bool(tenant_id) and tenant_id != 'none'


def isTenantScopedCaller(auth: dict):
    """Indica se o chamador é usuário de um tenant — ou seja, se os grants
    'viewRoles'/'executeRoles' devem ser aplicados a ele.

    Roles globais e roles de organização (`tenantId == 'none'`) administram a
    configuração dos grants; aplicá-los a elas esconderia do administrador
    justamente o que ele precisa configurar.
    """
    return not isGlobalOrganizationRole(auth.get('role')) and hasTenantScope(auth.get('tenantId'))


def hasViewGrant(variable_info: dict, role: str):
    """Avalia o grant de leitura de um sensor/estado. Ausência de 'viewRoles' libera."""
    view_roles = variable_info.get('viewRoles')
    if view_roles is None:
        view_roles = variable_info.get('allowedRoles')
    if view_roles is None:
        return True
    canonical_role = canonicalTenantRole(role)
    return canonical_role in {canonicalTenantRole(item) for item in view_roles}


def hasExecuteGrant(command_info: dict, role: str):
    """Avalia executeRoles, mantendo allowedRoles como fallback legado."""
    execute_roles = command_info.get('executeRoles')
    if execute_roles is None:
        execute_roles = command_info.get('allowedRoles')
    if execute_roles is None:
        return True
    canonical_role = canonicalTenantRole(role)
    return canonical_role in {canonicalTenantRole(item) for item in execute_roles}


def _filterVariables(variables, auth: dict, predicate):
    """Aplica um predicado de grant a um dict/lista de variáveis, preservando o
    formato de entrada (equipment grava dict; dashboard/report devolvem lista)."""
    if not variables or not isTenantScopedCaller(auth):
        return variables
    role = auth.get('tenantRole')
    if not role:
        return {} if isinstance(variables, dict) else []
    if isinstance(variables, dict):
        return {k: v for k, v in variables.items() if predicate(v, role)}
    return [v for v in variables if predicate(v, role)]


def filterVariablesForRole(variables, auth: dict):
    """Remove de 'sensors'/'states'/'commands' o que a role do tenant não pode ver."""
    return _filterVariables(variables, auth, hasViewGrant)


def filterValuesForRole(values: dict, sensors, states, auth: dict):
    """Remove de um dict de leituras (ex: 'lastValues') as variáveis que a role não
    pode ver.

    Esconder a declaração do sensor sem esconder o valor não protege nada — o valor
    é exatamente o dado que 'viewRoles' restringe. Variáveis que não estão
    declaradas em 'sensors'/'states' passam, seguindo o mesmo default de sensor sem
    grant (aberto): não há como restringir o que não foi configurado.
    """
    if not values or not isTenantScopedCaller(auth):
        return values
    role = auth.get('tenantRole')
    if not role:
        return {}
    declared = {}
    for group in (sensors or {}, states or {}):
        if isinstance(group, dict):
            declared.update(group)
    return {
        k: v for k, v in values.items()
        if k not in declared or hasViewGrant(declared[k], role)
    }


def authorizeCommandExecution(equipment: dict, command_name: str, auth: dict):
    command_info = (equipment.get('commands') or {}).get(command_name)
    if not command_info:
        raise Exception(f"404::Comando '{command_name}' não cadastrado neste equipamento")
    if isTenantScopedCaller(auth):
        if not auth.get('tenantRole'):
            raise Exception("403::Role de tenant ausente")
        if not hasExecuteGrant(command_info, auth['tenantRole']):
            raise Exception(f"403::Acesso negado para o comando '{command_name}'")
    return command_info


def authorizeScheduledActions(equipment: dict, actions, auth: dict):
    """Valida a lista de comandos que o técnico virtual vai disparar no equipamento.

    Cada item precisa ser um comando cadastrado no equipamento e liberado para a
    role do chamador — sem isso a automação seria um desvio do grant.
    """
    if not actions:
        return
    if not isinstance(actions, list):
        raise Exception("400::'actions' deve ser uma lista")
    for item in actions:
        if not isinstance(item, dict):
            raise Exception("400::Cada item de 'actions' deve ser um objeto")
        variable = item.get('variable')
        if not variable:
            raise Exception("400::Cada item de 'actions' deve conter 'variable'")
        if (equipment.get('commands') or {}).get(variable):
            command_info = authorizeCommandExecution(equipment, variable, auth)
            if 'value' not in item:
                raise Exception(f"400::Comando '{variable}' deve conter 'value'")
            item['value'] = coerceCommandValue(command_info, item['value'])
        else:
            raise Exception(f"404::Comando '{variable}' não cadastrado neste equipamento")


_ROLE_HIERARCHY = {'superadmin': 5, 'collaborator': 4, 'admin': 3, 'manager': 2, 'viewer': 1}

def getSaaSRoleLevel(role: str) -> int:
    """Retorna o nível numérico de uma role na hierarquia SaaS (0 = desconhecida)."""
    return _ROLE_HIERARCHY.get(role, 0)

def validateSaaSRoleAssignment(caller_role: str, target_organization_id: str, target_role: str):
    """Impede escalada de privilégio na criação ou alteração de usuários SaaS.

    Contas e roles da organização PLATFORM são administradas exclusivamente pelo
    superadmin. O collaborator administra todas as organizações de negócio, mas
    não pode criar ou promover usuários para roles globais.

    Hierarquia: superadmin > collaborator > admin > manager > viewer.
    Um caller só pode atribuir roles de nível igual ou inferior ao seu.
    Roles customizadas (não presentes na hierarquia) são tratadas como nível 0
    e só podem ser atribuídas por admin ou acima.
    """
    if caller_role == 'superadmin':
        return True

    if target_organization_id == PLATFORM_ORGANIZATION_ID:
        raise Exception('403::Apenas superadmin pode administrar usuários da plataforma')

    if target_role in GLOBAL_ORGANIZATION_ROLES:
        raise Exception(f"403::Apenas superadmin pode atribuir a role '{target_role}'")

    caller_level = _ROLE_HIERARCHY.get(caller_role, 0)
    target_level = _ROLE_HIERARCHY.get(target_role, 0)
    if caller_level < target_level:
        raise Exception(f"403::Você não tem permissão para atribuir a role '{target_role}'")

    return True


def validateSaaSTargetRoleExists(roles_table: str, organization_id: str, role: str,
                                 region: str = 'sa-east-1', tenant_id: str = None):
    """Garante que a role atribuída existe no escopo alvo.

    Com `tenant_id` de um tenant real, a role é procurada em `TENANT#{tenantId}#ROLES`
    — mesmo escopo que `authorizeSaaS` vai usar no login desse usuário. Sem isso, um
    usuário de tenant poderia ser criado com uma role da organização e tomar 403 em
    tudo depois.
    """
    resolved_role = canonicalTenantRole(role) if hasTenantScope(tenant_id) else role
    role_def = resolveSaaSRoleDefinition(
        roles_table, organization_id, tenant_id, resolved_role, region
    )
    # Compatibilidade com tenants antigos que persistiram os ids em inglês.
    # O fallback permanece estritamente dentro do mesmo tenant.
    if (not role_def.get('success') or not role_def.get('data')) and resolved_role != role:
        role_def = resolveSaaSRoleDefinition(
            roles_table, organization_id, tenant_id, role, region
        )
    if not role_def.get('success') or not role_def.get('data'):
        if hasTenantScope(tenant_id) and not isGlobalOrganizationRole(role):
            raise Exception(f"400::Role '{role}' não existe no tenant '{tenant_id}'")
        raise Exception(f"400::Role '{role}' não existe na organização '{organization_id}'")
    return True


def resolveSaaSTargetScope(auth: dict, organization_id: str = None, tenant_id: str = None,
                           require_tenant: bool = False):
    """Resolve o escopo solicitado sem permitir fuga de organização ou tenant."""
    caller_role = auth.get('role')
    caller_org = auth.get('organizationId')
    caller_tenant = auth.get('tenantId')

    if isGlobalOrganizationRole(caller_role):
        if not organization_id or organization_id == PLATFORM_ORGANIZATION_ID:
            raise Exception('400::organizationId alvo deve ser informado para uma organização de negócio')
        if require_tenant and (not tenant_id or tenant_id == 'none'):
            raise Exception('400::tenantId alvo deve ser informado')
        return {
            'organizationId': organization_id,
            'tenantId': tenant_id or 'none'
        }

    if organization_id and organization_id != caller_org:
        raise Exception('403::Acesso negado para esta organização')

    if caller_tenant and caller_tenant != 'none':
        if tenant_id and tenant_id != caller_tenant:
            raise Exception('403::Acesso negado para este tenant')
        target_tenant = caller_tenant
    else:
        target_tenant = tenant_id or 'none'

    if require_tenant and target_tenant == 'none':
        raise Exception('400::tenantId alvo deve ser informado')

    return {
        'organizationId': caller_org,
        'tenantId': target_tenant
    }


def findSaaSEquipment(table_name: str, equipment_id: str, auth: dict, region: str = 'sa-east-1'):
    """Localiza um equipamento por id respeitando o escopo do chamador.

    Três caminhos, do mais restrito ao mais amplo:
      - usuário de tenant: get_item direto na PK do próprio tenant;
      - usuário de organização (`tenantId == 'none'`): query no GSI da organização,
        que impede achar equipamento de outra organização;
      - role global: scan (o equipamento pode estar em qualquer tenant do sistema).

    Devolve o item completo do DynamoDB ou levanta Exception('404::...'). Usado por
    todos os handlers que recebem um equipmentId na rota — a checagem de escopo é a
    única coisa que separa um tenant de ler o equipamento de outro, então ela mora
    aqui em vez de replicada por handler.
    """
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    tenant_id = auth.get('tenantId')

    if isGlobalOrganizationRole(auth.get('role')):
        items = getAllDynamoItems(table, 'scan',
            FilterExpression=boto3.dynamodb.conditions.Attr('equipmentId').eq(equipment_id)
        )
        if not items:
            raise Exception("404::Equipamento não encontrado no sistema")
        return items[0]

    if not hasTenantScope(tenant_id):
        items = getAllDynamoItems(table,
            IndexName='organizationId-tenantId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('organizationId').eq(auth['organizationId']),
            FilterExpression=boto3.dynamodb.conditions.Attr('equipmentId').eq(equipment_id)
        )
        if not items:
            raise Exception("404::Equipamento não encontrado nesta organização")
        return items[0]

    response = table.get_item(
        Key={
            'pk': f"TENANT#{tenant_id}#EQUIPMENT",
            'sk': f"EQUIPMENT#{equipment_id}"
        }
    )
    if 'Item' not in response:
        raise Exception("404::Equipamento não encontrado neste tenant")
    return response['Item']


def authorizeSaaS(token_data: dict, roles_table: str, module: str, action: str, region: str = 'sa-east-1'):
    """Autorização SaaS baseada em organizationId, tenantId e role dinâmica.

    Sem retrocompatibilidade: o token precisa conter organizationId, tenantId e organizationRole.

    A role é resolvida no escopo do tenant quando o token tem `tenantId != 'none'`, e
    no escopo da organização caso contrário — ver `resolveSaaSRoleDefinition`.
    """
    organization_id = token_data.get('organizationId')
    tenant_id = token_data.get('tenantId')
    role_name = token_data.get('organizationRole')
    user_id = token_data.get('sub')

    if not organization_id:
        raise Exception('401::Usuário não associado a uma organização')
    if not tenant_id:
        raise Exception('401::Usuário não associado a um tenant')
    if not role_name:
        raise Exception('401::Role da organização não informado no token')
    if not user_id:
        raise Exception('401::Identificação do usuário não encontrada no token')

    resolved_role_name = canonicalTenantRole(role_name) if hasTenantScope(tenant_id) else role_name
    role_def = resolveSaaSRoleDefinition(
        roles_table, organization_id, tenant_id, resolved_role_name, region
    )
    # Tokens e tabelas anteriores podem usar manager/technician/client. Primeiro
    # tentamos o id canônico e, se ele ainda não foi migrado, o id original no
    # mesmo escopo de tenant — nunca há fallback para a organização.
    if (not role_def.get('success') or not role_def.get('data')) and resolved_role_name != role_name:
        role_def = resolveSaaSRoleDefinition(
            roles_table, organization_id, tenant_id, role_name, region
        )
    if not role_def.get('success') or not role_def.get('data'):
        raise Exception(f"403::Role '{role_name}' não encontrada no escopo informado")

    if not checkPermission(role_def['data'], module, action):
        raise Exception(f"403::Acesso negado::Permissão '{module}:{action}' não disponível para o nível '{role_name}'")

    return {
        'organizationId': organization_id,
        'tenantId': tenant_id,
        'role': resolved_role_name,
        'tenantRole': canonicalTenantRole(token_data.get('tenantRole')),
        'permissions': role_def['data'].get('permissions', {})
    }
