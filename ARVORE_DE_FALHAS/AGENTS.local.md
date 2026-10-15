# AGENTS.local.md

## Aprendizados acumulados

- Modelos e documentos de modelo pertencem à organização e usam `tenant_id = null`; equipamentos pertencem a tenants e exigem `org_id + tenant_id`.
- O Cognito envia `custom:tenantId=PLATFORM` para superadmin. Esse valor é uma sentinela de escopo organizacional, não um tenant real, e deve ser normalizado na fronteira de autenticação.
- `context_ids` é apenas metadado legado de busca. Associações de documento a modelo/equipamento devem usar `document_bindings` e os campos tipados `target_type`/`target_id`.
- O serviço de árvores não consulta atualmente o cadastro do ATI. Na criação direta, a existência do equipamento não pode ser confirmada; o isolamento garantido é o primeiro vínculo do UUID ao `org_id + tenant_id` autenticado.
- Substituições de modos de falha usam versão candidata e promoção transacional; nunca limpar o resultado ativo antes de a nova geração concluir.
- Equipamentos podem não ter modelo de origem. `model_id` é opcional em árvores e versões de equipamento, mas continua obrigatório para templates e herança de modelo.
