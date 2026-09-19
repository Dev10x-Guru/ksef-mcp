# ADR-102: Warstwa antykorupcyjna nad klientem KSeF

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** odczyt zainstalowanego źródła `ksef2` 0.19.0 (nie README);
  `docs/domain/decisions.md` D-005, D-010, D-011, D-017, D-019, D-020,
  D-023, D-031; `docs/domain/model.md`; `docs/domain/glossary.md`;
  zgłoszenie [#35](https://github.com/Dev10x-Guru/ksef-mcp/issues/35)

## Kontekst

[D-002] odłożyło wybór biblioteki klienta i zapowiedziało warstwę
antykorupcyjną **po** pierwszym realnym wywołaniu API. [D-017] wybrało
`ksef2` i wskazało cztery rzeczy, które mimo istnienia SDK muszą mieszkać
u nas: retry, walidacja okna dat, `pageSize` i tablica translacji
kierunku. Pierwsze wywołania już były — `verify` działa — więc moment,
o którym mówiło [D-002], nastąpił.

Zgłoszenie #35 jest korzeniem łańcucha blokad: `#36`–`#42` nie mogą
zacząć, dopóki nie wiadomo, na czym operują. Dopóki port nie istnieje,
każde z tych zadań rozstrzygałoby samo — w pierwszym miejscu, które tego
potrzebuje — gdzie mieszka retry i co jest typem dziedzinowym. To jest
decyzja o **strukturze kodu**, więc zgodnie z [D-036] należy do
`docs/adr/`, a nie do `decisions.md`.

Ograniczenia, z których to wynika, są zapisane gdzie indziej i nie są tu
powtarzane, tylko przywoływane: limit 20 zapytań metadanych na godzinę
[D-010], 20 eksportów na godzinę [D-031 §5], 64 pobrania treści na
godzinę [D-031 §1], oraz to, że Ministerstwo Finansów rejestruje
przekroczenia i wydłuża blokadę przy powtórzeniach.

## Decyzja

`src/ksef_mcp/ksef_port.py` przestaje być modułem i staje się pakietem
`src/ksef_mcp/ksef_port/`. Ścieżka importu zostaje ta sama, więc `cli.py`
się nie zmienia — a port ma gdzie urosnąć, zamiast powstać obok jako
drugi, konkurencyjny.

| Moduł | Zawartość | Widzi `ksef2` |
|---|---|---|
| `types.py` | typy dziedzinowe | nie |
| `errors.py` | hierarchia błędów | nie |
| `protocol.py` | `KsefPort`, `KsefSession` | nie |
| `budget.py` | licznik budżetu zapytań | nie |
| `retry.py` | polityka ponawiania | nie |
| `connection.py` | serwis aplikacyjny `check_connection` | nie |
| `adapter.py` | implementacja portu na `ksef2` | **tak, jedyny** |

Kryterium „tooli nie widzą typów SDK" jest więc sprawdzalne mechanicznie:
`ksef2` wolno zaimportować wyłącznie w `adapter.py`. Test tego pilnuje.

### Identyfikatory po angielsku, mimo polskich nazw w słowniku

`glossary.md` i `model.md` mapują pojęcia na `NumerKSeF`, `Okres`,
`KierunekFaktur`. `CLAUDE.md` wymaga jednak kodu po angielsku, a zastany
`ksef_port.py` był już tak napisany. Wybrane: identyfikatory angielskie
plus jawna tabela odwzorowania — zamiast dwóch języków w jednym module
albo cichego rozjazdu między słownikiem a kodem.

| Słownik (`glossary.md`) | Typ w kodzie |
|---|---|
| `NumerKSeF` | `KsefNumber` |
| `Okres` (z `DateType`) | `Period`, `DateType` |
| `PunktKontynuacji` | `ContinuationPoint` |
| `MetadaneFaktury` | `InvoiceMetadata` |
| `KierunekFaktur` | `InvoiceDirection`, `WIRE_SUBJECT_TYPES` |
| `KontekstPodmiotu` | `SubjectScope`, `Nip` (w `paths.py`) |
| `Środowisko` | `KsefEnvironment` (istniejący, w `config.py`) |
| `Poświadczenie` | `Credential` (protokół, w `types.py`) |

Wiersz `KontekstPodmiotu` wskazywał wcześniej `SubjectContext` w
`types.py` — typ, którego nie wołał żaden moduł produkcyjny. Odwzorowanie
było więc obietnicą, nie opisem: tożsamość podmiotu naprawdę żyła jako
goły `nip: str`, powielona w sześciu magazynach (GH-111, GH-113). Pojęcie
mieszka teraz w `paths.py` i jest używane, a martwy typ został usunięty.

Do `paths.py`, a nie do portu, bo kontekst podmiotu odpowiada na pytanie
*gdzie na dysku*, którego port z założenia nie zadaje. Kierunek importu
też na to nie pozwala: `paths` czyta `KsefEnvironment` z `config`, a
`config` jest liściem, po który sięga cała reszta — odwrócenie tej
zależności zamknęłoby cykl przez `ksef_port/__init__.py`.

Wiersz `Poświadczenie` jest nowy i pilnuje granicy, której ten ADR strzeże
od początku: token przyjeżdża jako argument, a keyring zostaje powyżej
portu. `Credential` to protokół strukturalny, więc `ksef_port` nazywa typ
tokenu, nie importując `token_store` ani `keyring` (GH-115).

### Kształt portu

```python
class KsefPort(Protocol):
    environment: KsefEnvironment

    def session(self, *, nip: str, token: str) -> AbstractContextManager[KsefSession]: ...


class KsefSession(Protocol):
    def read_limits(self) -> KsefLimits: ...
    def query_metadata(self, *, period: Period, direction: InvoiceDirection) -> MetadataPage: ...
    def start_export(self, *, period: Period, direction: InvoiceDirection) -> ExportHandle: ...
    def check_export(self, *, handle: ExportHandle) -> ExportStatus: ...
    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes: ...
    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes: ...
```

Token jest **argumentem**, nie odczytem z keyringu. Port sięgający sam po
sekret jest niewywoływalny bez niego, a na zablokowanej kolekcji taki
odczyt otwiera okno, które wiesza transport stdio [D-004]. Keyring
zostaje w `token_store.py`.

`fetch_part` i `download_invoice` oddają **surowe bajty**. Nic ich nie
dotyka przed zapisem `temp → rename` [D-006].

`fetch_part` idzie przy tym prosto po `httpx`, z pominięciem SDK. Część
paczki leży w zewnętrznym magazynie pod podpisanym URL-em, nie niesie
poświadczenia KSeF, a `ksef2` wystawia ten transport wyłącznie za
prywatnym atrybutem — pożyczanie prywatnego pola to dokładnie to
sprzężenie, przed którym ten port ma bronić.

### Czego SDK nie pozwala zrobić: pominąć górnej granicy okna

[D-031 §4] zaleca **pomijać `DateRange.To`**, żeby KSeF sam zbudował
możliwie dużą spójną paczkę. `InvoicesFilter` w `ksef2` ma jednak
`date_to` z domyślną wartością „teraz" i nie ma sposobu, by to pole
wyłączyć. Port realizuje więc najbliższą rzecz, która działa: przy
`Period.date_to is None` wysyła „teraz" **razem z**
`restrict_to_permanent_storage_hwm_date=True`, czyli KSeF i tak zatrzymuje
paczkę na punkcie kompletności i sam wybiera okno. Skutek jest ten, o
który chodziło; zapisane, bo litera zalecenia nie jest spełniona.

### Hierarchia błędów: pięć rodzin pod jednym korzeniem

```
KsefPortError(RuntimeError)
├── KsefRequestRejected        nasza walidacja; nic nie poleciało
├── KsefAuthenticationFailed   KSeF odpowiedział: nie ty
├── KsefRateLimited            429; niesie retry_after
├── KsefRefused                KSeF odpowiedział błędem
└── KsefUnreachable            surowe httpx sprzed odpowiedzi
```

Trzy rodziny wejściowe sprowadzone do jednej wyjściowej: `KSeFException`
z SDK, surowe `httpx` (DNS, connect — **bez wspólnej bazy** z poprzednią)
oraz błędy własne portu. Bez tego każde zadanie zależne łapałoby co
innego.

**Zmiana wobec stanu zastanego:** `KSeFException` mapuje się teraz na
`KsefRefused`, wcześniej na `KsefUnreachable`. Sklejenie „nie ma sieci"
z „KSeF odmówił" odbiera zadaniu zależnemu rozróżnienie, od którego
zależy decyzja operacyjna: pierwsze wolno ponowić od razu, drugiego nie.

### Polityka ponawiania

`RetryPolicy(attempts, max_wait, sleep)` czeka **wyłącznie** tyle, ile
podał `KSeFRateLimitError.retry_after`. Brak nagłówka albo żądanie
dłuższe niż `max_wait` → wyjątek leci do wołającego, bez czekania.

Domyślną jest `NO_AUTOMATIC_RETRY` (`attempts=1`). Powód jest podwójny:
zgadywanie czasu oczekiwania to dokładnie ten wzorzec, za który MF
wydłuża blokadę, a agent czekający minutę w środku wywołania narzędzia
wygląda na zawieszonego.

Polityka jest podłączona przez `GuardedSession`, która owija sesję portu
w miejscu jej otwarcia (GH-100). Wcześniej miała testy i zero
wywołujących w produkcji — faktyczne zachowanie brało się z
`SINGLE_ATTEMPT` w adapterze, więc klasa umiejąca uszanować
`Retry-After` nigdy nie działała. Podłączenie nie zmienia ruchu do
KSeF-u ani o jedno żądanie: domyślna wciąż wysyła jedną próbę. Zmienia
się to, że wołający, który chce uszanować czas podany przez serwer, ma
gdzie to powiedzieć.

### Bezpiecznik odmów

`GuardedSession` prowadzi też licznik **kolejnych odmów** z KSeF-u
(GH-99). Budżet chroni przed sukcesem zbyt częstym; seria odmów to
osobne zagrożenie i dokładnie ten wzorzec, który MF analizuje jako próbę
obchodzenia limitów [D-031 §8]. Po pięciu odmowach pod rząd serwer
odmawia lokalnie i podaje moment, od którego wolno spróbować — późniejszy
z dwóch: czasu podanego przez KSeF w `Retry-After` oraz własnej godziny
karencji. To zatrzymanie, nie ponowienie, więc krótki `Retry-After` nie
skraca bezpiecznika; wydłużyć go może.

Za odmowę liczy się `KsefRateLimited`, `KsefAuthenticationFailed` i
`KsefRefused` — trzy sposoby, na które KSeF mówi „nie". `KsefUnreachable`
nie, bo odpowiedź w ogóle nie powstała, a zamykanie podmiotu za cudzą
awarię sieci byłoby gorsze od problemu. Pobranie części paczki idzie z
pominięciem bezpiecznika: to podpisany URL do zewnętrznego magazynu, bez
poświadczeń KSeF-u i poza jakimkolwiek limitem.

### Licznik budżetu, i sprostowanie co do endpointu

`QueryBudget` liczy przesuwane okno godzinowe osobno dla
`METADATA_QUERY`, `EXPORT`, `EXPORT_STATUS` i `INVOICE_DOWNLOAD`, a sufit
bierze z KSeF — nigdy z wartości domyślnych, bo MF podnosi limity na
wniosek [D-031 §8]. Limit, którego KSeF nie podał, jest `None` i **nie
jest zmyślany**: taka operacja nie jest blokowana, bo blokada na
wymyślonej liczbie jest gorsza niż jej brak.

**`GET /limits/context` nie zwraca limitów, o które chodziło.**
Zgłoszenie #35 oraz [D-031 §8] wskazują ten endpoint jako źródło dla
licznika budżetu. Odczyt zainstalowanego `ksef2` pokazuje co innego:

| Endpoint | Metoda SDK | Co zwraca |
|---|---|---|
| `GET /limits/context` | `limits.get_context_limits()` | **rozmiary sesji** — maks. 1 MB faktury, 3 MB z załącznikiem, 10 000 faktur w sesji |
| `GET /rate-limits` | `limits.get_api_rate_limits()` | **limity na sekundę / minutę / godzinę** per rodzina operacji |

Liczby z `/limits/context` to dokładnie te z [D-031 §9], czyli
ograniczenia wolumenu, a nie tempa. Godzinowe dwadzieścia zapytań, o
które chodzi licznikowi, siedzi pod `/rate-limits`.

Port czyta więc **oba** i oddaje je jako `KsefLimits(rates=…,
ceilings=…)`: `rates` zasila licznik budżetu, `ceilings` będzie potrzebne
`#36` i `#37` do decyzji o rozmiarze paczki. Sprostowanie, a nie
odstępstwo — intencja [D-031 §8] („odpytać rzeczywiste limity zamiast
zakładać") jest zrealizowana; pomylony był numer endpointu.

### Dlaczego `Protocol`, a nie klasa bazowa?

| Podejście | Zalety | Wady |
|---|---|---|
| **`typing.Protocol`** | podstawienie nie wymaga dziedziczenia, więc test dowodzący wymienności nie może oszukać, dziedzicząc implementację; `runtime_checkable` daje `isinstance` | `isinstance` sprawdza obecność metod, nie sygnatury |
| `abc.ABC` | wymusza komplet metod przy imporcie | atrapa dziedziczy po tym samym korzeniu co implementacja, więc test wymienności dowodzi mniej |
| Bez szwu, `ksef2` wprost w toolach | najmniej kodu | dokładnie to, czemu #35 ma zapobiec |

### Dlaczego `Decimal`, a nie `float`?

Kwoty brutto/netto/VAT trafiają do zestawienia CSV [D-023] i do
porównania archiwum z rejestrem, które w badaniu [D-025] wykryło
brakującą fakturę. Porównanie kwot na `float` daje fałszywe rozbieżności
na groszach. Zastany `InvoiceSummary` używał `float`; to się zmienia.

## Uzasadnienie

Kryterium akceptacji #35 brzmi: zadania `#36`–`#42` mają zacząć **bez
zgadywania**. Dlatego port jest zaprojektowany szerzej, niż wymaga tego
pierwszy konsument: operacje eksportu i pobierania części istnieją, choć
`verify` potrzebuje wyłącznie uwierzytelnienia i jednego zapytania o
metadane. Alternatywa — dodawać operacje w miarę potrzeb — oznaczałaby,
że kształt portu ustali przypadkowo pierwsze zadanie, które go dotknie,
czyli dokładnie ten tryb awarii, który #35 opisuje.

Przyjęty kompromis: część operacji nie ma dziś produkcyjnego wołającego.
Pokryte są testami kontraktowymi, nie ruchem, więc pierwsze prawdziwe
wywołanie `#36` może wykryć rozjazd z API. To cena za odblokowanie
łańcucha; alternatywa blokuje go dalej.

## Konsekwencje

**Pozytywne:**

- `#36`–`#42` mają nazwane typy i sygnatury; żadne z nich nie rozstrzyga
  samo, gdzie mieszka retry, walidacja okna i `pageSize`.
- Wymiana klienta to zmiana `adapter.py` i niczego więcej — dowiedzione
  testem, w którym ten sam serwis aplikacyjny przechodzi przeciw
  implementacji na `ksef2` i przeciw atrapie w pamięci.
- Okno dat i numer KSeF są odrzucane **przed** wysłaniem, więc błąd
  użytkownika nie kosztuje zapytania z deficytowego budżetu.

**Negatywne:**

- Operacje eksportu nie mają dziś konsumenta, więc ich zgodność z API
  potwierdzi dopiero `#36`.
- Podwójne nazewnictwo (polskie w słowniku, angielskie w kodzie) wymaga
  utrzymywania tabeli odwzorowania. Rozjedzie się cicho, jeśli nikt jej
  nie zaktualizuje przy zmianie nazwy typu.
- `Decimal` zamiast `float` zmienia typ pól publicznych zastanego
  `InvoiceSummary`, który znika na rzecz `InvoiceMetadata`.

## Powiązane

- [ADR-100](100-decision-provenance-and-adversarial-re-derivation.md) —
  proweniencja, wg której ten dokument jest oznaczony jako autorstwa agenta
- [#35](https://github.com/Dev10x-Guru/ksef-mcp/issues/35) — zgłoszenie,
  wraz z kryteriami akceptacji wywiedzionymi z zadań zależnych
- `docs/domain/decisions.md` [D-017] — wybór `ksef2` i lista tego, co mimo
  SDK należy do portu; ten ADR jest jego realizacją w strukturze kodu
