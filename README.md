[![PyPI](https://img.shields.io/pypi/v/ksef-mcp)](https://pypi.org/project/ksef-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/ksef-mcp)](https://pypi.org/project/ksef-mcp/)
[![Testy](https://github.com/Dev10x-Guru/ksef-mcp/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/Dev10x-Guru/ksef-mcp/actions/workflows/pytest.yml)
[![Pokrycie 100%](https://img.shields.io/badge/pokrycie-100%25-brightgreen)](https://github.com/Dev10x-Guru/ksef-mcp/blob/main/pyproject.toml)
[![Licencja AGPL-3.0](https://img.shields.io/badge/licencja-AGPL--3.0-blue)](https://github.com/Dev10x-Guru/ksef-mcp/blob/main/LICENSE)

[![ksef-mcp — faktury z KSeF prosto do Twojego agenta AI](https://raw.githubusercontent.com/Dev10x-Guru/ksef-mcp/main/site/og-image.png)](https://ksef.dev10x.guru)

**[Strona projektu](https://ksef.dev10x.guru)** ·
[PyPI](https://pypi.org/project/ksef-mcp/) ·
[Instalacja](#instalacja-i-uruchomienie) ·
[Narzędzia MCP](#narzędzia-mcp) ·
[Dziennik zmian](https://github.com/Dev10x-Guru/ksef-mcp/blob/main/CHANGELOG.md)

# ksef-mcp

**Faktury z KSeF prosto do Twojego agenta AI.** Lokalny serwer MCP do
Krajowego Systemu e-Faktur, zbudowany przez Dev10x.Guru: pobiera,
archiwizuje i przegląda faktury zakupowe na Twojej maszynie — dane faktur
nie przechodzą przez żadną usługę pośredniczącą.

## Problem

Faktury zakupowe leżą w KSeF, a praca na nich — znaleźć fakturę za
miesiąc, przekazać zestawienie księgowej, sprawdzić, co przyszło od
ostatniego razu — dzieje się gdzie indziej. Darmowa aplikacja
Ministerstwa nie powie, które faktury są nowe, a zdalne serwery MCP
przepuszczają dokumenty i uwierzytelnienie przez cudzą usługę.

## Jak to rozwiązujemy

- **Lokalnie.** Klient MCP uruchamia serwer przez `uvx`; archiwum XML
  zostaje na Twoim dysku.
- **Token w keyringu.** Poświadczenia trafiają do systemowego magazynu
  kluczy, nie do pliku konfiguracyjnego.
- **Domyślnie środowisko testowe.** Produkcja wymaga świadomego wyboru.
- **Oszczędnie z limitami.** Z narzędzi MCP do KSeF sięga tylko
  synchronizacja; po odmowie z powodu limitu nic nie ponawia samo.

## Instalacja w trzech krokach

1. `uvx ksef-mcp onboarding` — NIP, token KSeF, środowisko, katalog
   roboczy i rejestracja w Claude Code.
2. `uvx ksef-mcp verify` — potwierdza połączenie i pokazuje ostatnie
   faktury.
3. „Zsynchronizuj faktury z KSeF" — poproś agenta; resztę robią narzędzia
   MCP.

Wymaga Pythona 3.13 (pobiera go `uv`). Node jest potrzebny wyłącznie do
PDF-ów — bez niego działa wszystko poza nimi. Szczegóły każdego kroku
w sekcji [Instalacja i uruchomienie](#instalacja-i-uruchomienie).

## Stan projektu

Serwer MCP wystawia dziś sześć narzędzi: `server_info`,
`synchronise_invoices`, `list_recent_invoices`, `export_period_statement`,
`review_new_invoices` i `render_invoice_pdf` — patrz sekcja
[Narzędzia MCP](#narzędzia-mcp) niżej. Wszystko, co poniżej oznaczono jako
🚧 **planowane**, jeszcze nie istnieje w kodzie — opisujemy to, żeby kierunek był
jawny, nie żeby sugerować gotowość.

| Obszar | Stan |
|---|---|
| Pakiet, uruchamianie przez `uvx`, testy | ✅ działa |
| Konfiguracja: `onboarding`, `doctor`, `token` | ✅ działa |
| Potwierdzenie połączenia: `verify` | ✅ działa |
| Synchronizacja i przegląd faktur zakupowych | ✅ działa |
| Wyszukiwanie i pobieranie faktur sprzedażowych | 🚧 planowane |
| Wizualizacja PDF | ✅ działa, wymaga Node |

## Narzędzia MCP

| Narzędzie | Co robi | Sięga do KSeF |
|---|---|---|
| `server_info` | zwraca nazwę i wersję działającego serwera | nie |
| `synchronise_invoices` | pobiera, odszyfrowuje i archiwizuje paczki faktur zakończone przez KSeF od ostatniego uruchomienia | **tak** |
| `list_recent_invoices` | listuje metadane faktur z ostatnich trzydziestu dni, wg typu podmiotu | nie |
| `export_period_statement` | zapisuje faktury zakupowe za wybrany miesiąc jako CSV do przekazania księgowej | nie |
| `review_new_invoices` | pokazuje faktury, które przyszły od ostatniego przeglądu — czego nie potrafi darmowa aplikacja Ministerstwa | nie |
| `render_invoice_pdf` | zapisuje już zarchiwizowaną fakturę jako PDF, bez sięgania do sieci | nie |

`synchronise_invoices` jest jedynym narzędziem z tej listy, które wydaje
godzinowy budżet zapytań do KSeF. Pozostałe pracują na danych już
zarchiwizowanych lokalnie.

## Co ten projekt robi

MVP jest wąski i celowo: **znajdź faktury za wybrany miesiąc i pobierz je.**

Mapa drogowa ma trzy etapy:

1. jeden podmiot, faktury **zakupowe**, wyłącznie odczyt,
2. ten sam podmiot, faktury sprzedażowe,
3. biura rachunkowe z przełączaniem podmiotów.

**Poza zakresem:** wysyłka faktur, korekty, zarządzanie uprawnieniami. To nie jest
zapomniane — to jest świadomie niezbudowane.

## Dla kogo

Odbiorcą jest **użytkownik techniczny**. Instalacja wymaga terminala i
menedżera wersji; w kliencie innym niż Claude Code także ręcznej edycji jego
pliku konfiguracyjnego. Nie udajemy, że jest to instalacja dla osoby
nietechnicznej — dystrybucja dla takiego odbiorcy (instalator albo
rozszerzenie do klienta) to osobny, przyszły etap.

## Wymagania wstępne

**Python 3.13.14** — przypięty dokładnie, nie zakresem (`.python-version` oraz
`requires-python` w `pyproject.toml`). `uv` pobierze ten interpreter sam, więc nie
trzeba instalować go ręcznie.

Zasada obowiązuje w całym projekcie: przypinamy konkretne wersje, nigdy zakresy —
także zależności. Rozjazd interpretera pociąga rozjazd rozwiązanych wersji
bibliotek, a `uvx` i tak rozwiązuje wersję za nas.

**Node 22.17.0 przez [fnm](https://github.com/Schniz/fnm)** — potrzebny
**wyłącznie** do generowania PDF-ów.

```powershell
winget install Schniz.fnm   # Windows
```

⚠️ Sama obecność pliku `.node-version` nie wystarczy. Automatyczne przełączanie
wersji wymaga `fnm env` w profilu powłoki — bez tego plik jest deklaracją bez
egzekucji i można pracować na innej wersji Node, nie wiedząc o tym.

Bez Node narzędzie **działa** i oddaje XML, CSV oraz listę faktur. Traci wyłącznie
PDF. To degradacja, nie awaria.

## Instalacja i uruchomienie

Jedyny krok do świadomego wykonania to jedna komenda:

```bash
uvx ksef-mcp onboarding
```

Przeprowadzi po kolei przez wszystko, co trzeba ustalić przed pierwszym
uruchomieniem, i można ją uruchamiać wielokrotnie — poprawia to, co już
zapisano, zamiast dokładać drugi wpis:

1. sprawdza warunki wstępne: Python, Node (tylko do PDF-ów) i magazyn
   keyringu, w którym zamieszka token;
2. pyta o NIP podmiotu, magazyn tokenu i środowisko KSeF — domyślnie
   testowe, nigdy produkcyjne bez wyraźnego wyboru;
3. prosi o wklejenie tokena KSeF (bez echa) i zapisuje go w keyringu;
4. pyta o katalog roboczy na zestawienia i PDF-y i mówi, gdzie naprawdę
   ląduje archiwum XML — ten katalog obejmuje się kopią zapasową;
5. rejestruje serwer w Claude Code (`claude mcp add`) i proponuje
   instalację skilla, który uczy agenta korzystać z narzędzi;
6. na koniec proponuje `verify` — domyślnie **nie**, bo to jedyny krok,
   który sięga do KSeF i wydaje godzinowy budżet.

Serwer komunikuje się przez stdio i jest uruchamiany przez klienta MCP, nie
ręcznie. Pliku konfiguracyjnego klienta nie trzeba edytować — onboarding
robi to za Ciebie, gdy na maszynie jest komenda `claude`.

### Ścieżka awaryjna: klient bez komendy `claude`

Gdy klient MCP to nie Claude Code (albo `claude` nie ma na `PATH`),
onboarding pomija rejestrację i pokazuje, co wpisać ręcznie. Wpis w pliku
klienta (`.mcp.json`, `claude_desktop_config.json`) wygląda tak:

```json
{
  "mcpServers": {
    "ksef": {
      "command": "uvx",
      "args": ["ksef-mcp"]
    }
  }
}
```

Reszta konfiguracji — NIP, token, środowisko, katalog roboczy — i tak
pochodzi z onboardingu; sam wpis w kliencie tylko uruchamia serwer.

### Dla współtwórcy: wersja z katalogu roboczego

Instalując z PyPI, tego wariantu nie potrzebujesz. Służy do uruchamiania
kodu z lokalnego klonu zamiast opublikowanej wersji:

```json
{
  "mcpServers": {
    "ksef": {
      "command": "uvx",
      "args": ["--from", "/ścieżka/do/ksef-mcp", "ksef-mcp"]
    }
  }
}
```

### Komendy

| Komenda | Co robi | Sięga do KSeF |
|---|---|---|
| `ksef-mcp` | uruchamia serwer MCP na stdio | nie |
| `ksef-mcp onboarding` | konfiguracja przed pierwszym uruchomieniem | nie |
| `ksef-mcp doctor` | same warunki wstępne | nie |
| `ksef-mcp token set\|delete\|status` | token w keyringu | nie |
| `ksef-mcp skill install --scope user\|project` | uczy agenta, jak używać serwera | nie |
| `ksef-mcp purge` | kasuje faktury z archiwum, zachowując indeks deduplikacji | nie |
| `ksef-mcp verify` | potwierdza połączenie i pokazuje ostatnie faktury | **tak** |

`purge` jest bezpiecznikiem bezterminowej retencji: archiwum nie wygasa samo,
więc czyszczenie odbywa się jawną komendą. Ciąć można po podmiocie (`--nip`,
domyślnie ten z konfiguracji), po dacie wpływu do KSeF (`--od`, `--do`) albo po
obu naraz. Zanim cokolwiek zniknie, komenda wypisuje numery KSeF do skasowania
i pyta o zgodę — domyślnie odmawia. Indeks deduplikacji zostaje nietknięty,
więc ponowna synchronizacja nie ściąga skasowanych faktur powtórnie; nietknięte
zostają też punkty kontynuacji i zapis tego, co już przejrzano. Każde
skasowanie zostawia wpis w dzienniku audytu.

`skill install` zapisuje skill dla Claude Code: przy zakresie `user` do
`~/.claude/skills/ksef-mcp/`, przy `project` do `./.claude/skills/ksef-mcp/`
w katalogu wywołania. Zakresu nie przyjmuję domyślnie — `uvx` bywa uruchamiany
z przypadkowego miejsca, więc cicho wybrany katalog byłby ostatnim, w którym
ktokolwiek szukałby pliku. Komenda instaluje i aktualizuje: gdy skill już jest
i różni się od nowego, pokazuje różnicę i pyta, zanim cokolwiek nadpisze —
cudze zmiany nie znikają bez pokazania ich.

Na maszynie bez magazynu keyringu (headless, WSL, kontener) tokenu nie da się
zapisać. Ścieżką awaryjną jest zmienna `KSEF_TOKEN` — gdy jest ustawiona,
ma pierwszeństwo przed keyringiem, a `ksef-mcp token status` powie, z którego
źródła token pochodzi. Pierwszeństwo jest celowe: kto ją eksportuje, robi to
świadomie, a ciche preferowanie keyringu wyglądałoby na zignorowanie eksportu.

Osobnym przypadkiem jest magazyn obecny, ale **zablokowany** — po uśpieniu
maszyny albo po upływie własnego czasu magazynu. Każda komenda dotykająca
tokenu sprawdza wtedy stan blokady i przerywa z instrukcją zamiast otwierać
okno z prośbą o hasło. Takie okno otwiera się w środku czynności wyglądającej
na zwykły odczyt i zawiesza rozmowę z agentem, bo serwer MCP na stdio nie ma
gdzie go pokazać. Stan blokady pokazuje też `ksef-mcp doctor`.

`verify` jest osobną komendą, a nie ostatnim krokiem onboardingu, celowo.
Onboarding uruchamia się wielokrotnie przy poprawianiu konfiguracji, a każde
zapytanie do KSeF zjada godzinowy budżet, którego przekroczenia Ministerstwo
Finansów rejestruje. Budżet wydajemy wtedy, gdy prosisz o to świadomie.

Gdy KSeF odmówi z powodu limitu, `verify` wypisze czas oczekiwania i **nie
ponowi** zapytania samoczynnie. Wbudowane ponawianie w `ksef2` jest z tego
samego powodu ograniczone do jednej próby: jego okno wynosi cztery sekundy,
a rzeczywisty `Retry-After` bywa liczony w minutach, więc pętla nie doczeka
końca limitu — doda tylko prób do wzorca wyglądającego na jego obchodzenie.

## Wizualizacja PDF

Narzędzie MCP `render_invoice_pdf` bierze numer KSeF faktury **już leżącej
w archiwum** i zapisuje ją jako PDF w katalogu roboczym. Nic nie pobiera:
nie zużywa godzinowego budżetu zapytań i działa bez sieci. Faktura, której
jeszcze nie zsynchronizowano, jest odmawiana, a nie dociągana.

PDF-y generuje **oficjalny generator Ministerstwa Finansów**
(`@akmf/ksef-fe-invoice-converter`, licencja MIT), zwendorowany w
`src/ksef_mcp/rendering/vendor/`. Wynik jest tożsamy z tym, co daje portal MF — zweryfikowane
uruchomieniem, nie tylko lekturą kodu. Dokument niesie kod QR, link weryfikacyjny
i numer KSeF.

Bundel **nie pochodzi z rejestru npm** — paczki o tej nazwie tam nie ma.
Serwuje go portal weryfikacyjny MF pod `/client-app/pdf-lib/`; szczegóły
i suma kontrolna w
[`src/ksef_mcp/rendering/vendor/LICENCJA-MF.md`](src/ksef_mcp/rendering/vendor/LICENCJA-MF.md).

Link weryfikacyjny trafia wyłącznie na dokumenty **produkcyjne**. Środowiska
TEST i DEMO nie mają powierzchni weryfikacyjnej, więc PDF stamtąd nie niesie
odsyłacza prowadzącego donikąd.

Generator obsługuje FA(1), FA(2), FA(3), UPO i PEF, ale **przetestowaliśmy wyłącznie
FA(3)**. Pozostałe schematy traktujemy jako niepotwierdzone.

## Bezpieczeństwo danych

Token KSeF nie powinien trafiać do pliku konfiguracyjnego klienta MCP — te pliki są
zwykłym tekstem na dysku. Docelowo serwer będzie czytał poświadczenia z keyringu
systemowego.

Domyślnym środowiskiem jest TEST. Produkcja wymaga świadomego włączenia.

## Limity zapytań

API KSeF ogranicza liczbę zapytań o metadane. Krążące wartości to 8/s, 16/min
i 20/h, ale **nie potwierdziliśmy ich** — nie opierajcie na nich planowania, dopóki
nie zostaną zweryfikowane wobec dokumentacji MF.

## Rozwój

`make help` wypisuje wszystkie dostępne komendy.

```bash
make install              # uv sync --group dev
make hooks                # instalacja hooków pre-commit i commit-msg
make test                 # uv run pytest z pokryciem
make test-live            # testy ksef_live na środowisku testowym KSeF
make lint                 # pre-commit na całym drzewie
make coverage-report      # testy + otwarcie raportu HTML
make upgrade-requirements # uv lock --upgrade
make build-requirements   # eksport do requirements/*.txt
```

Linting i formatowanie idą wyłącznie przez pre-commit — to ta sama ścieżka,
która blokuje commit, więc lokalny przebieg nie rozjeżdża się z hookiem.

Pokrycie testami jest egzekwowane na poziomie 100% (`fail_under` w `pyproject.toml`),
więc lokalny przebieg i CI stosują identyczny próg.

`make test-live` sprawdza kontrakt z prawdziwym rejestrem testowym KSeF, a nie
z atrapą. Poświadczenia testowe trafiają do nieśledzonego `ksef.secrets.env`
(`cp ksef.secrets.env.example ksef.secrets.env`); własna konfiguracja
i token produkcyjny zostają nietknięte, a środowisko jest wpisane na sztywno
jako `test`. Ten sam skrypt, `bin/ksef_live.py`, uruchamia ręczny workflow
`ksef-live.yml` z sekretami środowiska GitHub `ksef-test`. Każdy przebieg
wydaje godzinowy budżet podmiotu testowego.

## Licencje

Projekt jest na licencji **AGPL-3.0-only** — pełny tekst w pliku [LICENSE](LICENSE).

Zwendorowany generator PDF Ministerstwa Finansów jest osobnym artefaktem na
licencji **MIT**. Jego nota licencyjna leży obok niego —
[`src/ksef_mcp/rendering/vendor/LICENCJA-MF.md`](src/ksef_mcp/rendering/vendor/LICENCJA-MF.md)
— i dotyczy wyłącznie tego pliku, nie reszty projektu.

## To nie jest `ksef-mcp.pl`

Istnieje niepowiązany z nami projekt o tej samej nazwie, wystawiony jako
zdalny serwer MCP pod `https://ksef-mcp.pl/mcp` (HTTP + OAuth). Różnica jest
zasadnicza, nie kosmetyczna: tam faktury i uwierzytelnienie przechodzą przez
cudzą usługę, tutaj nie opuszczają Twojej maszyny. Jeśli Twój klient MCP
wystawił Ci adres autoryzacyjny w przeglądarce — to nie był ten serwer. Nasz
uruchamia się lokalnie przez `uvx ksef-mcp` i o nic nie pyta w przeglądarce.

Obie dystrybucje instalują skrypt konsolowy o nazwie `ksef-mcp`, więc przy
obu zainstalowanych wygrywa ta wcześniejsza w `PATH`. Sprawdzisz, co masz,
przez `ksef-mcp doctor` [#75].
