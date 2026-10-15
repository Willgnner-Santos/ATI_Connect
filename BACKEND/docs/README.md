# Documentação · Plataforma ATI

O [README principal](../readme.md) apresenta o projeto e o fluxo inicial.
Este diretório concentra os contratos, guias de uso e procedimentos de operação.

## API

[openapi.yaml](openapi.yaml) descreve 41 operações HTTP. A
[collection Postman](plataforma-ati.postman_collection.json) inclui requisições,
variáveis e scripts de apoio. O inventário de métodos e rotas é comparado ao
[template SAM](../template.yaml) pelos testes unitários; isso não substitui a
validação funcional de todos os schemas e exemplos.

Importe a collection no Postman e configure `base_url` para o ambiente desejado.
Preencha credenciais apenas no ambiente local do Postman, sem exportar segredos.
Para a API publicada, `base_url` inclui o stage; para SAM local, não:

```text
Publicado: https://<api-id>.execute-api.<regiao>.amazonaws.com/<stage>
Local:     http://127.0.0.1:3000
```

Os arquivos OpenAPI e Postman preservam o endpoint original de sandbox. Ele é
uma configuração de exemplo do projeto, não a confirmação de disponibilidade
ou autorização de acesso ao ambiente.

## Guias e operação

| Documento | Conteúdo |
| --- | --- |
| [Criar organização](guides/criar-organizacao.md) | Hierarquia e criação de organizações |
| [Collaborator](guides/collaborator-role.md) | Escopo do papel operacional global |
| [Técnico Virtual](guides/virtual-technician.md) | Ações, notificações e agendamentos |
| [Testes locais](ops/local-testing.md) | Unitários, Docker/SAM e integração real |
| [Scripts](ops/scripts.md) | Deploy, layer, atualização, migrações e simuladores |
| [Cognito](ops/cognito.md) | Consultas de diagnóstico sem credenciais fixas |
| [Infraestrutura](../infrastructure/README.md) | Limites entre stacks e tabelas DynamoDB |
| [Validação manual](validation/validation.md) | Roteiros funcionais de QA |
| [Limitações conhecidas](ops/known-limitations.md) | Premissas e pendências operacionais |

## Manutenção

Ao alterar endpoints, mantenha `template.yaml`, handlers, OpenAPI e collection
coerentes e execute `python -m pytest`. Atualize o guia funcional correspondente
quando uma regra mudar. Evite duplicar a lista completa de endpoints em vários
READMEs: o contrato e os testes devem concentrar essa informação.

O [relatório de desenvolvimento de abril a agosto de 2026](history/development-2026-04-01--2026-08-07.md)
é um registro histórico, não um manual de operação nem um inventário atual.
