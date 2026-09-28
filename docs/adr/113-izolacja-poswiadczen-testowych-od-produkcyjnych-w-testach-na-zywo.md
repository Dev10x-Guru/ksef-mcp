# ADR-113: Izolacja poświadczeń testowych od produkcyjnych w testach na żywo

- **Date:** 2026-09-26
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** https://github.com/Dev10x-Guru/ksef-mcp/pull/267; konwencja z bl-zebra i tt-pos; [D-004](../domain/decisions.md#d-004--token-ksef-w-keyringu-systemowym-z-twardym-fallbackiem-zamiast-promptu); CLAUDE.md — zasady bezpieczeństwa KSeF
- **Depends-on:** [D-004](../domain/decisions.md#d-004--token-ksef-w-keyringu-systemowym-z-twardym-fallbackiem-zamiast-promptu) — ścieżka awaryjna dla tokenu bez magazynu kluczy

## Kontekst

Testy `@pytest.mark.ksef_live` sięgają do prawdziwego rejestru testowego KSeF. Na maszynie opiekuna projektu, na której podmiot jest skonfigurowany na produkcję, wszystkie te testy pomijają się — słusznie, bo nigdy nie idą na produkcję.

Potrzeba: opiekun chce jednej komendy `make test-live` na swoją maszynę, aby sprawdzić kontrakt z KSeF pomiędzy przebiegami CI. Dziś to wymaga ręcznego wywołania `uv run pytest -m ksef_live` ze znajomością flagi, a testy pozostają pominięte.

Ograniczenia (z CLAUDE.md):
- Poświadczenia są sekretami — `KSEF_TOKEN` jest jedyną wspieraną zmienną, `KSEF_NIP` nie istnieje
- NIP i token nigdy nie trafiają do logów ani komunikatów
- Nigdy nie wołaj produkcyjnego KSeF z testów — default to TEST, a każde wywołanie SDK w porcie przechodzi środowisko jako jawny argument
- Pierwszym bezpiecznikiem jest wpisanie środowiska na sztywno w kodzie (CLAUDE.md), nie wybranie zmienną

## Decyzja

Nowy plik `bin/ksef_live.py` — skrypt uruchomieniowy przygotowujący testy na żywo. Skrypt:

1. **Czyta poświadczenia** z pliku `ksef.secrets.env` albo zmiennych `KSEF_LIVE_TEST_NIP` i `KSEF_LIVE_TEST_TOKEN` (zmienne mają pierwszeństwo). Plik jest ignorowany przez git (konwencja bl-zebra), a szablon `ksef.secrets.env.example` jest commitowany.

2. **Pisze osobną konfigurację** z `environment: "test"` wpisanym na sztywno do `.tmp/ksef-live/dane/ksef-mcp/configuration.json`. Własna konfiguracja na produkcję zostaje nietknięta.

3. **Ustawia `XDG_DATA_HOME`** na `.tmp/ksef-live/dane` tylko na czas biegu (zanim pytest załaduje rzeczywistą konfigurację ze starego miejsca).

4. **Podaje token** ścieżką awaryjną `KSEF_TOKEN` (D-004), a nie nową zmienną — token jest kluczowany samym NIP-em, wspólnym dla TEST i produkcji, więc wpis w keyringu nigdy nie jest czytany ani nadpisywany.

5. **Nigdy nie wypisuje NIP ani tokenu** (`Credentials.__repr__` ukrywa wartości).

6. **Środowisko jest stałą w kodzie**, nie parametrem — jedyny sposób, aby ominąć bezpiecznik, to edycja skryptu.

Ten sam skrypt uruchamia workflow CI `ksef-live.yml`, gdzie sekrety pochodzą ze środowiska GitHub `ksef-test`.

```python
# Środowisko zacodowane: nie ma drogi, którą mogłaby się zmienić
ENVIRONMENT = "test"

# Poświadczenia z pliku lub zmiennych
credentials = resolved_credentials(environment=os.environ, root=REPOSITORY_ROOT)

# Izolowana konfiguracja w katalogu tymczasowym
live = prepared_live_directory(credentials=credentials, root=REPOSITORY_ROOT)

# Bieg z własnym XDG_DATA_HOME
run_environment = {
    **os.environ,
    "XDG_DATA_HOME": str(live / "dane"),
    "XDG_CACHE_HOME": str(live / "pamiec"),
    "KSEF_TOKEN": credentials.token,  # ścieżka awaryjna
}
```

Makefile dostaje nowy cel `make test-live`.

## Uzasadnienie

**Dlaczego `bin/ksef_live.py` zamiast `conftest.py`?**

Skrypt musi ustawić `XDG_DATA_HOME` zanim pakiet załaduje konfigurację — przed `import ksef_mcp`. To się dzieje w `conftest.py` za późno. Dodatkowo pakiet nie dostaje nowej zależności (`python-dotenv`).

**Dlaczego `.secrets.env` zamiast `KSEF_LIVE_TOKEN` i `KSEF_LIVE_NIP`?**

Nazwa `KSEF_LIVE_TEST_*` jasno wskazuje typ wartości (test subject, nie produkcja) i rozróżnia je od zmiennych systemowych. Konwencja pliku przejęta z bl-zebra (podobną mają tt-pos i Dev10x-Claude) — nieśledzony plik + commitowany szablon.

**Dlaczego `KSEF_TOKEN` (zamiast nowej zmiennej)?**

Token jest kluczowany samym NIP-em, wspólnym dla TEST i produkcji. Nowa zmienna mogłaby wskazywać na inny wpis w keyringu — co oznaczałoby, że czytanie produkcyjnego tokenu z keyringa mogłoby się cofnąć. `KSEF_TOKEN` to ścieżka awaryjna z D-004, przeznaczona dokładnie dla takiego przypadku (headless, bez magazynu kluczy).

**Dlaczego hardcoded `test` zamiast zmiennej?**

To jedyny bezpiecznik, który działa — jeśli środowisko dałoby się wybrać zmienną (czy to `KSEF_LIVE_ENVIRONMENT` czy `KSEF_ENV`), każde pominięcie lub zła domyślna wartość mogłaby skierować bieg na produkcję. CLAUDE.md eksplicytnie zakazuje tego wyboru dla dokładnie tego powodu. Skrypt zamiast zmiennych.

## Konsekwencje

**Pozytywne:**
- Opiekun sprawdza kontrakt z KSeF lokalnie bez utraty dostępu do konfiguracji produkcyjnej
- Sekrety testowe są przechowywane poza gitems (`.gitignore: *.secrets.env`)
- Token ani NIP nigdy nie wycieką do komunikatów błędów ani logów
- Ten sam skrypt uruchamia testy w CI (`ksef-live.yml`) — konsystencja między lokalnym i automatycznym

**Negatywne:**
- Nowy skrypt do utrzymania (`bin/ksef_live.py` + `bin/test_ksef_live.py`)
- Dodatkowy katalog `.tmp/ksef-live/` w `.gitignore`
- Deweloper musi skopiować szablon (`cp ksef.secrets.env.example ksef.secrets.env`) zanim pierwszy raz uruchomi `make test-live`

## Powiązane

- [D-004](../domain/decisions.md#d-004--token-ksef-w-keyringu-systemowym-z-twardym-fallbackiem-zamiast-promptu) — ścieżka awaryjna dla tokenu
- [Zgłoszenie GH-265](https://github.com/Dev10x-Guru/ksef-mcp/issues/265)
- [PR #267](https://github.com/Dev10x-Guru/ksef-mcp/pull/267)
