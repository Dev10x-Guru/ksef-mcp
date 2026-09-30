# ADR-106: Odzyskiwanie eksportów z wygasłymi odnośnikami presigned

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** janusz-skonieczny
- **Authored-by:** agent (claude-haiku-4-5)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-033; `PR #184`; [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md); [ADR-104](104-odczyt-paczki-eksportu-i-cykl-zycia-klucza.md)
- **Refines:** [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)
- **Depends-on:** [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)

## Kontekst

[ADR-103] ustanowiło, że punkt kontynuacji przesuwa się, gdy paczka eksportowa staje się gotowa — a nie gdy jest zarchiwizowana. Ten kompromis chroni budżet, bo utrata punktu wymuszałaby pełną resynchronizację (D-032), ale ustawia pułapkę: między czasem, gdy paczka jest deklarowana gotową, a czasem jej pobrania, presigned link wygasa na własnym zegarze, niezależnym od eksportu KSeF.

Gdy punkt już przesunął się za okno, które wygasły link pokrywał, kolejne przebiegi:
- Nie pobierają tej paczki (bo punkt jest za jej oknem)
- Nie proszą o ponowny eksport (bo paczka czeka w stanie `ready`)
- Nie wiedzą o wygaśnięciu (bo URL zwróci 403 albo 410 dopiero przy próbie pobrania)

System trafia w zakleszczenie: punkt nigdy nie przesunie się dalej, bo paczka nie skończy pobierania, a paczka nie skończy pobierania, bo punkt jest poza jej oknem.

## Decyzja

### 1. Rozróżnianie wygasłych odnośników od innych odmów

Presigned link wygasa z dwoma kodem HTTP:
- `403 Forbidden` — magazyn presigned (S3-shaped) oznacza, że URL zawiera sygnaturę spoza zakresu ważności
- `410 Gone` — jawne stwierdzenie, że ten konkretny zasób już nie istnieje

Oba kody nigdy nie uzdrawiają się czekaniem — każda próba na tym samym URL zostanie odrzucona w ten sam sposób. Inne błędy (5xx, timeout, DNS) mogą być przejściowe.

```python
EXPIRED_LINK_STATUSES: Final[frozenset[int]] = frozenset({403, 410})
```

Odpowiedź `403` lub `410` przy pobieraniu części podnosi nowy wyjątek `PackageLinkExpired`, subklasę `KsefRefused`. Typ musi być odróżnialny, bo polityka ponowień (`ARCHIVING_FAILURES`) mówi „czekaj następny przebieg", a oczekiwanie dokładnie nie pomaga — wygasły link nigdy się nie ożywi.

### 2. Zapamiętywanie początku okna synchronizacji

`PendingExport` nosi teraz pole `covering_from: datetime | None` — timestamp, gdzie stał punkt kontynuacji **w momencie zainicjowania eksportu**.

Dla rekordu napisanego przed istnieniem pola (brak kompatybilności wstecz): pole deserializuje się do `None`, co mówi „okno nie zostało zapamiętane".

```python
@dataclass
class PendingExport:
    # ... istniejące pola ...
    covering_from: datetime | None = None
```

Deserializacja używa `.get()` zamiast `[...]`, żeby stary rekord bez tego pola był czytelny bez migracji schematu.

### 3. Odpytywanie KSeF o status eksportu

Gdy pobieranie części zawali się na `PackageLinkExpired`, zamiast czekać na kolejny przebieg, synchronizacja **natychmiast odpytuje KSeF** (`GET /status`), czy eksport nadal istnieje w systemie KSeF.

- Jeśli KSeF eksport **wciąż podaje**: link-uri się nie zmienił lub zmienił na żywy — czekamy. Rekord zostaje.
- Jeśli KSeF eksportu **już nie podaje**: jest on skończony w systemie KSeF i nigdy nie będzie miał żywych odnośników. Eksport usuwamy z rekordu.

### 4. Cofanie punktu kontynuacji

Gdy eksport jest potwierdzony jako nieodzyskalny (`PackageLinkExpired` i KSeF go nie podaje), punkt kontynuacji cofa się do **początku okna, które ta paczka miała pokryć**.

```python
def rolled_back_to(export: PendingExport, *, stored: DirectionState | None) -> datetime:
    """Gdzie punkt wraca, gdy eksport jest potwierdzony jako nieodzyskalny."""
    remembered = (
        export.started_at - MAX_QUERY_WINDOW
        if export.covering_from is None
        else export.covering_from
    )
    return remembered if stored is None else min(remembered, stored.reached)
```

- Rekord, który pamiętał `covering_from`: punkt wraca dokładnie do `covering_from`
- Rekord bez `covering_from` (zapisany przed tą zmianą): szacujemy okno jako `started_at - MAX_QUERY_WINDOW`, bo to maksymalny zakres, jakiego mogła dotyczyć paczka
- Jeśli typ ma już jakąś historię (`stored` nie null): bierz minimum — nigdy nie poruszaj się naprzód, bo to omijałoby faktury, o które żadne inne zapytanie nie prosi powtórnie

**Niezmiennik**: punkt cofa się wyłącznie w tej sytuacji i nigdy nie idzie naprzód — dla typu dormantnego dłużej niż `MAX_QUERY_WINDOW`, odgadnięcie mogłoby zwolnić jakieś faktury.

### 5. Wznowienie żądania w jednym przebiegu

Gdy punkt jest cofnięty:
1. Rekord eksportu jest usuwany
2. Punkt kontynuacji jest przesuwany do początku okna
3. **Nowy eksport jest żądany w tym samym przebiegu** (`SyncOutcome.RECOVERED`)

Dzięki temu odzyskanie trwa jeden przebieg, nie dwa. Drugi przebieg zawsze trafiałby na brak kolejkowanego eksportu, a wznowienie żądania teraz jest bezpłatne — licznik budżetu pozwala.

## Uzasadnienie

**Dlaczego `PackageLinkExpired` musi być osobnym typem:** Polityka ponowień (`ARCHIVING_FAILURES`) mówi „jeśli pobieranie zawali się, czekaj do następnego przebiegu". Jest to poprawne dla przejściowych błędów (timeout, 5xx, DNS). Ale wygasły link nigdy się nie ożywa — każda próba na tym samym URL zwróci te same 403/410. Czekanie nic nie zmienia.

**Dlaczego zapamiętywać `covering_from` zamiast go odgadywać:** Paczka mogła być żądana o oknie zmieniającym się w trakcie oczekiwania — na przykład, gdy typ jest dormantny przez dłuższy czas. Pamiętanie dokładnie, jaki zakres został poproszony, pozwala dokładnie do niego wrócić, zamiast zgadywać. Zgadywanie zbyt szerokiego okna mogłoby pominąć faktury.

**Dlaczego cofać do minimum, nie do samego `covering_from`:** Jeśli typ ma już zapisaną historię (`stored`), punkt może już być poza `covering_from`. Bierz minimum, żeby nigdy nie pominąć tego, co zostało już pobrane.

**Dlaczego żądać ponownie w tym samym przebiegu:** Ekonomia. Licznik budżetu już dozwolił jeden eksport na typ — po cofnięciu punkt stoi w tej samej sytuacji co przed wygasłym linkiem. Żądanie ponowne teraz kosztuje nic, bo to część tego samego przebiegu; żądanie w następnym przebiegu wymagałoby czekania i dodatkowego wywołania.

## Konsekwencje

**Pozytywne:**
- System automatycznie odzyskuje się z wygasłych linków zamiast zawiesić się w zakleszczeniu
- Księgowa nie musi ręcznie resetować stanu ani czekać na manualne interwencje
- Odzyskiwanie jest dokończone w jednym przebiegu, co zmniejsza opóźnienie
- Presigned link nigdy nie wygasa w trakcie jednego przebiegu (limit czasu pobrania to 5 minut)

**Negatywne:**
- Punkt kontynuacji może się cofnąć — wbrew poprzedzającemu niezmiennikowi. To zmienia założenia o monotonii przesuniętego punktu
- Dodatkowe odpytanie KSeF za każdym razem, gdy pobieranie zawali się na 403/410
- Stary rekord bez `covering_from` wymaga heurystyki (`started_at - MAX_QUERY_WINDOW`), która może być niedokładna dla paczki żądanej w niestandardowych czasach
- Schemat `PendingExport` zmienia się, ale bez formalnej migracji — starsze wersje kodu będą ignorować nowe pole, nowsze będą odczytywać `None` dla starych rekordów

## Powiązane

- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — pętla synchronizacji i model stanu, którego to rozszerza
- [ADR-104](104-odczyt-paczki-eksportu-i-cykl-zycia-klucza.md) — pobieranie części i obsługa błędów, którą to zmienia dla kodu 403/410
- [D-033](../domain/decisions.md#d-033--synchronizacja-statusu-a-klucz-aes) — cykl życia klucza eksportu
- `PR #184` — implementacja tego mechanizmu
- `GH-93` — zgłoszenie opisujące zakleszczenie
