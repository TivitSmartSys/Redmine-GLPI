# Redmine → GLPI

Migração de projetos do Redmine para o GLPI: projeto, tarefas, campos adicionais
(container 15) e Faturamento (container 25), a partir de **um número de issue**.

O modo padrão é **simulação**: nada é gravado no GLPI sem uma ação explícita e
uma confirmação digitada. Toda execução produz um relatório que registra cada
campo de origem — inclusive os que não chegaram ao GLPI e por quê.

A especificação completa está em `INSTRUKCJA_Redmine_do_GLPI_2.md`.

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
| `GLPI_URL` | URL da API do GLPI |
| `GLPI_APP_TOKEN` | App-Token do GLPI |
| `GLPI_USER_TOKEN` | user_token do GLPI |

## Painel web

```bash
python serve.py                       # http://127.0.0.1:8000 (abre o navegador)
python serve.py --port 8123 --no-browser
```

Escuta **somente em 127.0.0.1**. Reúne, em quatro seções, tudo o que a ferramenta
faz:

- **Migração** — número da issue, modo Simulação/Gravação, execução ao vivo,
  cartões-resumo e o relatório completo (idêntico ao `.txt`), com cópia e
  download.
- **Auditoria** — quais valores existentes no Redmine seriam perdidos por não
  haver entrada correspondente no dicionário do GLPI.
- **Histórico** — o que já foi criado, segundo `migration.db`.
- **Configuração** — mapeamento de campos, mapas de status e de usuários,
  escopo da migração e presença (nunca o valor) de cada credencial.

Gravar exige trocar o modo para **Gravação** e digitar `sim` na confirmação —
a mesma regra da linha de comando. Uma execução por vez.

## Linha de comando

```bash
python main.py --issue 20238                        # simulação (padrão)
python main.py --issue 20238 --report saida.txt     # simulação + relatório em arquivo
python main.py --issue 20238 --apply                # grava, após digitar "sim"
python main.py --issue 20238 --apply --yes          # grava sem perguntar (pipelines)

python audit_coverage.py                            # auditoria, tracker 14
python audit_coverage.py --tracker 42
```

Códigos de saída: `0` sucesso · `1` falha · `2` erro de configuração.

## Testes

```bash
python -m pytest tests -q
```

Cobrem a lógica pura: os números dos cartões-resumo contra a seção de
integridade do relatório, e o portão de confirmação de gravação.

## Estrutura

| Pasta | |
|---|---|
| `clients/` | APIs do Redmine e do GLPI |
| `transform/` | mapeamento de campos e classificação da árvore |
| `resolve/` | status, usuários e dicionários |
| `report/` | relatório e todo o texto em PT-BR |
| `store/` | mapa local da migração (SQLite) |
| `web/` | painel web (Flask) sobre as mesmas funções da CLI |
| `config/` | `mapping.yml`, `status_map.yml`, `user_map.yml`, constantes |
