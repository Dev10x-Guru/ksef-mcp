# ADR-108: Język komunikatów i granica warstwy `messages.py`

- **Date:** 2026-09-20
- **Status:** Accepted
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-sonnet-5)
- **Reviewed-by:** —
- **Sources:** `docs/memos/architecture-audit-2026-09-19.md`;
  zgłoszenie [#163](https://github.com/Dev10x-Guru/ksef-mcp/issues/163);
  [D-040](../domain/decisions.md#d-040--co-jest-zmianą-łamiącą-trzy-powierzchnie-objęte-semver)
- **Depends-on:** [D-004](../domain/decisions.md#d-004--token-ksef-w-keyringu-systemowym-z-twardym-fallbackiem-zamiast-promptu)

## Kontekst

`messages.py` istnieje jako warstwa prezentacji — jego własny docstring
mówi, że „jedna funkcja pyta albo rozgałęzia, druga zamienia wartość na
tekst". Mimo to jest importowany w dokładnie jednym miejscu: `cli.py`.
Serwer MCP nigdy z niego nie korzystał, a `assess()` (`review.py`),
`summarise()` (`listing.py`), `statement.py` i `synchronisation.py`
składają wieloznaniowe komunikaty wprost w modułach dziedzinowych.

Audyt architektury (2026-09-19) znalazł dwa osobne problemy pod tą samą
etykietą „niespójne komunikaty":

1. **Czysty dług, bez osądu.** Zdanie „Brak konfiguracji. Uruchom
   najpierw: ksef-mcp onboarding" istniało w trzech niezależnych
   literałach (`server.py`, dwa razy `cli.py`), plus czwarta,
   odrębnie sformułowana wersja w `messages.describe_subject`.
2. **Granica języka szła wg modułu, nie wg odbiorcy.**
   `statement.UnreadablePeriod` i `sync_store.SyncStateUnreadable`
   pełnią identyczną rolę — odmawiają odczytu czegoś, czego ta wersja
   nie rozumie — a jedna mówiła po polsku, druga po angielsku.
   `reported()` w `server.py` sklejał angielski szablon z treścią
   wyjątku, która bywała polska. Powodem nie był świadomy podział wg
   odbiorcy (LLM kontra człowiek) — obie ścieżki trafiają do tego
   samego klienta MCP — tylko to, w jakim module dana klasa wyjątku
   akurat powstała.

## Decyzja

**Konsolidacja bez pełnej migracji.** `messages.py` NIE staje się
jedynym miejscem komunikatów w projekcie. Zdania nierozerwalnie
związane z decyzją progową, którą opisują — `listing.summarise` i
`review.assess` w szczególności — zostają w swoich modułach
dziedzinowych. To świadoma granica, nie przeoczenie: rozdzielenie
liczby/progu od zdania, które go tłumaczy czytelnikowi, zwiększyłoby
ryzyko, że któreś z nich zmieni się bez drugiego.

**Cztery kopie „brak konfiguracji" → jedna funkcja.** Dodano
`messages.describe_not_configured()`. Trzy literalne kopie
(`server.py`, `cli.py` ×2) wywołują ją teraz zamiast własnego
literału. `messages.describe_subject` zostaje odrębny celowo: renderuje
jeden wiersz tabeli stanu (`doctor`), nie odmowę zatrzymującą polecenie.

**Wszystkie komunikaty, które dochodzą do klienta MCP lub do
terminala, są po polsku** — zgodnie z regułą CLAUDE.md „dokumentacja,
rozmowa i wyniki pracy po polsku". Odbiorcą `ToolError` jest agent LLM
czytający okno czatu, nie log; to samo dotyczy wyjątków przechwytywanych
przez `reported()` (`KsefMcpError`, `NotConfigured`) i komunikatów CLI.
Po angielsku zostają wyłącznie: nazwy identyfikatorów, docstringi,
komunikaty logów technicznych (`technical_log()`) i nazwy narzędzi MCP
(`AuditedOperation`, np. `synchronise_invoices` — to nazwa maszynowa,
nie proza).

Skutkiem tego wyboru: ujednolicono na polski pięć wywołań
`storage.require_schema` (`audit.py`, `config.py`, `review.py`,
`archive.py`, `sync_store.py`) oraz szablon w `reported()`
(`server.py`), który wcześniej sklejał angielskie „needs configuration
first" / „could not finish" z treścią wyjątku.

### Dlaczego konsolidacja częściowa zamiast pełnej migracji do `messages.py`?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Wybrane: konsoliduj czysty dług, udokumentuj granicę** | Usuwa realne rozjazdy (cztery kopie, dwa języki) bez ryzyka rozerwania zdania od progu, który opisuje | Wymaga czytania obu miejsc (`messages.py` i moduł dziedzinowy), żeby znaleźć jeden komunikat |
| Pełna migracja do `messages.py` | Jedno miejsce dla każdego zdania | Odrywa `listing.summarise`/`review.assess` od progów, które tłumaczą — ryzyko rozjazdu przy przyszłej zmianie progu |
| Zostawić jak było | Zero ryzyka regresji teraz | Cztery kopie tego samego zdania i dwujęzyczne wyjątki tej samej roli zostają jako dług, który audyt już raz znalazł |

## Uzasadnienie

Duplikacja czterech kopii jednego zdania jest długiem bez kompromisu —
nic nie broni tego, że „Brak konfiguracji..." istnieje cztery razy.
Rozjazd językowy między `UnreadablePeriod` i `SyncStateUnreadable` też
nie miał uzasadnienia dziedzinowego: obie klasy odmawiają z tego samego
powodu (nieczytelny format) i trafiają do tego samego odbiorcy. Pełna
migracja wszystkiego do `messages.py` byłaby jednak reflexem, przeciw
któremu ostrzega samo zgłoszenie #163 — `listing.summarise` i
`review.assess` opisują próg, który jest częścią logiki dziedzinowej
modułu, nie prezentacji, i przeniesienie samego zdania bez progu
zostawiłoby dwa miejsca do zmiany zamiast jednego.

## Konsekwencje

**Pozytywne:**
- Jedno miejsce zmienia zdanie „brak konfiguracji" dla całego projektu.
- Każdy wyjątek odmowy schematu (`require_schema`) mówi tym samym
  językiem do tego samego odbiorcy.
- Granica `messages.py` jest teraz nazwana, nie tylko widoczna
  w strukturze plików — przyszły PR wie, gdzie nowy komunikat ma
  trafić.

**Negatywne:**
- `messages.py` nadal nie jest jedynym źródłem tekstu w projekcie —
  trzeba pamiętać o wyjątku dla progów dziedzinowych.
- Migracja dotknęła pięć modułów magazynu i testy asercji tekstu
  wyjątków (`tests/test_audit.py`, `tests/test_review.py`,
  `tests/test_sync_store.py`, `tests/test_storage.py`,
  `tests/test_archive.py`) — koszt jednorazowy, opłacony w tym PR-ze.

## Powiązane

- [D-040](../domain/decisions.md#d-040--co-jest-zmianą-łamiącą-trzy-powierzchnie-objęte-semver)
  — rozstrzyga osobno, że treść komunikatu błędu NIE jest częścią
  kontraktu SemVer (kształt pola jest).
- [#163](https://github.com/Dev10x-Guru/ksef-mcp/issues/163) — zgłoszenie
  źródłowe.
