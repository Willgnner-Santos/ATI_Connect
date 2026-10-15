# Fluxos de Validação Manual (SaaS)

Guia de validação manual do ciclo de vida dos principais módulos da API SaaS,
para rodar contra o ambiente de Sandbox usando a coleção Postman.

## 🔗 Endpoints base
**URL Sandbox:** `https://2wdbgmcijd.execute-api.sa-east-1.amazonaws.com/sandbox`

## 🚦 Pré-requisitos
1. Coleção Postman `plataforma-ati.postman_collection.json` carregada.
2. Variável de ambiente `{{url}}` (ou `{{base_url}}`) apontando para o endpoint acima.
3. Token de **Superadmin** para os passos que exigem criação de organização/usuário.

---

## Módulo Organization / Tenant

### 1. Criar Organização (`POST /saas/organization`)
*   **Request:** `SaaS > Organization > Create Organization`
*   **Body:**
    ```json
    {
        "name": "Cliente QA Teste",
        "email": "qa@cliente-teste.com",
        "type": "distributor",
        "admin": {
            "name": "Admin QA",
            "email": "admin.qa@cliente-teste.com",
            "password": "TemporaryPassword123!"
        }
    }
    ```
*   **Validação:** Status `201 Created`.
*   **Ação:** Salve o `organizationId` retornado na variável `{{org_id_test}}`.

### 2. Listar Organizações (`GET /saas/organization`)
*   **Request:** `SaaS > Organization > Get Organizations`
*   **Validação:**
    *   Status: `200 OK`.
    *   Verifique se "Cliente QA Teste" aparece na lista (se for superadmin) ou se apenas ela aparece (se logar com o admin da nova org).

### 3. Criar Tenant (`POST /saas/tenant`)
*   **Request:** `SaaS > Tenant > Create Tenant`
*   **Body:**
    ```json
    {
        "organizationId": "{{org_id_test}}",
        "name": "Unidade Industrial 01",
        "user": {
            "name": "Manager Unidade 01",
            "email": "manager.u1@cliente-teste.com",
            "password": "TemporaryPassword123!",
            "role": "manager"
        }
    }
    ```
*   **Validação:** Status `201 Created`.
*   **Ação:** Salve o `tenantId` na variável `{{tenant_id_test}}`.

### 4. Listar Tenants por Org (`GET /saas/tenant`)
*   **Request:** `SaaS > Tenant > Get Tenants`
*   **Query Param:** `?organizationId={{org_id_test}}`
*   **Validação:**
    *   Status: `200 OK`.
    *   A lista deve conter a "Unidade Industrial 01".

### 5. Teste de Segurança (Cross-Org)
*   Tente criar um Tenant enviando o `organizationId` de outra empresa (que não seja a sua).
*   **Esperado:** `403 Forbidden`.

---

## Módulo User

### 1. Autenticação
*   **Request:** `POST /saas/auth/login`
*   **Body:** Credenciais de superadmin.
*   **Esperado:** Receber `access_token` e salvá-lo como Bearer Token para os próximos passos.

### 2. Criar Usuário (POST)
*   **Endpoint:** `POST /saas/user`
*   **Body:**
    ```json
    {
        "name": "QA Test User",
        "email": "qa.user.test@plataforma-ati.com",
        "organizationId": "PLATFORM",
        "tenantId": "PLATFORM",
        "role": "admin",
        "password": "TemporaryPassword123!"
    }
    ```
*   **Validação:**
    *   Status: `201 Created`.
    *   Resposta: Deve conter o `userId`.
*   **Ação:** Salve o `userId` retornado para os passos seguintes.

### 3. Listar e Filtrar (GET)
*   **Endpoint:** `GET /saas/user?organizationId=PLATFORM`
*   **Validação:**
    *   Status: `200 OK`.
    *   Conteúdo: O usuário criado no passo anterior deve estar na lista.

### 4. Atualizar Dados (PUT)
*   **Endpoint:** `PUT /saas/user/{userId}`
*   **Body:**
    ```json
    {
        "name": "QA User Renomeado",
        "role": "manager"
    }
    ```
*   **Validação:**
    *   Status: `200 OK`.
    *   Verificação: Rode o GET novamente e confirme se o nome foi alterado.

### 5. Teste de Segurança (Escopo)
*   **Cenário:** Tente atualizar ou deletar um `userId` inexistente ou de outra organização.
*   **Esperado:** Status `404 Not Found` (devido ao filtro de `pk` por Org) ou `403 Forbidden`.

### 6. Excluir Usuário (DELETE)
*   **Endpoint:** `DELETE /saas/user/{userId}`
*   **Validação:**
    *   Status: `200 OK`.
*   **Verificação de Limpeza:** Tente fazer o GET do usuário. Ele não deve mais existir no DynamoDB nem permitir login no Cognito.

---

## Módulo Equipment

### 1. Criar Equipamento (`POST /saas/equipment`)
*   **Request:** `SaaS > Equipment > Create Equipment`
*   **Body:**
    ```json
    {
        "organizationId": "{{org_id_test}}",
        "tenantId": "{{tenant_id_test}}",
        "name": "Equipamento QA 01",
        "equipmentType": "sensor_box",
        "model": "ATI-V1",
        "local": "Setor de Carga",
        "sensors": {
            "temp_01": { "sensorType": "temperature", "unit": "°C" },
            "hum_01": { "sensorType": "humidity", "unit": "%" }
        }
    }
    ```
*   **Validação:** Status `201 Created`. Deve retornar o `equipmentId`, `thingName` e as chaves/certificados do IoT.
*   **Ação:** Salve o `equipmentId` na variável `{{equipment_id_test}}`.

### 2. Listar Equipamentos (`GET /saas/equipment`)
*   **Filtros:** `?organizationId={{org_id_test}}` ou `?tenantId={{tenant_id_test}}`.
*   **Validação:** Status `200 OK`. Verifique se o equipamento aparece e se os campos `thingId`, `endpoint` e `topic` estão preenchidos.

### 3. Visualizar Detalhes (`GET /saas/equipment/{id}`)
*   **Endpoint:** `{{url}}/saas/equipment/{{equipment_id_test}}`
*   **Validação:** Status `200 OK`. Deve trazer os detalhes completos, incluindo o dicionário de sensores.

### 4. Atualizar Equipamento (`PUT /saas/equipment/{id}`)
*   **Body:**
    ```json
    {
        "name": "Equipamento QA 01 - Modificado",
        "status": "inactive"
    }
    ```
*   **Validação:** Status `200 OK`.

### 5. Excluir Equipamento (`DELETE /saas/equipment/{id}`)
*   **Validação:** Status `200 OK`.
*   **Verificação no AWS Console (IoT Core):** A "Thing" associada e seu certificado devem ter sido removidos.

### 6. Teste de Segurança (Cross-Tenant)
*   Logue com um usuário de outro Tenant e tente dar `GET` ou `DELETE` no ID deste equipamento.
*   **Esperado:** `404 Not Found` ou `403 Forbidden`.

---

**Última Atualização:** 2026-08-07
**Status do Código:** Handlers revisados e corrigidos
