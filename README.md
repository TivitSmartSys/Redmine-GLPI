# Redmine → GLPI

Migração de projetos do Redmine para o GLPI: o projeto, suas tarefas, os campos
adicionais do plugin Fields, o Faturamento, as **notas**, os **anexos**, a data
de criação original e a **entidade** correspondente ao Cliente.

O modo padrão é **simulação**: nada é gravado no GLPI sem uma ação explícita e
uma confirmação digitada. Toda execução produz um relatório que registra cada
campo de origem — inclusive os que não chegaram ao GLPI e por quê.

A especificação completa está em `INSTRUKCJA_Redmine_do_GLPI_2.md`; a
arquitetura e as armadilhas verificadas estão em `CLAUDE.md`; a implantação em
servidor está em `DEPLOY.md`.

## Instalação

```bash
pip install -r requirements.txt
cp .env.example .env      # e preencha os cinco valores
```

`.env` (nunca versionado):

| Variável | |
|---|---|
| `REDMINE_URL` | URL base do Redmine (sem `apirest.php`) |
| `REDMINE_API_KEY` | chave da API do Redmine |
| `GLPI_URL` | URL da API do GLPI (com `apirest.php`) |
| `GLPI_APP_TOKEN` | App-Token do GLPI |
| `GLPI_USER_TOKEN` | user_token do GLPI |

O Redmine é lido e **nunca** escrito. Toda a memória da migração vive no GLPI
(o marcador `rdmfield`) e no arquivo SQLite local.

## Painel web

```bash
python serve.py                       # http://127.0.0.1:8000 (abre o navegador)
python serve.py --port 8123 --no-browser
```

Escuta **somente em 127.0.0.1**. Reúne, em seis seções, tudo o que a ferramenta
faz:

- **Migração** — número da issue, modo Simulação/Gravação, execução ao vivo,
  cartões-resumo e o relatório completo (idêntico ao `.txt`), com cópia e
  download.
- **Lote** — migra um projeto inteiro do Redmine, com limite opcional, barra de
  progresso, retomada de execuções anteriores e os três artefatos de cada
  execução: o resumo, o relatório de cada item e o console gravado em disco.
- **Progresso** — quanto já foi migrado contra o escopo real, medido no
  momento da leitura. Lê sempre o cache; atualizar é uma execução como outra
  qualquer, e só faz GET.
- **Auditoria** — quais valores existentes no Redmine seriam perdidos por não
  haver entrada correspondente no dicionário do GLPI.
- **Histórico** — o que já foi criado, segundo `migration.db`.
- **Configuração** — a instância apontada, mapeamento de campos, mapas de
  status, de usuários e de entidades, escopo da migração, as constantes
  operacionais e a presença (nunca o valor) de cada credencial.

Gravar exige trocar o modo para **Gravação** e digitar `sim` na confirmação —
a mesma regra da linha de comando. Uma execução por vez.

O painel **não** expõe as opções de pular anexos ou notas: são chaves de
depuração, e uma caixa de seleção numa execução de milhares de itens está a um
clique de uma migração parcial silenciosa.

## Linha de comando

```bash
# uma issue
python main.py --issue 20238                        # simulação (padrão)
python main.py --issue 20238 --report saida.txt     # simulação + relatório em arquivo
python main.py --issue 20238 --apply                # grava, após digitar "sim"
python main.py --issue 20238 --apply --yes          # grava sem perguntar (pipelines)

# um projeto inteiro do Redmine, em lotes supervisionados e retomáveis
python migrate_batch.py --project operacao-cemig              # simulação
python migrate_batch.py --project hydro --apply --limit 20    # grava, 20 raízes pendentes
python migrate_batch.py --project hydro --resume <run-id>     # retoma pendentes e falhas

# leitura, nunca escrita
python migration_status.py                          # progresso no terminal
python migration_status.py --html reports/status.html
python audit_coverage.py --tracker 42               # cobertura dos dicionários

# desfazer o registro de uma migração (não apaga o projeto)
python reset_migration.py --issue 16467             # diagnóstico
python reset_migration.py --issue 16467 --apply     # apaga marcador e mapa local
```

Códigos de saída: `0` sucesso · `1` falha · `2` erro de configuração.

`purge_import.py` está **desativado** e recusa qualquer execução. Ele foi feito
para a instância de testes, onde havia um import defeituoso a remover; nesta
instância ele selecionaria mais de mil projetos legítimos. Não use.

## Testes

```bash
python -m pytest tests -q        # 322 testes
```

Cobrem o portão de confirmação, a aritmética das três seções do relatório e dos
cartões-resumo, o corte em 255 caracteres, o mapa cliente→entidade, o escopo
CEMIG, o deslocamento da data de criação, as fases de anexos e notas, o livro
de lotes e o painel. Não há linter nem etapa de build.

A verificação de verdade é a simulação contra uma issue real — ela exercita a
sessão, o preflight, os dicionários e a árvore inteira.

## Estrutura

| Pasta | |
|---|---|
| `clients/` | APIs do Redmine e do GLPI |
| `transform/` | mapeamento de campos, classificação da árvore, anexos e notas |
| `resolve/` | status, usuários, dicionários e entidades |
| `report/` | relatório e todo o texto em PT-BR |
| `store/` | mapa local da migração e o livro de lotes (SQLite) |
| `batch/` | seleção, laço e resumo das execuções em lote |
| `web/` | painel web (Flask) sobre as mesmas funções da CLI |
| `config/` | `mapping.yml`, `status_map.yml`, `user_map.yml`, `entity_map.yml`, constantes |
