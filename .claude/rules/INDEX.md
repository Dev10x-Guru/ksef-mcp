# Indeks reguł i kierowanie agentami

Tablica kierowania zależna od ścieżki dla `.claude/rules/` i
`.claude/agents/` w tym repozytorium.

## Kontrakt katalogu

- Ten plik jest jedynym źródłem prawdy w `.claude/rules/`.
- Pełna treść reguł znajduje się w `references/*.md`.
- Wyzwalacze i listy kontrolne agentów znajdują się w
  `.claude/agents/*.md`.
- Umiejętności własne projektu znajdują się w `.claude/skills/*/SKILL.md`.

## Wzorce plików -> Agenty -> Odwołania

| Wzorzec pliku | Agent główny | Wymagane odwołania |
|---|---|---|
| `src/ksef_mcp/**/*.py` | `reviewer-generic`, `reviewer-security` | `references/review-checks-common.md` |
| `tests/**/*.py` | `reviewer-test-patterns`, `reviewer-security` | `references/review-checks-common.md` |
| `.github/workflows/**`, `pyproject.toml`, `bin/**` | `reviewer-infra` | `references/review-checks-common.md` |
| `docs/**`, `.claude/**/*.md`, `README.md`, `CLAUDE.md` | `reviewer-docs` | `references/review-checks-common.md` |

## Strategia wczytywania

| Lokalizacja | Kiedy wczytywane | Treść |
|----------|------------|---------|
| `CLAUDE.md` | Każda sesja | Konwencje projektu i podsumowanie stosu technologicznego |
| `.claude/rules/INDEX.md` | Każda sesja | Ta tablica kierowania |
| `references/*.md` | Na żądanie, dopasowane wg wzorca pliku powyżej | Szczegółowe przewodniki po git, przeglądzie, JTBD |

## Kontrole przekrojowe

Zawsze stosuj `references/review-checks-common.md`, w tym jego sekcję
Zagadnienia specyficzne dla KSeF (bezpieczeństwo wywołań produkcyjnych,
obsługa poświadczeń, obsługa XML faktury, izolacja testów `ksef_live`).

## Dokumenty referencyjne (`references/`)

| Plik | Temat | Zakres |
|------|-------|--------|
| `git-commits.md` | Format commita, gitmoji, atomowe commity | Obowiązkowe dla wszystkich commitów |
| `git-pr.md` | Format PR-a, porządkowanie, informacje zwrotne z przeglądu | Obowiązkowe dla wszystkich PR-ów |
| `git-jtbd.md` | Format Job Story, zasady, przykłady | Obowiązkowe dla decyzji JTBD |
| `review-guidelines.md` | Przepływ przeglądu, wątki, podsumowania | Obowiązkowe dla przeglądów PR-ów |
| `review-checks-common.md` | Fałszywe alarmy, weryfikacja, zagadnienia specyficzne dla KSeF | Obowiązkowe dla agentów przeglądu kodu |

## Specyfikacje agentów (`.claude/agents/`)

| Plik | Wyzwalacz | Odwołania |
|------|---------|------------|
| `reviewer-generic.md` | `src/ksef_mcp/**/*.py` | `references/review-checks-common.md` |
| `reviewer-security.md` | `src/ksef_mcp/**/*.py`, `tests/**/*.py` | `references/review-checks-common.md` |
| `reviewer-test-patterns.md` | `tests/**/*.py` | `references/review-checks-common.md` |
| `reviewer-infra.md` | `.github/workflows/**`, `pyproject.toml`, `bin/**` | `references/review-checks-common.md` |
| `reviewer-docs.md` | `docs/**`, `.claude/**/*.md`, `README.md`, `CLAUDE.md` | `references/review-checks-common.md` |

## Umiejętności własne (`.claude/skills/`)

| Umiejętność | Kiedy | Czego NIE robi |
|---|---|---|
| `release-notes` | sekcja „Bez wydania" w `CHANGELOG.md` jest pusta albo niekompletna przed wydaniem | nie wydaje — podnoszenie wersji, tagowanie i publikacja należą do `bin/release.py` |

## Budżety rozmiaru

| Typ pliku | Maks. linii |
|-----------|-----------|
| Specyfikacje agentów | 200 |
| Dokumenty referencyjne | 300 |
| `CLAUDE.md` | 120 |

To wytyczne, nie sztywne bramki — dziel plik, gdy trudno się w nim
poruszać, nie wyłącznie na podstawie liczby linii.
