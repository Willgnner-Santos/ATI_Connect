from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from src.bedrock.bedrock_client import bedrock_client


def chat_claude(_, *, system=None, messages, max_tokens=4096):
    body: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "messages": messages,
    }
    if system:
        body["system"] = system
    result = bedrock_client._invoke_model_raw(body)
    for block in result.get("content", []):
        if block.get("type") == "text":
            return block["text"]
    return ""


_CATALOG_FAILURE_GUARD_VERSION = "v2"
_RAG_FAILURE_QUALITY_VERSION = "v1.2"
_FAILURE_MODE_PROGRESS_VERSION = "v1"


FailureModeProgressCallback = Callable[[dict[str, Any]], None]


def _emit_failure_mode_progress(
    callback: FailureModeProgressCallback | None,
    *,
    percent: int,
    phase: str,
    message: str,
    current_batch: int = 0,
    total_batches: int = 0,
    failures_found: int = 0,
) -> None:
    if callback is None:
        return
    try:
        callback({
            "percent": max(0, min(99, int(percent))),
            "phase": str(phase or "processing"),
            "message": str(message or "Processando modos de falha..."),
            "current_batch": max(0, int(current_batch)),
            "total_batches": max(0, int(total_batches)),
            "failures_found": max(0, int(failures_found)),
        })
    except Exception:
        # Telemetria nunca pode interromper a geração da FTA.
        return


FAILURE_MODE_SYSTEM = """Você é um engenheiro de confiabilidade que extrai modos de falha exclusivamente de evidências de um manual técnico.
O manual pode ser de qualquer equipamento, máquina, sistema, componente ou fabricante. Nunca assuma antecipadamente o tipo, a marca ou os subsistemas do ativo e nunca reutilize conhecimento de outro equipamento.

Retorne SOMENTE JSON válido no formato:
{"items":[{"title":"","description":"","evidence_quote":"","category":"","severity":"media","tags":[],"source_pages":[],"alarm_codes":[],"cause_groups":[{"cause":"","effects":[],"actions":[]}],"causes":[],"effects":[],"actions":[],"probable_cause":"","symptoms":"","impact":"","recommendation":""}]}

Regras obrigatórias de evidência:
- Use somente informações sustentadas pelos trechos fornecidos nesta chamada. Não complete lacunas com conhecimento externo, exemplos anteriores ou outro manual.
- evidence_quote é obrigatório para cada item automático: copie literalmente um trecho curto da evidência fornecida (sem traduzir, resumir ou parafrasear) que contenha a condição anormal e/ou o componente que sustenta o modo de falha. Se não existir um trecho literal de apoio, NÃO gere o item.
- Cada item deve representar uma falha técnica, defeito, anomalia, alarme, perda de função, degradação, condição anormal de processo ou condição explicitamente ligada a mau funcionamento do ativo.
- Uma proibição/instrução do tipo "não faça X", "não remova X", "não opere X" ou regra de segurança NÃO é, por si só, um modo de falha. Só crie uma falha quando o texto também declarar a condição técnica anormal/consequência correspondente.
- Perigo ocupacional, condição de uso, escolha de material, EPI, risco de queimadura/esmagamento, procedimento de transporte ou condição segura de operação NÃO é modo de falha se não houver perda de função, defeito ou anomalia técnica do equipamento/processo.
- Não transforme checklist, inspeção, etapa de manutenção, componente, peça, recomendação ou house event isolado em modo de falha.
- Seções de catálogo/lista de peças, peças de reposição, spare parts, replacement parts, parts list, ricambi, componentes de reposição, fabricantes, tipos e códigos de peça NÃO são evidência de modo de falha por si só. Um título de sistema/conjunto dentro de um catálogo de peças (por exemplo, "Sistema elétrico" seguido apenas de itens, fabricantes e códigos) NÃO deve virar "Falha no sistema ...". Só extraia um modo dessas seções quando o próprio texto declarar explicitamente uma condição anormal, defeito, sintoma, causa ou troubleshooting do equipamento.
- Não crie causa circular: a causa não pode ser igual, quase igual ou mera repetição/paráfrase do título da falha.
- effects deve descrever consequência operacional/técnica. O nome ou número de um alarme não é um efeito por si só.
- actions deve conter apenas ações corretivas/diagnósticas explicitamente sustentadas pelo trecho. Se uma ação não estiver sustentada, não invente uma ação genérica.
- cause_groups deve preservar a relação causa -> efeito -> ação quando ela estiver explícita. Não crie "Diagnóstico geral" para guardar ações soltas.
- category deve preferir o nome real do subsistema, conjunto, processo ou função descrito no próprio manual (por exemplo, o nome de um sistema, unidade, circuito, estação ou mecanismo). Use categorias genéricas somente se o trecho não trouxer uma categoria mais específica.
- Inclua também falhas de qualidade/processo quando o manual as tratar como defeitos, alarmes ou condições anormais do equipamento/processo.
- alarm_codes deve conter exatamente códigos/números que o próprio trecho identifica explicitamente como alarme, alarm, erro/error code, fault code, trip ou warning code. Números de diagnóstico/registradores de leitura (por exemplo DGN/DIAG/Diagnosis No.), referências elétricas, bornes, conectores, coordenadas de esquema, códigos de peça e identificadores de opção NÃO são alarm_codes só porque parecem códigos.
- source_pages deve conter somente números de página explicitamente mostrados nos cabeçalhos [Página ...] ou [Páginas ...] do contexto. Não invente páginas.
- Quando houver código de alarme ou página, mencione-os também de forma objetiva na description para facilitar auditoria.
- severity deve ser uma entre: baixa, media, alta, critica. Não marque tudo com a mesma severidade; use a consequência descrita no texto e, quando não houver evidência suficiente, use media.
- tags deve conter termos curtos e específicos do subsistema, componente, alarme ou fenômeno encontrado no texto.
- probable_cause, symptoms, impact e recommendation podem resumir os campos estruturados, sem adicionar informação nova.
- Ignore riscos de projeto, cronogramas, custos, partes comerciais, marketing, responsabilidades administrativas e textos sem relação com falha técnica do ativo.
- Se não houver modos de falha relevantes no trecho, retorne {"items":[]}.
- Escreva em português do Brasil, preservando nomes técnicos, siglas, códigos e nomes de subsistemas do manual.
"""

ALARM_FAILURE_MODE_SYSTEM = """Você é um engenheiro de confiabilidade extraindo modos de falha exclusivamente de registros de ALARME/FAULT/ERROR já validados do manual técnico atual.
Retorne SOMENTE JSON válido e compacto no formato:
{"items":[{"title":"","description":"","evidence_quote":"","category":"","severity":"media","tags":[],"alarm_codes":[],"cause_groups":[{"cause":"","effects":[],"actions":[]}]}]}

Regras obrigatórias:
- Use somente os registros fornecidos nesta chamada. Não use conhecimento externo, fabricante/modelo, outro manual ou inferências não escritas.
- Cada registro recebido já foi classificado deterministicamente como alarme/fault/error do documento atual; mensagens operacionais meramente informativas não são fornecidas aqui.
- evidence_quote é obrigatório e deve copiar literalmente um trecho curto de UM dos campos text fornecidos. Não traduza nem parafraseie a citação.
- alarm_codes deve conter somente códigos presentes exatamente nos registros fornecidos e diretamente sustentando o mesmo modo de falha.
- Agrupe códigos somente quando descrevem a MESMA condição técnica/componente. Não una apenas por pertencerem ao mesmo subsistema.
- Não crie um modo por código automaticamente. Gere entre 1 e 6 modos distintos quando houver condições anormais diferentes no lote, priorizando perda de função, proteções/trips, sensores, intertravamentos e anomalias de processo.
- title deve ser uma condição de falha técnica objetiva, não um cabeçalho genérico de sistema e não uma ação.
- description deve resumir apenas o que os registros dizem sobre a condição. Não inclua código que não esteja em alarm_codes.
- cause_groups só deve conter causa/efeito/ação explicitamente sustentados pelos registros. Não invente ações genéricas.
- severity deve ser baixa, media, alta ou critica; quando a evidência não justificar outra classificação, use media.
- category deve usar o subsistema/componente indicado pelos próprios registros; não invente taxonomia externa.
- Se houver pelo menos uma condição anormal válida no lote, não retorne items vazio.
- Escreva os campos descritivos em português do Brasil e preserve códigos/siglas do manual.
"""

ALARM_ASSIGNMENT_SYSTEM = """Você valida a associação entre modos de falha e registros de ALARME já extraídos do mesmo manual técnico.
Retorne SOMENTE JSON válido no formato:
{"assignments":{"mode_id":["codigo"]}}

Regras obrigatórias:
- Use somente os candidatos fornecidos para cada mode_id.
- Se nenhum candidato descrever a MESMA condição técnica do modo de falha, retorne lista vazia.
- NÃO associe um código apenas porque pertence ao mesmo subsistema, contém a mesma palavra genérica (laser, sensor, motor, controle etc.) ou aparece próximo no manual.
- Diferencie falha, causa, efeito, intertravamento vizinho e condição operacional. Um alarme de outro fenômeno técnico não deve ser anexado.
- O RÓTULO OFICIAL do alarme deve representar diretamente a mesma condição técnica e o mesmo componente do TÍTULO do modo. Causas/efeitos servem apenas como confirmação e nunca justificam sozinhos uma associação.
- Diferencie qualificadores próximos porém distintos: calibração, contato/colisão, fio interrompido, comunicação off-line, proteção disparada, posição não atingida, nível/pressão/fluxo baixo, aberto/fechado etc.
- Se houver componente numerado/ordinal, o índice deve coincidir exatamente.
- Selecione no máximo 3 códigos por modo, apenas quando cada um satisfizer individualmente essas regras.
- Códigos de tabelas de mensagens operacionais não são candidatos e nunca devem ser tratados como alarm_codes.
- Na dúvida, não associe. Precisão é mais importante que quantidade.
"""

TAXONOMY_SYSTEM = """Você identifica a taxonomia funcional de um equipamento exclusivamente a partir do manual atual.
Retorne SOMENTE JSON válido:
{"categories":[{"name":"","aliases":[],"keywords":[]}]}

Regras:
- Crie entre 6 e 18 categorias principais quando houver evidência suficiente.
- Priorize grupos funcionais, subsistemas, conjuntos e processos reais descritos no documento.
- Não use fabricante/modelo como categoria.
- Não transforme cada componente pequeno em categoria principal.
- Não crie mega-categoria juntando quatro ou mais subsistemas independentes por hífen, barra ou lista; prefira os grupos funcionais reais documentados.
- Não force taxonomias genéricas Mecânica/Elétrica quando o manual trouxer grupos mais específicos.
- Preserve nomes técnicos próprios quando úteis.
- Se o documento trouxer uma tabela/lista explícita de grupos funcionais, trate esses grupos como taxonomia principal e não os fragmente em componentes menores.
- Nunca use nomes da plataforma de software (Data Ingest, RAG, Qdrant, Bedrock, Claude, API, documento/manual) como categoria do equipamento.
- Escreva os nomes em português do Brasil.
- aliases deve conter traduções/sinônimos encontrados entre as categorias candidatas ou no manual.
"""

CATEGORY_MAPPING_SYSTEM = """Você classifica modos de falha em uma taxonomia já aprovada para o documento atual.
Retorne SOMENTE JSON válido:
{"mapping":{"mode_id":"Nome da categoria"}}

Regras:
- Use exclusivamente uma das categorias permitidas recebidas.
- Classifique pela função/subsistema ao qual a falha pertence, não por uma palavra isolada do título.
- Não invente categoria nova.
- Se houver ambiguidade, escolha a categoria funcional mais próxima sustentada pelo título, causas, efeitos e tags.
"""

CATEGORY_RECLASSIFICATION_SYSTEM = """Você reclassifica modos de falha JÁ EXISTENTES para a taxonomia funcional explícita do próprio manual.
Retorne SOMENTE JSON válido:
{"mapping":{"mode_id":"Nome exato da categoria permitida"}}

Regras obrigatórias:
- Use EXCLUSIVAMENTE uma das categorias permitidas recebidas. Nunca crie, traduza, fragmente, combine ou renomeie categorias.
- O campo current_category é apenas um vestígio da classificação anterior e NÃO é autoridade.
- Classifique pelo grupo funcional que fisicamente/funcionalmente possui a falha, usando título, descrição, causas, efeitos, ações e evidência das páginas de origem.
- Componentes pequenos (sensor, bomba, válvula, rolamento, encoder, circuito, solda etc.) devem ser atribuídos ao grupo funcional principal ao qual pertencem no documento, e não virar categoria própria.
- Falhas elétricas/intertravamentos devem ir ao grupo funcional que possui o circuito quando isso estiver claro; use o grupo elétrico principal apenas quando a evidência realmente tratar do quadro/alimentação/circuito geral.
- Falhas de processo/qualidade devem ir ao grupo funcional responsável pelo fenômeno técnico, e não a uma categoria abstrata de qualidade/processo.
- Se houver ambiguidade, escolha a categoria permitida com maior propriedade funcional sustentada pela evidência.
- Todos os mode_id recebidos devem aparecer no mapping.
"""

_TAXONOMY_RECLASSIFICATION_VERSION = "v2"

EXPLICIT_FUNCTIONAL_TAXONOMY_RECOVERY_SYSTEM = """Você extrai SOMENTE a lista explícita de grupos funcionais/subsistemas que aparece no trecho do manual atual.
Retorne SOMENTE JSON válido no formato:
{"groups":["Nome 1","Nome 2"]}

Regras obrigatórias:
- Não infira, resuma, traduza, reorganize nem invente grupos.
- Use apenas nomes que estejam literalmente presentes no trecho fornecido sob um cabeçalho/lista/tabela de grupos funcionais, functional groups, gruppi funzionali ou equivalente.
- Ignore PLC, Profibus, encoders, inversores, alarmes e componentes citados fora da lista explícita.
- Preserve a ordem documental da lista.
- Se não existir uma lista explícita confiável no trecho, retorne {"groups":[]}.
"""

EXISTING_ALARM_REVALIDATION_SYSTEM = """Você faz uma revisão FINAL de associações de alarmes já salvas em uma FTA.
Retorne SOMENTE JSON válido:
{"keep":{"mode_id":["codigo"]}}

Regras obrigatórias:
- Para cada mode_id, avalie SOMENTE os códigos/labels oficiais fornecidos em candidates.
- Mantenha um código apenas se o RÓTULO OFICIAL representar diretamente a MESMA condição técnica e o MESMO componente do título do modo.
- Não mantenha código só porque pertence ao mesmo subsistema ou porque descrição/causa/efeito menciona o mesmo contexto.
- Títulos genéricos como "falha no sistema elétrico", "falha no sistema pneumático", "falha no sensor", "falha no carregador" NÃO devem possuir alarmes altamente específicos de comunicação, posição, calibração, reconhecimento ou intertravamento, a menos que o próprio título expresse essa condição específica.
- Diferencie rigorosamente: comunicação/off-line ≠ falha elétrica genérica; posição não atingida ≠ sensor com falha; calibração ≠ contato/colisão; material não encontrado ≠ oxidação; timeout ≠ desalinhamento; reconhecimento de tipo ≠ falha genérica do componente.
- Se o label oficial documentar explicitamente duas condições alternativas para o mesmo código, o código pode ser mantido em modos equivalentes a cada uma dessas condições.
- Na dúvida, REMOVA. Precisão é mais importante que cobertura.
- Todos os mode_id recebidos devem aparecer em keep, mesmo que com lista vazia.
"""

SEMANTIC_DEDUP_SYSTEM = """Você revisa pares de modos de falha candidatos a duplicação.
Retorne SOMENTE JSON válido:
{"merge_pairs":[["mode_id_a","mode_id_b"]]}

Una somente quando os dois itens representam a MESMA condição técnica de falha, mesmo que escritos com palavras diferentes.
NÃO una:
- uma causa com a falha que ela provoca;
- uma falha de componente com uma falha diferente do subsistema;
- condições com códigos de alarme distintos quando o texto indicar fenômenos técnicos diferentes;
- itens apenas porque pertencem à mesma categoria.
Na dúvida, não una.
"""

FAILURE_REVIEW_SYSTEM = """Você faz uma revisão conservadora de modos de falha extraídos de um manual técnico.
Retorne SOMENTE JSON válido:
{"drop_mode_ids":["..."]}

Inclua em drop_mode_ids APENAS itens que claramente NÃO são modos de falha, por exemplo:
- proibição/house event sem anomalia técnica;
- perigo ocupacional ou uso de EPI sem perda de função do equipamento;
- procedimento normal, transporte, instalação ou escolha de material sem falha técnica;
- componente isolado sem condição de falha;
- recomendação de manutenção sem defeito/anomalia.
Mantenha falhas, alarmes, perdas de função, anomalias de processo, defeitos de qualidade e condições técnicas anormais.
Na dúvida, mantenha.
"""

FAILURE_TITLE_STOPWORDS = {
    "falha", "falhas", "erro", "erros", "problema", "problemas", "sistema",
    "na", "no", "nas", "nos", "da", "do", "das", "dos", "de", "em", "e",
    "para", "por", "um", "uma", "a", "o", "ao", "à", "producao", "produção",
}

ALARM_TABLE_TERMS = (
    "alarm code", "fault code", "error code", "lista de alarmes", "tabela de alarmes",
    "alarmes plc", "plc alarms", "alarm plc", "alarmes iso", "iso alarms", "alarm list",
    "tabella allarmi", "lista allarmi", "codice allarme",
    "tabla de alarmas", "lista de alarmas", "codigo de alarma",
    "alarmliste", "alarmtabelle", "fehlercode", "storungscode",
    "table des alarmes", "liste des alarmes", "code defaut", "code erreur",
)

ALARM_MESSAGE_SECTION_TERMS = (
    "mensagens plc", "mensagem plc", "plc messages", "operational messages",
    "mensagens operacionais", "operating messages",
)

_FAILURE_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:falh\w*|fault\w*|fail(?:ure|ed|ing)?|erro\w*|error\w*|alarme?\w*|alarm\w*|trip\w*|shutdown\w*|interlock\w*|avaria\w*|anomalia\w*|defeito\w*|defect\w*|malfunction\w*|guasto\w*|allarme\w*|averia\w*|fallo\w*|fehler\w*|storung\w*|panne\w*|defaut\w*)\b"),
    re.compile(r"\b(?:nao\s+(?:funciona|inicia|parte|liga|abre|fecha|responde|move|movimenta|comunica|refrigera|aquece|pressuriza|atinge|detecta|reconhece|alimenta|descarrega|carrega|corta|gira)|loss\s+of|perda\s+de|excess\w*|insuficient\w*|insufficient\w*|overheat\w*|sobreaquec\w*|vazamento\w*|leak\w*|vibracao\w*|vibration\w*|travamento\w*|jam\w*|colisao\w*|collision\w*|desgaste\w*|wear\w*|low|high|below|above|out\s+of\s+range|fora\s+de\s+faixa|fora\s+de\s+tolerancia|sem\s+sinal|no\s+signal|not\s+detected|overcurrent|undervoltage|overvoltage)\b"),
)
_FAILURE_STRUCTURAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:troubleshoot\w*|diagnostic\w*|diagnostico\w*|problem(?:s)?|problema(?:s)?|possiveis\s+causas|possible\s+causes|cause(?:s)?|causa(?:s)?|solution(?:s)?|solucao(?:es)?|remed(?:y|ies)|corrective\s+action|azione\s+correttiva|soluzione|solucion|ursache|losung)\b"),
    re.compile(r"\b(?:alarm\s+list|alarm\s+table|fault\s+code|error\s+code|lista\s+de\s+alarmes|tabela\s+de\s+alarmes|table\s+des?\s+alarmes?|tabella\s+allarmi|alarmliste|planilha\s+\d+)\b"),
)
# Safety-system patterns: components and conditions described in installation/technical
# manuals that are inherently relevant for FTA even without explicit failure vocabulary.
_SAFETY_SYSTEM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:emergenc\w*|seguranca\w*|safety\w*|risco\w*|perigo\w*|hazard\w*|protecao\w*|protection\w*|norma\s+tecnic\w*|normas\s+de\s+seguranca)\b"),
    re.compile(r"\b(?:freio\w*|brake\w*|limitador\w*|governor\w*|antiqueda\w*|anti.queda\w*|paraqueda\w*|parachute\w*)\b"),
    re.compile(r"\b(?:intertravamento\w*|enclavamento\w*|bloqueio\w*|lockout\w*|sobrecarga\w*|overload\w*|ruptura\w*|fratura\w*|curto.circuito\w*)\b"),
    re.compile(r"\b(?:fim\s+de\s+curso|limit\s+switch|contato\s+de\s+seguranca|nobreak\w*|no.break\w*|redundanc\w*|backup\w*)\b"),
)
_FAILURE_TECHNICAL_CODE_RE = re.compile(r"(?<!\w)[A-Z]{0,4}[- ]?\d{3,7}(?!\w)", flags=re.IGNORECASE)

ALARM_MATCH_STOPWORDS = FAILURE_TITLE_STOPWORDS | {
    "alarme", "alarm", "codigo", "code", "avaria", "anomalia", "condicao", "condição",
    "ativo", "ativa", "inativo", "inativa", "maquina", "máquina", "equipamento",
    "controle", "control", "plc", "cnc", "sistema", "system", "ciclo", "modo",
    "sensor", "laser", "unidade", "grupo", "geral", "generico", "genérico",
}

PLATFORM_CATEGORY_TERMS = {
    "data ingest", "ingest", "rag", "qdrant", "bedrock", "claude", "api",
    "documento", "manual", "arquivo", "frontend", "backend", "pipeline",
}

NON_FAILURE_TITLE_PREFIXES = (
    "uso de ", "uso incorreto ", "utilizacao de ", "utilização de ",
    "esmagamento de ", "risco de ", "exposicao a ", "exposição a ",
    "pecas com alta temperatura", "peças com alta temperatura",
)

ALLOWED_SEVERITIES = {"baixa", "media", "alta", "critica"}
DEFAULT_CATEGORY = "Geral"
DEFAULT_SEVERITY = "media"
SEVERITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("critica", ("critic", "emergencia", "risco grave", "incendio", "choque", "perda total", "parada total")),
    ("alta", ("alto risco", "trip", "desarma", "parada", "falha grave", "sobrecarga", "queima", "travado")),
    ("media", ("instabilidade", "alerta", "alarme", "intermitente", "queda", "desvio", "aumento")),
    ("baixa", ("leve", "pequeno", "ruido", "tendencia", "desgaste inicial", "ajuste fino")),
)


def _normalize_text(value: str) -> str:
    base = unicodedata.normalize("NFKD", value or "")
    no_accents = "".join(char for char in base if not unicodedata.combining(char))
    no_accents = no_accents.lower()
    no_accents = re.sub(r"[^\w\s/-]", " ", no_accents)
    no_accents = re.sub(r"\s+", " ", no_accents).strip()
    return no_accents


def _normalize_source_pages(value: Any) -> list[int]:
    raw_values = value if isinstance(value, list) else [value]
    pages: list[int] = []
    seen: set[int] = set()

    for raw_value in raw_values:
        if raw_value is None or raw_value == "":
            continue

        if isinstance(raw_value, int):
            candidates = [raw_value]
        else:
            candidates = [int(match) for match in re.findall(r"\d+", str(raw_value))]

        for page in candidates:
            if page <= 0 or page in seen:
                continue
            seen.add(page)
            pages.append(page)

    return sorted(pages)


def _normalize_alarm_codes(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    codes: list[str] = []
    seen: set[str] = set()

    for raw_value in raw_values:
        safe_value = str(raw_value or "").strip()
        if not safe_value:
            continue

        for candidate in re.split(r"[,;|\n]+", safe_value):
            code = re.sub(r"\s+", " ", candidate).strip(" -•\t\r\n")
            if not code or len(code) > 48:
                continue
            key = _normalize_text(code)
            if not key or key in seen:
                continue
            seen.add(key)
            codes.append(code)

    return codes


def _is_circular_cause(failure_title: str, cause_text: str) -> bool:
    title = _normalize_text(failure_title)
    cause = _normalize_text(cause_text)
    if not title or not cause:
        return False
    if title == cause:
        return True
    if len(title) >= 12 and len(cause) >= 12 and (title in cause or cause in title):
        return True

    title_tokens = {token for token in title.split() if len(token) >= 4}
    cause_tokens = {token for token in cause.split() if len(token) >= 4}
    if not title_tokens or not cause_tokens:
        return False

    shared_tokens = title_tokens.intersection(cause_tokens)
    overlap = len(shared_tokens) / max(1, min(len(title_tokens), len(cause_tokens)))

    # Não descartar uma causa mais específica apenas porque ela reutiliza o nome
    # do subsistema/falha. Se a causa introduz um termo técnico novo, tratamos como
    # informação adicional e deixamos a decisão final para o prompt de evidência.
    novel_cause_tokens = cause_tokens.difference(title_tokens)
    return overlap >= 0.85 and not novel_cause_tokens


def _is_forbidden_cause(cause_text: str) -> bool:
    """Reject placeholders and non-causes without inventing a replacement.

    These values are common LLM fallbacks when the source has an action but no
    stated cause. They must never be persisted as FTA causes. The list is generic
    and multilingual; it contains no equipment/manufacturer-specific vocabulary.
    """
    normalized = _normalize_text(cause_text)
    if not normalized or len(normalized) < 3:
        return True

    exact = {
        "diagnostico geral", "diagnostico", "general diagnosis", "general diagnostic",
        "causa desconhecida", "unknown cause", "causa nao informada", "causa nao informado",
        "nao informado", "nao informada", "not informed", "not specified", "unspecified",
        "a determinar", "a ser determinado", "to be determined", "tbd", "n a", "na",
        "indefinido", "indefinida", "desconhecido", "desconhecida",
        "causa geral", "general cause", "acao geral", "acoes gerais",
    }
    if normalized in exact:
        return True

    placeholder_patterns = (
        r"^causa\s+(?:ainda\s+)?(?:nao\s+)?(?:determinad[ao]|identificad[ao]|conhecid[ao])$",
        r"^(?:sem|nenhuma)\s+causa\s+(?:informada|identificada|definida)$",
        r"^(?:verificar|checar|inspecionar)\s+(?:o|a|os|as)?\s*(?:sistema|equipamento|componente)?$",
    )
    return any(re.fullmatch(pattern, normalized) for pattern in placeholder_patterns)


def _looks_like_prohibition_title(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False

    technical_non_function_patterns = (
        "nao parte", "nao inicia", "nao liga", "nao abre", "nao fecha", "nao responde",
        "nao move", "nao movimenta", "nao comunica", "nao refrigera", "nao aquece",
        "nao pressuriza", "nao atinge", "nao detecta", "nao reconhece", "nao alimenta",
        "nao descarrega", "nao carrega", "nao corta", "nao gira", "nao funciona",
    )
    if any(pattern in normalized for pattern in technical_non_function_patterns):
        return False

    prohibition_patterns = (
        "proibido ", "proibida ", "nunca ", "nao remover ", "nao retirar ", "nao abrir ",
        "nao tocar ", "nao usar ", "nao utilizar ", "nao operar ", "nao acessar ",
        "nao instalar ", "nao transportar ", "nao levantar ", "nao icar ", "nao desconectar ",
        "deve ser evitado", "deve ser evitada",
    )
    return any(pattern in f"{normalized} " for pattern in prohibition_patterns)


def _looks_like_non_failure_record(item: dict[str, Any]) -> bool:
    """Reject obvious usage/safety conditions without a technical anomaly."""
    title = _normalize_text(str(item.get("title") or ""))
    if not title:
        return True

    technical_terms = (
        "falha", "erro", "alarme", "avaria", "defeito", "anomalia", "intervencao",
        "intervenção", "paragem", "parada", "insuficiente", "perda", "quebra",
        "travamento", "travado", "desalinhamento", "vazamento", "entupido",
        "nao funciona", "não funciona", "nao ativo", "não ativo", "nao atingida",
        "não atingida", "colisao", "colisão", "desgaste", "superaquecimento",
    )
    if any(term in title for term in (_normalize_text(value) for value in technical_terms)):
        return False

    return any(title.startswith(_normalize_text(prefix)) for prefix in NON_FAILURE_TITLE_PREFIXES)


def _batch_page_numbers(batch_text: str) -> set[int]:
    pages: set[int] = set()
    for match in re.finditer(r"\[(?:Página|Pagina)\s+(\d+)[^\]]*\]", batch_text, flags=re.IGNORECASE):
        pages.add(int(match.group(1)))
    for match in re.finditer(r"\[(?:Páginas|Paginas)\s+(\d+)\s*-\s*(\d+)[^\]]*\]", batch_text, flags=re.IGNORECASE):
        start = int(match.group(1))
        end = int(match.group(2))
        if 0 < start <= end and end - start <= 500:
            pages.update(range(start, end + 1))
    return pages


def _split_batch_page_blocks(batch_text: str) -> list[tuple[int, str]]:
    pattern = re.compile(
        r"\[(?:Página|Pagina)\s+(\d+)(?:\s*\|[^\]]*)?\]\s*",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(batch_text or ""))
    blocks: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(batch_text)
        try:
            page = int(match.group(1))
        except Exception:
            continue
        text = str(batch_text[start:end] or "").strip()
        if page > 0 and text:
            blocks.append((page, text))
    return blocks


def _code_regex(code: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in re.split(r"\s+", str(code or "").strip()) if part]
    if not parts:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", flags=re.IGNORECASE)


def _looks_like_reference_not_alarm(code: str) -> bool:
    safe = re.sub(r"^[-–—]+", "", str(code or "").strip())
    if not safe:
        return True

    # Diagnostic/readout register identifiers are measurements, not alarm codes.
    # This explicitly blocks common CNC notations such as DGN 300 / DIAG300.
    if re.fullmatch(r"(?:DGN|DIAG|DIAGNOSIS|DIAGNOSTIC)[- ]?\d{1,6}", safe, flags=re.IGNORECASE):
        return True

    # Common terminal/option/schematic references. They are useful evidence, but
    # they are not alarm identifiers unless a real alarm table explicitly says so.
    if re.fullmatch(r"[XO]\d+(?:[- ]\d+){1,3}", safe, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[A-Z]\d{1,4}[- ]\d{1,3}", safe, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[IQ]\d{1,4}\.\d{1,3}", safe, flags=re.IGNORECASE):
        return True

    # IEC 81346 / industrial schematic designations. Prefixes can appear with a
    # leading dash in extracted PDFs (e.g. -YV452, -SQ214), so the dash is stripped
    # above before validation. Keep this list deliberately limited to reference
    # designators that are not fault families.
    if re.fullmatch(
        r"(?:YV|SQ|KA|KM|QF|HL|SA|SB|FU|FR|KT|KQ|XS|XT|XP|X|M|Q|S|B)[- ]?\d{1,6}(?:[./-]\d{1,4})?",
        safe,
        flags=re.IGNORECASE,
    ):
        return True
    if re.fullmatch(r"(?:I|Q|AI|AQ|DI|DO)\d{1,5}(?:\.\d{1,4})+", safe, flags=re.IGNORECASE):
        return True
    return False


def _alarm_table_section_kind(value: str) -> str | None:
    """Classifica apenas cabeçalhos explícitos de tabelas de alarme/mensagem.

    Alguns fabricantes chamam a coluna de uma tabela de mensagens de ``N° Alarme``.
    Portanto, o nome da coluna isolado não é suficiente para classificar o bloco
    como uma tabela de alarmes. A seção/tabela explícita tem precedência.
    """
    normalized = _normalize_text(value)
    if not normalized:
        return None

    if any(_normalize_text(term) in normalized for term in ALARM_MESSAGE_SECTION_TERMS):
        return "message"
    if "alarmes iso" in normalized or "iso alarms" in normalized:
        return "alarm_iso"
    if (
        "alarmes plc" in normalized
        or "plc alarms" in normalized
        or "alarm plc" in normalized
    ):
        return "alarm_plc"
    if any(_normalize_text(term) in normalized for term in ALARM_TABLE_TERMS + (
        "alarm table",
    )):
        return "alarm_generic"
    return None


def _is_alarm_table_context(value: str) -> bool:
    kind = _alarm_table_section_kind(value)
    return bool(kind and kind.startswith("alarm"))


def _is_plausible_alarm_code(code: str, *, table_context: bool) -> bool:
    safe = str(code or "").strip()
    if not safe or len(safe) > 32 or _looks_like_reference_not_alarm(safe):
        return False

    # Pure numeric codes are common in PLC/CNC alarm tables. Three-digit codes
    # are accepted only inside an explicit alarm table; elsewhere require >=4.
    if re.fullmatch(r"\d{3,9}", safe):
        return table_context or len(safe) >= 4

    # Generic alphanumeric fault/error codes such as A1234 or E12001.
    if re.fullmatch(r"[A-Z]{1,3}[- ]?\d{3,9}", safe, flags=re.IGNORECASE):
        return True

    # Decimal-looking cross references (123.6 / 136.2) are intentionally not
    # accepted outside a real alarm table because electrical drawings use them
    # heavily as page/column references.
    if table_context and re.fullmatch(r"\d{3,9}[./-]\d{1,5}", safe):
        return True

    return False


def _alarm_row_codes_from_text(text: str, *, table_context: bool) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    safe_text = str(text or "")

    # Explicit prose form. Require an alarm-like code, but do not accept short
    # decimal cross references in schematic drawings.
    direct_pattern = re.compile(
        r"(?:n[º°o]?\.?\s*(?:do\s*)?)?"
        r"(?:alarme|alarm|fault\s*code|error\s*code|c[oó]digo\s*(?:de\s*)?(?:alarme|erro|falha))"
        r"\s*(?:n[º°o]?\.?\s*)?[:#=\-]?\s*"
        r"([A-Z]{0,3}\d{3,9}(?:[./-]\d{1,5})?)",
        flags=re.IGNORECASE,
    )
    for match in direct_pattern.finditer(safe_text):
        code = re.sub(r"\s+", " ", match.group(1)).strip()
        key = _normalize_text(code)
        if (
            code
            and key
            and key not in seen
            and _is_plausible_alarm_code(code, table_context=table_context)
        ):
            seen.add(key)
            codes.append(code)

    if not table_context:
        return codes

    # Alarm tables extracted from PDFs frequently put the code on its own line
    # and the description on subsequent lines. Scan line starts first.
    line_pattern = re.compile(
        r"^\s*([A-Z]{0,3}\d{3,9}(?:[/-]\d{1,5})?)(?=\s|$|[|;:])\s*(.*)$",
        flags=re.IGNORECASE,
    )
    for raw_line in safe_text.splitlines():
        match = line_pattern.match(raw_line)
        if not match:
            continue
        code = re.sub(r"\s+", " ", match.group(1)).strip()
        key = _normalize_text(code)
        if (
            code
            and key
            and key not in seen
            and _is_plausible_alarm_code(code, table_context=True)
        ):
            seen.add(key)
            codes.append(code)

    # Some chunkers flatten line breaks. In a confirmed table context, accept
    # standalone 4-9 digit/alphanumeric codes from the block and use surrounding
    # text as the row evidence. Hyphenated logical pages such as 4-303 and signal
    # references such as I50.1 are excluded by the boundaries/pattern.
    inline_pattern = re.compile(
        r"(?<![\w./-])([A-Z]{0,3}\d{4,9})(?![\w./-])",
        flags=re.IGNORECASE,
    )
    for match in inline_pattern.finditer(safe_text):
        code = match.group(1).strip()
        key = _normalize_text(code)
        if (
            code
            and key
            and key not in seen
            and _is_plausible_alarm_code(code, table_context=True)
        ):
            seen.add(key)
            codes.append(code)

    return codes


def _alarm_row_label(code: str, text: str) -> str:
    """Extrai somente a coluna/trecho de DESCRICAO do alarme, sem explicacao/solucao."""
    safe = re.sub(r"\s+", " ", str(text or "")).strip()
    safe = re.sub(r"(?<=[A-Za-zÀ-ÿ])-\s+(?=[A-Za-zÀ-ÿ])", "", safe)
    safe = _code_regex(code).sub("", safe, count=1).strip(" -:;|")
    if not safe:
        return ""

    marker_pattern = re.compile(
        r"\b(?:indica|indicam|[ée]\s+gerad[oa]|[ée]\s+visualizad[oa]|significa|ocorre\s+quando|"
        r"this\s+alarm|indicates|generated\s+when|poss[ií]veis\s+causas|possible\s+causes|"
        r"poss[ií]vel\s+problema|possible\s+problem|erro\s+run[- ]?time|o\s+plc\s+n[aã]o|"
        r"the\s+plc\s+did|planilha\s+\d|visualiza[cç][aã]o[, ]|solu[cç][aã]o\b|solution\b)",
        flags=re.IGNORECASE,
    )
    match = marker_pattern.search(safe)
    if match and match.start() >= 3:
        safe = safe[:match.start()].strip(" -:;|")

    return safe[:220].strip()





def _extract_alarm_rows_from_batch(batch_text: str) -> list[dict[str, Any]]:
    """Extrai somente registros provenientes de seções reais de ALARME.

    Tabelas explicitamente identificadas como mensagens operacionais são ignoradas,
    mesmo quando reutilizam uma coluna chamada ``N° Alarme``. Isso preserva a
    distinção documental entre Alarmes PLC/ISO e Mensagens PLC.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str]] = set()
    blocks = _split_batch_page_blocks(batch_text)
    if not blocks and str(batch_text or "").strip():
        blocks = [(0, str(batch_text or ""))]

    for raw_page, block in blocks:
        page = raw_page if raw_page > 0 else None
        section_kind = _alarm_table_section_kind(block)
        if section_kind == "message":
            continue

        table_context = bool(section_kind and section_kind.startswith("alarm"))
        lines = block.splitlines()

        # Estrutura típica de tabela PDF: código em início de linha, seguido de
        # descrição/explicação/solução até o próximo código. Só habilitamos esta
        # leitura em seção de alarme explicitamente identificada.
        if table_context:
            code_line_indices: list[tuple[int, str]] = []
            line_pattern = re.compile(
                r"^\s*([A-Z]{0,3}\d{3,9}(?:[/-]\d{1,5})?)(?=\s|$|[|;:])\s*(.*)$",
                flags=re.IGNORECASE,
            )
            for index, raw_line in enumerate(lines):
                match = line_pattern.match(raw_line)
                if not match:
                    continue
                code = match.group(1).strip()
                if _is_plausible_alarm_code(code, table_context=True):
                    code_line_indices.append((index, code))

            for position, (line_index, code) in enumerate(code_line_indices):
                next_index = (
                    code_line_indices[position + 1][0]
                    if position + 1 < len(code_line_indices)
                    else min(len(lines), line_index + 14)
                )
                excerpt = re.sub(
                    r"\s+",
                    " ",
                    " ".join(lines[line_index:next_index]),
                ).strip()[:1400]
                key = (page, _normalize_text(code))
                if key not in seen:
                    seen.add(key)
                    rows.append({
                        "page": page,
                        "code": code,
                        "label": _alarm_row_label(code, excerpt),
                        "text": excerpt,
                        "source_kind": section_kind or "alarm_generic",
                    })

        # Fora de uma tabela explícita ainda aceitamos prosa que diga diretamente
        # "alarme 1234"/"fault code A1234". Em seção de mensagens isso já foi
        # bloqueado acima.
        codes = _alarm_row_codes_from_text(block, table_context=table_context)
        inline_positions: list[tuple[int, str]] = []
        for code in codes:
            match = _code_regex(code).search(block)
            if match:
                inline_positions.append((match.start(), code))
        inline_positions.sort()

        for index, (position, code) in enumerate(inline_positions):
            key = (page, _normalize_text(code))
            if key in seen:
                continue
            next_position = (
                inline_positions[index + 1][0]
                if index + 1 < len(inline_positions)
                else min(len(block), position + 1400)
            )
            excerpt = re.sub(r"\s+", " ", block[position:next_position]).strip()[:1400]
            seen.add(key)
            rows.append({
                "page": page,
                "code": code,
                "label": _alarm_row_label(code, excerpt),
                "text": excerpt,
                "source_kind": section_kind or "explicit_alarm",
            })

    return rows


def _validate_alarm_codes_against_batch(value: Any, batch_text: str) -> list[str]:
    available = {
        _normalize_text(row["code"]): row["code"]
        for row in _extract_alarm_rows_from_batch(batch_text)
        if row.get("code")
    }
    if not available:
        return []

    output: list[str] = []
    for code in _normalize_alarm_codes(value):
        key = _normalize_text(code)
        if key in available and available[key] not in output:
            output.append(available[key])
    return output


def _alarm_code_family(code: str) -> tuple[str, int]:
    safe = str(code or "").strip().upper().replace(" ", "")
    match = re.fullmatch(r"([A-Z]{0,3})[- ]?(\d{3,9})", safe)
    if not match:
        return ("other", 0)
    return (match.group(1) or "numeric", len(match.group(2)))


def _collect_document_alarm_rows(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build trusted alarm rows from the current document.

    PDF table extraction can place parameter/component references on their own
    lines inside an alarm explanation. We therefore infer the dominant alarm-code
    families across the document and discard sparse outlier formats. A manual
    whose real alarm table uses 4-digit codes still keeps them because that
    family becomes dominant for that document.
    """
    by_code: dict[str, dict[str, Any]] = {}

    # Agrupa chunks pela página antes de classificar a seção. Em PDFs extensos o
    # cabeçalho "Alarmes PLC" pode cair em um chunk e as linhas da tabela em outro
    # chunk da mesma página. A visão agregada mantém a classificação correta sem
    # propagar estado de uma página para a seção seguinte.
    page_texts: dict[int | None, list[str]] = {}
    for chunk in chunks:
        chunk_text = str(chunk.get("text") or "").strip()
        if not chunk_text:
            continue
        raw_page = chunk.get("page_start")
        page = int(raw_page) if isinstance(raw_page, int) and raw_page > 0 else None
        bucket = page_texts.setdefault(page, [])
        normalized_chunk = _normalize_text(chunk_text)
        if normalized_chunk and all(_normalize_text(existing) != normalized_chunk for existing in bucket):
            bucket.append(chunk_text)

    for page, texts in sorted(
        page_texts.items(),
        key=lambda value: value[0] if isinstance(value[0], int) else 10**9,
    ):
        page_text = "\n".join(texts)
        block = f"[Página {page}]\n{page_text}" if page else page_text
        for row in _extract_alarm_rows_from_batch(block):
            code = str(row.get("code") or "").strip()
            key = _normalize_text(code)
            if not key:
                continue
            candidate = {
                "code": code,
                "page": row.get("page"),
                "label": str(row.get("label") or "").strip(),
                "text": str(row.get("text") or "").strip(),
                "source_kind": str(row.get("source_kind") or "explicit_alarm"),
            }
            current = by_code.get(key)
            candidate_is_table = str(candidate.get("source_kind") or "").startswith("alarm_")
            current_is_table = bool(
                current and str(current.get("source_kind") or "").startswith("alarm_")
            )
            if (
                current is None
                or (candidate_is_table and not current_is_table)
                or (candidate_is_table == current_is_table and len(candidate["text"]) > len(str(current.get("text") or "")))
            ):
                by_code[key] = candidate

    if not by_code:
        return []

    # Quando o documento fornece uma tabela explícita de alarmes, ela é a fonte
    # autoritativa. Menções do tipo "retardo do alarme 700025" em páginas de
    # parâmetros continuam úteis como evidência geral, mas não substituem a linha
    # oficial Descrição/Explicação/Solução da tabela.
    table_rows = {
        key: row
        for key, row in by_code.items()
        if str(row.get("source_kind") or "").startswith("alarm_")
    }
    if len(table_rows) >= 3:
        by_code = table_rows

    family_counts: dict[tuple[str, int], int] = {}
    for row in by_code.values():
        family = _alarm_code_family(str(row.get("code") or ""))
        if family[1] <= 0:
            continue
        family_counts[family] = family_counts.get(family, 0) + 1

    max_family_count = max(family_counts.values(), default=0)
    if max_family_count < 3:
        accepted_families = set(family_counts)
    else:
        family_threshold = max(3, int(max_family_count * 0.12))
        accepted_families = {
            family
            for family, count in family_counts.items()
            if count >= family_threshold
        }

    filtered: list[dict[str, Any]] = []
    for row in by_code.values():
        family = _alarm_code_family(str(row.get("code") or ""))
        if family not in accepted_families:
            continue
        filtered.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        page = row.get("page")
        return (int(page) if isinstance(page, int) else 10**9, str(row.get("code") or ""))

    return sorted(filtered, key=sort_key)


def _validate_alarm_codes_against_document(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trusted = {
        _normalize_text(str(row.get("code") or "")): row
        for row in alarm_rows
        if str(row.get("code") or "").strip()
    }
    if not trusted:
        for item in items:
            item["alarm_codes"] = []
        return items

    for item in items:
        valid_codes: list[str] = []
        pages = set(_normalize_source_pages(item.get("source_pages")))
        for raw_code in _normalize_alarm_codes(item.get("alarm_codes")):
            row = trusted.get(_normalize_text(raw_code))
            if row is None:
                continue
            canonical = str(row.get("code") or "").strip()
            if canonical and canonical not in valid_codes:
                valid_codes.append(canonical)
            if isinstance(row.get("page"), int) and int(row["page"]) > 0:
                pages.add(int(row["page"]))
        item["alarm_codes"] = valid_codes
        item["source_pages"] = sorted(pages)[:4]
    return items


def _significant_tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) >= 4 and token not in FAILURE_TITLE_STOPWORDS
    }


def _alarm_rows_for_item(item: dict[str, Any], batch_text: str) -> list[dict[str, Any]]:
    rows = _extract_alarm_rows_from_batch(batch_text)
    if not rows:
        return []

    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    causes = " ".join(_split_text_values(item.get("causes")))
    query_tokens = _significant_tokens(" ".join([title, description, causes]))
    explicit_codes = {
        _normalize_text(code)
        for code in _normalize_alarm_codes(item.get("alarm_codes"))
    }

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        row_text = str(row.get("text") or "")
        row_tokens = _significant_tokens(row_text)
        score = len(query_tokens.intersection(row_tokens))
        code_key = _normalize_text(str(row.get("code") or ""))
        if code_key and code_key in explicit_codes:
            score += 12
        if title and _normalize_text(title) in _normalize_text(row_text):
            score += 8
        if score >= 3:
            scored.append((score, row))

    if not scored:
        return []
    best = max(score for score, _ in scored)
    return [row for score, row in scored if score >= max(3, best - 1)][:6]


def _infer_source_pages_from_batch(item: dict[str, Any], batch_text: str) -> list[int]:
    alarm_rows = _alarm_rows_for_item(item, batch_text)
    alarm_pages = sorted({int(row["page"]) for row in alarm_rows if row.get("page")})
    if alarm_pages:
        return alarm_pages[:4]

    blocks = _split_batch_page_blocks(batch_text)
    if not blocks:
        return []
    if len(blocks) == 1:
        return [blocks[0][0]]

    title = str(item.get("title") or "")
    title_tokens = _significant_tokens(title)
    detail_tokens = _significant_tokens(
        " ".join([
            str(item.get("description") or ""),
            " ".join(_split_text_values(item.get("causes"))),
            " ".join(_split_text_values(item.get("effects"))),
        ])
    )

    scored: list[tuple[int, int]] = []
    for page, text in blocks:
        normalized_text = _normalize_text(text)
        text_tokens = _significant_tokens(text)
        score = 0
        if title and len(_normalize_text(title)) >= 10 and _normalize_text(title) in normalized_text:
            score += 10
        score += 3 * len(title_tokens.intersection(text_tokens))
        score += len(detail_tokens.intersection(text_tokens))
        scored.append((score, page))

    best_score = max((score for score, _ in scored), default=0)
    if best_score < 5:
        return []
    return sorted({page for score, page in scored if score >= max(5, best_score - 2)})[:4]


def _merge_failure_mode_records(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for field_name in [
        "description", "category", "severity", "probable_cause", "symptoms", "impact",
        "recommendation", "source_doc_id", "source_doc_filename",
    ]:
        if not target.get(field_name) and source.get(field_name):
            target[field_name] = source[field_name]

    target["tags"] = _split_tags(list(target.get("tags", [])) + list(source.get("tags", [])))
    target["source_pages"] = sorted(set(
        _normalize_source_pages(target.get("source_pages"))
        + _normalize_source_pages(source.get("source_pages"))
    ))
    target["alarm_codes"] = _normalize_alarm_codes(
        list(target.get("alarm_codes", [])) + list(source.get("alarm_codes", []))
    )
    evidence_by_code: dict[str, dict[str, Any]] = {}
    for raw in list(target.get("alarm_evidence", [])) + list(source.get("alarm_evidence", [])):
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        if not code:
            continue
        current = evidence_by_code.get(code)
        if current is None or int(raw.get("match_score") or 0) > int(current.get("match_score") or 0):
            evidence_by_code[code] = dict(raw)
    target["alarm_evidence"] = [evidence_by_code[code] for code in target["alarm_codes"] if code in evidence_by_code]
    target["cause_groups"] = _merge_cause_groups(
        list(target.get("cause_groups", [])), list(source.get("cause_groups", []))
    )
    target["causes"] = _merge_unique_text_lists(target.get("causes", []), source.get("causes", []))
    target["effects"] = _merge_unique_text_lists(target.get("effects", []), source.get("effects", []))
    target["actions"] = _merge_unique_text_lists(target.get("actions", []), source.get("actions", []))

    group_causes, group_effects, group_actions = _collapse_cause_groups(target.get("cause_groups", []))
    target["causes"] = _merge_unique_text_lists(target.get("causes", []), group_causes)
    target["effects"] = _merge_unique_text_lists(target.get("effects", []), group_effects)
    target["actions"] = _merge_unique_text_lists(target.get("actions", []), group_actions)
    target["probable_cause"] = str(target.get("probable_cause") or "").strip() or _format_text_list(target["causes"])
    target["impact"] = str(target.get("impact") or "").strip() or _format_text_list(target["effects"])
    target["recommendation"] = str(target.get("recommendation") or "").strip() or _format_text_list(target["actions"])
    target["symptoms"] = str(target.get("symptoms") or "").strip() or ", ".join(target["tags"])
    target["updated_at"] = max(int(target.get("updated_at") or 0), int(source.get("updated_at") or 0))
    return target


def _failure_pair_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_title = _normalize_text(str(left.get("title") or ""))
    right_title = _normalize_text(str(right.get("title") or ""))
    if not left_title or not right_title:
        return 0.0
    if left_title == right_title:
        return 1.0

    left_tokens = _significant_tokens(left_title)
    right_tokens = _significant_tokens(right_title)
    union = left_tokens.union(right_tokens)
    jaccard = len(left_tokens.intersection(right_tokens)) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_title, right_title).ratio()

    left_codes = {_normalize_text(v) for v in _normalize_alarm_codes(left.get("alarm_codes"))}
    right_codes = {_normalize_text(v) for v in _normalize_alarm_codes(right.get("alarm_codes"))}
    if left_codes and right_codes and left_codes.intersection(right_codes):
        return max(0.92, sequence, jaccard)

    return max(sequence, jaccard)


def _canonical_failure_title_tokens(item: dict[str, Any]) -> frozenset[str]:
    title = re.sub(r"[-/]+", " ", str(item.get("title") or ""))
    return frozenset(_significant_tokens(title))


def _safe_title_containment_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
    left_tokens: frozenset[str],
    right_tokens: frozenset[str],
    sequence: float,
) -> bool:
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    if not (left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)):
        return False

    shorter = left_tokens if len(left_tokens) <= len(right_tokens) else right_tokens
    # Two-token titles are often too generic (e.g. "sensor capacitivo"). Keep the
    # previous conservative threshold for them. Three or more exact subset tokens
    # are a strong containment signal even when the longer title adds context such
    # as "durante troca de barra".
    if len(shorter) <= 2 and sequence < 0.80:
        return False

    left_title = str(left.get("title") or "")
    right_title = str(right.get("title") or "")
    left_anomalies = _delivery_anomaly_signatures(left_title).difference({"generic_fault"})
    right_anomalies = _delivery_anomaly_signatures(right_title).difference({"generic_fault"})
    if left_anomalies and right_anomalies and left_anomalies.isdisjoint(right_anomalies):
        return False

    left_context = _delivery_context_signatures(left_title)
    right_context = _delivery_context_signatures(right_title)
    if left_context and right_context and left_context.isdisjoint(right_context):
        return False

    left_ordinals = _delivery_component_ordinals(left_title)
    right_ordinals = _delivery_component_ordinals(right_title)
    for family in set(left_ordinals).intersection(right_ordinals):
        if left_ordinals[family] and right_ordinals[family] and left_ordinals[family].isdisjoint(right_ordinals[family]):
            return False

    return len(shorter) >= 3 or sequence >= 0.80


def _deterministic_deduplicate_failure_modes(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mescla apenas duplicatas lexicais fortes; alarme nunca é critério de merge."""
    output: list[dict[str, Any]] = []

    for item in items:
        item_tokens = _canonical_failure_title_tokens(item)
        item_title = _normalize_text(str(item.get("title") or ""))
        merged = False

        for index, current in enumerate(output):
            current_tokens = _canonical_failure_title_tokens(current)
            current_title = _normalize_text(str(current.get("title") or ""))
            sequence = SequenceMatcher(None, item_title, current_title).ratio()

            exact_title = bool(item_title and item_title == current_title)
            same_token_signature = len(item_tokens) >= 2 and item_tokens == current_tokens
            title_containment = _safe_title_containment_duplicate(
                item,
                current,
                item_tokens,
                current_tokens,
                sequence,
            )

            if not (exact_title or same_token_signature or title_containment):
                continue

            canonical = dict(current)
            if len(str(item.get("title") or "")) < len(str(current.get("title") or "")):
                canonical["title"] = item.get("title")
            output[index] = _merge_failure_mode_records(canonical, item)
            merged = True
            break

        if not merged:
            output.append(dict(item))

    return output

def _semantic_deduplicate_failure_modes(runtime, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Revisor semântico conservador: nunca usa alarm_codes para aproximar modos."""
    if len(items) < 2:
        return items

    candidates: list[dict[str, Any]] = []
    allowed_pairs: set[frozenset[str]] = set()

    for index, left in enumerate(items):
        left_title = _normalize_text(str(left.get("title") or ""))
        left_tokens = _canonical_failure_title_tokens(left)
        if not left_title:
            continue
        for right in items[index + 1:]:
            right_title = _normalize_text(str(right.get("title") or ""))
            right_tokens = _canonical_failure_title_tokens(right)
            if not right_title:
                continue

            sequence = SequenceMatcher(None, left_title, right_title).ratio()
            union_tokens = left_tokens.union(right_tokens)
            jaccard = (
                len(left_tokens.intersection(right_tokens)) / len(union_tokens)
                if union_tokens else 0.0
            )
            exact = left_title == right_title
            same_signature = len(left_tokens) >= 2 and left_tokens == right_tokens
            containment = (
                len(left_tokens) >= 2
                and len(right_tokens) >= 2
                and (left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens))
                and sequence >= 0.74
            )
            strong_overlap = jaccard >= 0.68 and sequence >= 0.68

            if not (exact or same_signature or containment or strong_overlap):
                continue

            left_id = str(left.get("mode_id") or "").strip()
            right_id = str(right.get("mode_id") or "").strip()
            if not left_id or not right_id:
                continue
            allowed_pairs.add(frozenset((left_id, right_id)))
            candidates.append({
                "a": {
                    "mode_id": left_id,
                    "title": left.get("title"),
                    "description": str(left.get("description") or "")[:260],
                    "causes": _split_text_values(left.get("causes"))[:3],
                    "effects": _split_text_values(left.get("effects"))[:3],
                },
                "b": {
                    "mode_id": right_id,
                    "title": right.get("title"),
                    "description": str(right.get("description") or "")[:260],
                    "causes": _split_text_values(right.get("causes"))[:3],
                    "effects": _split_text_values(right.get("effects"))[:3],
                },
                "title_similarity": round(sequence, 3),
                "token_jaccard": round(jaccard, 3),
            })

    if not candidates:
        return items

    strict_system = """Você confirma se dois registros são realmente o MESMO modo de falha técnico.
Retorne SOMENTE JSON: {"merge_pairs":[["mode_id_a","mode_id_b"]]}.
Mescle apenas sinônimos/reformulações da mesma condição técnica, do mesmo componente e mesmo mecanismo de falha.
NÃO mescle porque pertencem ao mesmo subsistema, possuem efeitos parecidos, ações parecidas, ou aparecem próximos no manual.
Não use códigos de alarme como justificativa. Na dúvida, não mescle.
"""

    confirmed_pairs: list[tuple[str, str]] = []
    for start in range(0, len(candidates), 30):
        batch = candidates[start:start + 30]
        try:
            response = chat_claude(
                runtime,
                system=strict_system,
                messages=[{"role": "user", "content": json.dumps({"pairs": batch}, ensure_ascii=False)}],
                max_tokens=1100,
            )
            parsed = _extract_json_object(response)
        except Exception:
            continue
        raw_pairs = parsed.get("merge_pairs")
        if not isinstance(raw_pairs, list):
            continue
        for pair in raw_pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            left_id = str(pair[0] or "").strip()
            right_id = str(pair[1] or "").strip()
            if left_id and right_id and frozenset((left_id, right_id)) in allowed_pairs:
                confirmed_pairs.append((left_id, right_id))

    if not confirmed_pairs:
        return items

    parent: dict[str, str] = {}
    by_id = {str(item.get("mode_id") or ""): item for item in items}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a: str, b: str) -> None:
        if a not in by_id or b not in by_id:
            return
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for left_id, right_id in confirmed_pairs:
        union(left_id, right_id)

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        mode_id = str(item.get("mode_id") or "")
        groups.setdefault(find(mode_id), []).append(item)

    output: list[dict[str, Any]] = []
    for group in groups.values():
        canonical = dict(group[0])
        best_title = min(
            (str(item.get("title") or "") for item in group if str(item.get("title") or "")),
            key=len,
            default=str(canonical.get("title") or ""),
        )
        canonical["title"] = best_title or canonical.get("title")
        for duplicate in group[1:]:
            canonical = _merge_failure_mode_records(canonical, duplicate)
        output.append(canonical)
    return output

def _review_failure_modes(runtime, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []

    # Primeiro elimina somente casos determinísticos de alta confiança. O filtro
    # existente continua valendo como camada adicional de compatibilidade.
    kept: list[dict[str, Any]] = []
    for item in items:
        if _looks_like_non_failure_record(item):
            continue
        if _delivery_non_failure_reason(item):
            continue
        kept.append(item)
    items = kept
    if not items:
        return []

    review_system = """Você revisa candidatos a MODO DE FALHA de um equipamento a partir do próprio manual técnico.
Retorne SOMENTE JSON válido no formato:
{"decisions":[{"mode_id":"...","classification":"technical_failure|operational_state|procedure_or_instruction|safety_hazard|human_health_effect|maintenance_only|normal_condition|catalog_or_parts_list","confidence":"high|medium|low"}]}.

Mantenha como technical_failure uma condição técnica anormal do equipamento, componente ou processo que represente perda/degradação de função, defeito, falha de sensor/atuador, posição não atingida, pressão/nível/fluxo inadequado, intertravamento causado por condição técnica anormal, ou defeito de qualidade diretamente causado pelo processo/equipamento.
NÃO classifique como falha: modo operacional normal/reservado, mensagem apenas instrucional, ação/procedimento, proibição, perigo ocupacional isolado, efeito à saúde humana, condição normal, ou texto apenas de manutenção preventiva.
Classifique como catalog_or_parts_list quando o candidato tiver sido criado apenas a partir de título de seção, catálogo/lista de peças, peças de reposição/spare parts/replacement parts, fabricante/tipo/código de componente, sem uma condição técnica anormal explícita. Não converta o nome de um sistema em "falha do sistema" apenas porque ele aparece como cabeçalho de uma lista de componentes.
Uma consequência de segurança pode existir dentro de uma falha técnica; não descarte a falha apenas porque ela possui efeito de segurança.
Na dúvida use confidence low.
"""

    compact = [
        {
            "mode_id": item.get("mode_id"),
            "title": item.get("title"),
            "category": item.get("category"),
            "description": str(item.get("description") or "")[:380],
            "causes": _split_text_values(item.get("causes"))[:4],
            "effects": _split_text_values(item.get("effects"))[:4],
            "actions": _split_text_values(item.get("actions"))[:3],
        }
        for item in items
    ]

    drop_ids: set[str] = set()
    valid_nontechnical = {
        "operational_state", "procedure_or_instruction", "safety_hazard",
        "human_health_effect", "maintenance_only", "normal_condition", "catalog_or_parts_list",
    }
    for start in range(0, len(compact), 24):
        batch = compact[start:start + 24]
        try:
            response = chat_claude(
                runtime,
                system=review_system,
                messages=[{"role": "user", "content": json.dumps({"items": batch}, ensure_ascii=False)}],
                max_tokens=1400,
            )
            parsed = _extract_json_object(response)
        except Exception:
            continue
        decisions = parsed.get("decisions")
        if not isinstance(decisions, list):
            continue
        allowed_ids = {str(entry.get("mode_id") or "") for entry in batch}
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            mode_id = str(decision.get("mode_id") or "").strip()
            classification = str(decision.get("classification") or "").strip().lower()
            confidence = str(decision.get("confidence") or "").strip().lower()
            if mode_id in allowed_ids and classification in valid_nontechnical and confidence == "high":
                drop_ids.add(mode_id)

    return [item for item in items if str(item.get("mode_id") or "") not in drop_ids]

def _sanitize_taxonomy_name(value: str) -> str:
    name = _to_title_case_label(str(value or "").strip().replace("_", " "))
    normalized = _normalize_text(name)
    if not normalized or normalized in PLATFORM_CATEGORY_TERMS:
        return ""
    if any(term == normalized for term in PLATFORM_CATEGORY_TERMS):
        return ""
    return name


def _extract_explicit_functional_categories(
    evidence_chunks: list[dict[str, Any]],
) -> list[str]:
    """Extrai grupos funcionais explícitos mesmo quando a tabela foi quebrada em chunks.

    A regra é genérica: procura marcadores de grupos funcionais no documento atual
    e agrega uma pequena janela de chunks vizinhos para reconstruir tabelas que o
    PDF/chunker separou. Nenhum fabricante/modelo é hardcoded.
    """
    markers = (
        "grupos funcionais", "grupos funcional", "functional groups", "functional group",
        "grupos de funcao", "grupos de função", "functional units", "grupos de funções",
    )
    marker_keys = {_normalize_text(marker) for marker in markers}
    candidates: list[str] = []
    seen: set[str] = set()

    chunks = [chunk for chunk in evidence_chunks if isinstance(chunk, dict)]
    windows: list[str] = []

    for index, chunk in enumerate(chunks):
        raw_text = str(chunk.get("text") or "")
        normalized = _normalize_text(raw_text)
        if not any(marker in normalized for marker in marker_keys):
            continue

        # Reconstrói a tabela com vizinhos próximos. O limite curto evita que uma
        # lista numerada distante seja confundida com a taxonomia funcional.
        start = max(0, index - 1)
        end = min(len(chunks), index + 6)
        parts: list[str] = []
        base_page = chunk.get("page_start")
        for neighbor in chunks[start:end]:
            page = neighbor.get("page_start")
            if isinstance(base_page, int) and isinstance(page, int) and abs(page - base_page) > 2:
                continue
            text = str(neighbor.get("text") or "").strip()
            if text:
                parts.append(text)
        if parts:
            windows.append("\n".join(parts))
        windows.append(raw_text)

    for raw_text in windows:
        normalized = _normalize_text(raw_text)
        marker_positions = [normalized.find(marker) for marker in marker_keys if marker in normalized]
        # Mantém o texto integral porque a normalização muda os offsets, mas a
        # presença do marcador já limita a janela aos chunks corretos.
        if not marker_positions:
            continue

        patterns = (
            re.compile(r"(?m)^\s*(\d{1,2})\s*\n\s*([^\n]{3,100}?)\s*\n\s*(\d+(?:\.\d+){1,4})\s*$"),
            re.compile(r"(?m)^\s*\d{1,2}\s+(.{3,100}?)\s+\d+(?:\.\d+){1,4}\s*$"),
        )

        raw_names: list[str] = []
        first = patterns[0]
        first_matches = list(first.finditer(raw_text))
        if first_matches:
            raw_names.extend(match.group(2) for match in first_matches)
        else:
            raw_names.extend(match.group(1) for match in patterns[1].finditer(raw_text))

        for raw_name in raw_names:
            name = _sanitize_taxonomy_name(raw_name)
            key = _normalize_text(name)
            if not name or not key or key in seen:
                continue
            if key in {"grupo", "grupos", "paragrafo", "referencia", "ref", "descricao"}:
                continue
            seen.add(key)
            candidates.append(name)

    return candidates[:18]

def _extract_explicit_functional_categories_robust(
    evidence_chunks: list[dict[str, Any]],
) -> list[str]:
    """Extrai uma lista/tabela explícita de grupos funcionais sem inventar taxonomia.

    Esta rotina é usada também pela reclassificação rápida da FTA existente. Ela
    exige um marcador documental de grupo funcional e uma sequência numerada
    coerente, evitando confundir índices, listas de alarmes ou componentes soltos.
    """
    initial = _extract_explicit_functional_categories(evidence_chunks)
    if len(initial) >= 4:
        return initial

    markers = {
        _normalize_text(value)
        for value in (
            "grupos funcionais", "grupo funcional", "functional groups",
            "functional group", "functional units", "gruppi funzionali",
            "grupos de função", "grupos de funcoes", "grupos de funções",
        )
    }
    chunks = [chunk for chunk in evidence_chunks if isinstance(chunk, dict)]
    if not chunks:
        return initial

    windows: list[str] = []
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "").strip()
        normalized = _normalize_text(text)
        if not text or not any(marker in normalized for marker in markers):
            continue
        base_page = chunk.get("page_start")
        parts: list[str] = []
        for neighbor in chunks[max(0, index - 2):min(len(chunks), index + 12)]:
            n_page = neighbor.get("page_start")
            if isinstance(base_page, int) and isinstance(n_page, int) and abs(n_page - base_page) > 3:
                continue
            n_text = str(neighbor.get("text") or "").strip()
            if n_text:
                parts.append(n_text)
        if parts:
            windows.append("\n".join(parts))

    # Captura linhas de tabela como "1 Carregador 1.2.1" e também listas
    # numeradas sem a coluna de parágrafo. Depois exige uma sequência crescente.
    candidates: list[tuple[int, str]] = []
    row_patterns = (
        re.compile(r"(?m)^\s*(\d{1,2})\s+([A-Za-zÀ-ÿ][^\n]{2,100}?)\s+(\d+(?:\.\d+){1,4})\s*$"),
        re.compile(r"(?m)^\s*(\d{1,2})\s*[\).:\-]?\s+([A-Za-zÀ-ÿ][^\n]{2,100}?)\s*$"),
    )
    for window in windows:
        local: list[tuple[int, str]] = []
        for pattern in row_patterns:
            for match in pattern.finditer(window):
                try:
                    number = int(match.group(1))
                except Exception:
                    continue
                raw_name = re.sub(r"\s+\d+(?:\.\d+){1,4}\s*$", "", match.group(2)).strip(" .:-")
                name = _sanitize_taxonomy_name(raw_name)
                key = _normalize_text(name)
                if not name or not key:
                    continue
                if any(bad in key for bad in ("planilha", "tabela", "figura", "pagina", "paragrafo", "referencia")):
                    continue
                local.append((number, name))
            if local:
                break
        if len(local) > len(candidates):
            candidates = local

    if not candidates:
        return initial

    by_number: dict[int, str] = {}
    for number, name in candidates:
        if 1 <= number <= 30 and number not in by_number:
            by_number[number] = name

    # Prefere a maior sequência iniciada em 1. Tabelas de grupos funcionais
    # normalmente usam a coluna Ref.; exigir sequência reduz falsos positivos.
    ordered: list[str] = []
    for number in range(1, 31):
        if number not in by_number:
            if number <= 4:
                ordered = []
            break
        ordered.append(by_number[number])

    if len(ordered) >= 4:
        return _unique_strings(ordered)[:18]
    return initial



def _explicit_taxonomy_marker_chunks(evidence_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seleciona a janela documental próxima do cabeçalho explícito de grupos funcionais."""
    markers = {
        _normalize_text(value)
        for value in (
            "grupos funcionais", "grupo funcional", "grupos funcio", "grupo funcio",
            "functional groups", "functional group", "functional units",
            "gruppi funzionali", "gruppo funzionale", "grupos de funções", "grupos de funcao",
        )
    }
    chunks = [
        chunk for chunk in evidence_chunks
        if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
    ]
    selected_indexes: set[int] = set()
    marker_pages: set[int] = set()
    for index, chunk in enumerate(chunks):
        normalized = _normalize_text(str(chunk.get("text") or ""))
        if not any(marker in normalized for marker in markers):
            continue
        selected_indexes.update(range(max(0, index - 2), min(len(chunks), index + 12)))
        page = chunk.get("page_start")
        if isinstance(page, int) and page > 0:
            marker_pages.update({page, page + 1, page + 2})

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks):
        page = chunk.get("page_start")
        if index not in selected_indexes and not (isinstance(page, int) and page in marker_pages):
            continue
        key = _normalize_text(str(chunk.get("content_hash") or str(chunk.get("text") or "")[:320]))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(chunk)
    return output[:120]


def _unique_failure_mode_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove chunks duplicados preservando ordem e a versão mais informativa.

    O finalizador combina chunks diretos do documento com resultados de busca dirigida.
    Esses caminhos podem devolver o mesmo trecho com IDs/metadados diferentes; por isso
    a deduplicação usa, nesta ordem, content_hash/point_id/id e uma assinatura textual
    estável por documento+página+conteúdo.
    """
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in chunks:
        if not isinstance(raw, dict):
            continue
        text = str(
            raw.get("text")
            or raw.get("text_excerpt")
            or raw.get("excerpt")
            or raw.get("content")
            or raw.get("chunk_text")
            or raw.get("raw_text")
            or ""
        ).strip()
        if not text:
            continue
        page = raw.get("page_start") or raw.get("page_number") or raw.get("page") or ""
        document_id = str(raw.get("document_id") or "").strip()
        stable_id = str(
            raw.get("content_hash")
            or raw.get("point_id")
            or raw.get("id")
            or ""
        ).strip()
        if stable_id:
            key = f"id:{stable_id}"
        else:
            key = "text:" + _normalize_text(f"{document_id}|{page}|{text[:1200]}")
        if not key or key in seen:
            continue
        seen.add(key)
        chunk = dict(raw)
        if not str(chunk.get("text") or "").strip():
            chunk["text"] = text
        if chunk.get("page_start") is None and page != "":
            try:
                chunk["page_start"] = int(page)
            except Exception:
                pass
        output.append(chunk)
    return output


def _collect_explicit_taxonomy_search_chunks(
    ingest_client,
    *,
    context_ids: list[str],
) -> list[dict[str, Any]]:
    """Busca dirigida e isolada ao ativo atual para recuperar a tabela funcional."""
    queries = (
        "grupos funcionais grupo funcional tabela configuração da máquina",
        "functional groups functional group machine configuration",
        "gruppi funzionali configurazione macchina",
        "subsistemas grupos de funções configuração equipamento",
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        try:
            payload = ingest_client.search_text(
                query=query,
                context_ids=context_ids,
                limit=40,
                min_score=0.0,
            )
        except Exception:
            continue
        citations = payload.get("citations", []) if isinstance(payload, dict) else []
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            chunk = _citation_to_failure_mode_chunk(citation)
            if not chunk:
                continue
            key = _normalize_text(str(chunk.get("content_hash") or str(chunk.get("text") or "")[:320]))
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(chunk)
    return output[:160]


def _taxonomy_group_is_grounded(name: str, evidence_text: str) -> bool:
    normalized_name = _normalize_text(name)
    normalized_evidence = _normalize_text(evidence_text)
    if not normalized_name or not normalized_evidence:
        return False
    if normalized_name in normalized_evidence:
        return True
    tokens = [token for token in normalized_name.split() if len(token) >= 3]
    return bool(tokens) and all(token in normalized_evidence for token in tokens)


def _recover_explicit_functional_categories_with_llm(
    runtime,
    *,
    filename: str,
    evidence_chunks: list[dict[str, Any]],
) -> list[str]:
    """Transcreve uma lista explícita quando o PDF quebrou a tabela em chunks irregulares."""
    marker_chunks = _explicit_taxonomy_marker_chunks(evidence_chunks)
    if not marker_chunks:
        return []

    blocks: list[str] = []
    total = 0
    for chunk in marker_chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        page = chunk.get("page_start")
        block = f"[Página {page}]\n{text}" if isinstance(page, int) and page > 0 else text
        if blocks and total + len(block) > 24_000:
            break
        blocks.append(block)
        total += len(block)
    evidence_text = "\n\n".join(blocks).strip()
    if not evidence_text:
        return []

    try:
        response = chat_claude(
            runtime,
            system=EXPLICIT_FUNCTIONAL_TAXONOMY_RECOVERY_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Arquivo: {filename}\n\nTrecho do manual:\n{evidence_text}",
            }],
            max_tokens=900,
        )
    except Exception:
        return []

    raw_groups = _extract_json_object(response).get("groups")
    if not isinstance(raw_groups, list):
        return []

    output: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_groups:
        name = _sanitize_taxonomy_name(str(raw_name or ""))
        key = _normalize_text(name)
        if not name or not key or key in seen or len(name) > 100:
            continue
        if _taxonomy_name_is_overcombined(name):
            continue
        if not _taxonomy_group_is_grounded(name, evidence_text):
            continue
        seen.add(key)
        output.append(name)
    return output[:18] if len(output) >= 4 else []

def _item_taxonomy_evidence(
    item: dict[str, Any],
    evidence_chunks: list[dict[str, Any]],
    *,
    max_chars: int = 2600,
) -> str:
    pages = set(_normalize_source_pages(item.get("source_pages")))
    title_tokens = _significant_tokens(str(item.get("title") or ""))
    ranked: list[tuple[int, str]] = []
    for chunk in evidence_chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        page = chunk.get("page_start")
        score = 0
        if isinstance(page, int) and page in pages:
            score += 20
        if title_tokens:
            score += len(title_tokens.intersection(_significant_tokens(text[:1800]))) * 3
        if score > 0:
            label = f"[Página {page}]\n{text}" if isinstance(page, int) and page > 0 else text
            ranked.append((score, label))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    parts: list[str] = []
    size = 0
    for _, text in ranked:
        if parts and size + len(text) > max_chars:
            break
        parts.append(text)
        size += len(text)
        if len(parts) >= 4:
            break
    return "\n\n".join(parts)


def revalidate_existing_alarm_assignments(
    runtime,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Revalida alarmes JÁ salvos sem extrair novos alarmes do documento.

    A função usa exclusivamente ``alarm_evidence`` persistido em cada modo. Em
    caso de falha/omissão do modelo, a operação inteira aborta antes da gravação
    pelo chamador. Itens manuais são preservados sem alteração.
    """
    output = [dict(item) for item in items if isinstance(item, dict)]
    payload_items: list[dict[str, Any]] = []
    evidence_rows: dict[str, dict[str, Any]] = {}

    for item in output:
        if str(item.get("source_type") or "").strip().lower() == "manual":
            continue
        mode_id = str(item.get("mode_id") or "").strip()
        codes = _normalize_alarm_codes(item.get("alarm_codes"))
        if not mode_id or not codes:
            continue
        evidence_by_code = {
            str(entry.get("code") or "").strip(): entry
            for entry in (item.get("alarm_evidence") or [])
            if isinstance(entry, dict) and str(entry.get("code") or "").strip()
        }
        candidates: list[dict[str, Any]] = []
        for code in codes:
            evidence = evidence_by_code.get(code)
            if not evidence:
                # Sem evidência oficial, o código não pode sobreviver à revisão.
                continue
            label = str(evidence.get("label") or "").strip()
            candidates.append({
                "code": code,
                "label": label,
                "page": evidence.get("page"),
                "source_kind": evidence.get("source_kind"),
            })
            evidence_rows[code] = {
                "code": code,
                "label": label,
                "text": label,
                "page": evidence.get("page"),
                "source_kind": evidence.get("source_kind") or "alarm_generic",
            }
        payload_items.append({
            "mode_id": mode_id,
            "title": item.get("title"),
            "description": str(item.get("description") or "")[:500],
            "category": item.get("category"),
            "causes": _split_text_values(item.get("causes"))[:4],
            "effects": _split_text_values(item.get("effects"))[:4],
            "candidates": candidates,
        })

    if not payload_items:
        return {"items": output, "removed_alarm_links": 0, "reviewed_modes": 0}

    # Baseline determinístico: a IA só pode remover/confirmar; omissão ou falha
    # de rede não derruba toda a finalização.
    keep: dict[str, list[str]] = {}
    output_by_mode = {
        str(item.get("mode_id") or "").strip(): item
        for item in output
        if isinstance(item, dict)
    }
    for entry in payload_items:
        source_item = output_by_mode.get(entry["mode_id"])
        deterministic: list[str] = []
        if source_item is not None:
            for candidate in entry["candidates"]:
                code = str(candidate.get("code") or "").strip()
                if not code:
                    continue
                if _delivery_alarm_direct_title_match(
                    source_item,
                    {
                        "code": code,
                        "label": str(candidate.get("label") or ""),
                        "text": str(candidate.get("label") or ""),
                        "source_kind": candidate.get("source_kind") or "alarm_generic",
                    },
                ):
                    deterministic.append(code)
        keep[entry["mode_id"]] = deterministic[:3]

    def apply(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        for raw_mode_id, raw_codes in raw.items():
            mode_id = str(raw_mode_id or "").strip()
            entry = next((item for item in payload_items if item["mode_id"] == mode_id), None)
            if entry is None:
                continue
            allowed = {str(candidate.get("code") or "").strip() for candidate in entry["candidates"]}
            selected = [
                code for code in _normalize_alarm_codes(raw_codes)
                if code in allowed
            ]
            keep[mode_id] = selected[:3]

    for start in range(0, len(payload_items), 12):
        batch = payload_items[start:start + 12]
        try:
            response = chat_claude(
                runtime,
                system=EXISTING_ALARM_REVALIDATION_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({"items": batch}, ensure_ascii=False)}],
                max_tokens=1500,
            )
            apply(_extract_json_object(response).get("keep"))
        except Exception:
            continue

    removed = 0
    for item in output:
        if str(item.get("source_type") or "").strip().lower() == "manual":
            continue
        mode_id = str(item.get("mode_id") or "").strip()
        if mode_id not in keep:
            continue
        previous = _normalize_alarm_codes(item.get("alarm_codes"))
        selected = keep[mode_id]
        evidence_lookup = {
            str(entry.get("code") or "").strip(): entry
            for entry in (item.get("alarm_evidence") or [])
            if isinstance(entry, dict) and str(entry.get("code") or "").strip()
        }
        # O modelo é revisor, não autoridade final. Cada código ainda precisa
        # passar pela trava determinística de mesma condição + mesmo componente.
        selected = [
            code for code in selected
            if code in evidence_lookup
            and _delivery_alarm_direct_title_match(
                item,
                {
                    "code": code,
                    "label": str(evidence_lookup[code].get("label") or ""),
                    "text": str(evidence_lookup[code].get("label") or ""),
                    "source_kind": evidence_lookup[code].get("source_kind") or "alarm_generic",
                },
            )
        ][:3]
        removed += max(0, len(previous) - len(selected))
        item["alarm_codes"] = selected
        selected_set = set(selected)
        item["alarm_evidence"] = [
            entry for entry in (item.get("alarm_evidence") or [])
            if isinstance(entry, dict) and str(entry.get("code") or "").strip() in selected_set
        ]

    rows = list(evidence_rows.values())
    output = _synchronize_alarm_descriptions(output, rows)
    return {
        "items": output,
        "removed_alarm_links": removed,
        "reviewed_modes": len(payload_items),
    }


def reclassify_existing_failure_modes_to_explicit_taxonomy(
    runtime,
    *,
    ingest_client,
    asset_id: str,
    doc_id: str,
    filename: str,
    items: list[dict[str, Any]],
    bronze_id: str | None = None,
    envelope_id: str | None = None,
    progress_callback: FailureModeProgressCallback | None = None,
) -> dict[str, Any]:
    """Reclassifica SOMENTE categorias da FTA já salva, sem nova extração.

    Títulos, causas, efeitos, ações, severidade, alarmes, páginas e IDs são
    preservados byte-a-byte no payload em memória; apenas ``category`` dos itens
    automáticos é alterado. Itens manuais permanecem intocados.
    """
    _emit_failure_mode_progress(
        progress_callback, percent=1, phase="preparing",
        message="Preparando o documento e identificando o contexto técnico.",
    )
    context_ids = _build_failure_mode_context_ids(
        asset_id=asset_id,
        doc_id=doc_id,
        bronze_id=bronze_id,
        envelope_id=envelope_id,
    )
    document_ids = _unique_strings([envelope_id, bronze_id, doc_id])
    direct_chunks = _collect_ingest_direct_chunks(
        ingest_client,
        document_ids=document_ids,
        context_ids=context_ids,
        limit=3000,
        relevant_only=False,
    )
    if not direct_chunks:
        raise ValueError("Não foi possível recuperar os chunks do documento para reclassificar a taxonomia.")

    taxonomy_evidence = list(direct_chunks)
    explicit_groups = _extract_explicit_functional_categories_robust(taxonomy_evidence)

    if len(explicit_groups) < 4:
        search_chunks = _collect_explicit_taxonomy_search_chunks(
            ingest_client,
            context_ids=context_ids,
        )
        taxonomy_evidence.extend(search_chunks)
        taxonomy_evidence = _unique_failure_mode_chunks(taxonomy_evidence)
        explicit_groups = _extract_explicit_functional_categories_robust(taxonomy_evidence)

    if len(explicit_groups) < 4:
        explicit_groups = _recover_explicit_functional_categories_with_llm(
            runtime,
            filename=filename,
            evidence_chunks=taxonomy_evidence,
        )

    if len(explicit_groups) < 4:
        marker_count = len(_explicit_taxonomy_marker_chunks(taxonomy_evidence))
        raise ValueError(
            "O manual atual não forneceu uma lista explícita e confiável de grupos funcionais "
            f"após parser direto + busca dirigida + recuperação estrita (chunks={len(taxonomy_evidence)}, "
            f"janelas_com_marcador={marker_count}); a FTA permaneceu inalterada."
        )

    taxonomy = [{"name": name, "aliases": [], "keywords": []} for name in explicit_groups]
    allowed_by_key = {_normalize_text(name): name for name in explicit_groups}

    original_items = [dict(item) for item in items if isinstance(item, dict)]
    auto_items = [item for item in original_items if str(item.get("source_type") or "").strip().lower() != "manual"]
    manual_ids = {
        str(item.get("mode_id") or "")
        for item in original_items
        if str(item.get("source_type") or "").strip().lower() == "manual"
    }

    mapping: dict[str, str] = {}
    compact: list[dict[str, Any]] = []
    for item in auto_items:
        mode_id = str(item.get("mode_id") or "").strip()
        if not mode_id:
            continue
        current = str(item.get("category") or "").strip()
        current_key = _normalize_text(current)
        if current_key in allowed_by_key:
            mapping[mode_id] = allowed_by_key[current_key]
        compact.append({
            "mode_id": mode_id,
            "title": item.get("title"),
            "description": str(item.get("description") or "")[:700],
            "current_category": current,
            "severity": item.get("severity"),
            "tags": _split_tags(item.get("tags"))[:14],
            "causes": _split_text_values(item.get("causes"))[:5],
            "effects": _split_text_values(item.get("effects"))[:5],
            "actions": _split_text_values(item.get("actions"))[:5],
            "source_pages": _normalize_source_pages(item.get("source_pages"))[:8],
            "document_evidence": _item_taxonomy_evidence(item, direct_chunks),
        })

    def apply_mapping(raw_mapping: Any) -> None:
        if not isinstance(raw_mapping, dict):
            return
        for raw_mode_id, raw_category in raw_mapping.items():
            mode_id = str(raw_mode_id or "").strip()
            category_key = _normalize_text(str(raw_category or ""))
            if mode_id and category_key in allowed_by_key:
                mapping[mode_id] = allowed_by_key[category_key]

    # Primeira passagem em lotes: rápida e suficiente para a maioria dos modos.
    unresolved = [entry for entry in compact if entry.get("mode_id") not in mapping]
    for start in range(0, len(unresolved), 16):
        batch = unresolved[start:start + 16]
        try:
            response = chat_claude(
                runtime,
                system=CATEGORY_RECLASSIFICATION_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "document": filename,
                    "allowed_categories": explicit_groups,
                    "items": batch,
                }, ensure_ascii=False)}],
                max_tokens=2400,
            )
            apply_mapping(_extract_json_object(response).get("mapping"))
        except Exception:
            continue

    still_unresolved = [entry for entry in compact if entry.get("mode_id") not in mapping]
    for entry in still_unresolved:
        try:
            response = chat_claude(
                runtime,
                system=CATEGORY_RECLASSIFICATION_SYSTEM + "\nVocê DEVE escolher exatamente uma categoria permitida para o único item recebido.",
                messages=[{"role": "user", "content": json.dumps({
                    "document": filename,
                    "allowed_categories": explicit_groups,
                    "items": [entry],
                }, ensure_ascii=False)}],
                max_tokens=500,
            )
            apply_mapping(_extract_json_object(response).get("mapping"))
        except Exception:
            continue

    taxonomy_rows = [{"name": name, "aliases": [], "keywords": []} for name in explicit_groups]
    by_mode = {str(item.get("mode_id") or "").strip(): item for item in auto_items}
    for entry in compact:
        mode_id = str(entry.get("mode_id") or "").strip()
        if not mode_id or mode_id in mapping:
            continue
        source_item = by_mode.get(mode_id) or {}
        enriched = dict(source_item)
        evidence = str(entry.get("document_evidence") or "")
        if evidence:
            enriched["effects"] = _merge_unique_text_lists(
                _split_text_values(enriched.get("effects")),
                [evidence[:1600]],
            )
        ranked = sorted(
            ((_category_match_score(enriched, category), str(category.get("name") or "")) for category in taxonomy_rows),
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            continue
        second = ranked[1][0] if len(ranked) > 1 else -1
        if ranked[0][0] > second:
            mapping[mode_id] = ranked[0][1]

    unresolved_ids = [
        str(entry.get("mode_id") or "")
        for entry in compact
        if entry.get("mode_id") not in mapping
    ]
    if unresolved_ids:
        raise ValueError(
            "A reclassificação foi cancelada porque alguns modos permaneceram sem grupo funcional mesmo após "
            "duas passagens de IA e fallback determinístico: " + ", ".join(unresolved_ids[:12])
        )

    output: list[dict[str, Any]] = []
    changed = 0
    before_categories = _unique_strings([str(item.get("category") or "") for item in original_items if item.get("category")])
    for item in original_items:
        mode_id = str(item.get("mode_id") or "").strip()
        if mode_id in manual_ids:
            output.append(item)
            continue
        mapped = mapping.get(mode_id)
        if mapped:
            if _normalize_text(str(item.get("category") or "")) != _normalize_text(mapped):
                changed += 1
            item["category"] = mapped
        output.append(item)

    after_categories = _unique_strings([str(item.get("category") or "") for item in output if item.get("category")])
    return {
        "version": _TAXONOMY_RECLASSIFICATION_VERSION,
        "items": output,
        "taxonomy": explicit_groups,
        "changed_modes": changed,
        "manual_modes_preserved": len(manual_ids),
        "before_categories": before_categories,
        "after_categories": after_categories,
        "auto_modes": len(auto_items),
    }


def _taxonomy_evidence_text(chunks: list[dict[str, Any]], max_chars: int = 28_000) -> str:
    priority_terms = (
        "configuracao da maquina", "configuração da máquina", "grupos funcionais", "grupo funcional",
        "componentes principais", "descricao geral", "descrição geral", "arquitetura", "subsistemas",
        "table of contents", "indice", "índice", "sumario", "sumário", "machine configuration",
    )
    ranked = sorted(
        chunks,
        key=lambda chunk: sum(
            1 for term in priority_terms if _normalize_text(term) in _normalize_text(str(chunk.get("text") or ""))
        ),
        reverse=True,
    )
    parts: list[str] = []
    size = 0
    for chunk in ranked:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        page = chunk.get("page_start")
        block = f"[Página {page}]\n{text}" if isinstance(page, int) and page > 0 else text
        if parts and size + len(block) > max_chars:
            break
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts)


def _taxonomy_name_is_overcombined(name: str) -> bool:
    """Detecta categoria gerada que aglutina muitos subsistemas independentes."""
    safe = str(name or "").strip()
    if not safe:
        return False
    separator_count = len(re.findall(r"\s*(?:/|\+|\||;|,|(?<=[A-Za-zÀ-ÿ])-(?=[A-Za-zÀ-ÿ]))\s*", safe))
    normalized = _normalize_text(safe)
    functional_families = (
        "hidraul", "pneumat", "gas", "agua", "optic", "eletric", "mecanic",
        "laser", "refrig", "aspir", "lubr", "controle", "seguranca", "sensor",
    )
    family_count = sum(1 for token in functional_families if token in normalized)
    return separator_count >= 3 and family_count >= 4


def _extract_document_taxonomy(
    runtime,
    *,
    document_context: str,
    evidence_chunks: list[dict[str, Any]],
    candidate_categories: list[str],
) -> list[dict[str, Any]]:
    explicit_groups = _extract_explicit_functional_categories(evidence_chunks)

    # An explicit functional-group table is stronger evidence than ad-hoc
    # categories generated independently by each extraction batch. Use it as the
    # canonical taxonomy exactly as documented by the equipment manual.
    if len(explicit_groups) >= 4:
        return [
            {"name": name, "aliases": [], "keywords": []}
            for name in explicit_groups
        ]

    evidence = _taxonomy_evidence_text(evidence_chunks)
    payload = {
        "document_context": str(document_context or "")[:4500],
        "candidate_categories": [
            value for value in candidate_categories[:80]
            if _sanitize_taxonomy_name(value)
        ],
        "explicit_functional_groups": explicit_groups,
        "evidence": evidence,
    }
    try:
        response = chat_claude(
            runtime,
            system=TAXONOMY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=1800,
        )
        parsed = _extract_json_object(response)
    except Exception:
        return []

    raw_categories = parsed.get("categories")
    if not isinstance(raw_categories, list):
        return []

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_categories:
        if not isinstance(raw, dict):
            continue
        name = _sanitize_taxonomy_name(str(raw.get("name") or ""))
        key = _normalize_text(name)
        if not key or key == _normalize_text(DEFAULT_CATEGORY) or key in seen or len(name) > 90:
            continue
        if _taxonomy_name_is_overcombined(name):
            continue
        seen.add(key)
        output.append({
            "name": name,
            "aliases": _unique_strings(list(raw.get("aliases") or []))[:12],
            "keywords": _unique_strings(list(raw.get("keywords") or []))[:18],
        })
    return output[:18]


def _category_match_score(item: dict[str, Any], category: dict[str, Any]) -> int:
    item_text = " ".join([
        str(item.get("title") or ""),
        str(item.get("category") or ""),
        " ".join(_split_tags(item.get("tags"))),
        " ".join(_split_text_values(item.get("causes"))),
        " ".join(_split_text_values(item.get("effects"))),
    ])
    item_tokens = _significant_tokens(item_text)
    category_text = " ".join([
        str(category.get("name") or ""),
        " ".join(_unique_strings(list(category.get("aliases") or []))),
        " ".join(_unique_strings(list(category.get("keywords") or []))),
    ])
    category_tokens = _significant_tokens(category_text)
    return len(item_tokens.intersection(category_tokens))


def _classify_failure_modes_to_taxonomy(
    runtime,
    items: list[dict[str, Any]],
    taxonomy: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = _unique_strings([str(category.get("name") or "").strip() for category in taxonomy if category.get("name")])
    if not items or not allowed:
        return items

    allowed_by_key = {_normalize_text(value): value for value in allowed}
    mapping: dict[str, str] = {}

    compact = []
    for item in items:
        mode_id = str(item.get("mode_id") or "").strip()
        current = str(item.get("category") or "").strip()
        current_key = _normalize_text(current)
        if mode_id and current_key in allowed_by_key:
            mapping[mode_id] = allowed_by_key[current_key]
        compact.append({
            "mode_id": mode_id,
            "title": item.get("title"),
            "description": str(item.get("description") or "")[:320],
            "current_category": current,
            "tags": _split_tags(item.get("tags"))[:12],
            "causes": _split_text_values(item.get("causes"))[:4],
            "effects": _split_text_values(item.get("effects"))[:4],
        })

    def apply_mapping(raw_mapping: Any) -> None:
        if not isinstance(raw_mapping, dict):
            return
        for mode_id, category in raw_mapping.items():
            category_key = _normalize_text(str(category or ""))
            if category_key in allowed_by_key:
                mapping[str(mode_id)] = allowed_by_key[category_key]

    for start in range(0, len(compact), 20):
        batch = [entry for entry in compact[start:start + 20] if entry.get("mode_id") not in mapping]
        if not batch:
            continue
        try:
            response = chat_claude(
                runtime,
                system=CATEGORY_MAPPING_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({"allowed_categories": taxonomy, "items": batch}, ensure_ascii=False)}],
                max_tokens=1800,
            )
            apply_mapping(_extract_json_object(response).get("mapping"))
        except Exception:
            pass

    unresolved = [entry for entry in compact if entry.get("mode_id") not in mapping]
    for start in range(0, len(unresolved), 6):
        batch = unresolved[start:start + 6]
        try:
            response = chat_claude(
                runtime,
                system=CATEGORY_MAPPING_SYSTEM + "\nSe a evidencia for insuficiente, omita o mode_id; nunca use a primeira categoria como fallback.",
                messages=[{"role": "user", "content": json.dumps({"allowed_categories": taxonomy, "items": batch}, ensure_ascii=False)}],
                max_tokens=1000,
            )
            apply_mapping(_extract_json_object(response).get("mapping"))
        except Exception:
            pass

    taxonomy_by_name = {str(category.get("name") or ""): category for category in taxonomy if category.get("name")}
    output: list[dict[str, Any]] = []
    for item in items:
        mode_id = str(item.get("mode_id") or "")
        mapped = mapping.get(mode_id)
        if not mapped:
            ranked = sorted(
                ((_category_match_score(item, category), str(category.get("name") or "")) for category in taxonomy if category.get("name")),
                reverse=True,
            )
            if ranked and ranked[0][0] >= 2:
                # Evita empates fracos que antes acabavam sempre na primeira categoria.
                second = ranked[1][0] if len(ranked) > 1 else 0
                if ranked[0][0] > second:
                    mapped = ranked[0][1]
        if not mapped:
            # Item sem subsistema identificavel nao e confiavel o suficiente para a FTA automatica.
            continue
        item["category"] = taxonomy_by_name.get(mapped, {"name": mapped}).get("name", mapped)
        output.append(item)
    return output



def _enrich_source_pages_from_chunks(
    items: list[dict[str, Any]],
    evidence_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    page_chunks = [
        chunk for chunk in evidence_chunks
        if isinstance(chunk.get("page_start"), int) and int(chunk.get("page_start") or 0) > 0
    ]
    if not page_chunks:
        return items

    for item in items:
        existing_pages = _normalize_source_pages(item.get("source_pages"))
        if existing_pages:
            item["source_pages"] = existing_pages
            continue

        title = str(item.get("title") or "")
        title_tokens = _significant_tokens(title)
        details = " ".join([
            str(item.get("description") or ""),
            " ".join(_split_text_values(item.get("causes"))),
            " ".join(_split_text_values(item.get("effects"))),
            " ".join(_split_text_values(item.get("actions"))),
            " ".join(_split_tags(item.get("tags"))),
        ])
        detail_tokens = _significant_tokens(details)
        alarm_codes = _normalize_alarm_codes(item.get("alarm_codes"))

        scored: list[tuple[int, int]] = []
        normalized_title = _normalize_text(title)
        for chunk in page_chunks:
            text = str(chunk.get("text") or "")
            normalized_text = _normalize_text(text)
            text_tokens = _significant_tokens(text)
            score = 0
            if normalized_title and len(normalized_title) >= 10 and normalized_title in normalized_text:
                score += 14
            score += 4 * len(title_tokens.intersection(text_tokens))
            score += min(6, len(detail_tokens.intersection(text_tokens)))
            for code in alarm_codes:
                if _code_regex(code).search(text):
                    score += 16
            if score > 0:
                scored.append((score, int(chunk["page_start"])))

        if not scored:
            continue
        best = max(score for score, _ in scored)
        minimum = 8 if len(title_tokens) >= 2 else 12
        if best < minimum:
            continue
        item["source_pages"] = sorted({page for score, page in scored if score >= max(minimum, best - 2)})[:4]

    return items


def _collect_alarm_evidence_chunks(
    ingest_client,
    *,
    context_ids: list[str],
    direct_chunks: list[dict[str, Any]],
    limit_per_query: int = 36,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(chunk: dict[str, Any]) -> None:
        text = str(chunk.get("text") or "").strip()
        if not text:
            return
        if _alarm_table_section_kind(text) == "message":
            return
        block = text
        page = chunk.get("page_start")
        if isinstance(page, int) and page > 0:
            block = f"[Página {page}]\n{text}"
        if not _extract_alarm_rows_from_batch(block):
            normalized = _normalize_text(text)
            if not any(term in normalized for term in ALARM_TABLE_TERMS):
                return
        key = _normalize_text(str(chunk.get("content_hash") or text[:280]))
        if not key or key in seen:
            return
        seen.add(key)
        output.append(chunk)

    for chunk in direct_chunks:
        add(chunk)

    if not context_ids:
        return output

    queries = (
        "tabela de alarmes número alarme descrição explicação solução PLC CNC",
        "alarmes PLC alarmes ISO código descrição explicação solução",
        "alarm table alarm list fault code error code description explanation remedy",
        "alarm fault trip interlock diagnostic cause remedy troubleshooting",
    )
    for query in queries:
        try:
            payload = ingest_client.search_text(
                query=query,
                context_ids=context_ids,
                limit=limit_per_query,
                min_score=0.0,
            )
        except Exception:
            continue
        citations = payload.get("citations", []) if isinstance(payload, dict) else []
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if isinstance(citation, dict):
                chunk = _citation_to_failure_mode_chunk(citation)
                if chunk:
                    add(chunk)
    return output


def _unique_strings(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        safe_value = str(value or "").strip()
        key = _normalize_text(safe_value)
        if safe_value and key and key not in seen:
            seen.add(key)
            output.append(safe_value)
    return output


def _build_failure_mode_context_ids(
    *,
    asset_id: str,
    doc_id: str,
    bronze_id: str | None,
    envelope_id: str | None,
) -> list[str]:
    return _unique_strings([
        asset_id,
        doc_id,
        f"asset:{asset_id}" if asset_id else "",
        f"doc:{doc_id}" if doc_id else "",
        bronze_id,
        envelope_id,
    ])


def _build_failure_mode_document_context(
    ingest_client,
    *,
    envelope_id: str | None,
    filename: str,
) -> str:
    parts: list[str] = []
    safe_filename = str(filename or "").strip()
    if safe_filename:
        parts.append(f"arquivo: {safe_filename}")

    safe_envelope_id = str(envelope_id or "").strip()
    if not safe_envelope_id:
        return "; ".join(parts)

    try:
        envelope = ingest_client.get_document(safe_envelope_id)
    except Exception:
        return "; ".join(parts)

    if not isinstance(envelope, dict):
        return "; ".join(parts)

    extracted_data = envelope.get("extracted_data")
    if not isinstance(extracted_data, dict):
        extracted_data = {}

    field_labels = (
        ("nome_equipamento", "equipamento"),
        ("fabricante", "fabricante"),
        ("modelo", "modelo"),
        ("tipo_equipamento", "tipo"),
        ("tipo_manual", "manual"),
        ("dados_tecnicos_principais", "dados técnicos"),
        ("lista_pecas_resumo", "subsistemas/componentes"),
        ("instrucoes_operacao", "operação"),
        ("instrucoes_manutencao", "manutenção"),
    )

    for field_name, label in field_labels:
        value = str(extracted_data.get(field_name) or "").strip()
        if not value:
            continue
        value = re.sub(r"\s+", " ", value)
        if len(value) > 700:
            value = value[:700].rstrip() + "..."
        parts.append(f"{label}: {value}")

    return "; ".join(parts)


def _build_failure_mode_queries(document_context: str) -> list[str]:
    safe_context = re.sub(r"\s+", " ", str(document_context or "").strip())
    prefix = safe_context[:900] if safe_context else "equipamento do manual atual"

    queries = [
        f"{prefix} falha erro alarme código diagnóstico causa solução ação corretiva",
        f"{prefix} troubleshooting fault error alarm diagnostic cause remedy corrective action",
        f"{prefix} perda de função mau funcionamento anomalia parada trip interlock shutdown warning",
        f"{prefix} sistema elétrico controle automação PLC CNC sensores atuadores comunicação falha diagnóstico",
        f"{prefix} sistema mecânico movimento acionamento transmissão alinhamento desgaste travamento falha",
        f"{prefix} utilidades processo hidráulico pneumático refrigeração gás água pressão temperatura vazamento falha",
        f"{prefix} manutenção preventiva corretiva inspeção ajuste calibração substituição diagnóstico falha",
        f"{prefix} qualidade processo defeito rejeição dimensional programação peça produto falha alarme",
        # Structural queries deliberately avoid model/manufacturer names. They are
        # useful when chapter headings carry more retrieval signal than the prose.
        "troubleshooting chapter problems possible causes remedies corrective actions",
        "alarm list appendix alarm table code description explanation cause solution",
        "fault code error code warning trip shutdown interlock cause remedy table",
        "tabela de alarmes numero alarme descricao explicacao causa solucao",
        "problema possiveis causas solucao manutencao corretiva diagnostico",
        "tabella allarmi guasto causa soluzione manutenzione",
        "tabla alarmas averia fallo causas soluciones mantenimiento",
        "alarmliste fehler ursache losung wartung",
        "table alarmes defaut cause solution maintenance",
        "instalação comissionamento configuração parametrização calibração erro falha diagnóstico solução",
    ]
    return _unique_strings(queries)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        return {}

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_json_items_resilient(text: str) -> list[dict[str, Any]]:
    """Return complete items even when an LLM response is truncated at max_tokens.

    The normal path requires valid JSON. When Bedrock stops in the middle of the
    final item, however, all earlier objects in the ``items`` array are still
    useful. This conservative recovery parses only fully balanced JSON objects
    that occur inside the array and ignores the incomplete tail.
    """
    parsed = _extract_json_object(text)
    items = parsed.get("items", []) if isinstance(parsed, dict) else []
    if isinstance(items, list):
        valid = [item for item in items if isinstance(item, dict)]
        if valid:
            return valid

    cleaned = str(text or "")
    marker_match = re.search(r'"items"\s*:\s*\[', cleaned, re.IGNORECASE)
    if not marker_match:
        return []

    recovered: list[dict[str, Any]] = []
    index = marker_match.end()
    depth = 0
    start_index: int | None = None
    in_string = False
    escaped = False

    while index < len(cleaned):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue
        if char == "{":
            if depth == 0:
                start_index = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start_index is not None:
                candidate = cleaned[start_index:index + 1]
                try:
                    value = json.loads(candidate)
                except Exception:
                    value = None
                if isinstance(value, dict):
                    recovered.append(value)
                start_index = None
        elif char == "]" and depth == 0:
            break
        index += 1

    return recovered


def _chunks_path(asset_id: str, doc_id: str) -> Path:
    # Local chunk files not used in ATI — always returns a non-existent path
    return Path("/dev/null") / f"{asset_id}__{doc_id}.jsonl"


def _split_text_values(value: Any) -> list[str]:
    def text_from_item(item: Any) -> str:
        if isinstance(item, dict):
            for key in (
                "title",
                "name",
                "description",
                "label",
                "text",
                "cause",
                "causa",
                "action",
                "acao",
                "effect",
                "efeito",
                "failure",
                "falha",
            ):
                safe_value = str(item.get(key) or "").strip()
                if safe_value:
                    return safe_value
            return ""

        return str(item or "").strip()

    if isinstance(value, list):
        raw_values = [text_from_item(item) for item in value]
    elif isinstance(value, dict):
        raw_values = [text_from_item(value)]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw_values = re.split(r"\n+|;|\u2022|-|\*", text)

    output: list[str] = []
    seen: set[str] = set()

    for item in raw_values:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip(" -•\t\r\n"))
        if not cleaned:
            continue
        key = _normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)

    return output


def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = [str(item or "").strip() for item in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw_values = re.split(r"\n+|,|;|\|", text)

    output: list[str] = []
    seen: set[str] = set()

    for item in raw_values:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip(" -•\t\r\n"))
        if not cleaned:
            continue
        if len(cleaned) > 36:
            cleaned = cleaned[:36].strip()
        key = _normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)

    return output


def _merge_unique_text_lists(*values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        for item in _split_text_values(value):
            key = _normalize_text(item)
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(item)

    return output


def _format_text_list(values: list[str]) -> str:
    return "\n".join(value.strip() for value in values if str(value or "").strip())


def _to_title_case_label(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        return DEFAULT_CATEGORY
    return " ".join(part[:1].upper() + part[1:] for part in cleaned.split(" "))


def _normalize_category(value: Any, *fallback_values: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned:
        return _to_title_case_label(cleaned)
    _ = fallback_values
    return DEFAULT_CATEGORY


def _infer_severity(*values: str) -> str:
    haystack = _normalize_text(" ".join(value for value in values if value))
    if not haystack:
        return DEFAULT_SEVERITY

    for severity, keywords in SEVERITY_RULES:
        if any(keyword in haystack for keyword in keywords):
            return severity

    return DEFAULT_SEVERITY


def _normalize_severity(value: Any, *fallback_values: str) -> str:
    cleaned = _normalize_text(str(value or "").strip())
    if cleaned in ALLOWED_SEVERITIES:
        return cleaned
    return _infer_severity(*fallback_values)


def _normalize_cause_group_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        cause = str(item.get("cause") or item.get("title") or "").strip()
        effects = _split_text_values(item.get("effects") or item.get("impact"))
        actions = _split_text_values(item.get("actions") or item.get("recommendation"))
        cause_id = str(item.get("cause_id") or uuid.uuid4().hex[:12]).strip() or uuid.uuid4().hex[:12]
    else:
        cause = str(item or "").strip()
        effects = []
        actions = []
        cause_id = uuid.uuid4().hex[:12]

    if not cause:
        return None

    return {
        "cause_id": cause_id,
        "cause": cause,
        "effects": effects,
        "actions": actions,
    }


def _build_cause_groups_from_flat_lists(
    causes: list[str],
    effects: list[str],
    actions: list[str],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []

    if len(causes) == 1:
        groups.append(
            {
                "cause_id": uuid.uuid4().hex[:12],
                "cause": causes[0],
                "effects": effects,
                "actions": actions,
            }
        )
        return groups

    for cause in causes:
        groups.append(
            {
                "cause_id": uuid.uuid4().hex[:12],
                "cause": cause,
                "effects": [],
                "actions": [],
            }
        )

    # Com múltiplas causas não existe evidência suficiente para associar listas
    # planas de efeitos/ações a uma causa específica. Mantemos esses valores nos
    # campos de topo e não fabricamos um nó genérico de diagnóstico.
    return groups


def _normalize_cause_groups(value: Any, causes: list[str], effects: list[str], actions: list[str]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        groups: list[dict[str, Any]] = []
        seen: set[str] = set()

        for raw_item in value:
            normalized = _normalize_cause_group_item(raw_item)
            if not normalized:
                continue

            key = _normalize_text(normalized["cause"])
            if key in seen:
                for current_group in groups:
                    if _normalize_text(current_group["cause"]) != key:
                        continue
                    current_group["effects"] = _merge_unique_text_lists(current_group.get("effects", []), normalized.get("effects", []))
                    current_group["actions"] = _merge_unique_text_lists(current_group.get("actions", []), normalized.get("actions", []))
                    break
                continue

            seen.add(key)
            groups.append(normalized)

        if groups:
            return groups

    return _build_cause_groups_from_flat_lists(causes, effects, actions)


def _collapse_cause_groups(cause_groups: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    causes: list[str] = []
    effects: list[str] = []
    actions: list[str] = []
    seen_causes: set[str] = set()

    for group in cause_groups:
        cause = str(group.get("cause") or "").strip()
        if cause:
            key = _normalize_text(cause)
            if key and key not in seen_causes:
                seen_causes.add(key)
                causes.append(cause)

        effects = _merge_unique_text_lists(effects, group.get("effects", []))
        actions = _merge_unique_text_lists(actions, group.get("actions", []))

    return causes, effects, actions


def load_document_chunks(asset_id: str, doc_id: str) -> list[dict[str, Any]]:
    path = _chunks_path(asset_id, doc_id)
    if not path.exists():
        return []

    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        safe_line = line.strip()
        if not safe_line:
            continue

        try:
            payload = json.loads(safe_line)
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue

        text = str(payload.get("text", "") or "").strip()
        if not text:
            continue

        items.append(payload)

    return items


def _select_failure_mode_batch_max_chars(chunks: list[dict[str, Any]]) -> int:
    """Scale LLM batch size with the actual indexed chunk window.

    The goal is to keep neighboring troubleshooting/alarm records together while
    staying safely below the model context budget. This is based only on chunk
    lengths, not on any manufacturer, model, chapter number or language.
    """
    lengths = sorted(
        len(str(chunk.get("text") or ""))
        for chunk in chunks
        if str(chunk.get("text") or "").strip()
    )
    if not lengths:
        return 18_000

    median = lengths[len(lengths) // 2]
    # Around 12 neighboring chunks gives enough cross-record context for tables
    # and troubleshooting sections. Clamp for predictable Bedrock cost/latency.
    target = int(max(18_000, median * 12))
    return min(36_000, target)


def build_failure_mode_batches(chunks: list[dict[str, Any]], max_chars: int = 18_000) -> list[str]:
    batches: list[str] = []
    current_parts: list[str] = []
    current_size = 0
    seen_hashes: set[str] = set()

    for chunk in chunks:
        content_hash = str(chunk.get("content_hash", "") or "").strip()
        if content_hash and content_hash in seen_hashes:
            continue
        if content_hash:
            seen_hashes.add(content_hash)

        page_start = chunk.get("page_start", None)
        page_end = chunk.get("page_end", None)
        section_title = str(chunk.get("section_title", "") or "").strip()
        text = str(chunk.get("text", "") or "").strip()
        if not text:
            continue

        header_parts: list[str] = []
        if isinstance(page_start, int) and isinstance(page_end, int) and page_start > 0 and page_end > 0:
            if page_start == page_end:
                header_parts.append(f"Página {page_start}")
            else:
                header_parts.append(f"Páginas {page_start}-{page_end}")
        if section_title:
            header_parts.append(f"Seção {section_title}")

        block = text
        if header_parts:
            block = f"[{' | '.join(header_parts)}]\n{text}"

        projected_size = current_size + len(block) + 2
        if current_parts and projected_size > max_chars:
            batches.append("\n\n".join(current_parts).strip())
            current_parts = []
            current_size = 0

        current_parts.append(block)
        current_size += len(block) + 2

    if current_parts:
        batches.append("\n\n".join(current_parts).strip())

    return [batch for batch in batches if batch]


def normalize_failure_mode_item(
    item: dict[str, Any],
    *,
    source_type: str,
    source_doc_id: str | None = None,
    source_doc_filename: str | None = None,
    existing_mode_id: str | None = None,
) -> dict[str, Any] | None:
    title = str(item.get("title", "") or "").strip()
    description = str(item.get("description", "") or "").strip()

    if title and _looks_like_prohibition_title(title):
        return None

    raw_causes = _split_text_values(item.get("causes") or item.get("probable_cause"))
    raw_effects = _split_text_values(item.get("effects") or item.get("impact"))
    raw_actions = _split_text_values(item.get("actions") or item.get("recommendation"))
    tags = _split_tags(item.get("tags") or item.get("symptoms"))
    cause_groups = _normalize_cause_groups(item.get("cause_groups"), raw_causes, raw_effects, raw_actions)
    valid_groups: list[dict[str, Any]] = []
    for group in cause_groups:
        cause_text = str(group.get("cause") or "").strip()
        if _is_forbidden_cause(cause_text):
            # Preserve explicitly documented effects/actions even when the model
            # wrapped them in a placeholder cause. We remove only the invented
            # cause itself.
            raw_effects = _merge_unique_text_lists(raw_effects, _split_text_values(group.get("effects")))
            raw_actions = _merge_unique_text_lists(raw_actions, _split_text_values(group.get("actions")))
            continue
        if _is_circular_cause(title, cause_text):
            continue
        valid_groups.append(group)
    cause_groups = valid_groups
    raw_causes = [
        cause for cause in raw_causes
        if not _is_forbidden_cause(cause) and not _is_circular_cause(title, cause)
    ]
    group_causes, group_effects, group_actions = _collapse_cause_groups(cause_groups)

    causes = _merge_unique_text_lists(raw_causes, group_causes)
    effects = _merge_unique_text_lists(raw_effects, group_effects)
    actions = _merge_unique_text_lists(raw_actions, group_actions)

    probable_cause = str(item.get("probable_cause", "") or "").strip() or _format_text_list(causes)
    symptoms = str(item.get("symptoms", "") or "").strip() or ", ".join(tags)
    impact = str(item.get("impact", "") or "").strip() or _format_text_list(effects)
    recommendation = str(item.get("recommendation", "") or "").strip() or _format_text_list(actions)

    if not any([title, description, probable_cause, symptoms, impact, recommendation, causes, effects, actions, cause_groups]):
        return None

    if not title:
        if description:
            title = description[:90].strip()
        elif causes:
            title = causes[0][:90].strip()
        elif probable_cause:
            title = probable_cause[:90].strip()
        else:
            title = f"Modo de falha {uuid.uuid4().hex[:6]}"

    if len(title) > 140:
        title = title[:140].strip()

    category = _normalize_category(
        item.get("category"),
        title,
        description,
        probable_cause,
        impact,
        recommendation,
        " ".join(causes),
        " ".join(effects),
        " ".join(actions),
    )
    severity = _normalize_severity(
        item.get("severity"),
        title,
        description,
        probable_cause,
        impact,
        recommendation,
        " ".join(causes),
        " ".join(effects),
    )

    source_pages = _normalize_source_pages(item.get("source_pages") or item.get("pages"))
    alarm_codes = _normalize_alarm_codes(item.get("alarm_codes") or item.get("alarms") or item.get("codes"))

    alarm_evidence: list[dict[str, Any]] = []
    for raw in item.get("alarm_evidence") or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not code or not label:
            continue
        page = raw.get("page")
        alarm_evidence.append({
            "code": code,
            "label": label[:260],
            "page": int(page) if isinstance(page, int) and page > 0 else None,
            "source_kind": str(raw.get("source_kind") or "")[:40],
            "match_score": int(raw.get("match_score") or 0),
        })

    now = int(time.time())
    return {
        "mode_id": str(existing_mode_id or item.get("mode_id") or uuid.uuid4().hex[:12]),
        "title": title,
        "description": description,
        "category": category,
        "severity": severity,
        "tags": tags,
        "source_pages": source_pages,
        "alarm_codes": alarm_codes,
        "alarm_evidence": alarm_evidence,
        "causes": causes,
        "effects": effects,
        "actions": actions,
        "cause_groups": cause_groups,
        "probable_cause": probable_cause,
        "symptoms": symptoms,
        "impact": impact,
        "recommendation": recommendation,
        "source_type": source_type,
        "source_doc_id": str(source_doc_id or item.get("source_doc_id") or "").strip() or None,
        "source_doc_filename": str(source_doc_filename or item.get("source_doc_filename") or "").strip() or None,
        "created_at": int(item.get("created_at") or now),
        "updated_at": now,
    }



def _merge_cause_groups(existing_groups: list[dict[str, Any]], new_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}

    for group in existing_groups + new_groups:
        normalized = _normalize_cause_group_item(group)
        if not normalized:
            continue

        key = _normalize_text(normalized["cause"])
        if key not in index_by_key:
            index_by_key[key] = len(merged)
            merged.append(normalized)
            continue

        current = merged[index_by_key[key]]
        current["effects"] = _merge_unique_text_lists(current.get("effects", []), normalized.get("effects", []))
        current["actions"] = _merge_unique_text_lists(current.get("actions", []), normalized.get("actions", []))

    return merged


def deduplicate_failure_modes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}

    for item in items:
        normalized = normalize_failure_mode_item(
            item,
            source_type=str(item.get("source_type", "manual") or "manual"),
            source_doc_id=item.get("source_doc_id"),
            source_doc_filename=item.get("source_doc_filename"),
            existing_mode_id=item.get("mode_id"),
        )
        if not normalized:
            continue

        key = _normalize_text("|".join([normalized["category"], normalized["title"]]))
        if not key:
            key = str(normalized["mode_id"])

        if key not in index_by_key:
            index_by_key[key] = len(merged)
            merged.append(normalized)
            continue

        existing = merged[index_by_key[key]]
        existing_source_type = str(existing.get("source_type", "") or "")
        normalized_source_type = str(normalized.get("source_type", "") or "")

        if existing_source_type != "manual" and normalized_source_type == "manual":
            merged[index_by_key[key]] = normalized
            continue

        _merge_failure_mode_records(existing, normalized)

    return merged


def _evidence_quote_is_grounded(
    raw_item: dict[str, Any],
    evidence_text: str,
) -> bool:
    """Validate the verbatim evidence quote — requires strict literal containment."""
    evidence = str(evidence_text or "").strip()
    quote = str(raw_item.get("evidence_quote") or "").strip()
    normalized_evidence = _normalize_text(evidence)
    normalized_quote = _normalize_text(quote)
    if not normalized_evidence or not normalized_quote:
        return False
    return normalized_quote in normalized_evidence


def _failure_mode_is_grounded_in_evidence(
    raw_item: dict[str, Any],
    evidence_text: str,
) -> bool:
    """Fail closed when an automatic mode has no traceable source evidence.

    ``evidence_quote`` solves the cross-language case: the model may write the FTA
    in Portuguese while the source manual is English/Italian/etc., but the quote
    must remain verbatim. Alarm codes and exact technical phrases are conservative
    fallbacks for legacy/mocked outputs that predate the quote field.
    """
    evidence = str(evidence_text or "").strip()
    normalized_evidence = _normalize_text(evidence)
    if not normalized_evidence:
        return False

    if _evidence_quote_is_grounded(raw_item, evidence):
        return True

    # Exact alarm/fault code in the source is strong grounding even if an older
    # model response omitted evidence_quote. Electrical/diagnostic references are
    # explicitly excluded by _looks_like_reference_not_alarm.
    for code in _normalize_alarm_codes(raw_item.get("alarm_codes") or raw_item.get("alarms") or raw_item.get("codes")):
        if _looks_like_reference_not_alarm(code):
            continue
        if _code_regex(code).search(evidence):
            return True

    # Legacy fallback for same-language manuals: remove generic failure words and
    # require the remaining technical phrase to occur literally in the evidence.
    title = _normalize_text(str(raw_item.get("title") or ""))
    core_tokens = [
        token for token in title.split()
        if token not in FAILURE_TITLE_STOPWORDS and len(token) >= 4
    ]
    if core_tokens:
        core_phrase = " ".join(core_tokens)
        if len(core_phrase) >= 6 and core_phrase in normalized_evidence:
            return True
        shared = set(core_tokens).intersection(set(normalized_evidence.split()))
        required = 1 if len(core_tokens) == 1 else max(2, (len(set(core_tokens)) + 1) // 2)
        if len(shared) >= required:
            return True

    return False


def extract_failure_modes_from_batch(
    runtime,
    *,
    batch_text: str,
    filename: str,
    grounding_text: str | None = None,
) -> list[dict[str, Any]]:
    if not batch_text.strip():
        return []

    user_prompt = (
        f"Arquivo técnico: {filename}\n\n"
        "Trecho do manual/documento:\n"
        f"{batch_text}\n\n"
        "Extraia os modos de falha relevantes descritos acima."
    )

    response_text = chat_claude(
        runtime,
        system=FAILURE_MODE_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4096,
    )
    raw_items = _extract_json_items_resilient(response_text)
    if not isinstance(raw_items, list):
        return []

    normalized_items: list[dict[str, Any]] = []
    source_evidence = grounding_text if grounding_text is not None else batch_text
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        if not _failure_mode_is_grounded_in_evidence(raw_item, source_evidence):
            continue
        normalized = normalize_failure_mode_item(raw_item, source_type="auto")
        if normalized:
            allowed_pages = _batch_page_numbers(batch_text)
            normalized["alarm_codes"] = _validate_alarm_codes_against_batch(
                normalized.get("alarm_codes"), batch_text
            )
            matched_alarm_rows = _alarm_rows_for_item(normalized, batch_text)
            if matched_alarm_rows:
                normalized["alarm_codes"] = _normalize_alarm_codes(
                    list(normalized.get("alarm_codes") or [])
                    + [row.get("code") for row in matched_alarm_rows]
                )

            if allowed_pages:
                explicit_pages = [
                    page for page in _normalize_source_pages(normalized.get("source_pages"))
                    if page in allowed_pages
                ]
                inferred_pages = [
                    page for page in _infer_source_pages_from_batch(normalized, batch_text)
                    if page in allowed_pages
                ]
                normalized["source_pages"] = sorted(set(explicit_pages + inferred_pages))[:4]
            else:
                normalized["source_pages"] = []
            normalized_items.append(normalized)

    return normalized_items


def _alarm_match_tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) >= 4 and token not in {_normalize_text(x) for x in ALARM_MATCH_STOPWORDS}
    }


_DELIVERY_SEMANTIC_LOCK_VERSION = "v3"

_DELIVERY_ALARM_TOKEN_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "para", "por", "com", "sem", "um", "uma", "the", "of", "and", "to", "for", "with", "without",
    "del", "de", "la", "el", "y", "con", "sin", "falha", "falhas", "erro", "erros", "alarme", "alarmes",
    "fault", "faults", "error", "errors", "alarm", "alarms", "avaria", "avarias", "sistema", "system",
    "maquina", "máquina", "machine", "equipamento", "equipment", "condicao", "condição", "condition",
    "estado", "state", "problema", "problem", "nao", "não", "not",
}


def _delivery_alarm_token_family(token: str) -> str:
    value = _normalize_text(token)
    families = (
        (("oleo", "óleo", "oil"), "oleo"),
        (("lubr", "lubric"), "lubr"),
        (("refriger", "coolant", "cooling", "chiller"), "refrig"),
        (("pressao", "pressão", "pressure"), "press"),
        (("temper", "temperature"), "temper"),
        (("tensao", "tensão", "voltage"), "tensao"),
        (("eletric", "electric"), "eletric"),
        (("pneumat", "pneumatic"), "pneumat"),
        (("hidraul", "hydraulic"), "hidraul"),
        (("comunic", "communication"), "comunic"),
        (("posicao", "posição", "position"), "posicao"),
        (("nivel", "nível", "level"), "nivel"),
        (("fluxo", "flow"), "fluxo"),
        (("filtro", "filter"), "filtro"),
        (("bomba", "pump"), "bomba"),
        (("motor",), "motor"),
        (("sensor",), "sensor"),
        (("shutter", "obturador"), "shutter"),
        (("gas", "gás"), "gas"),
        (("agua", "água", "water"), "agua"),
        (("tubo", "tube"), "tubo"),
        (("sustent", "support"), "sustent"),
        (("rele", "relé", "relay"), "rele"),
        (("fusivel", "fusível", "fuse"), "fusivel"),
        (("vibr",), "vibr"),
        (("emerg",), "emerg"),
        (("curso", "limit"), "curso"),
        (("sold", "weld"), "solda"),
        (("torcao", "torção", "twist"), "torcao"),
        (("carreg", "load"), "carreg"),
        (("rebarba", "burr"), "rebarba"),
        (("lente", "lens"), "lente"),
        (("laser",), "laser"),
    )
    for prefixes, family in families:
        if any(value.startswith(_normalize_text(prefix)) for prefix in prefixes):
            return family
    return value


def _delivery_alarm_tokens(value: Any) -> set[str]:
    normalized = _normalize_text(str(value or ""))
    raw_tokens = re.findall(r"[a-z0-9]+", normalized)
    output: set[str] = set()
    for token in raw_tokens:
        if token in _DELIVERY_ALARM_TOKEN_STOPWORDS or len(token) < 2:
            continue
        output.add(_delivery_alarm_token_family(token))
    return output


def _delivery_item_core_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("description") or "")[:420],
        *[str(value) for value in _split_text_values(item.get("causes"))[:5]],
        *[str(value) for value in _split_text_values(item.get("effects"))[:5]],
    ]
    return " ".join(part for part in parts if part.strip())


def _delivery_is_generic_failure_title(title: str) -> bool:
    normalized = _normalize_text(str(title or ""))
    generic_patterns = (
        r"^falha\s+(?:(?:no|na|nos|nas|do|da|dos|das|em)\s+)?",
        r"^problemas?\s+(?:(?:no|na|nos|nas|do|da|dos|das|em)\s+)?",
        r"^avaria\s+(?:(?:no|na|nos|nas|do|da|dos|das|em)\s+)?",
        r"^erro\s+geral\b",
        r"^system failure\b",
        r"^general fault\b",
        r"^general failure\b",
        r"^generic fault\b",
    )
    return any(re.search(pattern, normalized) for pattern in generic_patterns)

def _delivery_is_generic_section_heading_record(item: dict[str, Any]) -> bool:
    """Detecta pseudo-falhas criadas pela parafrase de um cabecalho de secao.

    O guard v2 cobre tambem registros legados que receberam causas/efeitos/acoes
    fabricados pelo LLM depois que um simples cabecalho de catalogo foi convertido
    em "Falha no sistema ...". Campos estruturados, sozinhos, nao podem salvar
    esse registro quando titulo e descricao continuam sendo apenas uma parafrase
    generica do nome da secao.
    """
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    title_n = _normalize_text(title)
    desc_n = _normalize_text(description)
    if not title_n or not desc_n:
        return False

    generic_prefixes = (
        "falha no sistema ", "falha na sistema ", "falhas no sistema ",
        "problema no sistema ", "problemas no sistema ", "avaria no sistema ",
        "failure in the system ", "system failure ", "system fault ",
        "guasto nel sistema ", "errore nel sistema ",
    )
    if not title_n.startswith(generic_prefixes):
        return False

    def core(value: str) -> str:
        value = _normalize_text(value)
        value = re.sub(r"\b(?:pagina|page|pag|secao|section|capitulo|chapter)\b.*$", "", value).strip()
        value = re.sub(r"^(?:falha|falhas|problema|problemas|avaria|failure|fault|guasto|errore)\s+", "", value)
        value = re.sub(r"^(?:no|na|nos|nas|do|da|dos|das|em|in|nel|nella)\s+", "", value)
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip(" .,:;-_")

    title_core = core(title_n)
    desc_core = core(desc_n)
    if not title_core or not desc_core:
        return False

    title_tokens = set(title_core.split())
    desc_tokens = set(desc_core.split())
    overlap = len(title_tokens & desc_tokens) / max(1, len(title_tokens))
    sequence = SequenceMatcher(None, title_core, desc_core).ratio()
    list_like = any(marker in desc_n for marker in (
        " incluindo ", " composto por ", " composta por ", " components ",
        "componentes", "fabricante", "manufacturer", "produtor", "codigo",
        "tipo ", "pecas", "spare parts", "replacement parts", "parts list", "ricambi",
    ))
    strong_paraphrase = overlap >= 0.82 and (sequence >= 0.72 or list_like)
    if not strong_paraphrase:
        return False

    # Fenomenos tecnicos concretos impedem a remocao automatica. A lista e
    # propositalmente composta por estados/anomalias, nao por nomes de componentes.
    anomaly_markers = (
        "nao funciona", "nao funcion", "nao ativo", "nao ativa", "nao ating",
        "insuficiente", "excessiv", "entupid", "obstru", "vazamento", "fuga",
        "quebra", "quebrad", "trav", "bloque", "desgaste", "superaquec",
        "temperatura alta", "temperatura baixa", "pressao alta", "pressao baixa",
        "nivel alto", "nivel baixo", "fluxo baixo", "fluxo insuf", "perda de",
        "interrup", "curto circuito", "sobrecarg", "desalinh", "colis",
        "comunicacao perdida", "off line", "offline", "timeout", "fora de faixa",
        "fora do limite", "posicao nao", "falha de sensor", "sensor com falha",
        "alarme ativo", "avaria ativa", "defeito",
        "does not work", "not working", "not active", "not reached", "insufficient",
        "clogged", "blocked", "leak", "broken", "overheat", "overpressure",
        "underpressure", "low level", "low flow", "communication lost",
    )
    has_specific_anomaly = any(marker in desc_n for marker in anomaly_markers)

    # Caso legado observado em catalogos: o titulo "Falha no sistema X" e a
    # descricao "Falhas no sistema X, incluindo A/B/C" sao apenas a secao/lista
    # reescrita. Mesmo que o LLM tenha preenchido campos estruturados depois,
    # continua nao havendo fenomeno tecnico especifico na evidencia textual.
    description_is_generic_heading = desc_n.startswith((
        "falhas no sistema ", "falha no sistema ", "problemas no sistema ",
        "problema no sistema ", "avaria no sistema ", "system failure ",
        "system fault ", "failure in the system ",
    ))
    if description_is_generic_heading and not has_specific_anomaly:
        return True

    structured: list[Any] = []
    for field in ("causes", "effects", "actions", "cause_groups"):
        value = item.get(field)
        if isinstance(value, list):
            structured.extend(entry for entry in value if entry)
        elif value:
            structured.append(value)

    # Fora do padrao legado acima, preserva a heuristica conservadora antiga:
    # evidencia estruturada explicita evita remocao automatica.
    if structured:
        return False

    return not has_specific_anomaly


def _delivery_non_failure_reason(item: dict[str, Any]) -> str:
    """Retorna motivo somente para nao-falhas deterministicas de alta confianca."""
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    normalized = _normalize_text(title)
    if not normalized:
        return "empty_title"

    first_word = normalized.split()[0] if normalized.split() else ""
    imperative_words = {
        "levar", "colocar", "selecionar", "usar", "utilizar", "evitar", "verificar", "pressionar",
        "deslocar", "aguardar", "manter", "fechar", "abrir", "resfriar", "ajustar", "instalar",
        "substituir", "limpar", "remover", "acionar", "desativar", "ativar", "move", "select", "use",
        "check", "verify", "press", "wait", "keep", "close", "open", "replace", "clean", "remove",
    }
    if first_word in imperative_words:
        return "procedure_or_instruction"

    if normalized.startswith("modo ") and any(
        token in normalized
        for token in ("manual", "automatic", "semiautom", "semi automatic", "jog", "setup", "set up", "assistencia", "service")
    ):
        return "operational_mode"

    health_or_human_effect_markers = (
        "febre", "intoxic", "lesao", "ferimento", "queimadura", "asfix", "irritacao",
        "poison", "injury", "burn injury", "human exposure",
    )
    if any(marker in normalized for marker in health_or_human_effect_markers):
        return "human_health_effect"

    # Emissao/formacao de fumos toxicos por processo/material e perigo ocupacional,
    # nao perda de funcao do equipamento. Falhas do EXAUSTOR/ASPIRADOR continuam
    # validas porque possuem o componente tecnico no proprio titulo.
    toxic_fume_patterns = (
        "formacao de fumos toxicos", "formacao de fumos", "emissao de fumos toxicos",
        "inalacao de fumos", "toxic fumes", "toxic smoke formation",
    )
    if any(pattern in normalized for pattern in toxic_fume_patterns) and not any(
        component in normalized for component in ("aspirador", "exaustor", "extrator", "ventilador", "filter", "filtro")
    ):
        return "process_or_occupational_hazard"

    if normalized.startswith(("risco de ", "perigo de ", "risk of ", "hazard of ")):
        return "safety_hazard"
    if "acesso nao autorizado" in normalized or "unauthorized access" in normalized:
        return "safety_hazard"

    if _delivery_is_generic_section_heading_record(item):
        return "generic_section_heading"

    normalized_description = _normalize_text(description)
    if (
        any(term in normalized_description for term in ("uso por parte do operador e absolutamente proibido", "reserved to service", "reservado ao servico"))
        and not any(term in normalized for term in ("falha", "erro", "avaria", "nao funciona"))
    ):
        return "prohibition_or_restricted_operation"

    return ""

def _prune_weak_alarm_assignments(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Valida os códigos e prefere sempre o melhor candidato oficial forte.

    Não confiamos na seleção inicial do LLM: mesmo um código aparentemente
    válido pode ser apenas um match lexical mais fraco que outro código oficial.
    Por isso, além de validar os selecionados, sempre comparamos candidatos
    lexicais do documento e permitimos substituir por um match claramente melhor.
    """
    trusted_rows = [
        row for row in alarm_rows
        if str(row.get("code") or "").strip()
        and str(row.get("source_kind") or "").lower() != "message"
    ]
    rows_by_code = {
        _normalize_text(str(row.get("code") or "")): row
        for row in trusted_rows
    }

    prepared_rows: list[tuple[dict[str, Any], set[str]]] = []
    cheap_stop = {
        "de", "da", "do", "das", "dos", "a", "o", "e", "em", "na", "no",
        "para", "com", "sem", "por", "um", "uma", "the", "of", "and", "to",
        "falha", "erro", "avaria", "alarme", "fault", "failure", "error",
    }
    for row in trusted_rows:
        label = _normalize_text(str(row.get("label") or ""))
        tokens = {
            token for token in re.findall(r"[a-z0-9]+", label)
            if len(token) >= 3 and token not in cheap_stop and not token.isdigit()
        }
        prepared_rows.append((row, tokens))

    for item in items:
        scored_by_code: dict[str, tuple[int, dict[str, Any]]] = {}

        # 1) códigos que vieram do modelo/extrator, somente se passarem a trava.
        for code in _normalize_alarm_codes(item.get("alarm_codes")):
            row = rows_by_code.get(_normalize_text(code))
            if not row:
                continue
            score = _alarm_row_match_score(item, row)
            if score <= 0:
                continue
            key = _normalize_text(str(row.get("code") or code))
            scored_by_code[key] = max(scored_by_code.get(key, (0, row)), (score, row), key=lambda pair: pair[0])

        # 2) busca local pelos melhores rótulos oficiais. Executada SEMPRE para
        # corrigir seleções válidas porém subótimas (ex.: programa-peça x soldagem).
        title_norm = _normalize_text(str(item.get("title") or ""))
        title_tokens = {
            token for token in re.findall(r"[a-z0-9]+", title_norm)
            if len(token) >= 3 and token not in cheap_stop and not token.isdigit()
        }
        lexical_ranked: list[tuple[float, dict[str, Any]]] = []
        for row, row_tokens in prepared_rows:
            overlap = len(title_tokens.intersection(row_tokens))
            if overlap < 2:
                continue
            coverage = overlap / max(1, len(title_tokens))
            lexical_ranked.append((coverage, row))
        lexical_ranked.sort(key=lambda pair: pair[0], reverse=True)
        for _, row in lexical_ranked[:24]:
            score = _alarm_row_match_score(item, row)
            if score <= 0:
                continue
            key = _normalize_text(str(row.get("code") or ""))
            current = scored_by_code.get(key)
            if current is None or score > current[0]:
                scored_by_code[key] = (score, row)

        validated = sorted(scored_by_code.values(), key=lambda pair: pair[0], reverse=True)
        if not validated:
            item["alarm_codes"] = []
            continue

        best_score, best_row = validated[0]
        kept_rows = [best_row]
        title_ordinals = _delivery_component_ordinals(str(item.get("title") or ""))
        best_label = _normalize_text(str(best_row.get("label") or ""))

        # Secundários só entram quando são praticamente a mesma família oficial
        # e não há ordinal explícito no título.
        if not title_ordinals:
            for score, row in validated[1:]:
                if len(kept_rows) >= 3:
                    break
                if score < max(52, best_score - 8):
                    continue
                label = _normalize_text(str(row.get("label") or ""))
                simplified_best = re.sub(r"\b(?:1|2|3|4|5|6|7|8|9|10)\b", "#", best_label)
                simplified_label = re.sub(r"\b(?:1|2|3|4|5|6|7|8|9|10)\b", "#", label)
                if SequenceMatcher(None, simplified_best, simplified_label).ratio() >= 0.88:
                    kept_rows.append(row)

        item["alarm_codes"] = _normalize_alarm_codes([row.get("code") for row in kept_rows])[:3]
        pages = set(_normalize_source_pages(item.get("source_pages")))
        pages.update(
            int(row["page"]) for row in kept_rows
            if isinstance(row.get("page"), int) and int(row["page"]) > 0
        )
        item["source_pages"] = sorted(pages)[:4]
    return items


_DELIVERY_ALARM_CONDITION_TOKENS = {
    "posicao", "press", "nivel", "fluxo", "temper", "tensao", "protecao", "alcance",
    "alimentacao", "calib", "centragem", "focal", "transfer", "timeout", "contato",
    "configuracao", "interrupcao", "variacao", "condensacao", "desgaste", "offline",
    "dano", "sobrecarga", "subtensao", "sobretensao", "corrente", "fase", "invalido",
    "ativacao", "desativacao", "habilitacao", "comando", "medida", "dados", "bloqueio",
    "aberto", "fechado", "insuficiente", "baixo", "excessivo", "compatibilidade", "ausencia",
    "desabilitado", "validade", "bypass", "obstrucao",
}

_DELIVERY_COMPONENT_WORDS = (
    "sustentador", "support", "descarregador", "unloader", "motor", "bomba", "pump",
    "eixo", "axis", "gas", "ramo", "branch", "sensor", "valvula", "valve", "shutter",
    "obturador", "mandril", "chuck", "luneta", "steady", "centrador", "carro", "carriage",
    "linha", "line", "inverter", "laser", "chiller", "refrigerador", "cooler", "filtro", "filter",
    "alavanca", "lever", "cilindro", "cylinder", "encoder", "rele", "relay", "fusivel", "fuse",
    "lente", "lens", "bico", "nozzle", "cabeca", "head", "correia", "belt", "corrente", "chain",
    "ventilador", "fan", "compressor", "secador", "dryer", "fonte", "source", "gerador", "generator",
    "plc", "cnc", "quadro", "panel", "porta", "door", "barreira", "barrier",
    "alinhador", "aligner", "medidor", "espada", "espadas", "descarga", "inclinacao",
    "carregador", "loader", "barra", "emergencia",
)

def _delivery_alarm_extended_family(token: str) -> str:
    value = _delivery_alarm_token_family(token)
    normalized = _normalize_text(value)
    extra = (
        (("descalibr", "calibr", "afinac", "tuning"), "calib"),
        (("centrag", "centraliz", "centering", "centring"), "centragem"),
        (("focal",), "focal"),
        (("compens",), "compens"),
        (("transfer",), "transfer"),
        (("configur",), "configuracao"),
        (("interromp", "interrupt", "broken"), "interrupcao"),
        (("protec", "protect", "termomag", "trip"), "protecao"),
        (("ating", "alcanc", "reach", "attain"), "alcance"),
        (("toc", "contact", "colis"), "contato"),
        (("offline",), "offline"),
        (("incompat", "compatib", "compat"), "compatibilidade"),
        (("invalid", "inval"), "invalido"),
        (("valid",), "validade"),
        (("bypass",), "bypass"),
        (("ausen", "missing"), "ausencia"),
        (("bloque", "jam"), "bloqueio"),
        (("desabil", "disable"), "desabilitado"),
        (("abert", "open"), "aberto"),
        (("fech", "closed"), "fechado"),
        (("insuf", "insuff", "baixo", "low"), "insuficiente"),
        (("excess",), "excessivo"),
        (("variac", "variation"), "variacao"),
        (("condens",), "condensacao"),
        (("sobrecarg", "overload"), "sobrecarga"),
        (("subtens", "undervolt"), "subtensao"),
        (("sobretens", "overvolt"), "sobretensao"),
        (("corrente", "current"), "corrente"),
        (("fase", "phase"), "fase"),
        (("ativ", "activation"), "ativacao"),
        (("desativ", "deactivation"), "desativacao"),
        (("habilit", "enable"), "habilitacao"),
        (("comando", "command"), "comando"),
        (("medid", "measure"), "medida"),
        (("dado", "data"), "dados"),
        (("rolament", "bearing"), "rolamento"),
        (("optica", "optical"), "optica"),
        (("mandril", "chuck"), "mandril"),
        (("luneta", "steady"), "luneta"),
        (("feixe", "beam"), "feixe"),
        (("circuit",), "circuito"),
        (("profibus",), "profibus"),
        (("capacitiv",), "capacitivo"),
        (("alavanc", "lever"), "alavanca"),
        (("centrador",), "centrador"),
        (("acionamento", "drive"), "acionamento"),
        (("alinhador", "aligner"), "alinhador"),
        (("medidor",), "medidor"),
        (("espada",), "espada"),
        (("descarg", "unload"), "descarga"),
        (("inclin",), "inclinacao"),
        (("cilind",), "cilindro"),
        (("barra",), "barra"),
        (("dinamic", "dynamic"), "dinamico"),
        (("entup", "obstru", "clog"), "obstrucao"),
        (("program",), "programa"),
        (("usinag", "machin"), "usinagem"),
        (("peca", "piece", "part"), "peca"),
        (("procura", "busca", "search"), "procura"),
        (("ciclo", "cycle"), "ciclo"),
    )
    for prefixes, family in extra:
        if any(normalized.startswith(prefix) for prefix in prefixes):
            return family
    return normalized



def _delivery_alarm_direct_tokens(value: Any) -> set[str]:
    normalized = _normalize_text(str(value or ""))
    raw_tokens = re.findall(r"[a-z0-9]+", normalized)
    output: set[str] = set()
    for token in raw_tokens:
        if token in _DELIVERY_ALARM_TOKEN_STOPWORDS or len(token) < 2 or token.isdigit():
            continue
        family = _delivery_alarm_extended_family(token)
        if family:
            output.add(family)
    return output


_DELIVERY_ORDINAL_WORDS = {
    "primeiro": "1", "primeira": "1", "first": "1", "primero": "1", "primera": "1",
    "segundo": "2", "segunda": "2", "second": "2", "segundo_es": "2", "segunda_es": "2",
    "terceiro": "3", "terceira": "3", "third": "3", "tercero": "3", "tercera": "3",
    "quarto": "4", "quarta": "4", "fourth": "4", "cuarto": "4", "cuarta": "4",
    "quinto": "5", "quinta": "5", "fifth": "5", "quinto_es": "5", "quinta_es": "5",
}

_DELIVERY_STRONG_COMPONENT_FAMILIES = {
    "sustent", "motor", "bomba", "sensor", "shutter", "mandril", "luneta", "centrador",
    "inverter", "laser", "refrig", "filtro", "rele", "fusivel", "lente", "alavanca",
    "valvula", "gas", "tubo", "pneumat", "hidraul", "eletric", "acionamento",
    "capacitivo", "rolamento", "optica", "feixe", "bico", "cabeca",
    "alinhador", "medidor", "espada", "descarga", "inclinacao", "cilindro",
    "carreg", "barra", "emerg",
}

_DELIVERY_DOMAIN_QUALIFIER_FAMILIES = {
    "oleo", "lubr", "solda", "gas", "agua", "refrig", "optica", "focal", "centragem",
    "torcao", "capacitivo", "profibus", "tubo", "rolamento", "bico", "cabeca", "mandril",
    "luneta", "sustent", "pneumat", "hidraul", "eletric", "laser",
}

_DELIVERY_GENERIC_SEMANTIC_TOKENS = {
    "int", "interno", "interna", "automatico", "automatica", "programado", "programada",
    "utilizado", "utilizada", "montado", "montada", "requerido", "requerida", "uso",
}


def _delivery_anomaly_signatures(value: Any) -> set[str]:
    """Assinaturas da NATUREZA da anomalia derivadas apenas do título/rótulo oficial."""
    text = _normalize_text(str(value or ""))
    signatures: set[str] = set()
    patterns = (
        ("not_reached", r"(?:nao\s+(?:foi\s+)?(?:ating|alcanc)|not\s+(?:reach|attain))"),
        ("calibration", r"(?:descalibr|calibr|afinac|tuning)"),
        ("interruption", r"(?:interromp|wire\s+break|cable\s+break|open\s+circuit|broken\s+(?:wire|cable))"),
        ("contact_collision", r"(?:\btoc\w*\b|contato|colis)"),
        ("communication_offline", r"(?:off\s*line|offline|profibus|communication|comunicacao)"),
        ("protection_trip", r"(?:protec|termomag|\btrip\w*\b|dispar)"),
        ("not_active", r"(?:nao\s+(?:esta\s+)?ativ|nao\s+ativo|not\s+active|not\s+enabled|nao\s+habilit)"),
        ("incompatible", r"(?:incompat|nao\s+compativ|not\s+compatible)"),
        ("invalid", r"(?:inval|nao\s+valid|not\s+valid)"),
        ("bypass", r"\bbypass\b"),
        ("inhibited", r"(?:bloqueio|inibi|desabil|inhibit|disable)"),
        ("jammed", r"(?:trav\w*|\bjam\w*)"),
        ("clogged", r"(?:entup|obstru|\bclog\w*)"),
        ("missing", r"(?:ausen|missing|nao\s+presente|nao\s+encontrad)"),
        ("open", r"(?:\babert\w*\b|\bopen\b)"),
        ("closed", r"(?:\bfechad\w*\b|\bclosed\b)"),
        ("insufficient_low", r"(?:insuf|\bbaixo\b|\bbaixa\b|\blow\b)"),
        ("excessive", r"(?:excess|demasiad)"),
        ("leak", r"(?:vazamento|fuga|\bleak\w*)"),
        ("contamination", r"(?:contamin|poluid|sujeira|dirty)"),
        ("wear_damage", r"(?:desgast|\bdano\b|\bdanos\b|damage|wear)"),
        ("vibration", r"(?:vibr)"),
        ("short_circuit", r"(?:curto\s+circuit|short\s+circuit)"),
        ("overload", r"(?:sobrecarg|overload)"),
        ("misalignment", r"(?:desalinh|fora\s+de\s+alinh|misalign)"),
        ("oxidation_corrosion", r"(?:oxid|corros|\brust\w*)"),
        ("expired", r"(?:vencid|expir)"),
        ("timeout", r"(?:time[ -]?out|\btimeout\b)"),
        ("selection_identity", r"(?:selec(?:ao|ion).*incorret|wrong\s+selection|erro\s+(?:no\s+)?reconhecimento|reconhecimento.*nao\s+defin)"),
        ("intervention_trip", r"(?:intervencao|interveio|intervention)"),
        ("generic_fault", r"(?:\bfalha\b|\bavaria\b|\berro\b|\bfault\b|\bfailure\b)"),
    )
    for name, pattern in patterns:
        if re.search(pattern, text):
            signatures.add(name)
    return signatures




_DELIVERY_CONTEXT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("program_piece", (
        r"programa[- ]?peca", r"programa de peca", r"part program",
        r"usinagem program", r"programad[oa].*usinag", r"machining program",
    )),
    ("weld_search", (
        r"procura (?:de )?sold", r"busca (?:de )?sold", r"weld search", r"seam search",
    )),
    ("capacitive_calibration", (
        r"calibr.*sensor capac", r"sensor capac.*calibr", r"capacitive sensor.*calibr",
    )),
    ("focal_search", (
        r"(?:procura|busca).*focal", r"focal.*(?:procura|busca)", r"focus search",
    )),
    ("lot_transfer", (
        r"transfer.*lote", r"lote.*transfer", r"batch transfer",
    )),
)


def _delivery_context_signatures(value: Any) -> set[str]:
    text = _normalize_text(str(value or ""))
    signatures: set[str] = set()
    for name, patterns in _DELIVERY_CONTEXT_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            signatures.add(name)
    return signatures


def _delivery_component_tokens(value: Any) -> set[str]:
    return _delivery_alarm_direct_tokens(value).intersection(_DELIVERY_STRONG_COMPONENT_FAMILIES)


def _delivery_specific_qualifiers(value: Any) -> set[str]:
    tokens = _delivery_alarm_direct_tokens(value)
    return {
        token for token in tokens
        if token not in _DELIVERY_STRONG_COMPONENT_FAMILIES
        and token not in _DELIVERY_ALARM_CONDITION_TOKENS
        and token not in _DELIVERY_GENERIC_SEMANTIC_TOKENS
        and len(token) >= 3
    }


def _delivery_component_ordinals(value: Any) -> dict[str, set[str]]:
    """Extrai indice/ordinal LOCAL ao componente, evitando numeros de paginas/I/O ou de outras linhas."""
    normalized = _normalize_text(str(value or ""))
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    words = normalized.split()
    result: dict[str, set[str]] = {}

    ordinal_words = dict(_DELIVERY_ORDINAL_WORDS)
    component_keys = {_normalize_text(word): _delivery_alarm_extended_family(word) for word in _DELIVERY_COMPONENT_WORDS}

    for index, word in enumerate(words):
        family = component_keys.get(word)
        if not family:
            continue
        values: set[str] = set()
        # Somente vizinhos realmente ligados ao nome: "sustentador 1" / "primeiro sustentador".
        neighborhood = []
        if index > 0:
            neighborhood.append(words[index - 1])
        if index + 1 < len(words):
            neighborhood.append(words[index + 1])
        if index + 2 < len(words) and words[index + 1] in {"numero", "n", "no"}:
            neighborhood.append(words[index + 2])
        for candidate in neighborhood:
            if candidate.isdigit() and 1 <= int(candidate) <= 20:
                values.add(str(int(candidate)))
            mapped = ordinal_words.get(candidate)
            if mapped:
                values.add(mapped)
        if values:
            result.setdefault(family, set()).update(values)
    return result



def _delivery_alarm_direct_title_match(item: dict[str, Any], row: dict[str, Any]) -> bool:
    """Trava semântica: título e rótulo oficial devem representar a MESMA falha.

    Palavras de contexto/subsistema não bastam. Componentes diferentes, ordinais
    diferentes e naturezas de anomalia diferentes bloqueiam a associação.
    """
    title = str(item.get("title") or "").strip()
    label = str(row.get("label") or "").strip()
    if not title or not label:
        return False

    normalized_title = _normalize_text(title)
    normalized_label = _normalize_text(label)
    title_tokens = _delivery_alarm_direct_tokens(title)
    label_tokens = _delivery_alarm_direct_tokens(label)
    if not title_tokens or not label_tokens:
        return False

    title_context = _delivery_context_signatures(title)
    label_context = _delivery_context_signatures(label)
    # Contextos operacionais explicitamente diferentes são incompatíveis mesmo
    # quando compartilham o mesmo componente e a mesma palavra "incompatível".
    # Ex.: bico incompatível com programa-peça != bico incompatível com procura de soldagem.
    if title_context and label_context and title_context.isdisjoint(label_context):
        return False

    title_ordinals = _delivery_component_ordinals(title)
    label_ordinals = _delivery_component_ordinals(label)
    if title_ordinals:
        row_ordinals = label_ordinals or _delivery_component_ordinals(str(row.get("text") or "")[:360])
        for component, title_values in title_ordinals.items():
            if component not in row_ordinals:
                if component in _delivery_component_tokens(label):
                    return False
                continue
            if row_ordinals[component] != title_values:
                return False

    title_components = _delivery_component_tokens(title)
    label_components = _delivery_component_tokens(label)
    if title_components and label_components:
        if title_components.isdisjoint(label_components):
            return False
        # Quando o título cita dois ou mais componentes, todos são identidade
        # técnica. Ex.: mandril+luneta não equivale a mandril+centrador.
        if len(title_components) >= 2 and not title_components.issubset(label_components):
            return False
        coverage = len(title_components.intersection(label_components)) / max(1, len(title_components))
        if coverage < 0.75:
            return False

    title_anomalies = _delivery_anomaly_signatures(title)
    label_anomalies = _delivery_anomaly_signatures(label)
    non_discriminative = {"generic_fault", "intervention_trip"}
    title_distinct = title_anomalies.difference(non_discriminative)
    label_distinct = label_anomalies.difference(non_discriminative)

    # indisponível/inibido são equivalentes; demais naturezas distintas não são.
    def anomaly_groups(values: set[str]) -> set[str]:
        groups = set(values)
        if groups.intersection({"inhibited", "not_active"}):
            groups.add("unavailable")
        return groups

    if title_distinct:
        if not label_distinct:
            return False
        if anomaly_groups(title_distinct).isdisjoint(anomaly_groups(label_distinct)):
            return False

    # Títulos vagos ("Falha no...", "Problemas na...") nunca recebem código
    # apenas por compartilhar o subsistema. O rótulo oficial precisa expressar
    # uma anomalia real e, quando há componente no título, identificá-lo.
    if _delivery_is_generic_failure_title(title):
        faultish = label_anomalies.intersection({
            "generic_fault", "intervention_trip", "communication_offline",
            "protection_trip", "not_reached", "calibration", "interruption",
            "contact_collision", "incompatible", "invalid", "jammed", "missing",
            "insufficient_low", "excessive", "leak", "contamination", "wear_damage",
            "vibration", "short_circuit", "overload", "timeout", "not_active",
        })
        if not faultish:
            return False
        if title_components and not label_components:
            return False

    overlap = title_tokens.intersection(label_tokens)
    if not overlap:
        return False

    title_conditions = title_tokens.intersection(_DELIVERY_ALARM_CONDITION_TOKENS)
    label_conditions = label_tokens.intersection(_DELIVERY_ALARM_CONDITION_TOKENS)
    if title_conditions and label_conditions and title_conditions.isdisjoint(label_conditions):
        return False

    title_domain = title_tokens.intersection(_DELIVERY_DOMAIN_QUALIFIER_FAMILIES)
    label_domain = label_tokens.intersection(_DELIVERY_DOMAIN_QUALIFIER_FAMILIES)
    sequence = SequenceMatcher(None, normalized_title, normalized_label).ratio()
    containment = (
        len(normalized_title) >= 8 and len(normalized_label) >= 8
        and (normalized_title in normalized_label or normalized_label in normalized_title)
    )
    if title_domain and label_domain and title_domain.isdisjoint(label_domain):
        if sequence < 0.86 and not containment:
            return False

    # Sem uma âncora de componente+anomalia, exige cobertura lexical forte.
    anomaly_shared = bool(anomaly_groups(title_distinct).intersection(anomaly_groups(label_distinct)))
    component_shared = bool(title_components.intersection(label_components))
    semantic_anchor = component_shared and anomaly_shared
    coverage = len(overlap) / max(1, len(title_tokens))
    if not semantic_anchor:
        if len(title_tokens) >= 4 and coverage < 0.55 and sequence < 0.80 and not containment:
            return False
        if len(title_tokens) == 3 and coverage < 0.75 and sequence < 0.80 and not containment:
            return False

    # Título genérico sem o contexto especializado do rótulo não deve herdar o
    # código apenas por palavras como "barra" ou "incompatível".
    if label_context and not title_context and len(title_tokens) <= 4:
        if coverage < 0.70 and sequence < 0.82 and not containment:
            return False

    return True


def _alarm_row_match_score(item: dict[str, Any], row: dict[str, Any]) -> int:
    title = str(item.get("title") or "").strip()
    row_label = str(row.get("label") or "").strip()
    if not title or not row_label:
        return 0
    if not _delivery_alarm_direct_title_match(item, row):
        return 0

    normalized_title = _normalize_text(title)
    normalized_label = _normalize_text(row_label)
    title_tokens = _delivery_alarm_direct_tokens(title)
    label_tokens = _delivery_alarm_direct_tokens(row_label)
    overlap = title_tokens.intersection(label_tokens)
    sequence = SequenceMatcher(None, normalized_title, normalized_label).ratio()

    score = 12 * len(overlap)
    score += int(round((len(overlap) / max(1, len(title_tokens))) * 20))
    component_overlap = _delivery_component_tokens(title).intersection(_delivery_component_tokens(row_label))
    score += 10 * len(component_overlap)
    anomaly_overlap = _delivery_anomaly_signatures(title).intersection(_delivery_anomaly_signatures(row_label))
    score += 12 * len(anomaly_overlap)
    qualifier_overlap = _delivery_specific_qualifiers(title).intersection(_delivery_specific_qualifiers(row_label))
    score += 5 * min(3, len(qualifier_overlap))

    if sequence >= 0.88:
        score += 24
    elif sequence >= 0.78:
        score += 16
    elif sequence >= 0.66:
        score += 8
    if normalized_title in normalized_label or normalized_label in normalized_title:
        score += 18

    minimum = 52 if _delivery_is_generic_failure_title(title) else 38
    return score if score >= minimum else 0


def _candidate_alarm_rows_for_item(
    item: dict[str, Any],
    alarm_rows: list[dict[str, Any]],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in alarm_rows:
        if str(row.get("source_kind") or "").lower() == "message":
            continue
        score = _alarm_row_match_score(item, row)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda value: value[0], reverse=True)
    return [row for _, row in scored[:limit]]

def _deterministic_alarm_rows_for_item(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback conservador usado quando a validação semântica não responde."""
    ranked = sorted(
        ((_alarm_row_match_score(item, row), row) for row in candidates),
        key=lambda value: value[0],
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for score, row in ranked:
        if score < 22:
            continue
        selected.append(row)
        if len(selected) >= 3:
            break
    return selected


def _verify_alarm_assignments(
    runtime,
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Valida semanticamente código ↔ falha e substitui associações frouxas.

    O algoritmo gera poucos candidatos por item com regras determinísticas e usa
    o LLM somente como revisor binário/semântico entre candidatos já confiáveis da
    tabela. O retorno é validado contra esses candidatos e limitado a três códigos.
    """
    if not items:
        return items

    rows_by_code = {
        _normalize_text(str(row.get("code") or "")): row
        for row in alarm_rows
        if str(row.get("code") or "").strip()
        and str(row.get("source_kind") or "").lower() != "message"
    }

    candidates_by_mode: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        mode_id = str(item.get("mode_id") or "").strip()
        if not mode_id:
            continue
        candidates = _candidate_alarm_rows_for_item(item, list(rows_by_code.values()))
        candidates_by_mode[mode_id] = candidates

    selected_by_mode: dict[str, list[str]] = {}
    compact_items: list[dict[str, Any]] = []
    for item in items:
        mode_id = str(item.get("mode_id") or "").strip()
        candidates = candidates_by_mode.get(mode_id, [])
        if not mode_id or not candidates:
            continue
        compact_items.append({
            "mode_id": mode_id,
            "title": str(item.get("title") or "")[:220],
            "description": str(item.get("description") or "")[:320],
            "causes": _split_text_values(item.get("causes"))[:3],
            "effects": _split_text_values(item.get("effects"))[:3],
            "candidates": [
                {
                    "code": str(row.get("code") or ""),
                    "page": row.get("page"),
                    "label": str(row.get("label") or "")[:220],
                    "evidence": str(row.get("text") or "")[:420],
                    "source_kind": row.get("source_kind"),
                }
                for row in candidates
            ],
        })

    # Pequenos lotes mantêm o julgamento focado e evitam truncamento de JSON.
    for start in range(0, len(compact_items), 8):
        batch = compact_items[start:start + 8]
        try:
            response = chat_claude(
                runtime,
                system=ALARM_ASSIGNMENT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": json.dumps({"items": batch}, ensure_ascii=False),
                }],
                max_tokens=1200,
            )
            parsed = _extract_json_object(response)
            raw_assignments = parsed.get("assignments")
        except Exception:
            raw_assignments = None

        if not isinstance(raw_assignments, dict):
            continue

        # Uma resposta JSON válida representa uma decisão deliberada do revisor.
        # Se um mode_id não vier no mapping, tratamos como "nenhum alarme" em vez
        # de reintroduzir associação pelo fallback lexical.
        for entry in batch:
            selected_by_mode.setdefault(str(entry.get("mode_id") or ""), [])

        candidate_codes_by_mode = {
            str(entry["mode_id"]): {
                _normalize_text(str(candidate.get("code") or "")): str(candidate.get("code") or "")
                for candidate in entry.get("candidates") or []
                if str(candidate.get("code") or "").strip()
            }
            for entry in batch
        }
        for mode_id, raw_codes in raw_assignments.items():
            safe_mode_id = str(mode_id or "").strip()
            available = candidate_codes_by_mode.get(safe_mode_id, {})
            if not available:
                continue
            validated: list[str] = []
            for raw_code in raw_codes if isinstance(raw_codes, list) else []:
                canonical = available.get(_normalize_text(str(raw_code or "")))
                if canonical and canonical not in validated:
                    validated.append(canonical)
                if len(validated) >= 3:
                    break
            selected_by_mode[safe_mode_id] = validated

    # Substitui, em vez de acumular, alarm_codes. Se o revisor não respondeu para
    # um modo específico, usa apenas o fallback determinístico de alta confiança.
    for item in items:
        mode_id = str(item.get("mode_id") or "").strip()
        candidates = candidates_by_mode.get(mode_id, [])
        selected_codes = selected_by_mode.get(mode_id)
        if selected_codes is None:
            selected_rows = _deterministic_alarm_rows_for_item(item, candidates)
        else:
            selected_rows = [
                rows_by_code[_normalize_text(code)]
                for code in selected_codes
                if _normalize_text(code) in rows_by_code
            ]

        item["alarm_codes"] = _normalize_alarm_codes([
            row.get("code") for row in selected_rows
        ])[:3]
        pages = set(_normalize_source_pages(item.get("source_pages")))
        pages.update(
            int(row["page"])
            for row in selected_rows
            if isinstance(row.get("page"), int) and int(row["page"]) > 0
        )
        item["source_pages"] = sorted(pages)[:4]

    return items



def _delivery_compound_alarm_branches(label: str) -> list[str]:
    """Retorna alternativas explícitas de um rótulo composto de alarme.

    Um simples slash não basta: pelo menos duas partes precisam carregar uma
    natureza de anomalia reconhecível. Isso evita tratar nomes de componente
    com barra como alarmes multi-condição.
    """
    raw = str(label or "").strip()
    if not raw:
        return []
    parts = [part.strip(" ,;:-") for part in re.split(r"\s*(?:/|\bou\b|\bor\b)\s*", raw, flags=re.IGNORECASE) if part.strip(" ,;:-")]
    if len(parts) < 2:
        return []
    anomaly_parts = [part for part in parts if _delivery_anomaly_signatures(part).difference({"generic_fault", "intervention_trip"})]
    return parts if len(anomaly_parts) >= 2 else []


def _delivery_title_branch_index(title: str, branches: list[str]) -> int | None:
    title_anomalies = _delivery_anomaly_signatures(title).difference({"generic_fault", "intervention_trip"})
    title_tokens = _delivery_alarm_direct_tokens(title)
    best: tuple[float, int] | None = None
    for index, branch in enumerate(branches):
        branch_anomalies = _delivery_anomaly_signatures(branch).difference({"generic_fault", "intervention_trip"})
        branch_tokens = _delivery_alarm_direct_tokens(branch)
        anomaly_overlap = len(title_anomalies.intersection(branch_anomalies))
        lexical_overlap = len(title_tokens.intersection(branch_tokens))
        lexical_coverage = lexical_overlap / max(1, len(title_tokens))
        score = (4.0 * anomaly_overlap) + lexical_coverage
        if anomaly_overlap == 0 and lexical_coverage < 0.45:
            continue
        if best is None or score > best[0]:
            best = (score, index)
    return best[1] if best is not None else None


def _delivery_titles_equivalent_for_same_alarm(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_title = str(left.get("title") or "").strip()
    right_title = str(right.get("title") or "").strip()
    if not left_title or not right_title:
        return False
    if str(left.get("category") or "") != str(right.get("category") or ""):
        return False

    left_ord = _delivery_component_ordinals(left_title)
    right_ord = _delivery_component_ordinals(right_title)
    for component in set(left_ord).intersection(right_ord):
        if left_ord[component] != right_ord[component]:
            return False

    left_anom = _delivery_anomaly_signatures(left_title).difference({"generic_fault", "intervention_trip"})
    right_anom = _delivery_anomaly_signatures(right_title).difference({"generic_fault", "intervention_trip"})
    if left_anom and right_anom and left_anom.isdisjoint(right_anom):
        return False

    left_tokens = _delivery_alarm_direct_tokens(left_title).difference(_DELIVERY_GENERIC_SEMANTIC_TOKENS)
    right_tokens = _delivery_alarm_direct_tokens(right_title).difference(_DELIVERY_GENERIC_SEMANTIC_TOKENS)
    union = left_tokens.union(right_tokens)
    jaccard = len(left_tokens.intersection(right_tokens)) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, _normalize_text(left_title), _normalize_text(right_title)).ratio()
    return bool((left_anom and left_anom == right_anom and jaccard >= 0.58) or jaccard >= 0.74 or sequence >= 0.84)


def _coalesce_equivalent_modes_for_shared_alarm(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mescla reformulações da MESMA condição antes de arbitrar código compartilhado."""
    if len(items) < 2:
        return items
    output = [dict(item) for item in items]
    changed = True
    while changed:
        changed = False
        for left_index in range(len(output)):
            left = output[left_index]
            left_codes = set(_normalize_alarm_codes(left.get("alarm_codes")))
            if not left_codes:
                continue
            for right_index in range(left_index + 1, len(output)):
                right = output[right_index]
                right_codes = set(_normalize_alarm_codes(right.get("alarm_codes")))
                if not left_codes.intersection(right_codes):
                    continue
                if not _delivery_titles_equivalent_for_same_alarm(left, right):
                    continue
                # Prefere o título com mais contexto técnico, mas evita títulos enormes.
                left_title = str(left.get("title") or "")
                right_title = str(right.get("title") or "")
                canonical = dict(left)
                if 0 < len(right_title) <= 120 and len(right_title) > len(left_title):
                    canonical["title"] = right_title
                output[left_index] = _merge_failure_mode_records(canonical, right)
                output.pop(right_index)
                changed = True
                break
            if changed:
                break
    return output


def _delivery_shared_alarm_is_justified(items: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    """Permite 1 código em múltiplos modos apenas quando o rótulo oficial é composto.

    Cada modo precisa casar fortemente com o alarme completo e ocupar uma alternativa
    distinta do próprio rótulo. Ex.: "filtro entupido/nível insuficiente".
    """
    if len(items) < 2:
        return False
    branches = _delivery_compound_alarm_branches(str(row.get("label") or ""))
    if len(branches) < len(items):
        return False
    used: set[int] = set()
    for item in items:
        if _alarm_row_match_score(item, row) < 38:
            return False
        branch_index = _delivery_title_branch_index(str(item.get("title") or ""), branches)
        if branch_index is None or branch_index in used:
            return False
        used.add(branch_index)
    return len(used) == len(items)


def _resolve_alarm_ownership(
    runtime,
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Garante propriedade única de cada código de alarme entre modos distintos.

    A validação por modo é feita antes por ``_verify_alarm_assignments``. Esta
    etapa olha globalmente a árvore. Se o mesmo código sobrar em duas falhas,
    compara as duas contra a linha oficial do alarme. Em ambiguidade real o
    código é removido de ambas, em vez de escolher arbitrariamente.
    """
    if not items:
        return items

    rows_by_code = {
        _normalize_text(str(row.get("code") or "")): row
        for row in alarm_rows
        if str(row.get("code") or "").strip()
        and str(row.get("source_kind") or "").lower() != "message"
    }

    owners: dict[str, list[dict[str, Any]]] = {}
    canonical_code: dict[str, str] = {}
    for item in items:
        for raw_code in _normalize_alarm_codes(item.get("alarm_codes")):
            key = _normalize_text(raw_code)
            if not key or key not in rows_by_code:
                continue
            canonical_code[key] = str(rows_by_code[key].get("code") or raw_code).strip()
            owners.setdefault(key, []).append(item)

    duplicates = {
        code_key: group
        for code_key, group in owners.items()
        if len({str(item.get("mode_id") or id(item)) for item in group}) > 1
    }
    # Alarmes oficialmente compostos podem representar alternativas técnicas
    # distintas. Preservamos o compartilhamento somente quando cada modo ocupa
    # uma alternativa diferente do rótulo oficial.
    duplicates = {
        code_key: group
        for code_key, group in duplicates.items()
        if not _delivery_shared_alarm_is_justified(group, rows_by_code[code_key])
    }
    if not duplicates:
        return items

    decisions: dict[str, str] = {}
    payload_items: list[dict[str, Any]] = []
    for code_key, group in duplicates.items():
        row = rows_by_code[code_key]
        payload_items.append({
            "code": canonical_code.get(code_key, str(row.get("code") or "")),
            "alarm": {
                "page": row.get("page"),
                "label": str(row.get("label") or "")[:240],
                "evidence": str(row.get("text") or "")[:520],
            },
            "modes": [
                {
                    "mode_id": str(item.get("mode_id") or ""),
                    "title": str(item.get("title") or "")[:220],
                    "description": str(item.get("description") or "")[:320],
                    "category": str(item.get("category") or "")[:120],
                    "causes": _split_text_values(item.get("causes"))[:3],
                    "effects": _split_text_values(item.get("effects"))[:3],
                }
                for item in group
            ],
        })

    ownership_system = """Você arbitra a propriedade de códigos de alarme entre modos de falha do MESMO manual técnico.\nRetorne SOMENTE JSON válido: {\"owners\":{\"codigo\":\"mode_id\"}}.\nPara cada código, escolha no máximo um mode_id apenas quando a descrição oficial do alarme representar diretamente a mesma condição técnica daquele modo.\nNão escolha por compartilhar apenas subsistema ou palavra genérica.\nSe dois modos forem igualmente plausíveis, ou nenhum representar diretamente o alarme, retorne string vazia para o código.\nPrecisão é mais importante que cobertura.\n"""

    for start in range(0, len(payload_items), 10):
        batch = payload_items[start:start + 10]
        try:
            response = chat_claude(
                runtime,
                system=ownership_system,
                messages=[{"role": "user", "content": json.dumps({"duplicates": batch}, ensure_ascii=False)}],
                max_tokens=900,
            )
            parsed = _extract_json_object(response)
            raw = parsed.get("owners")
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            continue
        for raw_code, raw_owner in raw.items():
            code_key = _normalize_text(str(raw_code or ""))
            if code_key not in duplicates:
                continue
            allowed_ids = {str(item.get("mode_id") or "") for item in duplicates[code_key]}
            owner_id = str(raw_owner or "").strip()
            decisions[code_key] = owner_id if owner_id in allowed_ids else ""

    # Fallback determinístico somente quando há vencedor forte e margem clara.
    for code_key, group in duplicates.items():
        if code_key in decisions:
            continue
        row = rows_by_code[code_key]
        ranked = sorted(
            [(_alarm_row_match_score(item, row), str(item.get("mode_id") or "")) for item in group],
            reverse=True,
        )
        best_score, best_id = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0
        if best_id and best_score >= 24 and (best_score - second_score) >= 8:
            decisions[code_key] = best_id
        else:
            decisions[code_key] = ""

    for code_key, group in duplicates.items():
        winner = decisions.get(code_key, "")
        for item in group:
            mode_id = str(item.get("mode_id") or "")
            item["alarm_codes"] = [
                code for code in _normalize_alarm_codes(item.get("alarm_codes"))
                if _normalize_text(code) != code_key or (winner and mode_id == winner)
            ][:3]

    return items


def _attach_alarm_evidence(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persiste rotulo oficial/pagina de cada codigo para auditoria e rastreabilidade."""
    rows_by_code = {
        _normalize_text(str(row.get("code") or "")): row
        for row in alarm_rows
        if str(row.get("code") or "").strip()
        and str(row.get("source_kind") or "").lower() != "message"
    }
    for item in items:
        evidence: list[dict[str, Any]] = []
        for code in _normalize_alarm_codes(item.get("alarm_codes")):
            row = rows_by_code.get(_normalize_text(code))
            if not row:
                continue
            evidence.append({
                "code": str(row.get("code") or code).strip(),
                "label": str(row.get("label") or "").strip()[:260],
                "page": row.get("page") if isinstance(row.get("page"), int) else None,
                "source_kind": str(row.get("source_kind") or "")[:40],
                "match_score": _alarm_row_match_score(item, row),
            })
        item["alarm_evidence"] = evidence
    return items


def _synchronize_alarm_descriptions(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove qualquer referencia legada a codigo e reconstroi apenas os aprovados."""
    trusted_codes = [
        str(row.get("code") or "").strip()
        for row in alarm_rows
        if str(row.get("code") or "").strip()
    ]

    legacy_label = (
        r"(?:c[oó]d(?:igo)?s?\.?\s*(?:de\s+)?alarme(?:s)?|"
        r"n[°ºo]?\.?\s*alarme(?:s)?|alarm\s+codes?|alarm\s+code)"
    )

    for item in items:
        selected = _normalize_alarm_codes(item.get("alarm_codes"))[:3]
        description = str(item.get("description") or "").strip()

        description = re.sub(
            r"(?i)\s*Alarmes?\s+documentados?\s+associados?\s*:\s*[^.]*\.?",
            " ",
            description,
        )

        # Remove rótulo + valor, inclusive variantes "Codigo alarme", "Cod. alarme",
        # "Nº alarme" e estados "nao fornecido/informado/disponivel".
        description = re.sub(
            rf"(?i)\b{legacy_label}\s*[:#=\-]?\s*"
            r"(?:n[aã]o\s+(?:fornecid[oa]s?|informad[oa]s?|dispon[ií]ve(?:l|is)|aplic[aá]ve(?:l|is)))?\s*"
            r"(?:\+?[A-Z]?\d[\w.\-/]*(?:\s*[,;/]\s*\+?[A-Z]?\d[\w.\-/]*)*)?\s*\.?",
            " ",
            description,
        )

        for code in trusted_codes:
            description = _code_regex(code).sub("", description)

        description = re.sub(r"\(\s*[,;.-]?\s*\)", " ", description)
        description = re.sub(r"\s+([,;.])", r"\1", description)
        description = re.sub(r"[,;]\s*[,;]", ",", description)
        description = re.sub(r"\s+", " ", description).strip(" ,;.-")

        if selected:
            suffix = "Alarmes documentados associados: " + ", ".join(selected) + "."
            description = (description + ". " + suffix).strip() if description else suffix
        item["description"] = description
    return items

def _enrich_failure_modes_with_alarm_rows(
    items: list[dict[str, Any]],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compatibilidade: enriquecimento determinístico estrito e limitado a 3 códigos."""
    if not items:
        return items
    for item in items:
        candidates = _candidate_alarm_rows_for_item(item, alarm_rows)
        selected = _deterministic_alarm_rows_for_item(item, candidates)
        item["alarm_codes"] = _normalize_alarm_codes([row.get("code") for row in selected])[:3]
        pages = set(_normalize_source_pages(item.get("source_pages")))
        pages.update(
            int(row["page"])
            for row in selected
            if isinstance(row.get("page"), int) and int(row["page"]) > 0
        )
        item["source_pages"] = sorted(pages)[:4]
    return items


def _build_alarm_row_batches(
    alarm_rows: list[dict[str, Any]],
    *,
    max_rows: int = 45,
    max_chars: int = 18_000,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    for row in alarm_rows:
        compact = {
            "code": str(row.get("code") or "").strip(),
            "page": row.get("page"),
            "text": re.sub(r"\s+", " ", str(row.get("text") or row.get("evidence") or "")).strip()[:700],
        }
        if not compact["code"]:
            continue
        projected = current_chars + len(json.dumps(compact, ensure_ascii=False))
        if current and (len(current) >= max_rows or projected > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(compact)
        current_chars += len(json.dumps(compact, ensure_ascii=False))

    if current:
        batches.append(current)
    return batches


def _alarm_rows_for_item_from_rows(
    item: dict[str, Any],
    alarm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    title = str(item.get("title") or "")
    details = " ".join([
        title,
        str(item.get("description") or ""),
        " ".join(_split_text_values(item.get("causes"))),
        " ".join(_split_text_values(item.get("effects"))),
    ])
    query_tokens = _significant_tokens(details)
    explicit_codes = {
        _normalize_text(code) for code in _normalize_alarm_codes(item.get("alarm_codes"))
    }

    scored: list[tuple[int, dict[str, Any]]] = []
    normalized_title = _normalize_text(title)
    for row in alarm_rows:
        row_text = str(row.get("text") or "")
        row_tokens = _significant_tokens(row_text)
        score = len(query_tokens.intersection(row_tokens))
        code_key = _normalize_text(str(row.get("code") or ""))
        if code_key and code_key in explicit_codes:
            score += 14
        if normalized_title and len(normalized_title) >= 10 and normalized_title in _normalize_text(row_text):
            score += 8
        if score >= 3:
            scored.append((score, row))

    if not scored:
        return []
    best = max(score for score, _ in scored)
    return [row for score, row in scored if score >= max(3, best - 1)][:8]


def _extract_failure_modes_from_alarm_rows(
    runtime,
    *,
    alarm_rows: list[dict[str, Any]],
    filename: str,
    _allow_split_retry: bool = True,
) -> list[dict[str, Any]]:
    if not alarm_rows:
        return []

    compact_rows = [
        {
            "code": str(row.get("code") or "").strip(),
            "page": row.get("page"),
            "text": re.sub(r"\s+", " ", str(row.get("text") or "")).strip()[:700],
        }
        for row in alarm_rows
        if str(row.get("code") or "").strip() and str(row.get("text") or "").strip()
    ]
    if not compact_rows:
        return []

    response_text = chat_claude(
        runtime,
        system=ALARM_FAILURE_MODE_SYSTEM,
        messages=[{
            "role": "user",
            "content": json.dumps({
                "file": filename,
                "task": (
                    "Agrupe estes registros oficiais de alarme em modos de falha técnicos distintos. "
                    "Retorne JSON compacto e cite literalmente a evidência de cada modo."
                ),
                "alarm_rows": compact_rows,
            }, ensure_ascii=False),
        }],
        max_tokens=3200,
    )
    raw_items = _extract_json_items_resilient(response_text)

    trusted_by_key = {
        _normalize_text(str(row.get("code") or "")): row
        for row in alarm_rows
    }
    output: list[dict[str, Any]] = []
    alarm_evidence_text = "\n".join(str(row.get("text") or "") for row in alarm_rows)
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        # Trusted codes are not enough by themselves: the generated technical
        # condition must also quote the current alarm rows verbatim. This blocks
        # a model from attaching a real code to an invented failure title.
        if not _evidence_quote_is_grounded(raw_item, alarm_evidence_text):
            continue
        normalized = normalize_failure_mode_item(raw_item, source_type="auto")
        if not normalized:
            continue

        valid_rows: list[dict[str, Any]] = []
        for code in _normalize_alarm_codes(normalized.get("alarm_codes")):
            row = trusted_by_key.get(_normalize_text(code))
            if row is not None:
                valid_rows.append(row)
        if not valid_rows:
            valid_rows = _alarm_rows_for_item_from_rows(normalized, alarm_rows)
        if not valid_rows:
            continue

        normalized["alarm_codes"] = _normalize_alarm_codes([
            row.get("code") for row in valid_rows
        ])[:3]
        normalized["source_pages"] = sorted({
            int(row["page"])
            for row in valid_rows
            if isinstance(row.get("page"), int) and int(row["page"]) > 0
        })[:4]
        output.append(normalized)

    # A completely empty alarm batch is suspicious because every input row was
    # already deterministically classified as an alarm/fault/error. Retry once
    # with two smaller groups. This protects coverage from output truncation or
    # an overly broad grouping decision without weakening grounding rules.
    if not output and _allow_split_retry and len(alarm_rows) > 12:
        midpoint = len(alarm_rows) // 2
        left = _extract_failure_modes_from_alarm_rows(
            runtime, alarm_rows=alarm_rows[:midpoint], filename=filename,
            _allow_split_retry=False,
        )
        right = _extract_failure_modes_from_alarm_rows(
            runtime, alarm_rows=alarm_rows[midpoint:], filename=filename,
            _allow_split_retry=False,
        )
        return _deterministic_deduplicate_failure_modes(left + right)

    return output


def _extract_failure_modes_from_alarm_batch(
    runtime,
    *,
    batch_text: str,
    filename: str,
) -> list[dict[str, Any]]:
    if not batch_text.strip() or not _extract_alarm_rows_from_batch(batch_text):
        return []

    response_text = chat_claude(
        runtime,
        system=ALARM_FAILURE_MODE_SYSTEM + "\n" + FAILURE_MODE_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Arquivo técnico: {filename}\n\n"
                "Trechos de tabelas/listas de alarmes do documento atual:\n"
                f"{batch_text}\n\n"
                "Extraia somente condições técnicas de falha/anomalia sustentadas pelos registros acima."
            ),
        }],
        max_tokens=2200,
    )
    parsed = _extract_json_object(response_text)
    raw_items = parsed.get("items", [])
    if not isinstance(raw_items, list):
        return []

    output: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        normalized = normalize_failure_mode_item(raw_item, source_type="auto")
        if not normalized:
            continue
        matched_rows = _alarm_rows_for_item(normalized, batch_text)
        if not matched_rows:
            continue
        normalized["alarm_codes"] = _normalize_alarm_codes([row.get("code") for row in matched_rows])
        normalized["source_pages"] = sorted({
            int(row["page"]) for row in matched_rows if row.get("page")
        })[:4]
        output.append(normalized)
    return output


def extract_failure_modes_for_document(
    runtime,
    *,
    asset_id: str,
    doc_id: str,
    filename: str,
) -> list[dict[str, Any]]:
    chunks = load_document_chunks(asset_id, doc_id)
    if not chunks:
        return []

    batches = build_failure_mode_batches(
        chunks,
        max_chars=_select_failure_mode_batch_max_chars(chunks),
    )
    collected: list[dict[str, Any]] = []

    for batch_text in batches:
        batch_items = extract_failure_modes_from_batch(
            runtime,
            batch_text=batch_text,
            filename=filename,
        )
        for item in batch_items:
            item["source_type"] = "auto"
            item["source_doc_id"] = doc_id
            item["source_doc_filename"] = filename
            collected.append(item)

    return deduplicate_failure_modes(collected)


def _citation_to_failure_mode_chunk(citation: dict[str, Any]) -> dict[str, Any] | None:
    text = str(
        citation.get("text_excerpt")
        or citation.get("excerpt")
        or citation.get("text")
        or citation.get("content")
        or citation.get("chunk_text")
        or citation.get("raw_text")
        or ""
    ).strip()
    if not text:
        return None

    page_number = citation.get("page_number") or citation.get("page") or citation.get("page_start")
    try:
        page = int(page_number) if page_number is not None else None
    except Exception:
        page = None

    document_id = str(citation.get("document_id") or "").strip()
    point_id = str(citation.get("point_id") or citation.get("id") or "").strip()
    section_title = str(citation.get("section_title") or citation.get("section") or "Data Ingest").strip()
    content_hash = "|".join([document_id, point_id, str(page or ""), text[:240]])

    return {
        "text": text,
        "page_start": page,
        "page_end": page,
        "section_title": section_title or "Data Ingest",
        "content_hash": content_hash,
        "document_id": document_id or None,
        "point_id": point_id or None,
    }


def _is_failure_relevant_chunk(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False

    if any(pattern.search(normalized) for pattern in _FAILURE_SIGNAL_PATTERNS):
        return True

    structural = any(pattern.search(normalized) for pattern in _FAILURE_STRUCTURAL_PATTERNS)
    if structural:
        return True

    if any(pattern.search(normalized) for pattern in _SAFETY_SYSTEM_PATTERNS):
        return True

    # Some alarm tables are extracted almost entirely as codes + short labels.
    # Require several technical-looking codes so a spare-parts code does not make
    # an arbitrary catalog chunk relevant by itself. Catalog sections are still
    # rejected later by the dedicated catalog guard.
    code_hits = _FAILURE_TECHNICAL_CODE_RE.findall(str(text or ""))
    if len(code_hits) >= 4 and any(
        token in normalized
        for token in ("descricao", "description", "causa", "cause", "solucao", "solution", "alarme", "alarm")
    ):
        return True

    return False


def _collect_ingest_direct_chunks(
    ingest_client,
    *,
    document_ids: list[str],
    context_ids: list[str],
    limit: int = 3000,
    relevant_only: bool = True,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()

    safe_document_ids: list[str] = []
    for value in document_ids:
        safe_value = str(value or "").strip()
        if safe_value and safe_value not in safe_document_ids:
            safe_document_ids.append(safe_value)

    safe_context_ids: list[str] = []
    for value in context_ids:
        safe_value = str(value or "").strip()
        if safe_value and safe_value not in safe_context_ids:
            safe_context_ids.append(safe_value)

    for document_id in safe_document_ids:
        try:
            payload = ingest_client.get_document_chunks(
                document_id=document_id,
                context_ids=safe_context_ids,
                limit=limit,
            )
        except Exception:
            continue

        raw_chunks = payload.get("chunks", []) if isinstance(payload, dict) else []
        if not isinstance(raw_chunks, list):
            continue

        for raw_chunk in raw_chunks:
            if not isinstance(raw_chunk, dict):
                continue

            chunk = _citation_to_failure_mode_chunk(raw_chunk)
            if not chunk:
                continue

            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            if relevant_only and not _is_failure_relevant_chunk(text):
                continue

            key = _normalize_text(str(chunk.get("content_hash") or text[:280]))
            if not key or key in seen:
                continue

            seen.add(key)
            chunks.append(chunk)

    return chunks


def _collect_ingest_failure_mode_chunks(
    ingest_client,
    *,
    context_ids: list[str],
    document_ids: list[str] | None = None,
    document_context: str = "",
    limit_per_query: int = 32,
    prefetched_direct_chunks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Coleta evidências do documento atual sem permitir busca global na base vetorial."""
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()

    safe_context_ids = _unique_strings(list(context_ids or []))
    safe_document_ids = _unique_strings(list(document_ids or []))

    direct_chunks = list(prefetched_direct_chunks or [])
    if not direct_chunks:
        direct_chunks = _collect_ingest_direct_chunks(
            ingest_client,
            document_ids=safe_document_ids,
            context_ids=safe_context_ids,
            limit=3000,
            relevant_only=False,
        )

    for chunk in direct_chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        # Direct document chunks are always included — the relevance filter is only
        # applied to semantic-search results (below) to prevent cross-document contamination.
        # Claude will determine which chunks actually contain FTA-relevant evidence.
        key = _normalize_text(str(chunk.get("content_hash") or text[:280]))
        if not key or key in seen:
            continue
        seen.add(key)
        chunks.append(chunk)

    # Nunca executar busca sem context_ids. Documentos legados continuam sendo
    # atendidos pela leitura direta por document_id acima. Isso impede que um
    # manual de outro ativo/tenant contamine a FTA atual.
    if not safe_context_ids:
        return chunks

    for query in _build_failure_mode_queries(document_context):
        try:
            payload = ingest_client.search_text(
                query=query,
                context_ids=safe_context_ids,
                limit=limit_per_query,
                min_score=0.0,
            )
        except Exception:
            continue

        citations = payload.get("citations", []) if isinstance(payload, dict) else []
        if not isinstance(citations, list):
            continue

        for citation in citations:
            if not isinstance(citation, dict):
                continue

            chunk = _citation_to_failure_mode_chunk(citation)
            if not chunk:
                continue

            text = str(chunk.get("text") or "").strip()
            if not text or not _is_failure_relevant_chunk(text):
                continue

            key = _normalize_text(str(chunk.get("content_hash") or text[:280]))
            if not key or key in seen:
                continue

            seen.add(key)
            chunks.append(chunk)

    return chunks


def _safe_action_text(action: Any) -> str:
    if isinstance(action, dict):
        return str(
            action.get("title")
            or action.get("name")
            or action.get("description")
            or action.get("action")
            or action.get("acao")
            or action.get("text")
            or ""
        ).strip()

    return str(action or "").strip()


def _safe_cause_text(cause: Any) -> str:
    if isinstance(cause, dict):
        return str(
            cause.get("title")
            or cause.get("name")
            or cause.get("description")
            or cause.get("cause")
            or cause.get("causa")
            or cause.get("text")
            or ""
        ).strip()

    return str(cause or "").strip()


def _build_action_payload(action_text: str, *, cause_id: str | None = None) -> dict[str, Any]:
    safe_action = str(action_text or "").strip()
    action_id = uuid.uuid4().hex[:12]

    payload: dict[str, Any] = {
        "action_id": action_id,
        "id": action_id,
        "title": safe_action,
        "name": safe_action,
        "description": safe_action,
        "action": safe_action,
        "acao": safe_action,
        "text": safe_action,
    }

    if cause_id:
        payload["cause_id"] = cause_id
        payload["parent_cause_id"] = cause_id

    return payload


def _normalize_actions_for_cause_link(actions: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for action in actions:
        safe_action = str(action or "").strip()
        if not safe_action:
            continue

        key = _normalize_text(safe_action)
        if not key or key in seen:
            continue

        seen.add(key)
        normalized.append(safe_action)

    return normalized


def _attach_actions_to_normalized_causes(
    item: dict[str, Any],
    *,
    cause_actions: dict[str, list[str]],
    fallback_actions: list[str],
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item

    causes = item.get("causes")
    if not isinstance(causes, list):
        causes = item.get("causas")

    if not isinstance(causes, list) or not causes:
        return item

    normalized_cause_actions = {
        _normalize_text(cause): _normalize_actions_for_cause_link(actions)
        for cause, actions in cause_actions.items()
        if str(cause or "").strip()
    }

    normalized_fallback_actions = _normalize_actions_for_cause_link(fallback_actions)

    top_level_actions: list[dict[str, Any]] = []
    linked_causes: list[Any] = []

    for cause in causes:
        if isinstance(cause, dict):
            cause_payload = dict(cause)
        else:
            cause_id = uuid.uuid4().hex[:12]
            cause_text = _safe_cause_text(cause)
            cause_payload = {
                "cause_id": cause_id,
                "id": cause_id,
                "title": cause_text,
                "name": cause_text,
                "description": cause_text,
                "cause": cause_text,
                "causa": cause_text,
                "text": cause_text,
            }

        cause_text = _safe_cause_text(cause_payload)
        cause_key = _normalize_text(cause_text)

        cause_id = str(
            cause_payload.get("cause_id")
            or cause_payload.get("id")
            or uuid.uuid4().hex[:12]
        ).strip()

        cause_payload["cause_id"] = cause_id
        cause_payload["id"] = cause_id

        selected_actions = normalized_cause_actions.get(cause_key, [])
        if not selected_actions:
            selected_actions = normalized_fallback_actions

        action_payloads = [
            _build_action_payload(action_text, cause_id=cause_id)
            for action_text in selected_actions
            if str(action_text or "").strip()
        ]

        existing_actions = cause_payload.get("actions")
        if isinstance(existing_actions, list):
            for existing_action in existing_actions:
                existing_action_text = _safe_action_text(existing_action)
                if existing_action_text:
                    action_payloads.append(_build_action_payload(existing_action_text, cause_id=cause_id))

        existing_acoes = cause_payload.get("acoes")
        if isinstance(existing_acoes, list):
            for existing_action in existing_acoes:
                existing_action_text = _safe_action_text(existing_action)
                if existing_action_text:
                    action_payloads.append(_build_action_payload(existing_action_text, cause_id=cause_id))

        deduped_action_payloads: list[dict[str, Any]] = []
        seen_actions: set[str] = set()

        for action_payload in action_payloads:
            action_text = _safe_action_text(action_payload)
            action_key = _normalize_text(action_text)
            if not action_key or action_key in seen_actions:
                continue

            seen_actions.add(action_key)
            deduped_action_payloads.append(action_payload)

        if not deduped_action_payloads:
            default_action = _build_default_action_for_unlinked_cause(
                failure_title=str(item.get("title") or item.get("name") or item.get("failure") or ""),
                cause_text=cause_text,
                general_actions=[_safe_action_text(action) for action in top_level_actions],
            )
            if default_action:
                deduped_action_payloads.append(_build_action_payload(default_action, cause_id=cause_id))

        cause_payload["actions"] = deduped_action_payloads
        cause_payload["acoes"] = deduped_action_payloads
        cause_payload["recommended_actions"] = deduped_action_payloads
        cause_payload["action_ids"] = [
            str(action.get("action_id") or action.get("id") or "")
            for action in deduped_action_payloads
            if str(action.get("action_id") or action.get("id") or "").strip()
        ]

        top_level_actions.extend(deduped_action_payloads)
        linked_causes.append(cause_payload)

    item["causes"] = linked_causes
    item["causas"] = linked_causes

    existing_top_actions = item.get("actions")
    if isinstance(existing_top_actions, list):
        for existing_action in existing_top_actions:
            existing_action_text = _safe_action_text(existing_action)
            if existing_action_text:
                top_level_actions.append(_build_action_payload(existing_action_text))

    deduped_top_actions: list[dict[str, Any]] = []
    seen_top_actions: set[str] = set()

    for action_payload in top_level_actions:
        action_text = _safe_action_text(action_payload)
        action_key = _normalize_text(action_text)
        if not action_key or action_key in seen_top_actions:
            continue

        seen_top_actions.add(action_key)
        deduped_top_actions.append(action_payload)

    item["actions"] = deduped_top_actions
    item["acoes"] = deduped_top_actions
    item["recommended_actions"] = deduped_top_actions

    return item

def _fallback_failure_modes_from_ingest_chunks(
    *,
    doc_id: str,
    filename: str,
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Não fabrica modos de falha quando o LLM não encontrou evidência suficiente."""
    _ = (doc_id, filename, chunks)
    return []


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("title")
            or value.get("name")
            or value.get("description")
            or value.get("label")
            or value.get("text")
            or value.get("cause")
            or value.get("causa")
            or value.get("action")
            or value.get("acao")
            or value.get("failure")
            or value.get("falha")
            or ""
        ).strip()

    return str(value or "").strip()


def _id_value(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = value.get(key)
        safe = str(raw or "").strip()
        if safe:
            return safe
    return ""


def _ensure_item_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index + 1:03d}"


def _build_linked_action(
    *,
    action_text: str,
    action_id: str,
    cause_id: str,
    cause_text: str,
) -> dict[str, Any]:
    safe_action = str(action_text or "").strip()
    safe_cause = str(cause_text or "").strip()

    return {
        "action_id": action_id,
        "id": action_id,
        "title": safe_action,
        "name": safe_action,
        "description": safe_action,
        "text": safe_action,
        "action": safe_action,
        "acao": safe_action,

        # Campos redundantes para compatibilidade com o frontend atual.
        "cause_id": cause_id,
        "parent_cause_id": cause_id,
        "causa_id": cause_id,
        "linked_cause_id": cause_id,
        "related_cause_id": cause_id,

        "cause": safe_cause,
        "causa": safe_cause,
        "related_cause": safe_cause,
        "parent_cause": safe_cause,
    }


def _fallback_cause_action_map_for_failure(failure_title: str) -> dict[str, list[str]]:
    """Mantido por compatibilidade; não contém regras específicas de fabricante/equipamento."""
    _ = failure_title
    return {}


def _build_default_action_for_unlinked_cause(
    *,
    failure_title: str,
    cause_text: str,
    general_actions: list[str],
) -> str:
    """Evita inventar ação corretiva sem evidência explícita do manual."""
    _ = (failure_title, cause_text, general_actions)
    return ""


def _link_actions_to_causes_for_frontend(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item

    normalized_groups = _normalize_cause_groups(
        item.get("cause_groups"),
        _split_text_values(item.get("causes") or item.get("probable_cause")),
        _split_text_values(item.get("effects") or item.get("impact")),
        _split_text_values(item.get("actions") or item.get("recommendation")),
    )

    failure_title = str(item.get("title") or "").strip()
    linked_causes: list[dict[str, Any]] = []
    linked_top_actions: list[dict[str, Any]] = []
    seen_action_global: set[str] = set()

    for cause_index, group in enumerate(normalized_groups):
        cause_text = str(group.get("cause") or "").strip()
        cause_key = _normalize_text(cause_text)
        if not cause_text or cause_key in {"diagnostico geral", "diagnostico", "geral", "acao geral", "acoes gerais"}:
            continue
        if _is_circular_cause(failure_title, cause_text):
            continue

        cause_id = str(group.get("cause_id") or _ensure_item_id("cause", cause_index)).strip()
        cause_payload: dict[str, Any] = {
            "cause_id": cause_id,
            "causa_id": cause_id,
            "id": cause_id,
            "title": cause_text,
            "name": cause_text,
            "description": cause_text,
            "text": cause_text,
            "cause": cause_text,
            "causa": cause_text,
            "effects": _split_text_values(group.get("effects")),
        }

        cause_actions: list[dict[str, Any]] = []
        for action_index, action_text in enumerate(_split_text_values(group.get("actions"))):
            action_key = _normalize_text(action_text)
            if not action_key:
                continue
            action_id = f"{cause_id}_action_{action_index + 1:03d}"
            action_payload = _build_linked_action(
                action_text=action_text,
                action_id=action_id,
                cause_id=cause_id,
                cause_text=cause_text,
            )
            cause_actions.append(action_payload)
            if action_key not in seen_action_global:
                seen_action_global.add(action_key)
                linked_top_actions.append(action_payload)

        cause_payload["actions"] = cause_actions
        cause_payload["acoes"] = cause_actions
        cause_payload["recommended_actions"] = cause_actions
        cause_payload["action_ids"] = [
            str(action.get("action_id") or action.get("id") or "")
            for action in cause_actions
            if str(action.get("action_id") or action.get("id") or "").strip()
        ]
        linked_causes.append(cause_payload)

    item["causes"] = linked_causes
    item["causas"] = linked_causes
    item["actions"] = linked_top_actions
    item["acoes"] = linked_top_actions
    item["recommended_actions"] = linked_top_actions
    return item


def link_actions_to_causes_for_frontend(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked_items: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        linked_items.append(_link_actions_to_causes_for_frontend(item))

    return linked_items

def extract_failure_modes_for_ingest_document(
    runtime,
    *,
    ingest_client,
    asset_id: str,
    doc_id: str,
    filename: str,
    bronze_id: str | None = None,
    envelope_id: str | None = None,
    progress_callback: FailureModeProgressCallback | None = None,
) -> list[dict[str, Any]]:
    _emit_failure_mode_progress(
        progress_callback, percent=1, phase="preparing",
        message="Preparando o documento e identificando o contexto técnico.",
    )
    context_ids = _build_failure_mode_context_ids(
        asset_id=asset_id,
        doc_id=doc_id,
        bronze_id=bronze_id,
        envelope_id=envelope_id,
    )
    document_ids = _unique_strings([envelope_id, bronze_id, doc_id])
    document_context = _build_failure_mode_document_context(
        ingest_client,
        envelope_id=envelope_id,
        filename=filename,
    )
    _emit_failure_mode_progress(
        progress_callback, percent=4, phase="context",
        message="Contexto do manual identificado. Carregando trechos indexados.",
    )

    # Carrega todos os chunks textuais do documento atual uma única vez. O limite
    # cobre manuais extensos com milhares de chunks sem recorrer a busca global.
    direct_chunks = _collect_ingest_direct_chunks(
        ingest_client,
        document_ids=document_ids,
        context_ids=context_ids,
        limit=3000,
        relevant_only=False,
    )
    _emit_failure_mode_progress(
        progress_callback, percent=7, phase="retrieval",
        message=f"{len(direct_chunks)} trechos do documento carregados. Buscando evidências de falha.",
    )

    chunks = _collect_ingest_failure_mode_chunks(
        ingest_client,
        context_ids=context_ids,
        document_ids=document_ids,
        document_context=document_context,
        limit_per_query=32,
        prefetched_direct_chunks=direct_chunks,
    )
    if not chunks:
        _emit_failure_mode_progress(
            progress_callback, percent=99, phase="finalizing",
            message="Nenhuma evidência técnica de falha foi encontrada no documento.",
        )
        return []

    _emit_failure_mode_progress(
        progress_callback, percent=10, phase="retrieval",
        message=f"{len(chunks)} trechos relevantes selecionados. Preparando lotes de análise.",
    )

    high_priority_terms = (
        "tabela de alarmes", "alarm list", "alarm code", "fault code", "error code",
        "alarmes plc", "alarmes iso", "alarme", "alarm", "erro", "error",
        "fault", "trip", "shutdown", "interlock", "problemas x solucoes", "problemas e solucoes",
        "possiveis causas", "possible causes", "troubleshooting", "diagnostico", "diagnostic",
        "quality", "qualidade",
    )
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: sum(
            1 for term in high_priority_terms
            if _normalize_text(term) in _normalize_text(str(chunk.get("text") or ""))
        ),
        reverse=True,
    )

    collected: list[dict[str, Any]] = []
    batch_max_chars = _select_failure_mode_batch_max_chars(ordered_chunks)
    general_batches = build_failure_mode_batches(ordered_chunks, max_chars=batch_max_chars)
    general_total = len(general_batches)
    _emit_failure_mode_progress(
        progress_callback, percent=12, phase="extracting",
        message=f"Iniciando análise textual em {general_total} lote(s).",
        current_batch=0, total_batches=general_total, failures_found=0,
    )
    for general_index, batch_text in enumerate(general_batches, start=1):
        enriched_batch_text = (
            "CONTEXTO DO DOCUMENTO ATUAL (use apenas para identificar o ativo e seus subsistemas; não invente falhas a partir deste resumo):\n"
            f"{document_context or filename}\n\n"
            "TAREFA:\n"
            "Extraia modos de falha técnicos somente das evidências abaixo. Varra também tabelas/listas de alarmes, erros, fault codes, trips, intertravamentos, troubleshooting e defeitos de qualidade/processo. Mensagens operacionais meramente informativas podem contextualizar o processo, mas seus códigos NÃO devem ser tratados como alarm_codes.\n"
            "Quando houver estrutura Problema/Causas/Solução (ou equivalentes), preserve essa relação.\n"
            "Não trate perigos ocupacionais, proibições ou procedimentos normais como falhas se não houver anomalia técnica. Rejeite causa que repita o título.\n"
            "Ignore seções de catálogo/lista de peças, peças de reposição/spare parts/replacement parts/parts list e tabelas de fabricante-tipo-código quando elas apenas enumerarem componentes. Cabeçalhos como Sistema elétrico, Sistema hidráulico ou Componentes mecânicos dentro dessas listas NÃO são modos de falha. Só use esse conteúdo se houver condição anormal/defeito/troubleshooting explicitamente descrito.\n"
            "Registre source_pages/alarm_codes somente quando sustentados pela evidência.\n\n"
            f"{batch_text}"
        )
        for item in extract_failure_modes_from_batch(
            runtime,
            batch_text=enriched_batch_text,
            grounding_text=batch_text,
            filename=filename,
        ):
            item["source_type"] = "auto"
            item["source_doc_id"] = doc_id
            item["source_doc_filename"] = filename
            collected.append(item)

        general_percent = 12 + int(round(40 * general_index / max(1, general_total)))
        _emit_failure_mode_progress(
            progress_callback, percent=general_percent, phase="extracting",
            message=f"Analisando evidências textuais: lote {general_index} de {general_total}.",
            current_batch=general_index, total_batches=general_total,
            failures_found=len(collected),
        )

    # Parse the complete alarm evidence deterministically, then attach trusted
    # codes/pages to the failure modes extracted from the document. Alarm tables
    # should improve traceability without turning every PLC/ISO row into a new
    # FTA node.
    alarm_chunks = _collect_alarm_evidence_chunks(
        ingest_client,
        context_ids=context_ids,
        direct_chunks=direct_chunks,
        limit_per_query=40,
    )
    alarm_rows = _collect_document_alarm_rows(direct_chunks + alarm_chunks)
    _emit_failure_mode_progress(
        progress_callback, percent=56, phase="alarms",
        message=f"{len(alarm_rows)} registro(s) de alarme/fault code validado(s) no manual.",
        failures_found=len(collected),
    )

    # alarm_codes extraídos livremente pelo LLM são evidência NÃO confiável.
    # Eles não podem participar de deduplicação nem reforçar a si próprios.
    for item in collected:
        item["alarm_codes"] = []

    # Coverage hardening: explicit alarm/fault tables are authoritative evidence.
    # Generate additional grouped modes only for trusted rows that are not already
    # represented by the general extraction. This path is document-agnostic and
    # activates only when a real alarm table was detected. It does NOT create one
    # FTA node per code: ALARM_FAILURE_MODE_SYSTEM groups equivalent conditions.
    covered_alarm_keys: set[str] = set()
    for item in collected:
        candidates = _candidate_alarm_rows_for_item(item, alarm_rows)
        for row in _deterministic_alarm_rows_for_item(item, candidates):
            key = _normalize_text(str(row.get("code") or ""))
            if key:
                covered_alarm_keys.add(key)

    uncovered_alarm_rows = [
        row for row in alarm_rows
        if _normalize_text(str(row.get("code") or "")) not in covered_alarm_keys
    ]
    alarm_generated: list[dict[str, Any]] = []
    alarm_batches = _build_alarm_row_batches(
        uncovered_alarm_rows,
        max_rows=45,
        max_chars=18_000,
    )
    alarm_total = len(alarm_batches)
    _emit_failure_mode_progress(
        progress_callback, percent=58, phase="alarms",
        message=f"Preparando cobertura de alarmes em {alarm_total} lote(s).",
        current_batch=0, total_batches=alarm_total, failures_found=len(collected),
    )
    for alarm_index, row_batch in enumerate(alarm_batches, start=1):
        for item in _extract_failure_modes_from_alarm_rows(
            runtime,
            alarm_rows=row_batch,
            filename=filename,
        ):
            item["source_type"] = "auto"
            item["source_doc_id"] = doc_id
            item["source_doc_filename"] = filename
            alarm_generated.append(item)

        alarm_percent = 58 + int(round(20 * alarm_index / max(1, alarm_total)))
        _emit_failure_mode_progress(
            progress_callback, percent=alarm_percent, phase="alarms",
            message=f"Validando alarmes: lote {alarm_index} de {alarm_total}.",
            current_batch=alarm_index, total_batches=alarm_total,
            failures_found=len(collected) + len(alarm_generated),
        )

    collected.extend(alarm_generated)
    items = deduplicate_failure_modes(collected)
    if not items:
        _emit_failure_mode_progress(
            progress_callback, percent=99, phase="finalizing",
            message="Análise concluída sem modos de falha tecnicamente sustentados.",
        )
        return []
    _emit_failure_mode_progress(
        progress_callback, percent=80, phase="review",
        message=f"Revisando {len(items)} modo(s) candidato(s) e removendo não-falhas.",
        failures_found=len(items),
    )
    # Remove somente não-falhas claras; na dúvida o revisor preserva o item.
    items = _review_failure_modes(runtime, items)
    items = deduplicate_failure_modes(items)
    items = _deterministic_deduplicate_failure_modes(items)

    _emit_failure_mode_progress(
        progress_callback, percent=84, phase="deduplication",
        message=f"Consolidando duplicidades entre {len(items)} modo(s) válidos.",
        failures_found=len(items),
    )
    # Une duplicatas semânticas confirmadas pelo modelo, preservando páginas,
    # códigos, causas, efeitos e ações de todos os membros do grupo.
    items = _semantic_deduplicate_failure_modes(runtime, items)
    items = deduplicate_failure_modes(items)
    items = _deterministic_deduplicate_failure_modes(items)

    # Taxonomia única por documento. O modelo deriva grupos funcionais das
    # evidências do próprio manual e depois classifica cada falha nessa lista.
    _emit_failure_mode_progress(
        progress_callback, percent=88, phase="taxonomy",
        message="Organizando os modos de falha nos grupos funcionais do próprio manual.",
        failures_found=len(items),
    )
    candidate_categories = _unique_strings([
        str(item.get("category") or "") for item in items if item.get("category")
    ])
    taxonomy = _extract_document_taxonomy(
        runtime,
        document_context=document_context,
        evidence_chunks=direct_chunks,
        candidate_categories=candidate_categories,
    )
    items = _classify_failure_modes_to_taxonomy(runtime, items, taxonomy)

    _emit_failure_mode_progress(
        progress_callback, percent=92, phase="traceability",
        message="Validando páginas de origem e rastreabilidade das falhas.",
        failures_found=len(items),
    )
    # Última camada de rastreabilidade: se o LLM omitiu página, procura a melhor
    # evidência no conjunto integral de chunks do documento, com limiar conservador.
    items = _enrich_source_pages_from_chunks(items, direct_chunks)
    items = _validate_alarm_codes_against_document(items, alarm_rows)
    items = deduplicate_failure_modes(items)
    items = _deterministic_deduplicate_failure_modes(items)

    _emit_failure_mode_progress(
        progress_callback, percent=95, phase="verification",
        message="Verificando associações entre falhas e códigos de alarme documentados.",
        failures_found=len(items),
    )
    # A associação código ↔ falha é sempre a última transformação semântica.
    # Assim nenhuma deduplicação posterior volta a unir listas de alarmes e
    # ultrapassar o limite/precisão aprovados pelo revisor.
    items = _verify_alarm_assignments(runtime, items, alarm_rows)
    items = _prune_weak_alarm_assignments(items, alarm_rows)
    items = _coalesce_equivalent_modes_for_shared_alarm(items, alarm_rows)
    items = _resolve_alarm_ownership(runtime, items, alarm_rows)
    items = _attach_alarm_evidence(items, alarm_rows)
    items = _synchronize_alarm_descriptions(items, alarm_rows)
    _emit_failure_mode_progress(
        progress_callback, percent=99, phase="saving",
        message=f"Validação concluída. Preparando {len(items)} modo(s) de falha para salvar.",
        failures_found=len(items),
    )
    return items



# Sentinel para distinguir "storage_tenant_id não informado pelo caller" de
# "storage_tenant_id=None deliberado (documento org-scoped)".
_STORAGE_TENANT_UNSET: object = object()


def generate_failure_modes(
    *,
    org_id: str,
    tenant_id: str | None,
    doc_id: str,
    filename: str,
    bronze_id: str | None = None,
    envelope_id: str | None = None,
    storage_tenant_id: Any = _STORAGE_TENANT_UNSET,
    progress_callback: FailureModeProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """ATI entry point: generate failure modes using the IngestAdapter for Qdrant/Postgres access.

    storage_tenant_id controla o tenant físico usado para buscar chunks no Qdrant.

    Valores aceitos:
    - _STORAGE_TENANT_UNSET (padrão): usa tenant_id do equipamento (caso típico).
    - str: tenant específico do documento.
    - None: documento org-scoped — busca com escopo organizacional, sem filtrar
      por tenant. Não confundir com "não informado".
    """
    from src.fault_tree.ingest_adapter import IngestAdapter

    # Diferencia "não informado" (fallback para tenant do equipamento) de
    # "None explícito" (documento org-scoped).
    if storage_tenant_id is _STORAGE_TENANT_UNSET:
        effective_storage_tenant = tenant_id
    else:
        effective_storage_tenant = storage_tenant_id  # pode ser None para org-scope

    adapter = IngestAdapter(org_id=org_id, tenant_id=effective_storage_tenant)
    runtime = None  # chat_claude ignores the runtime argument
    return extract_failure_modes_for_ingest_document(
        runtime,
        ingest_client=adapter,
        asset_id=doc_id,
        doc_id=doc_id,
        filename=filename,
        bronze_id=bronze_id,
        envelope_id=envelope_id,
        progress_callback=progress_callback,
    )
