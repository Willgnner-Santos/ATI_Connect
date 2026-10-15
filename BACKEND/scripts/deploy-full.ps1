# ============================================
# Deploy Completo - Plataforma ATI SaaS
# ============================================
# Este script faz:
# 1. Build e deploy da stack SAM
# 2. Cria organizacao PLATFORM
# 3. Cria roles padrao
# 4. Cria superadmin no Cognito
# 5. Adiciona superadmin no DynamoDB

param(
    [string]$Stage = "sandbox",
    [string]$SuperadminEmail = "admin@plataforma-ati.com",
    [string]$UserPoolId,
    [Parameter(Mandatory = $true)]
    [SecureString]$SuperadminPassword
)

$SuperadminPasswordPlain = [System.Net.NetworkCredential]::new('', $SuperadminPassword).Password

$ErrorActionPreference = "Stop"

# ============================================
# Configuracoes
# ============================================
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$SuperadminName = "Super Admin"
$Project = "plataforma-ati"
$Region = "sa-east-1"

if ($Stage -notin @("sandbox", "staging", "production")) {
    Write-Host "STAGE invalido: $Stage (use: sandbox, staging ou production)" -ForegroundColor Red
    exit 1
}

if (-not $UserPoolId -and $Stage -eq "sandbox") {
    $UserPoolId = "sa-east-1_USbMV3pst"
}
if (-not $UserPoolId) {
    throw "Informe -UserPoolId para o stage '$Stage'."
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Deploy Completo - Plataforma ATI SaaS" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Stage: $Stage"
Write-Host "Region: $Region"
Write-Host "Superadmin Email: $SuperadminEmail"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ============================================
# 1. Deploy DynamoDB Infrastructure
# ============================================
Write-Host "[1/8] Deploying DynamoDB tables..." -ForegroundColor Yellow

$dynamoStackName = "$Project-infra-saas-dynamo-$Stage"

# Check if stack exists (ignore errors)
$ErrorActionPreference = "Continue"
$dynamoExists = aws cloudformation describe-stacks --stack-name $dynamoStackName --region $Region 2>&1
$stackExists = $LASTEXITCODE -eq 0
$ErrorActionPreference = "Stop"

if ($stackExists) {
    Write-Host "   -> Stack already exists, skipping..." -ForegroundColor Gray
} else {
    Write-Host "   -> Creating DynamoDB tables..." -ForegroundColor Gray
    aws cloudformation deploy `
        --template-file infrastructure/cloudformation/active/02-dynamodb-saas.yml `
        --stack-name $dynamoStackName `
        --parameter-overrides Project=$Project Stage=$Stage `
        --tags ATI=ATI project=$Project stage=$Stage `
        --region $Region

    Write-Host "   -> DynamoDB tables created!" -ForegroundColor Green
}

Write-Host ""

# ============================================
# 2. Build SAM
# ============================================
Write-Host "[2/8] Building SAM application..." -ForegroundColor Yellow
& "C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd" build --config-env $Stage
Write-Host "Build completed!" -ForegroundColor Green
Write-Host ""

# ============================================
# 3. Deploy SAM
# ============================================
Write-Host "[3/8] Deploying SAM stack..." -ForegroundColor Yellow
& "C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd" deploy --config-env $Stage
Write-Host "Stack deployed!" -ForegroundColor Green
Write-Host ""

# ============================================
# 4. Obter API Endpoint
# ============================================
Write-Host "[4/8] Getting API endpoint..." -ForegroundColor Yellow
$ApiEndpoint = aws cloudformation describe-stacks `
    --stack-name "$Project-saas-$Stage" `
    --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" `
    --output text

Write-Host "API Endpoint: $ApiEndpoint" -ForegroundColor Green
Write-Host ""

# ============================================
# 5. Salvar Config do Cognito no DynamoDB
# ============================================
Write-Host "[5/8] Saving Cognito config to DynamoDB..." -ForegroundColor Yellow

# Obter Client ID e Secret do Cognito
$clientId = aws cognito-idp list-user-pool-clients `
    --user-pool-id $UserPoolId `
    --max-results 1 `
    --query "UserPoolClients[0].ClientId" `
    --output text `
    --region $Region

if ([string]::IsNullOrEmpty($clientId) -or $clientId -eq "None") {
    Write-Host "   ERROR: Could not get Cognito Client ID" -ForegroundColor Red
    exit 1
}

Write-Host "   Client ID: $clientId" -ForegroundColor Gray

# Obter o Client Secret
$clientSecret = aws cognito-idp describe-user-pool-client `
    --user-pool-id $UserPoolId `
    --client-id $clientId `
    --query "UserPoolClient.ClientSecret" `
    --output text `
    --region $Region

if ([string]::IsNullOrEmpty($clientSecret) -or $clientSecret -eq "None") {
    Write-Host "   WARNING: Could not get Cognito Client Secret (may not be required)" -ForegroundColor Yellow
    $cognitoItem = @"
{
    "pk": {"S": "CONFIG"},
    "sk": {"S": "COGNITO#SAAS"},
    "userPoolId": {"S": "$UserPoolId"},
    "clientId": {"S": "$clientId"},
    "region": {"S": "$Region"}
}
"@
} else {
    Write-Host "   Client Secret: (stored securely)" -ForegroundColor Gray
    # Criar item do Cognito com Client Secret usando arquivo temporario (sem BOM)
    $cognitoItem = @"
{
    "pk": {"S": "CONFIG"},
    "sk": {"S": "COGNITO#SAAS"},
    "userPoolId": {"S": "$UserPoolId"},
    "clientId": {"S": "$clientId"},
    "clientSecret": {"S": "$clientSecret"},
    "region": {"S": "$Region"}
}
"@
}

$tempFile = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempFile, $cognitoItem, $utf8NoBom)

aws dynamodb put-item `
    --table-name "$Project-$Stage-tenants" `
    --item file://$tempFile | Out-Null

Remove-Item $tempFile -ErrorAction SilentlyContinue

Write-Host "Cognito config saved!" -ForegroundColor Green
Write-Host ""

# ============================================
# 6. Criar Organizacao PLATFORM
# ============================================
Write-Host "[6/8] Creating PLATFORM organization..." -ForegroundColor Yellow

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Criar organizacao usando arquivo temporario (sem BOM)
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

$tempFile = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempFile, $orgItem, $utf8NoBom)

aws dynamodb put-item `
    --table-name "$Project-$Stage-organizations" `
    --item file://$tempFile | Out-Null

Remove-Item $tempFile -ErrorAction SilentlyContinue

Write-Host "PLATFORM organization created!" -ForegroundColor Green
Write-Host ""

# ============================================
# 7. Criar Roles Padrao
# ============================================
Write-Host "[7/8] Creating default roles..." -ForegroundColor Yellow

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
                        {"S": "equipment:*"}
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
                        {"S": "equipment:*"}
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

# Salvar roles em arquivo temporario (sem BOM)
$tempFile = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempFile, $rolesJson, $utf8NoBom)

aws dynamodb batch-write-item --request-items file://$tempFile | Out-Null

Remove-Item $tempFile -ErrorAction SilentlyContinue

Write-Host "Roles created (superadmin, collaborator, admin, manager, viewer)!" -ForegroundColor Green
Write-Host ""

# ============================================
# 8. Criar Superadmin
# ============================================
Write-Host "[8/8] Creating superadmin user..." -ForegroundColor Yellow

# Verificar credenciais AWS primeiro
Write-Host "   Checking AWS credentials..." -ForegroundColor Gray
$identity = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR: AWS credentials not configured properly" -ForegroundColor Red
    Write-Host "   Run: aws configure" -ForegroundColor Yellow
    exit 1
}
Write-Host "   -> Credentials OK" -ForegroundColor Green

# Criar usuário no Cognito
Write-Host "   Creating user in Cognito (Pool: $UserPoolId)..." -ForegroundColor Gray
$ErrorActionPreference = "Continue"
$userResponseRaw = aws cognito-idp admin-create-user `
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
    --message-action SUPPRESS `
    --region $Region 2>&1
$ErrorActionPreference = "Stop"

if ($LASTEXITCODE -ne 0) {
    # Check if user already exists
    if ($userResponseRaw -match "UsernameExistsException") {
        Write-Host "   -> User already exists in Cognito, getting user ID..." -ForegroundColor Yellow
        $UserId = aws cognito-idp admin-get-user `
            --user-pool-id $UserPoolId `
            --username $SuperadminEmail `
            --query "Username" `
            --output text
    } else {
        Write-Host "   ERROR: Failed to create user in Cognito" -ForegroundColor Red
        Write-Host "   $userResponseRaw" -ForegroundColor Red
        exit 1
    }
} else {
    $userResponse = $userResponseRaw | ConvertFrom-Json
    $UserId = $userResponse.User.Username
}

Write-Host "   User ID: $UserId"

# Definir senha permanente
aws cognito-idp admin-set-user-password `
    --user-pool-id $UserPoolId `
    --username $SuperadminEmail `
    --password $SuperadminPasswordPlain `
    --permanent | Out-Null

# Adicionar no DynamoDB usando arquivo temporario (sem BOM)
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

$tempFile = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempFile, $userItem, $utf8NoBom)

aws dynamodb put-item `
    --table-name "$Project-$Stage-tenant-users" `
    --item file://$tempFile | Out-Null

Remove-Item $tempFile -ErrorAction SilentlyContinue

Write-Host "Superadmin created!" -ForegroundColor Green
Write-Host ""

# ============================================
# Resumo Final
# ============================================
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Deploy Completo Finalizado!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Informacoes:" -ForegroundColor White
Write-Host "   API Endpoint: $ApiEndpoint"
Write-Host "   Stage: $Stage"
Write-Host ""
Write-Host "Superadmin Credentials:" -ForegroundColor White
Write-Host "   Email: $SuperadminEmail"
Write-Host "   Password: configurada via SecureString"
Write-Host "   Role: superadmin"
Write-Host ""
Write-Host "Testar Login:" -ForegroundColor White
Write-Host "   Endpoint: $ApiEndpoint/saas/auth/login" -ForegroundColor Gray
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
