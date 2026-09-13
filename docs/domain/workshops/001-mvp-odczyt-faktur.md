# Workshop 001: MVP — odczyt faktur za miesiąc

- **Date:** 2026-09-13
- **Participants:** Janusz Skonieczny (ekspert dziedzinowy i decydent);
  obsada AI: facylitator, panel czterech person (księgowa/back-office,
  compliance/prawnik, integrator/dev, użytkownik agenta AI), adwokat
  diabła. Wejście z sesji `kb-c7` (baza wiedzy) i `ksef-mcp-1-ab`
  (weryfikacje na zainstalowanych paczkach).
- **Brief:** serwer MCP do KSeF uruchamiany lokalnie przez `uvx`, na bazie
  biblioteki z PyPI.

## Key Findings

**Zakres zawęził się w trakcie sesji trzykrotnie i to była najważniejsza
praca warsztatu.** Punkt wyjścia — „pełny zakres" — przeszedł w „odczyt
faktur za miesiąc", a następnie w mapę drogową trzech etapów: jeden
podmiot i faktury zakupowe → ten sam podmiot i sprzedażowe → biura
rachunkowe z przełączaniem podmiotów.

**Nie ma oficjalnego SDK KSeF dla Pythona.** Ministerstwo Finansów
(`CIRFMF`) wydaje SDK wyłącznie dla Javy i C#, ale publikuje oficjalny
kontrakt OpenAPI. To zmieniło charakter decyzji o bibliotece z „które
README brzmi lepiej" na „pakiet społecznościowy, generowanie z kontraktu
czy klient własny".

**Limity zapytań okazały się głównym ograniczeniem projektowym, nie
wydajnościowym.** Budżet 20 zapytań metadanych na godzinę dzielony między
metadane, pobieranie treści, ponowienia i błędne wywołania agenta
przesądził o trzech decyzjach naraz: `pageSize=250` jako niezmiennik,
zakaz sterowania paginacją przez model i konieczność cache'u metadanych.

**Serwer MF nie renderuje PDF-ów żadną ścieżką HTTP.** Ustalone przez
obserwację po dwóch błędnych wnioskach z dokumentacji. Portal
weryfikacyjny ściąga do przeglądarki oficjalny moduł
`@akmf/ksef-fe-invoice-converter` i generuje PDF po stronie klienta.

**Adwokat diabła obalił trzon pierwszej propozycji modelu.** `PobranieOkresu`
było sagą w przebraniu, pięć kontekstów ograniczonych w jednoprocesowym
narzędziu stdio było przerostem, a warstwa antykorupcyjna projektowana
przed wyborem klienta chroniła przed wyobrażonym API.

**Ministerstwo Finansów publikuje kanoniczny wzorzec synchronizacji, dla
którego zaprojektowaliśmy własne, gorsze odpowiedniki.** Ustalone dopiero
pod koniec sesji, przy weryfikacji limitów. Eksport paczek jest ścieżką
zalecaną, okna wyznacza High Water Mark, kompletność wymaga iteracji po
typach podmiotu. Do tego przeoczono limit `GET /invoices/ksef/{nr}` =
**64 żądania/h**, przez co sufit wolumenu był szacowany ~80× za wysoko.
Zarzut adwokata, że model powstał przed kontaktem z API, okazał się
trafniejszy, niż go wtedy przyjęto.

**Teza o wartości produktu została poddana falsyfikacji i przetrwała.**
Na rzeczywistym archiwum: 61% faktur docierało później niż tydzień po
wystawieniu (mediana 10 dni), 32 z 57 plików miało już `.ksef.` w
nazwie — czyli robota była wykonywana ręcznie. Odpytanie produkcji KSeF
wykazało **jedną fakturę kosztową, która nie trafiła do ewidencji
wcale**, mimo że okres był już zarchiwizowany.

## Decisions Made

D-001 … D-034 — pełna treść w `decisions.md`. Najważniejsze:

| ID | Skrót |
|---|---|
| D-001 | MVP = odczyt za miesiąc; mapa drogowa trzech etapów |
| D-006 | `PobranieOkresu` nie jest agregatem; jedyny agregat to `WpisArchiwum` |
| D-009 | Kontekst podmiotu należy do poświadczenia, nie do zapytania |
| D-017 | Klientem jest `ksef2`; retry i walidacje należą do portu |
| D-018 | `MCPServer`, nie `FastMCP` |
| D-020 | Paginacja nie jest sterowana przez model; budżet jest liczony |
| D-025 | Osią produktu jest automat, archiwum i delta — **zwalidowane** |
| D-027 | PDF z oficjalnego generatora MF pod Node, ze zwendorowanego bundla |
| D-028 | Odbiorcą etapu 1 jest użytkownik techniczny |
| D-031 | Synchronizacja wg kanonicznego wzorca MF: eksport + HWM |
| D-032 | Magazyn rozdzielony wg XDG: cache osobno od trwałego stanu |
| D-033 | Klucz eksportu ma własny cykl życia, poza keyringiem |
| D-034 | Archiwum bezterminowe, czyszczone jawną komendą |
| D-035 | Sześć powierzchni nazewniczych; wszystkie zbiegają się do `ksef-mcp` |
| D-036 | Dwa rejestry decyzji: produktowe w `decisions.md`, strukturalne w `docs/adr/` |

Zastąpione: D-003 przez D-016, D-002 przez D-017, D-016 przez D-027,
D-024 przez D-031. `D-005`, `D-008` i `D-022` obowiązują z korektami
opisanymi w `D-031`.

**D-015 nie została zastąpiona** — nazwa pakietu wróciła do `ksef-mcp` po
przejściowym odstępstwie wprowadzonym w implementacji wbrew obowiązującej
decyzji. `D-035` ją potwierdza i opisuje sekwencję, żeby nie czytała się
jako dwie sprzeczne decyzje.

## Model Changes

- Z pięciu kontekstów ograniczonych zostały dwa: **Dostęp** i **Archiwum**.
- Z trzech agregatów został jeden: **`WpisArchiwum`**, z niezmiennikiem
  egzekwowanym przez `temp → rename`, a nie przez zbiór w pamięci.
- Szew wielopodmiotowości przeniesiony z zapytania do warstwy poświadczeń.
- `Okres` zyskał drugą składową — `DateType`. Bez niej „sierpień" ma trzy
  różne znaczenia.
- Cykl życia procesu wszedł do modelu jako pełnoprawny element.
- **Synchronizacja przeszła z własnego pomysłu na kanoniczny wzorzec MF**
  — eksport paczek zamiast pojedynczych pobrań, High Water Mark zamiast
  własnego znacznika, iteracja po typach podmiotu. `DateType` przestał
  być wyborem użytkownika i jest przybity do `PermanentStorage`.
- Magazyn lokalny rozdzielony na cache i trwały stan; szyfrowanie
  AES-256 wróciło do etapu 1 razem z eksportem.

## Open Questions

**Zamknięte w trakcie sesji — wszystkie weryfikacją, nie dyskusją:**

| Pytanie | Rozstrzygnięcie |
|---|---|
| Renderowanie PDF | oficjalny generator MF pod Node [D-027] |
| Limity zapytań | potwierdzone u źródła, plus przeoczony limit 64/h [D-031] |
| Maksymalne okno zapytania | **pytanie zniknęło** — okna wyznacza KSeF |
| Teza o wartości produktu | sfalsyfikowana i przetrwała [D-025] |
| `keyring` bez D-Bus | wykrywalny bez promptu, ST-3 |
| `keyring` **zablokowany** | wykrywalny, ale trzeba ominąć API `keyring`, ST-3 |
| Próg listy w czacie | **50 pozycji** [D-023] |
| Aktualizacja bundla MF | ręcznie przy wydaniu [D-027] |
| Format `.mcpb` | potwierdzony; rachunek kosztów odwrotny [D-028] |

**Pozostają otwarte — obie wymagają danych, których dziś nie mamy:**

1. **Zakres komendy czyszczącej** — per podmiot, per okres, czy po obu
   wymiarach [D-034]. Rozstrzygnie się, gdy archiwum urośnie na tyle, by
   pokazać, którym wymiarem ludzie faktycznie chcą ciąć.
2. **Czy wartość utrzymuje się dla biur rachunkowych** — test [D-025]
   objął **jeden podmiot** i nie uogólnia się na etap 3. Powtórzenie
   wymaga dostępu do archiwum biura, nie tylko tokenu.

---

> **Zapis zamknięty 2026-09-13.** Od tego momentu niemutowalny zgodnie
> z `document-structure.md`. Stan decyzji żyje dalej w `decisions.md` —
> ten dokument opisuje, **co się wydarzyło**, nie **co obowiązuje**.

## Artifacts Produced

- `docs/domain/model.md`, `decisions.md`, `glossary.md`,
  `stress-tests.md` (ST-1…ST-5), `epics.md` (T-01…T-10)
- `docs/domain/README.md`, `workshops/TEMPLATE.md`
- `calculator.md` **świadomie pominięty** — domena nie ma warstwy
  obliczeniowej.

## Uwagi procesowe

**Wniosek oparty na niepełnej lekturze okazał się błędny sześciokrotnie.**
Kolejno: że portal weryfikacyjny oddaje wyłącznie XML; że serwuje gotowy
PDF; że `ksef2` jest lepszą drogą renderowania; że mapper `ksef2` odwraca
semantykę `Subject2`; że MF deduplikuje za nas i `D-005` dubluje ich
mechanizm; oraz — już w skrypcie porównującym — dwie pomyłki dające 10 i
7 fałszywych braków zamiast czterech rzeczywistych.

Za każdym razem rozstrzygnął dopiero kontakt z artefaktem źródłowym:
wygenerowany plik, surowa odpowiedź HTTP, treść faktury, linia kodu,
oficjalny dokument MF. **Żadnej z tych pomyłek nie wychwyciło czytanie
dokumentacji ani weryfikacja cudzym streszczeniem.**

Najbardziej pouczająca była trzecia — nie wynikała z braku danych.
Wiedziano, że WeasyPrint wymaga bibliotek systemowych, i że Node to jeden
plik binarny. Te dwa fakty po prostu nie zostały zestawione, bo „czysty
Python" brzmiało jak oczywista wygrana. Obalił ją dopiero użytkownik,
patrząc na wygenerowany PDF.

To potwierdza zarzut adwokata diabła, że modelowanie przed realnym
wywołaniem API produkuje kształt fikcyjny. Wszystkie korekty modelu
przyszły z kontaktu z artefaktem, żadna z rozumowania.
