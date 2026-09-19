# ADR-107: Wyłączność zapisu w katalogu podmiotu

- **Date:** 2026-09-19
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-006, D-011, D-033, D-034;
  `docs/memos/architecture-audit-2026-09-19.md` rozdz. 3 i 9; zgłoszenia
  [#101](https://github.com/Dev10x-Guru/ksef-mcp/issues/101),
  [#102](https://github.com/Dev10x-Guru/ksef-mcp/issues/102),
  [#103](https://github.com/Dev10x-Guru/ksef-mcp/issues/103),
  [#104](https://github.com/Dev10x-Guru/ksef-mcp/issues/104),
  [#105](https://github.com/Dev10x-Guru/ksef-mcp/issues/105)
- **Depends-on:** [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md),
  [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md)

## Kontekst

[ADR-103] i [ADR-105] rozstrzygnęły **atomowość** pojedynczego zapisu:
plik roboczy, `fsync`, `os.replace` w obrębie jednego katalogu — jedyny
prymityw, jaki system plików daje [D-006]. Audyt z 19 września stwierdza,
że ta część jest przemyślana lepiej niż w większości projektów z bazą
danych, i że brakuje części drugiej: **wyłączności**. W całym `src/` nie
było ani jednej blokady.

Trzy rzeczy z tego wynikały i wszystkie trzy są udokumentowane cytatami z
kodu w rozdziale 3 memo:

1. **Zgubiona aktualizacja.** Każdy magazyn robi `load() → modyfikacja →
   save()` na całym dokumencie. Dwa klienty MCP na jeden podmiot — Claude
   Desktop i Claude Code obok siebie — to zwykła konfiguracja, a narzędzia
   MCP są synchroniczne, więc SDK biegnie nimi w puli wątków. Wyścig
   zachodzi zatem także **wewnątrz jednego procesu**, bez drugiego
   serwera. Drugi pisarz nadpisywał punkty kontynuacji i klucze AES
   dokumentem, który o nich nie wiedział [#101].
2. **Stała nazwa pliku przejściowego.** `staging = path.with_suffix(".tmp")`
   było identyczne dla każdego pisarza. Dwa `save()` otwierały ten sam
   deskryptor z `O_TRUNC`, więc obcięty JSON powstawał **przed**
   przemianowaniem i atomowość `rename(2)` nie chroniła niczego [#102].
3. **Sprawdzenie zamiast wymuszenia.** Unikalność pliku faktury jest
   wymuszona strukturalnie — nazwa jest tożsamością, a `_written` odmawia
   nadpisania [ADR-105 §3]. Unikalność wpisu indeksu deduplikacji była
   tylko sprawdzana [#103].

Do tego dwa braki mniejszego kalibru: dziennik audytowy opierał
nieprzeplatanie linii na atomowości `O_APPEND`, która sięga tylko
`PIPE_BUF` [#104], a po `os.replace` nigdzie nie było `fsync` katalogu
nadrzędnego, więc przetrwanie samej zamiany nie było domknięte [#105].

## Decyzja

### 1. Trzy prymitywy w `storage.py`, świadomie ani jednego więcej

`exclusive_write` (blokada katalogu), `written_atomically` (zapis pod
nazwą, której nikt inny nie wybierze) i `replaced_durably` (`os.replace`
plus `fsync` katalogu). Magazyny zostają tam, gdzie są; wspólny jest
mechanizm, nie model zapisu. Konsolidacja magazynów w jeden prymityw to
osobna praca (M12, #145–#148) i ten dokument jej **nie** przesądza.

### 2. Zakresem blokady jest cykl, nie zapis

`fcntl.flock(LOCK_EX | LOCK_NB)` na pliku `.lock` w katalogu podmiotu.
Trzyma go cały przebieg `Synchroniser.run`, całe `InvoiceArchive.store`
oraz `SyncStore.updating` i `ReviewStore.updating`. Zablokowanie samego
`save()` niczego by nie dało: zgubiona aktualizacja powstaje **między**
odczytem a zapisem, nie w trakcie zapisu.

Blokada obejmuje katalog `subjects/<NIP>/<środowisko>/`, więc jest jedna
dla stanu synchronizacji, archiwum, indeksu, dziennika przeglądu i
dziennika audytowego. Cache okresów ma własny korzeń i własny plik
blokady.

Unikalność wpisu indeksu wymuszamy **dwustronnie**: blokada sprawia, że
cykl odczyt-zmiana-zapis jest niepodzielny, a `DeduplicationIndex.with_entry`
odmawia numeru, który indeks już trzyma. Ani jedno, ani drugie nie
wystarcza samo — blokada bez odmowy wciąż pozwala dodać numer dwa razy w
jednym cyklu, odmowa bez blokady wciąż startuje z nieaktualnej migawki.

### 3. Odmowa, nigdy czekanie

`LOCK_NB` i wyjątek `WriteExclusivityUnavailable` z nazwą zajętego
katalogu. Zablokowane narzędzie MCP to zawieszona sesja agenta, a
czekanie na blokadę po procesie ubitym przez `uvx` nie skończyłoby się
nigdy. Dla synchronizacji odmowa jest zresztą wynikiem lepszym niż
kolejka: dwa przebiegi na jeden podmiot wydają jeden przydział dwa razy,
a to jest dokładnie wzorzec, za który Ministerstwo wydłuża blokadę
[D-017].

Blokada jest **wznawialna w obrębie wątku**: `flock` wiąże się z
otwartym opisem pliku, więc wątek wchodzący we własną blokadę drugim
deskryptorem zostałby odrzucony przez samego siebie. Licznik zagnieżdżeń
leży w `threading.local`, więc drugi **wątek** nadal dostaje odmowę.

### 4. Nazwa pliku przejściowego pochodzi z `mkstemp`, tryb z `fchmod`

`tempfile.mkstemp(dir=...)` w katalogu docelowym, `os.fchmod` na
deskryptorze. Znika okno, w którym plik niosący klucze AES albo dane
kontrahenta istniał z trybem z umask, i znika współdzielona nazwa. Cenę
widać przy odmowie: nazwa jest unikalna, więc nikt tego pliku już nie
nadpisze i ścieżka błędu musi go sama usunąć.

Po `os.replace` idzie `fsync` katalogu nadrzędnego — w siedmiu miejscach,
które wskazuje [#105], oraz w `allowance.py`, gdzie ten sam wzorzec
powstał po dacie memo.

### 5. Dziennik audytowy zostaje dziennikiem

`audit.py` **nie** przechodzi na `temp → rename`. Odstępstwo od [D-006]
jest tam świadome i rozdział 9 memo wymienia je wśród rzeczy do ochrony:
przepisywanie całego śladu przy każdej linii kosztowałoby kwadratowo i
narażało cały zgromadzony dowód, żeby dopisać jedno zdanie. Poprawiamy
dwie rzeczy, których `O_APPEND` nigdy nie dawał: dopisanie idzie pod
blokadą podmiotu (bo niepodzielność `write()` sięga tylko `PIPE_BUF`, a
jedno `synchronise_invoices` z czterema kierunkami przekracza cztery
kilobajty bez wysiłku), a odczyt uszkodzonej linii podnosi
`AuditTrailUnreadable` z numerem linii zamiast surowego
`JSONDecodeError`. Komunikat nie cytuje treści linii — ta niesie numery
KSeF [D-011].

### Dlaczego `flock` zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **`fcntl.flock` na pliku `.lock`, nieblokująco** | działa między procesami i między wątkami naraz; zwalniana przez jądro, gdy proces ginie, więc nie zostaje blokada-sierota | POSIX-only; `flock` wiąże się z opisem pliku, więc wznawialność trzeba dopisać samemu |
| `fcntl.lockf` (POSIX record locking) | blokuje zakresy, nie cały plik | zwalnia się przy zamknięciu **dowolnego** deskryptora tego pliku w procesie — pułapka w puli wątków |
| Katalog albo plik jako znacznik (`O_EXCL`) | przenośne, także na Windowsie | po ubitym procesie zostaje znacznik, którego nikt nie sprząta — dokładnie ta blokada-sierota, której nie chcemy |
| Blokada w pamięci (`threading.Lock`) | najprostsze | nie widzi drugiego procesu, a to jest przypadek z [#101] |
| Kolejkowanie zamiast odmowy | pisarz nie traci pracy | zawiesza narzędzie MCP, a przy synchronizacji wydaje przydział dwa razy |

## Uzasadnienie

Przyjęty kompromis jest jeden i warto go nazwać: `fcntl` nie istnieje na
Windowsie. Dzisiaj nie jest to problem — `secretstorage` jest już
zależnością linuksową, a `preflight.py` bada stan kolekcji przez D-Bus —
ale to jest miejsce, w którym port na Windows zatrzyma się jako
pierwsze, i lepiej, żeby był zapisany tutaj niż odkryty wtedy.

Drugi kompromis: blokada chroni przed pisarzami, którzy jej używają.
Proces obcy, edycja pliku ręcznie, `rsync` po katalogu — nic z tego jej
nie widzi. To jest granica każdej blokady doradczej i nie udajemy, że
jest inaczej.

Trzeci: odmowa przerzuca koszt na wywołującego. Drugi klient MCP usłyszy
„zajęte" zamiast poczekać sekundę. Uznajemy to za właściwy wynik, bo
alternatywa dla synchronizacji to podwójny wydatek przydziału, a dla
narzędzia MCP — zawieszona sesja.

## Konsekwencje

**Pozytywne:**
- Dwa klienty MCP na jeden podmiot przestają gubić sobie nawzajem punkty
  kontynuacji, klucze AES i wpisy indeksu; przegrany dowiaduje się o tym
  wyjątkiem, zamiast myśleć, że zapisał.
- Unikalność wpisu indeksu jest wymuszona tak samo jak unikalność pliku
  faktury — strukturalnie, a nie sprawdzeniem.
- Plik niosący klucze AES ani przez chwilę nie istnieje z trybem z umask.
- Zamiana pliku przetrwa zanik zasilania, a nie tylko `SIGKILL`.
- Uszkodzona linia dziennika audytowego mówi, która to linia, zamiast
  wysypywać się surowym `JSONDecodeError`.

**Negatywne:**
- `fcntl` przypina magazyny do POSIX-a.
- Blokada jest doradcza: pisarz, który jej nie bierze, nadal wygrywa.
- Katalog podmiotu zyskuje plik `.lock`, który widać na listingu i który
  trzeba uwzględniać w testach oraz w komendzie czyszczącej [D-034].
- Nazwa pliku przejściowego przestała być przewidywalna, więc każda
  ścieżka odmowy musi sama po sobie posprzątać — inaczej katalog roboczy
  zapełni się plikami, których nikt nie nadpisze.

## Powiązane

- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — pętla
  eksportu, której cykl odczyt-zmiana-zapis ten dokument obejmuje
  blokadą.
- [ADR-105](105-archiwum-per-podmiot-i-indeks-deduplikacji.md) —
  strukturalna ochrona pliku faktury, na której wzorowana jest tutaj
  ochrona wpisu indeksu.
- [#101](https://github.com/Dev10x-Guru/ksef-mcp/issues/101),
  [#102](https://github.com/Dev10x-Guru/ksef-mcp/issues/102),
  [#103](https://github.com/Dev10x-Guru/ksef-mcp/issues/103),
  [#104](https://github.com/Dev10x-Guru/ksef-mcp/issues/104),
  [#105](https://github.com/Dev10x-Guru/ksef-mcp/issues/105) —
  zgłoszenia, które ten dokument rozstrzyga.
- M12 (#145–#148) — przyszła konsolidacja magazynów w jeden prymityw
  zapisu; ma wchłonąć `storage.py`, a nie powstać obok niego.
