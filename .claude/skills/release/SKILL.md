---
name: release
description: >-
  Use when cutting a ksef-mcp release — someone says „wydaj", „release",
  „tag a version", „make release". Ustala numer, zleca redakcję notatek,
  potwierdza z człowiekiem i uruchamia bin/release.py, a na koniec
  zostawia otwarte zadanie na sprawdzenie publikacji.
  DO NOT TRIGGER when: piszesz notatki bez wydawania (użyj release-notes)
  albo pytasz o stan wydanych wersji.
user-invocable: true
allowed-tools:
  - Bash(git log:*)
  - Bash(git tag:*)
  - Bash(git status:*)
  - Bash(git ls-remote:*)
  - Bash(bin/release.py:*)
  - Bash(make release-dry:*)
  - Bash(make release-fixes:*)
  - Bash(make release-features:*)
  - Bash(make release-major:*)
  - Bash(gh release view:*)
  - Bash(gh run list:*)
  - Bash(curl:*)
  - Read
  - Skill
  - AskUserQuestion
  - TaskCreate
  - TaskUpdate
---

# Wydanie ksef-mcp

**Zapowiedz:** „Używam release, żeby wydać ksef-mcp <numer>."

Ustala numer, dopilnowuje notatek, potwierdza z człowiekiem i uruchamia
`bin/release.py`. Wzorowane na `.claude/skills/release/` z Dev10x-Claude,
ale bez pary gałęzi `develop`/`main` i bez wersji roboczych `.devN` — to
repozytorium ma jedną gałąź.

**Wypchnięcie taga jest nieodwracalne.** Wyzwala `pypi-publish.yml`,
a numeru wydanego na PyPI nie da się użyć ponownie nawet po wycofaniu
paczki ze sprzedaży. To jest punkt bez powrotu i tak go traktuj.

## Orkiestracja

**WYMAGANE: utwórz zadania przy wywołaniu.**

1. `TaskCreate(subject="Ustalić numer wydania", activeForm="Ustalam numer")`
2. `TaskCreate(subject="Dopilnować notatek wydania", activeForm="Sprawdzam notatki")`
3. `TaskCreate(subject="Wydać i otagować", activeForm="Wydaję")`

Zależności sekwencyjne. Zamykaj po kolei.

## Przebieg

### 1. Ustal numer i rodzaj podniesienia

```bash
bin/release.py fixes --dry-run
```

Przebieg próbny przechodzi wszystkie kontrole i podaje numer, nic nie
zmieniając. **Jest to jedyny bezpieczny sposób poznania numeru** — nie
licz go w głowie z `pyproject.toml`, bo przy przerwanym wydaniu skrypt
wznawia poprzedni numer zamiast podnosić.

| Rodzaj zmian | Cel | Skutek z 0.1.0 |
|---|---|---|
| poprawki, drobiazgi | `make release-fixes` | 0.1.1 |
| nowe zdolności | `make release-features` | 0.2.0 |
| zmiana niezgodna wstecz | `make release-major` | 1.0.0 |

Repozytorium jest na `0.x`, więc numery nie niosą jeszcze obietnicy
stabilności. Progiem, który ją wprowadzi, będzie pierwsze `1.0.0`.

Gdy przebieg próbny mówi „wznowienie", poprzednie wydanie przerwano
w połowie. Dokańczaj je, zamiast zaczynać nowe — skrypt sam to rozpozna.

### 2. Dopilnuj notatek

`bin/release.py` odmawia wydania przy pustej sekcji `## Bez wydania`.
Przeczytaj ją i oceń, czy opisuje to, co faktycznie weszło:

```bash
git log --no-merges --format='===%n%s%n%n%b' <ostatni-tag>..HEAD
```

Gdy sekcja jest pusta albo niepełna — **`Skill(release-notes)`**. Ten
skill jej nie redaguje; redagowanie ma własne miejsce, bo tekst wolno
poprawiać wielokrotnie, a publikację robi się raz.

Notatki i wydanie GitHub to dwie różne rzeczy. `bin/release.py` tworzy
wydanie z `--generate-notes`, czyli z listą PR-ów; `CHANGELOG.md` jest
zapisem redagowanym przez człowieka. Oba są oczekiwane.

### 3. Potwierdź, zanim uruchomisz

**WYMAGANE: wywołaj `AskUserQuestion`** (nie zwykły tekst). Pokaż numer
rozstrzygnięty w kroku 1, nie rodzaj podniesienia — człowiek zatwierdza
konkretną wersję, bo to ona jest nieodwracalna.

Opcje: rozstrzygnięty cel (zalecany), cel alternatywny z wyjaśnieniem
skutku, oraz wstrzymanie.

### 4. Wydaj

```bash
CONFIRM_RELEASE=<numer> make release-fixes
```

**Zgoda niesie numer wersji, nie flagę.** Wartość musi zgadzać się
z wydawaną wersją, więc zostawiona w profilu powłoki nie autoryzuje
niczego poza tą jedną. Bez terminala i bez tej zmiennej skrypt odmawia.

Wymaga uprawnień administratora repozytorium: commit z podniesioną
wersją idzie wprost na `main`, a ta gałąź wymaga przeglądu PR-a.

### 5. Sprawdź, czy wydanie dotarło

**Wydanie jest skończone dopiero, gdy wszystkie trzy powierzchnie się
zgadzają.** Tag wypchnięty to nie to samo co paczka na PyPI.

```bash
git ls-remote --tags origin v<numer>
gh release view v<numer>
curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/ksef-mcp/<numer>/json
```

Publikacja przechodzi przez GitHub Actions i trwa dłużej niż tag, więc
`404` z PyPI zaraz po wydaniu znaczy „jeszcze nie", nie „nie udało się".
Podejrzyj przebieg: `gh run list --workflow=pypi-publish.yml --limit 3`.

**WYMAGANE: zostaw otwarte zadanie**, bo tego kroku nie domknie ten
skill — publikacja dzieje się poza nim:

```
TaskCreate(subject="Potwierdzić, że uvx ksef-mcp działa po wydaniu",
    description="PyPI ma wersję <numer>; `uvx ksef-mcp --version` na
    czystym środowisku zwraca ten numer. Dopiero wtedy zdolność
    obiecywana w README jest dostarczona.")
```

## Typowe pomyłki

| Pomyłka | Konsekwencja |
|---|---|
| Numer policzony z `pyproject.toml` zamiast z przebiegu próbnego | Przy przerwanym wydaniu numer jest inny, niż myślisz, a zgoda nie przejdzie |
| `CONFIRM_RELEASE=1` zamiast numeru | Skrypt odmawia; wartość musi wskazywać jedną konkretną wersję |
| Uruchomienie `make` bez zgody w sesji nieinteraktywnej | Skrypt blokuje wydanie — i tak ma być |
| Uznanie wydania za skończone po wypchnięciu taga | Publikacja może odpaść na OIDC; paczki nie ma, a tag sugeruje, że jest |
| Kasowanie taga po nieudanej publikacji | Gdy numer trafił już na PyPI, nie da się go użyć ponownie — podnieś numer, nie kasuj |
| Redagowanie notatek w tym skillu | Powiela `release-notes`; tekst wolno poprawiać, wydanie nie |

## Zobacz też

- `bin/release.py` — kontrole i ich uzasadnienia
- `.claude/skills/release-notes/` — redakcja sekcji „Bez wydania"
- `CHANGELOG.md` — format i zasada prowadzenia sekcji
