# ADR-101: Integracja SDK ksef2 i bezpieczeństwo limitów stawek

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** janusz-skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** [PR #23](https://github.com/Dev10x-Guru/ksef-mcp/pull/23); praktyka Ministerstwa Finansów ze śledzenia wzorców powtórzeń; [D-020](docs/domain/decisions.md) — Paginacja nie jest sterowana przez model; budżet zapytań jest liczony

## Kontekst

Biblioteka `ksef2` oferuje warstwę abstrakcji nad API KSeF, ale ma dwa ryzykowne ustawienia domyślne:

1. **Domyślne środowisko to PRODUCTION.** Błąd w kodzie lub pominięta zmiana zamieniłyby testowy workflow w operację podatkowo-prawnie wiążącą. Limity produkcji są dziesięciokrotnie poniżej limitów testowych, więc ta sama logika na produkcji błyskawicznie trafia na blokadę.

2. **Biblioteka automatycznie ponawia żądania na HTTP 429** (limit wyczerpany): trzy próby z backoffem ograniczonym do czterech sekund. Rzeczywisty `Retry-After` z KSeF mierzony jest w minutach, więc pętla nie doczeka naturalnego końca limitu — robi to, co wygląda na obchodzenie limitów. Ministerstwo Finansów rejestruje takie wzorce i blokuje podmioty, które je wykazują; blokada wydłuża się przy powtórzeniach.

3. **Projekt ma już politykę budżetu zapytań** ([D-020](docs/domain/decisions.md)): każde zapytanie, błędne czy prawidłowe, jest liczone. Automatyczne ponawianie marnuje budżet godzinowy bez kontroli ze strony operatora.

## Decyzja

Wprowadzić warstwę ochrony (`ksef_port.py`) między SDK a resztą kodu:

1. **Brak automatycznych powtórzeń**: skonfigurować `RetryConfig(max_attempts=1)` — jedno wywołanie, potem podnieść wyjątek zawierający `retry_after`. To przekazuje decyzję o czekaniu/ponawianiu do operatora.

2. **Zawsze jawnie przekazywać środowisko**: każde instantiowanie `Client(...)` wymaga parametru `environment=ENVIRONMENTS[configured_environment]`. SDK domyślnie rozumie `Environment.PRODUCTION`; kod zawsze mówi „nie — to jedno".

3. **Domyślne środowisko to TEST**: `DEFAULT_ENVIRONMENT = KsefEnvironment.TEST`. Nigdy PRODUCTION. Operator musi ustawić to jawnie w konfiguracji.

4. **Opakowanie SDK**: nowy moduł `ksef_port.py` eksportuje:
   - `check_connection(nip, token, environment)` — jedno bezpieczne wywołanie
   - Wyjątki domeny: `KsefRateLimited(message, retry_after=...)`, `KsefAuthenticationFailed`, `KsefUnreachable`
   - Mapowanie `KsefEnvironment(TEST|DEMO|PRODUCTION)` ↔ `ksef2.Environment.*`

5. **System konfiguracji**: `Configuration` dataclass przechowuje NIP, środowisko, backend keyringu, katalog faktur. Zapisywany w JSON z trybem `0o600`. Domyślna ścieżka: `~/.local/share/ksef-mcp/configuration.json` (via `platformdirs`).

6. **Token z fallbackiem bez keyringu**: `token_store.py` zapisuje w keyringu, ale gdy niedostępny (headless, WSL, kontener), sprawdza zmienną `KSEF_TOKEN`. Pierwszeństwo: jawnie eksportowana zmienna > keyring (kto ją ustawia, wie co robi).

### Dlaczego jedno wywołanie i jawne parametry zamiast alternatyw?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Wybrane: max_attempts=1 + jawne env** | Ministerstwo nie rejestruje wzorców powtórzeń. Operator kontroluje retry logic. Brak niespodziewanych zmian limitów. | Wymaga obsługi `KsefRateLimited` u każdego call site. Więcej boilerplate'u. |
| Auto-retry z backoffem (default SDK) | Prostsze call site. Biblioteka obsługuje transparentnie. | Konstruuje wzorce, które Ministerstwo blokuje. Marnuje budżet zapytań bez widoczności. Brak kontroli nad retry'em. |
| Brak ponowienia (SDK max_attempts=0) | Najprościej. | Żaden błąd przejściowy się nie poprawi; użytkownik byłby zmuszony powtarzać ręcznie. |

## Uzasadnienie

Decyzja implementuje [D-020](docs/domain/decisions.md) (budżet zapytań jest liczony, nie sterowany przez model):
- **Jeden attempt**: każda próba to dolar z budżetu; brak przeznaczonych powtórzeń.
- **Jawna kontrola nad retry**: operator wie dokładnie, kiedy i ile razy się ponawiało.
- **Brak maskowania błędów**: wyjątek zawiera `retry_after`, aby dać uzyskanie odpowiedzi z serwera.

Decyzja unika [D-011](docs/domain/decisions.md) ryzyka (niezamierzone trafienie w produkcję):
- Domyślnie TEST, jawnie środowisko — linia obrony przed błędem człowieka.

Linia obrony przed tym, co Ministerstwo rejestruje jako obchodzenie limitu:
- Ministerstwo czyta wzorce HTTP ponawiań, analizuje je pod kątem automatycznego/mechanicznego obchodzenia.
- Nasze wcięte wzorce byłyby flagą.
- Jedna próba + ręczny retry operatora nie wygląda jak obchodzenie.

## Konsekwencje

**Pozytywne:**
- Ministerstwo nie blokuje podmiotu za wzorce powtórzeń.
- Budżet zapytań pozostaje pod kontrolą operatora — każde wywołanie jest widoczne.
- Brak niespodziewanych zmian limitu testowego z powodu użycia produkcji.
- Konfiguracja i token są przechowywane bezpiecznie (uprawnienia 0o600, keyring lub zmienna środowiskowa).
- Jawne środowisko w każdym miejscu oznacza, że przegląd kodu wyłapie próbę zaszycia na stałe PRODUCTION.

**Negatywne:**
- Każde call site musi obsługiwać `KsefRateLimited` i decydować, czy czekać (`retry_after`) i ponawiać.
- Więcej kodu na poziomie obsługi błędów — brak transparentnego auto-retry.
- Przejściowe błędy (timeout) wymagają ręcznego powtarzania, zamiast biblioteki obsługującej to automatycznie.
- `max_attempts=1` oznacza, że nawet przy niestabilnej sieci jedna przerwa = błąd dla operatora.

## Powiązane

- [D-011](docs/domain/decisions.md) — Narzędzie zwraca ścieżki i metadane, nigdy treść faktury do kontekstu modelu
- [D-015](docs/domain/decisions.md) — Nazwa dystrybucji `ksef-mcp` na PyPI
- [D-020](docs/domain/decisions.md) — Paginacja nie jest sterowana przez model; budżet zapytań jest liczony
- [ADR-100](docs/adr/100-decision-provenance-and-adversarial-re-derivation.md) — Prowenienacja decyzji i adwersarialna ponowna derywacja
- [PR #23](https://github.com/Dev10x-Guru/ksef-mcp/pull/23) — ✨ GH-4 Doprowadzić od instalacji do działającej konfiguracji
