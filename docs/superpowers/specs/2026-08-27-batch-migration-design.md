# Migração em lote — limpeza do import de junho e execução por projeto Redmine

Data: 2026-08-27. Decisões fechadas com o gestor na mesma data.

## O problema

Até aqui a migração roda uma issue por vez (`python main.py --issue N`). Faltam
**5627 projetos**. O pedido original — "comparar cada issue do projeto e enviar
as que ainda não estão no GLPI" — não pode ser atendido como está, porque a
pergunta "esta issue já foi migrada?" não tem resposta confiável hoje.

### Medições que motivam este documento (2026-08-27, instâncias reais)

**Redmine tem exatamente 4 projetos.** Roots em escopo (trackers 14/42/39, todos
sem pai):

| Projeto Redmine | Tracker | Roots |
|---|---|---|
| Área de Telecom | 14 Projeto | 5451 |
| HYDRO | 42 Projeto Hydro | 170 |
| Operação CEMIG | 39 Projeto CEMIG | 6 |
| Configuração REDE CORP VOIP | — | **0 issues de qualquer tracker** |
| | | **5627** |

As 3459 Faturamento, 1289 Atividades, 69 Compras e 49 Subtarefas entram como
tarefas sob seus roots, não como itens da lista.

**O marcador `rdmfield` está envenenado.** O GLPI tem 1274 projetos, dos quais
**1253 foram criados em 2026-06-06** por um import em lote que não é esta
ferramenta (`migration.db` conhece 48 linhas). Comparando cada marcador com o
número "RDM &lt;n&gt;" do nome do próprio projeto:

| | |
|---|---|
| marcador **contradiz** o nome | **698** |
| nome sem número RDM — não verificável | 139 |
| marcador **confere** com o nome | **5** |

O projeto 579 chama-se `RDM 17673 - ENEL CE LDAT SED JGR x SED BFG` e carrega
`rdmfield` 17343. O marcador `16950` está em **13 projetos** distintos, o 17018
em 5 — todos vivos, todos com nomes sem relação entre si. Além disso, só 909 dos
1253 têm linha de container 15; **344 não têm marcador nenhum** e são invisíveis
ao dedup.

Amostra de 40 desses projetos de junho: **0 notas e 0 documentos vinculados**,
contra 9 notas e 40 documentos nos 9 projetos feitos por esta ferramenta. São
cascas vazias. (Tarefas não são contáveis pela rota `GET /ProjectTask` — ela
devolve 0 linhas para a instância inteira, embora a tarefa 14141 leia normalmente
por id.)

**Consequência para qualquer lote, nas duas direções ao mesmo tempo:** ~660
issues seriam puladas por acharem seu marcador no projeto de outra issue, e os
1253 projetos de junho seriam duplicados por não haver como encontrá-los pelo
marcador correto.

## Decisões fechadas

1. **Modelo de execução: lote por projeto Redmine, lançado à mão, em porções,
   com retomada.** Não uma passada única desassistida. Motivo: a primeira
   execução sobre milhares de itens falha em algo ainda desconhecido, e uma
   porção de 200 produz um relatório legível por inteiro antes de repetir o mesmo
   erro 4792 vezes.
2. **O import de junho é apagado, não reparado.** Reparar os marcadores a partir
   dos nomes recuperaria 764 das 930 linhas, mas deixaria 1253 cascas sem
   tarefas, notas nem arquivos, indistinguíveis de migrações completas. Confirmado
   pelo gestor: era um teste.
3. **Limpeza total com `force_purge`**, não lixeira.
4. **Os projetos de teste também são apagados**, incluindo `1291` e `1295`, que
   foram feitos por esta ferramenta mas são testes.
5. **As ~660 issues hoje puladas por marcadores falsos são migradas.** Depois da
   limpeza não há marcadores falsos, então o total volta a ser os 5627 completos.
6. **Não foi feita a medição prévia de quais dos 1253 o lote conseguiria
   recriar.** Levantado como risco, dispensado pelo gestor. O registro do dry-run
   da limpeza cobre a necessidade: ele lista todos os alvos antes da confirmação.
7. **Tudo tem de ficar registrado no relatório final** — exigência do gestor.
   Isso inclui o que a Fase 0 apagou, não só o que a Fase 1 escreveu.
8. **O Redmine é somente-leitura. Nada, em nenhuma fase, escreve nele.**
   Exigência do gestor, 2026-08-27: "migramos, mas não mudamos nada no Redmine".

## Invariante: o Redmine nunca é escrito

Verificado no código em 2026-08-27: `RedmineClient` emite exclusivamente `GET` —
em `_get()` (clients/redmine.py:87) e no download de anexos
(clients/redmine.py:223). Não existe um único `POST`, `PUT`, `DELETE` ou `PATCH`
dirigido ao Redmine em todo o repositório.

O lote **aumenta o risco de isso mudar sem querer**, porque é a primeira vez que
o código percorre milhares de issues e a tentação de "marcar como migrada no
Redmine" aparece naturalmente. Não faça. O estado de migração vive no GLPI (o
marcador `rdmfield`) e no `migration_map`/`batch_item` locais — nunca na origem.

Isso é fixado por teste, não por convenção: um teste percorre `RedmineClient` e
falha se qualquer verbo de escrita aparecer. Um dedup que dependesse de escrever
no Redmine também violaria a regra — a leitura em massa do container 15 existe
justamente para não precisar disso.

## Fase 0 — `purge_import.py`

Ferramenta de uso único, no mesmo padrão de `reset_migration.py`: dry-run por
omissão, `--apply` com confirmação "sim".

### Seleção do alvo

O alvo é definido **positivamente**, nunca como "apague o resto":

```
ALVO  = {projetos com date_creation em 2026-06-06} ∪ {lista explícita de ids de teste}
MANTÉM = 1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298   (9 projetos)
```

Total: **1265 apagados de 1274**.

A lista `MANTÉM` é literal no código, e o script **recusa-se a rodar** se algum
desses ids cair no alvo. A lista não é arbitrária — cada um dos 9 foi verificado
individualmente: o `rdmfield` aponta para um root real em escopo e o nome do
projeto confere com o assunto dessa issue.

| GLPI | RDM | Tracker |
|---|---|---|
| 1286 | 20438 | 14 |
| 1287 | 20472 | 14 |
| 1288 | 2101 | 14 (apesar do nome "Bodyshop") |
| 1289 | 20280 | 14 |
| 1290 | 19533 | 14 |
| 1292 | 19074 | 39 |
| 1293 | 18729 | 39 |
| 1296 | 20556 | 14 |
| 1298 | 15815 | 14 |

Nota sobre o 1298: seu `date_creation` é 2023-09-20 porque é o teste da função
de deslocamento de data criada em 2026-08-27, não porque seja um projeto antigo
alheio à migração.

### Ordem de exclusão — invertida de propósito

Para cada alvo: **linha do container 15 primeiro, projeto por último.**

Uma falha no meio do caminho tem de deixar o estado recuperável. Apagando o
projeto primeiro, uma falha deixa uma linha de container órfã com o marcador —
exatamente o veneno que estamos removendo, e o caso que originou o
`reset_migration.py`. Na ordem inversa, a falha deixa um projeto sem marcador,
que é reversível.

Sequência completa por alvo: container 15 → tarefas e suas linhas de container
26 → `Notepad` → `Document_Item` → `force_purge` do projeto. As medições dizem
que os projetos de junho não têm notas nem documentos, mas o script verifica e
reporta em vez de assumir.

### Verificação final

Releitura da contagem de projetos e das linhas de container 15. Esperado: 9
projetos e apenas linhas pertencentes a eles. **A limpeza não está concluída sem
essa leitura** — é ela que prova que o dedup deixou de estar envenenado.

### Registro permanente

O resultado da limpeza é gravado em disco (`reports/purge-<timestamp>.txt`) e é
insumo do relatório final da Fase 1, conforme a decisão 7.

## Fase 1 — `migrate_batch.py`

### Seleção

`--project hydro | operacao-cemig | projetos-telecom`, com `--limit N` e
`--resume`. A lista de pendentes é: roots do tracker daquele projeto sem pai,
**menos** os marcadores lidos numa **única leitura em massa** do container 15.
Não 5627 buscas individuais — a diferença é entre um minuto e uma hora só para
montar a lista.

### Sessões abertas uma vez

Preflight, alargamento de entidade para a raiz e carga dos dicionários de
dropdown acontecem **uma vez por lote**, não por item.

### Um item = o `main.py` que já existe

O lote chama `build_project_plan` → `apply_plan` dentro de `try/except`. Nenhuma
lógica de migração é duplicada: o lote é laço, ordem e contabilidade. Tudo que
está em CLAUDE.md (entidades, containers, notas, arquivos, datas, truncamento em
255) continua valendo sem alteração.

### Falha isolada

Uma falha de item registra o motivo e segue. Parada dura só para sessão GLPI
morta ou preflight reprovado — os casos em que as tentativas seguintes seriam
inúteis. Com 5627 itens, parar no primeiro arquivo de 33 MB ou no primeiro 403
do Redmine significa nunca terminar.

### Livro-razão

Nova tabela `batch_item` no SQLite: run, issue, estado
(`pending`/`ok`/`failed`/`skipped`), motivo, timestamps. `--resume` relê os
`pending` e `failed`.

Deliberadamente **separada de `migration_map`**, que responde outra pergunta —
"o que tem qual contrapartida no GLPI", não "o que já tentamos".

### Relatórios

O contrato da spec 13 (nada desaparece em silêncio) é mantido item a item: cada
um gera seu relatório completo pelo `Reporter` existente, em
`reports/<run>/RDM<id>.txt`.

Acima deles, um `resumo.txt` com os números por desfecho e **cada falha com seu
motivo, escrita por extenso**. O relatório agregado não substitui os detalhados —
soma-os, e incorpora o registro da Fase 0.

### Ordem: do menor para o maior

CEMIG (4) → HYDRO (170) → Telecom (5451). Os primeiros quatro custam minutos e
mostram se a Fase 0 foi bem feita. Começar pelo Telecom é conferir isso numa
conta de 5451 itens.

### Arquivos e notas

`--skip-attachments` e `--skip-notes` já existem no `main.py`; o lote apenas os
repassa. Se o download de arquivos dominar o tempo, é possível rodar a estrutura
primeiro e os arquivos numa segunda passada sobre o mesmo livro-razão.

## Testes

Somados aos 137 existentes:

- montagem da lista de pendentes, com subtração dos marcadores;
- retomada após interrupção;
- isolamento da falha de um item — o lote continua;
- dry-run por omissão nas duas ferramentas novas;
- recusa da limpeza quando o alvo alcança um projeto da lista `MANTÉM`;
- o relatório agregado soma exatamente os desfechos dos relatórios individuais;
- **`RedmineClient` não expõe nenhum verbo de escrita** — pino do invariante
  acima.

## Fora de escopo

- Reparar marcadores do import de junho (rejeitado — decisão 2).
- Atualizar projetos já migrados. A regra da v1 continua: encontrado o marcador,
  o projeto não é tocado.
- Backfill do `date_creation` nos projetos migrados antes de 2026-08-27. Segue
  sendo uma execução à parte, como registrado em CLAUDE.md.
- O projeto Redmine "Configuração REDE CORP VOIP" — está vazio, não há o que
  migrar.
