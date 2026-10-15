#!/usr/bin/env bash
# ============================================================
# smoke_fault_tree.sh — Smoke test E2E do módulo de Árvore de Falhas
#
# Executa o fluxo completo:
#   upload → ingestão → documento associado → gerar árvore →
#   consultar modos de falha → consultar versões → regenerar →
#   validar versão ativa/superseded
#
# Variáveis de ambiente obrigatórias:
#   BASE_URL       — ex: http://localhost:8000
#   ACCESS_TOKEN   — Bearer token Cognito válido
#   EQUIPMENT_ID   — UUID do equipamento de teste
#
# Variáveis opcionais:
#   MANUAL_PATH    — caminho para o PDF de teste
#                    (padrão: tests/fixtures/manual_test_equipment.pdf)
#   POLL_INTERVAL  — segundos entre polls (padrão: 5)
#   POLL_TIMEOUT   — timeout total de cada poll em segundos (padrão: 300)
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; echo -e "${RED}FAULT TREE SMOKE TEST: FAIL${NC}"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

: "${BASE_URL:?Variável BASE_URL não definida}"
: "${ACCESS_TOKEN:?Variável ACCESS_TOKEN não definida}"
: "${EQUIPMENT_ID:?Variável EQUIPMENT_ID não definida}"
MANUAL_PATH="${MANUAL_PATH:-tests/fixtures/manual_test_equipment.pdf}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT:-300}"

_get() {
    curl -sf \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        "$@"
}

_post_json() {
    local url="$1"; shift
    curl -sf \
        -X POST \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$1" \
        "${url}"
}

_poll() {
    local url="$1"
    local done_values="$2"   # pipe-separated list of terminal values
    local field="${3:-status}"
    local elapsed=0
    while [ "$elapsed" -lt "$POLL_TIMEOUT" ]; do
        local val
        val=$(_get "${url}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('${field}',''))" 2>/dev/null || echo "")
        for terminal in $(echo "$done_values" | tr '|' ' '); do
            if [ "$val" = "$terminal" ]; then
                echo "$val"
                return 0
            fi
        done
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
    done
    echo "TIMEOUT"
    return 1
}

echo "======================================================"
echo " FAULT TREE SMOKE TEST"
echo " Equipment: ${EQUIPMENT_ID}"
echo " Base URL:  ${BASE_URL}"
echo "======================================================"

# ── Passo 1: Upload do manual ─────────────────────────────────────────────────
info "Passo 1: Enviando PDF de teste..."
if [ ! -f "$MANUAL_PATH" ]; then
    fail "Arquivo de teste não encontrado: ${MANUAL_PATH}"
fi

UPLOAD_RESP=$(curl -sf \
    -X POST \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -F "file=@${MANUAL_PATH};type=application/pdf" \
    -F "target_type=equipment" \
    -F "target_id=${EQUIPMENT_ID}" \
    "${BASE_URL}/ingest/file")

JOB_ID=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")
[ -z "$JOB_ID" ] && fail "Upload não retornou job_id. Resposta: ${UPLOAD_RESP}"
pass "Upload aceito. job_id=${JOB_ID}"

# ── Passo 2: Aguardar ingestão completar ──────────────────────────────────────
info "Passo 2: Aguardando ingestão (job_id=${JOB_ID})..."
INGEST_STATUS=$(_poll "${BASE_URL}/ingest/status/${JOB_ID}" "completed|partial|failed" "status")
[ "$INGEST_STATUS" = "TIMEOUT" ] && fail "Timeout aguardando ingestão."
[ "$INGEST_STATUS" = "failed" ] && fail "Ingestão falhou. Verifique GET /ingest/status/${JOB_ID}"
pass "Ingestão concluída com status=${INGEST_STATUS}"

# ── Passo 3: Extrair document_id do job ──────────────────────────────────────
info "Passo 3: Extraindo document_id do job..."
JOB_DETAIL=$(_get "${BASE_URL}/ingest/status/${JOB_ID}")
DOCUMENT_ID=$(echo "$JOB_DETAIL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('document_id') or d.get('bronze_id',''))" 2>/dev/null || echo "")
[ -z "$DOCUMENT_ID" ] && fail "document_id não encontrado no job completado."
pass "document_id=${DOCUMENT_ID}"

# ── Passo 4: Validar documento associado ao equipamento ──────────────────────
info "Passo 4: Validando documento vinculado ao equipamento..."
DOCS_RESP=$(_get "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/documents")
DOC_COUNT=$(echo "$DOCS_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
[ "$DOC_COUNT" -lt 1 ] && fail "Nenhum documento vinculado ao equipamento após ingestão."
DOC_STATUS=$(echo "$DOCS_RESP" | python3 -c "
import sys,json
docs=json.load(sys.stdin)
target = [d for d in docs if d.get('document_id')=='${DOCUMENT_ID}']
print(target[0].get('status','unknown') if target else 'not_found')
" 2>/dev/null || echo "unknown")
[ "$DOC_STATUS" = "not_found" ] && fail "document_id=${DOCUMENT_ID} não encontrado nos documentos vinculados."
pass "Documento associado. status=${DOC_STATUS} (${DOC_COUNT} doc(s) total)"

# ── Passo 5: Iniciar geração ──────────────────────────────────────────────────
info "Passo 5: Iniciando geração da árvore de falhas..."
GEN_RESP=$(_post_json "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/generate" \
    "{\"document_id\": \"${DOCUMENT_ID}\"}")
GEN_ID=$(echo "$GEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('generation_id',''))" 2>/dev/null || echo "")
[ -z "$GEN_ID" ] && fail "Geração não retornou generation_id. Resposta: ${GEN_RESP}"
pass "Geração iniciada. generation_id=${GEN_ID}"

# ── Passo 6: Aguardar geração completar ──────────────────────────────────────
info "Passo 6: Aguardando geração (geração em background)..."
GEN_STATUS=$(_poll \
    "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/generations/${GEN_ID}" \
    "active|failed|empty" "status")
[ "$GEN_STATUS" = "TIMEOUT" ] && fail "Timeout aguardando geração."
[ "$GEN_STATUS" = "failed" ] && {
    VERSION_DETAIL=$(_get "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/generations/${GEN_ID}" 2>/dev/null || echo "{}")
    ERR=$(echo "$VERSION_DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null || echo "")
    fail "Geração falhou. error=${ERR}"
}
pass "Geração concluída com status=${GEN_STATUS}"

# ── Passo 7: Consultar modos de falha ────────────────────────────────────────
info "Passo 7: Consultando modos de falha..."
FM_RESP=$(_get "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/failure-modes")
FM_COUNT=$(echo "$FM_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
[ "$FM_COUNT" -lt 1 ] && fail "Nenhum modo de falha retornado."
pass "${FM_COUNT} modo(s) de falha encontrado(s)"

# ── Passo 8: Consultar versões ────────────────────────────────────────────────
info "Passo 8: Consultando versões..."
VERSIONS_RESP=$(_get "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/versions")
ACTIVE_COUNT=$(echo "$VERSIONS_RESP" | python3 -c "
import sys,json
v=json.load(sys.stdin)
print(sum(1 for x in v if x.get('status')=='active'))
" 2>/dev/null || echo "0")
[ "$ACTIVE_COUNT" -ne 1 ] && fail "Esperado exatamente 1 versão active, encontrado ${ACTIVE_COUNT}."
pass "Versões OK — 1 versão active"

# ── Passo 9: Regenerar ────────────────────────────────────────────────────────
info "Passo 9: Iniciando regeneração..."
REGEN_RESP=$(_post_json "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/generate" \
    "{\"document_id\": \"${DOCUMENT_ID}\"}")
REGEN_ID=$(echo "$REGEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('generation_id',''))" 2>/dev/null || echo "")
[ -z "$REGEN_ID" ] && fail "Regeneração não retornou generation_id."
[ "$REGEN_ID" = "$GEN_ID" ] && fail "generation_id da regeneração é igual ao da primeira geração."
pass "Regeneração iniciada. generation_id=${REGEN_ID}"

REGEN_STATUS=$(_poll \
    "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/generations/${REGEN_ID}" \
    "active|failed|empty" "status")
[ "$REGEN_STATUS" = "TIMEOUT" ] && fail "Timeout aguardando regeneração."
[ "$REGEN_STATUS" = "failed" ] && fail "Regeneração falhou."
pass "Regeneração concluída com status=${REGEN_STATUS}"

# ── Passo 10: Validar estado final das versões ───────────────────────────────
info "Passo 10: Validando estado final das versões..."
FINAL_VERSIONS=$(_get "${BASE_URL}/fault-trees/equipment/${EQUIPMENT_ID}/versions")
FINAL_ACTIVE=$(echo "$FINAL_VERSIONS" | python3 -c "
import sys,json
v=json.load(sys.stdin)
print(sum(1 for x in v if x.get('status')=='active'))
" 2>/dev/null || echo "0")
FINAL_SUPERSEDED=$(echo "$FINAL_VERSIONS" | python3 -c "
import sys,json
v=json.load(sys.stdin)
print(sum(1 for x in v if x.get('status')=='superseded'))
" 2>/dev/null || echo "0")

[ "$FINAL_ACTIVE" -ne 1 ] && fail "Esperado 1 versão active após regeneração, encontrado ${FINAL_ACTIVE}."
[ "$FINAL_SUPERSEDED" -lt 1 ] && fail "Esperado pelo menos 1 versão superseded após regeneração, encontrado ${FINAL_SUPERSEDED}."

pass "Estado final: ${FINAL_ACTIVE} active, ${FINAL_SUPERSEDED} superseded"

echo ""
echo "======================================================"
echo -e "${GREEN}FAULT TREE SMOKE TEST: PASS${NC}"
echo "======================================================"
