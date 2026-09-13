# Domain Model: ksef-mcp

> **Status:** Warsztat 001
> **Archetypy:** Party + Role, Accountability (etap 3), Moment-Interval,
> Range, Quantity/Money

Serwer MCP do KSeF uruchamiany lokalnie przez `uvx`, na transporcie stdio,
wywoływany przez agentów AI. Zakres etapu 1: odczyt faktur zakupowych
jednego podmiotu za wybrany miesiąc [D-001].

## Bounded Contexts

Dwa konteksty, nie pięć [D-007]. „Powierzchnia MCP" i „Prezentacja" to
warstwy dostarczania, nie konteksty domenowe.

### Dostęp

Odpowiada na pytanie: *kim jesteśmy wobec KSeF i czy wolno nam czytać?*

Uwierzytelnienie, cykl życia sesji, poświadczenia, środowisko. Kontekst
podmiotu należy tutaj — jest własnością poświadczenia, nie parametrem
zapytania [D-009]. Etap 3 (biura rachunkowe) rozszerza **ten** kontekst o
wiele poświadczeń, nie dokłada parametru gdzie indziej.

### Archiwum

Odpowiada na pytanie: *które faktury za ten okres już mamy, a których
jeszcze nie?*

Zapytania o listę, paginacja, pobieranie treści, deduplikacja, delta
„nowe od ostatniego pobrania". To tutaj mieszka jedyny niezmiennik wart
obrony.

### Mapa kontekstów

`Dostęp` → `Archiwum` — Customer-Supplier. Archiwum konsumuje
uwierzytelnioną sesję, nie wie nic o tokenach ani certyfikatach.

Klient KSeF (biblioteka zewnętrzna) wchodzi przez **warstwę
antykorupcyjną**, której kształt ustalimy **po** pierwszym realnym
wywołaniu API, nie przed [D-002].

## Aggregates

### `WpisArchiwum` — jedyny agregat

- **Korzeń:** pojedyncza zarchiwizowana faktura.
- **Tożsamość:** `NumerKSeF` (klucz naturalny).
- **Niezmiennik:** ta sama faktura nie jest przechowywana dwa razy.
- **Egzekwowanie:** zapis `temp → rename` pod nazwą `<NumerKSeF>.xml`.
  Na systemie plików `rename(2)` w obrębie jednego FS jest **jedynym**
  prymitywem atomowym — i to on, nie zbiór w pamięci, jest strażnikiem
  niezmiennika [D-006].

### Co agregatem **nie** jest

- **`PobranieOkresu`** — serwis aplikacyjny bez tożsamości. Łączyłby w
  jednym korzeniu trzy różne czasy życia (stan paginacji, rzut Archiwum,
  parametr zapytania) i żyłby przez N wywołań sieciowych z wygasającą
  sesją w tle. To saga w przebraniu, nie agregat [D-006].
- **`SesjaKSeF`** — obiekt wartości z terminem ważności.

## Value Objects

| Typ | Zawartość | Uwagi |
|---|---|---|
| `NumerKSeF` | identyfikator nadany przez KSeF | klucz naturalny deduplikacji; **nigdy** numer własny sprzedawcy |
| `Okres` | zakres dat **+ `DateType`** | `Issue` / `Invoicing` / `PermanentStorage` — trzy różne odpowiedzi na „sierpień"; archetyp Range |
| `KierunekFaktur` | `subjectType` | wymagane przez API; etap 1 przybity do `Subject2` |
| `KontekstPodmiotu` | NIP + rola | własność poświadczenia [D-009] |
| `Środowisko` | TEST / DEMO / PROD | oznaczane w każdej odpowiedzi narzędzia |
| `Poświadczenie` | referencja do keyringu | nigdy sam sekret w konfiguracji |
| `LinkWeryfikacyjny` | NIP + data + SHA-256 base64url pliku | KOD I; składany lokalnie [D-016] |

## Indeks deduplikacji

Przechowywany **osobno od treści faktur**: numery KSeF + skróty [D-005].
Rozdzielenie rozwiązuje konflikt między idempotencją a retencją —
deduplikacja po numerze nagradzałaby trzymanie starych plików jako
indeksu, a osobny indeks pozwala skasować treść, nie tracąc
powtarzalności.

## Ograniczenia domenowe

| Ograniczenie | Konsekwencja | Status |
|---|---|---|
| Limity zapytań metadanych 8/s, 16/min, **20/h** | `pageSize=250` jest niezmiennikiem, nie strojeniem [D-010] | [Verify] |
| `pageSize` 10–250, domyślnie 10 | naiwna paginacja wyczerpuje limit godzinowy na 200 fakturach | [Verify] |
| `subjectType` jest polem **wymaganym** | kierunek nie jest szwem na zapas | potwierdzone |
| Ścieżka eksportu wymaga szyfrowania | AES/RSA wraca do zakresu przy dużym wolumenie | [Verify] |
| Token KSeF wyświetlany **jednorazowo** | jego utrata jest kosztowna | potwierdzone |
| KSeF nie zna „zamknięcia okresu" | faktura wpada do miesiąca już rozliczonego | potwierdzone |
| Serwer MF **nie renderuje PDF** | wizualizacja należy do klienta [D-016] | potwierdzone obserwacją |

## Szwy zaprojektowane świadomie

- **Kierunek faktur** — pole obecne od pierwszego dnia (wymagane przez
  API), wartość przybita do `Subject2`, **niewystawiona jako parametr
  toola MCP**. Etap 2 odblokowuje wartość i wystawia wybór [D-008].
  Powód niewystawiania: agent LLM zobaczyłby parametr wyglądający na
  wybór i zawołał go z wartością, której etap 1 nie obsługuje.
- **Wielopodmiotowość** — szew leży w warstwie **poświadczeń**, nie w
  zapytaniu [D-009]. Etap 3 to wiele poświadczeń i wiele sesji.

## Granice narzędzia

- Tool zwraca **ścieżki i metadane**. Treść faktury nie trafia do kontekstu
  modelu [D-011] — faktura zakupowa jest niezaufanym wejściem, bo jej pola
  wypełnia osoba trzecia, a agent działa na maszynie z dostępem do
  keyringu.
- Operacje odczytowe **nie pytają o zgodę**. Bramka potwierdzenia przy
  odczycie uczy klikać „tak" automatycznie i psuje moment, w którym
  pytanie naprawdę ma znaczenie.
- Ślad audytowy zostaje mimo braku bramki: timestamp, NIP kontekstu,
  kryteria, liczba dokumentów, numery KSeF, ścieżka zapisu. Bez
  poświadczeń.
- Backend keyringu, który chciałby zapytać interaktywnie, **zawiesza
  transport stdio** — stąd twardy błąd zamiast promptu [D-004].

## Cykl życia procesu

Jest częścią modelu, nie szczegółem wdrożeniowym [D-014]. Serwer MCP
uruchamiany per sesja agenta, bez sprzątania, urósł w innym projekcie do
114 procesów i 3,79 GB RAM po trzech dniach. Serwer pod `uvx` w procesie
ma dokładnie tę charakterystykę.
