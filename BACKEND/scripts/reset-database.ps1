# ============================================
# Reset Database - Plataforma ATI SaaS
# ============================================
# ⚠️  ATENÇÃO: Este script apaga TODOS os dados!
#
# Remove:
# - Todos os items do DynamoDB
# - Todos os usuários do Cognito
#
# Recria:
# - Organização PLATFORM
# - Roles padrão
# - Superadmin

param(
    [string]$Stage = "sandbox",
    [string]$SuperadminEmail = "admin@plataforma-ati.com",
    [string]$UserPoolId,
    [Parameter(Mandatory = $true)]
    [SecureString]$SuperadminPassword,
    [switch]$Force  # Pular confirmação
)

$SuperadminPasswordPlain = [System.Net.NetworkCredential]::new('', $SuperadminPassword).Password

$ErrorActionPreference = "Stop"

# ============================================
# Configurações
# ============================================
$Project = "plataforma-ati"
$Region = "sa-east-1"
$SuperadminName = "Super Admin"

if ($Stage -notin @("sandbox", "staging", "production")) {
    Write-Host "❌ STAGE inválido: $Stage" -ForegroundColor Red
    exit 1
}

if (-not $UserPoolId -and $Stage -eq "sandbox") {
    $UserPoolId = "sa-east-1_USbMV3pst"
}
if (-not $UserPoolId) {
    throw "Informe -UserPoolId para o stage '$Stage'."
}

# Tabelas DynamoDB
$Tables = @(
    "$Project-$Stage-organizations",
    "$Project-$Stage-tenants",
    "$Project-$Stage-tenant-users",
    "$Project-$Stage-tenant-equipment",
    "$Project-$Stage-organization-roles",
    "$Project-$Stage-audit-log"
)

Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Red
Write-Host "║                                                                ║" -ForegroundColor Red
Write-Host "║  ⚠️  RESET COMPLETO - PLATAFORMA ATI SAAS                     ║" -ForegroundColor Red
Write-Host "║                                                                ║" -ForegroundColor Red
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Red
Write-Host ""
Write-Host "Stage: $Stage" -ForegroundColor Yellow
Write-Host "User Pool: $UserPoolId" -ForegroundColor Yellow
Write-Host ""
Write-Host "Este script vai:" -ForegroundColor Yellow
Write-Host "  ❌ Deletar TODOS os items das tabelas DynamoDB" -ForegroundColor Red
Write-Host "  ❌ Deletar TODOS os usuários do Cognito" -ForegroundColor Red
Write-Host "  ✅ Recriar organização PLATFORM" -ForegroundColor Green
Write-Host "  ✅ Recriar roles padrão" -ForegroundColor Green
Write-Host "  ✅ Recriar superadmin" -ForegroundColor Green
Write-Host ""

# ============================================
# Confirmação
# ============================================
if (-not $Force) {
    Write-Host "⚠️  ATENÇÃO: Esta ação NÃO pode ser desfeita!" -ForegroundColor Red
    Write-Host ""
    $confirmation = Read-Host "Digite 'RESET' (em maiúsculas) para confirmar"

    if ($confirmation -ne "RESET") {
        Write-Host "❌ Reset cancelado." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host ""
Write-Host "🔥 Iniciando reset..." -ForegroundColor Red
Write-Host ""

# ============================================
# 1. Limpar DynamoDB
# ============================================
Write-Host "🗑️  [1/3] Limpando DynamoDB..." -ForegroundColor Yellow
Write-Host ""

foreach ($table in $Tables) {
    Write-Host "   Limpando tabela: $table" -ForegroundColor Cyan

    # Obter todos os items
    $items = aws dynamodb scan `
        --table-name $table `
        --query "Items[].{pk:pk,sk:sk}" `
        --output json | ConvertFrom-Json

    if ($items.Count -eq 0) {
        Write-Host "      ✓ Tabela vazia" -ForegroundColor Gray
        continue
    }

    Write-Host "      Deletando $($items.Count) items..." -ForegroundColor Gray

    # Deletar items em batches de 25 (limite do DynamoDB)
    for ($i = 0; $i -lt $items.Count; $i += 25) {
        $batch = $items[$i..[Math]::Min($i + 24, $items.Count - 1)]

        $deleteRequests = @()
        foreach ($item in $batch) {
            $deleteRequests += @{
                DeleteRequest = @{
                    Key = @{
                        pk = $item.pk
                        sk = $item.sk
                    }
                }
            }
        }

        $requestItems = @{
            $table = $deleteRequests
        } | ConvertTo-Json -Depth 10

        aws dynamodb batch-write-item --request-items $requestItems | Out-Null
    }

    Write-Host "      ✅ $($items.Count) items deletados" -ForegroundColor Green
}

Write-Host ""
Write-Host "✅ DynamoDB limpo!" -ForegroundColor Green
Write-Host ""

# ============================================
# 2. Limpar Cognito
# ============================================
Write-Host "🗑️  [2/3] Limpando Cognito..." -ForegroundColor Yellow
Write-Host ""

# Listar todos os usuários
$users = aws cognito-idp list-users `
    --user-pool-id $UserPoolId `
    --query "Users[].Username" `
    --output json | ConvertFrom-Json

if ($users.Count -eq 0) {
    Write-Host "   ✓ Nenhum usuário encontrado" -ForegroundColor Gray
} else {
    Write-Host "   Deletando $($users.Count) usuários..." -ForegroundColor Gray

    foreach ($username in $users) {
        aws cognito-idp admin-delete-user `
            --user-pool-id $UserPoolId `
            --username $username 2>&1 | Out-Null
    }

    Write-Host "   ✅ $($users.Count) usuários deletados" -ForegroundColor Green
}

Write-Host ""
Write-Host "✅ Cognito limpo!" -ForegroundColor Green
Write-Host ""

# ============================================
# 3. Recriar Estrutura Base
# ============================================
Write-Host "🏗️  [3/3] Recriando estrutura base..." -ForegroundColor Yellow
Write-Host ""

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# 3.1 Criar Organização PLATFORM
Write-Host "   📦 Criando organização PLATFORM..." -ForegroundColor Cyan

$orgItem = @"
{
    "pk": {"S": "ORG#PLATFORM"},
    "sk": {"S": "METADATA"},
    "organizationId": {"S": "PLATFORM"},
    "name": {"S": "Plataforma ATI"},
    "type": {"S": "platform"},
    "status": {"S": "active"},
    "createdAt": {"S": "$Timestamp"},
    "updatedAt": {"S": "$Timestamp"}
}
"@

aws dynamodb put-item `
    --table-name "$Project-$Stage-organizations" `
    --item $orgItem | Out-Null

Write-Host "      ✅ Organização criada" -ForegroundColor Green

# 3.2 Criar Roles
Write-Host "   👥 Criando roles..." -ForegroundColor Cyan

$rolesJson = @"
{
    "$Project-$Stage-organization-roles": [
        {
            "PutRequest": {
                "Item": {
                    "pk": {"S": "ORG#PLATFORM#ROLES"},
                    "sk": {"S": "ROLE#superadmin"},
                    "organizationId": {"S": "PLATFORM"},
                    "roleId": {"S": "superadmin"},
                    "roleName": {"S": "Super Admin"},
                    "permissions": {"L": [{"S": "*:*"}]},
                    "description": {"S": "Super administrador com acesso total"},
                    "isSystemRole": {"BOOL": true},
                    "createdAt": {"S": "$Timestamp"},
                    "updatedAt": {"S": "$Timestamp"}
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "pk": {"S": "ORG#PLATFORM#ROLES"},
                    "sk": {"S": "ROLE#collaborator"},
                    "organizationId": {"S": "PLATFORM"},
                    "roleId": {"S": "collaborator"},
                    "roleName": {"S": "Colaborador"},
                    "permissions": {"L": [
                        {"S": "organization:*"},
                        {"S": "tenant:*"},
                        {"S": "user:*"},
                        {"S": "equipment:*"},
                        {"S": "action:*"},
                        {"S": "dashboard:read"},
                        {"S": "report:read"},
                        {"S": "catalog:*"},
                        {"S": "virtual-technician:*"}
                    ]},
                    "description": {"S": "Colaborador da plataforma com administração sobre todas as organizações"},
                    "isSystemRole": {"BOOL": true},
                    "createdAt": {"S": "$Timestamp"},
                    "updatedAt": {"S": "$Timestamp"}
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "pk": {"S": "ORG#PLATFORM#ROLES"},
                    "sk": {"S": "ROLE#admin"},
                    "organizationId": {"S": "PLATFORM"},
                    "roleId": {"S": "admin"},
                    "roleName": {"S": "Administrador"},
                    "permissions": {"L": [
                        {"S": "organization:*"},
                        {"S": "tenant:*"},
                        {"S": "user:*"},
                        {"S": "equipment:*"},
                        {"S": "action:*"}
                    ]},
                    "description": {"S": "Administrador da organização"},
                    "isSystemRole": {"BOOL": true},
                    "createdAt": {"S": "$Timestamp"},
                    "updatedAt": {"S": "$Timestamp"}
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "pk": {"S": "ORG#PLATFORM#ROLES"},
                    "sk": {"S": "ROLE#manager"},
                    "organizationId": {"S": "PLATFORM"},
                    "roleId": {"S": "manager"},
                    "roleName": {"S": "Gerente"},
                    "permissions": {"L": [
                        {"S": "tenant:read"},
                        {"S": "user:read"},
                        {"S": "user:write"},
                        {"S": "equipment:*"},
                        {"S": "action:*"}
                    ]},
                    "description": {"S": "Gerente de tenant"},
                    "isSystemRole": {"BOOL": true},
                    "createdAt": {"S": "$Timestamp"},
                    "updatedAt": {"S": "$Timestamp"}
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "pk": {"S": "ORG#PLATFORM#ROLES"},
                    "sk": {"S": "ROLE#viewer"},
                    "organizationId": {"S": "PLATFORM"},
                    "roleId": {"S": "viewer"},
                    "roleName": {"S": "Visualizador"},
                    "permissions": {"L": [
                        {"S": "tenant:read"},
                        {"S": "equipment:read"},
                        {"S": "action:read"},
                        {"S": "dashboard:read"},
                        {"S": "report:read"},
                        {"S": "catalog:read"},
                        {"S": "virtual-technician:read"}
                    ]},
                    "description": {"S": "Visualizador com acesso apenas leitura"},
                    "isSystemRole": {"BOOL": true},
                    "createdAt": {"S": "$Timestamp"},
                    "updatedAt": {"S": "$Timestamp"}
                }
            }
        }
    ]
}
"@

aws dynamodb batch-write-item --request-items $rolesJson | Out-Null

Write-Host "      ✅ 5 roles criadas" -ForegroundColor Green

# 3.3 Criar Superadmin
Write-Host "   👤 Criando superadmin..." -ForegroundColor Cyan

# Criar no Cognito
$userResponse = aws cognito-idp admin-create-user `
    --user-pool-id $UserPoolId `
    --username $SuperadminEmail `
    --user-attributes `
        Name=email,Value=$SuperadminEmail `
        Name=email_verified,Value=true `
        Name=name,Value="$SuperadminName" `
        Name=custom:organizationRole,Value=superadmin `
        Name=custom:organizationId,Value=PLATFORM `
        Name=custom:tenantId,Value=PLATFORM `
    --temporary-password $SuperadminPasswordPlain `
    --message-action SUPPRESS | ConvertFrom-Json

$UserId = $userResponse.User.Username

# Definir senha permanente
aws cognito-idp admin-set-user-password `
    --user-pool-id $UserPoolId `
    --username $SuperadminEmail `
    --password $SuperadminPasswordPlain `
    --permanent | Out-Null

# Adicionar no DynamoDB
$userItem = @"
{
    "pk": {"S": "ORG#PLATFORM#USER"},
    "sk": {"S": "USER#$UserId"},
    "userId": {"S": "$UserId"},
    "organizationId": {"S": "PLATFORM"},
    "tenantId": {"S": "PLATFORM"},
    "email": {"S": "$SuperadminEmail"},
    "name": {"S": "$SuperadminName"},
    "role": {"S": "superadmin"},
    "status": {"S": "active"},
    "createdAt": {"S": "$Timestamp"},
    "updatedAt": {"S": "$Timestamp"}
}
"@

aws dynamodb put-item `
    --table-name "$Project-$Stage-tenant-users" `
    --item $userItem | Out-Null

Write-Host "      ✅ Superadmin criado (User ID: $UserId)" -ForegroundColor Green

Write-Host ""
Write-Host "✅ Estrutura base recriada!" -ForegroundColor Green
Write-Host ""

# ============================================
# Resumo Final
# ============================================
Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                                                                ║" -ForegroundColor Green
Write-Host "║  ✅ RESET COMPLETO FINALIZADO!                                ║" -ForegroundColor Green
Write-Host "║                                                                ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "📊 Estatísticas:" -ForegroundColor White
Write-Host "   🗑️  Tabelas limpas: $($Tables.Count)" -ForegroundColor Gray
Write-Host "   👤 Usuários removidos: $($users.Count)" -ForegroundColor Gray
Write-Host ""
Write-Host "✨ Estrutura recriada:" -ForegroundColor White
Write-Host "   ✅ 1 Organização (PLATFORM)" -ForegroundColor Green
Write-Host "   ✅ 4 Roles (superadmin, admin, manager, viewer)" -ForegroundColor Green
Write-Host "   ✅ 1 Superadmin" -ForegroundColor Green
Write-Host ""
Write-Host "👤 Credenciais do Superadmin:" -ForegroundColor White
Write-Host "   Email: $SuperadminEmail" -ForegroundColor Cyan
Write-Host "   Password: configurada via SecureString" -ForegroundColor Cyan
Write-Host "   Role: superadmin" -ForegroundColor Cyan
Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  🎉 Sistema resetado e pronto para uso!                       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
