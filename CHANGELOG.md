# Dziennik zmian

Format wzorowany na [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/);
wersjonowanie zgodne z [SemVer](https://semver.org/lang/pl/).

Sekcję `## Bez wydania` prowadzi człowiek. `bin/release.py` przenosi jej
treść pod nowy numer wersji i odmawia wydania, gdy sekcja jest pusta —
wydanie bez opisu zmian jest gorsze niż brak dziennika, bo wygląda na
udokumentowane.

## Bez wydania

### Dodane

- Komenda `ksef-mcp skill install --scope user|project` zapisująca skill dla
  Claude Code, dzięki czemu agent od razu wie, jak korzystać z serwera:
  że odpowiada z lokalnego archiwum, że synchronizacja ma własny rytm, że
  treść faktury nie wchodzi do kontekstu i że każda odpowiedź nazywa
  środowisko. Zakres podaje się jawnie — `uvx` bywa uruchamiany
  z przypadkowego katalogu, więc cicho wybrane miejsce byłoby ostatnim,
  w którym ktokolwiek szukałby pliku ([GH-34]).
- Aktualizacja skilla pokazuje różnicę wobec zainstalowanego pliku i pyta
  o zgodę przed nadpisaniem, więc własne zmiany nie znikają niezauważone
  ([GH-34]).

- Serwer rozmawia z KSeF przez własną warstwę pośredniczącą, a nie
  bezpośrednio przez bibliotekę klienta. Dla podatnika oznacza to jedno:
  gdy biblioteka się zmieni albo zostanie wymieniona, narzędzia i ich
  odpowiedzi zostaną takie same ([GH-35]).
- Serwer odczytuje z KSeF rzeczywiste limity zapytań i liczy, ile z nich
  już zużył, zamiast zakładać wartości z dokumentacji. Podmiot, któremu
  Ministerstwo podniosło limit, dostaje tyle, ile mu przyznano
  ([GH-35]).

### Zmienione

- Po odmowie z powodu wyczerpanego limitu serwer czeka dokładnie tyle,
  ile podał KSeF, i tylko wtedy, gdy KSeF to podał — a domyślnie nie
  ponawia wcale i oddaje decyzję człowiekowi. Ministerstwo odnotowuje
  przekroczenia limitów i wydłuża blokadę przy powtórzeniach, więc
  wytrwałość klienta szkodzi bardziej niż pojedyncze niepowodzenie
  ([GH-35]).
- Zbyt szerokie okno dat i numer, który nie jest numerem KSeF, są
  odrzucane, zanim cokolwiek poleci do KSeF. Wcześniej kosztowały jedno
  zapytanie z godzinowej puli i wracały jako błąd serwera ([GH-35]).
- Kwoty faktur są liczone dokładnie, a nie w przybliżeniu — porównanie
  archiwum z ewidencją nie pokaże już różnicy o grosz, której nie ma
  ([GH-35]).

[GH-34]: https://github.com/Dev10x-Guru/ksef-mcp/issues/34
[GH-35]: https://github.com/Dev10x-Guru/ksef-mcp/issues/35

## 0.1.1 — 2026-09-13


**Pierwsze wydanie tego pakietu.** Nie ma tu więc zmian zachowania wobec
poprzedniej wersji — nie było poprzedniej. Wszystko poniżej jest nowe
i cała lista opisuje stan początkowy, a nie przyrost.

Narzędzie na tym etapie **konfiguruje się i potwierdza połączenie**.
Wyszukiwanie i pobieranie faktur oraz wizualizacja PDF są planowane;
README nazywa je wprost jako niegotowe.

### Dodane

- Komenda `ksef-mcp onboarding` prowadząca przez konfigurację: kontrola
  Node, jawny wybór magazynu keyringu, wybór środowiska KSeF, zapis tokenu
  i katalog na faktury ([GH-4]).
- Komenda `ksef-mcp doctor` sprawdzająca warunki wstępne bez sięgania
  do KSeF — mówi też, którego `node` używa, co przy przełącznikach wersji
  bywa całą odpowiedzią ([GH-4]).
- Komendy `ksef-mcp token set|delete|status` obsługujące token w keyringu.
  Token wchodzi bez echa, nigdy nie jest argumentem procesu i nigdy nie
  jest pokazywany — tylko długość i końcówka ([GH-4]).
- Komenda `ksef-mcp verify` potwierdzająca połączenie z KSeF i pokazująca
  ostatnie faktury zakupowe. Osobna od onboardingu, bo każde zapytanie
  wydaje godzinowy budżet, także przy przebiegu poprawkowym ([GH-4]).
- Ścieżka awaryjna przez zmienną `KSEF_TOKEN` dla maszyn bez keyringu —
  headless, WSL, kontener. Gdy jest ustawiona, ma pierwszeństwo przed
  keyringiem, a `token status` mówi, z którego źródła token pochodzi
  ([GH-4]).
- `CHANGELOG.md` oraz `bin/release.py`: wydanie idzie jedną komendą,
  pod kontrolami biegnącymi zanim cokolwiek dotrze do PyPI, a każdy krok
  wykrywa, czy już się wykonał, więc zerwana sieć nie zostawia publikacji
  w połowie ([GH-22]).

### Zmienione

- Pakiet nazywa się `ksef-mcp` — tak jak repozytorium, nagłówek README
  i serwer MCP. Instaluje się go przez `uvx ksef-mcp`, bez przełącznika
  `--from`. Konfiguracje klientów MCP pozostają bez zmian, bo `ksef-mcp`
  bez argumentów nadal uruchamia serwer na stdio ([GH-20], [GH-21]).
- Katalog na faktury: gdy tworzymy go sami, dostaje uprawnienia `0700`;
  gdy już istniał, zostawiamy jego uprawnienia bez zmian i mówimy, jakie
  są. Ktoś może wskazać katalog domowy albo współdzielony, a zaostrzanie
  cudzych uprawnień jest zmianą, o którą nie prosił ([GH-4]).

### Bezpieczeństwo

- **Wbudowane ponawianie żądań w `ksef2` ograniczone do jednej próby.**
  Jego okno wynosi cztery sekundy wobec limitów liczonych w minutach, więc
  pętla nie doczekałaby końca limitu — dokładałaby tylko prób do wzorca,
  który Ministerstwo Finansów czyta jako obchodzenie limitu, a czas
  blokady rośnie przy powtórzeniach. Przy odmowie limitu `verify` podaje
  czas oczekiwania i **nie ponawia sam** ([GH-4]).
- **Środowisko KSeF podawane jawnie przy każdym konstruowaniu klienta**,
  ponieważ domyślnym w bibliotece `ksef2` jest produkcja. Domyślnym
  w konfiguracji narzędzia jest środowisko testowe ([GH-4]).
- Plik konfiguracyjny powstaje od razu z trybem `0600`. Wcześniej między
  zapisem treści a nadaniem uprawnień istniało okno, w którym NIP był
  czytelny dla każdego konta na maszynie ([GH-4]).
- **`ksef-mcp token delete` nie twierdzi, że odwołał dostęp**, gdy token
  nadal podaje zmienna `KSEF_TOKEN` — mówi wprost, że trzeba ją wycofać
  z powłoki. Wpis w keyringu jest kluczowany NIP-em, zmienna nie jest,
  więc `verify` ostrzega, że przy niej nie ma gwarancji, do którego
  podmiotu token należy ([GH-4]).
- Wartość tokenu nie trafia do tekstowej reprezentacji obiektu, a NIP nie
  trafia do treści wyjątków — te przeżywają w śladzie stosu, w błędzie
  klienta MCP i w przechwyconym wyjściu ([GH-4]).
- Zawartość faktur nie przechodzi przez narzędzie: `verify` operuje
  wyłącznie na metadanych — numer KSeF, data, sprzedawca, kwota ([GH-4]).

[GH-4]: https://github.com/Dev10x-Guru/ksef-mcp/issues/4
[GH-20]: https://github.com/Dev10x-Guru/ksef-mcp/issues/20
[GH-21]: https://github.com/Dev10x-Guru/ksef-mcp/issues/21
[GH-22]: https://github.com/Dev10x-Guru/ksef-mcp/issues/22
