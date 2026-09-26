# ADR-110: Dokument JSON zapisany przez jeden prymityw

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5)
- **Reviewed-by:** —
- **Sources:** [ADR-107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md), memo rozdz. 9; zgłoszenia [#145](https://github.com/Dev10x-Guru/ksef-mcp/issues/145), [#146](https://github.com/Dev10x-Guru/ksef-mcp/issues/146), [#147](https://github.com/Dev10x-Guru/ksef-mcp/issues/147), [#148](https://github.com/Dev10x-Guru/ksef-mcp/issues/148)
- **Depends-on:** [ADR-107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md)

## Kontekst

[ADR-107] ustanowiło trzy prymitywy zapisu w `storage.py`: wyłączność, atomowość i trwałość. Ponad nimi siedem magazynów (`allowance.py`, `archive.py`, `config.py`, `period_cache.py`, `review.py`, `statement.py`, `sync_store.py`) pisało JSON-a dokładnie w taki sam sposób: każdy indentował identycznie, każdy pisał unikalną nazwę pliku przejściowego, każdy kończył linią nowej (GH-145).

Obietnica, że przerwany zapis nigdy nie zostawia półpliku, była więc własnością **siedmiu kopii**, które musiały się zgadzać. Następny magazyn, który miałby być dodany, musiałby przeczytać jedną z siedmiu kopii — a jej autor mógł być nieprzestrzenny. Gdy magazyn zapomnę odpowiedzi na pytanie „co zrobić, gdy plik na dysku ma starą wersję schematu?", siedem kopii mogło odpowiedzieć siedmioma sposobami, mimo że odpowiedź jest wymuszona ekonomicznie: zapisy, od których zależy rozliczenie podatnika (punkty kontynuacji, rejestr przeglądu, indeks deduplikacji) zawsze muszą odmówić, a podręczna pamięć (okresy, liczniki limitów) zawsze może udawać, że pliku nigdy nie było, bo odbuduje się sama.

## Decyzja

### 1. Klasa `JsonDocumentStore` — stała modulowa zawierająca politykę czytania

```python
@dataclass(frozen=True, kw_only=True)
class JsonDocumentStore:
    schema_version: int
    file_mode: int
    named: str
    on_mismatch: SchemaMismatch = SchemaMismatch.REFUSE
    refused_as: type[KsefMcpError] = KsefMcpError
    consequence: str = ""

    def load(self, path: Path) -> dict[str, object] | None:
        """Plik lub None, gdy jest go dla nas niemożliwe czytać."""
    
    def save(self, path: Path, *, document: Mapping[str, object]) -> Path:
        """Dokument zapisany pod unikalnym imieniem, wymieniony, fsync katalogu."""
```

Każdy magazyn trzyma jedną instancję jako stałą modulową. Nie zna ścieżki — magazyn wybiera plik w zależności od podmiotu i środowiska, a polityka, która znałaby ścieżkę, musiałaby być przebudowywana przy każdym czytaniu. Tutaj jest stałą, która mówi w jednym miejscu: co ten plik to, jak źle może być i co wtedy zrobić.

### 2. Enum `SchemaMismatch` — dwie odpowiedzi, obie celowe

```python
class SchemaMismatch(StrEnum):
    REFUSE = "refuse"  # Dla czegoś, od czego zależy podatnik
    MISS = "miss"      # Dla czegoś, co da się odbudować
```

- `REFUSE`: cokolwiek podatnik polega — punkt kontynuacji, rejestr przeglądu, indeks deduplikacji. Zgadnięcie przy jednym z nich pomija faktury lub je powtarza, bez ogłoszenia błędu.
- `MISS`: pierwiastki, które budują się sami — pamięć okresów, liczniki limitów dopuszczalnych. Odmowa przy starej wersji to byłaby przemianianie godziny dreptania podatnika w brak serwera, aż ktoś pliku usunie ręcznie.

Teraz nazwa sprawa, że następny magazyn wybiera swoją politykę w pełnej świadomości, zamiast dziedziczenia tego, którą kopię jego autor się zdecydował przeczytać (GH-146).

### 3. Funkcja `json_written_atomically` — jedno spektrum pisma dla wszystkich

```python
def json_written_atomically(
    target: Path,
    *,
    document: Mapping[str, object],
    file_mode: int,
) -> Path:
    """Jeden magazyn JSON-a, w jedynym piśmie każdy magazyn tu pisze."""
    content = json.dumps(document, indent=DOCUMENT_INDENT, ensure_ascii=False) + "\n"
    return written_atomically(target, content=content.encode("utf-8"), file_mode=file_mode)
```

Jest nazwana, nie powtarzana, bo serializacja i trwałość to jedno zobowiązanie: magazyn, który indentuje inaczej, jest tylko niespójny, ale magazyn, który sięga poza to do `written_atomically` z własnym bajtem, jest jednym `fsync` od złamania D-006 samodzielnie (GH-145).

## Uzasadnienie

Siedem kopii to siedem miejsc do zmiany, gdy przyszłość wymaga ulepszenia trwałości, zwrotności lub porządku JSON-a. Te zmiany powinny trafiać wszędzie — do wszystkich siedmiu — natychmiast i jednakowo. Jedno miejsce zmienia całą obietnicę, siedem miejsc powinien być błędem rewidenta.

Polityka schematu, która była dziedziczona milczeniem, teraz jest nazwaną rzeczą, którą poprzez widok historii można śledzić: dlaczego ten magazyn wybiera REFUSE, a tamten MISS. Popełnienie błędu podczas dodawania nowego magazynu — na przykład skopiowanie polityki z miejsca, które je ma odwrotnie — widać teraz w przeglądzie jako coś ze słowa „polityka", a nie jako szczegół implementacji, który zaniedby czytelnik.

## Konsekwencje

**Pozytywne:**
- Obietnica durability (D-006) żyje w jednym miejscu, nie w siedmiu kopii.
- Przyszła poprawa trwałości zmienia się raz, zamiast siedmiokrotnie (i pewności, że żaden magazyn nie został pominięty).
- Polityka schematu jest nazwana i celem świadomej decyzji, nie skutkiem Copy-Paste.
- Łatwo zauważyć naruszenie polityki podczas przeglądu.

**Negatywne:**
- Dodaje warstwę pośrednika (magazyn musi znać swój `JsonDocumentStore`).
- Wszystkie nowe magazyny JSON-a są teraz związane z tą abstrakcją — nie mogą ignorować jej bez świadomości.
- Zmiana w `JsonDocumentStore` wymaga zmian we wszystkich miejscach, które ją instancjują.

## Powiązane

- [ADR-107](107-wylacznosc-zapisu-w-katalogu-podmiotu.md) — trzy prymitywy (`exclusive_write`, `written_atomically`, `replaced_durably`), na których ten dokument się opiera
- [GH-145](https://github.com/Dev10x-Guru/ksef-mcp/issues/145) — Jeden prymityw zapisu dokumentu
- [GH-146](https://github.com/Dev10x-Guru/ksef-mcp/issues/146) — Polityka niezgodności schematu z nazwą
- [GH-147](https://github.com/Dev10x-Guru/ksef-mcp/issues/147) — Koniec przepisywania indeksu przy każdej fakturze
- [GH-148](https://github.com/Dev10x-Guru/ksef-mcp/issues/148) — Kod weryfikacyjny ze skrótu z indeksu
- [PR #208](https://github.com/Dev10x-Guru/ksef-mcp/pull/208) — Implementacja tej decyzji
