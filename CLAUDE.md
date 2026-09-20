# ksef-mcp

Serwer Model Context Protocol (MCP) dla polskiego KSeF — Krajowego
Systemu e-Faktur. Repozytorium publiczne, licencja AGPL-3.0.

## Język projektu (obowiązuje zawsze)

**Dokumentacja, rozmowa i wyniki pracy — po polsku. Kod — po
angielsku.**

Zasada dotyczy każdej odpowiedzi, nie tylko plików w repozytorium:

- **Odpowiadaj po polsku nawet wtedy, gdy polecenie było po
  angielsku.** Język polecenia nie zmienia języka odpowiedzi.
- **Poprawną polszczyzną, nie polglishem.** Człowiek może sobie
  pozwolić na skróty i kalki; agent nie. Jeśli w poleceniu padnie
  angielski albo spolszczony potworek, zmapuj go na poprawne polskie
  sformułowanie (patrz słownik niżej).
- **Wyniki zapisujemy po polsku, nawet gdy instrukcja narzędzia jest
  po angielsku.** Instrukcje umiejętności Dev10x są angielskie — to nie
  zmienia języka tego, co powstaje: Job Story, treści commitów, opisy
  PR-ów, komentarze przeglądu, dokumentacja.

**Po angielsku zostaje kod i wszystko, co odczytuje maszyna:** nazwy
identyfikatorów, docstringi (trafiają do klientów MCP jako opis
narzędzia), komunikaty logów, nazwy plików i ścieżki, klucze JSON,
nazwy zmiennych środowiskowych, etykiety nagłówków ADR oraz nazwy
agentów przeglądu.

### Słownik

Źródło rozstrzygające: [polski-w-it](https://github.com/nurkiewicz/polski-w-it).
Zasada autora: *jeśli musisz się zastanawiać, jakie jest polskie
tłumaczenie danego pojęcia, to prawdopodobnie ono nie istnieje*.

| angielski | polski | odradzane |
|---|---|---|
| code review | przegląd kodu | rewiu |
| review (czasownik) | przejrzeć | rewiułować |
| deploy | wdrożyć, wdrożenie | zdiplojować |
| build | zbudować | zbildować |
| release | wydanie, wersja | rilisować |
| bug | błąd | bug |
| fix | poprawka, poprawić | fiks, fiksnąć |
| feature | funkcja | ficzer, funkcjonalność |
| dependency | zależność | dependencja |
| default | domyślnie | difoltowo |
| issue (GitHub) | zgłoszenie | isiu |
| ticket | zadanie | tiket |
| plugin | wtyczka | plugin |
| template | szablon | templatka |
| agents | agenty | agenci |
| roadmap | plan prac | mapa drogowa |
| metric | miara | metryka |
| estimate | oszacowanie | estymata |
| scope | zakres | skoup |
| workaround | obejście | workaround |
| draft | szkic, wersja robocza | draft |
| permission | uprawnienie | permisja |
| credentials | poświadczenia | kredencjały |

**Zostają po angielsku**, bo nie mają dobrego polskiego odpowiednika:
*branch*, *commit*, *merge*, *push*, *pull*, *pull request*,
*pipeline*, *framework*. Odmieniaj je po polsku (*commita*, *po
merge'u*, *na branchu*), ale nie wymyślaj tłumaczeń ani nie twórz
polglishowych czasowników w rodzaju „zmergować" czy „zapushować" —
przeformułuj zdanie.

Dwa wpisy powyżej to decyzje projektu, nie cytat ze źródła:
*zgłoszenie* dla GitHub issue (słownik proponuje „problem", co w tym
kontekście myli) oraz *merge* jako rzeczownik, bo słownik odrzuca
„scalanie", a polglishowy czasownik wyklucza zasada powyżej.

## Układ katalogów

| Katalog          | Przeznaczenie                                   |
|------------------|-------------------------------------------------|
| `src/ksef_mcp/`  | Pakiet: serwer MCP, klient KSeF, narzędzia       |
| `tests/`         | Testy (odzwierciedlają `src/ksef_mcp/`)          |
| `bin/`           | Skrypty pomocnicze i skrypty CI                  |
| `docs/`          | Dokumentacja projektu, ADR-y                     |
| `references/`    | Wspólne opracowania (git, przegląd, JTBD)        |
| `.claude/rules/` | Kierowanie regułami wg ścieżek (`INDEX.md`)      |
| `.claude/agents/`| Agenty przeglądu wyspecjalizowane dziedzinowo    |

## Stos technologiczny

- Python przypięty do dokładnej wersji w `.python-version` oraz
  `requires-python`. CI nie nazywa żadnej wersji samodzielnie — `uv`
  czyta przypięcie, dzięki czemu istnieje jedno źródło prawdy.
- Zależności przez `uv`; zależności deweloperskie to grupa PEP 735
  `[dependency-groups] dev`, nie extra.
- Układ src: `src/ksef_mcp/`.
- Publikacja na PyPI jako `ksef-mcp` (D-015), skrypt konsolowy
  `ksef-mcp = "ksef_mcp.cli:main"`, dzięki czemu działa `uvx ksef-mcp`
  bez przełącznika `--from`. Trzy nazwy zbiegają się dziś do tego
  samego napisu i mimo to zostają rozdzielone w kodzie: dystrybucja
  (`DISTRIBUTION_NAME`, po niej `importlib.metadata` odczytuje
  wersję), serwer MCP (`SERVER_NAME`, tożsamość protokołu, po której
  klucz ma konfiguracja klienta i wpis w keyringu) oraz pakiet
  importu `ksef_mcp`. Sklejenie ich wybucha przy pierwszej zmianie
  nazwy — `PackageNotFoundError` przy imporcie.
- Bez Django, bez GraphQL, bez Celery, bez frameworka frontendowego,
  bez bazy i migracji, bez układu monorepo `apps/`. Ma tak zostać —
  zgłoś każdy PR, który wprowadza którąś z tych rzeczy bez uzgodnienia.

## Uwagi o serwerze MCP

- `mcp` >= 2.2.0 **usunął `FastMCP`**. Używaj
  `from mcp.server import MCPServer`. Nie dodawaj ani nie kopiuj
  przykładów z `FastMCP` do kodu, dokumentacji ani plików reguł.

## Praca z projektem

```bash
uv sync --group dev       # instalacja zależności wraz z narzędziami dev
uv run pytest             # testy wraz z pokryciem
uvx ksef-mcp              # uruchomienie spakowanego serwera przez uvx
uvx ksef-mcp onboarding   # konfiguracja przed pierwszym uruchomieniem
uvx ksef-mcp doctor       # same warunki wstępne, bez sięgania do KSeF
uvx ksef-mcp verify       # odpytuje KSeF — wydaje godzinowy budżet
```

Próg pokrycia: `pyproject.toml` ustawia `fail_under = 100`. Nowy kod nie
może go obniżyć; dotknięty kod zastany ma zostać z pokryciem nie
gorszym niż zastane.

## Zasady bezpieczeństwa KSeF (nienegocjowalne)

- **Nigdy nie wołaj produkcyjnego KSeF** z testów, przykładów ani
  domyślnej konfiguracji. Środowisko domyślne jest wiązane w kodzie,
  nie zmienną środowiskową: `DEFAULT_ENVIRONMENT` (`config.py`)
  wskazuje TEST, a każde wywołanie SDK w `ksef_port/adapter.py`
  przekazuje środowisko jawnym argumentem — nigdy nie polega na
  domyślnym `PRODUCTION` samego SDK. Nie dodawaj zmiennej środowiskowej
  `KSEF_ENV` „dla spójności" — takiej zmiennej dziś nie ma, a jej
  dodanie ominęłoby ten jedyny bezpiecznik. Jeśli wybór środowiska
  przez zmienną środowiskową kiedyś powstanie, to jako świadomie
  zaplanowana zmiana z osobnym ADR-em, nie cichym dopiskiem.
- **Poświadczenia są sekretami.** Jedyną wspieraną zmienną
  środowiskową jest `KSEF_TOKEN` — ścieżka awaryjna z D-004 dla maszyn
  bez magazynu kluczy (headless, WSL, kontener); ma pierwszeństwo przed
  keyringiem, gdy jest ustawiona. `KSEF_NIP` nie istnieje w kodzie —
  podmiot pochodzi z pliku konfiguracyjnego zapisanego przez
  `onboarding`. Żaden z tych sekretów nie może trafić do kodu na
  sztywno, do logów ani do żadnego pliku wersjonowanego w repozytorium
  lub wytwarzanego przez CI.
- **Nigdy nie loguj ani nie zapisuj XML-a faktury.** Dokumenty
  FA(2)/FA(3) zawierają dane osobowe podatnika. W testach używaj
  danych syntetycznych; treść faktury usuwaj z logów i komunikatów
  błędów.
- **Testy dotykające sieci noszą `@pytest.mark.ksef_live`** i muszą być
  odfiltrowane z domyślnego `uv run pytest` (patrz konfiguracja markera
  w `pyproject.toml` i w CI). Test sięgający do prawdziwego punktu
  końcowego KSeF bez tego markera jest błędem, nie udogodnieniem.

Osobne ostrzeżenie o limitach. Środowisko testowe KSeF ma limity
dziesięciokrotnie wyższe niż produkcja, a środowisko demonstracyjne
odpowiada produkcji. Zielony pipeline na środowisku testowym nie
dowodzi więc niczego o zachowaniu na produkcji — ten sam kod trafi tam
na limit dziesięciokrotnie niższy. Osobnym zagrożeniem jest ponawianie
żądań bez odczekania: Ministerstwo Finansów rejestruje przekroczenia
limitów, analizuje wzorce wskazujące na próby ich obchodzenia i może
zablokować podmiot lub zakres adresów IP, a czas blokady rośnie przy
powtórzeniach. Szkodę robi tu sama wytrwałość klienta, nie pojedyncza
operacja.

## Styl kodu

- Adnotacje typów przy każdej definicji; sygnaturę z trzema lub więcej
  parametrami rozpisz na wiele linii.
- Argumenty nazwane zamiast pozycyjnych, gdy wywołanie ma ich więcej
  niż kilka.
- Komentarz to ostateczność — zmieniaj nazwy i strukturę, aż kod
  wyjaśni się sam; dokumentuj *dlaczego*, nigdy *co*.
- Wyjątki własne podnoś blisko źródła i z opisowym komunikatem; dane
  wejściowe sprawdzaj wcześnie i przerywaj głośno, z kontekstem.

## Konwencje gita i PR-ów

- **Branch bazowy**: `main` — w tym repozytorium nie ma `develop`.
- **Nazwa brancha**: `użytkownik/NUMER-ZGŁOSZENIA/krótki-opis`
  (w worktree: `użytkownik/NUMER-ZGŁOSZENIA/nazwa-worktree/krótki-opis`).
- **Format commita**: `<gitmoji> <NUMER-ZGŁOSZENIA> <rezultat JTBD>` —
  nastawiony na rezultat („Umożliwia X"), nie na wykonanie („Dodaje X").
  Treść po polsku; oba wyrażenia regularne gitlinta wymagają jedynie
  emoji na początku, więc język treści jest dowolny.
- **Głos Job Story** (WYMAGANE): trzecia osoba i konkretna rola
  dziedzinowa — księgowa, integrator, podatnik. Nigdy pierwsza osoba
  ani bezosobowy „użytkownik". Szczegóły w `references/git-jtbd.md`.
- Każdy PR musi linkować zgłoszenie w
  `https://github.com/Dev10x-Guru/ksef-mcp/issues`. Gdy takiego nie ma,
  wpisz `Fixes: none — self-motivated refactor`.
- Pełne omówienie: `references/git-commits.md`, `references/git-pr.md`,
  `references/git-jtbd.md`.

## Przegląd kodu

Agenty przeglądu wyspecjalizowane dziedzinowo znajdują się w
`.claude/agents/`. Tablicę kierowania wg wzorców ścieżek zawiera
`.claude/rules/INDEX.md`, a przebieg pracy i kontrole przekrojowe —
`references/review-guidelines.md` oraz
`references/review-checks-common.md`.

Komentarze przeglądu pisz po polsku, zgodnie z zasadą na początku tego
pliku — również wtedy, gdy prompt uruchamiający przegląd jest po
angielsku.
