import json
import boto3
import traceback
from os import environ
from datetime import datetime
import base64
from plataforma_ati import (
    validateTokenSaaS, 
    authorizeSaaS, 
    lambdaReturn, 
    getSaaSConfig,
    validateInput,
    isGlobalOrganizationRole,
    validateSaaSRoleAssignment,
    validateSaaSTargetRoleExists
)

def handler(event, context):
    print("INFO: Iniciando atualização de usuário SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::User ID não informado")
        user_id = path_params['id']

        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 4. Validação de Input (campos opcionais para update)
        validate = validateInput([
            {'field': 'name', 'type': 'str', 'mandatory': False},
            {'field': 'email', 'type': 'str', 'mandatory': False, 'pattern': r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
            {'field': 'role', 'type': 'str', 'mandatory': False},
            {'field': 'tenantRole', 'type': 'str', 'mandatory': False},
            {'field': 'status', 'type': 'str', 'mandatory': False},
            {'field': 'password', 'type': 'str', 'mandatory': False}
        ], body)

        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 5. Execução
        result = process_request(token_data['data'], user_id, body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Usuário atualizado com sucesso",
            "data": result
        })

    except Exception as e:
        traceback.print_exc()
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

def process_request(token_claims, user_id, body, region, stage):
    # 1. Identificar o chamador (caller) — user_id (path) é o username/e-mail no Cognito, não o 'sub'
    caller_username = token_claims.get('username')
    caller_org_id = token_claims.get('organizationId')
    caller_tenant_id = token_claims.get('tenantId')
    caller_role = token_claims.get('organizationRole')

    # 2. Verificar se o chamador está editando o próprio perfil
    is_self = (caller_username == user_id)

    # 3. Validação de Segurança e Permissões (antes de tocar no Cognito do usuário alvo,
    # para não permitir enumeração de e-mail por quem não tem permissão administrativa)
    if is_self:
        # Se for atualização de si próprio: apenas nome e senha
        restricted_fields = ['email', 'role', 'tenantRole', 'status']
        for field in restricted_fields:
            if field in body:
                raise Exception(f"403::Acesso negado: você não pode alterar seu próprio {field}")
    else:
        # Se for administrativo (editando outro usuário): requer autorização de permissões
        # authorizeSaaS irá verificar se possui permissão 'user:write'
        authorizeSaaS(token_claims, environ['DYNAMODB_TABLE_ROLES'], 'user', 'write', region)

    # 4. Buscar informações do Cognito para o usuário alvo
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")

    cognito = boto3.client('cognito-idp', region_name=region)
    try:
        user_info = cognito.admin_get_user(
            UserPoolId=config['userPoolId'],
            Username=user_id
        )
    except cognito.exceptions.UserNotFoundException:
        raise Exception("404::Usuário não encontrado")
    except Exception as e:
        raise Exception(f"500::Erro ao buscar usuário no Cognito: {str(e)}")

    # Extrair atributos customizados do usuário alvo
    target_attrs = {}
    for attr in user_info['UserAttributes']:
        if attr['Name'].startswith('custom:'):
            key = attr['Name'].replace('custom:', '')
            target_attrs[key] = attr['Value']

    target_org_id = target_attrs.get('organizationId', 'none')
    target_tenant_id = target_attrs.get('tenantId', 'none')
    target_role = target_attrs.get('organizationRole', 'guest')
    if 'tenantRole' in body and body['tenantRole'] not in ('gestor', 'tecnico', 'user', 'manager', 'technician', 'client'):
        raise Exception("400::tenantRole inválida")

    # 5. Checagens de escopo administrativas (não aplicam a is_self)
    if not is_self:
        # Superadmin ignora restrições de org/tenant
        validateSaaSRoleAssignment(
            caller_role,
            target_org_id,
            body.get('role', target_role)
        )
        if 'role' in body:
            # O escopo do alvo define onde a role tem que existir: usuário de tenant
            # resolve a role em TENANT#{tenantId}#ROLES
            validateSaaSTargetRoleExists(
                environ['DYNAMODB_TABLE_ROLES'],
                target_org_id,
                body['role'],
                region,
                target_tenant_id
            )
        has_global_scope = isGlobalOrganizationRole(caller_role)
        if not has_global_scope:
            # 1. Deve pertencer à mesma organização
            if target_org_id != caller_org_id:
                raise Exception("403::Acesso negado para esta organização")

            # 2. Se for admin da Org (tenantId == 'none'), pode editar qualquer tenant da sua org
            # Se não for admin de org (é admin/manager de tenant), só pode editar o próprio tenant
            is_org_admin = caller_tenant_id in (None, 'none')
            if not is_org_admin:
                if target_tenant_id != caller_tenant_id:
                    raise Exception("403::Acesso negado para este tenant")

    # 6. Filtrar campos permitidos
    allowed_fields = ['name', 'email', 'role', 'tenantRole', 'status']
    update_data = {k: v for k, v in body.items() if k in allowed_fields}
    if 'status' in update_data and update_data['status'] not in ('active', 'inactive'):
        raise Exception("400::status deve ser active ou inactive; use os endpoints de delete/reactivate para exclusão")

    # 6. Atualizar Senha no Cognito (se informado)
    if 'password' in body and body['password']:
        print("INFO: Atualizando senha no Cognito...")
        try:
            cognito.admin_set_user_password(
                UserPoolId=config['userPoolId'],
                Username=user_id,
                Password=body['password'],
                Permanent=True
            )
        except Exception as e:
            raise Exception(f"500::Erro ao atualizar senha no Cognito: {str(e)}")

    status_changed = 'status' in update_data
    if status_changed:
        try:
            operation = cognito.admin_enable_user if update_data['status'] == 'active' else cognito.admin_disable_user
            operation(UserPoolId=config['userPoolId'], Username=user_id)
        except Exception as e:
            raise Exception(f"500::Erro ao sincronizar status no Cognito: {str(e)}")

    # 7. Atualizar Atributos no Cognito (se informado)
    if 'email' in update_data or 'role' in update_data or 'name' in update_data or 'tenantRole' in update_data:
        print("INFO: Atualizando atributos no Cognito...")
        user_attributes = []
        if 'name' in update_data:
            user_attributes.append({'Name': 'name', 'Value': update_data['name']})
        if 'email' in update_data:
            user_attributes.append({'Name': 'email', 'Value': update_data['email']})
        if 'role' in update_data:
            user_attributes.append({'Name': 'custom:organizationRole', 'Value': update_data['role']})
        if 'tenantRole' in update_data:
            user_attributes.append({'Name': 'custom:tenantRole', 'Value': update_data['tenantRole']})
            
        try:
            cognito.admin_update_user_attributes(
                UserPoolId=config['userPoolId'],
                Username=user_id,
                UserAttributes=user_attributes
            )
        except Exception as e:
            raise Exception(f"500::Erro ao atualizar atributos no Cognito: {str(e)}")

    # 8. Atualizar no DynamoDB (se houver campos além da senha)
    if update_data:
        print("INFO: Atualizando metadados no DynamoDB...")
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(environ['DYNAMODB_TABLE_USERS'])

        update_expr = "SET " + ", ".join([f"#{k} = :{k}" for k in update_data.keys()])
        update_expr += ", #updatedAt = :updatedAt"

        expr_attr_names = {f"#{k}": k for k in update_data.keys()}
        expr_attr_names['#updatedAt'] = 'updatedAt'

        expr_attr_values = {f":{k}": v for k, v in update_data.items()}
        expr_attr_values[':updatedAt'] = datetime.now().isoformat()

        # O pk deve usar o organizationId real do usuário alvo (target_org_id)
        pk = f"ORG#{target_org_id}#USER"

        try:
            table.update_item(
                Key={'pk': pk, 'sk': f"USER#{user_id}"},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values,
                ConditionExpression="attribute_exists(pk)"
            )
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            if status_changed:
                try:
                    rollback = cognito.admin_enable_user if user_info.get('Enabled') else cognito.admin_disable_user
                    rollback(UserPoolId=config['userPoolId'], Username=user_id)
                except Exception as rollback_error:
                    print(f"ERROR: Falha ao compensar status no Cognito: {rollback_error}")
            raise Exception("404::Usuário não encontrado no DynamoDB")
        except Exception as e:
            if status_changed:
                try:
                    rollback = cognito.admin_enable_user if user_info.get('Enabled') else cognito.admin_disable_user
                    rollback(UserPoolId=config['userPoolId'], Username=user_id)
                except Exception as rollback_error:
                    print(f"ERROR: Falha ao compensar status no Cognito: {rollback_error}")
            raise Exception(f"500::Erro ao atualizar no DynamoDB: {str(e)}")

    return {"userId": user_id}
