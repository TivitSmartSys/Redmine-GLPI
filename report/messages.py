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

# ---------------------------------------------------------------------------
# Painel web (web/)
# ---------------------------------------------------------------------------
# The panel is a second front end over the same migration code. Its strings live
# here for the same reason as every other string: one place to audit that no
# English or Polish leaks into what the user reads.

UI_APP_TITLE = "Migração Redmine → GLPI"
UI_APP_SUBTITLE = "Painel de operação"

UI_NAV_MIGRATION = "Migração"
UI_NAV_AUDIT = "Auditoria"
UI_NAV_HISTORY = "Histórico"
UI_NAV_CONFIG = "Configuração"

UI_STATUS_CHECKING = "verificando…"
UI_STATUS_ONLINE = "conectado"
UI_STATUS_OFFLINE = "sem conexão"

UI_MODE_DRY_RUN = "Simulação"
UI_MODE_APPLY = "Gravação"
UI_MODE_APPLY_BANNER = (
    "MODO GRAVAÇÃO — os dados serão escritos no GLPI após a sua confirmação."
)

UI_ISSUE_LABEL = "Número da issue no Redmine"
UI_ISSUE_PLACEHOLDER = "ex.: 20238"
UI_RUN_DRY_RUN = "Simular"
UI_RUN_APPLY = "Analisar e gravar"
UI_RUNNING = "Executando…"

UI_CONFIRM_TITLE = "Confirmar gravação no GLPI"
UI_CONFIRM_BODY = (
    "O relatório acima descreve exatamente o que será criado. Esta ação grava "
    "no GLPI e esta versão não desfaz nem atualiza projetos já migrados."
)
UI_CONFIRM_HINT = "Digite 'sim' para confirmar:"
# Same words the CLI accepts, exposed to the browser so the confirm button only
# lights up on a valid answer. The server still validates independently - like
# the CLI, anything else counts as "cancel", so a typo must not reach it.
UI_CONFIRM_ACCEPT_WORDS = sorted(APPLY_CONFIRM_ACCEPT)
UI_CONFIRM_OK = "Gravar no GLPI"
UI_CONFIRM_CANCEL = "Cancelar"
UI_CONFIRM_REJECTED = "Confirmação inválida. Nada foi gravado no GLPI."
UI_CONFIRM_EXPIRED = (
    "Tempo de confirmação esgotado; a sessão do GLPI foi encerrada. "
    "Nada foi gravado. Execute a análise novamente."
)

UI_JOB_BUSY = "Já existe uma execução em andamento. Aguarde a conclusão."
UI_JOB_NOT_FOUND = "Execução não encontrada ou já expirada."
UI_ISSUE_INVALID = "Informe um número de issue válido."
UI_TRACKER_INVALID = "Informe um número de tracker válido."

UI_CARD_PROJECT = "Projeto"
UI_CARD_TASKS = "Tarefas"
UI_CARD_FATURAMENTO = "Faturamento"
UI_CARD_WRITTEN = "Campos gravados"
UI_CARD_IGNORED = "Campos ignorados"
UI_CARD_UNRESOLVED = "Não resolvidos"

UI_WARN_UNRESOLVED = "{count} referência(s) não resolvida(s)"
UI_WARN_MANDATORY = "{count} campo(s) obrigatório(s) sem dados"
UI_WARN_SKIPPED = "{count} subtarefa(s) ignorada(s)"
UI_WARN_FAILURES = "{count} filho(s) ilegível(is) no Redmine"
UI_WARN_CYCLES = "{count} ciclo(s) na árvore"
UI_WARN_DEGRADED = "{count} linha(s) de Faturamento não gravada(s)"
UI_WARN_INTEGRITY = "Conferência de integridade FALHOU — não grave"

UI_REPORT_HEADING = "Relatório completo"
UI_REPORT_COPY = "Copiar"
UI_REPORT_COPIED = "Copiado"
UI_REPORT_DOWNLOAD = "Baixar .txt"
UI_CONSOLE_HEADING = "Execução"

UI_AUDIT_TRACKER_LABEL = "Tracker do Redmine"
UI_AUDIT_RUN = "Auditar dicionários"
UI_AUDIT_INTRO = (
    "Lista os valores que existem no Redmine e seriam perdidos porque não há "
    "entrada correspondente no dicionário do GLPI. Não grava nada."
)
UI_AUDIT_COL_FIELD = "Campo"
UI_AUDIT_COL_VALUE = "Valor sem correspondência"
UI_AUDIT_COL_COUNT = "Issues afetadas"
UI_AUDIT_COMPLETE = "Cobertura completa — nenhum valor seria perdido."

UI_HISTORY_INTRO = (
    "O que já foi criado no GLPI, segundo o mapa local (migration.db)."
)
UI_HISTORY_COL_REDMINE = "RDM"
UI_HISTORY_COL_GLPI = "GLPI"
UI_HISTORY_COL_ITEMTYPE = "Tipo"
UI_HISTORY_COL_PARENT = "Pai (RDM)"
UI_HISTORY_COL_STATUS = "Situação"
UI_HISTORY_COL_DATE = "Data"
UI_HISTORY_FILTER = "Filtrar…"
UI_HISTORY_EMPTY = "Nenhuma migração registrada ainda."

UI_CONFIG_INTRO = (
    "Somente leitura. Para alterar, edite os arquivos em config/ e reinicie o "
    "painel."
)
UI_CONFIG_ENV = "Credenciais (.env)"
UI_CONFIG_ENV_INTRO = (
    "Apenas a presença de cada variável é exibida; os valores nunca saem do "
    "servidor."
)
UI_CONFIG_ENV_PRESENT = "definida"
UI_CONFIG_ENV_MISSING = "ausente"
UI_CONFIG_SCOPE = "Escopo da migração"
UI_CONFIG_MAPPING = "Mapeamento de campos (mapping.yml)"
UI_CONFIG_STATUS_MAP = "Mapa de status (status_map.yml)"
UI_CONFIG_USER_MAP = "Mapa de usuários (user_map.yml)"
UI_CONFIG_NEVER_WRITE = "Colunas nunca gravadas"

UI_EMPTY = "(nenhum)"
UI_UNEXPECTED_ERROR = "Erro inesperado: {detail}"
