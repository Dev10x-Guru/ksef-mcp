---
name: release-notes
description: >
  Redaguje notatki wydania po polsku z commitów i PR-ów między tagami,
  w głosie JTBD, i wpisuje je do sekcji „Bez wydania" w CHANGELOG.md.
  TRIGGER when: przygotowujesz wydanie i potrzebujesz opisu zmian, albo
  gdy sekcja „Bez wydania" jest pusta, a bin/release.py odmawia wydania.
  DO NOT TRIGGER when: piszesz pojedynczy commit albo dokumentację
  niezwiązaną z wydaniem.
user-invocable: true
invocation-name: release-notes
allowed-tools:
  - mcp__plugin_Dev10x_cli__collect_prs
  - mcp__plugin_Dev10x_cli__pr_get
  - mcp__plugin_Dev10x_cli__issue_get
  - Bash(git log:*)
  - Bash(git tag:*)
  - Bash(gh release view:*)
  - Bash(gh release edit:*)
  - Read
  - Edit
  - AskUserQuestion
  - TaskCreate
  - TaskUpdate
---

# Notatki wydania

Zamienia historię commitów w opis tego, **co zmieniło się dla człowieka
obsługującego faktury** — nie w listę zmian w kodzie.

Wzorowane na `Dev10x:release-notes`, ale bez warstwy playbooka, bez
Linearu i bez Slacka: ten projekt ma jeden pakiet, jedno repozytorium
i jeden kanał publikacji, więc konfigurowalność tamtego skilla byłaby
tu wyłącznie kosztem.

## Kiedy używać

- Sekcja `## Bez wydania` w `CHANGELOG.md` jest pusta albo niekompletna,
  a zbliża się wydanie. `bin/release.py` odmawia wydania z pustą sekcją,
  więc to jest typowy moment wywołania.
- Trzeba opisać, co weszło między dwoma tagami.

## Czego ten skill NIE robi

Nie wydaje. Nie podnosi wersji, nie taguje, nie publikuje. Wydanie należy
do `bin/release.py`, a rozdział jest celowy: redagowanie tekstu wolno
powtarzać i poprawiać, publikacja jest nieodwracalna.

## Orkiestracja

**WYMAGANE: utwórz zadanie przy wywołaniu.**

1. `TaskCreate(subject="Zredagować notatki wydania", activeForm="Redaguję notatki")`

Zamknij je po wpisaniu treści do `CHANGELOG.md`.

## Przebieg

### 1. Ustal zakres

```bash
git tag --list --sort=-v:refname
```

Domyślnie: od najnowszego tagu do `HEAD`. Przy pierwszym wydaniu tagów
nie ma — wtedy zakresem jest cała historia i trzeba to powiedzieć wprost
w podsumowaniu, zamiast udawać przyrost.

### 2. Zbierz materiał

`mcp__plugin_Dev10x_cli__collect_prs` z wzorcem `GH-\d+`.

Przeczytaj też treści commitów, nie tylko tytuły — uzasadnienia „dlaczego"
siedzą w treści, a to one są materiałem na notatki:

```bash
git log <od>..<do> --no-merges --format="### %s%n%n%b%n---"
```

Gdy PR nie niesie Job Story, wywołaj `Dev10x:jtbd` w trybie
nieobsługiwanym i zbierz szkice do jednej partii.

### 3. Napisz notatki

**Po polsku, zgodnie z polityką językową projektu.** Głos Job Story:
trzecia osoba i konkretna rola dziedzinowa — księgowa, integrator,
podatnik. Nigdy „użytkownik".

Kategorie zgodne z Keep a Changelog, w tej kolejności, pomijając puste:

| Nagłówek | Co tu trafia |
|---|---|
| `### Dodane` | nowe zdolności |
| `### Zmienione` | zmiana zachowania istniejącej zdolności |
| `### Poprawione` | błędy |
| `### Usunięte` | wycofane zdolności |
| `### Bezpieczeństwo` | patrz niżej — w tym projekcie sekcja obowiązkowa |

Każdy wpis kończy odsyłaczem `([GH-N])`, a definicje odsyłaczy idą na
koniec pliku.

### 4. Sekcja Bezpieczeństwo jest obowiązkowa, gdy cokolwiek ją dotyka

To repozytorium obsługuje poświadczenia KSeF i dane osobowe z faktur,
a Ministerstwo Finansów blokuje podmioty za wzorce wskazujące na
obchodzenie limitów. Przejrzyj zmiany pod kątem czterech rzeczy i opisz
każdą, którą znajdziesz — **także wtedy, gdy wygląda na szczegół**:

- zmiana w tym, **kiedy i ile razy** kod sięga do API KSeF, w tym
  ponawianie żądań i wielkość strony;
- zmiana w tym, **gdzie ląduje token albo NIP** — magazyn, uprawnienia
  pliku, treść komunikatu, argumenty procesu;
- zmiana domyślnego **środowiska** KSeF;
- cokolwiek, co dotyka **treści faktury** albo jej trafiania do logu.

Powód, dla którego to jest osobny krok, a nie wiersz listy kontrolnej:
czytelnik notatek wydania jest podatnikiem, a nie recenzentem kodu.
Zmiana liczby ponowień nie wygląda na nowość, dopóki nie napisze się,
że chroni go przed przedłużeniem blokady.

### 5. Nazwij zmiany zachowania

Osobno wypisz to, co zadziała **inaczej niż wcześniej** u kogoś, kto już
używa narzędzia: zmienione domyślne wartości, zawężone zachowanie, inne
kody wyjścia, zmienione nazwy komend. Przedstaw tę listę przed zapisem —
to jest część, którą czytelnik musi zobaczyć, nawet gdy resztę pominie.

### 6. Wpisz do CHANGELOG.md

Wstaw treść pod `## Bez wydania`, zachowując istniejące wpisy — sekcję
prowadzi człowiek i skill jej nie nadpisuje bez potrzeby. Gdy istniejący
wpis mówi to samo innymi słowami, scal go, zamiast dublować.

`bin/release.py` przeniesie tę sekcję pod numer wersji przy wydaniu, więc
nie dopisuj numeru ani daty samodzielnie.

### 7. Wydanie GitHub — tylko gdy tag już istnieje

`bin/release.py` tworzy wydanie z `--generate-notes`, czyli z listą PR-ów.
Aby zastąpić ją zredagowaną treścią:

```bash
gh release edit <tag> --notes-file <plik>
```

Nigdy nie rób tego przed wydaniem — tag jeszcze nie istnieje i polecenie
i tak odpadnie.

## Czego unikać

Notatki wydania to nie `git log` z ładniejszym formatowaniem.

| Źle | Dlaczego | Zamiast tego |
|---|---|---|
| „Dodano `preflight.py`" | nazwa modułu nic nie mówi podatnikowi | „Brakujące warunki wstępne ujawniają się przed pierwszym uruchomieniem, wraz z komendą naprawczą" |
| „Poprawiono obsługę błędów" | nie wiadomo, co było zepsute | nazwij objaw, który czytelnik mógł widzieć |
| „Refaktoryzacja `cli.py`" | zmiana bez skutku dla czytelnika | pomiń albo opisz zdolność, którą odblokowuje |
| Wpis bez odsyłacza | nie da się dojść do uzasadnienia | zawsze `([GH-N])` |

## Zobacz też

- `references/git-jtbd.md` — głos Job Story, wybór aktora
- `bin/release.py` — wydanie; ten skill go nie zastępuje
- `CHANGELOG.md` — format i zasada prowadzenia sekcji „Bez wydania"
