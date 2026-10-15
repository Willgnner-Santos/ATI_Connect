# Técnico Virtual - Virtual Technician

**Status**: ✅ Completo (CRUD + processing layer)

---

## Visão Geral

O **Técnico Virtual** é um sistema de automação que permite executar ações automaticamente baseadas em:
- **Condições** dos sensores (ex: temperatura > 40°C)
- **Horários** programados (ex: todo dia às 8h)
- **Eventos** específicos

---

## Tipos de Ação

### 1. Action (Ação MQTT)
Envia comandos MQTT para os equipamentos quando condições são atendidas.

**Exemplo**: Quando temperatura > 40°C, enviar comando para ligar ventilação

```json
{
  "name": "Ligar ventilação se superaquecer",
  "type": "action",
  "equipmentId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "conditions": [
    {
      "variable": "temperature",
      "operator": ">",
      "value": "40",
      "type": "number",
      "condition": "AND"
    },
    {
      "variable": "fan_status",
      "operator": "=",
      "value": "off",
      "type": "string",
      "condition": "AND"
    }
  ],
  "actions": [
    {
      "variable": "fan",
      "value": "on",
      "type": "boolean",
      "delay": "0"
    }
  ]
}
```

**Fluxo**:
1. IoT Core recebe telemetria do equipamento
2. IoT Rule avalia condições (temperatura > 40 AND fan_status = off)
3. Se condições atendidas, envia mensagem para SQS
4. Lambda valida se condição ainda é válida
5. Lambda executa ação: publica MQTT `{"recurso": "fan", "valor": true, "type": "send"}`

---

### 2. Notification (Notificação)
Envia email/SMS quando condições são atendidas.

**Exemplo**: Quando porta aberta E elevador em movimento, notificar equipe

```json
{
  "name": "Alerta de porta aberta em movimento",
  "type": "notification",
  "equipmentId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "conditions": [
    {
      "variable": "door_status",
      "operator": "=",
      "value": "open",
      "type": "string",
      "condition": "AND"
    },
    {
      "variable": "movement",
      "operator": "=",
      "value": "true",
      "type": "boolean",
      "condition": "AND"
    }
  ],
  "notification": {
    "subject": "ALERTA: Porta aberta em movimento",
    "message": "Elevador {{equipmentName}} está em movimento com porta aberta!",
    "recipients": [
      "manutencao@empresa.com",
      "+5511999998888"
    ]
  }
}
```

**Fluxo**:
1. IoT Core recebe telemetria
2. IoT Rule avalia condições
3. Se atendidas, envia para SQS de notificações
4. Lambda valida condições
5. Lambda envia email via SNS/SES e SMS via SNS

---

### 3. Schedule (Agendamento)
Executa ações em horários programados (cron/interval).

**Exemplo 1**: Todo dia às 8h, ligar iluminação

```json
{
  "name": "Ligar iluminação às 8h",
  "type": "schedule",
  "equipmentId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "schedule": {
    "type": "interval",
    "interval": "daily",
    "time": "08:00"
  },
  "actions": [
    {
      "variable": "lights",
      "value": "on",
      "type": "boolean",
      "delay": "0"
    }
  ]
}
```

**Exemplo 2**: Toda segunda e quarta às 18h, executar manutenção preventiva

```json
{
  "name": "Manutenção preventiva",
  "type": "schedule",
  "equipmentId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "schedule": {
    "type": "interval",
    "interval": "weekly",
    "weekDay": [1, 3],
    "time": "18:00"
  },
  "actions": [
    {
      "variable": "maintenance_mode",
      "value": "true",
      "type": "boolean",
      "delay": "0"
    },
    {
      "variable": "lights",
      "value": "off",
      "type": "boolean",
      "delay": "5"
    }
  ]
}
```

**Exemplo 3**: Expressão cron customizada

```json
{
  "name": "Reset diário",
  "type": "schedule",
  "equipmentId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "schedule": {
    "type": "cron",
    "cron": {
      "minute": "0",
      "hour": "3",
      "dayOfMonth": "*",
      "month": "*",
      "dayOfWeek": "?"
    }
  },
  "actions": [
    {
      "variable": "reset",
      "value": "true",
      "type": "boolean",
      "delay": "0"
    }
  ]
}
```

**Fluxo**:
1. EventBridge Schedule dispara no horário programado
2. Lambda executor recebe evento
3. Lambda publica comandos MQTT na sequência (respeitando delays)

---

## Operadores Suportados

| Operador | Descrição | Exemplo |
|----------|-----------|---------|
| `=` | Igual | `temperature = 25` |
| `>` | Maior que | `temperature > 40` |
| `<` | Menor que | `pressure < 5` |
| `>=` | Maior ou igual | `speed >= 10` |
| `<=` | Menor ou igual | `humidity <= 60` |
| `!=` | Diferente | `status != error` |

---

## Condições Lógicas

Múltiplas condições podem ser combinadas com `AND` / `OR`:

```json
"conditions": [
  {
    "variable": "temperature",
    "operator": ">",
    "value": "40",
    "condition": "AND"
  },
  {
    "variable": "humidity",
    "operator": ">",
    "value": "80",
    "condition": "OR"
  },
  {
    "variable": "pressure",
    "operator": "<",
    "value": "5",
    "condition": "AND"
  }
]
```

**Avaliação**: `(temperature > 40 AND humidity > 80) OR (pressure < 5)`

A condição do **último item é ignorada** (não há próxima condição para combinar).

---

## API Endpoints

### Listar Ações
```http
GET /saas/virtual-technician
Authorization: Bearer {token}
```

**Query params**:
- `equipmentId` (opcional): Filtrar por equipamento
- `type` (opcional): Filtrar por tipo (action|notification|schedule)
- `status` (opcional): Filtrar por status (active|inactive)

**Resposta**:
```json
{
  "statusCode": 200,
  "message": "Ações listadas com sucesso",
  "data": {
    "actions": [
      {
        "actionId": "uuid",
        "name": "Ligar ventilação",
        "type": "action",
        "equipmentId": "uuid",
        "equipmentName": "Elevador A",
        "status": "active",
        "conditions": [...],
        "actions": [...],
        "ruleName": "ati_tenant_action_uuid",
        "createdAt": "2026-05-21T10:00:00Z",
        "createdBy": "user-sub",
        "updatedAt": "2026-05-21T10:00:00Z"
      }
    ],
    "total": 1
  }
}
```

---

### Criar Ação
```http
POST /saas/virtual-technician
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "Nome da ação",
  "type": "action|notification|schedule",
  "equipmentId": "uuid",
  "status": "active|inactive",
  "conditions": [...],        // Para action/notification
  "actions": [...],           // Para action/schedule
  "notification": {...},      // Para notification
  "schedule": {...}           // Para schedule
}
```

**Resposta**:
```json
{
  "statusCode": 201,
  "message": "Ação criada com sucesso",
  "data": {
    "actionId": "uuid",
    "name": "Nome da ação",
    "type": "action",
    "equipmentId": "uuid",
    "equipmentName": "Elevador A",
    "status": "active",
    "ruleName": "ati_tenant_action_uuid",
    "createdAt": "2026-05-21T10:00:00Z"
  }
}
```

---

### Atualizar Ação
```http
PUT /saas/virtual-technician/{actionId}
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "Novo nome (opcional)",
  "status": "active|inactive (opcional)",
  "conditions": [...],
  "actions": [...],
  ...
}
```

---

### Deletar Ação
```http
DELETE /saas/virtual-technician/{actionId}
Authorization: Bearer {token}
```

**Efeito**:
- Remove ação do DynamoDB
- Deleta IoT Rule (se tipo action/notification)
- Deleta EventBridge Schedule (se tipo schedule)

---

## Arquitetura

### Fluxo de Action/Notification

```
1. Equipamento publica telemetria
   ↓
2. IoT Core recebe no tópico ati/tenant/equipment/telemetry
   ↓
3. IoT Rule avalia condições SQL
   ↓
4. Se condições atendidas → SQS (action ou notification queue)
   ↓
5. Lambda Validator verifica se ainda é válido
   ↓
6. Lambda Executor:
   - Action: publica comando MQTT
   - Notification: envia email/SMS via SNS
```

### Fluxo de Schedule

```
1. EventBridge Schedule dispara no horário
   ↓
2. Lambda Executor recebe evento
   ↓
3. Lambda publica comandos MQTT sequencialmente
```

---

## Componentes da Infraestrutura

### DynamoDB Tables
- **virtual-technician**: Armazena configuração das ações
  - PK: `TENANT#{tenantId}#VIRTUAL_TECHNICIAN`
  - SK: `ACTION#{actionId}`
  - GSI: tenantId, equipmentId, type, status

### SQS Queues
- **virtual-technician-action**: Ações MQTT
- **virtual-technician-notification**: Notificações

### IoT Rules
- Nomeadas como: `ati_{tenantId}_{type}_{actionId}`
- SQL dinâmico baseado nas condições
- Ação: enviar para SQS

### EventBridge Schedules
- Nomeados como: `ati_{tenantId}_{actionId}`
- Expressão cron baseada na configuração
- Target: Lambda Executor

### Lambdas
- **VirtualTechnicianListFunction**: Lista ações
- **VirtualTechnicianCreateFunction**: Cria ações + IoT Rule/Schedule
- **VirtualTechnicianUpdateFunction**: Atualiza ações
- **VirtualTechnicianDeleteFunction**: Deleta ações + limpa recursos
- **VirtualTechnicianValidatorFunction**: Valida condições antes de executar
- **VirtualTechnicianExecutorFunction**: Executa ações MQTT
- **VirtualTechnicianNotifierFunction**: Envia notificações

---

## Status de Implementação

### ✅ Completo - CRUD Endpoints
- [x] Tabela DynamoDB
- [x] Endpoint GET (listar ações)
- [x] Endpoint POST (criar ações)
- [x] Endpoint GET /{id} (ver detalhes)
- [x] Endpoint PUT (atualizar ações)
- [x] Endpoint DELETE (deletar ações)
- [x] SQS Queues (+ DLQ com RedrivePolicy)
- [x] Criação de IoT Rules dinâmicas
- [x] Criação de EventBridge Schedules
- [x] Atualização de IoT Rules/Schedules
- [x] Deleção de IoT Rules/Schedules

### ✅ Completo - Processing Layer
- [x] Lambda Validator (valida condições sem `eval()`, allowlist de operadores)
- [x] Lambda Executor (executa ações MQTT, com deduplicação best-effort)
- [x] Lambda Notifier (envia email/SMS/Telegram via `NotificationManager`)
- [x] `batchItemFailures` nos 3 workers (retry seletivo via SQS)

### ⬜ Pendente
- [ ] Testes end-to-end automatizados

---

## Exemplos Práticos

### Caso 1: Segurança - Porta aberta
```bash
# Criar notificação de porta aberta
curl -X POST https://api/saas/virtual-technician \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Alerta porta aberta",
    "type": "notification",
    "equipmentId": "uuid",
    "status": "active",
    "conditions": [
      {
        "variable": "door",
        "operator": "=",
        "value": "open",
        "type": "string",
        "condition": "AND"
      }
    ],
    "notification": {
      "subject": "Porta aberta",
      "message": "Porta do elevador está aberta há mais de 5 minutos",
      "recipients": ["admin@empresa.com"]
    }
  }'
```

### Caso 2: Eficiência Energética
```bash
# Desligar iluminação à noite
curl -X POST https://api/saas/virtual-technician \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Desligar luzes 22h",
    "type": "schedule",
    "equipmentId": "uuid",
    "status": "active",
    "schedule": {
      "type": "interval",
      "interval": "daily",
      "time": "22:00"
    },
    "actions": [
      {
        "variable": "lights",
        "value": "off",
        "type": "boolean",
        "delay": "0"
      }
    ]
  }'
```

### Caso 3: Manutenção Preventiva
```bash
# Reset automático quando detectar anomalia
curl -X POST https://api/saas/virtual-technician \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Reset em caso de erro",
    "type": "action",
    "equipmentId": "uuid",
    "status": "active",
    "conditions": [
      {
        "variable": "error_count",
        "operator": ">",
        "value": "5",
        "type": "number",
        "condition": "AND"
      }
    ],
    "actions": [
      {
        "variable": "reset",
        "value": "true",
        "type": "boolean",
        "delay": "0"
      }
    ]
  }'
```

---

## Próximos Passos

1. Testes end-to-end automatizados
2. Documentar exemplos de uso com equipamentos reais
