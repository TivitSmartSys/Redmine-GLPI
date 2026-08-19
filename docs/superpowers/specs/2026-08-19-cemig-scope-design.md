# CEMIG w zakresie migracji — trackery 39 i 40

Data: 2026-08-19
Status: zaimplementowany i zweryfikowany na żywo (RDM 19074 → projekt GLPI 1292)

## Problem

CEMIG był poza zakresem od 2026-07-24 (`PROMPT_dla_Claude_Code.md` reguła 2,
spec sekcja 1a: „całkowicie poza zakresem. Nie implementować, nie testować").

Powód był **jeden i konkretny**, zapisany w spec 11.1: kontener 15 ma pięć
kolumn oznaczonych `mandatory`, a tracker 39 „Projeto CEMIG" nie ma źródła dla
trzech z nich — `Valor do Projeto`, `Gestão` i `Responsável Cliente` nie
istnieją na tym trackerze w ogóle. Kontener 15 jest typu „dom", więc jego
wartości jadą wewnątrz `POST /Project` i plugin waliduje je w hooku dodawania
projektu: brak wartości **odrzuca cały projekt** błędem
`ERROR_GLPI_ADD "Alguns campos obrigatórios estão vazios"` (zaobserwowane na
żywo 2026-08-04).

## Ustalenie, które odblokowało zmianę

`GET /PluginFieldsField`, 2026-08-19: **wszystkie 25 pól kontenera 15 mają
`mandatory: 0`**. To ten sam wynik co sweep z 2026-08-07 opisany w CLAUDE.md —
flaga została zdjęta po stronie GLPI i nikt nie wrócił do trackera 39.

Potwierdzone zapisem, nie tylko odczytem: RDM 19074 utworzył projekt 1292 z
`valordoprojetofield = None`, `responsvelclientefieldtwo = None` i
`plugin_fields_gestofielddropdowns_id = 0`.

**To jest cała zależność tej funkcji.** Ponowne oflagowanie którejkolwiek z
pięciu kolumn wyłącza tracker 39 z migracji, a naprawa jest po stronie GLPI —
migrator nie wymyśli wartości, dla której nie ma źródła.

## Zmierzone dane (na żywo, 2026-08-19)

| | |
|---|---|
| tracker 39 | 6 issues, **wszystkie bez rodzica**, zero relacji → zero Faturamento obiema ścieżkami |
| tracker 40 | 49 issues: 35 pod tymi 6 rootami, 8 bez rodzica, 6 pod issue 18575 (**HTTP 403** dla tokena API) |
| statusy 39 | 23 Planejamento (4), 15 Novo (2) — już w `status_map.yml` |
| statusy 40 | 15 (38), 16 (5), 14 (4), 23 (2) — już w `status_map.yml` |
| `Cliente` | `CEMIG D` (4), `CEMIG GT` (2) — już w `entity_map.yml` |
| pola 40 | wyłącznie `Pendência`, `Tipo de Pendência` → zrzut do `comment` (spec 9.4) |
| objętość | 1 załącznik, 6 notatek tekstowych, najdłuższa wartość pola 17 znaków |

Nieosiągalnych 14 issues trackera 40 zostaje niezmigrowanych — ta sama zamknięta
decyzja co przy 16 osieroconych Compras. Issue **18575** to projekt niewidoczny
dla tokena API, a nie brak danych; warto to zgłosić właścicielowi uprawnień
Redmine.

## Decyzje

1. **Tracker 39 wyłącznie jako root, 40 wyłącznie jako zadanie.** Asymetria jest
   zmierzona, nie założona: żaden issue trackera 39 nie ma rodzica, więc jako
   dziecko nie występuje. Wpisanie obu trackerów do obu zbiorów byłoby
   zgadywaniem — i jest to najbardziej naturalna błędna implementacja „dodaj
   CEMIG do zakresu", więc `tests/test_scope_cemig.py` ją przypina.

2. **Tracker 40 dostaje własny ProjectTaskType**, nie pożycza „Atividade" (4).
   Wiersz 6 `Subtarefa Cemig` założony ręcznie w GLPI; 35 podzadań pozostaje
   odróżnialnych od 1259 issues trackera 18.

3. **Twarda walidacja trackera roota.** `IN_SCOPE_ROOT_TRACKERS` był do tej pory
   czytany wyłącznie przez web UI. `transform.tree._classify` zwraca PROJECT dla
   roota **zanim** spojrzy na tracker, więc `--issue` przyjmował dowolny tracker
   i robił z niego projekt — zakres pilnował wyłącznie dzieci.
   `root_tracker_rejection` w `main.py` jeździ na istniejącym GET-cie z kroku
   „dostępność Redmine" i jest **ostatnim** krokiem preflightu: odmowa zakresu
   nie może przesłonić zepsutej sesji ani brakującego uprawnienia.

4. **Wartość słownika dodana ręcznie, nie przez migrator** (reguła 2). `Tipo do
   Projeto` = „Gestão de Projeto" nie istniało w
   `PluginFieldsTipodoprojetofielddropdown` — dodane jako wpis 30. Uwaga:
   istniejący wpis 25 to `Engenharia + Gestão de Projeto`, czyli inna wartość,
   której porównanie dokładne słusznie nie dopasowuje.

5. **Dwa miejsca twierdzące „to zablokuje zapis" poprawione.**
   `MANDATORY_CONTAINER15_COLUMNS` jest listą-strażnikiem wpisaną na sztywno, a
   nie odczytem flag na żywo, i do czasu CEMIG żadne z tych miejsc nigdy się nie
   odpaliło w praktyce (tracker 14 wypełnia wszystkie pięć kolumn). Na sześciu
   projektach CEMIG oba twierdziły nieprawdę — i to w kierunku, który
   powstrzymuje człowieka przed uruchomieniem `--apply`:

   - `REPORT_SECTION_5_BLOCKING` (sekcja 5 raportu) mówiło „a gravação do
     PROJETO é recusada sem estes dados" → przeformułowane warunkowo, z datą
     ostatniego pomiaru flag. Kolumny nadal są wymieniane z nazwy;
   - `UI_WARN_MANDATORY` (czerwony baner na górze web UI) mówił „{count}
     campo(s) obrigatório(s) sem dados — bloqueia a gravação" → **usunięty**.
     Sama liczba zostaje w payloadzie `summary`, a sekcja 5 raportu i tak
     wymienia kolumny. Gdyby GLPI kiedykolwiek oflagował je ponownie, baner
     wolno przywrócić **wyłącznie** czytając flagę na żywo, a nie listę-strażnik.

## Czego NIE trzeba było zmieniać

Mapowanie po **nazwach** pól custom (reguła nadrzędna spec 4) sprawiło, że pola
trackera 39 trafiły na swoje miejsce bez ani jednego nowego wpisu w
`mapping.yml`, a `Pendência` / `Tipo de Pendência` miały wpisy i pełne pokrycie
słowników już wcześniej. `transform/tree.py` nie wymagał zmian — dokładnie tak
samo jak przy wejściu trackera 41 w zakres, cała zmiana to przynależność do
`IN_SCOPE_TASK_TRACKERS`.

## Zmiany w kodzie

| Plik | Zmiana |
|---|---|
| `config/settings.py` | `TRACKER_PROJETO_CEMIG`, `TRACKER_SUBTAREFA_CEMIG`, oba zbiory zakresu, `PROJECTTASKTYPE_SUBTAREFA_CEMIG = 6` |
| `main.py` | `root_tracker_rejection()` + wywołanie na końcu `run_preflight` |
| `report/messages.py` | `PREFLIGHT_ROOT_TRACKER_OUT_OF_SCOPE`, przeformułowane `REPORT_SECTION_5_BLOCKING` |
| `config/user_map.yml` | `192 → 306` (`gabriel.figueiredo`, assignee 3 z 6 projektów) |
| `tests/test_scope_cemig.py` | 8 testów: dyspozycje drzewa, asymetria 39/40, typ zadania, zrzut do komentarza, walidacja roota |

Po stronie GLPI, ręcznie: `ProjectTaskType` 6 `Subtarefa Cemig`,
`PluginFieldsTipodoprojetofielddropdown` 30 `Gestão de Projeto`.

## Weryfikacja wykonana

- `python -m pytest tests -q` → 129 passed
- `python audit_coverage.py --tracker 39` i `--tracker 40` → pełne pokrycie
- dry-run 19074: 4 zadania (nie pominięte dzieci), encja 88 CEMIG D,
  `Tipo do Projeto` → 30, `projecttasktypes_id` → 6, **0 nierozwiązanych**
- `--apply` 19074 → projekt **1292**, wiersz kontenera **950**, zadania
  14168–14171; odczyt zwrotny potwierdza encję 88 na projekcie, wierszu
  kontenera i wszystkich czterech zadaniach, typ 6 na każdym zadaniu, zrzut
  `Pendência` / `Tipo de Pendência` w `comment`
- ponowne uruchomienie 19074 → dedup odmawia (projekt 1292)
- `--issue 20389` (Faturamento) i `--issue 19089` (Subtarefa Cemig) → preflight
  odmawia, kod wyjścia 1; `--issue 18729` (CEMIG) → kod wyjścia 0

## Zostaje otwarte

Pięć pozostałych projektów CEMIG (18729, 18721, 18713, 18701, 18586) nie jest
jeszcze zmigrowanych — 19074 był testem końca-do-końca.
