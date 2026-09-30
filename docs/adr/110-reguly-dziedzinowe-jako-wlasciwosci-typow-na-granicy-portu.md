# ADR-110: Reguły dziedzinowe jako własności typów na granicy portu

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** [PR #206](https://github.com/Dev10x-Guru/ksef-mcp/pull/206);
  [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) (ADR-102 określa
  GDZIE mieszkają typy; ten dokument określa CO one zawierają);
  `docs/domain/decisions.md` D-017, D-031, D-032
- **Depends-on:** [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)

## Kontekst

[ADR-102] ustalił, że typy dziedzinowe mieszkają w `ksef_port/types.py` na
granicy portu antykorupcyjnego. Tam żyją zaraz — ale wcześniej będą rozproszone
reguły, które te typy opisują. Przykłady:

- `SubjectRole` (kim podmiot jest na fakturze) miała cztery nazwy w kodzie:
  `InvoiceDirection`, `direction`, `subject_type`, `subject_role` — dwie z nich
  w jednej linii serwera.
- `Operation` (rodzina operacji liczana osobno w budżecie KSeF) była w
  `budget.py`, a tabela przydziału do limitów żyła w `RateLimits` w `types.py`,
  rozdzielone przez cztery modułu importu.
- `AuditedOperation` i `AuthorisationBasis` były konstanami `Final[str]`
  rozproszonymi nad dwa moduły, otwarte na typo bez ochrony typu.
- Reguły obliczeniowe — czy strona metadanych jest pełna, czy okres przeterminował,
  co jest markerem kontynuacji — były właściwościami zdefiniowanymi gdzie indziej,
  a typ, którym manipulowały, żył bez nich.

Księgowa czyta raport wygenerowany jednym narzędziem, a musi ufać, że każde
inne narzędzie obliczy to samo. Gdy reguła mieszka z dala od typu, który tymi
danymi manipuluje, to zaufanie wymaga przepisania całego kodu z tej reguły w
jego głowie. Gdy reguła JE jest właściwością typu, księgowa wie: wszystkie
narzędzia korzystają z tego samego kodu.

To dotyczy kodu produkcyjnego: gdy reguła jest na typie w `types.py`, każdy,
kto ten typ czyta — w przeglądu czy audycie — widzi, co typ robi. Gdy reguła
jest sześć linii poniżej, w innym module, dwaj czytelnicy mogą minąć się obok
niej.

## Decyzja

Reguły dziedzinowe kodujemy jako właściwości i metody na typach wartościowych
w `ksef_port/types.py`, nigdy jako luźne stałe, magiczne stringi czy funkcje
rozrzucone po modulach.

### Forma: typy wymuszone przez enumeracje

Gdy reguła jest zbiorem skończonym wartości, definiujemy `StrEnum`:

```python
class SubjectRole(StrEnum):
    SELLER = "seller"
    BUYER = "buyer"
    THIRD_SUBJECT = "third_subject"
    AUTHORIZED_SUBJECT = "authorized_subject"
```

To wymusza:
- **Typ na granicy**: autor kodu nie może przypadkowo wpisać `"Seller"` zamiast
  `"seller"`.
- **Widoczność dla czytelnika**: każdy, kto czyta sygnaturę funkcji zawierającej
  `subject_role: SubjectRole`, widzi od razu — nie w docstringu albo komentarzu
  — jakiemu zbiorowi wartości zaufać.
- **Brak typo na dysku**: gdy przechowujemy wartości na dysku, `StrEnum` daje
  nam `SubjectRole.SELLER.value == "seller"` — enumeracja jest wariantem, nie
  dupelek.

### Forma: właściwości obliczeniowe na typach

Gdy reguła to predykat, np. „czy strona metadanych jest pełna", żyje nie jako
funkcja `is_page_complete(page)` ale jako właściwość:

```python
@dataclass(frozen=True)
class MetadataPage:
    has_more: bool
    truncated: bool
    
    @property
    def complete(self) -> bool:
        return not (self.has_more or self.truncated)
```

Zysk:
- **Niezmiennik widoczny na czytanie**: każdy, kto dostaje `MetadataPage`, wie,
  że może go zapytać `page.complete` — to jest API typu, nie magiczna funkcja.
- **Historia zmian żyje z danymi**: gdy reguła zmieni się, ten sam commit doda
  dane i zmieni reguł, bo żyją razem.
- **Testowanie**: to, co testujemy, jest tym, co klient na granicy portu dostaje
  — nie test wartości stałej w module.

### Forma: tabele translacji dla wokabularzy zewnętrznych

Gdy typ ma rolę tłumacza między słownikiem naszym a słownikiem KSeF (D-017),
tablica mieszka przy nim:

```python
WIRE_SUBJECT_TYPES: Final[dict[SubjectRole, str]] = {
    SubjectRole.SELLER: "Subject1",
    SubjectRole.BUYER: "Subject2",
    SubjectRole.THIRD_SUBJECT: "Subject3",
    SubjectRole.AUTHORIZED_SUBJECT: "SubjectAuthorized",
}
```

Kompletność tej tablicy pilnuje test — każda wartość enumeracji musi być w
słowniku. Gdy słownik się zmieni, test pada, a zmiana reguły musi być świadoma.

## Uzasadnienie

Ta decyzja rozwiązuje cztery problemy:

1. **Rozpojenie pojęć**: cztery nazwy dla jednej rzeczy (`SubjectRole`) to
   inwestycja w trzęsienie po zmianie nazwy. Jedna nazwa, jeden typ = zmiana to
   przeszukanie i zastąpienie w IDE.

2. **Brak typo na wirtualnym dysku maszyny**: gdy `Operation` jest enumeracją,
   a `RateLimits` mapuje ją do limitów, kompilator widzi — jeśli przibędy nowa
   rodzina operacji, test pilnujący pokrycie mówi zaraz gdzie.

3. **Luk w pokryciu testów**: gdy reguła jest testowana ZARAZ obok typu, nie w
   osobnym module, luka w pokryciu jest widoczna. Utajniona funkcja w module
   może być testowana, ale wciąż nikt jej nie woła z kodu — typ żyje bez niej.

4. **Odczytanie kodu — pierwsza czytanka**: gdy nowoprzybyły czyta `MetadataPage`,
   natychmiast widzi całe API typu włącznie z regułami obliczeniowymi. Gdy reguła
   jest w `if not (page.has_more or page.truncated)` gdzieś w `synchronisation.py`,
   ją, trzeba koniec poszukiwania obliczy ją sam.

## Konsekwencje

**Pozytywne:**
- Typu nie można używać bez poznania jego reguł — kod jest mniej narażony na
  błędy logiki ze względu na gdzieś zapomniane założenie.
- Zmiana reguły to zmiana jednego miejsca, nie szukanie w siedmiu modulach gdzie
  else jest `if not (page.has_more or page.truncated)`.
- Testowanie: bieżący test portu żyje blisko typu, nie w zakurzonej funkcji, którą
  mało kto zna.
- Enumeracje wymuszają kompletność — typ, który może być tylko czterema wartościami,
  nie może być pięcioma przez typo.

**Negatywne:**
- Typ wartościowy rośnie w złożoności — może mieć więcej metod niż trzeba do
  konstruktora i porównania.
- Enumeracje nie rosną arbitralnie — jeśli przybędy piąta wartość `SubjectRole`,
  trzeba zaktualizować enumerację, a potem wszystkie testy na nią polegające.
  To jest cel, ale wymaga zmian w więcej miejscach.
- Ścisłość enumeracji — gdy pojawiłaby się konieczność ad-hoc wartości nie w
  zbiorze, typ ją odrzuci. To jest rzeczywiste ograniczenie.

## Powiązane

- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — określa, gdzie
  typy mieszkają i jak są chroniące przed SDK; ten ADR mówi, co w nich zawierają
- [#138–#144](https://github.com/Dev10x-Guru/ksef-mcp/issues) — siedem zgłoszeń,
  które tę zmianę przeprowadzają, od `SubjectRole` po `AuthorisationBasis`
- [PR #206](https://github.com/Dev10x-Guru/ksef-mcp/pull/206) — wdrożenie tej
  decyzji
