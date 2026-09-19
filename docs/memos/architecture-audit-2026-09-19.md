# Audyt architektoniczny — 19 września 2026

Audyt całego projektu przeprowadzony jedenastoma równoległymi przeglądami
(katalog wzorców, zdrowie modelu domeny, obiekty wartości, archetypy,
współbieżność i trwałość, wzorce behawioralne, pokrycie JTBD, spójność
przekrojowa, zapytania międzykontekstowe, struktura pakietów, dobre praktyki
branżowe). Ustalenia oznaczone jako **zweryfikowane** zostały sprawdzone
bezpośrednio — przez uruchomienie polecenia albo przeczytanie kodu — a nie
przejęte od agenta przeglądowego na słowo.

Stan repozytorium: branch `ksef-mcp-2`, wersja 0.3.3.dev0.

---

## 1. Rzecz najważniejsza

Projekt **nie wymaga ratunku**. Wymaga zamknięcia czterech dziur, z których
trzy da się zamknąć w jeden dzień.

Punkt odniesienia, zweryfikowany uruchomieniem w trakcie audytu:

| Miara | Wynik |
|---|---|
| `uv run pytest` | 959 testów przechodzi |
| Pokrycie | 100% linii i gałęzi (2724 instrukcje, 356 gałęzi, 0 nietrafionych) |
| Czas przebiegu | 5,53 s |
| `uv run pytest bin` | 91 testów przechodzi |
| `uv run ruff check .` | czysto |
| Cykle w grafie importów | **brak** |

Nienegocjowalne zasady bezpieczeństwa z `CLAUDE.md` są dotrzymane i sprawdziłem
to osobiście: domyślne środowisko to `TEST` i jest wyliczeniem, nie napisem
(`config.py:44`); podproces Node idzie listą argumentów, bez `shell=True`,
z limitem czasu (`pdf.py:200`); treść faktury jest wycinana z komunikatów
odmowy generatora (`pdf.py:208`, `without_quotations`); numer KSeF jest
walidowany przed sklejeniem ścieżki (`pdf.py:313`). Kryptografia paczki
eksportu, uprawnienia plików ustawiane na deskryptorze przed zapisem i cykl
życia klucza AES są zrobione starannie i z uzasadnieniem w komentarzach.

To trzeba powiedzieć przed listą braków, bo inaczej lista czyta się fałszywie.

---

## 2. Cztery ścieżki do jednego skutku — najwyższy priorytet

Cztery niezależne mechanizmy prowadzą do tego samego: **klient wygląda dla
Ministerstwa Finansów na obchodzącego limity**. Ministerstwo rejestruje
przekroczenia, analizuje wzorce i blokuje podmiot lub zakres adresów IP,
a czas blokady rośnie przy powtórzeniach. Szkodę robi tu sama wytrwałość
klienta, nie pojedyncza operacja.

Znalazły to niezależnie trzy fazy (katalog wzorców, archetypy, współbieżność).
Wszystkie cztery ścieżki **zweryfikowałem osobiście w kodzie**.

### 2.1. Rekord `FAILED` blokuje kierunek na stałe — BŁĄD, nakład S

```python
# sync_store.py:109 — zwraca PIERWSZY rekord dla kierunku
def pending_for(self, direction):
    return next((e for e in self.pending if e.direction == direction), None)


# sync_store.py:115 — dopisuje nowy na KONIEC
def with_pending(self, export):
    return replace(self, pending=(*self.without_pending(reference=export.reference), export))
```

`without_export` (`sync_store.py:118`) jest wołane z dokładnie jednego miejsca —
`package.py:202`, czyli wyłącznie po udanej archiwizacji. Rekord `FAILED` nie
jest usuwany nigdy. `_advance_one` (`synchronisation.py:254-264`) rozgałęzia się
tylko na `RUNNING` i `READY`, więc `FAILED` spada do `_start`, który zamawia
nową paczkę — dopisywaną **za** rekordem `FAILED`.

Skutek: po jednym odrzuconym eksporcie ten typ podmiotu zamawia paczkę co
piętnaście minut i nie odbiera żadnej. W nieskończoność.

Dowodem jest istniejący test — `tests/test_synchronisation.py:670`:

```python
assert [export.state for export in store.load().pending] == [
    ExportState.FAILED,
    ExportState.FAILED,  # dwa rekordy po JEDNYM przebiegu
]
```

Test utrwala narastanie jako zachowanie oczekiwane. Poprawka wymaga jego
przepisania. To odpowiedź na pytanie, dlaczego stuprocentowe pokrycie tego nie
złapało: pokrycie mierzy wykonanie linii, nie trafność asercji.

Zamysł był słuszny — komentarz przy `FAILED` mówi wprost, że referencja, której
nikt później nie wyjaśni, jest gorsza niż oznaczona jako porażka. Chodziło
o rekord martwej litery. Błąd polega na tym, że martwa litera leży w **kolejce
roboczej**, a nie w dzienniku.

**Poprawka.** Minimalna: `pending_for` wybiera rekord nieukończony, nie pierwszy
z brzegu. Właściwa: stany końcowe wychodzą z `SyncState.pending` do dziennika.
Kolejka trzyma otwarte, dziennik zamknięte.

### 2.2. Prawdziwe 429 wywraca całe wypisanie — BŁĄD, nakład S

```python
# ksef_port/errors.py
class KsefRequestRejected(KsefPortError): ...  # odmowa NASZEGO licznika


class KsefRateLimited(KsefPortError): ...  # RODZEŃSTWO, nie podklasa
```

Pętla po kierunkach (`listing.py:302`, identycznie `review.py:438`) łapie
wyłącznie `KsefRequestRejected`. Komentarz tuż nad nią deklaruje, że jeden
wyczerpany typ podmiotu nie może zabrać pozostałych trzech — i to prawda dla
odmowy lokalnej, a nieprawda dla prawdziwego 429, które przechodzi przez
`except` i wylatuje z `run()`.

Droższa awaria jest obsłużona gorzej niż tańsza. Przy 429 na drugim z czterech
typów przepada odpowiedź pierwszego — już opłacona z dwudziestu zapytań na
godzinę.

**Poprawka.** Rozszerzyć `except` o `KsefRateLimited` i rozstrzygnąć, czy
komunikat ma nieść `retry_after`. Jedna linia i jeden test.

### 2.3. Licznik budżetu godzinowego żyje jedno wywołanie — nakład M

`QueryBudget` (`ksef_port/budget.py:25`) trzyma `spent` w polu z
`default_factory=dict` i powstaje od zera w czterech miejscach
(`synchronisation.py:227`, `listing.py:291`, `review.py:427`,
`statement.py:439`), zawsze wewnątrz `with port.session(...)`. Nic nie wczytuje
tego stanu z dysku ani go nie zapisuje.

Okno **godzinowe** ma cykl życia **jednego wywołania narzędzia**. Agent wołający
trzy narzędzia w ciągu minuty wydaje trzy razy po pełnym przydziale, a licznik
ani razu nie odmówi. Dodatkowo `remaining()` czyta wyłącznie `per_hour` —
`per_second` i `per_minute` są odczytywane z KSeF, przechowywane i nigdy
niesprawdzane.

Docstring tej samej klasy precyzyjnie opisuje, dlaczego to groźne. `SyncStore`
i `PeriodCache` dostały trwałość dokładnie z tego powodu (D-021); licznik nie
dostał nic.

### 2.4. Stan zapisywany po całej pętli — ginie klucz AES przyjętej paczki — nakład S

```python
# synchronisation.py:223-238
with self.port.session(nip=nip, token=token) as session:
    for direction in SYNCHRONISED_DIRECTIONS:
        state, report = self._advance_one(...)  # cztery iteracje, wszystko w pamięci
path = self.store.save(state)  # :237 — ZA blokiem with
```

Wyjątek w dowolnym `_advance_one` wychodzi z `run()` i zapis nie następuje. Giną
naraz `attempted_at` dla typów już obsłużonych — więc następny przebieg zamawia
eksporty bez piętnastominutowego progu, sam budując wzorzec ponawiania — oraz
`PendingExport` z kluczem AES do eksportu, który KSeF **już przyjął**. Ta paczka
nie będzie możliwa do odszyfrowania nigdy (D-033).

Jest to sprzeczne z własnym komentarzem `_start`, który deklaruje, że próba jest
zapisywana przed paczką.

**Poprawka.** Zapis po każdym typie podmiotu; stan jest niezmienny, zapis idzie
przez temp→rename, cztery zapisy na przebieg nic nie kosztują.

### Dlaczego te cztery idą razem

To jeden mechanizm w czterech przebraniach: rzecz zaprojektowana poprawnie, ale
niepodłączona do niczego, co przeżywa proces. Naprawiane jako cztery osobne
zgłoszenia rozjadą się dokładnie tak, jak dziś rozjechały się `SINGLE_ATTEMPT`
w adapterze i nieużywany `RetryPolicy`.

---

## 3. Brak wyłączności zapisu

W całym `src/` nie ma **ani jednej blokady** — żadnego `fcntl.flock`, `O_EXCL`
ani pliku blokady. Tymczasem atomowość pojedynczego zapisu jest przemyślana
lepiej niż w większości projektów z bazą danych: wzorzec temp → `fsync` →
`os.replace` występuje we wszystkich magazynach, z uzasadnieniem w docstringach.

Słabym miejscem nie jest więc atomowość, tylko brak serializacji między
pisarzami:

- **Zgubiona aktualizacja.** Każdy magazyn robi `load() → modyfikacja → save()`
  na całym dokumencie. Dwa klienty MCP na ten sam podmiot (Claude Desktop
  i Claude Code to zwykła konfiguracja) dają dwa serwery pod `uvx`. Docstring
  `audit.py:39` **wprost przewiduje** dwa procesy dla jednego podmiotu i dla
  dziennika rozwiązuje to przez `O_APPEND` — pozostałe cztery pliki nie dostały
  niczego.
- **Stała nazwa pliku przejściowego.** `staging = self.path.with_suffix(".tmp")`
  (`sync_store.py:280`, `period_cache.py:316`, `archive.py:475`,
  `review.py:221`) jest identyczna dla każdego procesu. Dwa równoległe `save()`
  otwierają ten sam deskryptor z `O_TRUNC` i przeplatają zapisy — obcięty JSON
  powstaje **przed** zamianą, więc atomowość `rename(2)` nie chroni.
- **Indeks deduplikacji sprawdza unikalność, nie wymusza jej.**
  `archive.py:422-441` ładuje indeks raz i zapisuje całość po pętli. Unikalność
  samego pliku faktury jest wymuszona strukturalnie (`_written` odmawia
  nadpisania), unikalność wpisu indeksu — nie.
- **Narzędzia MCP są synchroniczne**, więc SDK wykonuje je w puli wątków. Każdy
  z tych wyścigów zachodzi zatem **wewnątrz jednego procesu**, bez drugiego
  serwera.

Dobra wiadomość: nie ma dzielonego stanu w pamięci, więc blokada plikowa
pokrywa oba przypadki naraz i jest jedynym mechanizmem działającym między
procesami.

---

## 4. Tożsamość podmiotu — jedno ustalenie, sześć faz

Sześć niezależnych przeglądów wskazało to samo, każdy ze swojej strony. To
najsilniej potwierdzone ustalenie całego audytu.

**`SubjectContext` istnieje, zawiera jedyną w projekcie walidację NIP-u i nie
jest używany przez żaden moduł produkcyjny.** Zweryfikowane:

```
src/ksef_mcp/ksef_port/types.py:108      definicja
src/ksef_mcp/ksef_port/__init__.py:30,63 eksport
tests/test_port_types.py:13,61,65        test
```

Zero wywołań w `src/`. Bramka pokrycia świeci na zielono, bo **test pokrywa
martwy kod** — bramka tej klasy długu nie łapie, ona ją maskuje.

Tymczasem surowy `nip: str` jest jednocześnie:

- kluczem w keyringu — `keyring.get_password(SERVER_NAME, nip)`;
- składową ścieżki na dysku, w pięciu powielonych miejscach:
  `base / SUBJECT_DIRECTORY / self.nip / str(self.environment)` w
  `archive.py:401`, `audit.py:214`, `review.py:204`, `sync_store.py:263`,
  `period_cache.py:261`.

Zapis `123-456-32-18` i `1234563218` daje dwa osobne archiwa i dwa wpisy
w keyringu. Po cichu, bez błędu.

**Prześledziłem to do końca i prowadzi do operacji nieodwracalnej:**

```
--nip (argparse, zero walidacji, cli.py:559)
  → run_purge (cli.py:469) → InvoiceArchive(nip=subject) (cli.py:487)
  → base / "subjects" / self.nip / env (archive.py:401)
  → ArchivePurge.remove(plan) (cli.py:503) → kasowanie plików
```

`ksef-mcp purge --nip ../../..` kieruje plan czyszczenia poza drzewo podmiotu.
Łagodzi to potwierdzenie (`cli.py:500`) i wypisanie kandydatów przed
skasowaniem — człowiek widzi, co zniknie — ale interaktywne potwierdzenie jest
kontrolą pomyłki, nie kontrolą bezpieczeństwa.

To nie jest luka wiedzy zespołu. `pdf.py:138-154` parsuje numer KSeF na granicy
i ma komentarz o `..` — ryzyko jest w projekcie rozpoznane i obsłużone dla
numeru faktury, a przeoczone dla NIP-u. Luka konsekwencji, nie luka wiedzy —
i dlatego tania do zamknięcia.

Glosariusz domeny nazywa brakujące pojęcie wprost: **Kontekst podmiotu**.

---

## 5. Struktura pakietów — odpowiedź na polecenie

Dziś `src/ksef_mcp/` jest płaskie: dwadzieścia modułów obok siebie i jeden
podpakiet. Cztery moduły przekraczają 500 linii, a `server.py` i `cli.py` to
24% kodu produkcyjnego.

**Graf importów jest acykliczny.** Zbudowałem go niezależnie i potwierdza to
faza strukturalna. To istotnie obniża koszt całej operacji: nie trzeba
rozplątywać węzła, tylko przesunąć granice. Pięć importów wewnątrz funkcji to
świadome odroczenie kosztu startu `ksef2` (~0,5 s przez `lxml`/`signxml`), nie
obejście cyklu — każdy ma komentarz nazywający powód.

### Dwa ruchy, które trzeba zrobić najpierw

**Warstwa antykorupcyjna sięga po moduł konfiguracji.** Rdzeniowe wyliczenie
portu mieszka w module odczytu konfiguracji:

```
ksef_port/protocol.py:4    from ksef_mcp.config import KsefEnvironment
ksef_port/connection.py:5  from ksef_mcp.config import KsefEnvironment
ksef_port/adapter.py:19    from ksef_mcp.config import KsefEnvironment
```

Dziś portu nie da się wyjąć ani przetestować bez `config`, co uniemożliwia
zapisanie kontraktu warstwowego bez wyjątku — a kontrakt z wyjątkiem nie
chroni niczego.

**To jednak nie jest naruszenie ADR-102, tylko zmiana jego decyzji.** ADR
zapisał ten stan świadomie, w tabeli odwzorowania słownika (linia 71):
`| Środowisko | KsefEnvironment (istniejący, w config.py) |`. ADR pilnuje,
by port nie sięgał po **sekrety** i po **wejście-wyjście** — i tego pilnuje
skutecznie (token jest argumentem, keyring zostaje w `token_store.py`). Nie
deklaruje natomiast niezależności od modułu konfiguracji.

Konsekwencja praktyczna: krok musi obejmować aktualizację ADR-102, inaczej
sam wprowadzi rozjazd, który `bin/check-adr-drift.py` istnieje po to, żeby
wykrywać. Nakład rośnie z S do M. Cel przenosin to **nie** `ksef_port/types.py`
(285 linii — `config` płaciłoby pełnym słownikiem KSeF za trzyczłonowy enum,
czyli dokładnie ten anty-wzorzec, który zwalczamy poniżej), lecz osobny liść
`ksef_port/environment.py`. Zasięg: 13 modułów produkcyjnych i 17 plików
testowych, w każdym jedna linia importu.

Ta sama tabela ADR-102 odsłania rzecz ostrzejszą: przypisuje pojęciu
`KontekstPodmiotu` typ `SubjectContext` — ten sam, który jest martwy
(rozdział 4). ADR obiecuje odwzorowanie, którego produkcja nie używa.

**Stałe ciągną duże moduły.** `SUBJECT_DIRECTORY` mieszka w `sync_store.py:34`
i jest importowane przez `archive`, `audit`, `period_cache`, `review` — cztery
moduły zależą od magazynu synchronizacji wyłącznie po układ katalogów.
Analogicznie `listing` i `review` importują `SYNCHRONISED_DIRECTIONS`
z `synchronisation.py` (523 linie), przez co czytelnik zależy od pisarza.
Pięć krawędzi grafu istnieje po dwie stałe.

### Rekomendowany układ: podział po kontekstach

Wariant warstwowy (`domain/` / `application/` / `infrastructure/` /
`interfaces/`) **odrzucam**. Przy 6,5 tys. linii dałby katalogi po trzy–pięć
plików, a każda zmiana funkcjonalna dotykałaby czterech katalogów naraz —
rozdzielałby to, co się razem zmienia.

```
src/ksef_mcp/
├── metadata.py  config.py  retention.py
├── paths.py                    # NOWY — układ katalogów podmiotu
├── ksef_port/                  # jedyna dziś poprawna granica
├── storage/    sync_store, archive, period_cache, audit, token_store
├── invoices/   package, synchronisation, listing, review, statement, directions.py
├── rendering/  pdf, preflight (część Node), node/, vendor/
├── setup/      skill, client
├── mcp/        app, results, context, tools_*, entrypoint
└── console/    prompts, messages, onboarding, diagnostics, tokens, maintenance, cli
```

**Granica opłacalności, powiedziana wprost:** opłaca się `mcp/`, `console/`,
`storage/`, `paths.py`, `invoices/directions.py`. Nie opłaca się wydzielać
`domain/` obok `application/` — domena to kilkaset linii wplecionych w klasy
przypadków użycia, a osobny pakiet dałby pliki po czterdzieści linii. Próg:
wprowadzić dopiero, gdy moduł w `invoices/` przekroczy ~600 linii albo pojawi
się zapis do KSeF.

### Ryzyko, które nie dotyczy katalogów

**Obecność `node/` i `vendor/` w wheelu zależy wyłącznie od `.gitignore`.**
Hatchling pakuje zawartość katalogu pakietu poza plikami ignorowanymi przez
system kontroli wersji. Dopisanie tam typowego dla Node wpisu (`*.js`,
`package.json`) po cichu wypadnie zasoby z dystrybucji — instalacja, import
i wszystkie 959 testów nadal przejdą, a awaria wyjdzie dopiero u użytkownika
przy pierwszym `render_invoice_pdf`.

Jeden test sprawdzający zawartość zbudowanego wheela zamyka tę klasę problemów
i jest najlepszym stosunkiem zysku do nakładu w całym audycie.

### Reguła migracji

Każdy krok to jeden odwracalny commit z zielonym `uv run pytest`. Przy 5,53 s na
pełny przebieg nie ma pokusy grupowania przeprowadzek dla oszczędności czasu.

Jedna pułapka wymaga podkreślenia: `--cov=ksef_mcp` mierzy pakiet **po nazwie
importu, nie po ścieżce pliku**. Przeniesienie `archive.py` do `storage/` bez
ruszenia `tests/test_archive.py` da zielony commit ze stuprocentowym pokryciem.
Bramka tego nie złapie nigdy, bo nie mierzy struktury katalogów testowych.
Dlatego **moduł i jego test przenosimy w tym samym commicie**, a weryfikacja
kroku to `uv run pytest` plus sprawdzenie, że na starym poziomie nie został plik
testujący przeniesiony moduł.

Warunek wstępny: trzynaście plików robi `from synthetic import ...` jako
z modułu najwyższego poziomu, więc `tests/support/` musi powstać **przed**
jakimkolwiek podziałem katalogów testowych.

Kroki łamiące dla osób z zewnątrz dotyczą wyłącznie ścieżek importu. Nazwa
dystrybucji, skrypt konsolowy, `SERVER_NAME` (tożsamość MCP i klucz keyringu)
oraz `DISTRIBUTION_NAME` zostają nietknięte i rozdzielone w `metadata.py`,
zgodnie z ostrzeżeniem `CLAUDE.md`. Projekt jest w statusie Alpha, więc łamanie
importów jest dopuszczalne — ale kroki przeprowadzkowe należy skupić w jednym
wydaniu minor, z wpisem w `CHANGELOG.md` wymieniającym starą i nową ścieżkę
każdego modułu. Rozłożenie na trzy wydania to trzykrotne zepsucie tym samym
osobom.

---

## 6. Poprawność wyniku dla księgowej

**Brak typu dokumentu.** `InvoiceMetadata` (`ksef_port/types.py:181`) nie ma
pola mówiącego, czym dany dokument jest; `as_metadata` (`adapter.py:152`) nie
czyta typu faktury z odpowiedzi KSeF. Napisy „FA(2)/FA(3)" żyją w siedmiu
docstringach i w żadnym typie.

`export_period_statement` sumuje kolumnę Brutto po wszystkich wierszach okresu.
Jeśli w oknie znajdzie się faktura korygująca, suma podana księgowej jest sumą
dokumentów, a nie sumą zobowiązania. Kod ostrzega przed sumowaniem walut
(`currency_warning`), więc świadomość „suma może wprowadzić w błąd" jest obecna
— tylko niepełna.

**Do rozstrzygnięcia przed wyceną:** czy metadane KSeF w ogóle niosą typ
dokumentu i czy korekty przychodzą z kwotami ujemnymi. Tego nie da się
rozstrzygnąć z kodu — wymaga weryfikacji wobec `ksef2`. Rekomendacja jest wobec
tej odpowiedzi warunkowa: samo ostrzeżenie w `Statement.warnings` zamyka ryzyko
tanio (M), pełne powiązanie korekty z korygowaną fakturą to osobna decyzja (L,
z ADR).

---

## 7. Łańcuch dostaw

- **Akcje GitHub nieprzypięte do SHA.** `anthropics/claude-code-action` jest
  przypięta wzorowo, z komentarzem wersji. Pozostałe —
  `actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/upload-artifact@v4`,
  `pypa/gh-action-pypi-publish@release/v1`,
  `13rac1/block-fixup-merge-action@v2.0.0` — siedzą na ruchomych tagach.
  Najpoważniejsza jest akcja publikująca: działa w zadaniu z `id-token: write`,
  więc przesunięcie gałęzi `release/v1` daje możliwość opublikowania dowolnego
  artefaktu jako `ksef-mcp`. Pakiet obsługuje poświadczenia podatkowe i ma
  dostęp do archiwum faktur.
- **Publikacja przez zaufane wydawanie OIDC** — zrobiona dobrze, bez
  długowiecznych tokenów; kontrola zgodności tagu z wersją w sdiście wyłapuje
  realny błąd człowieka. Brakuje atestacji pochodzenia
  (`actions/attest-build-provenance`).
- **Skrót zwendorowanego bundla zapisany, ale nieegzekwowany.** Nota
  `vendor/LICENCJA-MF.md` zapisuje pochodzenie wzorowo — adres, datę, rozmiar,
  SHA-256, a nawet ostrzeżenie, że portal zrywa transfer w połowie. Nic tego
  skrótu nie przelicza. Ostrzeżenie w samej nocie opisuje dokładnie scenariusz,
  w którym do repozytorium trafia obcięty bundel o poprawnej nazwie.
- **Brak skanowania podatności zależności.** Dokładne przypięcie bez skanowania
  z czasem obraca się we własne przeciwieństwo: `cryptography==50.0.1` obsługuje
  klucze do paczek z danymi osobowymi i zostanie na tej wersji, dopóki ktoś się
  nie zorientuje.
- **Artefakt MIT w projekcie AGPL-3.0-only** (zgłoszenie #83). Kierunek
  zgodności poprawny, atrybucja faktycznie spełniona, bo nota jedzie w katalogu
  pakietu — ale `.dist-info/licenses/` zawiera tylko AGPL.

---

## 8. Brak warstwy diagnostycznej

W całym `src/ksef_mcp/` nie ma ani jednego wywołania logującego. Ślad audytowy
jest znakomity w swojej roli, ale to rejestr **dostępów do danych**, nie dziennik
techniczny — celowo nie notuje nieudanych prób ani tego, na czym przebieg się
urwał.

Synchronizacja zakończona porażką wczoraj nie zostawiła nic, z czego można ją
odtworzyć. Jedyną odpowiedzią na zgłoszenie „nie pobrało mi się" jest prośba
o powtórzenie — czyli o wydanie kolejnej porcji deficytowego budżetu. Brak też
identyfikatora korelacji wiążącego wywołanie narzędzia MCP z żądaniami do KSeF.

**Kolejność ma znaczenie:** zgłoszenie #91 („czy komunikat odmowy może nieść
cudzy NIP") rozstrzyga zarazem, co wolno zapisać do dziennika. Decyzja
poprzedza logowanie, nie odwrotnie.

---

## 9. Co jest zrobione wzorcowo — chronić przy refaktoryzacji

Odnotowane, żeby porządkowanie magazynów tego nie zdeptało:

- **`audit.py`** — dziennik wyłącznie dopisywany, `O_APPEND` + `fsync`, schemat
  wersjonowany, bez treści faktury, z jawnym uzasadnieniem odstępstwa od
  wzorca temp → rename.
- **`retention.py`** — plan → zgoda → usunięcie → wpis zdarzenia; nie rusza
  indeksu ani punktów kontynuacji. Rozdział zdarzenia od stanu obowiązującego
  (GH-31) utrzymany.
- **Warstwa antykorupcyjna** — przecieku typów `ksef2` ani `mcp` poza ich
  warstwę **nie ma**; pilnują tego testy. ADR-102 jest wymuszony, nie tylko
  zadeklarowany.
- **`tests/doubles.py`** — atrapy z testem dryfu adnotacji wobec modeli SDK.
- **Świadomy brak wycofania wykładniczego** w `retry.py`: zgadywanie czasu
  oczekiwania jest wzorcem, za który Ministerstwo wydłuża blokadę. Propozycję
  „poprawienia" tego w drugą stronę należy odrzucać z odesłaniem do D-017 —
  warto dopisać regułę do `references/review-checks-common.md`.
- **Brak archetypu księgi** jest decyzją, nie zaniedbaniem: narzędzie
  konsekwentnie odmawia rozstrzygania o ujęciu podatkowym i mówi to wprost
  (`review.py:302`).

Sprawdzone i odrzucone jako fałszywe alarmy: wykluczenie `bin/` z `testpaths`
(świadome, obsłużone trzema zadaniami CI z komentarzem), N+1 wydający budżet
KSeF (pętle iterują po czterech stałych typach podmiotu), przeciek typów obcej
warstwy, `Period`/`AccountingPeriod`/`KsefEnvironment` jako obiekty wartości
(zrobione dobrze).

Bramka `fail_under = 100` **nie zdegenerowała testów** — nazwy są zdaniami
o zachowaniu, nie widać testów pisanych pod linie. Jej jedyny koszt to
niewidzialność martwego kodu, która wystąpiła dwukrotnie: `SubjectContext`
i `RetryPolicy`.

---

## 10. Kamienie milowe

| # | Kamień | Wpływ | Nakład | Zależności |
|---|---|---|---|---|
| **M1** | Serwer przestaje wyglądać na obchodzącego limity | HIGH | S+S+S+M | — |
| **M2** | Wyłączność zapisu i atomowość między procesami | HIGH | S+M | — |
| **M3** | Domknięcie łańcucha dostaw i dystrybucji | HIGH | 5×S | — |
| **M4** | Tożsamość podmiotu staje się typem | HIGH | M+L | — |
| **M5** | Rozstrzygnięcie #91, potem obserwowalność | MEDIUM | M+M | #91 |
| **M6** | Poprawność zestawienia wobec korekt | HIGH* | M / L | weryfikacja wobec `ksef2` |
| **M7** | Rozcięcie fałszywych krawędzi grafu | MEDIUM | 4×S | — |
| **M8** | Przygotowanie testów do podziału | MEDIUM | M | przed M9 |
| **M9** | Przeprowadzki pakietów | MEDIUM | L | M7, M8 |
| **M10** | Rozbicie adapterów wejścia | MEDIUM | M+M | M9 |
| **M11** | Reguły wracają do typów | MEDIUM | 6×S | — |
| **M12** | Wspólny prymityw magazynu | MEDIUM | M | M2 |
| **M13** | Zamrożenie struktury bramką CI | MEDIUM | S | M10 |
| **M14** | Kontrakt, dokumentacja, `README` | MEDIUM | S+M | — |
| **M15** | Spójna sygnalizacja błędu i trwałość konfiguracji | HIGH | M+S+S | — |
| **M16** | Testy, które sprawdzają zachowanie, nie linie | HIGH | S+M+M+L | — |
| **M17** | Kompletność odpowiedzi ponad 250 faktur | HIGH | S+S+L | M1 |

\* wpływ warunkowy wobec weryfikacji, czy KSeF udostępnia typ dokumentu.

**Kolejność.** M1 pierwsze i bez dyskusji — trzy z czterech poprawek to nakład
S, a skutkiem zaniechania jest blokada podmiotu, nie niewygoda. M2 i M3 są od
M1 niezależne i można je prowadzić równolegle; M3 nie dotyka kodu
produkcyjnego wcale. M4 zamyka najsilniej potwierdzone ustalenie audytu
i odblokowuje etap 3 projektu.

Refaktoryzacja strukturalna (M7–M10, M13) idzie **po** M1–M4, z jednego
powodu: dopóki w kodzie siedzą błędy poprawności, przenoszenie plików utrudnia
ich naprawę i zaciemnia historię. Za to gdy już się zacznie, M9 i M10 należy
wykonać w jednym ciągu — żadna z przeprowadzek nie zmienia zachowania, więc
959 zielonych testów przy stuprocentowym pokryciu gałęzi jest najlepszą możliwą
siatką: czerwony przebieg jednoznacznie oznacza błąd przenoszenia.

---

## 10a. Uzupełnienie: pokrycie JTBD i spójność przekrojowa

Dwie fazy wróciły po złożeniu pierwszej wersji tej notatki i przyniosły
ustalenia, które zmieniają obraz.

### Port nie potrafi pobrać drugiej strony metadanych — najpoważniejsze

```python
# ksef_port/types.py:199-204
class MetadataPage:
    invoices: tuple[InvoiceMetadata, ...]
    has_more: bool  # raportowane
    truncated: bool
    hwm_date: datetime | None
    # brak kursora, offsetu i numeru strony


# ksef_port/adapter.py:279
params = InvoiceMetadataParams(page_size=PAGE_SIZE, sort_order="desc")
# brak parametru strony
```

Zweryfikowane osobiście. To nie jest „nie zrobiono", tylko **nie da się przez
ten interfejs**. `has_more` jest raportowane i nie ma jak na nie zareagować.
Tymczasem synchronizacja ma pełny mechanizm punktów kontynuacji — dwa
niezgodne paradygmaty w jednym systemie.

Skutek dla produktu: `review_new_invoices` obiecuje „co przyszło od ostatniego
przeglądu" w oknie 89 dni dla czterech typów podmiotu. Powyżej 250 faktur
rejestr przeglądu zapisuje jako pokazane tylko to, co się zmieściło, a przy
następnym wywołaniu okno się przesuwa — **pominięte faktury nigdy nie wrócą**.
To dokładnie klasa błędu, którą ten moduł napisano, żeby wykrywać: historia
faktury z siódmego lipca w docstringu `review.py:1-40`. `_incompleteness:289`
ostrzega przed przypadkiem odwrotnym, ten groźniejszy jest niepokryty.

Poprawka doraźna jest tania i niezależna od ADR-a: `assess` nie oznacza niczego
jako pokazane przy `complete=False`, tą samą logiką, którą już stosuje powyżej
progu (`review.py:344-363`).

### Bramka pokrycia przepuszcza cztery klasy błędów

Teza z rozdziału 4 domyka się liczbą. Przy 959 testach i stu procentach linii
oraz gałęzi przechodzą:

1. **Martwy kod z własnym testem.** Wyczerpująco sprawdzone na wszystkich 31
   eksportach `ksef_port/__init__.py`: martwe są dokładnie dwa byty —
   `SubjectContext` i `RetryPolicy`/`NO_AUTOMATIC_RETRY`. Pozostałe 28 ma
   wywołania produkcyjne. Nie ma tego więcej.
2. **Test utrwalający błąd jako zachowanie oczekiwane.**
   `test_a_refused_export_is_kept_under_its_reference` z asercją
   `[FAILED, FAILED]`.
3. **Zachowanie rozciągnięte na wiele wywołań.** Wszystkie testy budżetu
   trzymają jeden obiekt w pamięci; zachowanie *pomiędzy* wywołaniami nie jest
   sprawdzane nigdzie.
4. **Kontrakt z cudzym API sprawdzany wyłącznie wobec własnej atrapy.** Marker
   `ksef_live` jest zadeklarowany w `pyproject.toml`, wykluczany przez CI,
   wymagany przez `CLAUDE.md` i cztery specyfikacje agentów przeglądu — i nie
   nosi go ani jeden test. Wspólna przyczyna zgłoszeń #90 i #82.

Piąty przypadek jest najczystszym dowodem tezy: **obsługa wyczerpanego limitu
jest w produkcji nieosiągalna**. `SYNCHRONISED_DIRECTIONS` ma cztery pozycje,
więc jedno wywołanie wydaje najwyżej cztery zapytania z budżetu budowanego od
zera — `remaining()` nie spadnie do zera, chyba że KSeF przyzna mniej niż
cztery zapytania na godzinę. Gałęzie `except` w `listing.py:302` i
`review.py:438` wraz z `refused()` i `unasked()`, napisane świadomie i
z komentarzem tłumaczącym zamiar, są nieuruchamialne. Testy sięgają tam
wyłącznie przez ręcznie skonstruowany `per_hour=0` — stan, którego system nie
potrafi wytworzyć.

Praktyczny wniosek: naprawa budżetu i ta gałąź to **jedno zadanie**. Degradacja
jest już napisana; brakuje wyłącznie budżetu, który faktycznie się wyczerpuje.

### Rozjazd CLI wobec serwera dotyczy codziennego cyklu pracy

`REFUSALS` (`server.py:105-112`) ma sześć pozycji; poza nią zostaje dziewięć
własnych wyjątków magazynów plus `JSONDecodeError` i `OSError`.
`refuse_a_locked_collection()` jest wołane przed każdym dotknięciem sekretu,
a kolekcja keyringu zamyka się sama po uśpieniu maszyny. Wszystkie pięć
narzędzi MCP idzie przez `authenticated_subject()` → `read_token()`, więc po
uśpieniu laptopa każde zwróci gołe „Error executing tool …" — mając gotowy,
napisany na tę sytuację komunikat z instrukcją wyjścia. `cli.py:667` go
pokazuje, serwer gubi.

Pokrycie `REFUSALS` jest odwrotnie proporcjonalne do ryzyka: najlepiej pokryte
jest `render_invoice_pdf`, czyli narzędzie, którego autor pisał tę krotkę.
`review_new_invoices` jest pokryte najsłabiej — `ReviewLedgerUnreadable`,
wyjątek istniejący *wyłącznie* po to, by nieść wyjaśnienie, nie dociera do
nikogo.

### Konfiguracja jest jedynym dokumentem bez obu zabezpieczeń

`config.save_configuration` (`config.py:72-93`) jest jedyną z siedmiu procedur
zapisu bez wzorca temp→rename — pisze w plik docelowy z `O_TRUNC`, bez `fsync`.
`load_configuration` nie łapie `JSONDecodeError` i nie łapie go żadne z sześciu
wywołań. Przerwany zapis sprawia, że **serwer i CLI przestają startować**,
a jedynym wyjściem jest ręczne skasowanie pliku. Ten sam plik jest też jedynym
z sześciu dokumentów trwałych bez `schema_version`.

### Poprawka do planu restrukturyzacji

Nazwy pakietów `mcp/` i `console/` z rozdziału 5 zastępuję przez **`server/`
i `cli/`** — czyli dokładnie te, które te moduły mają dziś. Skutek jest
istotniejszy niż estetyka: `ksef-mcp = "ksef_mcp.cli:main"` działa bez zmiany,
bo pakiet z `__init__.py` re-eksportującym `main` spełnia wpis skryptu
konsolowego identycznie jak moduł, a `from ksef_mcp.server import server`
przeżywa. **Dwa z trzech kroków łamiących przestają być łamiące.** Znika też
kolizja `ksef_mcp.mcp` z zależnością `mcp==2.2.0`, którą importuje
`server.py:8-9`.

Potwierdzone dowodem, nie wnioskowaniem: w repozytorium nie ma `pythonpath`,
`importmode`, `consider_namespace_packages`, `setup.cfg`, `pytest.ini`,
`tox.ini` ani `conftest.py` w korzeniu, a `tests/` nie ma `__init__.py`. Czyli
`from synthetic import` w trzynastu plikach stoi wyłącznie na domyślnym trybie
`prepend`, a `tests/support/` jest twardym warunkiem wstępnym przeprowadzek.
Awaria byłaby przy tym głośna — błąd zbierania testów — więc kroku nie da się
pominąć przez nieuwagę.

## 11. Metoda

Jedenaście równoległych przeglądów w trybie wyłącznie do odczytu, każdy
z osobnym zakresem i jednolitym formatem ustaleń. Zbieżność niezależnych faz
traktowana jako wzmocnienie: cztery ścieżki z rozdziału 2 znalazły trzy fazy
osobno, tożsamość podmiotu — sześć.

Wszystkie ustalenia o wpływie HIGH zostały zweryfikowane bezpośrednio przez
prowadzącego audyt: przez uruchomienie polecenia albo przeczytanie
przywoływanego kodu. Ustalenia, które weryfikacji nie przeszły, zostały
odrzucone i są wymienione w rozdziale 9 — razem z tym, co jest zrobione dobrze,
żeby raport nie czytał się jako sama lista zarzutów.
