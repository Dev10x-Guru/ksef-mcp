# ADR-106: Dziennik audytu — rejestracja każdego dostępu do faktur

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-005, D-006, D-011, D-032, D-034; zgłoszenie [#45](https://github.com/Dev10x-Guru/ksef-mcp/issues/45); [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md)
- **Refines:** [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md)

## Kontekst

Zgodność z przepisami wymaga wykazania, którą upoważnioną osobę, na jakiej podstawie, kiedy i do jakich danych dotarła w ramach systemu. KSeF nigdy nie sygnalizuje błędu, gdy agent pobiera faktury podmiotu A w odpowiedzi na pytanie o podmiot B — uprawnienie naprawdę istnieje, zapytanie jest well-formed, API odpowiada. Nic w protokole nie rozróżnia dostępu zamierzonego od niezamierzonego. Rozróżnia je tylko rejestr: kto działał pod którym NIP-em, na jakiej podstawie, wobec jakich kryteriów, które numery KSeF wróciły. Jeśli tego rejestru tu nie ma, to go nie ma.

Zgodnie z [D-032] i [D-034], ryzyka pomieszania klientów jednego biura rachunkowego są karalne. Osobne katalogi per podmiot i środowisko już je załatwiają. Teraz trzeba, żeby dziennik dostępu żył w tym samym miejscu — żeby jeden klient nigdy nie przeglądał śladów drugiego.

Dodatkowo, z [D-011]: rozpoznanie faktury jako już posiadanej to zdarzenie inne niż jej zapis — pierwszy oznacza dostęp, drugi oznacza ujawnienie. Ślad złożony wyłącznie z zapisów przybiera postać: ta faktura nigdy nie była dotykana. To jest fałsz. Trzeba to udokumentować osobno.

## Decyzja

### 1. Nowy moduł `src/ksef_mcp/audit.py` — dziennik JSON Lines, append-only

```
<katalog danych>/subjects/<NIP>/<środowisko>/
├── synchronisation.json      # ADR-103
├── deduplication.json        # ADR-105
├── audit.jsonl              # ← nowy: jeden wpis per linia, format JSON
└── invoices/
    └── <NumerKSeF>.xml
```

Każdy wpis to jedno zdarzenie dostępu — zapisanie, pokazanie modelowi albo pominięcie w deduplikacji. Wpisy się nie przepisują, nie edytuje się ich środku, nie modyfikuje się całego pliku po każdej linii. Zamiast tego: `os.open(..., O_APPEND|O_CREAT, 0o600)` i `fsync` po każdym zapisie. Jedna operacja `write()` na deskryptorze `O_APPEND` jest niepodzielna względem innych pisarzy, więc dwaj pracownicy biura działający dla tego samego podmiotu nie mogą przeplatać linii.

Katalog dostępu `0o700`, plik `0o600` — wyłącznie właściciel odczytuje ślad.

### 2. Taksonomia zdarzeń — trzy rodzaje ujawnienia (D-011)

```python
class Disclosure(StrEnum):
    DISK = "disk"                      # Faktury wylądowały w pliku
    MODEL_CONTEXT = "model_context"    # Metadane faktury zobaczył model w oknie czatu
    DEDUPLICATION_SKIP = "deduplication_skip"  # Faktura widziana, już posiadana
```

Osobne zdarzenia, bo „model zobaczył dziesięć numerów" ≠ „dziesięć plików powstało". Bez rozdzielenia:
- Ślad z samymi zapisami mówi: ta faktura nigdy nie była dotykana (fałsz, była widziana jako duplikat)
- Ślad z samymi modelami mówi: żaden plik nie powstał (fałsz, pliki są w archiwum)

### 3. Co zawiera każdy wpis

```json
{
  "schema_version": 1,
  "recorded_at": "2026-09-14T10:30:00+00:00",
  "operation": "synchronise_invoices",
  "nip": "1234567890",
  "environment": "test",
  "authorisation_basis": "ksef_token:keyring",
  "disclosure": "disk",
  "subject_role": "buyer",
  "criteria": "export packages up to 2026-09-10T00:00:00+00:00",
  "document_count": 5,
  "ksef_numbers": ["1234567890-20260901-0100AB12CD01-56", "..."],
  "output_path": "/dane/subjects/1234567890/test/invoices",
  "formats": ["xml"]
}
```

- **Nigdy wartość tokenu** — tylko źródło (`keyring`, `environment`). Tajemnica się tu nie trafia w ogóle.
- **Nigdy treść faktury** — tylko numer KSeF. Niezmiennik pozwala na odtworzenie dostępu.
- **Moment w UTC** — dla każdego wpisu osobno, tak żeby dysputa miała dokładny ślad czasowy.
- **Kryteria zapytania** — żeby czytający widział, o jaki zakres pytano.
- **Liczba i numery** — razem, bo pięćdziesiąt numerów w oknie czatu to hałas, ale zapis dostępu bez numerów to nie-zapis.

### 4. Schema versioning — odmowa odgadywania

Gdy linia ma `schema_version` inny niż Current, `AuditTrailUnreadable` — nie próbujemy odczytać. Ślad czytany przez zły schemat albo wymyśla dostęp, albo go ukrywa. Oba są gorsze w sporze niż przyznanie: „tego pliku nie mogę przeczytać".

### 5. Integracja z czterema narzędziami odczytu

- `synchronise_invoices` — wpisy `DISK` (co wylądowało) i `DEDUPLICATION_SKIP` (co było duplikatem)
- `list_recent_invoices` — wpis `MODEL_CONTEXT` (co zobaczył model)
- `export_period_statement` — wpis `DISK` (plik CSV powstał), bez `MODEL_CONTEXT` (numery KSeF nie trafiają do odpowiedzi)
- `review_new_invoices` — wpis `MODEL_CONTEXT` (co rozpoznano jako nowe)

Ślad powstaje niezależnie od braku bramki zgody [D-011] — rozliczalność i zgoda to dwie różne sprawy. Jeśli odczyt nie wymaga zgody człowieka, to nie oznacza, że nie wymaga rejestru.

### 6. Dlaczego append-only zamiast temp→rename (D-006)?

Divergencja od [D-006]:

| Aspekt | InvoiceArchive (D-006) | AuditTrail (tu) |
|---|---|---|
| Czytanie | Całego pliku naraz | Nigdy nie czyta się go w całości |
| Edycja | Przepisuje środek | Nigdy się nie edytuje |
| Inwariant | Całego pliku (brak pół-pliku) | Tylko dopisywania (działa append zawsze) |
| Koszt skalowania | O(1) na domknięcie | O(n) na zapis, gdyby przepisywać |
| Bezpieczeństwo | temp→rename chroni spójność | Jedna operacja write() na O_APPEND jest atomowa |
| Dowód | Przechowywane przez wiele lat | Przeżyje długo po dacie retencji faktury |

Przepisywanie całości na każdy wpis byłoby:
- Kwadratowe po liczbie wpisów i liczbie dni pracy serwera
- Zagrażałoby całemu dotychczasowemu dowodowi, żeby dołożyć jedną linię
- Byłoby gorszą grą niż nigdy — pół-zapis zamiast rozproszonego logu to żaden log

Jedna linia `fsync` po każdym wierszu się bierze pod uwagę — dowód, który nie przeżył zaniku zasilania, to nie-dowód.

### 7. Dlaczego per subject i per environment (nie global)?

Zgodnie z [D-032] i [D-034]: wspólny dziennik to główny wektor pomieszania klientów biura rachunkowego. Osobny katalog per podmiot i środowisko to już reguła w systemie; dziennik się do niej stosuje. Dysponent B nigdy nie powinien móc odczytać (nawet przypadkowo) co dysponent A miał dostęp.

### 8. Ślad się pojawia niezależnie od zgody (D-011)

Odczyt bez bramki potwierdzenia i tak zostawi wpis. Rozliczalność to prawo, zgoda to fakt. Ich decyzje są niezależne:
- Jeśli odczyt wymaga zgody i ją dostaniemy, rejestr istnieje.
- Jeśli odczyt wymaga zgody i jej nie dostaniemy, rejestr... też istnieje, bo system zarejestrował próbę.
- Jeśli odczyt nie wymaga zgody, rejestr istnieje, bo zobowiązanie do ujawnienia nie znika, kiedy nie pytamy.

## Uzasadnienie

Spór o wyciek to jedyne źródło prawdy — papier. Jeśli rejestru tutaj nie ma, to mówi się: „nie mieliśmy tego". Jeśli jest, ale pisze tylko co wylądowało na dysku, to mówi się: „ta osoba nigdy tego nie widziała" — i to może być fałsz. Jeśli jest, ale bez numerów KSeF, to mówi się: „to się stało, ale nie wiemy co" — nie-zapis.

Trzy osobne rodzaje ujawnienia to to, co [D-011] wymaga — rozróżnienie między dostępem i ujawnieniem. Samo to wymaga osobnego pola w każdym wpisie.

Append-only to wyższy koszt operacji niż temp→rename, ale zysk się bierze: dowód nie rośnie kwadratowo, nie stawia się całego logu pod ryzyko, jedna linia fsync jest niepodzielna. Dla logu, który może żyć przez lata, to argument przeważający.

Schema versioning to konserwacja — kiedy schema się zmieni, chcemy wiedzieć, co czytamy, albo chcemy wiedzieć, że nie wiemy. Odmowa odgadywania to nie błąd w kodzie; to bezpieczeństwo.

## Konsekwencje

**Pozytywne:**
- Każdy dostęp do faktur jest nieodwołalnie rejestrowany, co pozwala na odtworzenie w sporze o wyciek
- Rozróżnienie między dostępem (widzenie metadanych) i ujawnieniem (zapis lub model) jest jawne w każdym wpisie
- Duplikaty są logowane osobno, żeby ślad nie czytał się jako puste miejsce
- Osobny katalog per podmiot/środowisko eliminuje ryzyko pomieszania klientów biura
- Schema versioning pozwala na bezpieczne ewolucje bez stracenia interpretacji historii
- Append-only nie stawia historii pod ryzyko przy każdym nowym dostępie
- Minimalna PII w logu: numery KSeF, nie imiona ani treści

**Negatywne:**
- Dodatkowy I/O: każdy dostęp musi wykonać fsync, co ma koszt
- Każde z czterech narzędzi odczytu musi wiedzieć o audycie i dostarczyć dostatecznie szczegółowe dane
- Dodatkowe pola w `Statement` (ksef_numbers) tylko dla audytu, a nie dla użytkownika
- Long-term storage — logi rosnące latami zajmą miejsce
- Jeśli schema zmieni się w niekompatybilny sposób, stare wpisy będą nieodczytalne (ale to celowe — bezpieczeństwo)

## Powiązane

- [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md) — struktura katalogów per podmiot/środowisko
- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — stan synchronizacji, na którym audyt się buduje
- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — port KSeF, który dostarcza dane do audytu
- `docs/domain/decisions.md` [D-006] — temp→rename pattern dla plików
- `docs/domain/decisions.md` [D-011] — rozróżnienie dostępu i ujawnienia
- `docs/domain/decisions.md` [D-032] — katalog danych vs cache
- `docs/domain/decisions.md` [D-034] — ryzyko pomieszania klientów biura
- [GH-45](https://github.com/Dev10x-Guru/ksef-mcp/issues/45) — czego wymaga compliance
