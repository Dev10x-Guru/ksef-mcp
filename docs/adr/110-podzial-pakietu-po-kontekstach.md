# ADR-110: Podział pakietu po kontekstach, nie po warstwach

- **Date:** 2026-09-20
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** `docs/memos/architecture-audit-2026-09-19.md`; zgłoszenia
  [#129](https://github.com/Dev10x-Guru/ksef-mcp/issues/129),
  [#130](https://github.com/Dev10x-Guru/ksef-mcp/issues/130),
  [#131](https://github.com/Dev10x-Guru/ksef-mcp/issues/131),
  [#132](https://github.com/Dev10x-Guru/ksef-mcp/issues/132)
- **Depends-on:** [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)

## Kontekst

`src/ksef_mcp/` trzymał dwadzieścia kilka modułów na jednym poziomie,
obok siebie: prymitywy zapisu na dysk, synchronizację z KSeF-em,
składanie zestawienia, render PDF-a i rejestrację serwera w kliencie
MCP. Przy około sześciu i pół tysiącach linii nazwa pliku była jedynym
sygnałem, do czego moduł należy — kto szukał warstwy trwałości, czytał
listę plików zamiast otworzyć katalog.

Port do KSeF-u (`ksef_port/`) był już wydzielony i pokazywał, że
katalog z własną granicą czyta się lepiej niż płaska lista
([ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)). Pytanie
brzmiało nie *czy* dzielić resztę, lecz *wzdłuż czego*.

## Decyzja

Podział przebiega **po kontekstach**, nie po warstwach. Powstają cztery
pakiety:

| Pakiet | Moduły | Czym jest ten kontekst |
|---|---|---|
| `storage/` | `durability`, `sync_store`, `archive`, `period_cache`, `audit`, `token_store` | co i jak zostaje na dysku |
| `invoices/` | `package`, `synchronisation`, `listing`, `review`, `statement` | co przychodzi z KSeF-u i co z tego widzi księgowa |
| `rendering/` | `pdf`, `node_preflight` oraz zasoby `node/` i `vendor/` | wizualizacja faktury pod Node |
| `setup/` | `skill`, `client` | ustawienie środowiska przed pierwszym uruchomieniem |

Moduły pozostające na szczycie — `config`, `paths`, `errors`,
`messages`, `metadata`, `allowance`, `retention`, `diagnostics`,
`keyring_preflight` oraz adaptery wejścia `server` i `cli` — nie należą
do żadnego z czterech kontekstów: używa ich każdy. Wypchnięcie ich do
piątego pakietu o nazwie w rodzaju `common/` nazwałoby zbiór „reszta",
a to nie jest kontekst.

### Dlaczego kontekst zamiast warstwy?

| Podejście | Zalety | Wady |
|---|---|---|
| **Kontekst (wybrane)** | pliki zmieniające się razem leżą razem; katalog odpowiada na pytanie „o czym to jest" | granica konteksu bywa sporna i przesuwa się wraz z dziedziną |
| Warstwa (`domain/`, `application/`, `infrastructure/`) | znana konwencja, czytelna dla kogoś z zewnątrz | przy tym rozmiarze daje katalogi po trzy-pięć plików i **rozdziela to, co zmienia się razem** |
| Brak podziału (stan zastany) | zero pracy | nazwa pliku jako jedyny sygnał przynależności |

Rozstrzyga drugi wiersz tabeli. Dołożenie pola do listy faktur rusza
dziś `listing`, `review` i `statement` jednym pociągnięciem — warstwy
rozrzuciłyby te trzy pliki po trzech katalogach, a zysk byłby czysto
nominalny, bo warstwa i tak jest widoczna w kierunku importów.

### Granica opłacalności dalszego dzielenia

`invoices/` **nie** dostaje wewnętrznego `domain/` obok
`application/`. Domena to dziś kilkaset linii wplecionych w klasy
przypadków użycia, a osobny katalog na te kilkaset linii byłby
wypełnianiem struktury, nie porządkowaniem jej.

Próg, po którym warto wrócić do tego pytania, jest jawny: **moduł w
pakiecie przekracza ~600 linii albo pojawia się ścieżka zapisu do
KSeF-u**. Zapis wprowadza odpowiedzialność, której dziś nie ma nigdzie
— wysłanie dokumentu jest nieodwracalne — i to on, a nie sam przyrost
linii, uzasadni oddzielenie reguł od przypadków użycia.

### Kolizja nazw `storage`

Moduł `ksef_mcp/storage.py` trzymał prymitywy wyłączności zapisu i
trwałej podmiany ([ADR-107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md),
D-006). Nazwa `storage` nie może naraz oznaczać tego modułu i pakietu,
więc moduł wchodzi do pakietu jako `storage/durability.py`.

Odrzucono re-eksport w `storage/__init__.py`, który zachowałby starą
ścieżkę importu: dawałby jednej nazwie dwa znaczenia i ukryłby zmianę
przed integratorem, którego `CHANGELOG.md` ma o niej uprzedzić.

## Uzasadnienie

Dyscyplina przeprowadzki jest częścią decyzji, nie jej wykonaniem:
**moduł i jego test jadą w tym samym commicie**, a jeden pakiet to
jeden odwracalny commit z zielonym `uv run pytest`. Powód jest
mechaniczny: `--cov=ksef_mcp` mierzy pakiet po nazwie importu, nie po
ścieżce pliku, więc test zostawiony na starym poziomie dałby **zielony
commit ze stuprocentowym pokryciem**. Bramka pokrycia nie złapie tego
nigdy, bo nie patrzy na strukturę katalogów testowych. Weryfikacją
kroku jest pełny przebieg testów **oraz** sprawdzenie, że na starym
poziomie nie został plik testujący przeniesiony moduł.

Pakiet `rendering/` ma dodatkowy warunek: zasoby jadą razem z kodem,
który je zużywa. Wheel bez `render.mjs` albo bez zwendorowanego bundla
instaluje się, importuje i przechodzi testy jednostkowe — psuje się
dopiero w rękach podatnika. Dlatego przeprowadzka obejmuje w tym samym
commicie `artifacts` i `license-files` w `pyproject.toml`, wykluczenia
w `.pre-commit-config.yaml`, `bin/vendor_bundle.py` oraz listę zasobów
w `tests/test_distribution.py`.

## Konsekwencje

**Pozytywne:**

- Katalog odpowiada na pytanie „o czym jest ten kod", zanim otworzy się
  którykolwiek plik.
- Granica kontekstu jest sprawdzalna: import z `invoices/` do
  `storage/` jest oczekiwany, odwrotny wymaga uzasadnienia.
- Adaptery wejścia (`server.py`, `cli.py`) importują teraz z czterech
  nazwanych kontekstów zamiast z dwudziestu modułów, co przygotowuje
  ich własne rozbicie.

**Negatywne:**

- Ścieżki importu to zmiana łamiąca dla każdego, kto importuje ten
  pakiet jako bibliotekę. Nazwa dystrybucji, skrypt konsolowy,
  `SERVER_NAME` i `DISTRIBUTION_NAME` zostają nietknięte, a narzędzia
  MCP nie zmieniają ani nazw, ani sygnatur — koszt ponosi wyłącznie
  integrator sięgający po moduły wprost.
- Zmiana nazwy `storage` → `storage.durability` jest jedyną, która
  oprócz miejsca zmienia też nazwę modułu; wymaga osobnej uwagi przy
  czytaniu dziennika zmian.
- Granica między `invoices/` a `storage/` bywa sporna: `archive`
  przechowuje faktury, więc dałoby się bronić przypisania go do
  `invoices/`. Rozstrzygnięto na rzecz `storage/`, bo `archive`
  odpowiada za trwałość i deduplikację, a nie za treść dokumentu.

## Powiązane

- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — pierwszy
  wydzielony pakiet (`ksef_port/`) i wzorzec, który ten dokument
  uogólnia
- [ADR-107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md) — moduł,
  którego nazwa kolidowała z nazwą pakietu `storage/`
- [ADR-112](112-zwendorowany-generator-pdf-ministerstwa.md) — zasoby
  obce przenoszone razem z `rendering/`
- [#129](https://github.com/Dev10x-Guru/ksef-mcp/issues/129),
  [#130](https://github.com/Dev10x-Guru/ksef-mcp/issues/130),
  [#131](https://github.com/Dev10x-Guru/ksef-mcp/issues/131),
  [#132](https://github.com/Dev10x-Guru/ksef-mcp/issues/132) —
  przeprowadzki wykonane pod tę decyzję
