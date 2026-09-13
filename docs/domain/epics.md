# Epics & Tickets

> Aktualizowane w miarę doprecyzowywania. Ukończone = `[DONE]`.

## Implementation Priority

Kolejność wynika z [D-001]. Etapy 2 i 3 są rozszerzeniami, nie nowymi
ścieżkami — patrz [D-008] i [D-009].

---

## Etap 1 — Jeden podmiot, faktury zakupowe, odczyt

### T-01 — Uruchomienie serwera pod `uvx`

> Jako osoba konfigurująca narzędzie, gdy dodaję serwer do klienta MCP,
> chcę żeby wystartował jednym wpisem bez instalowania czegokolwiek
> ręcznie, żebym nie odpuścił na pierwszym kroku.

- Pakiet `ksef-mcp` [D-015], `src-layout`, entry point konsolowy.
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
- Środowisko (TEST/DEMO/PROD) oznaczane w każdej odpowiedzi.
- Instrukcja zdobycia tokenu w komunikacie błędu, nie link do PDF-a.
- AK: na maszynie bez sesji D-Bus narzędzie zwraca błąd z instrukcją i
  **nie zawiesza transportu**.

### T-03 — Zapytanie o listę faktur za okres

> Jako nabywca, gdy mówię „faktury za sierpień", chcę dostać kompletną
> listę, a jeśli niekompletną — usłyszeć o tym wprost.

- `POST /invoices/query/metadata`, w porcie `role="buyer"` → `Subject2`,
  **niewystawiony jako parametr toola** [D-008], [D-019].
- `pageSize=250` na sztywno [D-010]; **tool nie wystawia paginacji** —
  jedno deterministyczne wywołanie po stronie serwera [D-020].
- Licznik budżetu zapytań; własny retry ponad `KSeFRateLimitError`, bo
  `max_delay=4s` w SDK nie wystarczy przy limicie godzinowym [D-017].
- Walidacja okna dat we własnej warstwie; trzeci `except` na `httpx`.
- Odpowiedź tekstowa ma twardy próg pozycji [D-023].
- `DateType` jawny w odpowiedzi dla użytkownika, bo zmienia wynik.
- „Sierpień" bez roku → ostatni miniony + komunikat o założeniu.
- AK: przy wyniku stronicowanym komunikat „pokazuję N z M"; **nigdy cicha
  obcinka**.
- AK: zero wyników to osobny komunikat pokazujący, o co dokładnie zapytano
  (NIP, daty, kierunek, środowisko).

### T-04 — Pobranie i archiwizacja

> Jako księgowa, gdy kompletuję miesiąc, chcę pliki w znanym katalogu z
> nazwami, które coś znaczą — żeby dało się je przekazać i zaimportować.

- `GET /invoices/ksef/{ksefNumber}`, zapis `temp → rename` pod
  `<NumerKSeF>.xml` [D-006].
- Deduplikacja po numerze KSeF; **indeks odrębny od treści** [D-005].
- Wznawianie przerwanego pobrania bez duplikatów i bez zaczynania od zera.
- Nigdy ciche nadpisanie istniejącego pliku.
- AK: `PobieranieFakturyNieudane` **nie** oznacza okresu jako kompletnego.

### T-05 — Zestawienie CSV i lista w czacie

> Jako księgowa, gdy uzgadniam miesiąc, chcę jedną tabelę do sprawdzenia
> sumy i przekazania jednym załącznikiem.

- Kolumny świadomie wybrane — minimalizacja danych osobowych.
- Link weryfikacyjny KOD I przy każdej pozycji [D-016].
- Tool zwraca **ścieżki i metadane, nigdy treść faktury** [D-011].
- AK: suma w CSV zgadza się z aplikacją KSeF co do grosza.

### T-06a — Cache metadanych okresu

> Jako użytkownik, gdy pytam o ten sam miesiąc drugi raz, nie chcę czekać
> ani płacić za to budżetem zapytań.

- Powtórzone pytanie o ten sam okres kosztuje **zero** zapytań [D-021].
- Trwały znacznik ostatniego udanego zapytania per okres.
- Ważniejszy niż deduplikacja: deduplikacja chroni dysk, cache chroni
  deficytowy budżet.

### T-06 — Delta okresu

> Jako księgowa, gdy miesiąc jest już zaksięgowany, chcę wiedzieć, że
> wpadła do niego nowa faktura — zanim dowiem się o tym od doradcy.

- Ponowne odpytanie okresu zwraca „nowe od ostatniego pobrania" [D-005].
- Stan okresu: liczba, suma, znacznik czasu.
- Sygnalizacja, nie rozstrzygnięcie — ujęcie podatkowe to decyzja
  człowieka. ST-4.

### T-07 — Ślad audytowy odczytu

> Jako osoba odpowiedzialna za zgodność, gdy ktoś zapyta o zakres dostępu
> do faktur, chcę móc to odtworzyć.

- Wpis: znacznik czasu, NIP kontekstu, kryteria, liczba dokumentów, numery
  KSeF, ścieżka zapisu. Bez poświadczeń [D-011].
- Pominięcia deduplikacyjne logowane jawnie — inaczej ślad sugeruje, że
  faktury nie było.

---

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
- Zweryfikowane wykonaniem: wynik ma ten sam rozmiar i tę samą treść co
  PDF z portalu MF.
- AK: brak Node → czytelny komunikat, narzędzie **nadal oddaje** XML, CSV
  i listę. Degradacja, nie awaria.
- AK: brak `fnm env` w profilu powłoki → wykryte i zgłoszone, żeby wersja
  Node nie rozjechała się po cichu.
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

---

## Poza zakresem (horyzont)

Wysyłka faktur, korekty, UPO, zarządzanie uprawnieniami i tokenami, tryby
offline i awaryjne. Każde z nich wywołuje skutki podatkowe i wymaga bramek
potwierdzenia, których etap odczytowy świadomie nie ma [D-001], [D-011].
