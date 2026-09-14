# Dziennik zmian

Format wzorowany na [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/);
wersjonowanie zgodne z [SemVer](https://semver.org/lang/pl/).

Sekcję `## Bez wydania` prowadzi człowiek. `bin/release.py` przenosi jej
treść pod nowy numer wersji i odmawia wydania, gdy sekcja jest pusta —
wydanie bez opisu zmian jest gorsze niż brak dziennika, bo wygląda na
udokumentowane.

## Bez wydania

### Dodane

- Gotowa paczka eksportu jest odczytywana do końca: części pobierane
  z osobnych adresów, odszyfrowane kluczem AES-256 z inicjalizacji,
  złożone i rozpakowane. Podatnik dostaje faktury, a nie zaszyfrowany
  ZIP w kawałkach ([GH-37]).
- Nieudane pobranie którejkolwiek części nie przybliża okresu do
  „kompletnego" — paczka zostaje zapisana razem z kluczem i dokańcza ją
  kolejny przebieg, bez wydawania drugiego z dwudziestu eksportów na
  godzinę ([GH-37]).

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

- Zanim narzędzie sięgnie po token, sprawdza, czy magazyn haseł jest
  odblokowany — i gdy nie jest, mówi to wprost zamiast otwierać okno
  z prośbą o hasło. Takie okno zawieszało całą rozmowę z agentem, bo
  pojawiało się w środku czynności wyglądającej na zwykły odczyt
  ([GH-33]).

### Zmienione

- Gdy KSeF odmówi, `verify` podaje w jednym zdaniu, **którego podmiotu**
  i **którego środowiska** dotyczy odmowa oraz z jakiego powodu. Przy
  dwóch skonfigurowanych NIP-ach samo „token odrzucony" kazało zgadywać
  ([GH-33]).
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

### Bezpieczeństwo

- Klucz, którym zaszyfrowana jest paczka, znika w chwili trafienia
  faktur do archiwum — nie zostaje na dysku ani chwili dłużej i nie
  czeka na osobne sprzątanie. Eksport odrzucony przez KSeF również nie
  zachowuje klucza ([GH-37]).
- Paczka niezgodna z tym, co KSeF o niej podał — rozmiarem albo skrótem
  którejkolwiek części — nie jest rozpakowywana. Nie trafi też do
  archiwum plik, którego nazwa wskazuje poza paczkę ([GH-37]).

- Faktury z pobranej paczki lądują w archiwum pod numerem KSeF —
  `<NumerKSeF>.xml` — w podkatalogu osobnym dla każdego podmiotu i
  środowiska. Biuro rachunkowe nie pomiesza więc faktur dwóch klientów,
  a plik da się przekazać i zaimportować bez zgadywania, co w nim jest
  ([GH-38]).
- Powtórzona synchronizacja tego samego okresu nie tworzy duplikatów.
  Rozpoznanie idzie po numerze KSeF z manifestu paczki, nie po nazwie
  pliku — nazwy potrafiły dawać fałszywe wyniki, numer nie ([GH-38]).
- Faktura już zapisana nie jest po cichu nadpisywana: narzędzie mówi
  wprost, których numerów już nie pobierało ponownie ([GH-38]).
- Pamięć o tym, co już pobrano, leży w osobnym pliku obok faktur.
  Podatnik może więc skasować same faktury — dla oszczędności miejsca
  albo z powodów ochrony danych — a kolejna synchronizacja i tak nie
  ściągnie ich po raz drugi ([GH-38]).
- Paczka, której manifest nie wiąże numeru KSeF z plikiem, nie jest
  archiwizowana wcale, zamiast trafić do archiwum pod zgadniętą nazwą
  ([GH-38]).

- Pytanie o ten sam okres drugi raz nie kosztuje ani jednego z dwudziestu
  zapytań na godzinę. Odpowiedź na zamknięty przedział dat jest zapisywana
  na dysku razem ze znacznikiem chwili, w której naprawdę zapłacono za nią
  budżetem, i przy powtórzeniu wraca stamtąd — także po restarcie serwera
  ([GH-39]).
- Zapis jest osobny dla każdego typu podmiotu, więc odpowiedź na pytanie
  „co sprzedałem we wrześniu" nie zostanie podana jako odpowiedź na „co
  kupiłem" — ta sama firma bywa sprzedawcą na jednej fakturze i nabywcą
  na następnej ([GH-39]).
- Cache leży w katalogu podręcznym systemu, osobno od katalogu danych
  z punktami kontynuacji, indeksem deduplikacji i archiwum. Skasowanie
  katalogu podręcznego — ręcznie albo przez czyszczarkę dysku — kosztuje
  jedno ponowne odpytanie, nigdy pełnej resynchronizacji ([GH-39]).
- Uszkodzony albo obcięty wpis w cache'u nie jest błędem, tylko brakiem
  trafienia: serwer po prostu pyta KSeF raz jeszcze, zamiast odmówić
  odpowiedzi ([GH-39]).

- Jedno wywołanie `synchronise_invoices` kończy się fakturami na dysku.
  Paczka, którą KSeF ogłosi gotową, jest w tym samym przebiegu pobrana,
  odszyfrowana i zapisana jako `<NumerKSeF>.xml` w archiwum podmiotu —
  wcześniej przebieg kończył się wiedzą, że paczka czeka ([GH-57]).
- Odpowiedź narzędzia mówi, gdzie faktury wylądowały i które numery
  KSeF przyszły, a które podmiot już miał. Treści faktury nie niesie
  nigdy: dokument FA(2)/FA(3) zawiera dane osobowe kontrahenta, więc
  narzędzie nazywa plik i go nie otwiera ([GH-57]).
- Drugie wywołanie na tym samym oknie nie pobiera paczki ponownie i nie
  nadpisuje żadnej faktury — numery, które podmiot już ma, wracają jako
  „już posiadane" ([GH-57]).
- Nieudane pobranie albo nieudany zapis zostawia paczkę na dysku razem
  z kluczem i dokańcza ją kolejny przebieg. Punkt kontynuacji nie cofa
  się z tego powodu: przesuwa go to, co potwierdził KSeF, a nie to, czy
  temu przebiegowi udało się zapisać pliki ([GH-57]).

[GH-33]: https://github.com/Dev10x-Guru/ksef-mcp/issues/33
[GH-34]: https://github.com/Dev10x-Guru/ksef-mcp/issues/34
[GH-35]: https://github.com/Dev10x-Guru/ksef-mcp/issues/35
[GH-37]: https://github.com/Dev10x-Guru/ksef-mcp/issues/37
[GH-38]: https://github.com/Dev10x-Guru/ksef-mcp/issues/38
[GH-39]: https://github.com/Dev10x-Guru/ksef-mcp/issues/39
[GH-57]: https://github.com/Dev10x-Guru/ksef-mcp/issues/57

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
