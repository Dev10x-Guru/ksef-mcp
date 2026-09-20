# Jak działa rozdział przeglądu dla forków (#6)

Ten plik jest notatką dla człowieka, nie workflow. Opisuje, dlaczego
`claude-code-review.yml` i `claude-pr-hygiene.yml` wyglądają tak, jak
wyglądają, żeby następna osoba je zmieniająca nie rozbroiła zabezpieczeń,
nie wiedząc, że tam są.

## Problem

`ksef-mcp` to publiczna paczka na AGPL. PR z forka jest normalnym
scenariuszem współpracy, nie wyjątkiem. GitHub nie wydaje jednak tokenu
OIDC dla zdarzenia `pull_request` pochodzącego z forka, więc
`claude-code-action` nie mógł się uwierzytelnić. Oba przeglądy były
w związku z tym pomijane dla forków — współpracownik z zewnątrz nie
dostawał informacji zwrotnej, którą dostaje każdy inny.

## Rozwiązanie: dwa workflow zamiast jednego

| Workflow | Zdarzenie | Kontekst | Sekrety | Co robi |
|---|---|---|---|---|
| `pr-context.yml` | `pull_request` | fork | brak | zapisuje numer PR-a jako artefakt |
| `claude-code-review.yml` | `workflow_run` | repozytorium bazowe | tak | pobiera artefakt i przegląda |
| `claude-pr-hygiene.yml` | `workflow_run` | repozytorium bazowe | tak | j.w. dla metadanych PR-a |

`workflow_run` biegnie zawsze w kontekście repozytorium bazowego, także
gdy wyzwolił go przebieg z forka. Stąd dostęp do OIDC i do sekretów — i
stąd cała ostrożność poniżej.

## Zabezpieczenia, których nie wolno usunąć

**1. Uprzywilejowany workflow nigdy nie pobiera kodu z forka.**
`actions/checkout` bierze `main` z repozytorium bazowego, nigdy
`head_sha` PR-a. Gdyby brał head forka, dowolny obcy mógłby wykonać swój
kod z dostępem do `ANTHROPIC_API_KEY` i prawem zapisu.

**2. Zawartość artefaktu jest danymi, nie poleceniem.**
Numer PR-a wczytywany z artefaktu trafia do zmiennej środowiskowej i jest
używany wyłącznie w cudzysłowie (`"$PR_NUMBER"`). Nigdy przez `${{ }}`
w bloku `run:` — taka interpolacja wstawia napis do skryptu przed jego
uruchomieniem, więc treść z forka stałaby się kodem shella.

**3. Numer jest walidowany składniowo.**
Wyłącznie cyfry. Odrzucamy wszystko inne, zanim cokolwiek z nim zrobimy.

**4. Numer jest wiązany z SHA, którego fork nie kontroluje.**
To jest zabezpieczenie najmniej oczywiste i najważniejsze. Sama walidacja
składni nie wystarcza: atakujący mógłby wpisać do artefaktu numer *cudzego*
PR-a i skierować tam przegląd wraz z uprawnieniem do komentowania.
Dlatego sprawdzamy, że `headRefOid` PR-a o tym numerze równa się
`github.event.workflow_run.head_sha` — a tę wartość ustawia GitHub, nie
zgłaszający. Rozbieżność przerywa przebieg.

**5. Przegląd biegnie tylko dla przebiegu zakończonego powodzeniem.**
`workflow_run.conclusion == 'success'`, żeby przerwany przebieg zbierający
kontekst nie wyzwalał przeglądu na niekompletnych danych.

## Plik, którego GitHub nie wczytał (#194)

Od commita 1887587 żaden z obu uprzywilejowanych plików się nie
kompilował. Przebieg powstawał bez ani jednego zadania, podpisany ścieżką
pliku zamiast polem `name:`. Brak zadania oznacza brak `check_run`, więc
awaria nie trafiała do kontroli PR-a: przegląd wyglądał na czysty, bo się
w ogóle nie odbył.

Przyczyna była w komentarzu shella wewnątrz kroku `run:`: literalna, pusta
para ograniczników wyrażenia Actions, wpisana jako przykład tego, czego nie
robić. GitHub przeszukuje całą treść skryptu, także komentarze, i puste
wyrażenie nie przechodzi kompilacji. Komunikat kompilatora („An expression
was expected", z linią i kolumną) stoi na stronie nieudanego przebiegu
`push` pod nagłówkiem „Invalid workflow file" i to od niego trzeba zaczynać.
Pierwsza próba naprawy (przeniesienie `concurrency` na poziom zadania i
zabezpieczenie `||`) była pudłem, bo pominęła ten komunikat i zgadywała z
bisekcji na commit. Ta zmiana pozostała, bo nie szkodzi.

W komentarzach wewnątrz `run:` nie zapisuj ograniczników wyrażeń w żadnej
postaci; opisz je słowami.

Osobny plik `workflow-health.yml` pilnuje tej klasy usterek na przyszłość:
sprawdza po pushu na `main`, czy w rejestrze Actions któryś workflow nie
figuruje pod swoją ścieżką zamiast pod nazwą — bo to znaczy, że GitHub
nigdy go nie wczytał. Przed scaleniem tę samą usterkę widać na liście
przebiegów dla SHA gałęzi: uszkodzony plik zostawia tam nieudany przebieg
`push` bez zadań.

## Czego świadomie nie użyto

`pull_request_target` rozwiązałby uwierzytelnienie jedną linią i jest
wymieniany w większości poradników. Odrzucony: biegnie z pełnymi
uprawnieniami na zdarzeniu, którego treść pochodzi od zgłaszającego, więc
jedna nieuważna zmiana — dodany `checkout` z `head.sha` — zamienia go
w wykonanie obcego kodu z sekretami. Rozdział na dwa workflow wymaga
więcej pisania, ale nie ma stanu, w którym pojedyncza linia otwiera
repozytorium.
