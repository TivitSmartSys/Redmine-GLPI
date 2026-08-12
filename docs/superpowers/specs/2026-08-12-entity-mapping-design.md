# Encja projektu z pola `Cliente` — design

Data: 2026-08-12
Status: zatwierdzony (design), do zaplanowania

## Problem

Dziś `entities_id` **nie jest wysyłane** i GLPI zakłada każdy projekt w aktywnej
encji sesji API — 75, `TIVIT > SMART SYSTEMS` (`config/settings.py`, sekcja
`OBSERVED_SESSION_ENTITY_ID`; spec 11.3 zostawiała to jako `DO DECYZJI`).
Wszystkie zmigrowane dotąd projekty (1265–1283) siedzą w tej jednej encji.

Docelowo projekt ma trafiać do encji klienta. Odwzorowanie klient → encja
dostarczył manager w arkuszu *Mapeamento ClienteRedmine EntidadeGLPI -1.pdf*:
48 nazw klientów, 37 encji docelowych, 5 nazw oznaczonych „Nao sera migrado".

## Ustalenia z weryfikacji na żywo (2026-08-12, GLPI 11.0.6)

Zmierzone, nie założone. Sonda tylko do odczytu, a następnie jeden projekt-śmieć
utworzony w encji 4 i wyczyszczony (projekt 1284, wiersz kontenera 942, zadanie
14139 — wszystkie purge'owane, marker po sprzątaniu daje 0 trafień).

- Profil API to 4 Super-Admin, `project` 1151, `projecttask` 1151,
  `document` 255, `entity` 3327. **Nowy klucz API nie jest potrzebny.**
- `getMyEntities` (rekurencyjnie) zwraca **98 encji**, id 0–109, i wszystkie
  **37 encji docelowych z arkusza są osiągalne**.
- Domyślna sesja jest jednak zawężona: aktywna encja 75 rekurencyjnie =
  **widoczne tylko 75, 76, 77**.
- `POST /changeActiveEntities {"entities_id": 0, "is_recursive": true}` jest
  **akceptowane** i podnosi widoczność do 98 encji.
- `POST /Project` z `entities_id: 4` **ląduje w encji 4** — klucz jest
  respektowany, nie ignorowany po cichu.
- Wiersz kontenera 15 i `ProjectTask` **dziedziczą encję projektu** (obie
  odczytane jako `entities_id: 4`). Nie trzeba im nic wysyłać.
- Pola `mandatory` kontenera 15 są dziś 0, więc `POST /Project` przeszedł z samą
  nazwą i encją. To potwierdza notatkę w `settings.py`, nie zmienia jej.
- **Dedup jest scope'owany encją.** Marker widoczny root-rekurencyjnie (1
  trafienie) był **niewidoczny z sesji w encji 75 (0 trafień)**. Bez przełączenia
  sesji ta zmiana produkowałaby duplikat każdego projektu spoza encji 75 —
  w tym każdego już zmigrowanego, bo te siedzą w 75, a szukane byłyby w encji
  klienta.

## Decyzje

1. **Encja to własność projektu-korzenia.** Ustalana raz, z pola `Cliente`
   issue'a-korzenia. Zadania, kontenery, dokumenty i notatki nie dostają
   `entities_id` — dziedziczą. `Cliente` na Faturamento znalezionym po relacji
   jest ignorowany: zadanie wisi pod korzeniem i dzieli jego encję.
2. **Rozwiązywanie po nazwie, nie po ID** (wariant A z brainstormingu). Arkusz
   podaje ID instancji **testowej** (kolumny „GLPI TESTE" / „ID GLPI TESTE").
   Na produkcji te same nazwy będą miały inne ID, a zaszyte na sztywno „4"
   wpisałoby ENEL SP w losową encję cicho i bez błędu. Mapa trzyma więc
   `completename`, a ID pochodzi z `GET /Entity`. ID z arkusza zostaje jako
   `id_teste` — wyłącznie kontrola krzyżowa.
3. **Brak dopasowania → encja 75 + linia w raporcie** (opcja A), nigdy odmowa
   projektu i nigdy tworzenie encji. Zgodne z regułą 2 z `CLAUDE.md`.
4. **`entities_id` wysyłane zawsze**, także w fallbacku. Po przełączeniu sesji
   na root-rekurencyjną „encja sesji" to **0**, więc pominięcie klucza wrzuciłoby
   projekt do korzenia TIVIT zamiast do 75. Fallback musi być jawny.

## Komponenty

### `config/entity_map.yml` (nowy)

```yaml
entities:
  - completename: "TIVIT > GRUPO ENEL > BRASIL > ENEL SP"
    id_teste: 4
    clients: ["ENEL SP"]
  - completename: "TIVIT > GRUPO ENEL > BRASIL > EGP BR"
    id_teste: 6
    clients: ["CDSA", "EGP", "GPG", "VGR"]
  # ...
nao_sera_migrado: ["CIEN", "ENDESA", "ENDESA ESPANHA", "MS OPERACÕES", "OUTRO"]
```

Zawartość — pełne odwzorowanie z arkusza, potwierdzone z autorem 2026-08-12:

| id_teste | encja | klienci z Redmine |
|---|---|---|
| 3 | TIVIT > GRUPO ENEL > BRASIL > ENEL RJ | ENEL RJ; ENEL RJ – Cabeamento |
| 4 | TIVIT > GRUPO ENEL > BRASIL > ENEL SP | ENEL SP |
| 5 | TIVIT > GRUPO ENEL > BRASIL > ENEL CE | ENEL CE |
| 6 | TIVIT > GRUPO ENEL > BRASIL > EGP BR | CDSA; EGP; GPG; VGR |
| 7 | TIVIT > GRUPO ENEL > BRASIL > ENEL X | ENEL X |
| 27 | TIVIT > GRUPO ENERGISA > BRASIL > ENERGISA PARAIBA | ENERGISA PB |
| 32 | TIVIT > GRUPO NEOENERGIA > BRASIL | ENERGISA |
| 33 | TIVIT > GRUPO NEOENERGIA > BRASIL > NEOENERGIA COELBA | COELBA |
| 36 | TIVIT > GRUPO NEOENERGIA > BRASIL > NEOENERGIA ELEKTRO | ELEKTRO |
| 53 | TIVIT > VALE > BRASIL | VALE |
| 65 | TIVIT > HYDRO > BRASIL > PARAGOMINAS | PARAGOMINAS |
| 66 | TIVIT > HYDRO > BRASIL > ALUNORTE | ALUNORTE |
| 67 | TIVIT > HYDRO > BRASIL > ALBRAS | ALBRAS |
| 70 | TIVIT > GRUPO EQUATORIAL > BRASIL > CELG | ENEL GO; EQUATORIAL GO; EQUATORIAL GO – Automação |
| 71 | TIVIT > GRUPO EQUATORIAL > BRASIL > RIO GRANDE SUL | EQUATORIAL SUL |
| 88 | TIVIT > CEMIG > BRASIL > CEMIG D | CEMIG D |
| 89 | TIVIT > CEMIG > BRASIL > CEMIG G CAMARCOS | CEMIG G CAMARCOS |
| 90 | TIVIT > CEMIG > BRASIL > CEMIG G ITUTINGA | CEMIG G ITUTINGA |
| 91 | TIVIT > CEMIG > BRASIL > CEMIG G LESTE | CEMIG G LESTE |
| 92 | TIVIT > CEMIG > BRASIL > CEMIG G OESTE | CEMIG G OESTE |
| 93 | TIVIT > CEMIG > BRASIL > CEMIG G POÇO FUNDO | CEMIG G POÇO FUNDO |
| 94 | TIVIT > CEMIG > BRASIL > CEMIG G SUL | CEMIG G SUL |
| 95 | TIVIT > CEMIG > BRASIL > CEMIG GT | CEMIG GT |
| 96 | TIVIT > CEMIG > BRASIL > CEMIG PCH | CEMIG PCH |
| 97 | TIVIT > CEMIG > BRASIL > CEMIG SIM | CEMIG SIM |
| 98 | TIVIT > CEMIG > BRASIL > CEMIG TRADING | CEMIG TRADING |
| 99 | TIVIT > CEMIG > BRASIL > CEMIG_CENTRAL EÓLICA PARAJURU | CEMIG_CENTRAL EÓLICA PARAJURU |
| 100 | TIVIT > CEMIG > BRASIL > CEMIG_CENTRAL EOLICA VOLTA DO RIO | CEMIG_CENTRAL EOLICA VOLTA DO RIO |
| 101 | TIVIT > CEMIG > BRASIL > CEMIG_COMPANHIA TRANSMISSÃO CENTROOESTE DE MINAS | ta sama nazwa |
| 102 | TIVIT > CEMIG > BRASIL > CEMIG_EMPRESA DE SERVIÇOS DE COMERCIALIZAÇÃO DE ENERGIA ELÉTRICA | ta sama nazwa |
| 103 | TIVIT > CEMIG > BRASIL > CEMIG_HORIZONTES ENERGIA | ta sama nazwa |
| 104 | TIVIT > CEMIG > BRASIL > CEMIG_ROSAL ENERGIA | ta sama nazwa |
| 105 | TIVIT > CEMIG > BRASIL > CEMIG_SÁ CARVALHO | ta sama nazwa |
| 106 | TIVIT > CEMIG > BRASIL > CEMIG_SETE LAGOAS TRANSMISSORA DE ENERGIA | ta sama nazwa |
| 107 | TIVIT > CEMIG > BRASIL > CEMIG_UFV BOA ESPERANÇA | ta sama nazwa |
| 108 | TIVIT > CEMIG > BRASIL > CEMIG_UFV TRÊS MARIAS | ta sama nazwa |
| 109 | TIVIT > HYDRO > BRASIL > HCO BELEM | HCO BELEM |

37 encji, 43 nazwy klientów. Plus 5 nazw `nao_sera_migrado` = **48**, czyli
dokładnie tyle, ile liczy `PluginFieldsClientefielddropdown` (zweryfikowane
2026-08-03, `config/mapping.yml`). Ta zgodność jest głównym dowodem, że arkusz
został odczytany poprawnie mimo rozjechanego układu kolumn w PDF.

Dwa wiersze odczytane z pozycji komórek i **potwierdzone z autorem arkusza**:
`ENERGISA` → 32 (komórka leży w wierszu `GRUPO NEOENERGIA > BRASIL`) oraz
`VALE` → 53 (komórka scalona pionowo przez wiersze 53–59; brany jest górny).

### `resolve/entities.py` (nowy)

Resolver-brat istniejących (`status`, `user`, `dropdown`), z tą samą umową:
**zwraca `None` przy braku trafienia, nigdy nie zgaduje i nigdy nie tworzy
encji**.

- w preflight jeden `GET /Entity` **z jawnym `range`** — encji jest 98, a bez
  tego GLPI odda pierwsze 15 i mapa cicho straciłaby dwie trzecie wpisów (ta
  sama pułapka, co przy `document_links` i `notepad_rows`);
- cache: `completename` po `.strip()` + casefold → `id`;
- `resolve(client_value) -> int | None` po nazwie klienta, przez `entity_map`.

### `transform/mapper.py`

`entities_id` staje się zwykłym polem projektu z własnym `FieldRecord` — bez
nowego enuma i bez nowej sekcji raportu, więc arytmetyka sekcji 7 domyka się
sama:

| Sytuacja | `entities_id` | `Outcome` |
|---|---|---|
| `Cliente` w mapie, nazwa znaleziona w GLPI | rozwiązane ID | `WRITTEN` |
| `Cliente` pusty | 75 | `EMPTY_SOURCE` |
| `Cliente` spoza 48 (COELCE, AMPLA, CGTF…) | 75 | `NO_COUNTERPART` |
| `Cliente` z listy „Nao sera migrado" | 75 | `NEVER_WRITE` |
| `Cliente` w mapie, nazwy nie ma w GLPI | 75 | `UNRESOLVED` + ostrzeżenie |

`NO_COUNTERPART` obejmuje m.in. COELCE (481 issues) i AMPLA (456) — nazwy, które
świadomie nie mają odpowiednika, zgodnie z zamkniętą decyzją z 2026-08-03.

### `main.py` — preflight

Po `initSession`, przed dedupem:

1. `POST /changeActiveEntities {"entities_id": 0, "is_recursive": true}`.
   **Odmowa = twardy stop**, na równi z `ERROR_RIGHT_MISSING`: bez tego dedup
   widzi jedną gałąź i migracja zaczyna produkować duplikaty — awaria cichsza i
   groźniejsza niż przerwanie.
2. Wczytanie słownika encji do cache.
3. Kontrola krzyżowa `id_teste` vs rozwiązane ID; rozbieżność → **ostrzeżenie**
   w raporcie. To sygnał „jesteś na innej instancji niż arkusz".

### `report/`

Encja w nagłówku projektu (sekcja 2): nazwa, ID i klient, z którego wynika.
Nowe komunikaty w `report/messages.py`, PT-BR.

### `reset_migration.py`

To samo przełączenie sesji. Bez niego skrypt przestanie znajdować i kasować
cokolwiek spoza encji 75.

## Testy

Do istniejącego zestawu pytest:

- pięć ścieżek z tabeli `Outcome` powyżej;
- dopasowanie case-insensitive po `.strip()`;
- fallback na 75 w każdej ścieżce innej niż `WRITTEN`;
- `None`, gdy encji nie ma w GLPI — i że projekt mimo to powstaje;
- integralność YAML-a: suma `clients` + `nao_sera_migrado` to dokładnie 48
  nazw, bez duplikatów i bez nazwy przypisanej do dwóch encji;
- `entities_id` obecne w payloadzie **zawsze**.

## Ryzyka i rzeczy do sprawdzenia przy implementacji

1. **Format `completename`.** Zakładam `„A > B > C"`. Jeżeli `GET /Entity`
   zwraca inny separator, normalizacja idzie do resolvera. Do sprawdzenia w
   pierwszym kroku implementacji.
2. **Dokładna pisownia 48 nazw klientów.** Tabela wyżej jest przepisana z PDF-a,
   w którym występują półpauzy („ENEL RJ – Cabeamento", „EQUATORIAL GO –
   Automação"). Przed napisaniem YAML-a: zrzucić rzeczywiste wartości z
   `PluginFieldsClientefielddropdown` oraz z pola `Cliente` w Redmine i
   porównać z tą tabelą. Dopasowanie jest po casefold + strip, więc rodzaj
   myślnika ma znaczenie.
3. **Projekty już zmigrowane zostają w encji 75.** Ta zmiana ich nie przenosi.
   Przeniesienie istniejących projektów to osobna decyzja i osobne zadanie.
4. **Encja 0 jako aktywna encja sesji.** Preflight zmienia stan sesji API dla
   całego przebiegu. Wpływ na inne zapisy (dokumenty, Notepad) jest żaden —
   wszystkie wiszą pod projektem lub zadaniem i dziedziczą encję — ale to
   założenie warto potwierdzić w pierwszym pełnym `--apply` po zmianie.
