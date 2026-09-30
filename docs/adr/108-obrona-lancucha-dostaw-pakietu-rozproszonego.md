# ADR-108: Obrona łańcucha dostaw pakietu rozproszonego

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** audyt architektury z 2026-09-19; OWASP Supply Chain Risk Management;
  praktyki Ministerstwa Finansów w zakresie ograniczania transferów (D-027);
  SLSA Framework (Supply chain Levels for Software Artifacts);
  doświadczenie z serii zerwań transferu bundla MF opisane w
  `vendor/LICENCJA-MF.md`

## Kontekst

Pakiet `ksef-mcp` rozpowszechniany jest przez PyPI jako wheel i źródła (sdist).
Integrator pobierający paczkę z sieci publicznej nie widzi ani procesu budowania,
ani pochodzenia artefaktów — widzi wyłącznie to, co wylądowało w jego
`site-packages/`.

Audyt architektury z 2026-09-19 wskazał pięć dziur w ścieżce od commita do
paczki u użytkownika, wspólne dla wszystkich: **żadnej z nich nie widać po
zielonym CI**. Instalacja przechodzi, import przechodzi, testy przechodzą — a
awaria wychodzi u podatnika.

1. **Brakujące zasoby w dystrybucji** (GH-106): shim Node (`vendor/node/`)
   i zwendorowany generator Ministerstwa (`vendor/ksef-fe-invoice-converter.js`)
   do renderowania PDF-a żyły w repozytorium dzięki `.gitignore`, nigdy nie
   byłyż wymienione w manifeście. Wypadnięcie ich z wheela nie psuło ani
   instalacji, ani importu, ani testów — awaria wychodziła dopiero u
   użytkownika przy `render()`.

2. **Niegwarancjonowana tożsamość akcji GitHub** (GH-107): GitHub Actions mogą
   zostać przesunięte przez właściciela akcji w dowolnej chwili (tag
   `release/v1` to gałąź, którą się przesuwa). Akcja uruchamiana z
   `pypa/gh-action-pypi-publish@release/v1` może być czymś innym dziś niż wczoraj.

3. **Obcięty bundel przechodził przez wydanie** (GH-108): Ministerstwo Finansów
   dwukrotnie zerwało transfer generatora (opisane w `vendor/LICENCJA-MF.md`).
   Nota zawierała skrót i rozmiar, ale nic tych liczb nie przeliczało — obcięty
   plik o zapowiedzianej nazwie przeszedłby przez CI, przez `uv build`, do
   dystrybucji i ujawnił się u odbiorcy.

4. **Podatności w zależnościach nie są skanowane** (GH-109): podatność pojawia
   się bez niczyjej zmiany w repozytorium. Green CI na stanie zastanym to nie
   gwarancja, że któryś z pinów już nie jest znany jako niebezpieczny.

5. **Brak weryfikacji pochodzenia budowanego artefaktu** (GH-110): trusted
   publishing gwarantuje, kto plik wgrał, ale nie skąd on pochodzi. Brak
   związku między commitami w repozytorium a dystrybucją na PyPI — integrator
   pobierający wheel musi ufać całemu łańcuchowi „nazwa pliku na PyPI
   = nazwa z tagu Git", bez sprawdzenia.

Rozwiązanie każdego z tych czterech problemów należy do zakresu budowania,
publikowania i CI — czyli do struktury, która trwale będzie częścią procesu
wydawania. To jest decyzja architektoniczna.

## Decyzja

Projekt przyjmuje wielowarstwową strategię obrony łańcucha dostaw. Każda warstwa
chroni przed innym zagrożeniem:

### Warstwa 1: Integralność zasobów w dystrybucji

**Manifesty budzenia (`[tool.hatch.build] artifacts`)** — zasoby trafiające do
wheela wymieniane są jawnie w `pyproject.toml`, nie opierając się na `.gitignore`:

```toml
[tool.hatch.build.targets.wheel]
artifacts = [
    "src/ksef_mcp/node/render.mjs",
    "src/ksef_mcp/vendor/ksef-fe-invoice-converter.*.js",
    "src/ksef_mcp/vendor/package.json",
]
```

Zasoby są również **ładowane przez `importlib.resources`** zamiast bezpośredniego
dostępu do ścieżek, co gwarantuje, że są dostępne w każdym kontekście instalacji
(wheel, sdist, editable, zip).

**Testy dystrybucji** (`tests/test_distribution.py`) budują wheel i sdist, a
następnie zagląda do archiwów, sprawdzając:

- Obecność każdego wymaganego zasobu (parametryzowane po zasobach)
- Rozmiar bundla (chroniący przed obciętymi transferami)
- Zgodność między drzewem źródłowym a artefaktem (testy parametryzowane
  sprawdzają identyczny rozmiar)

Test buduje wheel raz na moduł (drogo, kilkanaście sekund), ale wszystkie
asercje czytają to samo archiwum. Całość wchodzi w `pytest` razem z reszta —
budzenie wheela jest częścią standard suite i nie da się go pominąć.

### Warstwa 2: Weryfikacja spójności bundla Ministerstwa

**`bin/vendor_bundle.py`** — skrypt, którego jedynym zadaniem jest egzekwowanie
tego, co o bundlu mówi `vendor/LICENCJA-MF.md`. Nota zawiera SHA-256 i rozmiar:

```markdown
| SHA-256 | `a1b2c3…` |
| Rozmiar | 5 123 456 bajtów |
```

Skrypt odczytuje notę, wydobywa liczby, porównuje z dysku:

- **Rozmiar** — sprawdzany pierwszy, bo niezgodność size to przerwany transfer,
  którego użytkownik chce poznać wprost
- **Skrót SHA-256** — drugorzędny; niezgodny skrót mówi „to inny plik", ale
  krótszy plik o zapowiedzianej nazwie to już diagnostyka

Nota jest **źródłem prawdy**; skrypt jest egzekutorem i niczego nie proponuje.
Podmienianie generatora polega na aktualizacji pliku i przepisaniu liczb w nocie.

**Skrypt uruchamiany na trzech okazjach:**
1. Hook pre-commit — blokuje commit ze złym bundlem
2. Zadanie CI (część pipelineu testów) — gwarancja w każdym przebiegu
3. `bin/release.py` jako bramka — **przed krokiem nieodwracalnym** (wgraniem na PyPI)

### Warstwa 3: GitHub Actions pinned to SHA

**Wszystkie akcje GitHub Actions są pinned do konkretnego commit SHA**, z
komentarzem nazwy wersji:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
- uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
- uses: actions/attest-build-provenance@96278af6caaf10aea03fd8d33a09a777ca52d62f # v3.2.0
```

SHA jest tym, co runner faktycznie wykonuje; komenarz odsyła do wersji, którą
człowiek powinien przeczytać w `CHANGELOG` akcji. Tag (`release/v1`) lub branch
(`main`) mogą być przesunięte bez wiedzy konsumenta — SHA nie.

Pinowanie jest **zmienne ręcznie**: `bin/dependabot` otwiera PR-y z nową wersją,
którą Dependabot odkryje. Każdy bump pojawia się jawnie.

### Warstwa 4: Skanowanie podatności w zależnościach

**Nowy przebieg `dependency-audit.yml`** wykonuje audyt zamieszonych, pinowanych
zależności:

```yaml
on:
  schedule:
    - cron: "17 5 * * 1"  # poniedziałek, 5:17 UTC
  push:
    branches: [main]
    paths: [pyproject.toml, uv.lock, .github/workflows/dependency-audit.yml]
  pull_request: [...]
```

Audyt:
1. Eksportuje zamieszane zależności z `uv.lock` jako requirements-txt (flaga
   `--frozen` gwarantuje, że audytuję dokładnie ten zestaw, który trafi do
   wheela)
2. Uruchamia `pip-audit --requirement … --no-deps` (flaga `--no-deps` każe
   pip-audit ufać, że plik jest kompletnym domknięciem)
3. Zgłasza podatności, jeśli znalezione

Przebieg **nie jest bramką w pipelineu mergeowania** — wznowienie na harmonogramie
ma budzić opiekuna, a nie blokować merge niezwiązany z zależnościami. Podatność
jest zagrożeniem długoterminowym, nie blokadą na PR.

**Dependabot** skonfigurowany dla:
- Ekosystemu `uv` (nie `pip`, bo pip nie czyta `uv.lock` i zostawił by plik)
- Ekosystemu `github-actions` (do bumowania akcji)

Narzędzia deweloperskie (grupa `[dependency-groups] dev`) są **grupowane** w
jeden PR, żeby bump `ruff` nie przeszkadzał audytowi `cryptography` (która
chroni klucze do eksportowania wrażliwych danych). Runtime zależności otwierają
każdy swój PR.

### Warstwa 5: Atestacja pochodzenia budowanego artefaktu

**Nowe uprawnienie w jobbie `build` pipelineu `pypi-publish.yml`:**

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

Po zbudowaniu wheela i sdista, przebieg uruchamia
`actions/attest-build-provenance@v3.2.0`:

```yaml
- name: Attest build provenance
  uses: actions/attest-build-provenance@... # SHA
  with:
    subject-path: dist/*
```

Atestacja podpisuje skrót artefaktu OIDC token'em zaufanego wydawcy, powiązując
każdą dystrybucję z:
- Tym repozytorium
- Tym workflowem (`pypi-publish`)
- Tym commitem (tag `v*`)
- Tą datą

Integrator pobierający wheel z PyPI może zweryfikować, że wheel na PyPI pochodzi
z tego commita w tym repozytorium, zamiast wierzyć, że „nazwa w PyPI =
nazwa w Git".

**Zapisywane jest *przed* uploaden na PyPI**, dzięki czemu nic nieatestowanego
nie dociera do artefaktu, który `publish` pobiera.

### Decyzje konfiguracyjne

**Gitmoji dla Dependabota**: prefiks `🔧`, nie `⬆️` — gitlint tego ostatniego
nie zna, każdy PR bota byłby czerwony na Git Checks zanim człowiek spojrzałby na
diff.

**`pip-audit` w grupie dev, nie jako flaga `--with`**: scanner przypiętty tylko
do przebiegu to wersja, którą nikt nie podnosi. Przeterminowana baza ostrzeżeń
jest jedyną awarią, którą to zadanie nie umie zgłosić — dlatego pip-audit siedzi
w zamieszanym drzewem (`uv.lock`) razem z resztą, nie jako „opcjonalny skaner,
który sam się aktualizuje".

## Uzasadnienie

Każda warstwa chroni przed **konkretnym zagrożeniem operacyjnym**, które zostało
zaobserwowane lub jest standardowym ryzykiem w dystrybucji:

| Zagrożenie | Warstwa | Mechanizm |
|---|---|---|
| Brakujący zasób w dystrybucji | Warstwa 1 | Jawny manifest + testy |
| Obcięty/uszkodzony bundel | Warstwa 2 | Skrypt egzekucji notatki |
| Zmiana akcji GitHub bez wiedzy | Warstwa 3 | SHA-pinned actions |
| Podatność w nieznanym zależności | Warstwa 4 | `pip-audit` na `uv.lock` |
| Niepowiązany wheel na PyPI | Warstwa 5 | SLSA provenance attestation |

Głębia obrony jest szacowana na podstawie ryzyka:

- **Warstwa 1** (integralność zasobów) — obserwowana: generator Ministerstwa
  zetknął się z przerwanymi transferami (nota je dokumentuje)
- **Warstwa 2** (weryfikacja notatki) — obserwowana: obcięty bundel przeszedł
  przez proces wydania bez alarmów
- **Warstwy 3–5** — standardowe SLSA praktyki dla dystrybuowanych pakietów

Mechanizmy się *uzupełniają*, nie konkurują:
- Warstwa 1 pilnuje, żeby wszystko, co powinno być w paczce, tam jest
- Warstwa 2 pilnuje, żeby to, co tam jest, jest dokładnie tym, co było w drzewie
- Warstwa 3 pilnuje, żeby proces budowania nie został podrobiony
- Warstwa 4 pilnuje, żeby zependencjonowane rzeczy nie były znane jako niebezpieczne
- Warstwa 5 pilnuje, żeby wheel na PyPI pochodził z tego commita, a nie
  z żadnego innego

## Konsekwencje

**Pozytywne:**

- Integrator instalujący z PyPI nie musi już wierzyć łańcuchowi słowa — każde
  nowe wydanie jest powiązane z commitem i workflowem atestacją
- Podatności w zależnościach są skanowane regularnie niezależnie od PR-ów
- Wypadnięcie zasobu z dystrybucji jest błędem na etapie `uv build`, nie u
  użytkownika
- Obcięty bundel jest zatrzymywany pre-commitem, przed PR-em i przed
  wydaniem — trzy szanse na wykrycie
- Żadna akcja GitHub Actions nie może być zmieniona bez jawnego przeglądu SHA

**Negatywne:**

- Każde wydanie wymaga dwóch dodatkowych bramek (sprawdzenie bundla, sprawdzenie
  tag/version) — `bin/release.py` się wydłużył
- Testy dystrybucji (`test_distribution.py`) budują wheel raz na moduł,
  dodając ~15 sekund do suite (akceptowalne, bo rzadko zmienia się zawartość)
- Atestacja wymaga uprawnienia `id-token: write` w jobbie, które musi być
  wdrażane w każdym pipeline'u wydawniczym z pretensjami do SLSA — to sama zmiana
  permisji, ale warte odnotowania
- Skanowanie podatności na harmonogramie może zgłosić alarm w nocy (jest
  celowe — długoterminowe zagrożenie, zasługuje na budzenie właściciela)

## Powiązane

- [GH-106](https://github.com/Dev10x-Guru/ksef-mcp/issues/106) — zasoby nie trafiały do dystrybucji
- [GH-107](https://github.com/Dev10x-Guru/ksef-mcp/issues/107) — akcje GitHub nie były pinned do SHA
- [GH-108](https://github.com/Dev10x-Guru/ksef-mcp/issues/108) — bundel nie był weryfikowany
- [GH-109](https://github.com/Dev10x-Guru/ksef-mcp/issues/109) — podatności nie były skanowane
- [GH-110](https://github.com/Dev10x-Guru/ksef-mcp/issues/110) — brak atestacji pochodzenia
- [#193](https://github.com/Dev10x-Guru/ksef-mcp/pull/193) — PR rozwiązujący wszystkie pięć
- [SLSA Framework](https://slsa.dev) — Supply chain Levels for Software Artifacts (inspiracja)
- `docs/domain/decisions.md` D-027 — obsługa bundla Ministerstwa
