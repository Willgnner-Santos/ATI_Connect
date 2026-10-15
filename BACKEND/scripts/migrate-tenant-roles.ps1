# ============================================
# Migração: roles por tenant + permissões de ação
# ============================================
# Necessário porque a resolução de role passou a ser ESTRITA: um usuário com
# custom:tenantId != 'none' resolve a role apenas em TENANT#{tenantId}#ROLES.
# Sem esta migração, todo usuário de tenant já existente toma 403 em qualquer
# endpoint (a role 'manager'/'viewer' dele só existe em ORG#{orgId}#ROLES).
#
# O que faz:
#   1. Semeia as roles 'tecnico' e 'user' em TENANT#{tenantId}#ROLES para cada
#      tenant existente.
#   2. Migra a role dos usuários de tenant: manager/admin -> tecnico,
#      viewer/qualquer outra -> user (DynamoDB + atributo do Cognito).
#   3. Acrescenta as permissões do módulo 'action' às roles de organização
#      existentes (admin/manager -> action:*, viewer -> action:read), senão nem
#      o admin da organização consegue listar ou enviar comando.
#
# NÃO apaga nada e é idempotente: rodar duas vezes produz o mesmo estado.
#
# Uso:
#   .\scripts\migrate-tenant-roles.ps1 -Stage sandbox
#   .\scripts\migrate-tenant-roles.ps1 -Stage sandbox -DryRun

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("sandbox", "staging", "production")]
    [string]$Stage,

    [string]$UserPoolId,

    [switch]$DryRun,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Project = "plataforma-ati"
$Region = "sa-east-1"

if (-not $UserPoolId -and $Stage -eq "sandbox") {
    $UserPoolId = "sa-east-1_USbMV3pst"
}
if (-not $UserPoolId) {
    throw "Informe -UserPoolId para o stage '$Stage'."
}

$TenantsTable = "$Project-$Stage-tenants"
$UsersTable = "$Project-$Stage-tenant-users"
$RolesTable = "$Project-$Stage-organization-roles"

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Mapa role de organização -> role de tenant equivalente
$RoleMap = @{
    "admin"   = "tecnico"
    "manager" = "tecnico"
    "viewer"  = "user"
}
$DefaultTenantRole = "user"

Write-Host ""
Write-Host "Migracao de roles de tenant - stage: $Stage" -ForegroundColor Cyan
Write-Host "  Tabelas: $TenantsTable, $UsersTable, $RolesTable" -ForegroundColor Gray
Write-Host "  User Pool: $UserPoolId" -ForegroundColor Gray
if ($DryRun) { Write-Host "  MODO DRY-RUN: nada será gravado" -ForegroundColor Yellow }
Write-Host ""

if (-not $DryRun -and -not $Force) {
    $confirmation = Read-Host "Digite o stage '$Stage' para confirmar a gravação"
    if ($confirmation -ne $Stage) {
        Write-Host "Cancelado." -ForegroundColor Yellow
        exit 0
    }
}

function Invoke-Write {
    param([string]$Description, [scriptblock]$Action)
    if ($DryRun) {
        Write-Host "      [dry-run] $Description" -ForegroundColor DarkGray
        return
    }
    & $Action | Out-Null
    Write-Host "      $Description" -ForegroundColor Green
}

# ============================================
# 1. Semear roles de tenant
# ============================================
Write-Host "[1/3] Semeando roles padrão nos tenants existentes..." -ForegroundColor Yellow

$tenants = aws dynamodb scan `
    --table-name $TenantsTable `
    --region $Region `
    --query 'Items[?tenantId != null].{tenantId:tenantId.S,organizationId:organizationId.S,name:name.S}' `
    --output json | ConvertFrom-Json

if (-not $tenants) { $tenants = @() }
Write-Host "   $($tenants.Count) tenant(s) encontrado(s)" -ForegroundColor Gray

$tenantRoleDefs = @(
    @{
        roleId      = "gestor"
        roleName    = "Gestor"
        description = "Gerencia técnicos e usuários do tenant, sem operar equipamentos"
        permissions = @(
            "user:read", "user:write", "equipment:read", "dashboard:read",
            "report:read", "catalog:read"
        )
    },
    @{
        roleId      = "tecnico"
        roleName    = "Técnico"
        description = "Opera o equipamento: envia comandos liberados para a role"
        permissions = @(
            "equipment:read", "equipment:update", "action:read", "action:execute",
            "dashboard:read", "report:read", "catalog:read", "virtual-technician:read",
            "virtual-technician:write", "virtual-technician:delete"
        )
    },
    @{
        roleId      = "user"
        roleName    = "Usuário"
        description = "Apenas observa: vê sensores liberados, sem enviar comandos"
        permissions = @(
            "equipment:read", "action:read", "dashboard:read", "report:read",
            "catalog:read", "virtual-technician:read"
        )
    }
)

foreach ($tenant in $tenants) {
    Write-Host "   Tenant $($tenant.tenantId) ($($tenant.name))" -ForegroundColor Cyan
    foreach ($roleDef in $tenantRoleDefs) {
        $itemObj = [ordered]@{
            pk             = @{ S = "TENANT#$($tenant.tenantId)#ROLES" }
            sk             = @{ S = "ROLE#$($roleDef.roleId)" }
            tenantId       = @{ S = $tenant.tenantId }
            organizationId = @{ S = $tenant.organizationId }
            roleId         = @{ S = $roleDef.roleId }
            roleName       = @{ S = $roleDef.roleName }
            description    = @{ S = $roleDef.description }
            permissions    = @{ L = @($roleDef.permissions | ForEach-Object { @{ S = $_ } }) }
            isSystemRole   = @{ BOOL = $true }
            createdAt      = @{ S = $Timestamp }
            updatedAt      = @{ S = $Timestamp }
        }
        $itemJson = $itemObj | ConvertTo-Json -Depth 5 -Compress
        $tmpFile = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($tmpFile, $itemJson, [System.Text.UTF8Encoding]::new($false))
        Invoke-Write "role '$($roleDef.roleId)' criada/atualizada" {
            aws dynamodb put-item --table-name $RolesTable --region $Region --item "file://$tmpFile"
        }
        Remove-Item $tmpFile -ErrorAction SilentlyContinue
    }
}

# ============================================
# 2. Migrar a role dos usuários de tenant
# ============================================
Write-Host ""
Write-Host "[2/3] Migrando usuários de tenant para as novas roles..." -ForegroundColor Yellow

$users = aws dynamodb scan `
    --table-name $UsersTable `
    --region $Region `
    --query 'Items[?tenantId != null && tenantId.S != ''none''].{userId:userId.S,organizationId:organizationId.S,tenantId:tenantId.S,role:role.S,email:email.S}' `
    --output json | ConvertFrom-Json

if (-not $users) { $users = @() }
Write-Host "   $($users.Count) usuário(s) de tenant encontrado(s)" -ForegroundColor Gray

foreach ($user in $users) {
    $currentRole = $user.role
    if ($tenantRoleDefs.roleId -contains $currentRole) {
        Write-Host "   $($user.email): já usa role de tenant '$currentRole', ignorado" -ForegroundColor DarkGray
        continue
    }

    $newRole = $RoleMap[$currentRole]
    if (-not $newRole) { $newRole = $DefaultTenantRole }

    Write-Host "   $($user.email): '$currentRole' -> '$newRole'" -ForegroundColor Cyan

    $updateInput = [ordered]@{
        TableName                = $UsersTable
        Key                      = [ordered]@{
            pk = @{ S = "ORG#$($user.organizationId)#USER" }
            sk = @{ S = "USER#$($user.userId)" }
        }
        UpdateExpression         = "SET #r = :r, updatedAt = :u"
        ExpressionAttributeNames = @{ '#r' = 'role' }
        ExpressionAttributeValues = [ordered]@{
            ':r' = @{ S = $newRole }
            ':u' = @{ S = $Timestamp }
        }
    }
    $updateJson = $updateInput | ConvertTo-Json -Depth 5 -Compress
    $tmpUpdate = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpUpdate, $updateJson, [System.Text.UTF8Encoding]::new($false))

    if ($DryRun) {
        Write-Host "      [dry-run] DynamoDB atualizado" -ForegroundColor DarkGray
        Write-Host "      [dry-run] Cognito atualizado" -ForegroundColor DarkGray
    } else {
        aws dynamodb update-item --region $Region --cli-input-json "file://$tmpUpdate" | Out-Null
        Write-Host "      DynamoDB atualizado" -ForegroundColor Green
        aws cognito-idp admin-update-user-attributes --user-pool-id $UserPoolId --region $Region --username $user.userId --user-attributes "Name=custom:organizationRole,Value=$newRole" | Out-Null
        Write-Host "      Cognito atualizado" -ForegroundColor Green
    }
    Remove-Item $tmpUpdate -ErrorAction SilentlyContinue
}

# ============================================
# 3. Acrescentar permissões de 'action' às roles de organização
# ============================================
Write-Host ""
Write-Host "[3/3] Acrescentando permissões do módulo 'action' às roles de organização..." -ForegroundColor Yellow

$orgActionPermissions = @{
    "admin"        = "action:*"
    "manager"      = "action:*"
    "collaborator" = "action:*"
    "viewer"       = "action:read"
}

$roleItems = aws dynamodb scan `
    --table-name $RolesTable `
    --region $Region `
    --query 'Items[?starts_with(pk.S, ''ORG#'')].{pk:pk.S,sk:sk.S,roleId:roleId.S,permissions:permissions.L[].S}' `
    --output json | ConvertFrom-Json

if (-not $roleItems) { $roleItems = @() }

foreach ($roleItem in $roleItems) {
    $needed = $orgActionPermissions[$roleItem.roleId]
    if (-not $needed) {
        Write-Host "   $($roleItem.pk)/$($roleItem.sk): role sem mapeamento, ignorada" -ForegroundColor DarkGray
        continue
    }

    $permissions = @($roleItem.permissions)
    if ($permissions -contains $needed -or $permissions -contains "*:*") {
        Write-Host "   $($roleItem.pk)/$($roleItem.sk): já possui '$needed'" -ForegroundColor DarkGray
        continue
    }

    $permissions += $needed

    Write-Host "   $($roleItem.pk)/$($roleItem.sk): + $needed" -ForegroundColor Cyan

    $updateInput2 = [ordered]@{
        TableName                 = $RolesTable
        Key                       = [ordered]@{
            pk = @{ S = $roleItem.pk }
            sk = @{ S = $roleItem.sk }
        }
        UpdateExpression          = "SET #p = :p, updatedAt = :u"
        ExpressionAttributeNames  = @{ '#p' = 'permissions' }
        ExpressionAttributeValues = [ordered]@{
            ':p' = @{ L = @($permissions | ForEach-Object { @{ S = $_ } }) }
            ':u' = @{ S = $Timestamp }
        }
    }
    $updateJson2 = $updateInput2 | ConvertTo-Json -Depth 6 -Compress
    $tmpUpdate2 = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpUpdate2, $updateJson2, [System.Text.UTF8Encoding]::new($false))

    if ($DryRun) {
        Write-Host "      [dry-run] permissões atualizadas" -ForegroundColor DarkGray
    } else {
        aws dynamodb update-item --region $Region --cli-input-json "file://$tmpUpdate2" | Out-Null
        Write-Host "      permissões atualizadas" -ForegroundColor Green
    }
    Remove-Item $tmpUpdate2 -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Migração concluída." -ForegroundColor Green
if ($DryRun) {
    Write-Host "Nada foi gravado (dry-run). Rode sem -DryRun para aplicar." -ForegroundColor Yellow
}
Write-Host ""
