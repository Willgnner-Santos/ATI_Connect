# Roadmap de Correções — Árvore de Falhas (v2 Revisada)

## Objetivo

Este roadmap organiza as correções identificadas no code review do repositório **Árvore de Falhas**, priorizando segurança, isolamento multi-tenant, integridade de processamento, durabilidade operacional e confiabilidade do RAG.

A versão revisada incorpora ajustes de prioridade, gates explícitos entre fases, redução do escopo da Fase 0 e tratamento específico para migração de dados associada ao R13.

---

# Princípios do Roadmap

1. Corrigir primeiro riscos de segurança e isolamento.
2. Não avançar de fase sem cumprir critérios objetivos de saída.
3. Criar testes de regressão junto das correções.
4. Não considerar sucesso quando houver falha parcial de persistência, indexação ou geração.
5. Não alterar constraints ou schema sem diagnóstico dos dados existentes e estratégia de rollback.
6. Só iniciar refatorações maiores depois que os invariantes críticos estiverem protegidos por testes.

---

# Visão Geral

| Fase | Objetivo | Itens principais | Prioridade |
|---|---|---|---|
| **0. Baseline mínimo de testes** | Tornar o projeto testável e criar proteção contra regressões críticas | R23 + testes de R01/R02 | P0 |
| **1. Segurança e isolamento multi-tenant** | Eliminar acesso, escrita e ingestão insegura | R01, R02, R08, R11, R16 | P0/P1 |
| **2. Hardening de autenticação, dados e processamento** | Completar segurança e impedir promoção/persistência incorreta | R12, R13, R22, R04, R05 | P0/P1 |
| **3. Mensageria e durabilidade** | Impedir perda de eventos e jobs | R06, R07, R09, R10, R20, R27 | P1 |
| **4. RAG, evidência e qualidade técnica** | Garantir uso correto de documentos e evidências | R03, R14, R15, R17, R18, R19, R21 | P1 |
| **5. Infraestrutura e deploy** | Tornar ambientes reproduzíveis e observáveis | R24, R25, R26 | P1/P2 |
| **6. Refatoração e hardening contínuo** | Reduzir complexidade e custo de manutenção | `generator.py`, contratos de erro, observabilidade | P2 |

---

# Fase 0 — Baseline Mínimo de Testes

**Prazo sugerido:** 3–5 dias

A Fase 0 deve ser pequena e objetiva. O objetivo não é construir toda a estratégia de testes do sistema, mas garantir que as correções críticas possam ser implementadas com proteção contra regressão.

## Escopo

- Corrigir erros de collection da suíte.
- Criar configuração mínima de testes.
- Remover dependência desnecessária de serviços reais na coleta dos testes.
- Criar fixtures básicas.
- Configurar execução automatizada no CI.
- Criar testes de regressão para:
  - isolamento entre tenants;
  - escrita sem permissão;
  - bugs R01 e R02.

## Fora do escopo da Fase 0

Os itens abaixo devem ser testados nas respectivas fases:

```text
concorrência
RabbitMQ
outbox
jobs duráveis
processamento distribuído
```

## Critério de saída — Gate 0

### GO

```text
pytest coleta toda a suíte sem erro
CI executa a suíte automaticamente
configuração de teste não depende de serviços reais para collection
existe teste negativo para acesso cross-tenant
existe teste negativo para escrita sem permissão
R01 e R02 possuem testes de regressão automatizados
```

### NO-GO

```text
erros de collection persistem
testes críticos dependem de infraestrutura externa para executar
não existe cobertura mínima de autorização e isolamento
```

A Fase 1 não deve começar sem o Gate 0 aprovado.

---

# Fase 1 — Segurança e Isolamento Multi-Tenant

**Prazo sugerido:** 1–2 semanas

Esta fase contém os itens de maior risco de segurança e deve ser tratada como bloqueadora para produção multi-tenant.

---

## 1.1 Isolamento de Tenant — R01

Tornar `tenant_id` obrigatório em consultas normais:

```python
get_fault_tree_by_equipment(
    equipment_id,
    org_id,
    tenant_id
)
```

Validar explicitamente:

```text
usuário
   ↓
tenant
   ↓
equipment
   ↓
fault tree
```

Não confiar apenas no `equipment_id` recebido pela API.

### Critério de aceitação

- Tenant A não lê árvore de Tenant B.
- Tenant A não obtém modos de falha de equipamentos de Tenant B.
- Consultas administrativas entre tenants usam método específico e autorização explícita.

---

## 1.2 Autorização de Escrita — R02

Para:

```http
DELETE /fault-trees/equipment/{equipment_id}
```

separar claramente:

```text
Autenticação de serviço
        OU
JWT + catalog:write
```

Autenticação válida não deve significar autorização automática.

### Critério de aceitação

- Usuário sem `catalog:write` recebe `403`.
- Nenhuma chamada de exclusão ocorre após falha de autorização.
- Chaves internas possuem fluxo separado.

---

## 1.3 Segurança de Ingestão Remota — R08

Mover R08 para esta fase, sem esperar os sprints posteriores.

Preferir referências a objetos autorizados:

```text
source_object_id
source_bucket
document_id
```

em vez de URLs arbitrárias.

Se HTTP continuar suportado:

- bloquear loopback;
- bloquear redes privadas;
- bloquear link-local;
- validar DNS;
- validar redirecionamentos;
- limitar tamanho durante streaming;
- limitar timeout;
- validar `Content-Length`;
- restringir egress do worker.

### Critério de aceitação

- Worker não acessa IPs locais/privados.
- Redirecionamento não permite contornar as regras.
- Tamanho máximo é aplicado durante download.
- Falhas de validação não iniciam download.

---

## 1.4 JWT/Cognito — R11

Obrigar em produção:

```env
COGNITO_CLIENT_ID
```

A validação deve checar:

```text
issuer
signature
expiration
aud/client_id
token_use
```

### Critério de aceitação

- Token emitido para outro app client é rejeitado.

---

## 1.5 Crash Determinístico — R16

R16 deve ser tratado como **P1**, não P2.

O `progress_callback` indefinido gera `NameError` determinístico quando o fluxo é alcançado.

### Ação

- Corrigir referência.
- Criar teste de regressão para a função.
- Validar se existe algum fluxo de produção que chama diretamente esse caminho.

### Critério de aceitação

- Fluxo executa sem `NameError`.
- Teste cobre execução normal da função.

---

# Gate 1 — Segurança Base

A Fase 2 só começa se os testes abaixo estiverem verdes.

### GO

```text
todos os testes de isolamento passam
nenhum endpoint crítico permite cross-tenant
operações de escrita exigem permissão
JWT de outro client é rejeitado
SSRF mitigado
R16 corrigido
```

### NO-GO

```text
qualquer regressão de autorização
qualquer consulta cross-tenant conhecida
ingestão ainda aceita destinos arbitrários sem proteção
```

---

# Fase 2 — Hardening de Autenticação, Dados e Integridade

**Prazo sugerido:** 1–2 semanas

---

## 2.1 Rotação de JWKS — R12

Implementar refresh controlado ao receber `kid` desconhecido.

### Critério de aceitação

- Rotação de chave não exige restart.
- Falhas de refresh são tratadas de forma explícita.

---

## 2.2 Migração de Escopo e Constraints — R13

R13 deve ser tratado como uma mini-entrega própria.

Alterar a constraint sem diagnosticar os dados existentes pode causar erro de migração, perda de vínculo ou inconsistência silenciosa.

## R13.1 Diagnóstico

Antes da migration:

- localizar bindings duplicados;
- identificar bindings cujo tenant diverge do recurso;
- medir volume afetado;
- gerar relatório de conflitos;
- identificar registros ambíguos.

Exemplo de saída:

```text
total_bindings
bindings_validos
bindings_duplicados
bindings_cross_tenant
bindings_ambiguos
```

---

## R13.2 Estratégia de Saneamento

Para cada conflito:

- manter vínculo válido;
- reatribuir quando a origem puder ser determinada;
- duplicar vínculo quando semanticamente necessário;
- enviar casos ambíguos para revisão manual;
- registrar alterações executadas.

---

## R13.3 Migração

Ordem obrigatória:

```text
1. backup
2. validação pré-migração
3. saneamento
4. alteração do schema/constraint
5. validação pós-migração
6. teste de rollback
```

Exemplo conceitual:

```sql
UNIQUE (
    org_id,
    tenant_id,
    equipment_id,
    document_id
)
```

A definição final deve refletir o domínio real.

---

## Regra para Migrações Críticas

> Nenhuma migration destrutiva ou alteração de constraint entra em produção sem uma query de diagnóstico prévia e uma estratégia de rollback testada.

---

## 2.3 Dependências Vulneráveis — R22

Atualizar em conjunto:

```text
FastAPI
Starlette
python-multipart
```

Adicionar gerenciamento determinístico de dependências:

```text
requirements.in
requirements.lock
```

ou solução equivalente com `uv` ou Poetry.

---

## 2.4 Resultado de Geração — R04

Uma geração vazia não deve ser automaticamente promovida.

Criar contrato explícito:

```python
GenerationResult(
    status="success" | "partial" | "failed" | "empty",
    modes=[...],
    evidence_count=...,
    errors=[...]
)
```

Somente resultados elegíveis podem ser promovidos automaticamente.

### Critério de aceitação

- Falha de recuperação não substitui versão ativa.
- Resultado truncado não é promovido como completo.
- Resultado vazio legítimo é distinguido de falha técnica.

---

## 2.5 Persistência Obrigatória — R05

Toda operação crítica deve ter retorno validado:

```python
result = save_envelope(...)

if not result:
    raise PersistenceError(...)
```

Aplicar a:

```text
save_envelope
create_ingest_job
save_chunk
save_event
mark_published
```

Padronizar estados:

```text
queued
processing
partial
completed
failed
dead_letter
```

Exemplo:

```json
{
  "chunks_total": 10,
  "chunks_embedded": 8,
  "chunks_failed": 2,
  "status": "partial"
}
```

---

# Gate 2 — Integridade do Processamento

A Fase 3 só começa quando:

### GO

```text
nenhum falso "success" conhecido permanece
persistências obrigatórias são verificadas
resultado vazio não promove versão automaticamente
schema R13 migrado com sucesso
rollback de R13 testado
dependências críticas atualizadas
```

### NO-GO

```text
falhas ainda retornam None/[]/False/"" e são interpretadas como sucesso
migration R13 não possui diagnóstico ou rollback
processamentos incompletos ainda aparecem como completed
```

---

# Fase 3 — Mensageria, Outbox e Durabilidade

**Prazo sugerido:** 2–3 semanas

Esta fase concentra os testes de mensageria e concorrência que foram removidos da Fase 0.

---

## 3.1 RabbitMQ — R06 e R07

Classificar erros em:

```text
erro definitivo
erro transitório
erro inesperado
```

Fluxo recomendado:

```text
Mensagem
   ↓
validar payload
   ↓
processar
   ↓
sucesso → ACK
erro transitório → retry
erro definitivo → DLQ
```

Adicionar:

```text
retry count
exponential backoff
DLQ
event_id
idempotency key
```

Evitar `nack(requeue=True)` indefinido.

### Testes desta fase

- mensagem inválida;
- falha transitória;
- falha definitiva;
- retry limitado;
- DLQ;
- duplicação de evento;
- comportamento após restart do consumer.

---

## 3.2 Outbox — R09

A transição de domínio e o evento devem ocorrer na mesma transação:

```text
BEGIN TRANSACTION

    atualiza estado do domínio
    insere evento na outbox

COMMIT
```

Evitar commits separados.

Para concorrência:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
```

ou mecanismo equivalente de lease/reserva.

### Critério de aceitação

- Estado concluído sempre possui evento correspondente.
- Dois publishers não processam simultaneamente o mesmo registro.
- Publicação repetida é idempotente.

---

## 3.3 Concorrência de Geração — R10

Adicionar controle explícito:

```text
generation_id
generation_version
current_generation_id
```

Na promoção:

```sql
UPDATE fault_tree
SET ...
WHERE
    id = ?
    AND current_generation_id = ?
```

### Testes

- duas gerações simultâneas;
- geração antiga termina por último;
- apenas geração válida é promovida.

---

## 3.4 Jobs Duráveis — R20

Substituir tarefas críticas baseadas apenas em `BackgroundTasks`.

Fluxo:

```text
API
 ↓
persiste job
 ↓
publica comando
 ↓
worker
 ↓
heartbeat/lease
 ↓
completed / failed
```

A API retorna:

```http
202 Accepted
```

```json
{
  "job_id": "...",
  "status": "queued"
}
```

### Critério de aceitação

- Restart da API não perde job.
- Restart do worker permite retomada.
- Jobs presos são identificados sem depender de novo restart.
- Existe heartbeat ou lease.

---

## 3.5 Sequenciamento de Histórico — R27

Eliminar:

```sql
MAX(seq) + 1
```

seguido de insert concorrente em sessão separada.

Usar mecanismo atômico:

- sequence;
- contador bloqueado;
- `INSERT ... RETURNING`;
- solução equivalente.

---

# Gate 3 — Durable Processing

A Fase 4 só começa quando:

### GO

```text
mensagens sobrevivem a falhas transitórias
retry possui limite
DLQ está operacional
outbox é consistente com o domínio
jobs sobrevivem a restart
gerações concorrentes não sobrescrevem versão nova
sequenciamento não possui corrida conhecida
```

### NO-GO

```text
qualquer caminho ainda ACKa falha definitiva incorretamente
jobs desaparecem após restart
outbox pode perder evento após commit de domínio
```

---

# Fase 4 — RAG, Evidência e Qualidade Técnica

**Prazo sugerido:** 1–2 semanas

---

## 4.1 Resolução de Documentos — R03, R14 e R19

Criar uma única função central:

```python
resolve_allowed_documents(
    org_id,
    tenant_id,
    equipment_id
)
```

Retorno sugerido:

```text
document_id
storage_tenant_id
relation_type
equipment_id
```

Chat e geração devem compartilhar a mesma regra.

### Critério de aceitação

- Busca usa somente documentos autorizados.
- Documentos herdados usam o storage tenant correto.
- Regras de vínculo não ficam duplicadas.

---

## 4.2 Filtros Vetoriais

Separar:

```text
contexto de autorização
contexto de busca
contexto do documento
```

Evitar combinar identificadores semanticamente diferentes com `AND`.

---

## 4.3 Evidência e Citações — R15

Separar citação literal de interpretação.

Modelo sugerido:

```json
{
  "quote": "...",
  "source_text": "...",
  "interpretation": "...",
  "confidence": 0.91
}
```

Uma citação literal precisa corresponder a trecho verificável da fonte.

### Critério de aceitação

- Inserção de negação não passa como equivalente.
- Toda citação é rastreável até o trecho-fonte.
- Interpretação não é apresentada como texto literal.

---

## 4.4 Falhas do LLM — R17

Não converter indisponibilidade do provedor em string vazia.

Criar erro explícito:

```python
class LLMUnavailableError(Exception):
    ...
```

A API deve retornar estado controlado, por exemplo:

```http
503 Service Unavailable
```

---

## 4.5 Classificação de Saudação — R18

Eliminar matching por prefixo que classifica:

```text
bomba não liga
```

como saudação por começar com `bom`.

Usar tokenização ou regex com limites de palavra.

---

## 4.6 PDFs e Rastreabilidade — R21

Modelo sugerido:

```python
PageContent(
    page_number=...,
    text=...,
    extraction_method="text" | "vision"
)
```

Chunks devem carregar:

```text
page_start
page_end
```

ou não atravessar páginas quando isso prejudicar rastreabilidade.

---

# Gate 4 — Trustworthy RAG

A Fase 5 só começa quando:

### GO

```text
chat e geração usam resolver documental único
busca respeita escopo
documentos herdados usam tenant/storage correto
citações são verificáveis
falhas do LLM não viram respostas vazias de sucesso
origem/página de PDF é rastreável
```

### NO-GO

```text
busca ainda retorna documento não autorizado
citação ainda é validada apenas por sobreposição lexical
pipeline ainda trata erro de LLM como resposta válida
```

---

# Fase 5 — Infraestrutura e Deploy

**Prazo sugerido:** aproximadamente 1 semana

---

## 5.1 Cliente S3 — R24

Eliminar proxy que cria instâncias repetidas.

Preferir dependency injection explícita:

```python
def get_s3_client() -> S3Client:
    return app.state.s3_client
```

---

## 5.2 RabbitMQ no Docker Compose — R25

Garantir que as credenciais do broker correspondam às usadas pelos clientes:

```text
RABBITMQ_DEFAULT_USER
RABBITMQ_DEFAULT_PASS
```

Adicionar healthchecks:

```text
PostgreSQL
RabbitMQ
Qdrant
```

---

## 5.3 Migrações — R26

Adotar uma única fonte de verdade para schema.

Recomendação:

```text
Alembic
```

Fluxo:

```text
docker compose up
 ↓
migration runner
 ↓
aplicação
```

Eliminar divergência entre:

```text
bootstrap
scripts SQL
README
schema real
```

---

# Gate 5 — Production Readiness

### GO

```text
CI verde
migrations automáticas e reproduzíveis
healthchecks configurados
ambientes usam credenciais/configuração coerentes
logs estruturados disponíveis
métricas principais disponíveis
deploy validado em ambiente limpo
```

### NO-GO

```text
schema depende de passos manuais não documentados
compose não sobe com as credenciais configuradas
deploy não é reproduzível
```

---

# Fase 6 — Refatoração e Hardening Contínuo

**Prazo sugerido:** contínuo após estabilização

A refatoração deve ocorrer depois dos invariantes críticos estarem protegidos.

## Principal Candidato

```text
src/fault_tree/generator.py
```

Divisão sugerida:

```text
fault_tree/
├── generation/
│   ├── service.py
│   ├── retrieval.py
│   ├── evidence.py
│   ├── classifier.py
│   ├── alarms.py
│   ├── parser.py
│   └── models.py
```

Antes da divisão:

- criar testes de caracterização;
- preservar contratos existentes;
- medir comportamento antes/depois.

## Outros Hardening

- padronizar exceções;
- padronizar `Result` e status;
- remover singletons implícitos;
- logs estruturados;
- correlation ID;
- métricas de filas e jobs;
- tracing entre API, worker e storage;
- timeouts explícitos;
- SLOs básicos.

---

# Planejamento Revisado por Sprint

Considerando sprints de 2 semanas e uma equipe pequena:

| Sprint | Entregas |
|---|---|
| **Sprint 1** | Fase 0 / R23 + R01 + R02 + R08 + R11 + R16 |
| **Sprint 2** | R12 + R13 + migração de dados de bindings + R22 + R04 |
| **Sprint 3** | R05 + R06 + R07 + testes de mensageria + DLQ + retry + idempotência |
| **Sprint 4** | R09 + R10 + R20 + R27 + testes concorrentes |
| **Sprint 5** | R03 + R14 + R19 + resolução documental |
| **Sprint 6** | R15 + R17 + R18 + R21 |
| **Sprint 7** | R24 + R25 + R26 + observabilidade |
| **Sprint 8+** | Refatoração do gerador + melhorias contínuas |

Estimativa geral:

```text
3 a 4 meses
```

O prazo depende principalmente de:

- volume de dados históricos afetados por R13;
- complexidade da mensageria;
- cobertura real dos testes;
- quantidade de regressões encontradas durante as correções.

---

# Priorização Revisada

## P0 — Bloqueadores

```text
R01 — isolamento multi-tenant no chat
R02 — autorização de exclusão
R04 — promoção indevida de resultado vazio
R05 — persistência/indexação marcada como sucesso
R06 — perda de mensagem por ACK
R09 — outbox não atômica
R11 — audience Cognito opcional
R20 — jobs não duráveis
R22 — dependências vulneráveis
R23 — suíte de testes / collection instável
```

## P1 — Alta Prioridade

```text
R03 — escopo incorreto de documentos no chat
R07 — poison message / retry
R08 — SSRF / ingestão remota
R10 — corrida de geração
R12 — refresh de JWKS
R13 — escopo inconsistente em UPSERT
R14 — filtros vetoriais incompatíveis
R15 — validação fraca de citações
R16 — progress_callback indefinido
R17 — falha do LLM tratada como vazio
R18 — classificação incorreta de saudação
R19 — documentos herdados no tenant errado
R21 — rastreabilidade de PDFs
R27 — corrida no sequenciamento do histórico
```

## P2 — Manutenção e Infraestrutura

```text
R24 — singleton S3 incorreto
R25 — credenciais RabbitMQ inconsistentes
R26 — divergência entre migrations/bootstrap/README
```

---

# Marcos do Projeto

## Marco 1 — Security Baseline

> Nenhum usuário consegue ler ou modificar recursos fora de seu escopo.

Critérios:

- tenant isolation testado;
- autorização de escrita testada;
- audience JWT obrigatório;
- SSRF mitigado;
- crash determinístico R16 eliminado.

---

## Marco 2 — Processing Integrity

> Nenhuma operação é registrada como sucesso se armazenamento, geração ou indexação falhar.

Critérios:

- estados `partial` e `failed` padronizados;
- persistências obrigatórias verificadas;
- árvore anterior preservada em caso de falha;
- schema R13 migrado com validação.

---

## Marco 3 — Durable Processing

> Um restart da aplicação ou worker não perde jobs e não deixa operações indefinidamente presas.

Critérios:

- workers duráveis;
- retry limitado;
- DLQ;
- heartbeat/lease;
- outbox transacional;
- idempotência;
- concorrência controlada.

---

## Marco 4 — Trustworthy RAG

> Toda resposta técnica utiliza documentos autorizados e evidências rastreáveis até a fonte.

Critérios:

- resolver documental único;
- filtros corretos;
- citações verificáveis;
- origem/página preservadas.

---

## Marco 5 — Production Readiness

> Deploy reproduzível, migrations determinísticas, observabilidade e suíte de regressão estável.

Critérios:

- CI verde;
- migrations automáticas;
- healthchecks;
- logs estruturados;
- métricas principais;
- documentação atualizada.

---

# Regra de Go / No-Go

Nenhuma fase deve ser considerada concluída apenas porque o desenvolvimento terminou.

A próxima fase só começa quando o gate correspondente estiver aprovado.

Regra:

```text
código implementado
        ↓
testes verdes
        ↓
critério de saída validado
        ↓
GO
        ↓
próxima fase
```

Se qualquer critério crítico falhar:

```text
NO-GO
```

e a fase permanece aberta.

---

# Regra de Congelamento de Features

Até a conclusão ou mitigação formal dos principais bloqueadores, recomenda-se não priorizar novas funcionalidades sobre:

```text
R01
R02
R04
R05
R06
R08
R09
R10
R11
R20
R22
R23
```

Esses itens concentram os principais riscos de:

- incidente de segurança;
- vazamento entre tenants;
- perda de processamento;
- inconsistência de estado;
- comportamento silenciosamente incorreto.

---

# Resultado Esperado

Ao final deste roadmap, o projeto deve possuir:

- isolamento multi-tenant consistente;
- autorização explícita por operação;
- autenticação Cognito endurecida;
- ingestão remota protegida;
- pipeline com contratos claros de sucesso, erro e parcialidade;
- jobs duráveis;
- mensageria com retry, DLQ e idempotência;
- outbox transacional;
- concorrência de geração controlada;
- RAG restrito a documentos autorizados;
- evidências rastreáveis;
- migrations reproduzíveis;
- estratégia de rollback para alterações críticas;
- suíte de regressão funcional;
- observabilidade mínima para produção;
- base arquitetural segura para refatorações futuras.
