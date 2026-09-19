# ADR-110: Polityka budżetowa dopełniania stron metadanych

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** audyt architektury 2026-09-19, zgłoszenia #179–#182
- **Depends-on:** [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)

## Kontekst

KSeF oddaje metadane stronami po 250 pozycji — to sufit API, nie nasz
wybór (D-010). Port raportował `has_more`, ale nie ustawiał
`page_offset` i nie miał pola, którym numer strony wróciłby do
wołającego. Okno powyżej 250 faktur wracało więc obcięte, a wszystko
powyżej sufitu przepadało po cichu.

Przewleczenie offsetu przez port (#182) to mechanizm i samo w sobie nie
wymagało decyzji. Decyzji wymaga to, **kiedy pętla dopełniająca ma
przestać** — bo każda kolejna strona kosztuje jedno z dwudziestu
zapytań o metadane na godzinę, a Ministerstwo rejestruje przekroczenia
limitów i wydłuża blokadę przy powtórzeniach (D-020, D-031 §8).

Warunkiem wstępnym był trwały licznik budżetu (#97/#98). Bez niego
licznik powstawał od zera przy każdym wywołaniu narzędzia i pętla
mogłaby wydać godzinowy przydział, nie zauważywszy tego.

## Decyzja

Pętla dopełniająca w `PeriodMetadataReader.read` **dociąga strony do
skutku**, a zatrzymuje ją wyłącznie trwały licznik — nie stała liczba
stron. Zatrzymuje się, zanim zejdzie poniżej rezerwy
`METADATA_RESERVE = 3` niewydanych zapytań, żeby pozostałe trzy typy
podmiotu tego samego pytania dało się jeszcze odpytać (D-031 §5).

Gdy licznik nie zna żadnego pułapu i **nie może niczego odmówić**,
pętla nie rusza poza pierwszą stronę. Licznik bez sufitu nie
powstrzymałby serwera odpowiadającego `has_more` w nieskończoność, a
wytrwałość klienta jest tu szkodą samą w sobie, nie pojedyncza
operacja.

Strona, która wraca, mówi, co ją zatrzymało: `has_more` razem z
`budget_bound` to okno warte ponowienia za godzinę, samo `has_more` to
okno, które uciął KSeF. To dwie różne wiadomości dla księgowej i tylko
pierwszą warto przeczekać.

Okno niekompletne **nie trafia na dysk**. W tym katalogu nic nie
wygasa, więc zapamiętanie kikuta uczyniłoby go odpowiedzią na zawsze, a
wywołanie, które godzinę później mogłoby okno dokończyć, dostałoby ten
kikut z dysku.

### Dlaczego dociąganie do skutku zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **Do skutku, ograniczone trwałym licznikiem i rezerwą** | Kompletność nie zależy od wolumenu działalności; granicą jest prawdziwy limit, nie zgadywanka | Duże okno potrafi wydać znaczną część przydziału za jednym razem |
| Przerwanie po stałych N stronach | Przewidywalny koszt jednego wywołania | N to liczba zgadnięta — ta sama rodzina co zgadywany czas oczekiwania, którą D-017 odrzuca; przy N za małym gubimy faktury dalej |
| Bez pętli, offset tylko udostępniony wołającemu | Najtańsze | Przenosi obowiązek na każdego wołającego z osobna; dziś nie zrobiłby tego żaden |

## Uzasadnienie

Stała liczba stron byłaby zgadywaniem, a trwały licznik zna prawdziwą
granicę i przeżywa proces, który ją przesunął. Rezerwa jest jedyną
liczbą wpisaną tu na sztywno i ma uzasadnienie dziedzinowe, nie
statystyczne: pytanie obejmuje cztery typy podmiotu, więc okno, które
wypiło godzinę, zostawiłoby trzy pozostałe zgłoszone jako nieznane.

Przyjętym kompromisem jest cena kompletności: miesiąc na tysiąc faktur
kosztuje cztery zapytania zamiast jednego. Cache płaci je raz —
niekompletne okno nie jest zapamiętywane, ale kompletne już tak, i
drugie pytanie o ten sam miesiąc nie kosztuje nic (D-021).

## Konsekwencje

**Pozytywne:**
- Odpowiedź przestaje zależeć od tego, ile podatnik wystawia faktur.
- `has_more` wreszcie ma odpowiedź, a nie tylko raport.
- Niekompletność ma podaną przyczynę, więc księgowa wie, czy czekać.
- Kikut okna nie utrwala się na dysku jako odpowiedź.

**Negatywne:**
- Jedno wywołanie na dużym wolumenie wydaje kilka zapytań zamiast
  jednego.
- Okno ucięte brakiem przydziału jest odpytywane od początku przy
  kolejnym wywołaniu — nie ma wznowienia od strony, na której stanęło.
- `MetadataPage` niesie pole (`budget_bound`), którego adapter nigdy nie
  ustawia; wypełnia je dopiero czytnik dopełniający.

## Powiązane

- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — port,
  przez który przewleczono offset strony
- [#182](https://github.com/Dev10x-Guru/ksef-mcp/issues/182) — przewleczenie
  offsetu strony przez port
- [#180](https://github.com/Dev10x-Guru/ksef-mcp/issues/180) — zabezpieczenie
  w `review.assess`, które po tej decyzji zostaje jako jedyne i obejmuje
  to, czego pętla domknąć nie może
