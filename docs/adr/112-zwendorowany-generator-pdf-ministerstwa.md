# ADR-112: Zwendorowany artefakt MIT w projekcie AGPL-3.0-only

- **Date:** 2026-09-20
- **Status:** Accepted
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-sonnet-5)
- **Reviewed-by:** —
- **Sources:** zgłoszenie [#83](https://github.com/Dev10x-Guru/ksef-mcp/issues/83);
  zgłoszenie [#42](https://github.com/Dev10x-Guru/ksef-mcp/issues/42)
  (wprowadziło artefakt, scalone); audyt architektury 2026-09-19,
  rozdział 7 („Łańcuch dostaw")
- **Depends-on:** [D-027](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla)

## Kontekst

Zgłoszenie [#42](https://github.com/Dev10x-Guru/ksef-mcp/issues/42)
wprowadziło do repozytorium
`src/ksef_mcp/vendor/ksef-fe-invoice-converter.1.1.39.js` — 3 135 550
bajtów zbudowanego JS-a, wytwarzanego przez Ministerstwo Finansów
i rozpowszechnianego na licencji **MIT**, w projekcie objętym
**AGPL-3.0-only**. Decyzja produktowa — że w ogóle wendorujemy ten
artefakt, zamiast pisać własny renderer albo pobierać PDF z portalu —
żyje w [D-027](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla)
i tam zostaje: przetrwałaby przepisanie projektu od zera, więc jest
dziedzinowa, nie strukturalna ([D-036](../domain/decisions.md#d-036--dwa-rejestry-decyzji-co-gdzie-mieszka)).

Ten ADR opisuje to, co **znika razem z konkretnym kształtem kodu**:
jak licencja MIT współistnieje z AGPL-3.0-only w jednej dystrybucji,
jakie pliki pakietowe to wymuszają i jaka jest procedura aktualizacji
artefaktu. Komentarz na #42 zarezerwował na to numer ADR-107,
zastrzegając, że nie da się tego opisać, zanim wiadomo, jaki artefakt
faktycznie wchodzi do repozytorium — jego licencja, zależności
i sposób budowania są treścią tego dokumentu. Numer 107 zajął
w międzyczasie inny, już scalony ADR
([107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md)); numer 106 jest
zarezerwowany dla szkiców botów. Ten dokument dostał numer **112**.

## Decyzja

### Dlaczego wendorujemy, zamiast pobierać przy budowaniu albo w czasie działania

Portal weryfikacyjny MF (`qr.ksef.mf.gov.pl`) jest **kanałem bez
kontraktu** — nie ma wersjonowanego API ani gwarancji dostępności.
Pobieranie artefaktu przy budowaniu albo w czasie działania uzależniłoby
renderowanie PDF od tego kanału i złamałoby determinizm budowania: ten
sam commit dawałby różny wynik w zależności od tego, co portal serwuje
danego dnia. Zwendorowany plik renderuje **offline** i nie przestaje
działać, gdy Ministerstwo wyda nową wersję — stary bundel jest ważny,
dopóki go nie podmienimy świadomie.

### Skutki licencyjne

MIT pozwala na włączenie artefaktu do projektu objętego licencją
silniej copyleftową (AGPL-3.0-only), pod warunkiem zachowania noty.
Kierunek zgodności jest więc poprawny. Nota mieszka obok artefaktu —
`src/ksef_mcp/vendor/LICENCJA-MF.md` — z jawnym zastrzeżeniem, że
dotyczy **wyłącznie** tego jednego pliku, nie reszty projektu.

Sama obecność noty w repozytorium nie wystarcza: skoro plik trafia do
dystrybycji, nota musi trafić razem z nim. Standard PEP 639 przenosi
listę plików licencyjnych do metadanych paczki poprzez pole
`license-files`. W tym repozytorium pole to, w chwili pisania tego
dokumentu, deklaruje wyłącznie `LICENSE` (treść AGPL) —
`.dist-info/licenses/` zbudowanego koła zawiera więc tylko notę
projektu, nie notę artefaktu obcego. Ta decyzja rozstrzyga, że
`license-files` musi wymieniać **oba** pliki:

```toml
[project]
license-files = ["LICENSE", "src/ksef_mcp/vendor/LICENCJA-MF.md"]
```

### Skutki pakowania

Artefakt dokłada ~3,1 MB do koła (wheel) — świadomie przyjęte w
[D-027](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla)
w zamian za render offline bez kroku budowania. Trzy niezależne
mechanizmy, już wprowadzone (#108), pilnują spójności artefaktu z jego
notą — ten dokument je **opisuje jako istniejące**, nie wprowadza
nowych:

1. **`[tool.hatch.build] artifacts`** w `pyproject.toml` — bundel,
   `package.json` i `LICENCJA-MF.md` są jawnie wymienione, bo
   hatchling pakuje pliki wg tego, co VCS *nie* ignoruje; bez tego
   wpisu przyszły `*.js` w `.gitignore` po cichu wypuściłby zasób
   z dystrybucji bez błędu na żadnym etapie.
2. **`.pre-commit-config.yaml`** wyklucza `src/ksef_mcp/vendor/` z
   hooków, które **przepisują** pliki (`end-of-file-fixer`,
   `trailing-whitespace`, `check-added-large-files`) — jeden z nich
   skrócił bundel o 14 bajtów przy pierwszym uruchomieniu, unieważniając
   sumę SHA-256 z noty, i nikt tego od razu nie zauważył.
3. **`bin/vendor_bundle.py`** — jedyny strażnik zgodności artefaktu ze
   źródłem. Nota jest źródłem prawdy (deklaruje rozmiar i skrót
   SHA-256), skrypt jest egzekutorem: porównuje to, co leży na dysku,
   z tym, co nota zapowiada, i odmawia (kod wyjścia 1) przy
   niezgodności rozmiaru (przerwany transfer — portal MF dwukrotnie
   zrywał połączenie w połowie) albo skrótu (inna zawartość). Wołany
   z hooka pre-commit (`always_run: true`, bo notę można przepisać bez
   dotykania bundla), z CI i z `bin/release.py` jako bramka przed
   krokiem nieodwracalnym. Pokryty testami w `bin/test_vendor_bundle.py`.

### Polityka aktualizacji

Aktualizacja wersji generatora jest dziś **ręczną, niezautomatyzowaną
operacją** — świadomie, z tego samego powodu co wendorowanie samo
w sobie: automat sprawdzający nowość wymagałby odpytywania portalu bez
kontraktu (uzasadnienie pełne:
[D-027, „Polityka aktualizacji zwendorowanego bundla"](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla)).
Wersja żyje w **nazwie pliku** (`ksef-fe-invoice-converter.1.1.39.js`)
i jest czytana stamtąd, nie z osobnej stałej — `BUNDLE_NAME` w
`src/ksef_mcp/pdf.py:35`. `generator_version()` (`pdf.py:146`) wycina
`1.1.39` z tej nazwy przez `removeprefix`/`removesuffix`.

Struktura repozytorium nie daje żadnej bramki, która wymusi
kompletność aktualizacji — jest to **zamknięta lista miejsc do
ręcznego przestawienia**, ustalona przy wdrożeniu [#42] i powtórzona
tu, bo jej brak oznacza, że pierwsza legalna aktualizacja skończy się
czerwonymi testami odczytanymi jako regresja renderowania, zamiast
jako rutynowa synchronizacja stałych:

| Miejsce | Co zmienić |
|---|---|
| `src/ksef_mcp/vendor/ksef-fe-invoice-converter.<stara>.js` | usunąć; podmienić na `<nowa>.js` pobrany z portalu z wznawianiem (`curl -C -`) |
| `src/ksef_mcp/vendor/LICENCJA-MF.md` | przepisać datę pobrania, rozmiar, skrót SHA-256, `Last-Modified` |
| `src/ksef_mcp/pdf.py:35` (`BUNDLE_NAME`) | nowa nazwa pliku |
| `tests/test_pdf.py:24` (`BUNDLE_DIGEST`, stała **testowa**, nie produkcyjna) | nowy skrót SHA-256 |
| `tests/test_pdf.py`, `tests/test_server.py` | literały wersji generatora w asercjach (`"1.1.39"`) |
| `bin/test_release.py`, `bin/test_vendor_bundle.py`, `tests/test_distribution.py` | ścieżki/literały zawierające nazwę pliku z wersją |

`bin/vendor_bundle.py` łapie niespójność między plikiem a notą, ale
**nie łapie** zapomnianej stałej w `pdf.py` ani w testach — to zakres,
którego ten skrypt świadomie nie pokrywa, bo porównuje wyłącznie
artefakt z jego notą, nie notę z resztą kodu.

Po podmianie: **test porównujący z portalem** (wygenerować PDF ze
znanej faktury i porównać rozmiar/treść z tym, co daje portal MF) —
procedura opisana w
[D-027](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla),
wymaga faktury testowej albo przebiegu pod markerem `ksef_live`.

### Dlaczego wendorowanie zamiast alternatyw?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Wybrane: zwendorować zbudowany artefakt, z notą MIT i strażnikiem SHA-256** | Determinizm budowania, render offline, prosta procedura aktualizacji (podmiana pliku + noty) | +3,1 MB do koła; aktualizacja ręczna, bez bramki wymuszającej kompletność listy miejsc |
| Pobierać artefakt przy budowaniu | Zero bajtów w repozytorium na stałe | Zależność sieciowa od kanału bez kontraktu; build niedeterministyczny; brak trybu offline |
| Pobierać w czasie działania (runtime) | Zawsze najnowsza wersja | To samo ryzyko kanału bez kontraktu, przeniesione z czasu budowania na czas użycia — gorsze, bo dotyka użytkownika końcowego, nie tylko CI |
| Napisać własny generator PDF | Pełna kontrola nad licencją i zależnościami | Odrzucone już w D-027 (porównanie z `ksef2` + WeasyPrint) — gorszy wynik, więcej zależności systemowych |

## Uzasadnienie

Koszt wendorowania — 3,1 MB w repozytorium i w kole, plus ręczna
procedura aktualizacji — jest niższy niż koszt zależności od kanału
bez kontraktu, który już raz zerwał transfer w połowie podczas
pobierania noty źródłowej. Trzy mechanizmy pakietowe (artifacts
hatchlinga, wykluczenie w pre-commicie, strażnik SHA-256) domykają
razem ryzyko cichej utraty albo cichego uszkodzenia artefaktu; żaden
z nich osobno by nie wystarczył — hatchling bez jawnej listy artifacts
zgubiłby plik bez błędu, pre-commit bez wykluczenia by go zepsuł,
a brak strażnika pozwoliłby przerwanemu pobraniu przejść przez commit
niezauważenie. Jedyna pozostała luka — `license-files` bez wpisu na
notę MIT — jest tania do zamknięcia (jedna linia w `pyproject.toml`)
i ten ADR ją zamyka razem z opisem.

## Konsekwencje

**Pozytywne:**
- Nota licencyjna MIT trafia do `.dist-info/licenses/` zbudowanego
  koła, nie tylko do repozytorium — atrybucja jest spełniona
  w dystrybucji, nie wyłącznie w źródle.
- Zamknięta lista miejsc do przestawienia przy aktualizacji wersji
  jest teraz spisana w jednym dokumencie, obok mechanizmów, które już
  istnieją i które jej nie zastępują.
- Render PDF pozostaje deterministyczny i działa offline, niezależnie
  od dostępności portalu MF.

**Negatywne:**
- Aktualizacja generatora pozostaje operacją ręczną, bez bramki
  wymuszającej kompletność listy — pominięcie jednego z sześciu
  miejsc jest możliwe i wykryje je dopiero czerwony test.
- Koło rośnie o ~3,1 MB trwale, niezależnie od tego, czy renderowanie
  PDF jest używane.

## Powiązane

- [D-027](../domain/decisions.md#d-027--pdf-generuje-oficjalny-generator-mf-pod-node-z-zwendorowanego-bundla)
  — decyzja produktowa o wendorowaniu generatora MF; ten ADR opisuje
  wyłącznie jej konsekwencje licencyjne i pakietowe.
- [D-036](../domain/decisions.md#d-036--dwa-rejestry-decyzji-co-gdzie-mieszka)
  — reguła rozstrzygająca, dlaczego ta treść jest tutaj, a nie w
  `decisions.md`.
- [#83](https://github.com/Dev10x-Guru/ksef-mcp/issues/83) — zgłoszenie
  źródłowe.
- [#42](https://github.com/Dev10x-Guru/ksef-mcp/issues/42) — wprowadziło
  artefakt do repozytorium (scalone).
