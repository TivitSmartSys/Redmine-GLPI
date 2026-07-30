"""All user-facing text, in Brazilian Portuguese (PT-BR).

Language rule (spec section 1a): everything the user sees - CLI output, report
body, error messages - is PT-BR. Code identifiers and comments stay in English.
Migrated data (field names, values, statuses) is copied verbatim and never
translated.

Keeping every string here makes it auditable that no English or Polish leaks
into the output, and it is the single place to review wording with the team.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Token redaction
# ---------------------------------------------------------------------------
# Tokens must never reach stdout, a log line, an exception message or the report
# file (spec section 13, rule 8). Every secret is registered here once at
# startup; redact() is applied to anything that might echo a request.

_SECRETS: list[str] = []
REDACTED = "***"


def register_secrets(values) -> None:
    """Register secret values that must be scrubbed from any output."""
    for value in values:
        if value and value not in _SECRETS:
            _SECRETS.append(value)


def redact(text: object) -> str:
    """Replace every registered secret in `text` with a placeholder."""
    result = str(text)
    for secret in _SECRETS:
        result = result.replace(secret, REDACTED)
    return result


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_MISSING_VARS = (
    "Configuração ausente. As seguintes variáveis de ambiente não estão "
    "definidas: {names}.\n"
    "Copie o arquivo .env.example para .env e preencha os valores. "
    "O arquivo .env nunca deve ser versionado."
)

CONFIG_REDMINE_URL_INVALID = (
    "REDMINE_URL está incorreta: contém 'apirest.php'. Redmine e GLPI são dois "
    "servidores diferentes — 'apirest.php' pertence somente ao GLPI. "
    "Informe apenas a URL base do Redmine."
)

CONFIG_FILE_UNREADABLE = (
    "Não foi possível ler o arquivo de configuração: {path}"
)

# ---------------------------------------------------------------------------
# Preflight (spec section 9.0)
# ---------------------------------------------------------------------------
PREFLIGHT_HEADER = "== Verificação inicial (preflight) =="

PREFLIGHT_SESSION_OK = "[OK] Sessão GLPI iniciada."

PREFLIGHT_SESSION_FAILED = (
    "[FALHA] Não foi possível iniciar a sessão no GLPI.\n"
    "  Detalhe: {detail}\n"
    "  Verifique GLPI_URL, GLPI_USER_TOKEN e GLPI_APP_TOKEN."
)

PREFLIGHT_FIELDS_RIGHTS_OK = "[OK] Permissões do plugin Fields confirmadas."

# Wording required by spec section 9.0, item 2.
PREFLIGHT_FIELDS_RIGHTS_MISSING = (
    "[FALHA] O token da API não tem permissão para o plugin Fields — os campos "
    "adicionais não seriam gravados.\n"
    "  Conceda a permissão em: Administração → Perfis → Campos adicionais.\n"
    "  A migração foi interrompida para não criar projetos com campos vazios."
)

PREFLIGHT_FIELDS_CHECK_FAILED = (
    "[FALHA] Não foi possível verificar as permissões do plugin Fields.\n"
    "  Detalhe: {detail}"
)

PREFLIGHT_DROPDOWNS_LOADING = "[..] Carregando os dicionários de listas ({count} no total)…"

PREFLIGHT_DROPDOWN_OK = "     [OK] {itemtype}: {count} valor(es)."

PREFLIGHT_DROPDOWN_EMPTY = (
    "     [AVISO] {itemtype}: dicionário VAZIO no GLPI. Todos os valores deste "
    "campo serão ignorados (com aviso no relatório), pois a migração nunca cria "
    "entradas de dicionário automaticamente."
)

PREFLIGHT_DROPDOWNS_EMPTY_SUMMARY = (
    "[AVISO] {count} de {total} dicionários estão vazios no GLPI. Os campos "
    "correspondentes NÃO serão migrados enquanto os dicionários não forem "
    "preenchidos em: Configurar → Campos adicionais.\n"
    "  Dicionários vazios: {names}"
)

PREFLIGHT_DROPDOWN_FAILED = (
    "     [AVISO] {itemtype}: não foi possível carregar o dicionário "
    "({detail}). Os campos que dependem dele serão ignorados com aviso."
)

PREFLIGHT_REDMINE_OK = "[OK] Redmine respondeu (issue {issue_id} acessível)."

PREFLIGHT_REDMINE_FAILED = (
    "[FALHA] Não foi possível acessar o Redmine.\n"
    "  Detalhe: {detail}\n"
    "  Verifique REDMINE_URL e REDMINE_API_KEY."
)

PREFLIGHT_PASSED = "== Preflight concluído com sucesso. =="

PREFLIGHT_ABORTED = "== Preflight falhou. Nada foi gravado. =="

# ---------------------------------------------------------------------------
# HTTP / API errors
# ---------------------------------------------------------------------------
HTTP_ERROR = "Erro HTTP {status} em {method} {path}: {detail}"

CONNECTION_ERROR = "Falha de conexão com {system}: {detail}"

REDMINE_ISSUE_NOT_FOUND = "Issue {issue_id} não encontrada no Redmine."

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
CLI_DESCRIPTION = (
    "Migra um projeto do Redmine para o GLPI (projeto, tarefas, campos "
    "adicionais e Faturamento). Por padrão executa em modo simulação."
)

CLI_HELP_ISSUE = "Número da issue raiz no Redmine (ex.: 20238)."
CLI_HELP_APPLY = (
    "Grava no GLPI. Sem esta opção nada é gravado (modo simulação padrão)."
)
CLI_HELP_YES = (
    "Confirma a gravação sem perguntar. Só tem efeito junto com --apply; "
    "as duas opções continuam sendo obrigatórias para gravar."
)
CLI_HELP_DB = "Caminho do banco SQLite com o mapa de migração."
CLI_HELP_REPORT = "Caminho do arquivo onde salvar o relatório."

CLI_MODE_DRY_RUN = (
    "MODO SIMULAÇÃO (dry-run): nada será gravado no GLPI. "
    "Use --apply para gravar."
)
CLI_MODE_APPLY = "MODO GRAVAÇÃO (--apply): as alterações serão gravadas no GLPI."

CLI_INTERRUPTED = "Operação interrompida pelo usuário. Nada foi gravado."

# ---------------------------------------------------------------------------
# Report (spec section 10)
# ---------------------------------------------------------------------------
REPORT_TITLE = "RELATÓRIO DE MIGRAÇÃO — RDM {issue_id}"
REPORT_MODE_DRY_RUN = "Modo: SIMULAÇÃO — nada será gravado no GLPI"
REPORT_MODE_APPLY = "Modo: GRAVAÇÃO"
REPORT_GENERATED_AT = "Gerado em: {timestamp}"

REPORT_SECTION_1 = "1. O QUE SERÁ CRIADO"
REPORT_SECTION_2 = "2. CAMPOS IGNORADOS — SEM EQUIVALENTE NO GLPI"
REPORT_SECTION_3 = "3. CAMPOS IGNORADOS — SEM VALOR NO REDMINE"
REPORT_SECTION_4 = "4. REFERÊNCIAS NÃO RESOLVIDAS"
REPORT_SECTION_5 = "5. CAMPOS OBRIGATÓRIOS DO GLPI SEM DADOS"
REPORT_SECTION_6 = "6. COLUNAS NUNCA GRAVADAS (por decisão)"
REPORT_SECTION_7 = "7. CONFERÊNCIA DE INTEGRIDADE"

REPORT_PROJECT_LINE = 'Projeto GLPI: "{name}"'
REPORT_ORIGIN_LINE = "  Origem: RDM {issue_id} (tracker {tracker_id} {tracker_name})"
REPORT_TASKS_LINE = "  Tarefas a criar: {count}"
REPORT_FATURAMENTO_LINE = "  Linhas de Faturamento (container 25): {count}"
REPORT_CORE_HEADER = "  Campos do projeto (glpi_projects):"
REPORT_CONTAINER15_HEADER = "  Campos adicionais (container 15):"
REPORT_NOTHING = "  (nenhum)"

REPORT_SECTION_2_INTRO = (
    "  Campos preenchidos no Redmine que não têm destino no GLPI. "
    "O valor está registrado abaixo para não se perder."
)
REPORT_SECTION_3_INTRO = (
    "  O campo existe nos dois sistemas, mas a origem está vazia. "
    "Nada foi perdido."
)
REPORT_SECTION_4_INTRO = (
    "  Valores presentes no Redmine que não puderam ser convertidos. "
    "O campo foi ignorado — NENHUM valor foi inventado."
)
REPORT_SECTION_5_INTRO = (
    "  O GLPI marca estas colunas como obrigatórias. Sem dados, a gravação "
    "pode ser recusada."
)

REPORT_INTEGRITY_INTRO = (
    "  Todo campo de origem aparece em exatamente uma categoria acima."
)
REPORT_INTEGRITY_TOTAL = "  Campos de origem analisados : {total}"
REPORT_INTEGRITY_WRITTEN = "    gravados no GLPI          : {count}"
REPORT_INTEGRITY_EMPTY = "    vazios no Redmine         : {count}"
REPORT_INTEGRITY_NO_COUNTERPART = "    sem equivalente no GLPI   : {count}"
REPORT_INTEGRITY_UNRESOLVED = "    não resolvidos            : {count}"
REPORT_INTEGRITY_OK = "  [OK] {parts} = {total} — nenhum campo foi descartado em silêncio."
REPORT_INTEGRITY_FAIL = (
    "  [ERRO] A soma das categorias ({parts}) não confere com o total ({total}). "
    "Isto é um defeito do migrador — não prossiga com --apply."
)

REPORT_TREE_HEADER = "  Árvore (Redmine → GLPI):"
REPORT_TREE_PROJECT = "    #{issue_id} [{tracker}] {subject} → Projeto{glpi}"
REPORT_TREE_TASK = "    #{issue_id} [{tracker}] {subject} → Tarefa{glpi}"
REPORT_TREE_FATURAMENTO = (
    "    #{issue_id} [{tracker}] {subject} → linha de Faturamento (container 25)"
)
REPORT_TREE_SKIPPED = "    #{issue_id} [{tracker}] {subject} → IGNORADO ({reason})"

REPORT_SECTION_SKIPPED = "SUBTAREFAS IGNORADAS — TRACKER FORA DO ESCOPO"
REPORT_SECTION_SKIPPED_INTRO = (
    "  Estas subtarefas NÃO serão criadas no GLPI. O projeto pai é criado "
    "normalmente."
)
REPORT_SKIPPED_LINE = 'IGNORADO subtarefa {tracker} {issue_id} "{subject}" — {reason}'

REPORT_SECTION_RELATIONS = "RELAÇÕES DO REDMINE NÃO MIGRADAS"
REPORT_SECTION_RELATIONS_INTRO = (
    "  Relações horizontais (relates) não fazem parte da hierarquia e nunca "
    "viram tarefas.\n  Somente parceiros do tracker 15 (Faturamento) geram "
    "linhas no container 25."
)
REPORT_RELATION_LINE = (
    '  - #{issue_id} [{tracker}] "{subject}" — relação {relation_type}, '
    "não migrada"
)

REPORT_SECTION_FATURAMENTO = "FATURAMENTO (container 25)"
REPORT_FATURAMENTO_ITEM = '  Faturamento RDM {issue_id} — "{subject}"'

REPORT_SECTION_TASKS = "TAREFAS (glpi_projecttasks)"
REPORT_TASK_ITEM = '  Tarefa RDM {issue_id} — "{subject}"'
REPORT_TASK_COMMENT = "    comentário gerado:"

REPORT_TREE_FAILURES = (
    "  [AVISO] Não foi possível ler {count} filho(s) no Redmine: {ids}. "
    "Eles não entram na migração."
)
REPORT_TREE_CYCLES = (
    "  [AVISO] Ciclo detectado na árvore; issue(s) já visitada(s): {ids}."
)

# ---------------------------------------------------------------------------
# Deduplication and write path
# ---------------------------------------------------------------------------
DEDUP_ALREADY_MIGRATED = (
    "A issue {issue_id} já foi migrada. Projeto GLPI existente: {glpi_id}.\n"
    "Esta versão não atualiza projetos já migrados — nada foi alterado."
)
DEDUP_LOCAL_HIT = (
    "  [pulado] RDM {issue_id} já consta no mapa local como {itemtype} "
    "{glpi_id}."
)

APPLY_CONFIRM_PROMPT = (
    "Confirma a gravação no GLPI? Digite 'sim' para continuar: "
)
APPLY_CONFIRM_ACCEPT = {"sim", "s", "yes", "y"}
APPLY_CANCELLED = "Gravação cancelada. Nada foi gravado no GLPI."

APPLY_HEADER = "== Gravando no GLPI =="
APPLY_PROJECT_CREATED = "[OK] Projeto criado: GLPI {glpi_id} (RDM {issue_id})."
APPLY_CONTAINER15_WRITTEN = "[OK] Campos adicionais gravados (container 15, linha {row_id})."
APPLY_TASK_CREATED = "[OK] Tarefa criada: GLPI {glpi_id} (RDM {issue_id})."
APPLY_FATURAMENTO_CREATED = (
    "[OK] Linha de Faturamento criada: GLPI {row_id} (RDM {issue_id})."
)
APPLY_FATURAMENTO_DEGRADED = (
    "[AVISO] O GLPI recusou linhas adicionais de Faturamento neste projeto. "
    "A primeira foi gravada; as demais estão no relatório com todos os valores.\n"
    "  Detalhe: {detail}"
)
APPLY_STEP_FAILED = "[FALHA] {step}: {detail}"
APPLY_DONE = "== Migração concluída. =="

REPORT_SAVED = "Relatório salvo em: {path}"
REPORT_NOTHING_WRITTEN = (
    "Nada foi gravado no GLPI. Use --apply para executar a migração."
)
