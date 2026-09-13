# Epics & Tickets

> Aktualizowane w miarę doprecyzowywania. Ukończone = `[DONE]`.

## Implementation Priority

Kolejność wynika z [D-001]. Etapy 2 i 3 są rozszerzeniami, nie nowymi
ścieżkami — patrz [D-008] i [D-009].

Synchronizacja opiera się na **kanonicznym wzorcu MF**: eksport paczek
z High Water Mark [D-031]. Ścieżka synchroniczna nie jest budowana
nawet dla niskiego wolumenu, żeby nie powstał próg przełączania.

---

## Etap 1 — Jeden podmiot, faktury zakupowe, odczyt

### T-01 — Uruchomienie serwera pod `uvx`

> Jako osoba konfigurująca narzędzie, gdy dodaję serwer do klienta MCP,
> chcę żeby wystartował jednym wpisem bez instalowania czegokolwiek
> ręcznie, żebym nie odpuścił na pierwszym kroku.

- Pakiet `ksef-mcp` [D-015], `src-layout`, entry point konsolowy.
- `MCPServer`, nie `FastMCP` — ten drugi usunięto w `mcp` 2.x [D-018].
- Test przed publikacją: `uvx --from git+...` — dokładnie ten wpis, który
  dostanie użytkownik końcowy.
- **Cykl życia procesu i sprzątanie w zakresie tego ticketu** [D-014], ST-2.
- AK: serwer startuje, zgłasza toole, kończy się czysto.

### T-02 — Uwierzytelnienie i poświadczenia

> Jako użytkownik, gdy pierwszy raz uruchamiam narzędzie, chcę usłyszeć
> „działa, jesteś połączony jako [nazwa firmy]", bo to jedyny dowód,
> któremu ufam.

- Token z keyringu; w konfiguracji wyłącznie nazwa konta [D-004].
- **Twardy błąd zamiast promptu**, gdy backend niedostępny — ST-3.
  Wykrycie: `isinstance(keyring.get_keyring(), fail.Keyring)`, bez
  odczytu, zapisu i interakcji. Zweryfikowane wykonaniem.
- **Backend wybiera użytkownik**, nie priorytet biblioteki [D-004].
- Środowisko (TEST/DEMO/PROD) oznaczane w każdej odpowiedzi.
- AK: na maszynie bez sesji D-Bus narzędzie zwraca błąd z instrukcją i
  **nie zawiesza transportu**.

### T-02a — Komenda `onboarding` *(GH-4)*

> Jako osoba stawiająca narzędzie, gdy uruchamiam je pierwszy raz, chcę
> przejść przez konfigurację krok po kroku i dowiedzieć się o brakujących
> zależnościach **zanim** coś pęknie w trakcie pracy.

Uruchamiana **poza transportem MCP**, więc jako jedyna może pytać
interaktywnie.

- Kontrola **obecności i wersji Node** [D-029] oraz integracji `fnm`
  z powłoką — bez `fnm env` w profilu `.node-version` jest deklaracją
  bez egzekucji.
- Wykrycie i wybór backendu keyringu; jawny komunikat, gdy jedyny
  dostępny to `fail.Keyring`.
- Kroki zdobycia tokenu: `ap.ksef.mf.gov.pl/web/tokens/generate-token`,
  uprawnienie **`InvoiceRead`** (nie `InvoiceWrite` — uprawnienia tokenu
  są niezmienne), ostrzeżenie o jednorazowym wyświetleniu.
- Wybór środowiska i katalogu roboczego; `chmod 0700`, ostrzeżenie przy
  ścieżkach synchronizowanych do chmury.
- Weryfikacja połączenia zakończona nazwą firmy.
- AK: komunikat o braku zależności podaje **gotową komendę naprawczą**
  dla wykrytego systemu, nie odsyła do dokumentacji.

### T-02b — Komenda zapisu sekretu w keyringu *(GH-4)*

> Jako użytkownik, gdy rotuję token albo dodaję drugi podmiot, chcę
> zapisać sekret jedną komendą, bez znajomości wewnętrznych atrybutów
> biblioteki.

Uzasadnienie z przebiegu na sucho: bez tej komendy zapis wymagał ręcznego
odtworzenia schematu atrybutów przez `secret-tool`. Żaden użytkownik tego
nie zrobi.

- Wywoływalna **niezależnie** od onboardingu i używana przez onboarding
  jako krok — jedna implementacja, nie dwie.
- Odczyt wartości **ze stdin bez echa**; nigdy w argumentach procesu ani
  w historii powłoki.
- Weryfikacja po zapisie potwierdza odczyt **nie pokazując wartości**.
- Komenda kasująca domyka rotację.
- AK: `secret-tool lookup` wypisuje sekret na stdout — nasza komenda
  weryfikująca **nie może** tak działać.

### T-02c — Instalacja i aktualizacja skilla *(GH-4)*

> Jako użytkownik, gdy konfiguruję narzędzie, chcę żeby agent od razu
> wiedział, jak z niego korzystać — bez zgadywania, które kosztuje budżet
> zapytań.

- Wybór zakresu: `~/.claude/skills/` albo `./.claude/skills/` w katalogu
  wywołania; zakres **jawny**, nigdy milcząco przyjęty.
- **Tworzenie i aktualizacja**; wykrycie istniejącego skilla i pokazanie
  różnicy przed nadpisaniem.
- Treść niesie: operacje biznesowe po lokalnym archiwum [D-030],
  synchronizacja ma własny rytm [D-031], treść faktury nie trafia do
  kontekstu modelu [D-011], oznaczanie środowiska.

### T-03 — Synchronizacja przyrostowa przez eksport paczek

> Jako nabywca, gdy chcę mieć komplet faktur, oczekuję że narzędzie
> samo pobierze wszystko, czego jeszcze nie mam — i nie uderzy w limity.

Kanoniczny przebieg [D-031]: inicjacja eksportu dla typu podmiotu, od
punktu kontynuacji, `DateType = PermanentStorage`,
`RestrictToPermanentStorageHwmDate`, **bez `DateRange.To`**.

- Scenariusz **„tylko do HWM"** — dane do punktu kompletności są
  definitywne, duplikatów minimum.
- Kontynuacja: `IsTruncated = true` → `LastPermanentStorageDate`;
  `false` → `PermanentStorageHwmDate`. Zakresy **przylegają**.
- **Iteracja po typach podmiotu**, osobny punkt kontynuacji dla każdego.
  Budżet 20 eksportów/h dzielony, ~4/h na typ; `Podmiot 3` i
  `Podmiot upoważniony` raz na dobę w oknie nocnym.
- Interwał cykliczny **≥ 15 minut** na typ podmiotu.
- Licznik budżetu; **własny retry** ponad `KSeFRateLimitError`, bo
  `max_delay=4s` w SDK nie wystarczy przy limicie godzinowym [D-017].
- `GET /limits/context` zamiast zakładania wartości domyślnych.
- Trzeci `except` na `httpx` — błędy transportowe przeciekają z SDK.
- AK: tool **nie wystawia paginacji ani okna** — jedno deterministyczne
  wywołanie po stronie serwera [D-020].

### T-04 — Rozpakowanie paczki i archiwizacja

> Jako księgowa, gdy kompletuję miesiąc, chcę pliki w znanym katalogu z
> nazwami, które coś znaczą — żeby dało się je przekazać i zaimportować.

- Pobranie części z osobnych URL-i, **deszyfrowanie AES-256** kluczem i
  IV z inicjalizacji, złożenie strumienia, rozpakowanie ZIP [D-031].
- Klucz zapisany przy rekordzie oczekującego eksportu, **kasowany po
  zarchiwizowaniu** paczki; nigdy w keyringu [D-033].
- Zapis `temp → rename` pod `<NumerKSeF>.xml` [D-006].
- Deduplikacja po **numerze KSeF** na podstawie `_metadata.json`;
  **indeks odrębny od treści** [D-005].
- Archiwum **per podmiot w osobnym podkatalogu** katalogu danych
  [D-032], [D-034].
- AK: nieudane pobranie części **nie** oznacza okresu jako kompletnego.
- AK: klucz nie przeżywa zakończonego eksportu.

### T-05 — Zestawienie CSV i lista w czacie

> Jako księgowa, gdy uzgadniam miesiąc, chcę jedną tabelę do sprawdzenia
> sumy i przekazania jednym załącznikiem.

- Kolumny świadomie wybrane — minimalizacja danych osobowych.
- Link weryfikacyjny KOD I przy każdej pozycji.
- Tool zwraca **ścieżki i metadane, nigdy treść faktury** [D-011].
- Twardy próg pozycji w odpowiedzi tekstowej [D-023].
- AK: suma w CSV zgadza się z aplikacją KSeF co do grosza.

### T-06a — Cache metadanych okresu

> Jako użytkownik, gdy pytam o ten sam miesiąc drugi raz, nie chcę czekać
> ani płacić za to budżetem zapytań.

- Powtórzone pytanie o ten sam okres kosztuje **zero** zapytań [D-021].
- Cache w katalogu cache; punkty kontynuacji w katalogu danych [D-032].
- Ważniejszy niż deduplikacja: deduplikacja chroni dysk, cache chroni
  deficytowy budżet.

### T-06 — Delta okresu

> Jako księgowa, gdy miesiąc jest już zaksięgowany, chcę wiedzieć, że
> wpadła do niego nowa faktura — zanim dowiem się o tym od doradcy.

- Ponowne odpytanie zwraca „nowe od ostatniego pobrania" [D-022].
- Sygnalizacja, nie rozstrzygnięcie — ujęcie podatkowe to decyzja
  człowieka. ST-4.
- **Zwalidowane na danych rzeczywistych** [D-025]: w próbie 90-dniowej
  jedna faktura kosztowa (215,80 PLN, VAT 40,35 PLN) nie trafiła do
  ewidencji wcale, mimo że okres był już zarchiwizowany. Przeoczenie
  jest **niewykrywalne bez porównania z rejestrem** — to jest funkcja,
  której ręczna archiwizacja nie ma.

### T-06b — Komenda czyszcząca archiwum

> Jako osoba odpowiedzialna za dane, gdy archiwum urośnie, chcę je
> wyczyścić bez utraty wiedzy o tym, co już pobrałem.

**Obowiązkowa w etapie 1**, nie dodatek — bez niej bezterminowa retencja
[D-034] nie ma bezpiecznika.

- Rozdzielenie indeksu od treści [D-005] pozwala skasować faktury **bez
  utraty idempotencji**.
- AK: po wyczyszczeniu ponowna synchronizacja nie ściąga skasowanych
  faktur powtórnie.
- Otwarte: czy czyszczenie per podmiot, per okres, czy po obu wymiarach.

### T-07 — Ślad audytowy odczytu

> Jako osoba odpowiedzialna za zgodność, gdy ktoś zapyta o zakres dostępu
> do faktur, chcę móc to odtworzyć.

- Wpis: znacznik czasu, NIP kontekstu, kryteria, liczba dokumentów, numery
  KSeF, ścieżka zapisu. Bez poświadczeń [D-011].
- Pominięcia deduplikacyjne logowane jawnie — inaczej ślad sugeruje, że
  faktury nie było.

### T-08 — Wizualizacja PDF

> Jako użytkownik, gdy dostaję komplet faktur za miesiąc, chcę też
> dokumenty, które da się otworzyć i przekazać — nie tylko XML.

Wizualizację generuje **oficjalny generator Ministerstwa Finansów**
(`@akmf/ksef-fe-invoice-converter`, MIT), zwendorowany jako zbudowany
bundel w `src/ksef_mcp/vendor/` i uruchamiany pod Node [D-027].

- Kontrakt: `generateInvoice(file, { nrKSeF, qrCode }, 'blob')` — wejściem
  dokładnie te bajty, które zapisaliśmy pod `<NumerKSeF>.xml`.
- **Kod QR, link weryfikacyjny i numer KSeF dostajemy w pakiecie** —
  generator rysuje je sam z przekazanego `qrCode`. Nie budujemy tego.
- Node przez `fnm`, wersja przypięta w `.node-version` [D-029]. Plik
  wchodzi **tym samym PR-em**, co bundel — pin i konsument razem.
- Nota licencyjna MIT obok bundla; projekt jest AGPL-3.0-only.
- Zweryfikowane wykonaniem: wynik ma ten sam rozmiar i tę samą treść co
  PDF z portalu MF.
- AK: brak Node → czytelny komunikat, narzędzie **nadal oddaje** XML, CSV
  i listę. Degradacja, nie awaria.
- **Znane ograniczenie:** generator obsługuje FA(1)/FA(2)/FA(3)/UPO/PEF,
  ale przetestowano wyłącznie **FA(3)**.

---

## Etap 2 — Faktury sprzedażowe

### T-09 — Odblokowanie kierunku

- Wystawienie wyboru `Subject1` / `Subject2` w sygnaturze toola [D-008].
- **Zmiana wartości, nie dodanie ścieżki** — jeśli okaże się refaktorem,
  szew był wycięty w złym miejscu.

---

## Etap 3 — Wsparcie biur rachunkowych

### T-10 — Wiele poświadczeń, przełączanie kontekstu

- Szew w warstwie poświadczeń, nie w zapytaniu [D-009]. ST-5.
- **Twarde przełączenie:** osobny katalog, osobny log, wyczyszczony
  kontekst poprzedniego klienta. Nigdy dwa podmioty w jednym katalogu.
- NIP i nazwa podmiotu w **każdej** odpowiedzi. Bez wyjątku.
- Ryzyko nr 1: agent pobiera faktury klienta A, odpowiadając o kliencie B.
  **API nie zgłosi błędu** — uprawnienie istnieje.
- Ograniczenie skali: budżet 20 eksportów/h **na kontekst**; biuro z
  kilkudziesięcioma podmiotami wyczerpie go na liczbie klientów, nie na
  liczbie faktur. ST-1.

---

## Poza zakresem (horyzont)

Wysyłka faktur, korekty, UPO, zarządzanie uprawnieniami i tokenami, tryby
offline i awaryjne. Każde z nich wywołuje skutki podatkowe i wymaga bramek
potwierdzenia, których etap odczytowy świadomie nie ma [D-001], [D-011].

Dystrybucja dla odbiorcy nietechnicznego — instalator Windows albo
rozszerzenie Claude Desktop [D-028]. Zaprojektowana jako szew, nie
budowana.
