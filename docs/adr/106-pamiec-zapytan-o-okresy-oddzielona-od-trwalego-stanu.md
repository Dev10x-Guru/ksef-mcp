# ADR-106: Pamięć zapytań o okresy, oddzielona od trwałego stanu

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-006, D-020, D-021, D-032; `docs/domain/epics.md` T-06a; zgłoszenie [#39](https://github.com/Dev10x-Guru/ksef-mcp/issues/39); [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)

## Kontekst

[D-021] ustalił, że druga odpowiedź na to samo pytanie powinna kostować zero zapytań — jeśli księgowa pyta o wrzesień dwukrotnie, druga godzina nie robi się mniejsza. Serwer MCP pod `uvx` bywa ubijany razem z sesją agenta [D-033], więc bez rekordu na dysku druga odpowiedź i tak byłaby droga.

[ADR-103] umieścił punkt kontynuacji w katalogu danych, bo jego utrata kosztuje pełną resynchronizację — operacji, która mierzy się dniami przy limicie dwudziestu eksportów na godzinę. Tutaj stawka jest odwrotna: zapomnienie odpowiedzi kosztuje jedno pytanie, a czyszczarka dysku honorująca konwencję cache może usunąć katalog w dowolnym momencie.

Dwa różne ryzyka, dwa różne katalogi.

## Decyzja

### 1. Pamięć pytań mieszka w katalogu cache'u, osobno od stanu synchronizacji

`src/ksef_mcp/period_cache.py` zapisuje odpowiedzi w katalogu podręcznym:

```
<platformdirs.user_cache_path>/subjects/<NIP>/<środowisko>/periods/
└── <kierunek>-<skrót_okna>.json
```

Katalog cache'u, nie danych: konwencja wyrażona w nazwa katalogu `cache` oraz w standardzie `freedesktop.org` mówi jasno, że jego zawartość wolno skasować w dowolnym momencie. Czyszczarka dysku honorująca tę konwencję nie rusza punktów kontynuacji, archiwum czy indeksu deduplikacji, które żyją w katalogu danych. Rozdział per NIP **i per środowisko** — z tego samego powodu, dla którego go stosuje `SyncStore` [ADR-103 §1].

Ścieżkę wyznacza `platformdirs.user_cache_path(appname=SERVER_NAME)`, nigdy sklejanie ręczne, bo platforma sobie zasługuje na respect.

### 2. Jeden JSON per pytanie: okno, kierunek, chwila, odpowiedź

```json
{
  "schema_version": 1,
  "nip": "1234567890",
  "environment": "test",
  "direction": "buyer",
  "queried_at": "2026-09-14T06:00:00+00:00",
  "period": {
    "date_from": "2026-09-01T00:00:00+00:00",
    "date_to": "2026-09-30T00:00:00+00:00",
    "date_type": "issue"
  },
  "page": {
    "invoices": [...],
    "has_more": true,
    "truncated": true,
    "hwm_date": "2026-09-30T23:59:00+00:00"
  }
}
```

`queried_at` to znacznik T-06a: czyli chwila, w której naprawdę zapłacono za tę odpowiedź budżetem. Leży **przy odpowiedzi, której dotyczy**, nie w osobnym rejestrze, bo znacznik bez odpowiedzi byłby kłamstwem — mówił, że okres pobrano, nie mając czego pokazać.

Kwoty (brutto, netto, VAT) są **stringami**, nie liczbami, bo JSON konwertuje `Decimal` na binarne ułamki, a grosz utracony na zaokrągleniu w cache'u byłby rozbieżnością wobec odpowiedzi KSeF aplikacji.

### 3. Uszkodzony wpis to brak trafienia, nigdy błąd

```python
try:
    cached = _decode(json.loads(path.read_text()))
    return cached
except (OSError, ValueError, KeyError, TypeError, KsefPortError):
    # Plik obcięty? Brak trafienia. Zapytaj KSeF raz jeszcze.
    return None
```

Inaczej niż `SyncStore` [ADR-103], który odmawia zgadywania punktu kontynuacji (bo źle zrozumiany omija faktury), tutaj uczciwa odpowiedź do usuniętego wpisu jest ponownym zapytaniem — wpis jest przecież odtwarzalny z definicji. Jeśli czyszczarka obcięła plik, najgorsza rzecz to odmówić odpowiedzi zamiast zapytać KSeF. Więc pytamy.

### 4. Okno bez końca nie zostaje zapamiętane

`Period.for_synchronisation` nie nazwą okresu, bo koniec jest otwarty i rośnie — zapamiętana odpowiedź podałaby wczorajsze faktury na jutrzejsze pytanie. `is_cacheable(period)` sprawdza `period.date_to is not None`, i koniec.

### 5. Klucz obejmuje kierunek i okno, ale nie NIP czy środowisko

```python
def cache_key(*, period: Period, direction: InvoiceDirection) -> str:
    ends = "open" if period.date_to is None else period.date_to.isoformat()
    spelling = f"{direction}|{period.date_type}|{period.date_from}|{ends}"
    return f"{direction}-{hashlib.sha256(spelling.encode()).hexdigest()[:32]}"
```

NIP i środowisko są już w ścieżce, więy klucz nie powtarza ich. Kierunek natomiast jest **wewnątrz** klucza, bo ta sama firma bywa sprzedawcą na jednej fakturze i nabywcą na następnej — jeden klucz dla obu podałby odpowiedź nabywcy na pytanie sprzedawcy [D-031 §5].

### 6. Plik `0600`, katalog `0700`, zapis przez `temp → rename` z `fsync`

Metadana zawiera nazwę kontrahenta [D-011], więc wpis ma sens ochrony dostępu od razu — tworzy się z docelowym trybem, nie przepisuje się potem:

```python
descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
```

Zapis idzie przez plik tymczasowy, `fsync` i `os.replace`, bo pół faktury na dysku byłaby co najmniej dziwna, a przerwany zapis kosztowałby powtórzenia budżetu bez powodu [D-006].

### Dlaczego katalog cache'u zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **Cache root, konwencja usuwalna** | czyszczarka nie rusza punktów kontynuacji; jedno pytanie na stracę cache'u odpowiada ograniczeniu, które je wymusza | musi być odrębna ścieżka |
| Dane root, osobny katalog | wspólne miejsce składowania | usunięcie cache'u wymusi całą resynchronizację; czyszczarka dysku nie robi różnicy między cache a danymi |
| W pamięci procesu | żaden I/O | ginie przy restarcie serwera; dokładnie to, co chcemy uniknąć |

## Uzasadnienie

Pamięć pytań ma dwa oblicza. Pierwsze: skrócić czas odpowiedzi, gdy agent ponawia pytanie kilka razy w sesjach [D-020]. Drugie — opisane wprost w [D-021] — nie tracić jednego z dwudziestu zapytań na godzinę, bo książę zapytał dwa razy.

Dwa katalogi wybieramy dlatego, że rozbieżne są konsekwencje utraty. Utrata stanu synchronizacji — punkt kontynuacji — to dias zamiast godzin pracy, bo limit dwudziestu eksportów na godzinę trzeba przejść od nowa. Utrata cache'u to jedno pytanie, które koszt ograniczenia uzasadnia w istocie.

Przyjęty kompromis: klucz AES w [ADR-103], teraz kwoty na stringach — i cache'a, i archiwum — leżą na dysku jawnie. Argument się nie zmienia: odszyfrowane faktury trafiają do katalogu danych obok tych kluczy, więc klucz obok odpowiedzi nie otwiera niczego, czego już tam nie ma [ADR-103].

## Konsekwencje

**Pozytywne:**
- Pytanie o ten sam okres w drugiej sesji nie kosztuje budżetu [D-021].
- Usunięcie cache'u — ręcznie albo przez czyszczarkę — kosztuje jedno ponowne pytanie, nigdy pełnej resynchronizacji.
- Odpowiedzi żyją tam, gdzie system oczekuje cache'u; punkty kontynuacji i archiwum nie są zagrożone.
- Wznowienie serwera przywraca pamięć z dysku bez koniczności przechowywania jej w keyring lub w bazie danych.

**Negatywne:**
- Kwoty przechowywane na stringach wymagają konwersji — `Decimal(str(kwota))` zamiast przyjęcia liczby.
- Jeśli schemat zmieni się pierwszy raz, brakuje migracji. Rekord w nowszej wersji jest odrzucany zamiast konwertowany. Świadome do czasu, aż to się stanie.
- Cache'u nie ma co eksportować ani archiwizować dla audytu. Odpowiedzi na pytania tracą się z treścią cache'u.

## Powiązane

- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — punkt kontynuacji i katalog danych (kontrast: tutaj cache)
- [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md) — archiwum i indeks deduplikacji (kontrast: osobne, w katalogu danych)
- [D-021], [D-032], [D-006], [D-031 §5] w `docs/domain/decisions.md`
- [#39](https://github.com/Dev10x-Guru/ksef-mcp/issues/39) — zgłoszenie realizowane tym dokumentem
