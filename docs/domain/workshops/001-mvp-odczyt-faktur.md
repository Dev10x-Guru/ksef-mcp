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

## Decisions Made

D-001 … D-025 — pełna treść w `decisions.md`. Najważniejsze:

| ID | Skrót |
|---|---|
| D-001 | MVP = odczyt za miesiąc; mapa drogowa trzech etapów |
| D-006 | `PobranieOkresu` nie jest agregatem; jedyny agregat to `WpisArchiwum` |
| D-009 | Kontekst podmiotu należy do poświadczenia, nie do zapytania |
| D-016 | Serwer MF nie renderuje PDF; renderowanie należy do klienta |
| D-017 | Klientem jest `ksef2`; retry i walidacje należą do portu |
| D-018 | `MCPServer`, nie `FastMCP` |
| D-020 | Paginacja nie jest sterowana przez model; budżet jest liczony |
| D-025 | Osią produktu jest automat, archiwum i delta — nie prezentacja |

Superseded: D-003 przez D-016, D-002 przez D-017, D-005 przez D-022.

## Model Changes

- Z pięciu kontekstów ograniczonych zostały dwa: **Dostęp** i **Archiwum**.
- Z trzech agregatów został jeden: **`WpisArchiwum`**, z niezmiennikiem
  egzekwowanym przez `temp → rename`, a nie przez zbiór w pamięci.
- Szew wielopodmiotowości przeniesiony z zapytania do warstwy poświadczeń.
- `Okres` zyskał drugą składową — `DateType`. Bez niej „sierpień" ma trzy
  różne znaczenia.
- Cykl życia procesu wszedł do modelu jako pełnoprawny element.

## Open Questions

1. **`renderers/` i extra `pdf` w `ksef2`** — czy renderuje FA(2)/FA(3) w
   czystym Pythonie, czy `weasyprint` ciągnie cairo/pango jako twardą
   zależność systemową, i jak daleko wynik odbiega od wizualizacji
   urzędowej. Może unieważnić całą gałąź z Node. *W trakcie weryfikacji.*
2. **Zachowanie `keyring` bez sesji D-Bus** — czy da się wykryć brak
   backendu **bez** wywołania promptu. Od tego zależy, czy [D-004]
   przeżyje pierwszego użytkownika. ST-3.
3. **Limity zapytań** (8/s, 16/min, 20/h) — potwierdzić w `open-api.json`.
   Cały [D-020] na nich stoi.
4. **Maksymalne okno zapytania po stronie API MF** — `ksef-client` wymusza
   100 dni po stronie klienta; czy to odbicie limitu serwera, czy własna
   ostrożność autora.
5. **Próg listy w czacie** [D-023] — konkretna wartość i forma skrótu.
6. **Czy przewaga nad Aplikacją Podatnika jest wystarczająca** — zarzut
   z pre-mortem. [D-025] deklaruje oś, ale nie dowodzi jej.

## Artifacts Produced

- `docs/domain/model.md`, `decisions.md`, `glossary.md`,
  `stress-tests.md` (ST-1…ST-5), `epics.md` (T-01…T-10)
- `docs/domain/README.md`, `workshops/TEMPLATE.md`
- `calculator.md` **świadomie pominięty** — domena nie ma warstwy
  obliczeniowej.

## Uwagi procesowe

Dwa razy w trakcie sesji wyciągnięto błędny wniosek o zachowaniu cudzego
systemu na podstawie dokumentacji, i raz na podstawie streszczenia cudzego
raportu. Za każdym razem rozstrzygnął dopiero kontakt z artefaktem
źródłowym — surowa odpowiedź HTTP, treść faktury, linia kodu. To
potwierdza zarzut adwokata diabła, że modelowanie przed pierwszym realnym
wywołaniem API produkuje kształt fikcyjny; obie korekty kształtu modelu
przyszły właśnie z takiego kontaktu.
