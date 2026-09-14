# ADR-103: Trwały stan synchronizacji i pętla eksportu paczek

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-006, D-020, D-031, D-032, D-033,
  D-034; `docs/domain/model.md`; zgłoszenie
  [#36](https://github.com/Dev10x-Guru/ksef-mcp/issues/36);
  [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)
- **Depends-on:** [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)

## Kontekst

[D-031] rozstrzygnęło **co** robimy: eksport paczek w scenariuszu „tylko
do HWM", `DateType` przybity do `PermanentStorage`, punkt kontynuacji
osobny dla każdego typu podmiotu, okna przylegające. [ADR-102] dało
**czym** to robić — port z typami `ContinuationPoint`, `Period`,
`ExportHandle`, `ExportPart` oraz licznikiem budżetu.

Została warstwa pomiędzy: pętla, która te operacje wywołuje w
odpowiedniej kolejności, i **miejsce, w którym jej stan przeżywa
restart procesu**. To drugie nie było dotąd rozstrzygnięte w kodzie i
jest właściwym przedmiotem tego dokumentu.

Trzy ograniczenia wyznaczają tu wszystko:

- **20 eksportów na godzinę** dzielone między cztery typy podmiotu
  [D-031 §5]. Zmarnowany eksport to nie opóźnienie, to ubytek zasobu.
- **Eksport jest asynchroniczny i kolejkowany.** Między inicjacją a
  dostępnością części mija czas, w którym serwer MCP pod `uvx` bywa
  ubijany razem z sesją agenta [D-033].
- **Utrata punktu kontynuacji wymusza pełną resynchronizację** [D-032] —
  przy dwudziestu eksportach na godzinę liczoną w dobach, nie minutach.

## Decyzja

### 1. Stan mieszka w katalogu danych, w jednym dokumencie na podmiot i środowisko

`src/ksef_mcp/sync_store.py` zapisuje **jeden plik JSON**:

```
<user_data_dir>/subjects/<NIP>/<środowisko>/synchronisation.json
```

Katalog danych, nie cache: konwencja katalogu cache brzmi *wolno
skasować w dowolnym momencie*, a czyszczarka dysku honorująca ją
kasowałaby ciągłość synchronizacji [D-032]. Ścieżkę wyznacza
`platformdirs.user_data_path(appname=SERVER_NAME)` — nigdy sklejanie
ręczne — a nazwą aplikacji jest `SERVER_NAME`, ta sama stała, po której
klucz ma keyring.

Podkatalog per NIP **i per środowisko**. Rozdział podmiotów to wymóg
[D-034]; rozdział środowisk dokłada ten dokument, bo punkt kontynuacji
ze środowiska testowego użyty wobec produkcji ogłosiłby kompletność
okresu, którego nikt nigdy nie pobrał.

Katalog `0700`, plik `0600` — plik niesie klucze AES oczekujących
eksportów [D-033].

### 2. Punkty kontynuacji i oczekujące eksporty w **jednym** dokumencie

To nie jest oszczędność, tylko niezmiennik. Przesunięcie punktu
kontynuacji jest bezpieczne **wyłącznie wtedy**, gdy części, które to
przesunięcie uzasadniają, są zapisane. Jedno `rename(2)` w obrębie
jednego katalogu jest jedynym prymitywem, który czyni oba fakty
prawdziwymi naraz [D-006]. Zapis idzie przez `temp → rename` z
`fsync`, więc awaria w trakcie zostawia poprzedni rekord nietknięty.

### 3. Schemat rekordu — kontrakt dla deszyfrowania [#37]

```json
{
  "schema_version": 1,
  "nip": "1234567890",
  "environment": "test",
  "continuation_points": {
    "buyer": { "reached": "2026-09-10T00:00:00+00:00",
               "attempted_at": "2026-09-12T12:00:00+00:00" }
  },
  "pending_exports": [
    {
      "reference": "20260912-EX-ABC",
      "direction": "buyer",
      "started_at": "2026-09-12T12:00:00+00:00",
      "state": "ready",
      "invoice_count": 7,
      "encryption_key": "<base64 klucza AES-256>",
      "initialisation_vector": "<base64 IV>",
      "parts": [
        { "ordinal": 1, "name": "package_part_1.zip.aes", "method": "GET",
          "url": "https://…", "size_bytes": 1024,
          "content_hash": "<base64 SHA-256 jawnej części>",
          "encrypted_size_bytes": 1040,
          "encrypted_content_hash": "<base64 SHA-256 zaszyfrowanej części>" }
      ]
    }
  ]
}
```

`reference` + `encryption_key` + `initialisation_vector` odtwarzają
`ExportHandle`; każdy wpis w `parts` odtwarza `ExportPart`. Konsument
woła `session.fetch_part(handle=…, part=…)` i dostaje **surowe,
zaszyfrowane bajty** — port ich nie dotyka, ten dokument też nie.
Deszyfrowanie należy do [#37].

`schema_version` jest odczytywany, a niezgodny **odrzucany wyjątkiem**,
nie zgadywany. Źle zrozumiany punkt kontynuacji pomija faktury, o które
nic już nigdy nie zapyta.

**Niezmiennik dla [#37]:** wpis `pending_exports` wolno usunąć dopiero
po zarchiwizowaniu jego części. Punkt kontynuacji został już przesunięty
za tę paczkę, więc ten wpis jest jedynym śladem, że okno w ogóle
pobrano. Kasowanie klucza jest częścią archiwizacji [D-033], nie
osobnym sprzątaniem.

### 4. Polityka przydziału budżetu wynika z dwóch reguł, nie z licznika per typ

- **Najwyżej jeden eksport na typ podmiotu w jednym przebiegu.**
- **Typ jest „należny" dopiero po interwale**: 15 minut dla sprzedawcy i
  nabywcy, doba i okno nocne dla `Podmiot 3` oraz `Podmiot upoważniony`
  [D-031 §5].

Pułap wychodzi z tego sam: cztery typy razy cztery eksporty na godzinę
to szesnaście, poniżej dwudziestu. Nie ma osobnego licznika „~4/h na
typ", który mógłby rozjechać się z interwałem.

Twardym zabezpieczeniem zostaje `QueryBudget` zasilany z
`GET /rate-limits` [ADR-102]: limit odczytany z KSeF, a nie założony.
Gdy godzinowa pula eksportów jest wyczerpana, typ dostaje wynik
`budget_spent` i przebieg idzie dalej — nie ma ponawiania ponad to, co
port już robi na `Retry-After`.

**Okno nocne to 00:00–06:00 UTC.** UTC, nie czas lokalny: okno wędrujące
ze strefą operatora i ze zmianą czasu nie jest regułą, którą da się
potem odtworzyć z rekordu na dysku.

**Znane ograniczenie:** `QueryBudget` liczy w pamięci procesu i zeruje
się przy restarcie. Trwałym strażnikiem tempa jest `attempted_at` w
rekordzie na dysku; licznik jest strażnikiem wewnątrz przebiegu.

### 5. Odpytywanie statusu jest ograniczone i nie kończy się czekaniem

Trzy próby co dziesięć sekund, bez czekania po ostatniej. Paczka, która
w tym czasie nie dojrzała, zostaje zapisana jako `running` i dokańcza ją
kolejny przebieg. Wywołanie narzędzia MCP blokujące się na minuty wygląda
dla agenta i dla człowieka jak zawieszone, a rekord na dysku sprawia, że
wcześniejsze zakończenie nic nie kosztuje.

Stąd **przebieg jest wznawialny**: najpierw dokańcza to, co zakolejkowane,
dopiero potem kolejkuje nowe. Ponowne wywołanie nie kosztuje drugiego
eksportu tego samego okna.

### 6. Narzędzie nie przyjmuje żadnych argumentów

`synchronise_invoices()` — bez okna, bez paginacji, bez rozmiaru strony.
Kryterium akceptacji [D-020], nie uproszczenie: agent sterujący tymi
parametrami spala godzinowy budżet w dwie minuty na ponowieniach,
a Ministerstwo odczytuje wzorzec jako próbę obchodzenia limitu.

### Dlaczego jeden dokument na podmiot zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **Jeden JSON, `temp → rename`** | punkt i części zapisane jednym atomowym krokiem; czytelny dla człowieka i dla `#37`; zero zależności | cały dokument przepisywany przy każdej zmianie |
| Plik na typ podmiotu | mniejsze zapisy | przesunięcie punktu i zapis części w dwóch krokach — awaria pomiędzy ogłasza kompletność okna, którego nie pobrano |
| SQLite | transakcje z pudełka | baza wykluczona wprost w `CLAUDE.md`; migracje i blokady pliku za cenę stanu mierzonego w kilobajtach |
| Keyring | sekrety we właściwym miejscu | [D-033] wprost odrzuca: klucz eksportu żyje godziny, keyring trzyma długowieczny sekret, a na headless i tak schodzi na ścieżkę awaryjną |

## Uzasadnienie

Rozmiar stanu jest mikroskopijny — cztery punkty kontynuacji i garść
oczekujących eksportów — a jego utrata kosztuje dni pracy przy limicie
dwudziestu eksportów na godzinę. Przy takiej asymetrii wygrywa
rozwiązanie, którego poprawność da się przeczytać z jednej funkcji:
zapis całego dokumentu i jedno `rename(2)`.

Przyjęty kompromis: klucz AES leży na dysku jawnie. [D-033] rozstrzygnęło
to wcześniej i argument się nie zmienił — odszyfrowane faktury trafią do
tego samego katalogu danych, więc klucz obok archiwum nie otwiera
niczego, co nie leży już obok w postaci jawnej.

Drugi przyjęty kompromis: punkt kontynuacji przesuwa się, gdy paczka jest
**gotowa**, a nie gdy jest **zarchiwizowana**. Odwrotna kolejność
wiązałaby postęp z istnieniem [#37] i do jego powstania kazałaby
powtarzać to samo okno co przebieg — czyli palić budżet, przed którym
ten mechanizm ma chronić. Cenę pokrywa niezmiennik z §3.

## Konsekwencje

**Pozytywne:**
- Ubicie serwera MCP w dowolnym momencie kosztuje najwyżej jeden
  przebieg odpytania statusu, nigdy eksportu ani punktu kontynuacji.
- [#37] dostaje gotowy kontrakt: ścieżkę, schemat i wywołanie portu, bez
  zgadywania.
- Limit 20/h jest niemożliwy do przekroczenia dwiema regułami, które da
  się sprawdzić bez uruchamiania czegokolwiek.
- Rozdział per środowisko wyklucza najgorszą klasę błędu: ogłoszenie
  kompletności produkcji na podstawie przebiegu testowego.

**Negatywne:**
- Cały dokument przepisywany przy każdej zmianie. Przy tej wielkości
  stanu bez znaczenia; przy tysiącach oczekujących eksportów wymagałby
  rewizji.
- `QueryBudget` nie przeżywa restartu, więc proces uruchamiany w pętli
  co minutę mógłby obejść licznik. Powstrzymuje go `attempted_at` na
  dysku, ale to dwa mechanizmy zamiast jednego.
- Brak migracji schematu: rekord w nowszej wersji jest odrzucany, a nie
  konwertowany. Świadome do czasu, aż schemat zmieni się pierwszy raz.

## Powiązane

- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — port,
  którego operacje ta pętla wywołuje
- [D-031], [D-032], [D-033], [D-020], [D-006] w
  `docs/domain/decisions.md`
- [#36](https://github.com/Dev10x-Guru/ksef-mcp/issues/36) — zgłoszenie
  realizowane tym dokumentem
- [#37](https://github.com/Dev10x-Guru/ksef-mcp/issues/37) — konsument
  rekordu oczekującego eksportu
