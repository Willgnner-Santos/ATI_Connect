# ============================================
# Delete DynamoDB Tables - Plataforma ATI
# ============================================
# Remove todas as tabelas DynamoDB de um ambiente
#
# ATENCAO: Esta acao e IRREVERSIVEL!
# Todas as tabelas e dados serao permanentemente deletados.

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("sandbox", "staging", "production")]
    [string]$Stage = "sandbox",

    [Parameter(Mandatory=$false)]
    [switch]$Force  # Pular confirmacao
)

$ErrorActionPreference = "Stop"
$Project = "plataforma-ati"
$Region = "sa-east-1"

# Lista de tabelas a deletar
$Tables = @(
    "$Project-$Stage-organizations",
    "$Project-$Stage-tenants",
    "$Project-$Stage-tenant-users",
    "$Project-$Stage-tenant-equipment",
    "$Project-$Stage-tenant-equipment-telemetry",
    "$Project-$Stage-organization-roles",
    "$Project-$Stage-audit-log"
)

Write-Host "================================================================" -ForegroundColor Red
Write-Host "                                                                " -ForegroundColor Red
Write-Host "  DELETE DYNAMODB TABLES - PLATAFORMA ATI                      " -ForegroundColor Red
Write-Host "                                                                " -ForegroundColor Red
Write-Host "================================================================" -ForegroundColor Red
Write-Host ""
Write-Host "Stage: $Stage" -ForegroundColor Yellow
Write-Host "Region: $Region" -ForegroundColor Yellow
Write-Host ""
Write-Host "Este script vai deletar as seguintes tabelas:" -ForegroundColor Yellow
foreach ($table in $Tables) {
    Write-Host "  - $table" -ForegroundColor Red
}
Write-Host ""

# ============================================
# Confirmacao
# ============================================
if (-not $Force) {
    Write-Host "ATENCAO: Esta acao NAO pode ser desfeita!" -ForegroundColor Red
    Write-Host "Todos os dados serao PERMANENTEMENTE perdidos!" -ForegroundColor Red
    Write-Host ""
    $confirmation = Read-Host "Digite 'DELETE-TABLES' (em maiusculas) para confirmar"

    if ($confirmation -ne "DELETE-TABLES") {
        Write-Host "Operacao cancelada." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host ""
Write-Host "Iniciando delecao de tabelas..." -ForegroundColor Red
Write-Host ""

$deletedCount = 0
$notFoundCount = 0
$errorCount = 0

foreach ($table in $Tables) {
    Write-Host "Deletando: $table" -ForegroundColor Cyan

    try {
        # Verificar se a tabela existe
        $tableExists = aws dynamodb describe-table `
            --table-name $table `
            --region $Region `
            2>&1

        if ($LASTEXITCODE -ne 0) {
            Write-Host "  -> Tabela nao encontrada (ja foi deletada)" -ForegroundColor Gray
            $notFoundCount++
            continue
        }

        # Deletar a tabela
        aws dynamodb delete-table `
            --table-name $table `
            --region $Region | Out-Null

        if ($LASTEXITCODE -eq 0) {
            Write-Host "  -> Tabela deletada com sucesso" -ForegroundColor Green
            $deletedCount++
        } else {
            Write-Host "  -> Erro ao deletar tabela" -ForegroundColor Red
            $errorCount++
        }

    } catch {
        Write-Host "  -> Erro: $_" -ForegroundColor Red
        $errorCount++
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "                                                                " -ForegroundColor Cyan
Write-Host "  DELECAO DE TABELAS CONCLUIDA                                 " -ForegroundColor Cyan
Write-Host "                                                                " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Estatisticas:" -ForegroundColor White
Write-Host "  -> Tabelas deletadas: $deletedCount" -ForegroundColor Green
Write-Host "  -> Tabelas nao encontradas: $notFoundCount" -ForegroundColor Gray
Write-Host "  -> Erros: $errorCount" -ForegroundColor Red
Write-Host ""

if ($deletedCount -gt 0) {
    Write-Host "Nota: As tabelas podem levar alguns segundos para serem completamente removidas." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Para recriar as tabelas, execute:" -ForegroundColor Cyan
    Write-Host "  .\deploy-full.ps1 -Stage $Stage" -ForegroundColor White
}
