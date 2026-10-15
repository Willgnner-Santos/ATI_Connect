# ============================================
# Update Lambdas - Plataforma ATI SaaS
# ============================================
# Atualiza o código de uma ou todas as Lambdas
#
# Uso:
#   .\update-lambdas.ps1 sandbox                        # Atualiza TODAS as lambdas
#   .\update-lambdas.ps1 sandbox -Lambda auth/login     # Atualiza apenas auth/login
#   .\update-lambdas.ps1 sandbox -Lambda organization/get

param(
    [ValidateSet("sandbox", "staging", "production")]
    [string]$Stage = "sandbox",
    [string]$Lambda = ""  # Opcional: caminho específico (ex: auth/login)
)

$ErrorActionPreference = "Stop"

# ============================================
# Configurações
# ============================================
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Project = "plataforma-ati"
$Region = "sa-east-1"
$S3Bucket = "$Project-serverless-deploys-$Region"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "🔄 Update Lambdas - Plataforma ATI SaaS" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Stage: $Stage"
Write-Host "Lambda: $(if ($Lambda) { $Lambda } else { 'ALL' })"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ============================================
# Função para atualizar uma Lambda
# ============================================
function Update-Lambda {
    param(
        [string]$LambdaPath
    )

    # Source directory names are not always the deployed FunctionName suffix.
    $functionNameOverrides = @{
        "push-subscriptions/register" = "push-subscription-register"
        "push-subscriptions/delete" = "push-subscription-delete"
    }
    $lambdaName = if ($functionNameOverrides.ContainsKey($LambdaPath)) {
        $functionNameOverrides[$LambdaPath]
    } else {
        $LambdaPath -replace '/', '-'
    }
    $functionName = "$Project-saas-$lambdaName-$Stage"

    Write-Host "📦 Updating: $LambdaPath" -ForegroundColor Yellow
    Write-Host "   Function: $functionName"

    $lambdaDir = "02_lambdas\saas\$LambdaPath"

    if (-not (Test-Path "$lambdaDir\handler.py")) {
        Write-Host "   ⚠️  handler.py not found, skipping..." -ForegroundColor Yellow
        return $false
    }

    # Criar ZIP
    $zipFile = "$env:TEMP\$lambdaName-$Stage.zip"

    Push-Location $lambdaDir
    Compress-Archive -Path * -DestinationPath $zipFile -Force
    Pop-Location

    # Upload para S3
    # --region explícito: sem ele a AWS CLI cai no AWS_REGION/AWS_DEFAULT_REGION do
    # shell, e o deploy inteiro pode ir para a região errada.
    aws s3 cp $zipFile "s3://$S3Bucket/lambdas/saas/$lambdaName.zip" --region $Region --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   ❌ Falha no upload para o S3 (aws exit $LASTEXITCODE)" -ForegroundColor Red
        return $false
    }

    # Atualizar Lambda
    # $ErrorActionPreference = 'Stop' não vale para executável nativo: sem checar
    # $LASTEXITCODE o script imprimia "atualizada com sucesso" sobre um erro da
    # AWS CLI, e um deploy que nunca aconteceu passava por feito.
    $result = aws lambda update-function-code `
        --function-name $functionName `
        --s3-bucket $S3Bucket `
        --s3-key "lambdas/saas/$lambdaName.zip" `
        --region $Region `
        --query "LastModified" `
        --output text
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   ❌ Falha ao atualizar o código da Lambda (aws exit $LASTEXITCODE)" -ForegroundColor Red
        return $false
    }

    Write-Host "   ✅ Updated at: $result" -ForegroundColor Green
    return $true
}

# ============================================
# Atualizar Lambda(s)
# ============================================

if ($Lambda) {
    # Atualizar apenas uma Lambda específica
    Write-Host "🎯 Atualizando Lambda específica..." -ForegroundColor White
    Write-Host ""

    if (Update-Lambda -LambdaPath $Lambda) {
        Write-Host ""
        Write-Host "✅ Lambda atualizada com sucesso!" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "❌ Falha ao atualizar Lambda!" -ForegroundColor Red
        exit 1
    }
} else {
    # Atualizar todas as Lambdas
    Write-Host "🎯 Atualizando TODAS as Lambdas..." -ForegroundColor White
    Write-Host ""

    $lambdas = @(
        "auth/login",
        "auth/refresh",
        "auth/logout",
        "auth/forgot-password",
        "auth/reset-password",
        "organization/get",
        "organization/new",
        "organization/delete",
        "organization/reactivate",
        "tenant/get",
        "tenant/new",
        "tenant/delete",
        "tenant/reactivate",
        "user/get",
        "user/new",
        "user/update",
        "user/delete",
        "user/reactivate",
        "equipment/get",
        "equipment/new",
        "equipment/view",
        "equipment/update",
        "equipment/delete",
        "equipment/action/get",
        "equipment/action/send",
        "catalog/sensor-type/get",
        "catalog/sensor-type/new",
        "catalog/sensor-type/delete",
        "catalog/equipment-model/get",
        "catalog/equipment-model/new",
        "catalog/equipment-model/update",
        "catalog/equipment-model/delete",
        "dashboard/get",
        "report/get",
        "telemetry/router",
        "push-subscriptions/register",
        "push-subscriptions/delete",
        "virtual-technician/list",
        "virtual-technician/create",
        "virtual-technician/view",
        "virtual-technician/update",
        "virtual-technician/delete",
        "virtual-technician/validator",
        "virtual-technician/executor",
        "virtual-technician/notifier"
    )

    $updated = 0
    $failed = 0

    foreach ($lambdaPath in $lambdas) {
        if (Update-Lambda -LambdaPath $lambdaPath) {
            $updated++
        } else {
            $failed++
        }
        Write-Host ""
    }

    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "✅ Atualização Completa!" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "   ✅ Atualizadas: $updated" -ForegroundColor Green
    if ($failed -gt 0) {
        Write-Host "   ⚠️  Falhas: $failed" -ForegroundColor Yellow
    }
    Write-Host "============================================" -ForegroundColor Cyan

    # Sai != 0 quando alguma Lambda não subiu, para não passar por deploy completo
    # em CI nem no olho de quem rodou.
    if ($failed -gt 0) {
        exit 1
    }
}
