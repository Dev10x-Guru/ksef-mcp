# ADR-106: Przegląd kodu dla PR-ów z forków przez podział workflow

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5)
- **Reviewed-by:** —
- **Sources:** [GH-6](https://github.com/Dev10x-Guru/ksef-mcp/issues/6) (issue), [PR #77](https://github.com/Dev10x-Guru/ksef-mcp/pull/77) (implementacja), `docs/fork-review-split.md`

## Kontekst

`ksef-mcp` to publiczna paczka na licencji AGPL-3.0. Pull requesty z forków są normalnym scenariuszem współpracy, nie wyjątkiem.

GitHub odmawia mintowania tokenu OIDC dla zdarzenia `pull_request` pochodzącego z forka. W konsekwencji, `claude-code-action` nie mógł się uwierzytelnić ani pobrać dostępu do API, a oba przeglądy — przegląd kodu i przegląd higieny PR-a — były automatycznie pomijane dla PR-ów z forków. Współpracownik z zewnątrz nie dostawał takiej samej informacji zwrotnej co każdy inny, co stanowiło barierę dla wkładu społeczności.

## Decyzja

Przeglądy kodu dla PR-ów z forków realizowane są przez podział na dwa niezależne workflow zamiast próby użycia jednego:

| Workflow | Zdarzenie | Kontekst | Uprawnienia | Funkcja |
|---|---|---|---|---|
| `pr-context.yml` | `pull_request` | fork | brak | zapisuje numer PR-a w artefakcie |
| `claude-code-review.yml` | `workflow_run` (z `pr-context`) | repozytorium bazowe | sekrety + zapis do PR-ów | pobiera artefakt i wykonuje przegląd |
| `claude-pr-hygiene.yml` | `workflow_run` (z `pr-context`) | repozytorium bazowe | sekrety + zapis do PR-ów | pobiera artefakt i sprawdza metadane |

### Dlaczego ten podział zamiast alternatyw?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Workflow split (workflow_run)** | Bez zakopanego ryzyka bezpieczeństwa; OIDC działa dla forków; każdy obcy PR dostaje przegląd; zabezpieczenia to jawne linie kodu, które można zaudytować | Wymaga pisania więcej kodu; logika obejmuje kilka zmiennych środowiskowych i walidacje |
| `pull_request_target` | Rozwiązałoby OIDC jedną linią; użytkownik-agentowe przykłady to pokazują | **Odrzucone:** biegnie z pełnymi sekretami na zdarzeniu, którego treść pochodzi od zgłaszającego; jedna nieuważna zmiana (np. dodanie `checkout` z `head.sha`) zamienia to w wykonanie obcego kodu z dostępem do `ANTHROPIC_API_KEY` |
| `pull_request` z warunkiem pomijającym forki | Prosta do zrozumienia | Pozostawia forki bez przeglądu; nie rozwiązuje problemu |

### Zabezpieczenia, które są kluczowe dla schematu

1. **Uprzywilejowany workflow nigdy nie pobiera kodu z forka.** `actions/checkout` w `claude-code-review.yml` i `claude-pr-hygiene.yml` zawsze bierze domyślny branch repozytorium bazowego, nigdy `head_sha` PR-a. Gdyby brał kod z forka, każdy obcy mógłby wykonać swój kod z dostępem do `ANTHROPIC_API_KEY` i prawem zapisu.

2. **Zawartość artefaktu jest danymi, nie poleceniami.** Numer PR-a wczytywany z artefaktu zapisywanego przez fork trafia do zmiennej shell'a i jest używany wyłącznie w cudzysłowie (`"$PR_NUMBER"`). Nigdy nie wstawiane przez `${{ }}` bezpośrednio do kodu shella w kroku — taka interpolacja wstawiłaby napis do skryptu *przed* jego uruchomieniem, więc treść z forka stałaby się wykonanym kodem.

3. **Numer PR-a jest walidowany składniowo.** Wyłącznie cyfry; wszystko inne jest odrzucane zaraz po wczytaniu, zanim cokolwiek się z nim robi.

4. **Numer jest wiązany z SHA, którym fork nie manipuluje.** To zabezpieczenie jest najmniej oczywiste, ale najważniejsze. Sama walidacja składni nie wystarczy: atakujący mógłby wpisać do artefaktu numer *cudzego* PR-a i skierować tam przegląd wraz z uprawnieniem do komentowania. Dlatego kod sprawdza, że `headRefOid` PR-a o tym numerze równa się `github.event.workflow_run.head_sha` — wartości, którą ustawia GitHub, nie zgłaszający. Rozbieżność przerywa przebieg.

5. **Przegląd biegnie wyłącznie dla przebiegu `pr-context` zakończonego powodzeniem.** Warunek `workflow_run.conclusion == 'success'` zapewnia, że przerwany lub niepomyślny przebieg zbierający kontekst nie wyzwoli przeglądu na niekompletnych danych.

## Uzasadnienie

Podział workflow na dwa osiąga kilka celów jednocześnie:

- Umożliwia OIDC dla forków, bo workflow uprzywilejowany (`workflow_run`) biegnie zawsze w kontekście repozytorium bazowego.
- Zachowuje bezpieczeństwo, bo kod forka nigdy nie jest checkoowany ani wykonywany w kontekście, w którym ma dostęp do sekretów.
- Pozostawia audytowi jawny, czytelny schemat zamiast jednej „magicznej" linii, która jest niebezpieczna z powodu jednej zmiennej.
- Umożliwia współpracy z publiczną społecznością bez kompromisu na bezpieczeństwie.

Dwu-workflow split jest standardem bezpiecznej integracji forków w open source, znany z repozytoriów takich jak `slint-ui`, `prisma`, czy `sentry` — wszędzie tam, gdzie krytyczne zadania (publikacja, przegląd, automat na sekretach) muszą działać dla PR-ów z forków.

## Konsekwencje

**Pozytywne:**
- Współpracownicy z forków dostają pełny przegląd kodu i higieny PR-a, tak samo jak autorzy z uprawnieniami do push.
- Bez polegania na pojedynczym warunku, który łatwo rozbroić — logika bezpieczeństwa jest rozproszona i trudna do przypadkowego wyłączenia.
- Jawne, audytowalne zabezpieczenia opisane w `docs/fork-review-split.md`.
- Pełne wsparcie dla publicznego projektu open source na AGPL.

**Negatywne:**
- Dwa workflow zamiast jednego: więcej linii kodu do utrzymania.
- Dodatkowy artefakt przechowywany przez 1 dzień (niedużo miejsca, ale jest).
- Przegląd czeka na zakończenie `pr-context` — niewielkie zwiększenie czasu wykonania CI (sekundy).
- Schemat jest bardziej subtelny; przyszły developer musi przeczytać `docs/fork-review-split.md`, żeby go zrozumieć, zamiast natychmiast „odkryć" z kodu.

## Powiązane

- [GH-6](https://github.com/Dev10x-Guru/ksef-mcp/issues/6) — zgłoszenie opisujące problem
- [docs/fork-review-split.md](../fork-review-split.md) — dokumentacja implementacyjna, obowiązkowa do przeczytania przed zmianami w workflow
- [PR #77](https://github.com/Dev10x-Guru/ksef-mcp/pull/77) — implementacja (część z paczki GH-6)
