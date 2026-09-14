# ADR-104: Odczyt paczki eksportu i cykl życia klucza AES

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** `docs/domain/decisions.md` D-005, D-011, D-031 §6–§7, D-032,
  D-033; `docs/domain/epics.md` T-04; zgłoszenie
  [#37](https://github.com/Dev10x-Guru/ksef-mcp/issues/37);
  [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)
- **Depends-on:** [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)
- **Refines:** [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md)

## Kontekst

[ADR-103] zostawiło rekord oczekującego eksportu jako gotowy kontrakt:
referencja, klucz AES-256, IV i lista części z adresami oraz skrótami.
Brakowało czynności, która ten rekord konsumuje — pobrania części,
odszyfrowania i rozpakowania — a przede wszystkim **momentu, w którym
klucz przestaje istnieć**.

[D-033] stawia niezmiennik wprost: klucz nie przeżywa zakończonego
eksportu, a kasowanie jest **częścią archiwizacji**, nie osobnym
sprzątaniem. Niezmiennik jest łatwy do zapisania i łatwy do zgubienia:
wystarczy, że kasowanie stanie się drugim krokiem, który ktoś może
pominąć albo wykonać przed czasem.

Drugie ograniczenie jest po przeciwnej stronie. Rekord jest **jedynym
śladem**, że okno w ogóle pobrano — punkt kontynuacji przesunął się już
wtedy, gdy paczka stała się gotowa ([ADR-103] §3). Skasowanie rekordu
zbyt wcześnie ogłasza kompletność okresu, którego nikt nie odczytał.

## Decyzja

### 1. Odczyt paczki mieszka w `src/ksef_mcp/package.py`

Cztery kroki w jednej kolejności, wprost za [D-031 §7]: pobranie części
z osobnego adresu → deszyfrowanie AES-256-CBC → złożenie strumienia →
rozpakowanie ZIP. Kolejność części wyznacza `ordinal`, nie kolejność
zapisu w rekordzie: części są kawałkami jednego ZIP-a i strumień
złożony inaczej nie rozpakuje się w nic.

### 2. Kasowanie klucza jest argumentem, nie krokiem

`PackageRetriever.archive(export=…, archivist=…)` przyjmuje archiwistę
jako funkcję. Rekord — a z nim klucz i IV — znika **wewnątrz tej samej
operacji**, dopiero gdy archiwista wróci bez wyjątku:

```python
package = self.collect(export=export)
archivist(package)
state = self.store.load()
path = self.store.save(state.without_export(reference=export.reference))
```

Nie da się zarchiwizować bez skasowania i nie da się skasować bez
zarchiwizowania. Nieudane pobranie części, uszkodzona paczka i
archiwista, który podniósł wyjątek, zostawiają rekord nietknięty — a
więc okno wciąż do pobrania, bez wydawania drugiego eksportu.

Zapis archiwum należy do [#38]; ten dokument ustala wyłącznie, że
skasowanie klucza jest jego domknięciem.

### 3. Skróty sprawdzamy po obu stronach szyfru

Każda część jest weryfikowana dwukrotnie: rozmiar i SHA-256 bajtów
pobranych (`encrypted_*`) oraz rozmiar i SHA-256 po odszyfrowaniu.
Paczka niezgodna z opisem nie trafia do archiwum. KSeF podaje skróty w
base64; przyjmujemy też zapis szesnastkowy, bo odrzucenie dobrej paczki
za sposób zapisu kosztowałoby eksport, który ją wytworzył.

### 4. Wpis wskazujący poza paczkę jest odrzucany

Nazwy wpisów ZIP pochodzą z zewnętrznego magazynu presigned. Wpis
absolutny albo z `..` jest przejściem po katalogach wręczonym
archiwizacji, więc rozpakowanie kończy się wyjątkiem, zanim [#38]
zobaczy jakąkolwiek nazwę.

### 5. `_metadata.json` wychodzi osobnym polem

`ExportPackage.metadata` obok `documents`. Plik jest **wejściem do
deduplikacji** po numerze KSeF [D-031 §6], a nie kolejną fakturą —
[#38] nie ma go rozpoznawać po nazwie.

### 6. Poprawka do [ADR-103]: eksport odrzucony też traci klucz

`PendingExport.encryption` jest odtąd `ExportEncryption | None`, a
przebieg synchronizacji zapisuje rekord `failed` **bez klucza**. Rekord
zostaje — referencja, której nikt później nie umie wyjaśnić, jest
gorsza niż oznaczona porażka — ale odrzucony eksport jest zakończony, a
klucz zakończonego eksportu nie ma czego otwierać. [ADR-103] tego
przypadku nie rozstrzygało i zostawiało klucz na dysku bezterminowo.
Sięgnięcie po `handle` takiego rekordu podnosi `ExportKeyDiscarded`
zamiast oddać uchwyt, którym nic się nie pobierze.

### Dlaczego archiwista jako argument zamiast alternatyw?

| Podejście | Zalety | Wady |
|---|---|---|
| **Archiwista przekazany do `archive`** | kasowanie klucza niemożliwe do pominięcia i niemożliwe do przedwczesnego wykonania; jedna operacja, jeden warunek | odwrócenie sterowania — wywołujący oddaje kolejność kroków |
| `collect()` + osobne `forget(reference)` | prostsze wywołanie | dokładnie to „osobne sprzątanie", które [D-033] odrzuca; pominięte raz, zostawia klucz na zawsze |
| Kasowanie zaraz po odszyfrowaniu | klucz żyje najkrócej | awaria zapisu archiwum kosztuje jeden z dwudziestu eksportów na godzinę i okno nie do odzyskania |
| Sprzątanie po czasie (TTL) | brak zmian w przepływie | kasuje klucz paczki, której jeszcze nie zapisano, i zostawia klucze, gdy proces nie wstaje |

## Uzasadnienie

Niezmiennik zapisany w dokumencie nie broni się sam; broni go kształt
wywołania. Odwrócenie sterowania jest tu jedyną ceną i jest niska:
jedyny wywołujący, jakiego ten mechanizm będzie miał, i tak archiwizuje
paczkę w całości.

Przyjęty kompromis: gdy archiwista zapisze faktury, a `save` rekordu
zawiedzie, klucz przeżyje archiwizację. Odwrotna kolejność — kasowanie
przed zapisem — zamienia ten sam błąd na utratę okna, więc kolejność
wybrano tak, by awaria kosztowała nadmiarowy sekret obok jawnych już
faktur, nie brakujące faktury.

## Konsekwencje

**Pozytywne:**
- Klucz przestaje istnieć dokładnie wtedy, gdy paczka trafia do
  archiwum — i nigdy wcześniej.
- Nieudane pobranie części nie zbliża okresu do „kompletnego": rekord z
  częściami zostaje na dysku.
- [#38] dostaje odszyfrowane bajty i `_metadata.json` osobno, bez
  zgadywania po nazwach.
- Paczka niezgodna ze skrótami albo wskazująca poza siebie nie dociera
  do warstwy zapisującej pliki.

**Negatywne:**
- Doszła zależność `cryptography` — deklarowana wprost, mimo że
  przychodzi już z `ksef2`.
- `PendingExport.encryption` bywa `None`, więc każdy konsument rekordu
  musi to rozważyć. Alternatywą był sekret-wartownik, czyli ten sam
  warunek udający dane.
- Ponowienie po nieudanej części pobiera wszystkie części od nowa.
  Adresy są presigned i nie liczą się do budżetu KSeF, więc cena to
  transfer, nie limit.

## Powiązane

- [ADR-103](103-trwaly-stan-synchronizacji-i-petla-eksportu.md) —
  rekord oczekującego eksportu, który ten dokument konsumuje i którego
  §3 uzupełnia o przypadek eksportu odrzuconego
- [D-031 §6–§7], [D-033], [D-032], [D-011], [D-005] w
  `docs/domain/decisions.md`
- [#37](https://github.com/Dev10x-Guru/ksef-mcp/issues/37) — zgłoszenie
  realizowane tym dokumentem
- [#38](https://github.com/Dev10x-Guru/ksef-mcp/issues/38) — archiwizacja,
  która domyka cykl życia klucza
