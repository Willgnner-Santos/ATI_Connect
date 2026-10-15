# ATI · Árvore de Falhas

Serviço Python/FastAPI para **ingestão de manuais técnicos em PDF**, **busca semântica (RAG)**, **geração/versionamento de árvores de falhas** e **chat contextualizado por equipamento**. Integra-se ao backend ATI, usa autenticação Cognito e mantém escopo por organização e tenant.

> **Escopo:** este repositório contém a API, o worker e as definições de infraestrutura. O backend de cadastro de equipamentos e o frontend ATI são sistemas separados.

## Stack e arquitetura

| Componente | Responsabilidade |
| --- | --- |
| **FastAPI** (`src/api.py`) | HTTP, uploads, consultas, pesquisa e rotas de árvores/chat |
| **Worker** (`src/worker.py`) | Consumo de mensagens RabbitMQ e processamento assíncrono |
| **PostgreSQL** (`src/storage/postgres_client.py`) | Metadados, vínculos, versões, jobs, outbox e histórico |
| **Qdrant** | Vetores e recuperação semântica dos documentos |
| **RustFS** (local) ou **S3** (produção) | Originais e imagens extraídas |
| **AWS Bedrock** | Embeddings e extração/geração com LLM |
| **Cognito** | Validação local de `id_token` via JWKS e RBAC |

O pipeline passa por **Bronze** (armazenamento e extração do PDF) e **Silver** (classificação, fragmentação, envelopes e indexação). As árvores de modelo pertencem à organização (`tenant_id = NULL`); as árvores de equipamento usam `org_id + tenant_id`. Gerações criam versões candidatas e só promovem resultados concluídos; a versão anterior permanece durante o processamento.

## Início rápido — Docker Compose local

Pré-requisitos: Docker Engine + Compose v2, acesso ao AWS Bedrock e configurações reais de Cognito. A imagem utiliza **Python 3.12**.

```bash
cp .env.example .env
# Edite .env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID e INTERNAL_API_KEY.
# Defina senhas diferentes das demonstrativas antes de compartilhar a stack.

docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:9081/health
```

- API: `http://localhost:9081` (`INGEST_PUBLIC_PORT` altera a porta externa).
- Swagger interativo: `http://localhost:9081/docs`.
- PostgreSQL: `localhost:5432`; Qdrant: `localhost:6333`; RabbitMQ: `localhost:5672` e console `localhost:15672`; RustFS: `localhost:9000` e console `localhost:9001`.
- Os serviços de infraestrutura são publicados apenas no **loopback** no Compose local (`RUSTFS_BIND_HOST` permite configurar o bind do RustFS separadamente). A API continua acessível pela porta configurada. Dentro dos containers, a aplicação usa nomes de serviço como `postgres`, `rabbitmq` e `rustfs`, independentemente dos endereços configurados para acesso pelo host.
- O bucket do RustFS é criado por `scripts/init_s3_bucket.py` durante a inicialização.

**Atenção a volumes existentes:** alterar `POSTGRES_USER`, `POSTGRES_PASSWORD`, `RABBITMQ_USER` ou `RABBITMQ_PASSWORD` no `.env` não altera automaticamente usuários já persistidos nos volumes. Ajuste as contas no serviço ou planeje uma migração. **Não** use `docker compose down -v` em ambientes com dados a preservar: esse comando os apaga.

Os exemplos de credenciais em `.env.example` **não são senhas de produção**. Sem AWS/Cognito configurados de verdade, a API até pode inicializar, mas os fluxos autenticados e de IA não funcionarão.

## Configuração

Copie e ajuste `.env.example` (o arquivo `.env` está ignorado pelo Git). Grupos principais:

| Configuração | Uso |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BEDROCK_REGION` | Acesso ao Bedrock e, se aplicável, S3 |
| `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_REGION` | JWT/JWKS do Cognito; client ID é obrigatório |
| `POSTGRES_*`, `POSTGRES_URI` | Banco e credenciais do Compose ou da execução local |
| `RABBITMQ_*`, `AMQP_URL` | Broker e autenticação da aplicação |
| `S3_*`, `RUSTFS_*` | Armazenamento de objetos local/S3 e URLs assinadas |
| `CORS_ALLOWED_ORIGINS` | Origens permitidas do frontend, separadas por vírgula |
| `INTERNAL_API_KEY` | Autenticação service-to-service com o backend ATI |
| `DYNAMODB_TABLE_ROLES` | Resolução das roles de organização, se disponível |
| `MAX_UPLOAD_BYTES`, `STALE_JOB_MINUTES` | Limite de upload e recuperação de jobs |

Para AWS S3, deixe `S3_ENDPOINT_URL` sem valor e configure credenciais/bucket adequados. O Compose **local** sobrescreve o endpoint com `http://rustfs:9000`; veja [Deploy e ambientes](docs/DEPLOY.md) para os outros cenários.

## API e autenticação

O cliente envia o **`id_token` do Cognito**, não o `access_token`:

```bash
# Obtenha o ID token pelo fluxo de login do seu ambiente; não salve credenciais no repo.
export ATI_ID_TOKEN='cole-aqui-um-token-temporario'
curl -fsS 'http://localhost:9081/envelopes' \
  -H "Authorization: Bearer ${ATI_ID_TOKEN}"
```

| Grupo | Endpoints principais |
| --- | --- |
| Ingestão | `POST /ingest/file`, `GET /ingest/status/{job_id}` |
| Documentos | `GET /envelopes`, `GET /envelopes/{envelope_id}`, `GET /bronze/{bronze_id}/images`, `POST /bronze/{bronze_id}/reprocess` |
| Busca | `POST /search` |
| Modelo | `POST /fault-trees/model`, `GET/PUT /fault-trees/model/{model_id}`, `POST /fault-trees/model/{model_id}/generate`, `GET /fault-trees/model/{model_id}/failure-modes`, `GET /fault-trees/model/{model_id}/documents` |
| Equipamento | `GET/PUT/DELETE /fault-trees/equipment/{equipment_id}`, `POST /fault-trees/equipment/{equipment_id}/generate`, `GET .../versions`, `GET .../generations/{generation_id}`, `GET .../failure-modes`, `GET .../documents`, `GET .../images` |
| Herança e integração | `POST /fault-trees/equipment/{equipment_id}/inherit/{model_id}`, `POST /fault-trees/model/{model_id}/copy` (`X-Internal-Key`) |
| Chat | `POST /chat`, `GET /chat/history`, `POST /chat/clear`, `GET /chat/documents/{document_id}/download` |
| Diagnóstico | `GET /health`, `GET /status` |

O upload aceita **PDF** via `multipart/form-data`, campo `file`. Para associação, informe `target_type=model|equipment` e `target_id`. Para equipamentos, o `target_id` deve ser UUID; `model_id` pode ser enviado como vínculo de origem. Retorna `202` e `job_id`: consulte o status antes de usar o documento.

```bash
curl -X POST http://localhost:9081/ingest/file \
  -H "Authorization: Bearer ${ATI_ID_TOKEN}" \
  -F 'file=@/caminho/manual.pdf' \
  -F 'target_type=equipment' \
  -F 'target_id=00000000-0000-4000-8000-000000000001' \
  -F 'generate_fault_tree=false'
```

A especificação **OpenAPI é gerada pelo FastAPI**. Fonte confiável: `/openapi.json` em uma instância ativa. Para exportação offline, em um ambiente Python com dependências instaladas, execute `python scripts/export_openapi.py`. Não se mantém uma cópia estática no Git para evitar contrato desatualizado.

## Desenvolvimento e testes

Use Python **3.12** para corresponder ao Docker e ao lockfile. São **duas instalações separadas**: o runtime com hashes e as ferramentas de teste.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

No Windows, use `.venv\Scripts\activate` para ativar o ambiente. Os testes unitários preenchem variáveis fictícias em `tests/conftest.py`; eles não exigem AWS real. Os testes de integração **só** devem ser ativados contra serviços de teste:

```bash
RUN_S3_INTEGRATION=1 python -m pytest tests/test_s3_integration.py
RUN_POSTGRES_INTEGRATION=1 python -m pytest tests/test_fault_tree_postgres_integration.py
```

O smoke E2E precisa de serviço real e permissões válidas. Para criar o manual sintético (não versionado) e executar:

```bash
python tests/fixtures/create_test_pdf.py
BASE_URL=http://localhost:9081 ACCESS_TOKEN="$ATI_ID_TOKEN" \
EQUIPMENT_ID='<uuid-de-equipamento-de-teste>' bash scripts/smoke_fault_tree.sh
```

Alterações em dependências devem começar em `requirements.in`; regenere `requirements.lock` **em Linux/Python 3.12** com `pip-compile --generate-hashes` e rode a suíte. `requirements.txt` existe somente para compatibilidade e aponta para o lock. A pipeline em `.github/workflows/ci.yml` executa os testes em Python 3.12.

## Atualizações e deploy

- Bancos existentes podem exigir **migrações explícitas**: leia [Migrações SQL](docs/MIGRACOES.md) antes de atualizar. O `create_all` no startup não substitui `ALTER TABLE` em bancos antigos.
- Para configurações de Docker local, imagem pré-construída e Portainer, consulte [Deploy e ambientes](docs/DEPLOY.md).
- Relatórios de revisão e roteiro de correções anteriores estão em [`docs/history/`](docs/history/README.md). São registros históricos, não a descrição do estado atual.
- Resumo de alterações desta limpeza: [LIMPEZA_REALIZADA.md](LIMPEZA_REALIZADA.md).

## Organização do repositório

```text
src/       API, worker, autenticação, ingestão, busca, chat e árvores de falhas
scripts/   migrações SQL, bootstrap S3, smoke E2E, exportação OpenAPI
tests/     regressão, testes unitários e integrações opt-in
infra/     Compose para produção e Portainer
docs/      operação/migrações e histórico de revisões
.github/   integração contínua
```

**Segurança:** não registre senhas, tokens, endpoints de login com credenciais ou dumps de dados no repositório. Tokens de teste devem ser obtidos pelo fluxo de autenticação oficial do respectivo ambiente. Para revogação/rotação de credenciais expostas anteriormente, consulte a seção de riscos em `LIMPEZA_REALIZADA.md`.
