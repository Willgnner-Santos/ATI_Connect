# Deploy e ambientes

## 1. Desenvolvimento local — `docker-compose.yml`

```bash
cp .env.example .env
# Configure valores reais para AWS/Bedrock, Cognito e chave interna.
docker compose config
docker compose up -d --build
docker compose ps
```

A API publica `${INGEST_PUBLIC_PORT:-9081}:8000`. As portas de banco, broker e Qdrant expostas pelo Compose local estão limitadas a `127.0.0.1`; as portas do RustFS usam `${RUSTFS_BIND_HOST:-127.0.0.1}`. As redes internas permitem comunicação entre containers. Se um navegador remoto precisar de arquivos assinados, publique o RustFS de forma controlada por HTTPS/reverse proxy e configure `S3_PUBLIC_ENDPOINT_URL` acessível externamente; não abra indiscriminadamente todas as portas. O armazenamento é RustFS. O `rustfs-init` cria o bucket no bootstrap.

**Credenciais:** `POSTGRES_USER`/`POSTGRES_PASSWORD` e `RABBITMQ_USER`/`RABBITMQ_PASSWORD` definem os usuários dos serviços no **primeiro provisionamento do volume**. O arquivo `.env` também inclui `POSTGRES_URI` e `AMQP_URL` para clientes Python executados diretamente no host; nos containers, o Compose sobrescreve as URLs para `postgres`/`rabbitmq`.

Para alterar credenciais com volumes já existentes, faça a operação pelo serviço de banco/broker e alinhe `.env`; a simples edição do Compose não recria usuários. Nunca exclua volumes contendo dados sem backup.

## 2. Imagem pré-construída — `infra/docker-compose.prod.yml`

Usa `image: ${IMAGE_URI}` e PostgreSQL/Qdrant/RabbitMQ persistentes. Requer `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `IMAGE_URI` e as demais variáveis do serviço. Aponta para **AWS S3**, não para RustFS local; reveja `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL` e `S3_USE_PATH_STYLE` de acordo com seu ambiente.

```bash
# A partir da raiz do repo, usando o arquivo de produção:
docker compose --env-file .env -f infra/docker-compose.prod.yml config
```

Revise a publicação da API na porta `80`, as credenciais do banco e a política de backups antes de aplicar. Essa stack **não** é intercambiável com a stack local sem migrar/ajustar os dados.

## 3. Portainer — `infra/docker-compose.portainer.yml`

A stack usa `build.context: ..` e uma **rede externa** `proxy_app-net`: a rede precisa existir no Docker Engine. Configure todos os segredos no Portainer e avalie a exposição das portas `6333`, `5672`, `9000` e `9001` (e a porta da API). Recomenda-se restringir serviços internos por firewall/reverse proxy, conforme sua infraestrutura.

A opção `build.context: ..` assume deploy com acesso ao repositório e resolução correta do caminho relativo; ao colar somente o YAML no editor web do Portainer, esse contexto pode não existir. Nesse cenário, adapte para imagem publicada (`image:`) ou configure deploy a partir do repositório Git com contexto válido.

## 4. Checklist de publicação

- Gere segredos únicos e mantenha `.env` fora do versionamento.
- Configure autenticação real: `COGNITO_USER_POOL_ID` e `COGNITO_CLIENT_ID` válidos.
- Aplique **somente** as [migrações pertinentes](MIGRACOES.md) ao banco atual.
- Configure HTTPS, CORS, políticas de firewall, rotacionamento e backup.
- Valide `/health`, status dos workers, integração com S3/Bedrock e a fila de ingestão.
- Após mudanças de usuário/senha, confira permissões e conexões nos volumes existentes.
