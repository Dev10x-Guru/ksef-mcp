# ADR-105: Archiwum per podmiot, nazwa pliku jako tożsamość, indeks obok treści

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-005, D-006, D-011, D-030,
  D-032, D-034; `docs/domain/epics.md` T-04; zgłoszenie
  [#38](https://github.com/Dev10x-Guru/ksef-mcp/issues/38);
  [ADR-104](104-odczyt-paczki-eksportu-i-cykl-zycia-klucza.md)
- **Depends-on:** [ADR-104](104-odczyt-paczki-eksportu-i-cykl-zycia-klucza.md)

## Kontekst

[ADR-104] kończy się na odszyfrowanych bajtach: `ExportPackage` niesie
faktury i osobno `_metadata.json`, a `PackageRetriever.archive` przyjmuje
archiwistę jako argument, żeby klucz eksportu zniknął w tej samej
operacji, w której faktury trafiły na dysk [D-033]. Archiwisty jednak nie
było — to zgłoszenie [#38] go dopisuje.

Do rozstrzygnięcia zostały cztery rzeczy naraz: gdzie leżą pliki, skąd
bierze się ich nazwa, co dokładnie broni niezmiennika „ta sama faktura nie
jest przechowywana dwa razy" i co się dzieje, gdy cel zapisu już istnieje.

Ograniczenia są twarde. Jedynym agregatem jest `WpisArchiwum`, jego
tożsamością `NumerKSeF`, a niezmiennika nie broni żadna transakcja —
system plików jej nie ma [D-006]. Deduplikacja musi działać po numerze
KSeF, nigdy po nazwie pliku: skrypt zgadujący po nazwach dwa razy podał
fałszywy wynik — dziesięć, potem siedem rzekomych braków zamiast czterech
rzeczywistych [D-005]. Indeks ma być odrębny od treści, bo inaczej
deduplikacja nagradza trzymanie starych plików i walczy z retencją
[D-005], a komenda czyszcząca jest obowiązkowa [D-034].

## Decyzja

### 1. Jeden korzeń per podmiot i środowisko, wspólny ze stanem synchronizacji

```
<katalog danych>/subjects/<NIP>/<środowisko>/
├── synchronisation.json      # ADR-103
├── deduplication.json        # indeks: numery KSeF + skróty
└── invoices/
    └── <NumerKSeF>.xml
```

Osobny podkatalog per podmiot wynika wprost z [D-032] i [D-034]: wspólny
katalog jest głównym wektorem pomieszania klientów biura rachunkowego.
Wymiar środowiska dokładamy z tego samego powodu, dla którego ma go
`SyncStore` — faktura z środowiska testowego w katalogu produkcyjnym jest
tym samym rodzajem pomyłki, tyle że trudniejszym do zauważenia. Katalog
danych, nigdy cache: to jest ta „lokalna baza danych", na której według MF
mają działać operacje biznesowe [D-030], a czyszczarka dysku honorująca
konwencję cache skasowałaby ją bez pytania.

### 2. Nazwa pliku pochodzi z manifestu, nie z paczki

`<NumerKSeF>.xml` — numer bierzemy z `_metadata.json`, który paruje numer
z nazwą wpisu w paczce. Nazwy wpisów w ZIP-ie wybiera strona eksportująca
i nie są tożsamością niczego; treści faktury nie czytamy w ogóle.

Manifest, który tego parowania nie daje, zatrzymuje archiwizację
wyjątkiem `ArchiveMetadataUnusable` — przed zapisem czegokolwiek, więc
rekord oczekującego eksportu zachowuje klucz i paczkę da się zarchiwizować
ponownie [ADR-104 §6]. Odmawiamy w czterech przypadkach: brak
`_metadata.json`, wpis bez numeru albo bez nazwy pliku, manifest nazywający
plik, którego w paczce nie ma, oraz plik w paczce, którego manifest nie
nazywa. Ostatni przypadek jest tu najważniejszy: przemilczenie go byłoby
cichą utratą faktury.

### 3. `rename(2)` jest strażnikiem, ale nie wolno mu nadpisywać

Zapis idzie przez plik roboczy, `fsync` i `os.replace` w obrębie jednego
katalogu — jedyny prymityw atomowy, jaki system plików daje [D-006].
Strażnikiem niezmiennika jest to, że dwa przebiegi z tą samą fakturą
**wyliczają tę samą nazwę**, a nie zbiór trzymany w pamięci.

Kryterium „nigdy ciche nadpisanie" [#38] jest z `rename` sprzeczne wprost:
`rename` nadpisuje po cichu z definicji. Rozstrzygamy tak, że przed
przemianowaniem sprawdzamy istnienie celu i przy trafieniu nie piszemy nic,
raportując numer jako `already_held`.

### Dlaczego sprawdzenie przed `rename` zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **Sprawdzenie istnienia + `temp → rename`** | zgodne z [D-006]; przenośne; nadpisanie nigdy nie jest ciche, bo pominięcie wchodzi do raportu | teoretyczny wyścig między sprawdzeniem a przemianowaniem |
| `os.link` z pliku roboczego | atomowa odmowa, gdy cel istnieje | nie `rename`, więc rozjazd z [D-006]; zawodzi na części systemów plików i na Windowsie; wymusza sprzątanie pliku roboczego po nieudanym dowiązaniu |
| `open(..., "x")` i zapis w miejscu | prosto | zapis nieatomowy — awaria zostawia pół faktury pod nazwą obiecującą całą |
| Sam indeks jako strażnik | brak dotknięć dysku | dokładnie ten „zbiór w pamięci", który [D-006] odrzuca; indeks skasowany razem z treścią przestaje bronić czegokolwiek |

Wyścig z pierwszego wiersza jest nieszkodliwy i to jest cała odpowiedź:
nazwa pliku **jest** tożsamością, więc jedyny pisarz, który może trafić w
ten sam cel, niesie tę samą fakturę i te same bajty. Utrata cudzych danych
jest tu niemożliwa z konstrukcji, a nie z synchronizacji.

### 4. Indeks obok treści, nie wewnątrz niej

`deduplication.json` leży **obok** katalogu `invoices/`, nie w nim, i
niesie numery KSeF ze skrótami SHA-256 oraz znacznik czasu. Skasowanie
całego katalogu `invoices/` — czyli to, co zrobi przyszła komenda
czyszcząca [D-034] — nie rusza indeksu, więc kolejna synchronizacja nie
ściągnie skasowanych faktur po raz drugi. To jest cała treść rozdziału
indeksu od treści [D-005], sprowadzona do decyzji o jednym poziomie
katalogu.

Kolejność sprawdzeń przy zapisie jest zatem taka: najpierw indeks (numer
znany → pomijamy), potem dysk (plik istnieje → pomijamy i uzupełniamy
indeks), na końcu zapis. Środkowy krok jest tym, co odbudowuje indeks
skasowany bez treści.

## Uzasadnienie

Przyjęty kompromis dotyczy manifestu. Dokładna schema `_metadata.json` nie
jest przypięta ani w repozytorium, ani w dokumentacji domenowej —
[Verify]. Czytamy więc kilka pisowni kluczy (`ksefNumber`, `ksef_number`,
`numerKSeF`; `fileName`, `file_name`, `nazwaPliku`; listę pod `invoices`,
`faktury` albo na najwyższym poziomie), ale **nie** dokładamy awaryjnego
zgadywania po nazwach plików. Tolerancja pisowni kosztuje kilka linii;
zgadywanie kosztowałoby poprawność, a to jest dokładnie ta pomyłka, którą
[D-005] nazywa po imieniu.

Drugi kompromis: nie parujemy numerów z plikami po kolejności, nawet gdy
liczby się zgadzają. Manifest bez nazwy pliku jest odrzucany. Parowanie po
pozycji zapisałoby fakturę pod cudzym numerem — a numer jest tożsamością,
więc taki błąd jest niewykrywalny później i nieusuwalny bez ponownego
pobrania.

## Konsekwencje

**Pozytywne:**
- Powtórzona synchronizacja nie tworzy duplikatów, a powód jest jeden i
  nazwany: nazwa pliku wyliczona z numeru KSeF.
- Skasowanie faktur nie kosztuje idempotencji, więc komenda czyszcząca
  [D-034] da się dopisać bez ruszania tego mechanizmu.
- Awaria w połowie zapisu zostawia plik roboczy, którego nikt nie czyta —
  nigdy pół faktury pod nazwą obiecującą całą.
- Faktury i indeks powstają z prawami `0600` w katalogach `0700`; raport
  niesie ścieżki i numery, nigdy treść faktury [D-011].

**Negatywne:**
- Archiwum nie ma dziś konsumenta: `Synchroniser` nie pobiera paczek, więc
  `PackageArchivist` czeka na wpięcie razem z `PackageRetriever`.
- Tolerancja pisowni kluczy manifestu jest zgadywaniem opartym na jednym
  źródle. Pierwsza prawdziwa paczka z KSeF może ją zawęzić — i powinna.
- Sprawdzenie istnienia celu to jedno dodatkowe `stat` na fakturę. Przy
  paczkach liczonych w setkach to nic; wart odnotowania jest fakt, że
  cena jest w ogóle płacona.

## Powiązane

- [ADR-104](104-odczyt-paczki-eksportu-i-cykl-zycia-klucza.md) — daje
  odszyfrowane bajty i `_metadata.json` osobno; ten dokument opisuje
  archiwistę, którego tamten przyjmuje jako argument.
- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) — ten sam
  układ katalogów per podmiot i ten sam zapis `temp → rename`.
- [#38](https://github.com/Dev10x-Guru/ksef-mcp/issues/38) — zgłoszenie.
