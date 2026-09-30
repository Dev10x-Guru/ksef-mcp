# ADR-106: Rejestr przeglądów — trzecie odrębne repozytorium dla stanu interakcji człowieka

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** D-022 „Deduplikacja i raport „co nowego" to dwie osobne rzeczy"; D-025 „Osią produktu jest automat, archiwum i delta"; ST-4 „Sygnalizacja, nie rozstrzygnięcie"; zgłoszenie [#43](https://github.com/Dev10x-Guru/ksef-mcp/issues/43); [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md)
- **Depends-on:** [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md)

## Kontekst

Badanie rzeczywistego archiwum [D-025] znalazło cztery brakujące faktury z trzydziestu ośmiu. Trzy były zaległością miesiąca bieżącego — darmowa Aplikacja Podatnika wykazałaby je dzisiaj. Jedna z lipca, dwieście piętnaście złotych, była przeoczeniem: wypadła poza regularny rytm dostawcy, a lipiec był już zarchiwizowany jako zamknięty. Porównanie „co mam teraz" z „co miałem w ostatnią sobotę" to jedyna obserwowana różnica między tym narzędziem a Aplikacją.

Trzy markery były dotąd zagrożone pomieszaniem:
1. `SyncStore` — gdzie ostatnio pytaliśmy KSeF
2. `DeduplicationIndex` — co trzymamy na dysku
3. (nieistniejący) — co człowiek już widział

Każdy z nich niesie inną interpretację „czy to coś nowego":
- `SyncStore` widzi „nowe" jako „pytanie trafiło w inny punkt ciągłości"
- `DeduplicationIndex` widzi „nowe" jako „brak hash'u w indeksie"
- Trzeci powinien widzieć „nowe" jako „nigdy nie zostało pokazane czytającemu"

Decyzje D-022 i D-025 implikują trzeci marker, lecz jego miejsce w architekturze nie było rozstrzygnięte.

## Decyzja

Trzeci marker żyje w własnym, trwałym rejestrze `review.json`, oddzielnym od deduplikacji i od cache'u. Jedna reguła per wymiar:

```
<katalog danych>/subjects/<NIP>/<środowisko>/
├── synchronisation.json      # ADR-103: gdzie pytaliśmy
├── deduplication.json        # ADR-105: co trzymamy
├── review.json               # tu: co człowiek widział
└── invoices/
    └── <NumerKSeF>.xml
```

`review.json` zawiera listę KSeF numerów ze znacznikami czasu odczytu, a każdy wpis niesie także datę otrzymania faktury *odczytaną z samego numeru KSeF*, nie z czasu pobrania. To rozstrzygnięcie jest kluczowe: faktura z numerem z lipca jest nowa dla czytającego niezależnie od tego, kiedy ktoś o nią zapytał. Numer KSeF to jedyne miejsce, w którym ta data jest zapisana — `InvoiceMetadata.issue_date` należy do sprzedawcy.

### Wariant adresowania daty

| Podejście | Zalety | Wady |
|---|---|---|
| **Data odczytana z numeru KSeF (`YYYYMMDD`)** | źródło jedyne; niezależne od momentu pobrania; niemożliwe do sfałszowania później | wymaga parsowania numeru; numer może być nieprawidłowy (jednak walidujemy) |
| Timestamp z `MetadataPage.hwm_date` | dostępny wprost | opisuje stronę, nie fakturę; powtórzenie zapytania zwróci inny `hwm_date` |
| Timestamp z `InvoiceMetadata.issue_date` | wyraźnie nazwane pole | to data sprzedawcy, nie data przyjęcia w KSeF; dla ST-4 bezsensowne |
| Czas pobrania w chwili rejestracji | najprostsze | faktura z lipca, pobrana wczoraj, nigdy się nie będzie zgadzać z rzeczywistością |

Wybór jest wzmacniany przez [D-022] — delta dotyczy tego, co *pokazane*, nie tego, co *posiadane*, i data to moment nadania numeru, a nie moment dostępu.

### Trwałość rejestru

Rejestr żyje w **katalogu danych**, nigdy w cache'u:
- Utrata cache'u (czyszczenie dysku) kosztuje jedno zapytanie
- Utrata rejestru przeglądów kosztuje historię tego, co już było pokazane — każda faktura zostaje „nowa" ponownie
- [D-034] wymaga komendy czyszczącej retencję, a komenda ta skasuje `invoices/`, nie rejestr

## Uzasadnienie

Separacja trzech markerów rozwiązuje dokładnie problem zaobserwowany w [D-025]:
1. Powtórzenie zapytania nie tworzy duplikatów w archiwum (rola `DeduplicationIndex` z [ADR-105])
2. Powtórzenie zapytania nie powtarza pokazania tej samej faktury (rola `ReviewStore`)
3. Zmiana okna zapytania nie wpływa na to, co było już widziane (separacja `SyncStore` z [ADR-103])

Każdy marker ma własny cykl życia:
- `SyncStore` — żyje do następnego zapytania
- `DeduplicationIndex` — żyje do czasu retencji (faktury kasujemy)
- `ReviewStore` — żyje niezależnie od retencji (coś co widzieliśmy nigdy nas już nie zadziwi)

Datowanie przez numer KSeF zamiast przez pobranie jest wymogiem ST-4: osobą decydującą o ujęciu jest człowiek, a to wyznacza pytanie: „czy ta faktura z lipca jest już rozliczona w moim katalogu zamkniętym w lipcu?". Odpowiedź musi być „tak, to jest lipcowa", niezależnie od tego, czy pytanie przyszło w lipcu, czy we wrześniu.

## Konsekwencje

**Pozytywne:**
- Trzy osobne pytania mają osobne odpowiedzi: gdzie pytaliśmy, co mamy, co widzieliśmy
- Utrata danych w jednym rejestrze nie wpływa na całość — czyszczenie retencji nie zaciera historii przeglądów
- Okno o stałych końcach (zaokrąglone do pełnej godziny) sprawia, że powtórzenie w ciągu godziny jest darmowe [D-021]
- Prawie wszystkie faktury poniżej progu listowania trafiają na dysk z prawami `0600`, co chroni zawartość [D-011]

**Negatywne:**
- Trzy osobne rejestry to trzy osobne pliki do śledzenia — człowiek zarządzający archiwum musi wiedzieć, że są
- Migracja rejestru przy zmianie `NIP` albo środowiska wymaga osobnego kroku (brak tej operacji dziś)
- Rejestr nosi znacznik czasu odczytu; synchronizacja bez pełnego pobrania oznacza, że wpis w rejestrze może być starszy niż plik na dysku (to jest poprawne, lecz może być zaskakujące)

## Powiązane

- [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md) — to sama struktura katalogów i ten sam wzorzec `temp → rename`
- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — pierwszy z trzech markerów
- [D-022](../domain/decisions.md#d-022) — deduplikacja i delta to dwie rzeczy
- [D-025](../domain/decisions.md#d-025) — delta jest osią wartości produktu
- [#43](https://github.com/Dev10x-Guru/ksef-mcp/issues/43) — zgłoszenie
