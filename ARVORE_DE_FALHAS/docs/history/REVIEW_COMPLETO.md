# Code review — Árvore de Falhas ATI

**Data:** 30 de setembro de 2026  
**Base:** snapshot enviado no arquivo `Arvore%20de%20falhas.zip`  
**SHA-256 do ZIP:** `1da09b7b821f6bb78ecdb68c8e6a44276de0891155c380a127b9aac21d3c7293`

## Parecer executivo

**Eu não aprovaria este snapshot para produção multi-tenant antes de corrigir os bloqueadores de autorização, isolamento e integridade de processamento.** O problema principal não é estilo: há caminhos que permitem leitura entre tenants da mesma organização, exclusão sem permissão de escrita e conclusão aparente de processamento que falhou.

O projeto contém fundamentos aproveitáveis: separação entre API, autenticação, ingestão, armazenamento, chat e geração; noção explícita de organização/tenant; versionamento de árvores; e uma operação de ativação transacional. A prioridade é tornar esses contratos consistentes de ponta a ponta. Uma reescrita total não é necessária para corrigir os primeiros riscos.

Foram registrados **27 achados**: **12 de gravidade alta**, **2 de gravidade alta condicionada ao ambiente/permissões**, **12 médios** e **1 baixo**. As gravidades são uma avaliação de impacto neste projeto, não pontuações CVSS próprias.

Há **18 verificações isoladas concluídas**, cobrindo **16 achados distintos**. Nessas verificações, “bug_reproduced=true” significa que o comportamento inadequado foi observado; não significa que o software passou em testes de qualidade.

## Escopo, método e limites

A revisão abrangeu estrutura e fluxos de API, Cognito/RBAC, isolamento, SQL/schema, S3, Qdrant, extração de documentos/imagens, embeddings, geração de falhas, chat, RabbitMQ/outbox, configuração, Docker, migrações e testes. O snapshot contém **51 arquivos Python em `src/`, 14.142 linhas**, e **31 funções de teste em 12 módulos**. O gerador concentra **5.803 linhas**.

Usei análise estática e rastreamento de chamadas, verificação sintática de todos os arquivos Python de `src/`, tentativa de coleta da suíte, execução de testes que puderam ser isolados sem alterar o código-fonte, e reproduções com os corpos originais de funções/classes extraídos por AST. Os colaboradores externos foram substituídos explicitamente por doubles. Dois checks de SQL usaram SQLite com as consultas originais; eles não demonstram comportamento concorrente do PostgreSQL.

**Não executei a aplicação completa nem as integrações reais.** Faltam serviços e algumas dependências no ambiente; o runtime disponível é Python 3.13.5, enquanto o Dockerfile usa 3.12. Não consegui montar um ambiente integralmente equivalente. Não medi cobertura, latência, custo de LLM, acurácia em corpus industrial, throughput ou memória sob carga. Não auditei o histórico Git. Não enviei documentos a serviços externos nem executei payloads de DoS ou acesso a metadados de nuvem. O código-fonte entregue pelo usuário não foi alterado.

As referências `arquivo:linha` dizem respeito exclusivamente a este snapshot. Os avisos de dependências foram consultados em fontes primárias em 30/09/2026; não representam uma varredura completa de todas as transitivas.

### Execução observada

| Verificação | Resultado |
|---|---|
| Compilação sintática dos 51 arquivos em `src/` | Sem erro de sintaxe |
| Coleta original da suíte | 10 erros de coleta; execução completa não realizada |
| `tests/test_scope.py`, com configuração sintética mínima | 2 testes passaram |
| Reproduções isoladas do review | 18 de 18 comportamentos inadequados reproduzidos |
| Integrações com PostgreSQL, Qdrant, RabbitMQ e AWS | Não executadas |

O log de coleta mostra tanto dependências ausentes **neste ambiente**, apesar de declaradas no requirements, quanto a falta de `COGNITO_USER_POOL_ID` na configuração de testes. Esses fatores não devem ser confundidos. A ausência de um ambiente completo não impede os achados estáticos e isolados, mas limita afirmações sobre implantação e comportamento ponta a ponta.

## Mapa priorizado dos achados

| ID | Gravidade | Achado |
|---|---|---|
| R01 | Alta | O chat consulta e devolve modos de falha sem isolar o tenant |
| R02 | Alta | DELETE de árvore não exige catalog:write no caminho JWT |
| R03 | Alta | Busca do chat não fica automaticamente limitada aos documentos do equipamento |
| R04 | Alta | Resultado vazio ou incompleto pode substituir a versão ativa |
| R05 | Alta | Falhas de persistência e indexação são apresentadas como sucesso |
| R06 | Alta | O consumidor confirma mensagens de ingestão que falharam |
| R07 | Média | JSON válido que não é objeto pode encerrar o consumidor |
| R08 | Alta condicional | Download da fila permite destinos internos e volume sem limite |
| R09 | Alta | A outbox não é transacional com o estado e não reclama registros |
| R10 | Alta | Gerações concorrentes podem promover resultado antigo sobre o novo |
| R11 | Alta condicional | A validação de audience do Cognito fica desligada por padrão |
| R12 | Média | Cache de JWKS não acompanha a rotação de chaves |
| R13 | Média | A chave única de document_bindings permite transferir um vínculo entre tenants |
| R14 | Média | Filtros incompatíveis esvaziam a recuperação direta de chunks |
| R15 | Alta | A validação de citação literal aceita texto com sentido invertido |
| R16 | Média | A reclassificação de taxonomia levanta NameError imediatamente |
| R17 | Média | Erro do provedor de chat vira resposta vazia bem-sucedida |
| R18 | Média | Heurística de saudação intercepta perguntas sobre bombas |
| R19 | Alta | Manuais herdados são autorizados pelo vínculo, mas lidos no escopo errado |
| R20 | Alta | Jobs aceitos não têm execução durável e a recuperação pode deixá-los presos |
| R21 | Média | Extração pode omitir páginas e produzir referências incorretas |
| R22 | Alta | Versões fixadas da stack de formulários têm avisos de segurança aplicáveis |
| R23 | Média | A suíte não tem configuração mínima autossuficiente |
| R24 | Baixa | O proxy S3 lazy não preserva uma instância |
| R25 | Média | Compose não aplica ao RabbitMQ as credenciais usadas pelos clientes |
| R26 | Média | Bootstrap de schema e migrações não produzem garantias equivalentes |
| R27 | Média | Sequenciamento do histórico de chat tem corrida |

## Achados detalhados

### R01 — O chat consulta e devolve modos de falha sem isolar o tenant

**Gravidade:** Alta. **Evidência:** Reproduzido em funções originais isoladas e no SQL original com SQLite.

**Referências:** `src/chat/pipeline.py:399-429`; `src/storage/postgres_client.py:987-1013`; `src/chat/router.py:31-45`.

**Problema.** run_chat_pipeline recebe tenant_id, mas chama get_fault_tree_by_equipment(equipment_id, org_id) sem esse argumento. O repositório interpreta a ausência como uma consulta a qualquer tenant da organização. A rota de chat aceita equipment_id informado pelo solicitante e não verifica previamente a existência do equipamento no tenant autenticado.

**Impacto.** Um usuário de tenant A com catalog:read e conhecimento do identificador de um equipamento de B pode obter os modos de falha de B, dentro da mesma organização. A consulta explícita aos modos cadastrados gera resposta determinística, sem depender de comportamento do modelo. Nos checks, uma marca sintética exclusiva de B apareceu na resposta executada com contexto de A.

**Correção recomendada.** Tornar tenant_id obrigatório nas operações de equipamento; passar o argumento em todas as chamadas; validar o vínculo do equipamento no escopo autenticado. Separar uma eventual consulta administrativa cross-tenant em método distinto, com autorização explícita.

**Critério de aceitação.** Criar dois tenants na mesma organização. Com token de A, consultar o equipamento de B deve resultar em 404/403 e jamais retornar dados de B. Repetir com o mesmo equipment_id nos dois tenants.

**Limite ou observação.** Não foi demonstrada exposição entre organizações: org_id permanece no filtro. O check não realizou autenticação HTTP real; exercitou o pipeline e o método SQL que a rota invoca.

### R02 — DELETE de árvore não exige catalog:write no caminho JWT

**Gravidade:** Alta. **Evidência:** Reproduzido em chamada isolada à função original da rota.

**Referências:** `src/fault_tree/router.py:264-294`; `src/auth/jwt_validator.py:69-105`.

**Problema.** A rota DELETE /fault-trees/equipment/{equipment_id} aceita chave interna ou get_current_tenant_optional. No ramo JWT, ela apenas resolve org_id/tenant_id, sem exigir a permissão de escrita usada pelas demais operações mutáveis. Os papéis padrão user, tecnico e gestor não possuem catalog:write.

**Impacto.** Um usuário somente de leitura consegue excluir a árvore do próprio tenant. Não é necessário comprometer a chave interna. O check comprovou que um contexto organization_role=user chega à chamada de exclusão.

**Correção recomendada.** Criar uma dependência de autorização composta: validar a chave interna no fluxo de serviço; no fluxo JWT, exigir catalog:write e tenant válido. Centralizar a política para evitar divergência entre verbos HTTP.

**Critério de aceitação.** Teste negativo HTTP com token de leitura retorna 403 e não chama delete_fault_tree. Testar separadamente token com escrita, ausência de token, chave inválida e chave interna válida.


### R03 — Busca do chat não fica automaticamente limitada aos documentos do equipamento

**Gravidade:** Alta. **Evidência:** Reproduzido: a busca recebe context_ids=None na requisição padrão.

**Referências:** `src/chat/pipeline.py:433-434`; `src/chat/schemas.py:6-9`; `src/fault_tree/ingest_adapter.py:74-128`.

**Problema.** context_ids é opcional e a seleção do equipamento não é convertida em uma lista de documentos autorizados. Sem context_ids, IngestAdapter.search_text aceita todos os resultados do tenant. A mensagem enviada ao modelo, porém, diz que o conteúdo corresponde ao equipamento selecionado.

**Impacto.** Respostas tecnicamente incorretas podem ser produzidas a partir do manual de outro equipamento do mesmo tenant. Mesmo depois de corrigir R01, esse erro de fundamentação permanece. Informar context_ids do lado do cliente não substitui uma política server-side.

**Correção recomendada.** Resolver document_bindings do equipamento no servidor e montar o filtro efetivo antes da busca vetorial. Contextos opcionais do cliente devem restringir, nunca ampliar, esse conjunto. Preservar a política explícita para documentos de modelo herdados.

**Critério de aceitação.** Indexar dois manuais com recomendações conflitantes em um mesmo tenant. A pergunta feita para A, sem context_ids, só pode recuperar documentos vinculados a A.

**Limite ou observação.** O problema aqui é isolamento entre equipamentos e correção da resposta; a busca vetorial já filtra o tenant.

### R04 — Resultado vazio ou incompleto pode substituir a versão ativa

**Gravidade:** Alta. **Evidência:** Reproduzido: generate_failure_modes retornando [] é promovido.

**Referências:** `src/fault_tree/service.py:107-140`; `src/storage/postgres_client.py:1233-1271`; `src/storage/qdrant_client.py:240-242`; `src/fault_tree/generator.py:5559-5564`; `src/fault_tree/generator.py:2633-2695`.

**Problema.** run_generation_candidate promove qualquer lista retornada pelo gerador. Camadas de recuperação transformam falhas em listas vazias, e o parser resiliente pode aproveitar apenas os objetos completos de uma saída incompleta. Não há um contrato de completude e validação antes da promoção.

**Impacto.** Uma indisponibilidade que se transforme em ausência de evidências, ou uma resposta truncada, pode ser registrada como geração concluída e substituir os modos visíveis por zero ou por apenas parte do resultado. A versão anterior pode permanecer no histórico, mas deixa de ser a versão ativa.

**Correção recomendada.** Diferenciar ausência legítima de modos, falha de recuperação, saída inválida, truncamento e geração completa. Propagar erros tipados; verificar stop_reason e schema; exigir critérios de promoção. Preservar a versão ativa quando a candidata não passa nesses critérios.

**Critério de aceitação.** Injetar timeout do Qdrant, embedding indisponível, JSON inválido, truncamento e []. Em todos os casos não validados, o conteúdo e a versão ativos permanecem inalterados. Criar um caminho explícito e auditável para aceitar uma árvore legitimamente vazia.

**Limite ou observação.** A ativação já usa uma transação, o que é positivo; o defeito é a validade do resultado promovido, não a ausência de commit atômico nessa função.

### R05 — Falhas de persistência e indexação são apresentadas como sucesso

**Gravidade:** Alta. **Evidência:** Reproduzido na Silver com save_envelope=False e nove embeddings falhos.

**Referências:** `src/layers/silver_layer.py:100-128`; `src/layers/silver_layer.py:186-223`; `src/envelope/repository.py:17-23`; `src/api.py:237-256`; `src/orchestrator.py:103-122`.

**Problema.** A Silver ignora o retorno booleano de save_envelope. O upload ignora create_ingest_job. Na Silver, vector_index_status é success sempre que chunks_stored > 0, mesmo com embeddings_failed > 0. chunks_generated recebe len(pages), não a quantidade de chunks; qdrant_failed não é preenchido por esse fluxo.

**Impacto.** A API pode devolver um job_id inexistente; vetores e vínculos podem referenciar envelope não persistido; uma indexação de 1 entre 10 chunks aparece como sucesso. Sem vetores, o status empty também pode virar completed no orquestrador.

**Correção recomendada.** Fazer persistências obrigatórias retornarem um resultado verificado ou lançarem exceção. Consolidar contagens reais de chunks gerados, embeddings e gravações vetoriais. Definir precisamente completed, partial, failed e empty, com propagação consistente até API/eventos.

**Critério de aceitação.** Falha ao criar o job não devolve 202 com identificador inválido. Falha no envelope interrompe ou compensa a etapa. Um de dez chunks armazenados produz partial e contagens coerentes.

**Limite ou observação.** No cenário isolado, a Silver devolveu envelope_id='missing-envelope', embeddings_failed=9 e vector_index_status='success'.

### R06 — O consumidor confirma mensagens de ingestão que falharam

**Gravidade:** Alta. **Evidência:** Reproduzidos ACK após download falho e ACK após pipeline retornando failed.

**Referências:** `src/messaging/request_consumer.py:103-140`; `src/messaging/request_consumer.py:142-182`; `src/orchestrator.py:136-151`.

**Problema.** _download_to_file converte erros de rede em False; _materialize_file converte isso em None; _handle_message confirma a mensagem. Quando o pipeline retorna um status de falha, também há ACK incondicional. Só exceções que escapam recebem NACK, com requeue imediato e sem limite. No caminho de download False, o temporário criado também fica abandonado.

**Impacto.** Uma falha transitória pode fazer o pedido desaparecer da fila sem reprocessamento. No caso de falha antes do pipeline, não se cria evento de erro por esse caminho. Em outros casos, requeue ilimitado permite um ciclo repetitivo de mensagens problemáticas. Repetições também não são deduplicadas por event_id.

**Correção recomendada.** Classificar falhas como permanentes ou transitórias. Confirmar apenas trabalho concluído ou falha definitiva duravelmente registrada. Aplicar retry com atraso, limite e dead-letter queue; usar event_id para idempotência; garantir cleanup no componente que cria o arquivo.

**Critério de aceitação.** Um HTTP 503/timeout no download não termina em ACK de sucesso. Mensagem inválida vai para descarte auditado/DLQ. A mesma event_id não repete efeitos. Nenhum caminho deixa temporários.

**Limite ou observação.** ACK significa que o consumidor declara o processamento da entrega concluído; a política deve refletir o resultado real [S6]. Não executei um broker real.

### R07 — JSON válido que não é objeto pode encerrar o consumidor

**Gravidade:** Média. **Evidência:** Reproduzido: body=[] gera TypeError que escapa de _handle_message.

**Referências:** `src/messaging/request_consumer.py:79-101`; `src/worker.py:63-104`.

**Problema.** Após json.loads, o código usa IngestRequestedEvent(**raw), mas captura apenas JSONDecodeError e ValidationError. Uma lista, número ou null causa TypeError. O loop externo trata a exceção como fatal e encerra run().

**Impacto.** Uma única mensagem inadequada pode parar o consumo. Se a task da outbox continuar, o processo ainda pode parecer ativo, sem reinício automático do serviço pelo simples fato de essa task ter terminado.

**Correção recomendada.** Validar o objeto com model_validate ou verificar isinstance(raw, dict); tratar todos os erros de entrada por mensagem. Adicionar supervisão de tasks e healthcheck que verifique o consumidor, não apenas o processo.

**Critério de aceitação.** Enviar [], null, string e objeto com campos inválidos; a mensagem seguinte válida ainda deve ser consumida.


### R08 — Download da fila permite destinos internos e volume sem limite

**Gravidade:** Alta condicional. **Evidência:** Reproduzido com transporte HTTP simulado; nenhuma conexão real foi feita.

**Referências:** `src/messaging/event_schemas.py:82-96`; `src/messaging/request_consumer.py:142-182`.

**Problema.** source_url é uma string arbitrária usada em requests.get, sem lista de origens permitidas, restrição de rede privada, revalidação de redirects ou limite de bytes transmitidos. content_base64 também é decodificado por inteiro antes de qualquer limitação local do pipeline.

**Impacto.** Quem conseguir publicar na fila de entrada pode fazer o worker acessar destinos internos e consumir disco/memória. A validação posterior de tamanho na Bronze não impede que o download já tenha ocorrido.

**Correção recomendada.** Preferir referência a objeto em armazenamento autorizado. Quando HTTP for necessário, validar esquema/origem, IP resolvido e cada redirecionamento; bloquear loopback, redes privadas e link-local conforme a política; limitar bytes durante o stream e o tamanho de entrada Base64; aplicar controle de saída na rede.

**Critério de aceitação.** Testes com transporte falso devem rejeitar destinos internos, redirects para rede privada e respostas acima do limite. Validar também permissões de publicação na fila.

**Limite ou observação.** Pré-condição importante: acesso à fila ou a algum produtor que a alimente. Não é um SSRF público demonstrado na API HTTP e não houve acesso a metadados de nuvem.

### R09 — A outbox não é transacional com o estado e não reclama registros

**Gravidade:** Alta. **Evidência:** Análise estática do fluxo de commits e publicação.

**Referências:** `src/orchestrator.py:124-132`; `src/orchestrator.py:153-204`; `src/messaging/outbox.py:57-126`; `src/messaging/outbox_worker.py:52-81`.

**Problema.** Os dados do pipeline são confirmados antes de save_event, que abre outra sessão e engole falhas retornando None. O chamador ignora esse retorno. fetch_pending apenas seleciona registros, sem claim/lease/lock; múltiplos workers podem selecionar o mesmo lote. mark_published também tem retorno ignorado.

**Impacto.** O estado pode existir sem seu evento correspondente, mesmo após ACK da requisição. Mais de um publicador pode processar o mesmo registro. Não há uma garantia exactly-once, nem isso seria obtido apenas adicionando um lock.

**Correção recomendada.** Gravar a transição de estado e seu evento na mesma transação PostgreSQL. Tratar S3/Qdrant como efeitos externos recuperáveis, com etapas idempotentes e reconciliação. Reclamar registros de outbox com lock/lease; preservar identificador estável e exigir deduplicação no consumidor.

**Critério de aceitação.** Forçar falha na persistência da outbox e comprovar que o estado final não se torna 'concluído sem evento'. Executar dois workers simultâneos, crash após publish e antes de mark_published, redelivery e recuperação de lease.

**Limite ou observação.** A semântica esperada deve ser entrega pelo menos uma vez com efeitos idempotentes. Publisher confirms e consumer ACKs resolvem responsabilidades distintas [S6].

### R10 — Gerações concorrentes podem promover resultado antigo sobre o novo

**Gravidade:** Alta. **Evidência:** Análise estática; concorrência real de PostgreSQL não foi executada.

**Referências:** `src/fault_tree/router.py:312-344`; `src/fault_tree/service.py:68-104`; `src/fault_tree/service.py:173-202`; `src/storage/postgres_client.py:1233-1267`.

**Problema.** O teste de geração em andamento ocorre sobre uma leitura anterior à criação da candidata. A criação da versão, o vínculo e o status são operações separadas. A promoção não compara uma current_generation_id nem exige que a candidata continue no estado elegível. O fluxo disparado pela ingestão também pode iniciar novas candidatas.

**Impacto.** Duas requisições podem iniciar gerações do mesmo alvo. Se a mais antiga terminar por último, ela pode sobrescrever o resultado de uma mais nova. A transação da ativação não estabelece a ordem de negócio entre trabalhos.

**Correção recomendada.** Implementar claim atômico por árvore, current_generation_id ou revisão monotônica, e compare-and-swap na promoção. Avaliar lock da árvore e índice parcial para uma versão ativa. Definir a política de substituição e cancelamento.

**Critério de aceitação.** Em PostgreSQL, iniciar duas candidatas com barreiras de sincronização e finalizar fora de ordem. A candidata obsoleta não pode substituir a atual. Repetir com uploads concorrentes e recuperação de jobs.

**Limite ou observação.** Não afirmo que duas versões ativas foram observadas em execução; isso precisa de teste concorrente e restrição de integridade apropriada.

### R11 — A validação de audience do Cognito fica desligada por padrão

**Gravidade:** Alta condicional. **Evidência:** Reproduzido com token RS256 sintético e chave local de teste.

**Referências:** `src/config/settings.py:47-50`; `src/auth/jwt_validator.py:198-229`.

**Problema.** COGNITO_CLIENT_ID é opcional e seu valor controla verify_aud. Sem ele, uma assinatura e issuer válidos bastam quanto ao destinatário do token; aud pode corresponder a outro app client do mesmo pool.

**Impacto.** Em pools compartilhados por aplicações, amplia-se indevidamente o conjunto de tokens aceitos por esta API. Não permite forjar assinatura, mas elimina uma checagem relevante de fronteira de aplicação.

**Correção recomendada.** Exigir app client ID ou allowlist explícita em configuração de produção e falhar no startup se ausente. Manter validação de assinatura, issuer, exp, token_use e contexto de autorização. A orientação do Cognito exige correspondência do audience [S5].

**Critério de aceitação.** Token assinado com issuer esperado, mas audience diferente, retorna 401. Token do cliente permitido passa. Configuração produtiva sem audience não inicializa.

**Limite ou observação.** No check, o token de cliente distinto foi aceito com None e rejeitado após configurar o cliente esperado. Não usei credenciais/token reais.

### R12 — Cache de JWKS não acompanha a rotação de chaves

**Gravidade:** Média. **Evidência:** Reproduzido: kid novo retorna 401 sem nova busca.

**Referências:** `src/auth/jwt_validator.py:31-61`.

**Problema.** _fetch_jwks usa lru_cache(maxsize=1) pelo tempo de vida do processo. _get_public_key não invalida nem atualiza o cache quando aparece uma chave desconhecida.

**Impacto.** Tokens legítimos assinados com chave nova podem ser rejeitados até reiniciar os processos, embora os serviços e credenciais estejam corretos.

**Correção recomendada.** Cache com prazo e refresh controlado em kid desconhecido; proteção contra tempestade de atualização; timeout e observabilidade. A documentação do Cognito recomenda atualização periódica e quando a chave recebida difere do cache [S5].

**Critério de aceitação.** Servir JWKS antiga e depois nova; um token com novo kid deve ser validado após uma atualização limitada, sem restart. Muitos kids inválidos não podem gerar consultas ilimitadas.


### R13 — A chave única de document_bindings permite transferir um vínculo entre tenants

**Gravidade:** Média. **Evidência:** Reproduzido usando o INSERT/UPSERT original em SQLite.

**Referências:** `src/storage/postgres_client.py:255-269`; `src/storage/postgres_client.py:799-842`; `src/storage/postgres_client.py:907-925`.

**Problema.** A unicidade usa apenas document_id, target_type e target_id. O conflito atualiza tenant_id. As árvores, por sua vez, aceitam equipment_id igual em tenants distintos. Um documento de modelo compartilhado pode legitimamente ser vinculado a ambos.

**Impacto.** Ao copiar o mesmo manual para o mesmo identificador de equipamento em outro tenant, o vínculo anterior é substituído em vez de coexistir. Isso altera quais documentos e imagens cada equipamento encontra.

**Correção recomendada.** Fazer a identidade do vínculo refletir org_id e tenant_id, além do alvo/documento. Tratar explicitamente o tenant nulo e migrar dados/chaves de conflito. Verificar integridade entre vínculo, documento e escopo autorizado.

**Critério de aceitação.** O mesmo documento de modelo associado a equipment_id igual em A e B deve produzir dois vínculos distintos, e ambos continuarão consultáveis.


### R14 — Filtros incompatíveis esvaziam a recuperação direta de chunks

**Gravidade:** Média. **Evidência:** Análise estática do caminho de escrita e leitura.

**Referências:** `src/api.py:224-235`; `src/layers/silver_layer.py:115-116`; `src/layers/silver_layer.py:202-215`; `src/fault_tree/generator.py:2513-2527`; `src/fault_tree/generator.py:4994-5000`; `src/fault_tree/generator.py:5792-5802`; `src/storage/qdrant_client.py:203-218`.

**Problema.** A escrita armazena document_id como envelope_id e context_ids como os contextos do upload, incluindo equipamento/modelo. O gerador monta context_ids com IDs de documento e passa asset_id=doc_id. A busca direta exige simultaneamente document_id correspondente E interseção com context_ids.

**Impacto.** Um upload padrão com contexto de equipamento pode não retornar seus próprios chunks na leitura direta, mesmo com document_id correto. A busca semântica de fallback pode recuperar parte deles, mas não elimina a perda da leitura completa e custa chamadas adicionais.

**Correção recomendada.** Após validar autorização e vínculo, a leitura direta deve filtrar pelo documento e seu escopo, sem obrigar um contexto incompatível. Na busca semântica, aplicar o filtro documental antes do ranking, em vez de recuperar limit*4 resultados e só então descartar os de outros documentos.

**Critério de aceitação.** Executar upload de equipamento com context_ids padrão e buscar diretamente pelo envelope_id: todos os chunks previstos devem aparecer. Um manual pouco similar não pode sumir porque os candidatos iniciais eram de outros manuais.

**Limite ou observação.** Não afirmo que toda geração sempre fica vazia: há caminhos semânticos de fallback. O defeito afeta cobertura, previsibilidade e custo.

### R15 — A validação de citação literal aceita texto com sentido invertido

**Gravidade:** Alta. **Evidência:** Reproduzido com inclusão de uma negação em evidência sintética.

**Referências:** `src/fault_tree/generator.py:3231-3251`; `src/fault_tree/generator.py:3254-3297`.

**Problema.** _evidence_quote_is_grounded, descrita como validação de citação verbatim, aceita também uma interseção de 75% entre conjuntos de palavras. A ordem das palavras e o papel de negações não são preservados.

**Impacto.** Um trecho não existente no manual pode ser aprovado como evidência. No check, a evidência 'The motor must be stopped before maintenance.' validou a citação inventada 'The motor must not be stopped before maintenance.'. Isso compromete a rastreabilidade técnica de causas e ações.

**Correção recomendada.** Para campos apresentados como citação literal, exigir um span verificável no trecho original, permitindo apenas normalizações conservadoras de OCR/espaçamento. Separar citação, interpretação e inferência. Aplicar revisão humana e avaliação de domínio para recomendações de manutenção relevantes à segurança.

**Critério de aceitação.** Corpus com negação, ordem invertida, números, unidades e permissões/proibições. Uma citação não presente deve ser rejeitada, mesmo com vocabulário quase igual. Ações precisam de evidência própria.

**Limite ou observação.** O teste demonstra falha do validador, não mede a frequência com que o modelo produz essa inversão em manuais reais.

### R16 — A reclassificação de taxonomia levanta NameError imediatamente

**Gravidade:** Média. **Evidência:** Reproduzido ao executar a função original.

**Referências:** `src/fault_tree/generator.py:1959-1979`.

**Problema.** reclassify_existing_failure_modes_to_explicit_taxonomy utiliza progress_callback, que não é argumento, variável local ou global definida. A primeira chamada a _emit_failure_mode_progress falha antes da reclassificação.

**Impacto.** A função não pode ser utilizada como está. É um erro determinístico que análise estática básica e teste unitário encontrariam.

**Correção recomendada.** Declarar progress_callback opcional com o mesmo contrato das demais funções, ou remover a referência. Adicionar teste de execução mínima e lint de nomes indefinidos.

**Critério de aceitação.** Chamar a função com itens vazios e com item válido, sem callback e com callback, sem NameError.

**Limite ou observação.** Não identifiquei chamada desse helper no fluxo HTTP principal. Portanto, não o classifico como quebra de toda geração.

### R17 — Erro do provedor de chat vira resposta vazia bem-sucedida

**Gravidade:** Média. **Evidência:** Reproduzido: RuntimeError do cliente Bedrock vira string vazia.

**Referências:** `src/chat/pipeline.py:27-43`; `src/chat/pipeline.py:533-550`.

**Problema.** _chat_claude captura Exception e retorna ''. O try/except do pipeline não recebe essa exceção e constrói ChatResponse com answer vazio. Indicadores de fontes ainda podem ser verdadeiros.

**Impacto.** A falha operacional fica indistinguível de uma resposta vazia; o usuário pode ficar sem orientação e a observabilidade de erro é reduzida. A mensagem do usuário é persistida, mas o store ignora conteúdo vazio do assistente.

**Correção recomendada.** Propagar erro tipado, responder com status apropriado ou fallback explícito e preservar correlation_id. Não expor detalhes internos do provedor na resposta de erro. Validar que answer contém conteúdo antes de registrar sucesso.

**Critério de aceitação.** Timeout, throttling e resposta sem bloco de texto não podem retornar uma resposta vazia como atendimento concluído.


### R18 — Heurística de saudação intercepta perguntas sobre bombas

**Gravidade:** Média. **Evidência:** Reproduzido com a mensagem 'bomba não liga'.

**Referências:** `src/chat/pipeline.py:70-78`; `src/chat/pipeline.py:374-381`.

**Problema.** Mensagens curtas que começam com 'bom' são tratadas como saudação. O prefixo casa com 'bomba', termo central no domínio de equipamentos.

**Impacto.** O problema operacional é ignorado antes da busca e o sistema responde apenas com uma apresentação. Essa falha independe de LLM, base vetorial ou conteúdo do manual.

**Correção recomendada.** Reconhecer saudações completas e fronteiras de palavras. Não tratar o prefixo de um substantivo técnico como cumprimento. Em mensagens compostas, retirar a saudação e manter a pergunta.

**Critério de aceitação.** 'bomba não liga', 'bomba parou' e 'bom dia, bomba parou' devem seguir para o diagnóstico; 'bom dia' isolado pode usar a resposta de saudação.


### R19 — Manuais herdados são autorizados pelo vínculo, mas lidos no escopo errado

**Gravidade:** Alta. **Evidência:** Análise estática; integração com PostgreSQL/Qdrant não foi executada.

**Referências:** `src/storage/postgres_client.py:907-925`; `src/fault_tree/router.py:321-343`; `src/fault_tree/service.py:123-129`; `src/fault_tree/generator.py:5790-5797`; `src/fault_tree/ingest_adapter.py:29-34`; `src/fault_tree/ingest_adapter.py:58-64`; `src/fault_tree/router.py:555-562`.

**Problema.** Documentos de modelo ficam no escopo organizacional (tenant_id=None). A cópia cria um vínculo no tenant do equipamento, mas não duplica o documento nesse tenant. A geração usa o tenant da árvore para recuperar envelope e vetores. A rota de imagens já diferencia document_tenant_id corretamente; geração e chat não fazem a mesma resolução.

**Impacto.** Um manual herdado pode aparecer como vinculado e, ainda assim, não fornecer texto para chat/regeneração. Em combinação com R04, uma regeneração sem evidências pode promover resultado vazio.

**Correção recomendada.** Separar escopo de autorização do alvo e escopo de armazenamento do documento. Resolver o documento a partir do vínculo autorizado e usar seu tenant de origem, sem liberar toda a organização ao usuário.

**Critério de aceitação.** Criar manual organizacional, copiar modelo para equipamento de A e regenerar. O manual deve ser recuperado; um manual organizacional não vinculado deve continuar bloqueado.


### R20 — Jobs aceitos não têm execução durável e a recuperação pode deixá-los presos

**Gravidade:** Alta. **Evidência:** Análise estática do agendamento e do procedimento de recuperação.

**Referências:** `src/api.py:43-52`; `src/api.py:209-256`; `src/fault_tree/router.py:334-344`; `src/storage/postgres_client.py:759-795`.

**Problema.** Uploads e gerações HTTP usam BackgroundTasks e arquivo local /tmp. recover_stale_work só é executado no startup e apenas marca registros mais antigos que STALE_JOB_MINUTES. Não há lease, heartbeat ou retomada de trabalho nessa recuperação.

**Impacto.** Restart pode perder a execução aceita. Um job interrompido poucos minutos antes do startup fica abaixo do corte de 30 minutos e pode permanecer pending/running até outro startup. Em múltiplas instâncias, um trabalho longo ainda válido pode ser marcado como interrompido por outra instância.

**Correção recomendada.** Persistir o insumo em storage durável antes da aceitação; enfileirar comando durável; executar em worker com lease/heartbeat e idempotência. Manter recuperação periódica que retome ou finalize estados com segurança. A própria documentação do FastAPI distingue BackgroundTasks de processamento pesado em workers separados [S7].

**Critério de aceitação.** Matar o processo imediatamente após 202 e durante a geração; reiniciar antes e depois do limite. Cada job deve retomar ou chegar a falha terminal auditável sem permanecer indefinidamente em execução.


### R21 — Extração pode omitir páginas e produzir referências incorretas

**Gravidade:** Média. **Evidência:** Análise estática; não executei OCR nem benchmark de PDFs reais.

**Referências:** `src/processors/pdf_processor.py:23-27`; `src/processors/pdf_processor.py:33-104`; `src/processors/pdf_processor.py:210-233`; `src/envelope/chunker.py:29-54`.

**Problema.** A escolha de extração usa a quantidade total de texto do documento: uma capa textual pode fazer páginas escaneadas posteriores ficarem sem OCR. A etapa Vision considera no máximo 20 páginas, mas extract_pages devolve todo o texto como página 1. O chunker também pode cruzar páginas e manter somente a página inicial. A renderização carrega todas as páginas antes de aplicar o corte.

**Impacto.** Conteúdo relevante pode sumir sem status parcial; referências podem apontar para uma página diferente; documentos extensos podem consumir muita memória antes do limite de Vision. A rastreabilidade dos modos e respostas fica prejudicada.

**Correção recomendada.** Extração e fallback por página; conservar numeração e faixa de páginas; declarar páginas processadas/omitidas e completude. Renderizar incrementalmente com limites de páginas, pixels, tempo e memória. Não inventar página 1 quando a origem é desconhecida.

**Critério de aceitação.** Fixtures de PDF digital, escaneado e misto; manual com mais de 20 páginas; informação só na página final; chunk que cruza duas páginas. A referência deve apontar para a origem real ou ser marcada como indisponível.


### R22 — Versões fixadas da stack de formulários têm avisos de segurança aplicáveis

**Gravidade:** Alta. **Evidência:** Requisitos comparados com avisos primários dos mantenedores em 30/09/2026.

**Referências:** `requirements.txt:28-35`; `src/api.py:149-165`; `src/api.py:206-219`.

**Problema.** python-multipart==0.0.6 está na faixa do aviso GHSA-2jv5-9r88-3w3p de ReDoS no Content-Type [S1] e do aviso de cabeçalhos multipart sem limite GHSA-pp6c-gr5w-3c5g/CVE-2026-42561 [S2]. FastAPI 0.109.0 exige Starlette >=0.35.0,<0.36.0 [S3], faixa afetada pelo aviso GHSA-f96h-pmfr-66vw [S4]. Há também aviso posterior aplicável a formulários URL-encoded [S8].

**Impacto.** A aplicação recebe formulários/uploads e usa os parsers envolvidos. O limite aplicado na função de upload não substitui limites no parser/servidor, pois o corpo precisa ser interpretado antes de chegar a essa lógica.

**Correção recomendada.** Atualizar FastAPI, Starlette e python-multipart de forma compatível, gerar lockfile e auditar todo o conjunto resolvido. Adicionar limites de corpo/cabeçalhos no ingresso e teste de regressão de formulários. Não forçar um Starlette incompatível com FastAPI antigo.

**Critério de aceitação.** Build limpo com versões resolvidas registradas; auditoria de dependências sem os avisos aplicáveis; testes de upload, autenticação, HTTPX e schemas após atualização.

**Limite ou observação.** Foi uma verificação direcionada, não uma auditoria completa de todas as dependências transitivas. Não executei os payloads de DoS. As primeiras versões corrigidas de avisos antigos não devem ser interpretadas como recomendação de versão segura atual.

### R23 — A suíte não tem configuração mínima autossuficiente

**Gravidade:** Média. **Evidência:** Coleta original terminou com 10 erros; dois testes de scope passaram após configuração sintética.

**Referências:** `tests/conftest.py:1-11`; `src/config/settings.py:9-12`; `src/config/settings.py:47-50`; `src/config/settings.py:69-77`; `requirements-dev.txt:1-3`.

**Problema.** conftest configura chaves AWS e região, mas não COGNITO_USER_POOL_ID, obrigatório na construção de Settings durante importação. Pacotes de utilidade importam componentes com configuração/infraestrutura, o que amplia o acoplamento dos testes. Neste ambiente, também faltam dependências que estão declaradas no projeto.

**Impacto.** Uma execução limpa falha antes de exercitar a maior parte dos casos. Testes unitários simples ficam dependentes de variáveis de integração e da inicialização de módulos pesados. O número de funções existentes não representa cobertura.

**Correção recomendada.** Fixtures de configuração completas; factories/injeção de clientes em vez de inicialização global; separar unitários de integração; CI na versão Python do Docker com instalação de requirements-dev. Acrescentar testes negativos de autorização, escopo, falhas e concorrência.

**Critério de aceitação.** pytest --collect-only e a suíte unitária passam em ambiente limpo, sem credenciais reais e sem serviços externos. Testes de integração têm markers, dependências e instruções explícitas.

**Limite ou observação.** A falta de psycopg2, qdrant-client, aio-pika e tenacity aqui é limitação do ambiente de review, não ausência dessas bibliotecas no requirements. Não atribuo todos os erros de coleta ao repositório.

### R24 — O proxy S3 lazy não preserva uma instância

**Gravidade:** Baixa. **Evidência:** Reproduzido: dois acessos resolvem para clientes distintos.

**Referências:** `src/storage/s3_client.py:238-249`.

**Problema.** _LazyS3Client.__getattr__ chama _get_s3_client em cada acesso, e essa função retorna S3Client() sempre. O cache de cliente implementado dentro de S3Client é descartado entre operações resolvidas pelo proxy.

**Impacto.** Criação repetida de clientes e perda de reutilização de recursos de conexão; comportamento diferente do singleton descrito pelo comentário. Não demonstra corrupção de objetos.

**Correção recomendada.** Manter a instância no proxy ou usar factory memoizada com inicialização controlada e possibilidade de override em testes.

**Critério de aceitação.** Dois acessos ao proxy devem usar a mesma instância; clientes de teste continuam substituíveis e a primeira operação inicializa o recurso apenas uma vez.


### R25 — Compose não aplica ao RabbitMQ as credenciais usadas pelos clientes

**Gravidade:** Média. **Evidência:** Análise estática da configuração; stack Docker não foi iniciada.

**Referências:** `docker-compose.yml:9-17`; `docker-compose.yml:35-43`; `docker-compose.yml:85-97`; `docker-compose.yml:55-83`; `Dockerfile:1-19`.

**Problema.** API e worker montam AMQP_URL com RABBITMQ_USER/RABBITMQ_PASSWORD, mas o serviço rabbitmq não recebe configuração de criação desse usuário/senha. Em um volume novo, trocar essas variáveis só muda os clientes. Além disso, os serviços de dados/gestão são publicados no host e o Dockerfile não define USER não-root.

**Impacto.** Credenciais personalizadas podem impedir conexão ao broker. Se essa configuração de desenvolvimento for exposta sem controles adicionais, aumenta-se a superfície de acesso a dados e administração.

**Correção recomendada.** Configurar provisionamento do usuário/vhost/permissões e tratar rotação em volumes existentes. Separar compose de desenvolvimento e produção, limitar binds/rede, remover publicação desnecessária, executar aplicação sem root e definir limites operacionais.

**Critério de aceitação.** Subir em volume novo com credenciais não padrão e confirmar consumo/publicação. Verificar listeners e permissões no ambiente-alvo.

**Limite ou observação.** Não afirmo que a rede de produção esteja exposta nem que a stack com credenciais padrão necessariamente falhe: a política de guest da imagem precisa ser considerada. O defeito verificável é a falta de provisionamento das credenciais personalizadas.

### R26 — Bootstrap de schema e migrações não produzem garantias equivalentes

**Gravidade:** Média. **Evidência:** Análise estática do metadata, scripts e instruções de atualização.

**Referências:** `src/storage/postgres_client.py:45-60`; `scripts/migrate_organization_scope.sql:5-30`; `README.md:471-481`; `src/api.py:43-51`.

**Problema.** O metadata usa UNIQUE(org_id, tenant_id, document_hash), enquanto a migração organizacional cria índice funcional com COALESCE para o escopo nulo. Essas garantias não são equivalentes. O trecho de atualização do README lista apenas as outras duas migrações e omite migrate_organization_scope.sql. create_all no startup não substitui a aplicação dessas alterações. As diferenças de NULL em unicidade e o alcance de create_all são documentados pelos projetos [S9, S10].

**Impacto.** Bancos novos e atualizados podem diferir na deduplicação de documentos organizacionais. Em bancos antigos, a omissão da migração pode manter tenant_id obrigatório e quebrar ingestão de documentos de modelo.

**Correção recomendada.** Adotar uma sequência versionada de migrações como fonte de verdade; alinhar índices do metadata e schema final, inclusive semântica de NULL; testar upgrade do schema anterior e bootstrap do zero. Verificar versão do banco no startup e readiness.

**Critério de aceitação.** Comparar constraints/índices de uma base nova e uma migrada. Ambas aceitam escopo organizacional e rejeitam duplicatas conforme o mesmo contrato.

**Limite ou observação.** A equivalência completa de migração precisa ser validada em PostgreSQL; o review não executou esses scripts contra uma instância real.

### R27 — Sequenciamento do histórico de chat tem corrida

**Gravidade:** Média. **Evidência:** Análise estática do SELECT e INSERT em sessões distintas.

**Referências:** `src/chat/store.py:20-44`; `src/storage/postgres_client.py:307-317`.

**Problema.** _next_seq calcula MAX(seq)+1 em uma sessão e append insere em outra. A tabela não tem unicidade do contador dentro do escopo. Duas requisições podem calcular o mesmo próximo valor.

**Impacto.** Mensagens concorrentes podem ter sequência duplicada e ordenação instável, afetando leitura e resumo. O desenho também mantém uma conversa por equipamento/tenant, não uma sessão explícita por usuário; esse segundo ponto depende do contrato do produto.

**Correção recomendada.** Usar um identificador ordenável gerado pelo banco ou contador atualizado atomicamente; impor a constraint correspondente. Definir se o histórico é compartilhado pelo equipamento ou isolado por conversa, antes de adicionar um conversation_id.

**Critério de aceitação.** Duas gravações simultâneas produzem sequência única e ordem determinística. Resumos não perdem nem repetem intervalos.

**Limite ou observação.** Não classifico o histórico compartilhado como vazamento sem requisito de isolamento por usuário. A corrida do contador é independente dessa decisão.

## Arquitetura e manutenibilidade

### Preservar as boas decisões

A separação física por responsabilidades facilita corrigir o projeto sem reescrita ampla. A propagação de `org_id`/`tenant_id` já existe em boa parte dos acessos, incluindo filtros explícitos no Qdrant (`src/storage/qdrant_client.py:182-201`). O defeito R01 se concentra em uma exceção perigosa a essa regra.

A ativação de versão contém verificação de linhas afetadas e rollback em uma transação (`src/storage/postgres_client.py:1233-1271`). Esse desenho é uma boa base para adicionar critérios de promoção e controle de concorrência.

O catálogo de imagens usa um ID determinístico que inclui escopo, documento e hash (`src/processors/image_cataloger.py:63-67`). O upload HTTP lê em blocos e aplica um limite de arquivo (`src/api.py:209-219`). Essas proteções devem ser preservadas, mas não substituem idempotência de todo o pipeline nem limites no parser e nas entradas de fila.

### Refatorar depois de estabilizar os contratos

O arquivo `generator.py` mistura recuperação, normalização, prompts, parsing, deduplicação, taxonomia, alarmes e validação. Eu o dividiria em módulos de recuperação, extração, validação de evidências, classificação, alarmes e montagem de resultado. A migração deve ser incremental, apoiada por fixtures e testes de caracterização: apenas mover funções sem preservar comportamento não elimina os defeitos.

O maior problema transversal é a conversão de falhas operacionais em valores aparentemente válidos: `[]`, `None`, `False` ou string vazia. Recomendo um contrato explícito de resultado por etapa, incluindo status, erros tipados, completude, métricas e identificadores de origem. “Não encontrado” e “não foi possível consultar” devem ser estados distintos.

Um objeto de escopo validado pode reduzir a repetição e impedir chamadas de equipamento sem tenant. Para herança, distinguir claramente o escopo do solicitante, o vínculo autorizado e o escopo original do documento. Uma função “universal” com tenant opcional aumenta o risco de leituras acidentalmente amplas.

Os clientes e a configuração devem ser construídos no startup/factories e injetados, em vez de exigir configuração completa ao importar módulos. Além de facilitar testes, isso permite controlar readiness, retries, fechamento de recursos e substituição de dependências.

### Desempenho, limites e observabilidade

`MAX_WORKERS` é armazenado no orquestrador, mas não estabelece por si só um limite operacional para todos os jobs. Definir concorrência por etapa, limites por tenant e orçamento de chamadas de IA é mais útil do que apenas expor essa variável. Verificar também chamadas de HTTP/DynamoDB síncronas no caminho de dependências async de autenticação antes de medir latência sob carga.

A configuração do chunker deve validar `chunk_size > overlap >= 0`: o código usa `stride = chunk_size - overlap` sem validação (`src/envelope/chunker.py:20-40`), e a Silver fixa overlap em 100 (`src/layers/silver_layer.py:192`). Um `CHUNK_SIZE` igual a 100 provoca passo zero; menor que 100 pode gerar resultado vazio. Acrescentar limites em configurações numéricas e parâmetros de paginação.

Eu registraria por job: organização/tenant, documento, equipamento, geração/versão, etapa, duração, tentativas, motivo de falha, páginas previstas/processadas, chunks gerados/persistidos, falhas de embedding e completude. Para mensageria: idade da outbox, DLQ, tentativas e leases expirados. Para o modelo: versão de prompt/modelo, uso de tokens, stop_reason e resultado da validação, evitando gravar desnecessariamente conteúdo sensível.

### Qualidade da IA

Não há base suficiente nesta execução para atribuir uma “acurácia” ao sistema. A existência de prompts exigindo evidências não garante que os resultados obedeçam a eles. R15 mostra uma brecha determinística no validador; R03, R14, R19 e R21 afetam a recuperação e a origem das evidências antes da resposta.

Para avaliar qualidade, preparar um corpus versionado e autorizado de manuais, com revisão de especialista. Medir cobertura de falhas explícitas, precisão de causa/efeito/ação, alarmes incorretos, páginas citadas, negações/unidades, abstenção sem evidência e estabilidade após alterações de prompt/modelo. Tratar texto do manual como dado não confiável, inclusive para instruções embutidas, sem dar a ele autoridade sobre o sistema. Não usar essas saídas como instrução operacional autônoma sem validação apropriada ao risco.

## Ordem recomendada de correção

### Etapa 1 — Fechar os bloqueadores de acesso e evitar perda silenciosa

Corrigir R01/R02 e fixar testes negativos de autorização antes de novas features. Exigir audience (R11), verificar as precondições de SSRF (R08) e atualizar a stack vulnerável (R22). Em paralelo, impedir promoção de resultados inválidos (R04) e sucesso com gravação/indexação falha (R05).

**Saída esperada:** nenhuma consulta de equipamento sem tenant, nenhuma exclusão com papel de leitura, nenhuma candidata inválida substituindo a ativa e nenhuma resposta de sucesso com persistência obrigatória falha.

### Etapa 2 — Tornar execução e eventos recuperáveis

Corrigir ACK/retry/DLQ/supervisão (R06/R07), transação da outbox (R09), concorrência de gerações (R10) e execução durável/leases (R20). Provisionar corretamente o broker (R25), alinhar schema/migrações (R26) e acrescentar idempotência por requisição e etapa.

**Saída esperada:** reinício, redelivery e conclusão fora de ordem têm resultados previstos e verificáveis; falhas são visíveis e trabalhos não desaparecem nem ficam indefinidamente em execução.

### Etapa 3 — Recuperar o documento certo e provar a evidência

Resolver documentos a partir dos vínculos autorizados (R03/R19); alinhar filtros de recuperação (R14); corrigir identidade do vínculo (R13); reforçar a validação de citações (R15) e preservar páginas/completude (R21). Corrigir as regressões determinísticas de chat e helper (R16–R18), JWKS (R12) e sequência (R27).

**Saída esperada:** equipamento A nunca usa manual de B por omissão de filtros; herança autorizada funciona; citação, ação e página são verificáveis.

### Etapa 4 — Reduzir o custo de manutenção

Tornar a suíte autossuficiente (R23), introduzir análise estática contínua, separar as responsabilidades do gerador e corrigir a factory S3 (R24). Criar testes de integração reproduzíveis, checks de schema/OpenAPI e avaliação de qualidade do corpus.

**Saída esperada:** cada alteração relevante tem teste de comportamento e rollback operacional; novos bugs de escopo, nomes indefinidos e retorno de erro não dependem de detecção manual.

## Matriz mínima de regressão antes de produção

| Área | Cenários essenciais |
|---|---|
| Autorização | Sem token, token inválido, audience errado, leitura tentando escrita, chave interna inválida, troca de tenant e organização |
| Escopo documental | Equipamentos A/B no mesmo tenant, tenants distintos, modelo organizacional herdado e não vinculado, envelope divergente do vínculo |
| Geração | Resultado vazio, JSON inválido/truncado, ausência real de falhas, duas gerações simultâneas, promoção fora de ordem, erro durante ativação |
| Persistência | Falha de job, envelope, chunks, vínculo, evento e mark_published; nenhum completed prematuro |
| Mensageria | Download 503/timeout, JSON não objeto, redelivery, duplicata, DLQ, crash antes/depois do ACK/publish, dois publicadores |
| Recuperação | Restart após 202, arquivo já persistido/não persistido, lease expirado, job recente e antigo, outra instância ainda ativa |
| Documentos/IA | PDF digital/misto/escaneado, páginas após o limite, chunk multipágina, negação, unidades, citação inexistente, ausência de evidência |
| Infra/schema | Banco novo e migrado equivalentes, broker com credenciais não padrão, volumes persistentes, listeners e readiness |

## Reproduções fornecidas

`review_checks.py` recebe a raiz do repositório extraído e gera um JSON. Ele não modifica o código-fonte, não usa os serviços reais e não é substituto da suíte original.

```bash
python review_checks.py "/caminho/para/o/repositorio" --output resultados.json
```

Dependências do harness: `pydantic`, `fastapi`, `loguru`, `sqlalchemy`, `PyJWT` e `cryptography`, além da biblioteca padrão. O arquivo `environment.json` registra as versões efetivamente usadas nesta revisão; elas não são as versões fixadas do deployment.

A execução seleciona corpos originais de funções/classes via AST para evitar importação de clientes reais. Constantes e helpers necessários são mantidos; colaboradores são doubles explícitos. As duas verificações SQL usam SQLite apenas para resultados de consulta/UPSERT, não para validar DDL, isolamento ou concorrência de PostgreSQL.

O nome de cada check indica o achado. `bug_reproduced=true` é uma expectativa do review sobre o snapshot vulnerável: após corrigir o código, o check correspondente pode falhar por não mais reproduzir o defeito. Transformar cada cenário em teste normal de comportamento seguro na suíte do projeto.

### Reprodução de teste original

O arquivo `original_scope_tests.log` contém o resultado de:

```bash
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test COGNITO_USER_POOL_ID=test PYTHONPATH=. pytest -q tests/test_scope.py
```

A configuração é exclusivamente sintética. `original_collection.log` guarda a tentativa de coleta sem `COGNITO_USER_POOL_ID`, e `review_checks.log` guarda a execução das reproduções. Nenhum desses resultados comprova integração com AWS ou serviços de produção.

## Fontes externas primárias

Consultadas em 30/09/2026. Os identificadores [S1]–[S10] são usados nos achados pertinentes. As fontes documentam comportamento dos componentes e avisos publicados; a aplicabilidade ao projeto decorre das versões e chamadas observadas no snapshot.

**[S1]** python-multipart: Content-Type Header ReDoS, GHSA-2jv5-9r88-3w3p; publicado em 12/02/2024. Faixa afetada inclui 0.0.6.

`https://github.com/Kludex/python-multipart/security/advisories/GHSA-2jv5-9r88-3w3p`

**[S2]** python-multipart: cabeçalhos multipart sem limite, GHSA-pp6c-gr5w-3c5g / CVE-2026-42561; publicado em 29/04/2026. Faixa afetada <0.0.27.

`https://github.com/Kludex/python-multipart/security/advisories/GHSA-pp6c-gr5w-3c5g`

**[S3]** FastAPI 0.109.0: pyproject.toml oficial, dependência Starlette >=0.35.0,<0.36.0.

`https://raw.githubusercontent.com/fastapi/fastapi/0.109.0/pyproject.toml`

**[S4]** Starlette: DoS via multipart/form-data, GHSA-f96h-pmfr-66vw; publicado em 15/10/2024.

`https://github.com/Kludex/starlette/security/advisories/GHSA-f96h-pmfr-66vw`

**[S5]** Amazon Cognito: verificação de JWT, audience e atualização do cache de JWKS em rotação de chaves.

`https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html`

**[S6]** RabbitMQ: acknowledgements, publisher confirms, redelivery e riscos de requeue imediato.

`https://www.rabbitmq.com/docs/confirms`

**[S7]** FastAPI: Background Tasks, seção Caveat sobre processamento pesado em processos/servidores separados.

`https://fastapi.tiangolo.com/tutorial/background-tasks/`

**[S8]** Starlette: limites ignorados em formulários URL-encoded, GHSA-82w8-qh3p-5jfq; publicado em 12/06/2026. Faixa afetada >=0.4.1,<1.3.1.

`https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq`

**[S9]** PostgreSQL: constraints de unicidade e comportamento padrão de valores NULL.

`https://www.postgresql.org/docs/current/ddl-constraints.html`

**[S10]** SQLAlchemy 2.0: MetaData, criação de schema e limites de create_all para migrações.

`https://docs.sqlalchemy.org/en/20/core/metadata.html`

## Conclusão

O projeto merece uma estabilização orientada a invariantes, não apenas uma limpeza de código. Os invariantes centrais são: cada operação tem um escopo obrigatório, nenhuma permissão de leitura autoriza escrita, um trabalho só é concluído quando seus efeitos exigidos são duráveis, e nenhum resultado gerado substitui a versão ativa sem evidências e estado elegível.

As falhas reproduzidas justificam bloquear a liberação multi-tenant deste snapshot. Depois das correções e dos testes de integração propostos, a estrutura existente oferece uma base razoável para evoluir o produto sem reescrever tudo.
