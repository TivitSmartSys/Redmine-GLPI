# Prompt startowy dla Claude Code

Wklej to razem z plikiem `INSTRUKCJA_Redmine_do_GLPI.md`.

---

Twoim zadaniem jest zbudowanie aplikacji migrującej projekty z Redmine do GLPI zgodnie z załączoną specyfikacją (`INSTRUKCJA_Redmine_do_GLPI.md`). Specyfikacja jest kompletna i oparta na realnych danych — traktuj ją jako źródło prawdy.

## Zasady nadrzędne

1. **Nie zgaduj.** Pola i sekcje oznaczone `DO WERYFIKACJI` zostaw jako `TODO` z komentarzem w kodzie — nie wymyślaj dla nich rozwiązań. Jeśli czegoś nie ma w specyfikacji, zapytaj mnie, zamiast zakładać.
2. ~~**Nie implementuj CEMIG (tracker 39) ani Subtarefa Cemig (tracker 40).**~~ **NIEAKTUALNE od 2026-08-19 — CEMIG jest w zakresie.** Powodem wykluczenia był wyłącznie bloker z sekcji 11.1: tracker 39 nie ma trzech z pięciu obowiązkowych kolumn kontenera 15, więc `POST /Project` byłby odrzucony. Odczyt na żywo 2026-08-19 pokazał `mandatory: 0` na wszystkich 25 polach kontenera 15 i RDM 19074 zapisał się jako projekt 1292 z tymi trzema kolumnami pustymi. 39 jest trackerem **wyłącznie root**, 40 **wyłącznie zadaniem** (zmierzone: żaden issue trackera 39 nie ma rodzica). Szczegóły w CLAUDE.md, sekcja „CEMIG".
3. ~~**Nie implementuj załączników.**~~ **NIEAKTUALNE od 2026-08-10** — załączniki migrują jako Dokumenty GLPI, notatki od 2026-08-11 jako wiersze Notepad.
4. ~~**Nie implementuj trackera 18 (Atividades)** jako osobnej migracji.~~ **NIEAKTUALNE od 2026-08-06** — 18 i 41 są zadaniami z własnym typem w GLPI. Jako root nadal nie występują.
5. **Dry-run jest domyślny.** Zapis do GLPI wyłącznie po jawnej fladze `--apply` i po wyświetleniu pełnego raportu planu.

## Język

- **Wszystkie komunikaty dla użytkownika, treść raportu, teksty CLI, komunikaty błędów → portugalski (PT-BR).**
- **Nazwy zmiennych, funkcji i komentarze w kodzie → angielski.**
- Dane migrowane (nazwy pól, wartości) przepisuj bez tłumaczenia.

## Kolejność pracy (nie rób wszystkiego naraz)

1. **Najpierw** zbuduj dwa klienty API: Redmine (nagłówek `X-Redmine-API-Key`) i GLPI (`initSession` → `Session-Token` + `App-Token` → `killSession`). Tokeny wyłącznie ze zmiennych środowiskowych / `.env` poza repo — nigdy w kodzie.
2. **Preflight** wg sekcji 9.0: test sesji GLPI i test uprawnień do pluginu Fields (`ERROR_RIGHT_MISSING` → przerwij z czytelnym komunikatem PT-BR).
3. Zaimplementuj **jeden pełny przypadek end-to-end w trybie dry-run na RDM 20238** (najprostszy z listy testowej — pola prawie puste, brak dzieci, brak relacji). Pokaż mi wynik, zanim pójdziesz dalej.
4. Dopiero po akceptacji rozbuduj: resolvery (status, użytkownik), zapis kontenera 15, logikę drzewa z regułą „dziecko spoza zakresu", ścieżkę Faturamento (kontener 25), generator raportu.
5. Na końcu przetestuj na pełnej liście 11 RDM z sekcji 1a.

## Kluczowe pułapki (są w specyfikacji, ale podkreślam)

- Mapuj pola custom **po nazwie**, nigdy po ID.
- Klucz `children` bywa nieobecny — używaj `.get("children", [])`.
- Kierunek relacji zmienny — partner to pole, które NIE jest ID bieżącego issue.
- Wartości liczbowe (`Valor`) przepisuj dosłownie + `.strip()`, nie parsuj.
- Pola `is_active: 0` — nigdy nie zapisuj.
- Deduplikacja przez pole `rdmfield` w GLPI + lokalny SQLite.

## Stos

Python 3 + `requests`. CLI: `main.py --issue <id> [--apply]`. SQLite na mapę migracji.

Zacznij od punktu 1 i 2. Pokaż mi strukturę projektu i oba klienty API, zanim przejdziesz do logiki migracji.
