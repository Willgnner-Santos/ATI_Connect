# ============================================
# Update Lambda Layer - Plataforma ATI
# ============================================
# Atualiza o layer compartilhado e faz deploy

param(
    [ValidateSet("sandbox", "staging", "production")]
    [string]$Stage = "sandbox"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Project = "plataforma-ati"
$Region = "sa-east-1"
$LayerName = "$Project-layer-$Stage"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Update Lambda Layer" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Layer: $LayerName"
Write-Host "Region: $Region"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ============================================
# 1. Criar ZIP do Layer
# ============================================
Write-Host "[1/4] Creating layer ZIP..." -ForegroundColor Yellow

# Build Linux/Python 3.14 dependencies instead of packaging installed vendor files.
python scripts/generate-local-template.py
if ($LASTEXITCODE -ne 0) { throw "Failed to generate the local layer template." }

sam build PlataformaAtiLayerLocal `
    --template-file template.local.yaml `
    --config-env $Stage `
    --use-container `
    --no-cached `
    --build-dir .aws-sam/layer-build
if ($LASTEXITCODE -ne 0) { throw "Layer build failed; no version was published." }

$layerSourcePath = Join-Path $RepoRoot ".aws-sam/layer-build/PlataformaAtiLayerLocal"
$zipPath = Join-Path $RepoRoot ".aws-sam/layer.zip"
if (-not (Test-Path (Join-Path $layerSourcePath "python/plataforma_ati.py"))) {
    throw "Layer build output is missing plataforma_ati.py."
}
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Push-Location $layerSourcePath
try {
    Compress-Archive -Path "python" -DestinationPath $zipPath -Force
} finally {
    Pop-Location
}

Write-Host "Layer ZIP created: $zipPath" -ForegroundColor Green
Write-Host ""

# ============================================
# 2. Publicar Nova Versao do Layer
# ============================================
Write-Host "[2/4] Publishing new layer version..." -ForegroundColor Yellow

$layerVersion = aws lambda publish-layer-version `
    --layer-name $LayerName `
    --zip-file fileb://$zipPath `
    --compatible-runtimes python3.14 `
    --region $Region `
    --query "Version" `
    --output text

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to publish layer version" -ForegroundColor Red
    exit 1
}

Write-Host "Layer version published: $layerVersion" -ForegroundColor Green
Write-Host ""

# ============================================
# 3. Obter ARN do Layer
# ============================================
Write-Host "[3/4] Getting layer ARN..." -ForegroundColor Yellow

$layerArn = aws lambda get-layer-version `
    --layer-name $LayerName `
    --region $Region `
    --version-number $layerVersion `
    --query "LayerVersionArn" `
    --output text

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($layerArn) -or $layerArn -eq "None") {
    throw "Failed to resolve the ARN of the published layer version."
}

Write-Host "Layer ARN: $layerArn" -ForegroundColor Green
Write-Host ""

# ============================================
# 4. Atualizar Funcoes Lambda
# ============================================
Write-Host "[4/4] Updating Lambda functions..." -ForegroundColor Yellow

# Lista de funcoes SaaS
$functions = @(
    "auth-login",
    "auth-refresh",
    "auth-logout",
    "auth-forgot-password",
    "auth-reset-password",
    "organization-get",
    "organization-new",
    "organization-delete",
    "organization-reactivate",
    "tenant-get",
    "tenant-new",
    "tenant-delete",
    "tenant-reactivate",
    "user-get",
    "user-new",
    "user-update",
    "user-delete",
    "user-reactivate",
    "equipment-get",
    "equipment-view",
    "equipment-new",
    "equipment-update",
    "equipment-action-send",
    "equipment-action-get",
    "telemetry-router",
    "push-subscription-register",
    "push-subscription-delete",
    "equipment-delete",
    "dashboard-get",
    "catalog-sensor-type-get",
    "catalog-sensor-type-new",
    "catalog-sensor-type-delete",
    "catalog-equipment-model-get",
    "catalog-equipment-model-new",
    "catalog-equipment-model-delete",
    "catalog-equipment-model-update",
    "report-get",
    "virtual-technician-create",
    "virtual-technician-list",
    "virtual-technician-view",
    "virtual-technician-update",
    "virtual-technician-delete",
    "virtual-technician-validator",
    "virtual-technician-executor",
    "virtual-technician-notifier"
)

$updated = 0
$failed = 0

foreach ($func in $functions) {
    $functionName = "$Project-saas-$func-$Stage"
    Write-Host "   Updating $functionName..." -ForegroundColor Gray

    $ErrorActionPreference = "Continue"
    aws lambda update-function-configuration `
        --function-name $functionName `
        --layers $layerArn `
        --region $Region `
        --output json | Out-Null
    $ErrorActionPreference = "Stop"

    if ($LASTEXITCODE -eq 0) {
        Write-Host "   -> OK" -ForegroundColor Green
        $updated++
    } else {
        Write-Host "   -> Failed" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($failed -eq 0) {
    Write-Host "Layer updated successfully." -ForegroundColor Green
} else {
    Write-Host "Layer update incomplete; inspect failed functions." -ForegroundColor Red
}
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Layer: $LayerName"
Write-Host "Version: $layerVersion"
Write-Host "Functions updated: $updated"
Write-Host "Functions failed: $failed"
Write-Host ""

# Limpar ZIP
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

Write-Host "Persist LayerArn=$layerArn in the SAM configuration before the next deployment."
if ($failed -gt 0) { exit 1 }
