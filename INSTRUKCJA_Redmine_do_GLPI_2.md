# Instrukcja budowy aplikacji: migracja Redmine → GLPI

**Wersja:** 1.5 · **Data:** 2026-07-24
**Odbiorca:** AI/developer implementujący aplikację
**Status faktów:** wszystkie ID, nazwy pól i endpointy zweryfikowane wywołaniami API na realnych instancjach. Podstawa: issues **19074** (tracker 39, drzewo potomków), **17582 / 18620 / 18826 / 20238 / 20314 / 20172** (tracker 14), **20389 / 20388 / 20378 / 17306** (tracker 15, Faturamento), **20154 / 19089 / 20292** (potomkowie), konfiguracja kontenerów 15 i 25, oraz analiza aktywności 26 trackerów. Zakres migracji zatwierdzony przez kierownictwo (sekcja 1a). Nie zgaduj — jeśli czegoś tu nie ma, oznaczone jest jako `DO WERYFIKACJI`.

**Otwarte `DO WERYFIKACJI` (żadne nie blokuje testu fazy 1):**
- test pól `mandatory` dla trackera 39 — wymagany PRZED fazą CEMIG, nie przed testem (sekcja 11.1)
- `entities_id` projektu (sekcja 11.3)
- znaczenie cf `Conformidade1` (sekcja 6.5)
- wybór wariantu obsługi pól custom zadań: `comment` (domyślny) vs `report-only` (sekcja 9.4)

**Zamknięte w wersji 1.5:** zakres migracji (7 żywych trackerów, reszta poza zakresem), reguła „dziecko spoza zakresu → pomiń + raport" (wariant c), lista testowa 11 RDM z dołączonym 20172 na ścieżkę Faturamento.

---

## 1. Cel

Aplikacja CLI (Python 3 + `requests`), która na podstawie **numeru issue w Redmine** tworzy w GLPI:

- **Projekt** (`Project`) z danych root-issue,
- **Zadania projektu** (`ProjectTask`) z całego drzewa potomków (rekurencyjnie, z zachowaniem hierarchii),
- **Wiersz pól dodatkowych** (plugin Fields, kontener 15) przypięty do projektu,
- **Wiersze Faturamento** (kontener 25) z powiązanych issues trackera 15,
- **Raport** wszystkich pól pominiętych i nierozwiązanych.

Tryb domyślny: **dry-run** (nic nie zapisuje, pokazuje plan + raport). Zapis wyłącznie po jawnym potwierdzeniu.

---

## 1a. Zatwierdzony zakres (decyzje kierownictwa, 2026-07-24)

### Trackery w zakresie migracji

| Tracker | Rola | Decyzja |
|---|---|---|
| 14 Projeto | Raiz | **Migrować** — najpierw test 11 RDM |
| 42 Projeto Hydro | Raiz | Migrować po teście |
| 39 Projeto CEMIG | Raiz | Migrować po teście (uwaga: pola obowiązkowe — sekcja 11.1) |
| 15 Faturamento | Faturamento | Migrować **tylko rekordy powiązane** z migrowanym projektem |
| 40 Subtarefa Cemig | Filho | Migrować jako zadania, po teście |

### Trackery poza zakresem

- **39 Projeto CEMIG** — **całkowicie poza zakresem** (decyzja 2026-07-24). Nie implementować, nie testować. Ryzyko pól obowiązkowych i cały tracker 39 odłożone na później. (Dotyczy to również trackera 40 Subtarefa Cemig, który jest dzieckiem wyłącznie projektów CEMIG.)
- **41 Compras** — „não migrar" jako osobny byt. **Jako dziecko** migrowanego projektu: patrz reguła niżej.
- **18 Atividades** — druga faza (priorytet: automatyzacja tworzenia atividades w GLPI). Jako dziecko: patrz reguła niżej.
- Wszystkie trackery martwe (30, 31, 22, 33, 29, 35, 34, 16, 28, 27) i puste (17, 19, 21, 23, 24, 26, 36, 37, 38) — **nie migrować**.

### Język aplikacji (wymóg)

- **Interfejs CLI, wszystkie komunikaty dla użytkownika, treść raportu, komunikaty błędów → portugalski (PT-BR).** Aplikacja jest używana przez zespół portugalskojęzyczny.
- **Nazwy zmiennych, funkcji i komentarze w kodzie → angielski.** Powód: dane i pola systemów są już po portugalsku (`situaofaturamentofield`, „Situação Faturamento"). Portugalskie nazwy zmiennych zlałyby się z nazwami pól i uczyniły kod nieczytelnym. Warstwa techniczna (kod) po angielsku, warstwa użytkownika (interfejs) po portugalsku.
- Dane migrowane (nazwy pól, wartości, statusy) przepisywane bez tłumaczenia — pochodzą z Redmine i idą do GLPI w oryginale.

### Reguła jednolita: dziecko trackera spoza zakresu

**Potwierdzona decyzja (wariant c):** gdy migrowany projekt ma dziecko o trackerze **spoza zakresu** (Compras, Atividades lub dowolny inny nieujęty powyżej), zadanie **nie jest tworzone** w GLPI, a do raportu trafia jawna notatka:

```
POMINIĘTO podzadanie <tracker> <redmine_id> „<subject>" — tracker poza zakresem migracji
```

Projekt-rodzic powstaje normalnie. Nic nie znika po cichu. Zasada obowiązuje jednolicie dla wszystkich trackerów spoza zakresu — nie ma osobnych reguł per tracker.

### Lista testowa (faza 1 — „Teste GLPI Projetos")

11 projektów. Jeśli migracja się powiedzie, przechodzimy do fazy właściwej.

| RDM | Znane cechy | Co testuje |
|---|---|---|
| 20238 | tracker 14, pola prawie puste, brak dzieci/relacji | projekt minimalny |
| 20237 | tracker 14, brak dzieci/relacji | projekt prosty |
| 20156 | tracker 14, **dziecko Atividades (20292)** | reguła „dziecko spoza zakresu" |
| 20314 | tracker 14, 7 załączników | pola pełne |
| 17455 | tracker 14 | — |
| 17444 | tracker 14 | — |
| 18620 | tracker 14, **dziecko Compras (20154)**, relacja 18655 | reguła „dziecko spoza zakresu" + relacja |
| 18826 | tracker 14, **dziecko Compras (19073)**, relacje 18897/17575 | j.w. + wiele relacji |
| 17582 | tracker 14, **12 załączników 55 MB**, relacja 18471 | pełne pola, relacja |
| 17176 | tracker 14 | — |
| **20172** | tracker 14, **powiązane Faturamento (20389)** | **ścieżka kontenera 25** |

**20172 dodany do listy testowej decyzją kierownictwa** — jako jedyny RDM z powiązanym Faturamento, testuje ścieżkę kontenera 25. Bez niego zatwierdzony moduł Faturamento zostałby nieprzetestowany do produkcji.

**Uwaga do fazy testowej:** żaden RDM z trackera 39 (CEMIG) nie jest w liście testowej. Sukces testu **nie** potwierdza migracji CEMIG — ryzyko pól obowiązkowych (sekcja 11.1) pozostaje nieprzetestowane i wymaga osobnego testu jednym projektem CEMIG przed odblokowaniem tej fazy.

---

## 2. Systemy i dostęp

| System | Adres bazowy | Uwierzytelnianie |
|---|---|---|
| Redmine (źródło) | `http://172.178.61.88` | nagłówek `X-Redmine-API-Key: <klucz>` |
| GLPI (cel) | `https://smartsystems-apps.brazilsouth.cloudapp.azure.com/apirest.php` | `initSession` → `Session-Token` + `App-Token` |

**To są dwa różne serwery.** Nigdy nie doklejaj `apirest.php` do adresu Redmine.

### Sesja GLPI

```
GET /apirest.php/initSession
  Headers: Authorization: user_token <USER_TOKEN>
           App-Token: <APP_TOKEN>
  → {"session_token": "..."}
```

Każde kolejne wywołanie: `Session-Token: <token>` + `App-Token: <token>`.
Na końcu: `GET /apirest.php/killSession`.

### Konfiguracja i sekrety

Wszystkie tokeny **wyłącznie ze zmiennych środowiskowych / pliku `.env` poza repozytorium**. Nigdy w kodzie, nigdy w logach, nigdy w komunikatach błędów. Wymagane zmienne:

```
REDMINE_URL, REDMINE_API_KEY
GLPI_URL, GLPI_USER_TOKEN, GLPI_APP_TOKEN
```

### Uprawnienia (zweryfikowane jako problem)

Profil API musi mieć prawa **odczytu i zapisu** do pluginu Fields. Token bez tych praw zwraca `["ERROR_RIGHT_MISSING", ...]` przy dostępie do `PluginFields*`. Aplikacja musi to wykryć **na starcie** (patrz sekcja 9, preflight) i przerwać z czytelnym komunikatem, zamiast tworzyć projekty z pustymi polami.

---

## 3. Model migracji (decyzja zamknięta)

```
issue root (np. 19074)          → glpi Project
  └─ każdy potomek, rekurencyjnie → glpi ProjectTask (projects_id = id projektu root)
       └─ potomek potomka          → glpi ProjectTask (projecttasks_id = id zadania-rodzica)
```

**Tracker prawie nie steruje logiką — istnieje dokładnie JEDEN wyjątek.**

```
tracker.id == 15 (Faturamento)  →  wiersz w kontenerze 25 (patrz 6.5)
każdy inny tracker              →  ProjectTask
```

Poza tym jednym rozgałęzieniem tracker jest ignorowany: root zawsze staje się Projektem, każdy inny potomek zawsze Zadaniem. Zweryfikowane trackery potomków: 40 („Subtarefa Cemig"), 41 („Compras") — oba traktowane identycznie.

**Dlaczego wyjątek dla 15 musi zostać:** tracker jest jedynym dyskryminatorem odróżniającym Faturamento. Bez niego (a) 42 issues Faturamento będących potomkami wpadłyby jako zwykłe zadania, tracąc Nº NF / Valor / Vencimento, oraz (b) algorytm relacji wciągnąłby dowolne powiązane issue — a `relates` łączy również Projeto↔Projeto (17582→18471, 18620→18655), co produkowałoby wiersze fakturowania z projektów.

### Pobieranie drzewa z Redmine

```
GET /issues/{id}.json?include=children,attachments,relations
```

**Uwaga krytyczna:** `include=children` zwraca dla dzieci **wyłącznie** `id`, `tracker`, `subject` — bez pól, bez dat, bez custom_fields. Dla każdego potomka trzeba wykonać **osobne** `GET /issues/{child_id}.json?include=children`, rekurencyjnie, aż do liści. Zabezpieczenie przed cyklem: zbiór odwiedzonych ID.

**Dwie pułapki parsowania (zweryfikowane na realnych danych):**

1. **Klucz `children` może w ogóle nie istnieć** w odpowiedzi, gdy issue nie ma potomków (issue 17582 — brak klucza; issue 19074 — klucz z 4 elementami). Używaj `issue.get("children", [])`, nigdy `issue["children"]`.
2. **`relations` to NIE potomkowie — ale NIE wolno ich ignorować.** Issue 17582 ma `relations: [{issue_to_id: 18471, relation_type: "relates"}]` — to powiązanie poziome, nie hierarchia. **Nigdy nie migruj issue powiązanego jako zadania.** Relacje służą wyłącznie do odnalezienia powiązanych issues **trackera 15 (Faturamento)** — patrz sekcja 6.5. Partnerzy o innym trackerze trafiają tylko do raportu.

---

## 4. Mapowanie pól — zasada nadrzędna

> **Mapuj po NAZWIE pola custom (`custom_fields[].name`), nie po jego ID.**

Powód: ID pól custom różnią się między trackerami Redmine. Dokument źródłowy `Mapeamento_REDMINE_x_GLPI.docx` opisywał tracker 14 („Projeto"); realne dane migrowane są także z trackera 39 („Projeto CEMIG"), który ma inny, uboższy zestaw pól.

**Dowód, że mapowanie po ID jest błędne (zweryfikowany na issue 17582):** dokument podawał dla „Situação Faturamento" wartość `custom_field_values[9]`. Realnie to pole ma **ID 41**, natomiast **ID 9 to „Ano de Início"**. Ta sama liczba w dokumencie opisywała dwa różne pola. Mapowanie po ID wpisałoby rok do pola statusu fakturowania.

Jeśli pole o danej nazwie nie występuje w issue → **pomiń i zapisz do raportu**. Nigdy nie zgaduj wartości.

---

## 5. Mapowanie: pola CORE projektu (`glpi_projects`)

Zapis przez `POST /apirest.php/Project` z `{"input": {...}}`.

| GLPI (kolumna) | Źródło Redmine | Transformacja |
|---|---|---|
| `name` | `issue.subject` | wprost |
| `content` | `issue.description` | wprost |
| `projectstates_id` | `issue.status.id` | mapa statusów (sekcja 7) |
| `users_id` | `issue.assigned_to.id` | mapa użytkowników (sekcja 8) |
| `plan_start_date` | `issue.start_date` | wprost (`yyyy-mm-dd`), może być `null` |
| `plan_end_date` | cf `DATA TERMINO PLANEJADA`, fallback `issue.due_date` | `DO DECYZJI` — patrz sekcja 11 |
| `real_end_date` | cf `Data Finalização`, fallback `issue.closed_on` | `DO DECYZJI` — patrz 11.5 |
| `percent_done` | `issue.done_ratio` | wprost (liczba 0–100) |
| `comment` | cf `NOTAS` | pomiń jeśli puste |
| `entities_id` | — | `DO DECYZJI` — patrz sekcja 11 |

**Pominięte świadomie (decyzje użytkownika):**
- `priority` — pomijamy w całości.
- Cliente / Entidade — ustawiane **ręcznie** po migracji. Nie próbuj rozwiązywać ani tworzyć encji.
- Załączniki (`Documentos`) — poza zakresem v1.

**Pominięte z braku odpowiednika:** `Ano de Início` (GLPI wyprowadza rok z daty utworzenia).

---

## 6. Mapowanie: pola PLUGIN (kontener „camposadicionaisprojetos")

### 6.1 Mechanika zapisu

Itemtype: **`PluginFieldsProjectcamposadicionaisprojeto`**
Klucze linkujące (zweryfikowane na realnym wierszu):

```json
{
  "items_id": <ID projektu GLPI>,
  "itemtype": "Project",
  "plugin_fields_containers_id": 15
}
```

**Sekwencja (obowiązkowa):**

1. Utwórz projekt przez `POST /Project` → odbierz `id`.
2. `GET /PluginFieldsProjectcamposadicionaisprojeto?searchText[items_id]=<id>` — sprawdź, czy plugin sam utworzył wiersz.
3. Jeśli wiersz istnieje → `PUT /PluginFieldsProjectcamposadicionaisprojeto/<row_id>`.
   Jeśli nie istnieje → `POST /PluginFieldsProjectcamposadicionaisprojeto`.

Oba warianty muszą być zaimplementowane — plugin zachowuje się różnie zależnie od wersji i konfiguracji.

### 6.2 Pola bezpośrednie (text / date / yesno) — wpisz wartość wprost

| Kolumna GLPI | Nazwa pola custom w Redmine | Typ |
|---|---|---|
| `rdmfield` | *(nie z Redmine)* → **`issue.id`, np. `19074`** | text — **marker deduplikacji** |
| `dataderecebimentodatapfield` | `Data de recebimento da TAP` | date `yyyy-mm-dd` |
| `nmerodopedidoclientefield` | `Número do pedido cliente` | text |
| `datadesolicitaobaremofield` | `Data de solicitação Baremos` | date |
| `datadeentregadapropostafield` | `Data de entrega da proposta` | date |
| `datadeaprovaodapropostafield` | `Data de aprovação da proposta` | date |
| `andamentodoprojetofield` | `Andamento do Projeto` | text |
| `valordoprojetofield` | `Valor do Projeto` | text — **mandatory w GLPI** |
| `responsvelclientefieldtwo` | `Responsável Cliente` | text — **mandatory w GLPI** |
| `backlogfield` | `Backlog` | yesno — **wymaga transformacji**, patrz niżej |

**Transformacja `yesno`:** Redmine przekazuje tekst (`"Não"`, `"Sim"`), GLPI oczekuje `0` / `1`. Mapa (bez rozróżniania wielkości liter, po `strip()`): `Não`/`Nao`/`No`/`0` → `0`; `Sim`/`Yes`/`1` → `1`. Wartość nierozpoznana → **pomiń pole + ostrzeżenie**. Nigdy nie wpisuj surowego tekstu do pola `yesno`.

**Formaty liczb — UWAGA, dwie różne konwencje w tym samym systemie:**

| Pole | Przykłady | Konwencja |
|---|---|---|
| `Valor do Projeto` (tracker 14) | `66.977,45` · `160.882,04` · `1.258,70` | brazylijska (przecinek dziesiętny) |
| `Valor Total da NF` (tracker 15) | `11639.68` · `2121.66` · `" 5593.40"` | amerykańska (kropka dziesiętna) |

Oba docelowe pola GLPI są typu **text** → przepisz wartość **dosłownie, bez konwersji i bez parsowania do float**. **Obowiązkowo zastosuj `.strip()`** — realne dane zawierają wiodące spacje (`" 5593.40"`). Nie próbuj normalizować obu formatów do jednego; to zmieniłoby dane widziane przez użytkowników.

**Formatowanie opisu:** `issue.description` zawiera znaczniki Textile/Markdown Redmine (`*pogrubienie*`, `\r\n`, adresy URL). GLPI nie interpretuje Textile. W v1 przepisz tekst dosłownie; konwersja formatowania jest poza zakresem.

### 6.3 Pola dropdown — wymagają resolucji nazwa → ID

Procedura dla każdego: `GET /apirest.php/<Itemtype>?range=0-999`, znajdź wpis o `name`/`completename` **równym** wartości z Redmine (porównanie bez rozróżniania wielkości liter, po `strip()`), użyj jego `id`.

**Polityka przy braku dopasowania (decyzja zamknięta): POMIŃ POLE I ZAPISZ OSTRZEŻENIE. Nigdy nie twórz nowej pozycji słownika automatycznie.**

| Kolumna GLPI | Nazwa pola custom w Redmine | Itemtype słownika |
|---|---|---|
| `plugin_fields_tipodoprojetofielddropdowns_id` | `Tipo do Projeto` | `PluginFieldsTipodoprojetofielddropdown` |
| `plugin_fields_gestofielddropdowns_id` | `Gestão` | `PluginFieldsGestofielddropdown` — **mandatory** |
| `plugin_fields_despesafielddropdowns_id` | `Despesa` | `PluginFieldsDespesafielddropdown` — **mandatory** |
| `plugin_fields_complexidadefielddropdowns_id` | `Complexidade` | `PluginFieldsComplexidadefielddropdown` — **mandatory** |
| `plugin_fields_pendnciafielddropdowns_id` | `Pendência` | `PluginFieldsPendnciafielddropdown` |
| `plugin_fields_tipodependnciafielddropdowns_id` | `Tipo de Pendência` | `PluginFieldsTipodependnciafielddropdown` |
| `plugin_fields_situaofaturamentofielddropdowns_id` | `Situação Faturamento` | `PluginFieldsSituaofaturamentofielddropdown` |
| `plugin_fields_enviodorelatriofielddropdowns_id` | `Envio do Relatório` | `PluginFieldsEnviodorelatriofielddropdown` |
| `plugin_fields_solicitaodoclientefielddropdowns_id` | `Solicitação do Cliente` | `PluginFieldsSolicitaodoclientefielddropdown` |
| `plugin_fields_enviodobaremofielddropdowns_id` | `Envio do Baremos` | `PluginFieldsEnviodobaremofielddropdown` |
| `plugin_fields_aprovaodoclientefielddropdowns_id` | `Aprovação do Cliente` | `PluginFieldsAprovaodoclientefielddropdown` |
| `plugin_fields_aprovaoparafinalizarfielddropdowns_id` | `Aprovação para Finalizar` | `PluginFieldsAprovaoparafinalizarfielddropdown` |
| `plugin_fields_clientefielddropdowns_id` | — | **NIE ZAPISUJ** (Cliente ustawiany ręcznie) |

`DO WERYFIKACJI`: wielkość liter w nazwach itemtype. Odpowiedź API podawała je w dwóch wariantach (`...fielddropdown` w `links`, `...fieldDropdown` w `listSearchOptions`). Użyj wariantu z `links` (małe `d`); przy `ERROR_ITEMTYPE_NOT_FOUND` spróbuj drugiego.

### 6.4 Pola NIEAKTYWNE — nigdy nie zapisuj

Mają `is_active: 0`. Zapis do nich jest błędem:

- `idrdmfield` (ID_RDM) — nieaktywne; **marker to `rdmfield`, nie to pole**
- `responsvelprojetofield` (Responsável Projeto) — nieaktywne; odpowiedzialny idzie w core `users_id`
- `statusfaturamentofield` (Status Faturamento) — nieaktywne; właściwe pole to `situaofaturamentofield`

---

## 6.5 Mapowanie: kontener „Faturamento" (kontener 25)

**Status: dane źródłowe ISTNIEJĄ** — `GET /issues.json?tracker_id=15&status_id=*` zwraca `total_count: 3409`. (Wcześniejszy odczyt „0" pochodził z tokena o ograniczonych uprawnieniach i był błędny.)

Itemtype: **`PluginFieldsProjectfaturamento`**, `plugin_fields_containers_id = 25`, przypięty do `itemtype: "Project"`.

### Mapowanie pól (issue trackera 15 → kolumny kontenera 25)

| Kolumna GLPI | Źródło Redmine | Typ / uwagi |
|---|---|---|
| `ttulofield` | `issue.subject` | text |
| `descriofield` | `issue.description` | textarea |
| `nnffield` | cf `No. NF` | text |
| `valortotaldanffieldtwo` | cf `Valor Total da NF` | text — `.strip()`, format US |
| `npedidoclientefield` | cf `No. Pedido Cliente` | text |
| `conformidadefield` | cf `Conformidade` | **yesno** — `Não`→0, `Sim`→1 |
| `competnciafield` | cf `Competência` | text |
| `emissodanffield` | cf `Emissão da NF` | date |
| `projeodefaturamentofield` | cf `Projeção de Faturamento` | date |
| `vencimentofield` | cf `Vencimento` | date |
| `parcelaprojnanffield` | cf `Parcela Proj na NF` | text |
| `observaefield` | cf `Observações` | textarea |
| `prioridadefield` | — | **NIE ZAPISUJ** (priorytet pomijamy globalnie) |

**Bez odpowiednika w GLPI → pomiń + raport:**
- cf `Responsável Cliente NF` (np. „Roberto Gonçalves", „Eloídis")
- cf `Cliente` (ustawiany ręcznie, zgodnie z decyzją globalną)

`DO WERYFIKACJI`: cf **`Conformidade1`** (ID 154) — nazwa sugeruje duplikat/wariant `Conformidade`, a w GLPI istnieje osobne pole `nconformidadefield` („Nº Conformidade"). We wszystkich zbadanych issues `Conformidade1` jest **puste**. Nie mapuj, dopóki nie potwierdzisz znaczenia.

### Powiązanie Faturamento z projektem — ROZSTRZYGNIĘTE

Kontener 25 jest przypięty do `Project`, więc każdy wiersz musi mieć projekt-właściciela. Weryfikacja na realnych danych:

| Sposób powiązania | Skala | Dowód |
|---|---|---|
| przez `relations` (`relates`) | dominujący | 20389 ↔ 20172 |
| przez `parent` | **42** z 3409 (1,2%) | 17306 → parent 17081 |
| brak powiązania | prawdopodobnie większość | 20388, 20378 — brak `parent`, brak `relations` |

**Algorytm (obie ścieżki obowiązkowe):**

1. Po utworzeniu projektu GLPI pobierz `relations` root-issue. Dla każdej relacji ustal ID partnera, pobierz je (`GET /issues/{id}.json`) i **sprawdź `tracker.id`**. Jeśli `== 15` → utwórz wiersz kontenera 25 przypięty do tego projektu. Jeśli inny tracker → **zignoruj**, tylko odnotuj w raporcie.
2. Podczas przechodzenia drzewa potomków: potomek o `tracker.id == 15` **nie** staje się zadaniem — trafia do kontenera 25 tego samego projektu.

> ⚠️ **PUŁAPKA — kierunek relacji.** Redmine zapisuje relację raz, w kierunku zależnym od tego, kto ją utworzył:
> - issue 17582: `{"issue_id": 17582, "issue_to_id": 18471}` — nasze ID w `issue_id`
> - issue 20389: `{"issue_id": 20172, "issue_to_id": 20389}` — nasze ID w `issue_to_id`
>
> ID partnera wyznaczaj zawsze jako **to z dwóch pól, które nie jest ID bieżącego issue**. Odczytywanie na sztywno `issue_to_id` pominie połowę powiązań.

**Zakres v1 (decyzja):** migrowane są wyłącznie te issues Faturamento, które są powiązane (relacją lub rodzicem) z migrowanym projektem. Faturamento bez powiązania — a to prawdopodobnie większość z 3409 — **pozostaje poza zakresem**. Migracja masowa wymagałaby osobnego trybu i osobnej decyzji, do ktorego projektu GLPI je przypiąć.

### Wielokrotność kontenera 25 — POTWIERDZONA

```
GET /apirest.php/PluginFieldsContainer/25
→ {"id":25,"name":"faturamento","label":"Faturamento",
   "itemtypes":"[\"Project\"]","type":"tab","is_recursive":1,"is_active":1}
```

**`type: "tab"`** → kontener tworzy dedykowaną zakładkę działającą jak **lista rekordów**, obsługującą **wiele wierszy na jeden projekt**. (Dla porównania: typy `dom` i `domtab` wstrzykują pola do istniejącego formularza i dopuszczają jeden wiersz na obiekt — tak zachowuje się kontener 15.)

Wniosek: projekt powiązany z kilkoma Faturamento jest w pełni migrowalny. Każde powiązane issue trackera 15 = osobny wiersz.

**`itemtypes: ["Project"]`** → wiersze przypinamy wyłącznie do `Project`, nigdy do `ProjectTask`. Kontener **26** (bliźniaczy, sufiksy `fieldtwo`) obsługuje inny itemtype — **nie używać** w tej migracji.

**Fallback (zostawić mimo potwierdzenia — tanie ubezpieczenie):** jeśli drugi `POST` dla tego samego projektu zostanie mimo wszystko odrzucony, przełącz się na tryb degradacji — pierwszy wiersz w GLPI, pozostałe do raportu z kompletem wartości (Nº NF, Valor Total, Vencimento, Projeção, Nº Pedido, Competência, Observações). Zaloguj raz, nie przerywaj migracji.

**Zasada nadrzędna: żaden Faturamento nie może zniknąć po cichu.** Albo trafia do GLPI, albo do raportu z pełnymi danymi.

### Relacja Projeto↔Faturamento — POTWIERDZONA

`GET /issues/20172.json` → `tracker: {"id": 14, "name": "Projeto"}`, `subject: "Remanejamento de pontos de rede SEDE"`, `Cliente: "EQUATORIAL GO"` — identyczny temat i klient jak powiązane Faturamento 20389. Relacja `relates` jest używana zgodnie z założeniem algorytmu.

**Dowód konieczności zasady „nie parsuj liczb":**

| Issue | Pole | Wartość |
|---|---|---|
| 20172 (Projeto) | `Valor do Projeto` | `5.593,40` |
| 20389 (Faturamento) | `Valor Total da NF` | `" 5593.40"` |

Ta sama kwota, dwie konwencje, w jednej powiązanej parze. Normalizacja parserem rozjechałaby wartości między projektem a fakturą. Przepisuj dosłownie, stosuj wyłącznie `.strip()`.

`DO WERYFIKACJI`: czy kontener 25 dopuszcza **wiele wierszy na jeden projekt** (zakładka wielokrotna). Przy 3409 issues i relacji wiele-do-jednego jest to prawdopodobne, ale niepotwierdzone. Sprawdź `GET /PluginFieldsContainer/25`.

Uwaga: istnieje bliźniaczy kontener **26** (pola z sufiksem `fieldtwo`) — prawdopodobnie ta sama struktura dla `ProjectTask`. Jeśli Faturamento ma trafiać na poziom zadania, a nie projektu, użyj 26. `DO WERYFIKACJI`.

---

`issue.status.id` (Redmine) → `projectstates_id` (GLPI). Ta sama mapa dla Projektu i Zadania.

| Redmine ID | Nazwa | GLPI ID |
|---|---|---|
| 15 | Novo | 1 |
| 13 | Em Elaboração | 7 |
| 9 | Aguardando Aprovação | 8 |
| 23 | Planejamento | 9 |
| 17 | Parado | 5 |
| 14 | Em Execução | 2 |
| 24 | Monitoramento e Controle | 6 |
| 25 | Encerramento | 10 |
| 16 | Finalizado | 3 |
| 11 | Cancelado | 4 |
| 18 | Não Aprovado | 11 |

Status spoza mapy → **pomiń pole, dodaj ostrzeżenie**, nie przerywaj migracji.

---

## 8. Mapa użytkowników

`issue.assigned_to.id` (Redmine) → `users_id` (GLPI).

| RDM | login | GLPI |
|---|---|---|
| 33 | alexandre.parra | 13 |
| 178 | ana.marreira | 269 |
| 95 | ana.nsilveira | 102 |
| 185 | andre.mapurunga | 289 |
| 190 | caroline.menezes | 771 |
| 173 | cristiane.cortes | 127 |
| 156 | daniel.honorato | 12 |
| 128 | david.sales | 60 |
| 195 | desirene.costa | 68 |
| 84 | genna.lima | 66 |
| 174 | giullian.barbosa | 172 |
| 94 | guilherme.coelho | 70 |
| 183 | gustavo.aires | 110 |
| 15 | igor.lonnes | 101 |
| 187 | jeane.silva | 180 |
| 189 | katielle.oliveira | 293 |
| 199 | paulo.rocha | 768 |
| 162 | powerbi | **BRAK** |
| 197 | rayssa.mota | 769 |
| 62 | rogerio.ramos | 9 |
| 36 | sophia.ribeiro | 31 |
| 80 | teodoro.lima | 770 |
| 101 | thyago.lima | 161 |
| 166 | vanessa.diniz | 98 |

**Polityka (decyzja zamknięta):** użytkownik bez odpowiednika (w tym `powerbi`/162) oraz issue bez `assigned_to` → **zostaw pole puste + ostrzeżenie w raporcie**. Nigdy nie przerywaj migracji z tego powodu.

---

## 9. Kolejność operacji

### 9.0 Preflight (przed czymkolwiek)

1. `initSession` w GLPI — przerwij z czytelnym błędem przy niepowodzeniu.
2. `GET /PluginFieldsField?range=0-0` — test uprawnień do pluginu. Przy `ERROR_RIGHT_MISSING` **przerwij** z komunikatem: „Token API nie ma praw do pluginu Fields — pola dodatkowe nie zostaną zapisane. Nadaj prawa w Administração → Perfis → Campos adicionais."
3. Wczytaj i zbuforuj wszystkie słowniki dropdown (sekcja 6.3) — jedno wywołanie na słownik, nie na pole.

### 9.1 Deduplikacja (zastępuje pierwotny pomysł „sprawdź czy ID istnieje")

Redmine i GLPI mają niezależne auto-increment — ID nie mogą i nie muszą się zgadzać. Zamiast tego:

```
GET /apirest.php/PluginFieldsProjectcamposadicionaisprojeto?searchText[rdmfield]=<issue_id>
```

- Znaleziono → projekt już zmigrowany. **Poinformuj użytkownika, podaj ID projektu GLPI, przerwij** (v1 nie aktualizuje istniejących).
- Nie znaleziono → migruj.

Dodatkowo lokalna baza SQLite jako cache i zabezpieczenie przed przerwaną migracją:

```sql
CREATE TABLE migration_map (
  redmine_id   INTEGER NOT NULL,
  glpi_id      INTEGER NOT NULL,
  glpi_itemtype TEXT NOT NULL,      -- 'Project' | 'ProjectTask'
  parent_redmine_id INTEGER,
  status       TEXT NOT NULL,        -- 'ok' | 'partial' | 'failed'
  migrated_at  TEXT NOT NULL,
  PRIMARY KEY (redmine_id, glpi_itemtype)
);
```

Przed każdym `POST` sprawdź `migration_map` — jeśli węzeł już zmigrowany, pomiń (idempotencja, wznawianie po awarii sieci).

### 9.2 Przebieg

1. Pobierz root-issue + całe drzewo (rekurencyjnie, sekcja 3).
2. Zbuduj plan i **wypisz raport dry-run** (sekcja 10). **STOP** — czekaj na potwierdzenie użytkownika.
3. `POST /Project` z pól core (sekcja 5) → zapisz `glpi_id` do `migration_map`.
4. Zapis wiersza kontenera 15 (sekcja 6.1) — z `rdmfield = issue.id` **zawsze**, nawet jeśli reszta pól pusta.
5. Przejdź drzewo **rodzic-przed-dzieckiem** (BFS/DFS pre-order). Dla każdego węzła:
   `POST /ProjectTask` z `projects_id` = ID projektu root, `projecttasks_id` = ID zadania-rodzica z `migration_map` (lub brak dla dzieci root-a).
6. Wypisz raport końcowy.

### 9.3 Mapowanie zadań (`glpi_projecttasks`)

Podstawa: **`IN_SCOPE_CHILD_TRACKERS` zawiera teraz `18` (Atividades)** — decyzja kierownictwa 2026-07-30. Faza 1 potwierdzona jako udana (2 projekty, w tym 1 z Faturamento). Zakres: **jednorazowa** migracja istniejących Atividades jako zadań projektu (wariant a). To NIE jest automatyzacja ciągła.

| GLPI (`projecttasks`) | Źródło Redmine | Uwaga |
|---|---|---|
| `projects_id` | ID projektu root (zawsze) | zadanie nie istnieje bez projektu |
| `projecttasks_id` | ID zadania-rodzica z `migration_map` | dla dzieci root-a: brak |
| `name` | `issue.subject` | wprost |
| `content` | `issue.description` | wprost |
| `projectstates_id` | mapa statusów (ta sama co projekt) | |
| `plan_start_date` | `issue.start_date` | może być `null` |
| `plan_end_date` | `issue.due_date` | |
| `real_start_date` | cf `Início Real (GLPI)` | pole zaprojektowane pod GLPI; często puste |
| `real_end_date` | cf `Término Real (GLPI)` | j.w. |
| `percent_done` | `issue.done_ratio` | |
| `is_milestone` | cf `Marco` | **yesno**: Não→0, Sim→1 |

**Zweryfikowane na issue 20424 (tracker 18 Atividades).** Pola custom Atividades zostały świadomie zaprojektowane pod GLPI (nazwy z sufiksem „(GLPI)") — dlatego mapują się na realne kolumny `projecttasks`, a NIE lecą do komentarza jak pola innych trackerów.

#### Przypisanie wykonawcy — przez `ProjectTaskTeam`, nie pole na zadaniu

**Decyzja: wykonawcą zadania jest pole `Recurso (GLPI)`, nie `assigned_to`.**

Pole `Recurso (GLPI)` (cf 159) ma dwie właściwości wymagające specjalnej obsługi:

1. **Jest listą** (`"multiple": true, "value": ["katielle.oliveira", ...]`). Iteruj po całej liście — nie bierz `value[0]`. Każdy wpis → osobny członek zespołu zadania.
2. **Zawiera LOGIN, nie ID.** Istniejąca `USER_MAP` jest kluczowana ID Redmine — tu login nie pasuje. **Wymagana odwrócona mapa login→GLPI id**, zbudowana z komentarzy w `USER_MAP` (każdy wpis ma login). Login spoza mapy → pomiń tego wykonawcę + ostrzeżenie.

Zapis: dla każdego rozwiązanego użytkownika wykonaj `POST /ProjectTaskTeam` z `{projecttasks_id, itemtype:"User", items_id:<glpi_user_id>}` **po** utworzeniu zadania (potrzebne jego id). W dry-run: wypisz planowanych członków zespołu, nie zapisuj.

**`assigned_to` (osoba przypisana do issue) → do komentarza** (decyzja 2026-07-30). Wykonawcą zadania w GLPI jest Recurso (GLPI), ale osoba przypisana w Redmine jest zachowana jako informacja. W issue 20424 to dwie różne osoby (assigned_to=rayssa.mota, Recurso=katielle.oliveira). Linia komentarza: `Responsável no Redmine: <login>`.

#### Rozwiązanie loginu Recurso → GLPI id

**Decyzja: mapować login z `glpi_users.name`. POTWIERDZONE (2026-07-30): loginy Redmine są znakowo identyczne z `glpi_users.name`.** Login z pola Recurso (np. `katielle.oliveira`) rozwiązujemy do GLPI id przez zapytanie:
```
GET /apirest.php/User?searchText[name]=<login>
```

**Kolejność w kodzie (live-lookup główny, mapa fallbackiem):**
- `resolve_login_via_glpi` — live-lookup przez `/User` — **ścieżka główna** (loginy potwierdzone jako identyczne, mapa ręczna zbędna, nowi użytkownicy działają automatycznie).
- `LOGIN_TO_GLPI` — statyczna mapa login→id — **fallback** na wypadek niedostępności API lub pustej odpowiedzi.
- Login nierozwiązany żadną ścieżką → pomiń wykonawcę + ostrzeżenie.

#### Godziny bez daty → komentarz

`Hora Início (formato 24h)` i `Hora Término (formato 24h)` to same godziny (np. „8", „14"), bez dnia — nie da się ich wstawić w pole datetime GLPI. **Decyzja: do komentarza** (sekcja 9.4), nie do pól dat.

#### Pozostałe pola custom Atividades → komentarz

`Cliente`, `Tipo de Site`, `Observações`, godziny oraz `assigned_to` — brak odpowiednika na zadaniu (albo świadomie do komentarza) → komentarz + raport (sekcja 9.4). Format komentarza dla 20424:
```
[Campos migrados do Redmine]
Responsável no Redmine: rayssa.mota
Cliente: ENEL SP
Tipo de Site: SUBESTAÇÃO
Hora Início: 8
Hora Término: 14
```

### 9.4 Pola custom zadań — NIE WOLNO ich gubić po cichu

**Zweryfikowany fakt:** potomkowie mają własne pola custom, a ich zestaw **zależy od trackera**:

| Issue | Tracker | Pola custom |
|---|---|---|
| 20154 | 41 Compras | `Cliente`, `Data Finalização`, `Data Cotação`, `Solicitação Interna`, `Código Solicitação Interna`, `Data Aprovação FP&A`, `Pedido` |
| 19089 | 40 Subtarefa Cemig | `Pendência`, `Tipo de Pendência` |

Żadne z nich nie ma odpowiednika w `glpi_projecttasks` ani w kontenerze pluginu przypiętym do `ProjectTask` (kontenery 15 i 25 są przypięte wyłącznie do `Project`).

**Zestawu nie da się zmapować na sztywno** — każdy tracker potomka wnosi inne pola, a lista trackerów rośnie (widzianych: 26 sztuk). Obsługa musi być generyczna: iteruj po `issue.custom_fields`, nie po zadeklarowanej liście.

**Zachowanie (decyzja zamknięta — wariant b, „comment"):**

1. **Zawsze** dopisz każde niepuste pole custom zadania do raportu, w formacie: `zadanie <redmine_id> „<subject>": <nazwa pola> = <wartość> — brak odpowiednika w GLPI (zapisane w komentarzu)`.
2. **Zawsze** zrzuć te pola do `comment` tworzonego `ProjectTask` jako tekst strukturalny, poprzedzony nagłówkiem wskazującym pochodzenie, po jednej linii na pole:
   ```
   [Campos migrados do Redmine]
   Data Cotação: 2026-06-15
   Pedido: 6700155505
   ```
   Nagłówek `[Campos migrados do Redmine]` jest obowiązkowy — odróżnia dane z migracji od treści wpisanej ręcznie w GLPI.
3. Pola puste (`""` lub `null`) pomijaj w zrzucie, ale odnotuj zbiorczo w raporcie jako „puste w źródle".

**Uwaga:** pola custom Atividades oznaczone „(GLPI)" (Recurso, Início Real, Término Real) są wyjątkiem od tej reguły — mapują się na realne kolumny/relacje zadania (sekcja 9.3), nie do komentarza. Do komentarza trafiają tylko pola bez odpowiednika na zadaniu (Cliente, Tipo de Site, Observações, godziny).

`DO WERYFIKACJI` (nie blokuje — test to potwierdzi): dokładny payload `POST /ProjectTaskTeam` (`itemtype:"User"` + `items_id`). Jeśli endpoint odrzuci zapis, fallback: lista wykonawców do komentarza jako „Recurso: <login>".

---

## 10. Raport (kluczowe wymaganie funkcjonalne)

Raport dry-run **i** raport końcowy muszą zawierać:

1. **Co powstanie / powstało**: nazwa projektu, liczba zadań, drzewo z ID (Redmine → GLPI).
2. **Pola pominięte — brak odpowiednika w GLPI**, z nazwami z Redmine i wartościami.
3. **Pola pominięte — brak wartości w Redmine** (pole istnieje po obu stronach, źródło puste).
4. **Nierozwiązane odwołania**: użytkownik bez mapowania, status spoza mapy, wartość dropdown nieznaleziona w słowniku GLPI — **z podaniem konkretnej wartości**, np. `Tipo do Projeto = "Gestão de Projeto" — brak w słowniku GLPI, pole pominięte`.
5. **Pola obowiązkowe GLPI bez danych** — wyróżnione, bo mogą spowodować odrzucenie zapisu (sekcja 11.1).

Format: czytelny tekst na stdout + opcjonalny zapis do pliku `report_<issue_id>_<timestamp>.txt`.

---

## 11. Otwarte punkty do rozstrzygnięcia PRZED produkcją

### 11.1 KRYTYCZNE: pola obowiązkowe bez źródła danych

Kontener 15 ma pięć pól z `mandatory: 1`. **Pokrycie danymi zależy od trackera źródłowego** — to kluczowe rozróżnienie:

| Pole GLPI (mandatory) | tracker 14 „Projeto" (issue 17582) | tracker 39 „Projeto CEMIG" (issue 19074) |
|---|---|---|
| Despesa | ✔ `CAPEX GDS` | ✔ `Outro` |
| Complexidade | ✔ `Alta` | ✔ `Baixa` |
| **Valor do Projeto** | ✔ `66.977,45` | ✘ brak |
| **Gestão** | ✔ `ITALTEL` | ✘ brak |
| **Responsável Cliente** | ✔ `Roberto Gonçalves` | ✘ brak |

Wniosek: projekty z trackera 14 zapiszą się w komplecie. Problem dotyczy **wyłącznie** trackera 39 (i potencjalnie innych trackerów o uboższym zestawie pól). Aplikacja musi wykrywać brak danych dla pola `mandatory` **w fazie dry-run** i wyraźnie to sygnalizować, zanim spróbuje zapisu.

**Test rozstrzygający (wykonać przed implementacją):** utwórz ręcznie testowy projekt i spróbuj zapisać wiersz kontenera z pominięciem tych trzech pól. Jeśli API zwróci błąd walidacji → wybierz jedno z:
- (a) zdjąć `mandatory` w konfiguracji pluginu w GLPI,
- (b) ustalić wartości domyślne wpisywane przy migracji,
- (c) zaakceptować, że projekt powstaje bez wiersza pól dodatkowych (z ostrzeżeniem).

### 11.2 `plan_end_date` — z którego pola?

Issue 19074 ma `due_date: null`, ale posiada custom field **`DATA TERMINO PLANEJADA` = 2026-05-29**. Do decyzji: czy `plan_end_date` bierzemy z `DATA TERMINO PLANEJADA` (z fallbackiem na `due_date`), czy odwrotnie. Domyślnie w kodzie: **`DATA TERMINO PLANEJADA` → fallback `due_date`**, konfigurowalne.

### 11.3 `entities_id` projektu

Nieustalone. GLPI utworzy projekt w **aktywnej encji sesji API**. Do potwierdzenia, czy to właściwa encja, czy trzeba ją wskazywać jawnie (`entities_id` w payloadzie lub parametr przy `initSession`).

### 11.5 `real_end_date` — z którego pola?

Issue 17582 ma `Data Finalização = ""` (puste), ale posiada `closed_on: "2025-06-13T17:36:48Z"`. Do decyzji, czy przy pustym `Data Finalização` używać `closed_on` jako fallbacku.

Uwaga na niespójność danych źródłowych: 17582 ma `closed_on` ustawione, a jednocześnie status `Encerramento` (niezakończony) i `done_ratio: 90`. Sam `closed_on` nie jest więc wiarygodnym wskaźnikiem zakończenia projektu. Domyślnie w kodzie: **używaj wyłącznie `Data Finalização`; `closed_on` tylko odnotuj w raporcie**.

### 11.4 Faturamento — SEKCJA NIEAKTUALNA, patrz 6.5

**Korekta względem wersji 1.0/1.1:** wcześniejsze stwierdzenie, że nie istnieją issues typu Faturamento, było **błędne** — wynikało z zapytania wykonanego tokenem o ograniczonych uprawnieniach. Realnie istnieje **3409** takich issues. Pełne mapowanie kontenera 25 oraz nierozstrzygnięty bloker strukturalny (brak rodzica) opisane są w **sekcji 6.5**.

Wniosek metodyczny: **nigdy nie wyciągaj wniosku „dane nie istnieją" z pustego wyniku**, dopóki nie potwierdzisz, że token ma pełny dostęp do wszystkich projektów.

Decyzja użytkownika: issue typu Faturamento → wiersz w kontenerze **25** (`PluginFieldsProjectfaturamento`, pola `Nº NF`, `Valor Total da NF`, `Vencimento`, `Emissão da NF`, `Competência`, `Projeção de Faturamento`, `Nº Pedido Cliente`, `Conformidade`, `Parcela Proj na NF`, `Observações`).

**Stan faktyczny:** zapytanie `GET /issues.json?tracker_id=15&status_id=*` zwróciło `total_count: 0` — brak issues typu Faturamento.

⚠️ **Zastrzeżenie do weryfikacji:** to zapytanie wykonano tokenem Redmine o **ograniczonych uprawnieniach**. Zero mogło wynikać z braku widoczności projektów, a nie z braku danych. **Powtórz zapytanie tokenem o pełnym dostępie** przed ostatecznym wyłączeniem modułu.

**Rozróżnienie, które trzeba zachować:** pole custom **„Situação Faturamento"** (cf 41, np. wartość `Faturado`) występujące na issues trackera 14 to **pole projektu**, mapowane do kontenera 15 (`plugin_fields_situaofaturamentofielddropdowns_id`). **Nie jest** dowodem na istnienie issues typu Faturamento i nie uruchamia kontenera 25.

**Zalecenie:** nie implementować w v1. Zostawić hook + powyższą listę pól. Gdy dane się pojawią: wiersz przypinamy do **najbliższego projektu-przodka w górę drzewa** (w praktyce do projektu root); jeśli brak przodka-projektu → ostrzeżenie i pominięcie.

Uwaga: istnieje też kontener **26** z bliźniaczym zestawem pól Faturamento (sufiksy `fieldtwo`) — prawdopodobnie dla `ProjectTask`. `DO WERYFIKACJI` przed użyciem którego­kolwiek.

---

## 12. Struktura kodu (sugerowana)

```
config/
  mapping.yml           # mapowanie po NAZWACH pól, z typem transformacji
  status_map.yml        # sekcja 7
  user_map.yml          # sekcja 8
clients/
  redmine.py            # fetch_issue(id), fetch_tree(root_id) — rekurencja + ochrona przed cyklem
  glpi.py               # init_session, kill_session, create_project, create_projecttask,
                        # get_container_row, write_container_row, resolve_dropdown, find_by_rdmfield
store/
  db.py                 # SQLite migration_map (sekcja 9.1)
resolve/
  status.py, users.py, dropdowns.py     # z cache
transform/
  mapper.py             # stosuje mapping.yml, zwraca (payload, lista_pominietych, lista_ostrzezen)
report/
  reporter.py           # sekcja 10
main.py                 # CLI: --issue <id> [--apply] [--db path] [--report path]
```

Domyślnie `main.py --issue 19074` = **dry-run**. Zapis wyłącznie z flagą `--apply` po wyświetleniu raportu i potwierdzeniu.

---

## 13. Zasady twarde dla implementującego

1. **Nie zgaduj ID ani nazw pól.** Wszystko, co nie jest w tym dokumencie, sprawdź wywołaniem API i oznacz w kodzie komentarzem z datą weryfikacji.
2. **Mapuj po nazwach pól custom, nigdy po ich ID.**
3. **Nigdy nie twórz automatycznie pozycji słowników dropdown ani encji.** Brak dopasowania = pominięcie + ostrzeżenie.
4. **Nigdy nie zapisuj do pól z `is_active: 0`.**
5. **Idempotencja przed każdym zapisem** — sprawdzenie `rdmfield` w GLPI oraz `migration_map` lokalnie.
6. **Dry-run jest domyślny.** Żaden zapis bez jawnej flagi i potwierdzenia.
7. **Każde pominięte pole trafia do raportu** — to główne wymaganie funkcjonalne, nie dodatek.
8. **Tokeny wyłącznie ze zmiennych środowiskowych.** Nigdy w logach ani komunikatach błędów.

---

## Załącznik A: przykładowe dane referencyjne (issue 19074)

```
id: 19074 · tracker: 39 "Projeto CEMIG" · projekt Redmine: 35 "Operação CEMIG"
subject: "Merit Triangulo"
status: 15 Novo → GLPI 1
assigned_to: 190 caroline.menezes → GLPI 771
start_date: null · due_date: null · attachments: [] (brak)
children: 19089, 19090, 19091, 19092 — wszystkie tracker 40 "Subtarefa Cemig"

custom_fields obecne:
  Cliente = "CEMIG D"                        → ręcznie, pomijamy
  Data de recebimento da TAP = "2026-01-26"  → dataderecebimentodatapfield
  Número do pedido cliente = "Aguardando Pedido" → nmerodopedidoclientefield
  Pendência = "N/A"                          → dropdown
  Tipo de Pendência = "N/A"                  → dropdown
  Tipo do Projeto = "Gestão de Projeto"      → dropdown
  Andamento do Projeto = ""                  → puste, pomijamy
  Ano de Início = "2026"                     → brak odpowiednika, raport
  Data Finalização = ""                      → puste
  Despesa = "Outro"                          → dropdown (mandatory)
  DATA TERMINO PLANEJADA = "2026-05-29"      → plan_end_date (patrz 11.2)
  NOTAS = ""                                 → comment
  Complexidade = "Baixa"                     → dropdown (mandatory)
  Data de solicitação Baremos = "2026-01-26" → datadesolicitaobaremofield
  Envio do Relatório = ""                    → puste
```

Oczekiwany efekt: 1 projekt GLPI + 4 zadania + wiersz kontenera 15 z `rdmfield = "19074"`.

---

## Załącznik B: dane referencyjne — tracker 14 (issue 17582)

Przypadek przeciwny do 19074: pełny zestaw pól, brak potomków, dużo załączników.

```
id: 17582 · tracker: 14 "Projeto" · projekt Redmine: 7 "Área de Telecom"
subject: "GPG - Refresh Industrial EGP"
status: 25 Encerramento → GLPI 10
assigned_to: 166 vanessa.diniz → GLPI 98
start_date: "2025-10-27" · due_date: "2026-07-24" · done_ratio: 90
closed_on: "2025-06-13T17:36:48Z"  (niespójne ze statusem — patrz 11.5)
children: KLUCZ NIEOBECNY (brak potomków)
relations: [{issue_to_id: 18471, relation_type: "relates"}]  → NIE migrować
attachments: 12 plików, łącznie ~55 MB (największy .eml: 35 MB)

custom_fields (pełny zestaw trackera 14):
  Cliente = "GPG"                              → ręcznie, pomijamy
  Responsável Cliente = "Roberto Gonçalves"    → responsvelclientefieldtwo (mandatory ✔)
  Data de recebimento da TAP = ""              → puste
  Número do pedido cliente = "3500747081"      → nmerodopedidoclientefield
  Gestão = "ITALTEL"                           → dropdown (mandatory ✔)
  Pendência = "ENEL"                           → dropdown
  Tipo de Pendência = "APROVAÇÃO"              → dropdown
  Tipo do Projeto = "Serviço"                  → dropdown
  Andamento do Projeto = "13.07 - Relatório..." → andamentodoprojetofield (długi tekst)
  Valor do Projeto = "66.977,45"               → valordoprojetofield, DOSŁOWNIE (mandatory ✔)
  Ano de Início = "2025"        [cf ID 9]      → brak odpowiednika, raport
  Situação Faturamento = "Faturado"  [cf ID 41] → dropdown
  Data Finalização = ""                        → puste (closed_on istnieje — patrz 11.5)
  Despesa = "CAPEX GDS"                        → dropdown (mandatory ✔) — SPRAWDŹ SŁOWNIK
  Complexidade = "Alta"                        → dropdown (mandatory ✔)
  Data de solicitação Baremos = "2025-02-10"   → datadesolicitaobaremofield
  Solicitação do Cliente = "Anexado"           → dropdown
  Data de entrega da proposta = "2025-10-20"   → datadeentregadapropostafield
  Envio do Baremos = "Anexado"                 → dropdown
  Data de aprovação da proposta = "2025-10-27" → datadeaprovaodapropostafield
  Aprovação do Cliente = "Anexado"             → dropdown
  Envio do Relatório = "Anexado"               → dropdown
  Aprovação para Finalizar = "Pendente"        → dropdown
  Backlog = "Não"                              → backlogfield = 0 (transformacja yesno)
```

Oczekiwany efekt: 1 projekt GLPI, 0 zadań, wiersz kontenera 15 z `rdmfield = "17582"` i kompletem pól.

---

## Załącznik C: inwentarz wartości dropdown do zweryfikowania

Wartości zaobserwowane w realnych issues (trackery 14 i 39). **Przed pierwszym uruchomieniem** pobierz każdy słownik GLPI i sprawdź pokrycie. Każda niepokryta wartość = pole pominięte + ostrzeżenie (a dla pól `mandatory` — potencjalnie odrzucony zapis).

| Pole | Zaobserwowane wartości | Status |
|---|---|---|
| `Despesa` (mandatory) | `CAPEX GDS` ✔ potwierdzone w słowniku · `OPEX GDS` · `OPEX NEGÓCIO` · `CAPEX NEGÓCIO` · `Outro` | sprawdź pozostałe |
| `Complexidade` (mandatory) | `Alta` · `Média` · `Baixa` | sprawdź |
| `Gestão` (mandatory) | `ITALTEL` · `TIVIT` | sprawdź |
| `Tipo do Projeto` | `Serviço` · `Fornecimento` · `Gestão de Projeto` | sprawdź |
| `Pendência` | `ENEL` · `TIVIT` · `N/A` | sprawdź |
| `Tipo de Pendência` | `APROVAÇÃO` · `COMPRAS` · `N/A` | sprawdź |
| `Situação Faturamento` | `Faturado` · `Não Faturado` | sprawdź |
| `Solicitação do Cliente` | `Anexado` | sprawdź |
| `Envio do Baremos` | `Anexado` | sprawdź |
| `Aprovação do Cliente` | `Anexado` · `Pendente` | sprawdź |
| `Aprovação para Finalizar` | `Pendente` | sprawdź |
| `Envio do Relatório` | `Anexado` | sprawdź |

Lista nie jest wyczerpująca — pochodzi z 5 issues. Zalecenie: przed produkcją zrób jednorazowy zrzut **wszystkich unikalnych wartości** tych pól ze wszystkich issues trackera 14 i porównaj ze słownikami GLPI. To jedyny sposób, by nie odkrywać braków po jednym na produkcji.

---

## Załącznik D: przypadki brzegowe potwierdzone na realnych danych

| Przypadek | Issue | Implikacja dla kodu |
|---|---|---|
| Brak klucza `children` | 17582, 20238, 20314 | `issue.get("children", [])` |
| Potomek typu `Compras` (tracker 41) | 18620 → 20154 | potomek = zadanie, tracker bez znaczenia |
| Potomek o **identycznym** `subject` co rodzic | 18620 / 20154 | nazwy zadań mogą duplikować nazwę projektu — dopuszczalne |
| `relations` bez `children` | 17582 → 18471, 18620 → 18655 | relacji **nie migrujemy**, tylko raportujemy |
| Wszystkie daty `null` | 20238, 20314, 19074 | `plan_start_date`/`plan_end_date` puste — nie wysyłaj kluczy |
| Większość pól custom pusta (`""`) | 20238 | puste ≠ brak — raportuj jako „puste w źródle" |
| `done_ratio` niezerowy | 90 (17582), 70 (18620), 20 (20314) | `percent_done` — realnie używane |
| Wiodąca spacja w wartości | 20389 (`" 5593.40"`) | `.strip()` na każdej wartości tekstowej |
| Odwołanie `#20237` w opisie | 20238 | zwykły tekst, nie przetwarzaj |
| Wartość `null` zamiast `""` | 17306 (`Conformidade1: null`) | używaj `if not value`, nie `if value == ""` |
| Relacja zapisana „od nas" | 17582 (`issue_id` = nasze ID) | partner = `issue_to_id` |
| Relacja zapisana „do nas" | 20389 (`issue_to_id` = nasze ID) | partner = `issue_id` |
| Faturamento jako potomek | 17306 → parent 17081 | 42 przypadki — kontener 25, NIE zadanie |
| Faturamento bez powiązania | 20388, 20378 | poza zakresem v1 |
| **Drzewa płaskie** (brak wnuków) | 20154, 19089 — brak `children` | rekurencja zostaje jako zabezpieczenie |
| Zadanie bez `assigned_to` | 19089 (tylko `author`) | brak wartości, nie błąd; nie podstawiaj `author` |
| Zadanie z własnymi polami custom | 20154 (7 pól), 19089 (2 pola) | patrz 9.4 — raport + zrzut do `comment` |
| Zestaw pól custom zależny od trackera | Compras ≠ Subtarefa Cemig | iteruj po `custom_fields`, nie po liście |

**Do sprawdzenia przed pierwszym uruchomieniem:** czy słownik `PluginFieldsDespesafielddropdown` zawiera wartość `CAPEX GDS`. Znana istniejąca wartość to `CAPEX NEGÓCIO`. Brak dopasowania → pole pominięte + ostrzeżenie (pole jest `mandatory` — patrz 11.1).
