# Decision Log

> **Append-only.** Decyzji się nie usuwa.
> Aby zmienić decyzję, dodaj nowy wpis z `supersedes: D-NNN`.

> ## ℹ️ REWIZJA SYNCHRONIZACJI ZAKOŃCZONA — czytaj [D-031]
>
> Pod koniec warsztatu 001 ustalono u źródła, że Ministerstwo Finansów
> publikuje **kanoniczny wzorzec synchronizacji przyrostowej**, dla
> którego zaprojektowaliśmy własne odpowiedniki. Rewizja jest zamknięta
> i mieszka w **[D-031]**.
>
> Co się zmieniło: eksport paczek jest ścieżką **podstawową**, nie
> awaryjną; okna wyznacza **High Water Mark**, a nie my; `DateType` przy
> synchronizacji jest przybity do `PermanentStorage`; kompletność wymaga
> **iteracji po typach podmiotu**; szyfrowanie AES-256 wraca do zakresu
> etapu 1. Przeoczony wcześniej limit `GET /invoices/ksef/{ksefNumber}`
> = **64 żądania/h** przesądził o odejściu od ścieżki synchronicznej.
>
> Co się **nie** zmieniło, wbrew temu, co po drodze zaraportowano:
> deduplikacja po numerze KSeF po naszej stronie **jest** wymagana —
> `_metadata.json` jej nie zastępuje, tylko ją zasila. [D-005] stoi.
>
> `D-024` jest zastąpione. `D-005`, `D-008` i `D-022` obowiązują z
> korektami opisanymi w [D-031] — ich wpisy odsyłają do właściwych
> sekcji.

---

## D-001 — MVP to odczyt faktur za wybrany miesiąc; mapa drogowa ma trzy etapy

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Pierwsza iteracja dostarcza jeden scenariusz: „znajdź faktury
  za wybrany miesiąc i pobierz je". Mapa drogowa:
  1. **Etap 1** — jeden podmiot, faktury **zakupowe**, wyłącznie odczyt.
  2. **Etap 2** — jeden podmiot, faktury **sprzedażowe**.
  3. **Etap 3** — wsparcie biur rachunkowych, przełączanie podmiotów.
- **Poza zakresem (horyzont):** wysyłka faktur, korekty, UPO, zarządzanie
  uprawnieniami i tokenami, tryby offline/awaryjne.
- **Uzasadnienie:** zakres odczytowy nie wywołuje skutków podatkowych, więc
  agent AI może działać bez bramek potwierdzeń, które psują zaufanie do
  narzędzia (patrz D-011). Etapy 2 i 3 to rozszerzenia **wartości
  parametru**, nie nowe ścieżki — patrz D-008 i D-009.
- **Alternatywy:** pełny zakres od razu — odrzucone, bo wciąga kryptografię
  wysyłkową, UPO i model uprawnień, zanim ktokolwiek potwierdzi, że sam
  odczyt działa.

## D-002 — Wybór biblioteki klienta KSeF odłożony; warstwa antykorupcyjna po pierwszym kontakcie

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Nie wybieramy teraz biblioteki. Klient KSeF jest wymiennym
  szczegółem za portem. Warstwę antykorupcyjną piszemy **po** pierwszym
  realnym wywołaniu API, nie przed.
- **Kontekst ustalony u źródła:** Ministerstwo Finansów (GitHub: `CIRFMF`)
  publikuje oficjalne SDK wyłącznie dla **Javy** (`ksef-client-java`) i
  **C#** (`ksef-client-csharp`). **Nie ma oficjalnego SDK w Pythonie.**
  Publikowany jest natomiast oficjalny kontrakt: `open-api.json` w
  `CIRFMF/ksef-api` (OpenAPI 3.0.4, `info.version: v2`, tytuł „KSeF API TE"
  — zrzut ze środowiska testowego).
- **Kandydaci:** pakiet społecznościowy z PyPI (`ksef2`, `ksef-client`),
  generowanie klienta z oficjalnego OpenAPI, cienki klient własny pod
  garstkę endpointów MVP.
- **Uzasadnienie:** warstwa antykorupcyjna zaprojektowana na wyobrażonym
  API chroni przed niewłaściwą rzeczą (anty-wzorzec Leaky Abstraction,
  wskazany przez adwokata diabła).
- **Kryterium rozstrzygnięcia:** zweryfikować na zainstalowanym pakiecie —
  nie z README ani z odznak w repo — czy kandydat pokrywa
  `POST /invoices/query/metadata` i `GET /invoices/ksef/{ksefNumber}`.

## D-003 — Wynikiem MVP są XML-e na dysku, zestawienie CSV i lista w czacie; PDF w etapie 1b

- **Status:** **Zastąpiona przez D-016**
- **Warsztat:** 001
- **Decyzja:** Etap 1 dostarcza: pliki XML w zadeklarowanym katalogu z
  nazwami niosącymi znaczenie, jedno zestawienie CSV za okres, oraz listę
  w czacie (kontrahent, data, kwota, numer KSeF). **Wizualizacja PDF
  przechodzi do etapu 1b**, za portem `InvoiceRenderer`.
- **Uzasadnienie:** persona użytkownika nazwała to najczęściej pomijanym
  elementem — „wynik ma trafić tam, gdzie idzie moja praca, a nie do
  czatu". Listy w czacie nie da się przesłać ani zaimportować. XML sam w
  sobie też nie jest dokumentem dla człowieka, ale CSV domyka potrzebę
  uzgodnienia miesiąca bez kosztu renderowania.
- **Dlaczego PDF nie w etapie 1:** oficjalny generator MF
  (`CIRFMF/ksef-pdf-generator`) jest biblioteką **TypeScript wymagającą
  Node.js 22.14.0**, a zgłoszenie `CIRFMF/ksef-api#12` o endpoint PDF w API
  zostało **zamknięte bez realizacji**. Własny render z XSD odpada, bo
  zgłaszający w tym samym wątku wskazał, że *sam XML nie zawiera wszystkich
  danych potrzebnych do wizualizacji* — dlatego MF wydało osobną bibliotekę
  zamiast arkusza XSLT.
- **Plan dla etapu 1b:** oficjalny generator MF jako zależność
  **opcjonalna** — jest Node, jest PDF; nie ma Node, narzędzie mówi o tym
  wprost i oddaje XML.

## D-004 — Token KSeF w keyringu systemowym, z twardym fallbackiem zamiast promptu

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Poświadczenia trzymamy w keyringu systemowym. Gdy backend
  keyringu jest niedostępny **albo chciałby zapytać interaktywnie** —
  natychmiastowy błąd z instrukcją naprawy. **Nigdy prompt.** Zmienna
  środowiskowa pozostaje jawnie wybieraną ścieżką awaryjną.
- **Uzasadnienie:** adwokat diabła wskazał to jako najbardziej prawdopodobną
  przyczynę śmierci projektu — serwer MCP na transporcie stdio nie ma
  przeglądarki ani interakcji, a interaktywny prompt o hasło **zawiesza
  transport**. **Potwierdzone wykonaniem (2026-09-13):** bez sesji D-Bus
  `keyring.get_keyring()` zwraca `keyring.backends.fail.Keyring`, a
  `SecretService` znika z listy dostępnych backendów — wykrycie nie
  wymaga odczytu, zapisu ani interakcji. Obrona jest wykonalna jedną
  linijką przy starcie.
- **Drugi tryb awarii — backend obecny, lecz zablokowany.** Zweryfikowany
  (ST-3) i groźniejszy niż brak backendu, bo pułapka siedzi w samym
  `keyring`: `get_preferred_collection()` po sprawdzeniu `is_locked()`
  **sam woła `unlock()`**, czyli otwiera prompt w środku wywołania
  wyglądającego na odczyt. Obrona: odczytać stan blokady przez
  `secretstorage` (`collection.is_locked()` — czysty odczyt właściwości
  D-Bus), **zanim** dotkniemy `keyring.get_password()`. Sprawdzenie musi
  poprzedzać **każde** dotknięcie sekretu, nie tylko start — kolekcja
  może zostać zablokowana po uśpieniu maszyny.
- **Kontekst:** token KSeF wyświetla się **jednorazowo** przy generowaniu i
  nie da się go odczytać ponownie — jego utrata jest kosztowna.
  Hosty MCP nie czytają `.env`; zmienne przychodzą z bloku `env` w
  konfiguracji klienta, która jest plaintextem na dysku. Stąd wzorzec:
  w konfiguracji tylko nazwa konta, sam sekret z keyringu.
- **Alternatywa:** zmienna środowiskowa jako ścieżka podstawowa — odrzucona
  jako domyślna, przyjęta jako awaryjna.
- **Backend wybiera użytkownik, nie priorytet biblioteki.** CLI wylicza
  dostępne magazyny (`keyring.backend.get_all_keyring()` — wykrycie bez
  odczytu, zapisu i interakcji), pokazuje, który byłby domyślny, i
  utrwala **jawny wybór** w konfiguracji. Powód: backend wybierany
  automatycznie zmienia się, gdy w systemie pojawi się inny pakiet
  keyringu — token zapisany wcześniej przestaje być widoczny, choć nadal
  istnieje w starym magazynie. Objaw („token zniknął") byłby oddalony od
  przyczyny (instalacja niezwiązanego pakietu). Ten sam wzorzec awarii,
  przed którym chroni rozdział cache od trwałego stanu [D-032].
- **Zapis sekretu jest komendą CLI**, wywoływalną niezależnie od
  onboardingu (rotacja tokenu, drugi podmiot, naprawa wpisu) i używaną
  przez onboarding jako krok — jedna implementacja, nie dwie. Odczyt
  wartości ze stdin **bez echa**; weryfikacja po zapisie potwierdza
  odczyt **nie pokazując wartości**. Komenda kasująca domyka rotację.
  Uzasadnienie z przebiegu na sucho: bez CLI zapis wymagał ręcznego
  odtworzenia wewnętrznego schematu atrybutów biblioteki przez
  `secret-tool` — czego żaden użytkownik nie zrobi. Patrz GH-4.

## D-005 — Idempotencja pobierania od pierwszego dnia; indeks deduplikacji odrębny od treści

- **Status:** **Aktywna — potwierdzona przez [D-031].** Podejrzenie, że
  własny indeks dubluje mechanizm MF, okazało się błędne: dokumentacja
  HWM wymaga deduplikacji **po stronie systemu lokalnego**, po numerze
  KSeF. Plik `_metadata.json` w paczce jej nie zastępuje — jest jej
  **wejściem**, bo niesie numery KSeF wszystkich faktur w paczce.
- **Warsztat:** 001
- **Decyzja:** Pobranie okresu jest powtarzalne. Deduplikacja po **numerze
  KSeF** (nigdy po numerze własnym sprzedawcy). Ponowne odpytanie okresu
  zwraca **deltę** — „nowe od ostatniego pobrania". Indeks deduplikacji
  (numery KSeF + skrót) przechowywany **osobno od treści faktur**.
- **Uzasadnienie:** KSeF nie zna pojęcia „zamknięcia okresu" i nie
  powstrzyma napływu faktur do miesiąca już rozliczonego — persona księgowej
  nazwała to najboleśniejszym zdarzeniem całego MVP. Rozdzielenie indeksu od
  treści rozwiązuje konflikt wskazany przez personę compliance:
  deduplikacja po numerze nagradza trzymanie starych plików jako indeksu, co
  walczy z retencją. Osobny indeks pozwala skasować treść, nie tracąc
  idempotencji.

## D-006 — `PobranieOkresu` nie jest agregatem

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** `PobranieOkresu` jest **serwisem aplikacyjnym** bez
  tożsamości. Jedynym agregatem jest `WpisArchiwum`. `SesjaKSeF` schodzi do
  obiektu wartości.
- **Uzasadnienie:** adwokat diabła wykazał, że proponowany agregat łączył w
  jednym korzeniu trzy różne czasy życia — stan paginacji (stan klienta
  HTTP), zbiór numerów (rzut Archiwum) i kierunek (parametr zapytania) — i
  żył przez N wywołań sieciowych z wygasającą sesją w tle. Agregat broni
  niezmiennika w jednej transakcji; to była **saga w przebraniu**. Dodatkowo
  niezmiennik „powtórzone pobranie nie tworzy duplikatów" należy do
  Archiwum, nie do Pobrania — dwa agregaty pilnowały tej samej reguły bez
  rozstrzygnięcia, kto wygrywa przy rozbieżności (Cross-Aggregate
  Transaction).
- **Egzekwowanie niezmiennika:** zapis `temp → rename` pod nazwą
  `<NumerKSeF>.xml`. Na systemie plików `rename(2)` w obrębie tego samego
  FS jest jedynym prymitywem atomowym — i to on, nie zbiór w pamięci, jest
  strażnikiem niezmiennika.

## D-007 — Dwa konteksty ograniczone, nie pięć

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Konteksty: **Dostęp** i **Archiwum**. „Powierzchnia MCP" i
  „Prezentacja" to warstwy dostarczania, nie konteksty domenowe.
- **Uzasadnienie:** pięć kontekstów w jednoprocesowym narzędziu stdio to
  mapowanie kontekstów zastosowane do problemu, który go nie ma
  (anty-wzorce One Model to Rule Them All / Golden Hammer).

## D-008 — `subjectType` przybity do `Subject2`, niewystawiony jako parametr narzędzia

- **Status:** **Aktywna, z korektą z [D-031].** Przybicie do `Subject2`
  pozostaje poprawne dla faktur zakupowych [D-019] i pole nadal nie
  wychodzi do sygnatury toola. Korekta: eksport wymaga wskazania typu
  podmiotu, a punkt kontynuacji jest **osobny dla każdego typu** — więc
  etap 1 potrzebuje pętli po typach, jeśli ma deklarować kompletność
  okresu, a nie tylko „faktury, gdzie jestem nabywcą".
- **Warsztat:** 001
- **Decyzja:** Kierunek faktur jest w modelu obecny od pierwszego dnia
  (pole **wymagane** przez API), ale w etapie 1 ma wartość stałą
  `Subject2` (nabywca) i **nie pojawia się w sygnaturze toola MCP**.
- **Uzasadnienie:** synteza dwóch ustaleń. Z jednej strony `subjectType`
  (`Subject1` = sprzedawca, `Subject2` = nabywca, `Subject3`,
  `SubjectAuthorized`) jest polem wymaganym w `POST /invoices/query/metadata`
  — bez niego zapytanie jest nieważne, więc to nie jest szew na zapas.
  Z drugiej strony adwokat diabła trafnie wskazał, że wystawienie go jako
  parametru narzędzia to Speculative Generality o realnym koszcie: agent LLM
  zobaczy parametr wyglądający na wybór i zawoła go z wartością, której etap
  1 nie obsługuje. Reguła foundation-ready dopuszcza rozszerzenia **danych**,
  nie rozszerzenia **powierzchni API**.
- **Etap 2** polega więc na odblokowaniu wartości i wystawieniu wyboru, a
  nie na dodaniu ścieżki.

## D-009 — Kontekst podmiotu należy do poświadczeń, nie do zapytania

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Podmiot (NIP) nie jest parametrem zapytania o faktury. Jest
  własnością sesji i poświadczenia. Etap 3 (biura rachunkowe) to **wiele
  poświadczeń i wiele sesji**, nie jeden parametr więcej.
- **Uzasadnienie:** pierwotny szew był wycięty w złym miejscu — modelował
  wyobrażony etap 3 zamiast domeny i na etapie 3 i tak by nie zadziałał.
  [Verify] Że kontekst podmiotu jest własnością sesji/tokenu, wymaga
  potwierdzenia w `uwierzytelnianie.md`.
- **Konsekwencja dla etapu 3:** przełączenie podmiotu to twarde
  przełączenie kontekstu — osobny katalog, osobny log, wyczyszczony kontekst
  poprzedniego klienta. Nigdy dwa podmioty w jednym katalogu.

## D-010 — `pageSize` 250 jest niezmiennikiem, nie parametrem do strojenia

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Zapytania o metadane zawsze z `pageSize` maksymalnym.
- **Uzasadnienie:** [Verify] limity zapytań metadanych to 8/s, 16/min i
  **20/h**, przy `pageSize` 10–250 i domyślnym **10**. Naiwna paginacja
  domyślną dziesiątką wyczerpuje limit godzinowy na **200 fakturach**; przy
  250 sufit to ~5000 faktur/h. To nie jest optymalizacja, tylko warunek
  działania. Liczby pochodzą z badania persony integratora i wymagają
  potwierdzenia w `open-api.json` przed uznaniem za twarde.
- **Konsekwencja:** powyżej sufitu godzinowego jedyną drogą jest
  `POST /invoices/exports`, która **wymaga szyfrowania** — czyli AES/RSA
  wraca do zakresu nawet w MVP czysto odczytowym. Patrz ST-1.

## D-011 — Narzędzie zwraca ścieżki i metadane, nigdy treść faktury do kontekstu modelu

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Tool MCP oddaje ścieżki plików i podsumowanie metadanych.
  XML faktury nie trafia do kontekstu modelu. Operacje odczytowe **nie
  pytają o zgodę**.
- **Uzasadnienie:** dwa niezależne ustalenia. Compliance: faktura zakupowa
  to **niezaufane wejście** — jej pola tekstowe wypełnia osoba trzecia, a
  treść trafiłaby do promptu agenta działającego na maszynie z dostępem do
  keyringu; tryb tylko-do-odczytu chroni KSeF, nie chroni maszyny.
  Użytkownik: pytanie o zgodę przy odczycie uczy klikać „tak"
  automatycznie i psuje moment, w którym pytanie naprawdę ma znaczenie.
- **Ślad audytowy zostaje** mimo braku bramki potwierdzenia: timestamp, NIP
  kontekstu, kryteria zapytania, liczba dokumentów, numery KSeF, ścieżka
  zapisu. Bez poświadczeń w logu.

## D-012 — Portal weryfikacyjny QR odrzucony jako kanał pozyskiwania faktur i PDF-ów

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** `qr.ksef.mf.gov.pl` nie jest ścieżką pobierania faktur ani
  źródłem wizualizacji PDF.
- **Uzasadnienie:** formularz anonimowy wymaga podania numeru faktury
  sprzedawcy, NIP-u nabywcy, nazwy i kwoty należności ogółem — żeby pobrać
  fakturę, trzeba już znać jej treść. To mechanizm **weryfikacji**
  („czy ta faktura naprawdę jest w KSeF i nie została zmieniona"), nie
  **pozyskiwania**; dla zapytania „pokaż faktury za sierpień" jest
  bezużyteczny, bo szukamy dokładnie tych danych, które trzeba podać.
  Dodatkowo oficjalna dokumentacja kodów QR wskazuje, że ścieżka
  weryfikacyjna oddaje **wyłącznie XML** — PDF-a tam nie ma.
- **Rozważony wariant dwuetapowy (odrzucony z innego powodu):** pobrać
  metadane i XML przez API, a następnie użyć pięciu pól, których żąda
  formularz anonimowy (numer KSeF, numer faktury sprzedawcy, NIP nabywcy,
  nazwa, kwota należności ogółem), by wyciągnąć wizualizację z portalu.
  Kompozycja jest poprawna — API dostarcza dokładnie te pola, więc zarzut
  zapętlenia w tym układzie **nie obowiązuje**. Odrzucona, bo na końcu tej
  drogi leży **ten sam XML**, który mamy już z API: wszystkie źródła
  konsekwentnie wymieniają wyłącznie wersję ustrukturyzowaną. Potwierdza to
  spójna decyzja projektowa MF — wizualizacja jest świadomie zepchnięta na
  klienta (biblioteka `ksef-pdf-generator` + zamknięte zgłoszenie o endpoint
  PDF), więc serwer MF nie renderuje PDF-ów także na portalu.
- **Sprostowanie:** uzasadnienie przez „zapętlenie" było błędne — API
  dostarcza dokładnie te pola, których żąda formularz, więc kompozycja
  działa i została zweryfikowana empirycznie. Decyzja pozostaje w mocy, ale
  z innego powodu: portal nie oddaje PDF-a ani żadnych danych, których nie
  mamy już z API. Pełne ustalenia i dowód w [D-016].
- **Uwaga bezpieczeństwa:** link weryfikacyjny jest **linkiem nosiciela** —
  kto go ma, dociera do dokumentu. Nie logujemy takich linków ani nie
  przekazujemy ich poza maszynę użytkownika.

## D-013 — Automatyzacja przeglądarki na Aplikacji Podatnika odrzucona

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Nie pozyskujemy faktur ani PDF-ów przez Playwright, `curl`
  ani inną automatyzację interfejsu webowego Aplikacji Podatnika.
- **Uzasadnienie:** Playwright dokłada runtime Node **plus ~300 MB
  przeglądarki**, czyli łamie obietnicę „jeden proces, zero infrastruktury"
  mocniej niż samo Node — a Node i tak pozostaje potrzebny. Wymaga
  interaktywnego logowania per podmiot, co zderza się z brakiem interakcji
  na transporcie stdio (patrz D-004). Scraping UI psuje się przy każdej
  zmianie portalu. Wariant z `curl` jest słabszy jeszcze o jeden poziom:
  wymagałby odtworzenia nieudokumentowanego wewnętrznego API portalu wraz z
  obsługą sesji — kontraktu, którego nikt nam nie obiecał.

## D-014 — Cykl życia procesu jest w zakresie modelowania

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Uruchamianie, współdzielenie i sprzątanie procesu serwera to
  część projektu, nie szczegół wdrożeniowy.
- **Uzasadnienie:** udokumentowane doświadczenie z innego serwera MCP
  uruchamianego per sesja agenta: brak sprzątania urósł do **114 procesów i
  3,79 GB RAM po trzech dniach**. Serwer pod `uvx` w procesie ma dokładnie
  tę charakterystykę. Patrz ST-2.

## D-016 — Wizualizację renderuje przeglądarka użytkownika; emitujemy link weryfikacyjny

- **Status:** Aktywna
- **Warsztat:** 001
- **supersedes:** D-003 (w części dotyczącej sposobu dostarczenia PDF)
- **Decyzja:** Etap 1 **nie generuje PDF-ów**. Przy każdej fakturze
  emitujemy **link weryfikacyjny KOD I** — w zestawieniu CSV i na liście w
  czacie. Wizualizację renderuje przeglądarka użytkownika, oficjalnym kodem
  MF. Hurtowe generowanie PDF-ów na dysk zostaje jako opcja etapu 1b, przez
  oficjalny pakiet npm jako zależność **opcjonalną**.
- **Ustalenie rozstrzygające (obserwacja bezpośrednia, nie dokumentacja):**
  strona `/download` portalu zawiera (a) atrybut `data-xml-text` z pełnym
  XML-em faktury zakodowanym base64 oraz (b) `<script
  src="/client-app/pdf-lib/invoice-visualisation-module.js">`. **PDF
  powstaje w przeglądarce**, z tego XML-a, przez ten moduł. Serwer MF nie
  emituje PDF-a żadną ścieżką HTTP — `curl` na tym samym URL-u dostaje
  HTML, a przycisk pobierania jest `disabled` do czasu załadowania JS.
- **Spójność z resztą ustaleń:** zamknięte zgłoszenie `CIRFMF/ksef-api#12`,
  wydanie `ksef-pdf-generator` jako biblioteki klienckiej i ten moduł na
  portalu to jedna konsekwentna decyzja projektowa MF — renderowanie należy
  do klienta, serwer oddaje wyłącznie strukturę.
- **Historia pomyłki (zachowana celowo):** po drodze przyjęto błędnie, że
  portal serwuje PDF pod GET-em. Wcześniej przyjęto równie błędnie, że
  portal oddaje wyłącznie XML i jest bezużyteczny. Obie tezy padły dopiero
  pod obserwacją. Wniosek procesowy: o zachowaniu cudzego systemu nie
  rozstrzygamy z dokumentacji ani z wyglądu strony.
- **Zweryfikowany, lecz nieużyteczny wariant:** POST formularza
  weryfikacyjnego (`?handler=Format`) z polami `Nip`, `IssueDate`,
  `InvoiceHash`, `KsefNumber`, `InvoiceNumber`, `BuyerIdentifierType`,
  `BuyerIdentifierValue`, `Amount` + `__RequestVerificationToken`
  **przechodzi** i zwraca ciasteczko `DownloadVerified` (JWT
  `VisualisationToken`, `ValidationResult: Success`, ważność 5 minut).
  Odrzucone z dwóch powodów: na końcu tej drogi leży **ten sam XML, który
  mamy już z API**, a token antyCSRF istnieje właśnie po to, by żądanie
  pochodziło z przeglądarki — hurtowe obchodzenie go byłoby omijaniem
  postawionej wprost kontroli na portalu rządowym.
- **Zysk uboczny:** link KOD I umiemy złożyć sami
  (`/invoice/{NIP}/{DD-MM-RRRR}/{SHA-256 base64url pliku}`) z XML-a i
  metadanych. Jest to i tak potrzebne w etapie 1b, bo faktura prezentowana
  jako PDF lub wydruk ma być oznaczona linkiem weryfikacyjnym, kodem QR i
  numerem KSeF.

## D-017 — Klientem KSeF jest `ksef2`; retry, walidacja okna i `pageSize` należą do portu

- **Status:** Aktywna
- **Warsztat:** 001
- **supersedes:** D-002 (w części odkładającej wybór klienta)
- **Decyzja:** Wybieramy `ksef2` (0.19.0, `requires-python >=3.12`).
  **Retry, walidacja okna dat i walidacja `pageSize` mieszkają w naszym
  porcie**, nie są delegowane do SDK.
- **Podstawa (weryfikacja przez czytanie zainstalowanego źródła, nie
  README):**
  - `query_metadata(*, filters, params)` z `date_type` w literałach
    `issue_date` / `invoicing_date` / `permanent_storage` — pokrywa się z
    naszym `Okres`.
  - `page_size = Field(default=10, ge=10, le=250)` — nasz wymóg 250
    [D-010] jest realizowalny wprost, a domyślna dziesiątka SDK to
    dokładnie pułapka, którą opisaliśmy.
  - `download_invoice(*, ksef_number) -> bytes` — **surowe bajty, bez
    parsowania**; nic nie może zmodyfikować treści przed zapisem
    `temp → rename` [D-006].
  - Retry jako middleware z respektowaniem `Retry-After`, plus
    `KSeFRateLimitError.retry_after` wystawiony wołającemu.
- **Dlaczego retry w porcie:** wbudowany retry ksef2 ma
  `max_delay = 4.0s`. Przy limicie **20 zapytań/h** serwer zwróci
  `Retry-After` rzędu minut, middleware przytnie to do 4 sekund, wyczerpie
  3 próby i rzuci wyjątek. Musimy złapać `KSeFRateLimitError` i użyć jego
  `retry_after` sami. To wymaganie, nie detal.
- **Dlaczego nie `ksef-client` (smekcio) 0.17.1:** **nie ma żadnej pętli
  ponawiania ani backoffu.** `Retry-After` jest jedynie wystawiony jako
  atrybut wyjątku. Zgodnie z kryterium przyjętym przed porównaniem —
  dyskwalifikacja, nie niedogodność.
- **Konsekwencje dla portu:**
  - Jawna tablica translacji kierunku: ksef2 mówi
    `role: buyer/seller/third_subject/authorized_subject`, drut mówi
    `Subject1..SubjectAuthorized`.
  - **Trzeci `except` na `httpx`** — obie biblioteki przepuszczają surowe
    błędy transportowe (DNS, connection) sprzed odpowiedzi; nie mają
    wspólnej bazy z wyjątkami SDK.
  - Walidacja okna dat u nas. `ksef-client` wymusza maksimum **100 dni**
    po stronie klienta; `ksef2` nie egzekwuje nic, więc przy zbyt szerokim
    oknie dostaniemy błąd serwera zamiast czytelnego komunikatu.
    [Verify] czy analogiczny limit istnieje po stronie API MF.
  - `ksef2` nie zwraca sumy kontrolnej z nagłówka (`ksef-client` zwraca).
    Przy deduplikacji po numerze KSeF to nie blokada.

## D-018 — `MCPServer`, nie `FastMCP`

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Serwer budujemy na `from mcp.server import MCPServer`.
- **Podstawa (zweryfikowane na zainstalowanym pakiecie `mcp` 2.2.0):**
  `FastMCP` został **usunięty**, nie przemianowany. Moduł
  `mcp.server.fastmcp` nadal istnieje, ale jego jedyną zawartością jest
  `raise ModuleNotFoundError` z komunikatem migracyjnym. Obowiązuje
  `MCPServer("ksef-mcp")`, dekorator `@server.tool()`, **synchroniczne**
  `server.run()` z domyślnym transportem `stdio`.
- **Konsekwencja:** każdy tutorial i przykład oparty na `FastMCP` jest
  martwy. `mcp<2` to jedyny sposób utrzymania kodu w starym stylu.

## D-019 — Kierunek: `Subject2` = nabywca = faktury zakupowe

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Etap 1 pyta jako `Subject2`. Potwierdza D-008.
- **Dowód (odczyt z treści prawdziwej faktury FA(3)):** w schemacie FA
  `Podmiot1` to **sprzedawca**, `Podmiot2` to **nabywca**. W zapytaniu
  `subjectType` deklaruje, kim jest pytający — więc `Subject2` znaczy
  „jestem nabywcą", czyli faktury zakupowe. To jest etap 1 [D-001].
- **Mapowanie w `ksef2` potwierdzone** (odczyt `_map_subject_type` w
  `infra/mappers/invoices/requests.py`): `seller → Subject1`,
  `buyer → Subject2`. Zgodne ze schematem FA, semantyka nie jest
  odwrócona. W porcie etap 1 przybija `role="buyer"`.
- **Uwaga procesowa:** zgłoszono wcześniej podejrzenie odwrócenia
  semantyki; okazało się pomyłką w relacji, nie w kodzie. Rozstrzygnął
  odczyt treści prawdziwej faktury. Wniosek: przy sprzecznych relacjach
  wracamy do artefaktu źródłowego, nie do streszczenia.

## D-020 — Paginacja nie jest sterowana przez model; budżet zapytań jest liczony

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Tool **nie wystawia paginacji**. Skompletowanie listy za
  okres to **jedno deterministyczne wywołanie** po stronie serwera, z
  `pageSize=250` na sztywno [D-010]. Serwer prowadzi **licznik budżetu
  zapytań** i raportuje jego stan.
- **Uzasadnienie:** budżet 20 zapytań/h dzielony jest między metadane,
  pobieranie treści, ponowienia **i każde błędne wywołanie agenta**. Agent
  LLM sterujący stronicowaniem spali budżet godzinowy w dwie minuty na
  niepotrzebnych ponowieniach. To nie jest kwestia modelu domenowego, lecz
  twarde wymaganie na powierzchnię narzędzia.
- **Powiązanie:** rozszerza [D-011] — nie tylko treść faktury nie trafia
  do modelu, ale i decyzje o zużyciu deficytowego zasobu.

## D-021 — Cache metadanych okresu

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Powtórzone pytanie o ten sam okres kosztuje **zero
  zapytań** do KSeF. Metadane okresu są cache'owane trwale, wraz ze
  znacznikiem ostatniego udanego zapytania.
- **Uzasadnienie:** przy 20 zapytaniach/h restart procesu bez cache'u
  oznacza przepytanie od zera z zasobu, którego brakuje. Cache jest
  ważniejszy niż deduplikacja — deduplikacja chroni dysk, cache chroni
  budżet.

## D-022 — Deduplikacja i raport „co nowego" to dwie osobne rzeczy

- **Status:** **Aktywna, z korektą z [D-031].** Rozdzielenie obu
  mechanizmów pozostaje słuszne. Korekta: własny „trwały znacznik"
  zastępuje **High Water Mark** — punkt kontynuacji to
  `PermanentStorageHwmDate` albo `LastPermanentStorageDate` dla paczek
  obciętych, przechowywany **osobno dla każdego typu podmiotu**.
- **Warsztat:** 001
- **supersedes:** D-005 (w części łączącej oba mechanizmy)
- **Decyzja:** Rozdzielamy:
  1. **Deduplikacja** — darmowa, `exists()` na `<NumerKSeF>.xml`, wchodzi
     do etapu 1 bez dyskusji.
  2. **Raport „co nowego od ostatniego pobrania"** — wymaga trwałego
     znacznika, jego formatu i migracji na etapie 3. To **funkcja**, nie
     higiena.
- **Uzasadnienie:** „idempotencja od pierwszego dnia" przemycała funkcję
  pod hasłem niezmiennika. Rozdzielenie pozwala wycenić ją osobno.
- **Co zostaje z D-005:** indeks deduplikacji odrębny od treści — ta część
  obowiązuje nadal i łączy się z cache'em metadanych [D-021].

## D-023 — Lista w czacie ma twardy limit

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Odpowiedź tekstowa pokazuje najwyżej **50 pozycji**.
  Powyżej progu narzędzie zwraca **liczbę faktur i sumę brutto**, nigdy
  pełną listę.
- **Uzasadnienie wartości progu — wymiarowane wobec potrzeby, nie wobec
  limitów API:** w rzeczywistym archiwum podmiotu z badania [D-025]
  miesiąc to około **13 faktur**, a pełne 90 dni — **38**. Próg 50 mieści
  więc i typowy miesiąc, i cały kwartał, przez co **w codziennej pracy
  jest niewidoczny**. Odcina dopiero wolumen, który i tak byłby
  nieczytelny w oknie rozmowy.
- **Dlaczego skrót nie zawiera ścieżki do pliku:** tool zwraca ścieżki i
  metadane **w każdej odpowiedzi** [D-011], więc powtarzanie ich w
  podsumowaniu byłoby zdublowaniem. Skrót ma dodać to, czego w ścieżce
  nie widać — rozmiar i wartość okresu.
- **Kolumny zestawienia CSV — rozstrzygnięte:** numer KSeF, numer faktury
  sprzedawcy, data wystawienia, NIP sprzedawcy, nazwa sprzedawcy, brutto,
  netto, VAT. **Osiem kolumn.**
  Świadomie pominięte: adresy, numery rachunków, pozycje faktury.
  Compliance nazwał CSV formatem „wrzucę do arkusza w chmurze" — obok
  PDF-a najbardziej wyciekowym z artefaktów. NIP i numer wystarczają do
  uzgodnień. Ścieżki do plików również pominięte: CSV ma przeżyć
  przesłanie dalej, a ścieżki lokalne po przesłaniu są bezużyteczne.
  Ten zestaw jest zarazem **minimalnym kontraktem `MetadaneFaktury`** w
  porcie [D-017] — te same pola są potrzebne do porównania archiwum z
  rejestrem, które w badaniu [D-025] wykryło brakującą fakturę.
- **Niezmiennik przeniesiony z badania person:** skrócenie musi być
  **powiedziane wprost** („pokazuję 50 z 812"). Cicha obcinka została
  nazwana rzeczą, która zabija zaufanie do narzędzia natychmiast — *„jeśli
  raz złapię narzędzie na tym, że pokazało 50 z 812 i nie powiedziało,
  nie zaufam już żadnej jego liczbie"*.
- **Odrzucone:** próg 25 (skracałby typowy kwartał, więc użytkownik
  widziałby skrót zbyt często) oraz 100 (przy większym podmiocie to już
  rząd dziesięciu tysięcy tokenów w każdej odpowiedzi).

## D-024 — Ścieżka `exports` poza etapem 1

- **Status:** **Zastąpiona przez D-031.** Obalona ustaleniem, że eksport
  paczek jest **zalecanym** mechanizmem synchronizacji przyrostowej, a nie
  ścieżką dla dużych wolumenów. Sufit przyjęty w tej decyzji
  („przyjmujemy sufit wolumenu") opierał się na błędnym oszacowaniu
  ~5000 faktur/h; realny limit `GET /invoices/ksef/{ksefNumber}` to
  **64/h**. Szyfrowanie AES-256, którego ta decyzja unikała, wraca do
  zakresu etapu 1.
- **Warsztat:** 001
- **Decyzja:** `POST /invoices/exports` nie wchodzi do etapu 1.
  Przyjmujemy sufit wolumenu wynikający z limitów zapytań.
- **Uzasadnienie:** ścieżka eksportu wymaga szyfrowania, czyli
  **zarządzania kluczem** — a [D-004] obejmuje wyłącznie token. Wciąganie
  AES/RSA i cyklu życia kluczy do MVP czysto odczytowego kosztuje więcej,
  niż daje. Model nie ma dla tego ani obiektu wartości, ani kontekstu.
- **Odwracalność:** gdy ktoś realnie uderzy w sufit, decyzja wraca —
  wtedy [D-004] musi objąć klucze, nie tylko token. Patrz ST-1.

## D-025 — Osią produktu jest automat, archiwum i delta — nie ładniejsza lista

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Przewagą narzędzia nad darmową Aplikacją Podatnika jest
  brak klikania, lokalne archiwum i odpowiedź na pytanie „co doszło od
  ostatniego razu". Nie prezentacja.
- **Uzasadnienie:** pre-mortem adwokata diabła — *„narzędzie nie umarło od
  błędów, umarło z braku powodu, żeby go użyć"*. Księgowa ma darmową
  aplikację MF; jeśli i tak musi tam zajrzeć, żeby zweryfikować nasz
  wynik, nasza wartość jest zerowa.
- **Konsekwencja:** każda funkcja etapu 1 musi dać się obronić pytaniem
  „czy Aplikacja Podatnika robi to samo jednym kliknięciem?".
- **Status: PRZESZŁO TEST FALSYFIKUJĄCY (2026-09-13).** Twierdzenie o
  wartości jest falsyfikowalne i zostało poddane próbie na rzeczywistym
  archiwum faktur zakupowych właściciela produktu.

### Wynik testu

Zmierzono opóźnienie między datą wystawienia faktury (z nazwy pliku) a
momentem jej zarchiwizowania (czas modyfikacji pliku) dla okna
2026-06-15…2026-09-13.

| Miara | Wartość |
|---|---|
| Faktur w oknie | **57** |
| Z nazwą zawierającą `ksef` | **32 (56%)** |
| Opóźnienie > 7 dni | **35 (61%)** |
| Opóźnienie > 14 dni | 23 (40%) |
| Opóźnienie > 30 dni | 8 (14%) |
| Mediana | **10 dni** |
| Średnia | 17,6 dnia |
| Maksimum | **146 dni** |

Odpowiedź na pytanie „ile faktur automat pokazałby wcześniej" brzmi
więc: **nie zero**. Przy medianie 10 dni i 61% faktur docierających
później niż tydzień po wystawieniu, przestrzeń na wyprzedzenie jest
realna.

### Mocniejszy dowód niż same opóźnienia

**32 z 57 plików ma już `.ksef.` w nazwie.** To nie jest hipoteza o
wartości — to obserwacja, że robota, którą narzędzie ma automatyzować,
jest **już wykonywana ręcznie**, 32 razy w trzy miesiące.

Archiwizacja odbywa się **partiami**: 16 dni roboczych w trzy miesiące,
z wyraźnymi skupiskami (7, 10, 11, 8 i 10 faktur jednego dnia). To jest
kształt pracy wsadowej, którą automat usuwa.

### Odpowiedź na test kontrolny „co konsumuje deltę"

Delta konsumuje się **do lokalnego archiwum plików**, utrzymywanego
ręcznie i nazywanego wg konwencji `data.kontrahent.kategoria.numer`.
Nie jest to „człowiek patrzący na listę" — jest to zasilanie
istniejącego już procesu.

### Granice tego dowodu — nazwane wprost

- Czas modyfikacji pliku mierzy **moment archiwizacji**, nie moment
  dowiedzenia się. Faktura mogła dotrzeć mailem wcześniej i czekać na
  opracowanie. Zmierzone opóźnienie jest więc **górnym oszacowaniem**
  zysku, nie jego miarą.
- Data wystawienia ≠ data nadania numeru KSeF, a to ta druga wyznacza
  moment dostępności [D-031]. Rzeczywiste wyprzedzenie jest o tyle
  mniejsze.
- Synchronizacja katalogu w chmurze mogła zmodyfikować część znaczników
  czasu.
- Próba obejmuje **jeden podmiot**. Wynik nie uogólnia się na biura
  rachunkowe bez osobnego sprawdzenia.

### Druga połowa testu — odpytanie produkcji KSeF

Wykonano tego samego dnia. Uwierzytelnienie tokenem na środowisku
produkcyjnym, `InvoicesFilter.for_buyer`, zakres 90 dni, operacja
wyłącznie odczytowa.

| Miara | Wartość |
|---|---|
| Faktur zakupowych w KSeF | **38** |
| Odnalezionych w archiwum lokalnym | 34 |
| **Brak w archiwum** | **4** |
| Suma brutto brakujących | 840,10 PLN |
| **Suma VAT brakujących** | **157,09 PLN** |

Brakujące dzielą się na dwie kategorie o różnym znaczeniu:

- **Trzy z września** (03, 08, 10) — bieżąca zaległość. Archiwum nie ma
  jeszcze katalogu na wrzesień; ostatnia sesja wsadowa objęła sierpień.
  To normalny rytm pracy, nie strata.
- **Jedna z 7 lipca** — `PGE Energetyka Kolejowa`, 215,80 PLN, VAT
  40,35 PLN. **To jest realne przeoczenie**: lipiec i sierpień zostały
  już zarchiwizowane w sesjach 4–5 sierpnia, a ta faktura tam nie
  trafiła. Archiwum zawiera faktury PGE z 31 maja, 30 czerwca i 31
  lipca — ta z 7 lipca wypada poza regularny rytm dostawcy i właśnie
  dlatego umknęła.

### Wniosek

Odpowiedź na pytanie adwokata diabła nie brzmi „zero". Narzędzie
wykryłoby **jedną fakturę kosztową, która nie dotarła do ewidencji
wcale**, oraz pokazałoby trzy bieżące, zanim zdążyłyby się zestarzeć.
Przy nieodliczonym VAT-cie 40,35 PLN z jednej przeoczonej faktury
kwartalnie, wartość nie leży w kwocie — leży w tym, że **przeoczenie
jest niewykrywalne bez porównania z rejestrem**. Ręczna archiwizacja
nie ma mechanizmu, który powiedziałby „czegoś brakuje".

### Granice tego wyniku

- Dopasowanie jest **heurystyczne**, oparte na nazwach plików: data
  ±3 dni plus słowo z nazwy sprzedawcy albo numer faktury. Nie ma
  pewnego klucza, bo archiwum lokalne nie przechowuje numerów KSeF.
- W trakcie analizy **dwukrotnie poprawiano błędy w samym skrypcie
  porównującym** — kolejne wersje raportowały 10, 7 i wreszcie 4 braki.
  Przyczyny: zły sposób wycinania nazwy sprzedawcy oraz skanowanie
  archiwum tylko na jednym poziomie zagnieżdżenia. Każdą z czterech
  pozycji potwierdzono następnie ręcznym wyszukaniem.
- **To jest argument za tym, żeby deduplikację oprzeć na numerze KSeF**
  [D-005], a nie na nazwie pliku ani numerze sprzedawcy. Potwierdzono natomiast
  obserwacją, że wizualizacja PDF jest **parytetem, nie przewagą**
  [D-027] — więc cały ciężar dowodu spoczywa na automacie, archiwum i
  delcie.
- **Test falsyfikujący (wykonalny bez kodu produkcyjnego):** *„Ile faktur
  z ostatnich 90 dni ten automat pokazałby wcześniej, niż dowiedziano się
  o nich faktycznie — i które to konkretnie?"* Kilka ręcznych wywołań
  `query/metadata`, w ramach budżetu zapytań, porównanych z rzeczywistym
  stanem wiedzy. Wynik „zero" obala tę decyzję. Wynik „N konkretnych
  faktur" daje kryterium, wobec którego da się **wymiarować** cache
  [D-021], deltę [D-022] i próg listy [D-023] — dziś są wymiarowane
  wobec limitów API, czyli wobec ograniczeń, nie wobec potrzeby.
- **Test kontrolny:** *co konsumuje deltę?* Raport „nowe od ostatniego
  pobrania" ma wartość tylko wtedy, gdy jego wyjście gdzieś wpada —
  dekretacja, import, powiadomienie. Jeśli wyjściem jest człowiek
  patrzący na listę, powstała druga Aplikacja Podatnika z gorszym
  interfejsem. **Automat, którego produktem jest spojrzenie, nie jest
  automatem.**

## D-026 — PDF renderujemy przez `ksef2`; Node odpada, dochodzi zależność systemowa

- **Status:** **Zastąpiona przez D-027**
- **Warsztat:** 001
- **supersedes:** D-016 (w części o sposobie renderowania)
- **Decyzja:** Wizualizację PDF generujemy w procesie, przez
  `InvoicePDFExporter` z `ksef2`. Ścieżka: **XML → XSLT (lxml) → HTML →
  WeasyPrint → PDF**. Gałąź z Node i modułem
  `@akmf/ksef-fe-invoice-converter` **skreślona**.
- **Rozstrzygające ustalenie — to jest oficjalna wizualizacja MF, nie
  layout autora SDK:** pakiet zawiera autentyczny, **podpisany cyfrowo**
  deskryptor `wyroznik.xml` Ministerstwa Finansów (FA(3), obowiązuje od
  2026-02-01), z nienaruszonym podpisem XAdES, wraz z rządowymi
  `styl.xsl` i `WspolneSzablonyWizualizacji`. Autor SDK dołączył artefakty
  rządowe i dokłada na wierzch wyłącznie drobny override CSS na potrzeby
  druku. Wierność jest więc urzędowa, a nie „podobna".
- **Dopasowanie do modelu:** wejściem jest **surowy XML** (bytes/str/plik),
  nie sparsowany model — bierzemy dokładnie te bajty, które zapisaliśmy
  pod `<NumerKSeF>.xml` [D-006], bez pośrednich reprezentacji.
- **Cena, przyjęta świadomie — twarda zależność systemowa:** `cairo` nie
  jest już potrzebne (WeasyPrint porzucił ten backend na rzecz `pydyf`,
  wszystkie zależności pythonowe to czyste wheele). Ale WeasyPrint robi
  runtime'owy `dlopen` na `libgobject-2.0`, `libpango-1.0`,
  `libharfbuzz`, `libharfbuzz-subset`, `libfontconfig`, `libpangoft2-1.0`.
  **Instalacja przejdzie na czystej maszynie, a render wywali się dopiero
  przy pierwszym użyciu** — to gorszy tryb awarii niż błąd instalacji.
- **Obrona przed tym trybem awarii:** `pdf` jako **extra**, nie zależność
  podstawowa. Tool PDF-owy wykrywa brak bibliotek i zwraca czytelny
  komunikat „zainstaluj X", zamiast przepuszczać błąd `dlopen` do agenta.
  Import `weasyprint` w `ksef2` jest leniwy, więc brak PDF-a nie wywraca
  reszty.
- **Znane ograniczenie: wyłącznie FA(3).** W `ksef2` nie ma schematów
  FA(2) — jedyny katalog to `infra/schema/fa3/`. Dla etapu 1 wystarcza
  (FA(3) obowiązuje od 2026-02-01), ale starszych faktur w archiwum tą
  drogą nie wyrenderujemy. Zapisane jako ograniczenie, żeby nie odkrywać
  go przy pierwszej starej fakturze.
- **Potwierdzone wykonaniem (2026-09-13):** wyrenderowano prawdziwą
  fakturę FA(3) przez `InvoicePDFExporter.export_from_string()` —
  `ksef2[pdf]==0.19.0` na Pythonie `3.13.14`, bez żadnych obejść. Wynik:
  4 strony A4 poziomo, producent WeasyPrint 70.0. Przy okazji potwierdzona
  zgodność `ksef2` z przypiętą wersją Pythona.
- **Wierność potwierdzona treścią, nie metadanymi:** etykiety pól w
  wyniku są cytatami z ustawy („Kolejny numer faktury, nadany w ramach
  jednej lub więcej serii…", „…o której mowa w art. 106b ust. 1 pkt 4
  ustawy"), nagłówek to `FA (3)` / `Kod systemowy FA (3)` / `Krajowy
  System e-Faktur (KSeF)`, baner `FAKTURA PODSTAWOWA`. To rządowy XSLT.
- **Konsekwencja produktowa, którą trzeba przyjąć świadomie:** wynik jest
  wizualizacją **dokumentu ustrukturyzowanego** — pole po polu, z pełnymi
  opisami ustawowymi — a nie zwartą fakturą handlową, jaką generują
  systemy komercyjne (porównanie: ten sam dokument z systemu
  komercyjnego mieści się na 1–2 stronach). Wygląda **identycznie jak w
  Aplikacji Podatnika**, więc wizualizacja jest **parytetem, nie
  przewagą**. Przewaga zostaje tam, gdzie ją zapisano: automat, archiwum,
  delta [D-025]. Własny, zwarty layout oznaczałby porzucenie wierności
  urzędowej — świadomie tego nie robimy.
- **[Verify] czego test nie sprawdził:** maszyna testowa ma biblioteki
  natywne, więc tryb awarii `dlopen` **nie został wywołany**. Pozostaje
  otwartym ryzykiem dla minimalnych obrazów kontenerów — adresuje je
  GH-4 (komenda `onboarding`).

## D-027 — PDF generuje oficjalny generator MF pod Node, z zwendorowanego bundla

- **Status:** Aktywna
- **Warsztat:** 001
- **supersedes:** D-026
- **Decyzja:** Wizualizację PDF generujemy **oficjalnym generatorem
  Ministerstwa Finansów** (`@akmf/ksef-fe-invoice-converter`, MIT),
  uruchamianym pod Node z **zwendorowanego, zbudowanego bundla**. Bez
  npm, bez kroku budowania, bez pobierania w czasie działania.
- **Potwierdzone wykonaniem (2026-09-13):** wygenerowano offline PDF
  o **tym samym rozmiarze i tej samej treści** co pobrany z portalu MF —
  36 743 bajty, ten sam layout, potwierdzone obejrzeniem. **Nie jest to
  zgodność bajt w bajt** — sumy SHA-256 się różnią, bo PDF niesie
  znacznik czasu utworzenia i identyfikator dokumentu. Wcześniejszy zapis
  „identyczny" był nadużyciem i został sprostowany.
  Cały koszt integracji to 3,1 MB bundla i ~60 linii shimu Node.
  Brakowało dokładnie **jednej** funkcji przeglądarki: `FileReader`.
  Żadnego canvas, żadnego DOM-u do renderowania — biblioteka niesie
  własne fonty (`configure-fonts.ts`, Roboto osadzony w bundlu).
- **Kontrakt:** `generateInvoice(file, { nrKSeF, qrCode }, 'blob')` —
  wejściem plik XML, wyjściem bajty PDF. Link weryfikacyjny KOD I
  składamy sami z NIP-u, daty wystawienia i skrótu pliku.
- **Dlaczego nie `ksef2` + WeasyPrint (poprzednia decyzja):** porównanie
  po wygenerowaniu obu wyników z tej samej faktury:

  | | Generator MF (Node) | `ksef2` (XSLT + WeasyPrint) |
  |---|---|---|
  | Wygląd | 2 strony, czytelny layout | **4 strony zrzutu schematu** |
  | Schematy | FA(1), FA(2), FA(3), UPO, PEF | **tylko FA(3)** |
  | Zależności systemowe | **żadne** | `libpango`, `libharfbuzz`, `libfontconfig` |
  | Tryb awarii | brak Node → czytelny komunikat | `dlopen` przy pierwszym renderze |

- **Obalone założenie:** „czysty Python" po stronie `ksef2` był pozorny —
  WeasyPrint i tak wymaga natywnych bibliotek systemowych, a Node to
  jeden plik binarny. Droga przez Node ma **mniej** zależności
  systemowych, nie więcej. To był błąd w ocenie kosztu, nie w danych:
  oba fakty były znane, ale nie zostały zestawione.
- **Skąd bundel:** portal MF serwuje zbudowany artefakt publicznie pod
  `/client-app/pdf-lib/ksef-fe-invoice-converter.<wersja>.js`. Licencja
  MIT pozwala go zwendorować — z zachowaniem noty licencyjnej. Wendorujemy
  zamiast pobierać w czasie działania, żeby nie zależeć od kanału bez
  kontraktu i żeby render działał offline.
- **Konsekwencja dla `onboarding` (GH-4):** kontrola zależności
  systemowych zmienia przedmiot — sprawdzamy **obecność Node**, a nie
  bibliotek natywnych. Tryb awarii jest przy tym łagodniejszy: brak Node
  wykrywamy zawczasu, zamiast pękać na `dlopen` w trakcie pracy.
- **Koszt po stronie użytkownika końcowego — Node.** `uvx` nie
  zainstaluje Node, a pakiet pythonowy nie może go wciągnąć jako
  zależności. Renderowanie PDF zadziała **wyłącznie u kogoś, kto ma
  Node**. Tryb awarii nie znika więc, tylko zmienia postać — ale zmienia
  ją na lepszą: brak Node wykrywamy jednym `node --version` **przed**
  użyciem zamiast pękać na `dlopen` w środku renderu; Node to jedna,
  znana instalacja zamiast zestawu bibliotek różniących się między
  dystrybucjami; a bez Node narzędzie **nadal działa** i traci wyłącznie
  PDF, zachowując XML, CSV i listę. Potwierdzone działanie na Node 22.17;
  generator deklaruje 22.14.0.
- **Ustalenia pakietowe:**
  - Bundel ląduje w `src/ksef_mcp/vendor/`, pod nazwą niosącą wersję
    generatora — trafia do wheela domyślną ścieżką hatchlinga, bez
    `force-include`.
  - `pre-commit` ma hook `check-added-large-files` z limitem 500 kB;
    dodajemy **wąski `exclude` na ścieżkę bundla**, nie podnosimy limitu
    globalnie i nie trzymamy bundla poza gitem. Pobieranie przy buildzie
    odpadło, bo wprowadza zależność sieciową i łamie determinizm.
  - Wheel rośnie do ~3,2 MB — przyjęte świadomie, w zamian za render
    offline bez kroku budowania.
  - **Nota licencyjna MIT** jako osobny plik obok bundla, z jawnym
    wskazaniem, że dotyczy wyłącznie tego artefaktu, nie reszty projektu
    (AGPL-3.0-only). Musi jechać w wheelu, nie tylko w repo.
### Polityka aktualizacji zwendorowanego bundla

- **Kiedy sprawdzamy:** **ręcznie, jako krok listy kontrolnej przed
  wydaniem**. Bez automatu, bez zadania cyklicznego.
- **Dlaczego nie automat:** sprawdzanie wymagałoby odpytywania portalu,
  czyli kanału **bez kontraktu** — tego samego, który odrzuciliśmy jako
  źródło PDF-ów [D-013]. Dokładanie mechanizmu, który sam w sobie jest
  kruchy, żeby pilnować artefaktu, który działa, jest złym rachunkiem.
  Stary bundel nie przestaje renderować, gdy MF wyda nowy.
- **Jak wykrywamy zmianę:** nazwa pliku niesie wersję
  (`ksef-fe-invoice-converter.<wersja>.js`), a **stopka wygenerowanego
  PDF-a też** („ksef-pdf-generator - wersja 1.1.39"). Podmiana jest więc
  widoczna w wyniku, nie tylko w repozytorium — i nie da się jej
  przeprowadzić niepostrzeżenie.
- **Jak weryfikujemy po podmianie:** **test porównujący z portalem** —
  wygenerować PDF ze znanej faktury i porównać rozmiar oraz treść z tym,
  co daje portal MF. Dokładnie ta procedura potwierdziła pierwszą wersję.
  Wymaga faktury testowej w repozytorium albo przebiegu pod markerem
  `ksef_live`.
- **Znane ograniczenie tego testu:** sprawdza **zgodność**, nie
  **jakość**. Gdy MF zmieni layout, test przejdzie — bo porównuje z
  portalem, który też się zmieni. Wariant renderowany przez `ksef2`
  odrzucono właśnie dlatego, że *wyglądał* źle, a żaden test rozmiaru by
  tego nie złapał [D-027]. Przy zmianie wersji **majora** warto obejrzeć
  wynik, nie tylko go porównać.
- **Nota licencyjna MIT** aktualizuje się razem z bundlem.

## D-028 — Odbiorcą etapu 1 jest użytkownik techniczny; księgowa to etap późniejszy

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Model dystrybucji `uvx` zostaje [D-015]. Zakładamy, że
  pierwsze uruchomienie wykonuje **osoba techniczna** — np. informatyk w
  biurze rachunkowym, który stawia narzędzie raz dla księgowych.
  „Księgowa ogarnie" dotyczy **używania**, nie instalacji.
- **Uzasadnienie:** łańcuch instalacji pęka wcześniej niż przy Node.
  `uv` na Windowsie wymaga polecenia w PowerShellu albo `winget`, a
  następnie trzeba wpisać serwer do pliku konfiguracyjnego MCP w JSON-ie.
  Dla osoby, która wg badania person „odpuszcza w 15 minut", to trzy
  bariery przed pierwszym uruchomieniem, nie jedna. Udawanie, że `uvx`
  jest dla księgowej, byłoby projektowaniem pod fikcyjnego użytkownika.
- **Szew na przyszłość (zaprojektowany, nie zbudowany):** dystrybucja dla
  odbiorcy nietechnicznego to osobny etap — instalator Windows albo
  rozszerzenie Claude Desktop. Nic z tego nie budujemy teraz; zapisujemy,
  żeby wybory etapu 1 nie zamknęły tej drogi.

### Format `.mcpb` — zweryfikowany u źródła (2026-09-13)

Wcześniejsze `[Verify]` zdjęte. Format istnieje i robi to, co zakładano:
archiwum zip z `manifest.json`, instalacja jednym kliknięciem
(dwuklik, przeciągnięcie do okna albo Ustawienia → Rozszerzenia),
transport stdio, działa offline, pakuje zależności.

**Cztery ustalenia, których nie znaliśmy — dwa zmieniają rachunek:**

1. **Node.js ships razem z Claude Desktop** na macOS i Windows.
   Dokumentacja mówi wprost: „users need no separate runtime", a Node jest
   **językiem zalecanym**.
2. **Python nie jest dostarczany.** Serwer pythonowy musiałby go
   zapakować albo wymagać.
3. **Brak Linuksa.** Obsługiwane wyłącznie `darwin` i `win32`.
4. **`user_config` w manifeście generuje interfejs ustawień** automatycznie,
   wraz z obsługą danych wrażliwych.

**Odwrócenie kosztów, które warto zobaczyć teraz, a nie za rok.** Dla
ścieżki `uvx` Python jest darmowy, a Node jest ciężarem [D-029]. Dla
ścieżki `.mcpb` jest **dokładnie odwrotnie**: Node dostajemy za darmo —
czyli ten sam runtime, którego potrzebuje generator PDF [D-027] — a
ciężarem staje się Python.

**Konsekwencje dla szwu:**
- Ścieżka `.mcpb` **usuwa** problem, którym jest dziś zależność od Node.
- Wprowadza natomiast **dwa nowe**: spakowanie Pythona i brak Linuksa.
- `user_config` mógłby zastąpić znaczną część komendy `onboarding` — ale
  tylko na tej ścieżce, więc komenda i tak musi istnieć dla `uvx`.
- Decyzja nie jest dziś potrzebna. Zapisane, bo **rachunek jest odwrotny,
  niż podpowiada intuicja z etapu 1**, i ktoś mógłby go źle przyjąć.

## D-029 — Node instalowany natywnie, nie przez koło pythonowe

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Node pozostaje zależnością środowiska użytkownika,
  instalowaną **narzędziami natywnymi dla ekosystemu Node** (Volta, fnm,
  instalacja systemowa). **Nie** wciągamy go jako zależności pythonowej.
- **Rozważona i odrzucona alternatywa:** `nodejs-wheel-binaries` —
  binarki Node spakowane w koło pythonowe, z kołami dla `win_amd64`,
  macOS (x86_64 i arm64) oraz Linuksa (glibc i musl). **Zweryfikowane
  wykonaniem:** `uv run --with nodejs-wheel-binaries node --version`
  zwraca `v22.17.0`, a uruchomiony tak renderer wygenerował fakturę o tym
  samym rozmiarze co pozostałe dwie drogi. Technicznie działa i usuwałoby
  Node z listy warunków wstępnych — odrzucone decyzją właściciela
  produktu na rzecz rozwiązania natywnego dla ekosystemu Node.
- **Wybrane narzędzie: `fnm`.** Jeden binarny plik, najszybszy z
  menedżerów wersji Node, instalowalny na Windowsie przez `winget`
  (`Schniz.fnm`). Wersję przypinamy plikiem **`.node-version` w repo** z
  wartością `22.17.0` — tą, na której render został zweryfikowany
  (generator MF deklaruje minimum 22.14.0).
- **Kiedy plik `.node-version` wchodzi — decyzja pierwotna:** nie osobno,
  tylko **tym PR-em, który wenduje bundel** — pin i jego konsument lądują
  razem. Pin bez konsumenta jest deklaracją wyprzedzającą potrzebę;
  README opisuje wymaganie i pułapkę z `fnm env` już teraz, więc wiedza
  nie ginie. To zastosowanie reguły „foundation-ready, nie przedwcześnie
  zbudowane" do artefaktu konfiguracyjnego, nie tylko do kodu.
- **Odstępstwo przyjęte świadomie:** `.node-version` (22.17.0) dodano
  **przed** bundlem, decyzją właściciela produktu. Powód przeważający:
  README od dawna odsyłał do tego pliku, więc jego brak był **odwołaniem
  w próżnię** — instrukcją, której nie da się wykonać. Domknięcie
  istniejącego rozjazdu okazało się ważniejsze niż unikanie pinu bez
  konsumenta. Odnotowane, bo to odstępstwo od reguły powyżej, a nie jej
  zastosowanie.
- **Symetria, o którą chodzi:** `uv` pilnuje Pythona `3.13.14`, `fnm`
  pilnuje Node `22.17.0`, obie wersje są **zadeklarowane w repo**, nie w
  czyjejś głowie. Spójne z regułą „zawsze konkretna wersja, nigdy
  zakres".
- **Znany koszt `fnm`:** automatyczne przełączanie wymaga dopisania
  `fnm env` do profilu powłoki — inaczej `.node-version` jest tylko
  deklaracją, nie egzekucją. Dla użytkownika technicznego [D-028] to
  akceptowalne; `onboarding` (GH-4) ma to wykryć i powiedzieć wprost,
  zamiast pozwolić na cichy rozjazd wersji.
- **Rozważona alternatywa:** Volta — przypina wersję w `package.json` i
  przełącza przez shimy, bez integracji z powłoką. Odrzucona na rzecz
  lżejszego `fnm`.
- **Konsekwencja:** brak Node pozostaje realnym warunkiem wstępnym, więc
  wykrywanie jego obecności i wersji w `onboarding` (GH-4) jest tym
  ważniejsze, a tool PDF-owy musi degradować się czytelnie — bez Node
  narzędzie nadal oddaje XML, CSV i listę.

## D-030 — Krajobraz ekosystemu: co istnieje i czego świadomie nie duplikujemy

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Nie budujemy klienta API KSeF ani narzędzi do wystawiania i
  wysyłki faktur. Zajmujemy **oś przychodzącą**: synchronizacja,
  archiwum, delta.
- **Skan ekosystemu (PyPI JSON API, 2026-09-13):**

  *Klienty API:* `ksef2` 0.19.0 (**używamy**, [D-017]), `ksef-client`
  0.17.1 (odrzucony — brak retry), `ksef2.0-python` 1.1.3.post1
  (nieoceniony), `pyksef` 0.3.3 (tylko uwierzytelnianie), `ksef-py`
  0.0.1a1 (alfa, sprzed API v2), `ksef` 0.2.8 („NOT PRODUCTION READY"),
  `ksef-utils` 1.4 (martwy, 2024).

  *Serwery MCP:* `mcp-ksef-pl` 0.10.1 (**jedyny na PyPI**, wydany
  2026-09-12), `stacking-hq/ksef2-mcp` (poza PyPI),
  `olegtyshcneko/ksef-mcp` (poza PyPI).

- **Gdzie pokrywamy się realnie:** `mcp-ksef-pl` ma oś **wychodzącą** —
  generowanie i walidacja FA(3)/FA(2), `submit_invoice_to_ksef`,
  `get_ksef_invoice_status`, `search_ksef_invoices`, rodzina
  Peppol/EN16931. **Nie ma narzędzia pobierania treści faktury** —
  wyszukuje po metadanych, nie ściąga dokumentów. Ta sama luka w
  `ksef2-mcp`, który ma `services/invoice_downloads.py` napisane, ale
  **niepodpięte jako tool**.
- **Sygnał ostrzegawczy, nie tylko nisza:** trzy niezależne projekty
  zbudowały wysyłkę i **żaden** nie zbudował pobierania. Można to czytać
  jako wolną niszę albo jako powód, dla którego nikt tam nie poszedł.
  Rozstrzyga to test falsyfikujący z [D-025].
- **Powiązane ograniczenie MF:** API pobierania *„nie jest przeznaczone
  do obsługi bezpośrednich operacji użytkowników końcowych w czasie
  rzeczywistym"*. Serwer MCP z definicji obsługuje zapytania użytkownika
  przez agenta, więc nasza zgodność z tym wymogiem **stoi i upada na
  lokalnym archiwum** — bez niego budujemy dokładnie to, czego MF
  zabrania.

## D-031 — Synchronizacja przyrostowa wg kanonicznego wzorca MF

- **Status:** Aktywna
- **Warsztat:** 001
- **supersedes:** D-024; rewiduje D-005, D-008, D-022
- **Źródła:** `CIRFMF/ksef-api` — `pobieranie-faktur/hwm.md`,
  `pobieranie-faktur/przyrostowe-pobieranie-faktur.md`,
  `limity/limity-api.md`, `limity/limity.md`. Czytane w całości.

### Decyzja

Synchronizację opieramy na **eksporcie paczek** z **High Water Mark**,
w **scenariuszu „tylko do HWM"**.

### 1. Eksport paczek jest ścieżką podstawową, nie awaryjną

`POST /invoices/exports` — asynchroniczny, kolejkowany. MF wymienia
*„synchronizację wyłącznie poprzez pobieranie pojedynczych faktur, bez
wykorzystania eksportu paczek"* jako **niezalecaną implementację**.
Obala to D-024, w którym eksport wystawiono poza etap 1.

Twardy powód liczbowy: `GET /invoices/ksef/{ksefNumber}` ma limit
**64 żądania/h**. Ścieżka synchroniczna jest dopuszczalna wyłącznie
w profilach niskiego wolumenu.

### 2. `PermanentStorage` jest jedynym dopuszczalnym typem daty

Cytat: *„Dla przyrostowego pobierania faktur **konieczne** jest użycie
daty typu `PermanentStorage`… inne typy dat (jak `Issue` czy
`Invoicing`) mogą prowadzić do nieprzewidywalnych zachowań"*.

**Korekta modelu:** `DateType` **nie jest** wyborem użytkownika w
`Okres`, jak wcześniej zapisano. Przy synchronizacji jest przybity do
`PermanentStorage`. `Issue` i `Invoicing` mogą służyć wyłącznie do
zapytań na **lokalnej** bazie — co jest zgodne z wymogiem MF, by
operacje biznesowe działały lokalnie [D-030].

### 3. Scenariusz „tylko do HWM"

HWM to moment, do którego system gwarantuje, że wszystkie faktury są
trwale zapisane i **żadna nowa już się nie pojawi**. Wszystko ≤ HWM to
zbiór zamknięty i kompletny; wszystko > HWM jest potencjalnie niepełne.

Pobieramy od ostatniego punktu kontynuacji **do bieżącego HWM**. Dane są
definitywne, duplikatów minimum. Cena przyjęta świadomie: faktury z
przedziału `(HWM, Teraz]` pojawią się dopiero w kolejnym cyklu.

Odrzucono scenariusz „do Teraz" (świeższe dane kosztem powtarzania
zakresu i większego zużycia budżetu 20/h) oraz wariant mieszany.

### 4. Okna wyznacza KSeF, nie my

Zalecenie: **pomijać `DateRange.To`** — system zbuduje możliwie dużą,
spójną paczkę w granicach własnych limitów. Kontynuacja:

| Warunek | Początek kolejnego okna |
|---|---|
| `IsTruncated = true` | `LastPermanentStorageDate` |
| `IsTruncated = false` | `PermanentStorageHwmDate` |

Zakresy muszą **przylegać**, bez nakładania. **Rozwiązuje to pytanie o
maksymalne okno zapytania** — nie my je wybieramy, więc pytanie znika.

### 5. Iteracja po typach podmiotu

Eksport wymaga wskazania typu podmiotu, a **punkt kontynuacji jest
osobny dla każdego typu**. Firma może występować w różnych rolach na
różnych fakturach, więc kompletność daje dopiero iteracja.

Budżet 20 eksportów/h dzielimy między typy; zalecane ~4/h na typ.
`Podmiot 3` i `Podmiot upoważniony` występują rzadko — wystarczy raz na
dobę w oknie nocnym. Interwał cykliczny **nie krótszy niż 15 minut** na
typ podmiotu.

**Korekta D-008:** przybicie do `Subject2` pozostaje poprawne dla
faktur zakupowych, ale etap 1 i tak potrzebuje pętli po typach, jeśli
ma deklarować kompletność okresu.

### 6. Deduplikacja po numerze KSeF zostaje — wbrew mojemu wcześniejszemu twierdzeniu

**Sprostowanie:** zaraportowałem, że MF deduplikuje za nas i że D-005
dubluje mechanizm. To było błędne. Dokument HWM mówi wprost, że przy
powtarzaniu zakresu *„po stronie systemu lokalnego konieczna jest
deduplikacja (np. po numerze KSeF)"*. Plik `_metadata.json` w paczce
nie zastępuje deduplikacji — jest jej **wejściem**, bo niesie numery
KSeF wszystkich faktur w paczce. Od 27.10.2025 jest dołączany zawsze,
wcześniej wymagał nagłówka `X-KSeF-Feature: include-metadata`.

D-005 obowiązuje dalej, wraz z rozdzieleniem indeksu od treści.

### 7. Szyfrowanie wraca do zakresu etapu 1

Paczka to **zaszyfrowany ZIP dzielony na części**: pobranie części z
osobnych URL-i, deszyfrowanie **AES-256** kluczem i IV wygenerowanymi
przy inicjalizacji eksportu, złożenie strumienia, rozpakowanie.

**Konsekwencja:** [D-004] musi objąć **klucze**, nie tylko token.

### 8. Rzeczywiste limity da się odpytać

`GET /limits/context` zwraca obowiązujące limity dla bieżącego
kontekstu. Licznik budżetu [D-020] powinien z tego korzystać zamiast
zakładać wartości domyślne — zwłaszcza że MF dopuszcza indywidualne
podniesienie limitów na wniosek.

### 9. Limity kontekstu, wcześniej nieznane

Faktura ≤ **1 MB** bez załącznika, ≤ **3 MB** z załącznikiem. Maksimum
**10 000** faktur w sesji. Do 500 faktur w pojedynczym identyfikatorze
zbiorczym.

### 10. Jedna ścieżka, także dla niskiego wolumenu

MF dopuszcza ścieżkę synchroniczną dla profili niskiego wolumenu, a
etap 1 (jeden podmiot, faktury zakupowe) prawdopodobnie zmieściłby się
w limicie 64 pobrań na godzinę. **Mimo to budujemy wyłącznie eksport.**

Uzasadnienie: druga ścieżka oznaczałaby drugi mechanizm do napisania i
utrzymania oraz **próg przełączania**, który trzeba by stroić. Persona
księgowej nazwała wybór „interaktywna czy wsadowa" decyzją techniczną
niepotrzebnie spadającą na użytkownika — a próg przełączania to ta sama
decyzja, tylko przebrana za automat. Koszt jednej ścieżki: AES-256 i
polling od pierwszego dnia. Zysk: etap 1 i etap 3 działają tak samo.

### Co pozostaje otwarte

- Kształt lokalnego magazynu punktów kontynuacji per typ podmiotu —
  częściowo rozstrzygnięte w [D-032], doprecyzowanie razem z polityką
  retencji.

## D-032 — Lokalny magazyn rozdzielony wg XDG: cache osobno od trwałego stanu

- **Status:** Aktywna
- **Warsztat:** 001
- **Decyzja:** Magazyn dzielimy na dwa korzenie, zgodnie z konwencją
  katalogów użytkownika. Ścieżki wyznaczamy biblioteką `platformdirs`,
  nigdy nie sklejamy ich ręcznie — inaczej Windows i macOS dostaną
  ścieżki linuksowe.

### Katalog cache — wolno skasować

| System | Ścieżka |
|---|---|
| Linux | `$XDG_CACHE_HOME/ksef-mcp` lub `~/.cache/ksef-mcp` |
| macOS | `~/Library/Caches/ksef-mcp` |
| Windows | `%LOCALAPPDATA%\ksef-mcp\Cache` |

Trafia tu **wyłącznie to, co da się odtworzyć jednym zapytaniem**:
cache metadanych okresu [D-021] i wyniki zapytań. Utrata kosztuje
budżet, nie ciągłość.

### Katalog danych — trwały stan, nie kasować

| System | Ścieżka |
|---|---|
| Linux | `$XDG_DATA_HOME/ksef-mcp` lub `~/.local/share/ksef-mcp` |
| macOS | `~/Library/Application Support/ksef-mcp` |
| Windows | `%LOCALAPPDATA%\ksef-mcp` |

Trafia tu wszystko, czego utrata jest droga:

- **Punkty kontynuacji HWM per typ podmiotu** [D-031] — ich utrata
  wymusza **pełną resynchronizację**, a budżet to 20 eksportów na
  godzinę dzielony między cztery typy podmiotu.
- **Indeks deduplikacji** (numery KSeF + skróty), odrębny od treści
  [D-005].
- **Archiwum XML** — to jest ta „lokalna baza danych", na której wg MF
  mają działać operacje biznesowe [D-030].

### Uzasadnienie rozdziału

Konwencja katalogu cache brzmi: *wolno skasować w dowolnym momencie*.
Czyszczarki systemowe i narzędzia porządkowe z tego korzystają. Gdyby
punkty kontynuacji leżały w cache, rutynowe sprzątanie dysku kasowałoby
ciągłość synchronizacji — a objaw (nagłe odpytywanie wszystkiego od
nowa i uderzanie w limity) byłby oddalony w czasie od przyczyny i
bardzo trudny do zdiagnozowania.

### Rozdział wobec wyników dla użytkownika

Ani cache, ani katalog danych **nie są** miejscem, gdzie lądują pliki
zamówione przez użytkownika. Te idą do **zadeklarowanego katalogu
roboczego**, osobnego per NIP, z `chmod 0700` i ostrzeżeniem przy
ścieżkach synchronizowanych do chmury. Magazyn jest wewnętrzny,
katalog roboczy jest produktem.

### Konsekwencje dla retencji i prywatności

Archiwum w katalogu danych zawiera dane osobowe kontrahentów i **nie
czyści się samo**. Potrzebna jest jawna polityka retencji i komenda
czyszcząca — bez nich po roku na dysku leży komplet faktur wszystkich
obsługiwanych podmiotów. Rozdzielenie indeksu od treści [D-005]
pozwala skasować treść bez utraty idempotencji.

### Otwarte

- Konkretna polityka retencji i jej domyślna wartość.
- Czy archiwum per podmiot ma być osobnym podkatalogiem w katalogu
  danych — wskazuje na to wymóg twardego rozdziału kontekstów w
  etapie 3 [D-009].

## D-033 — Klucz eksportu ma własny cykl życia, poza keyringiem

- **Status:** Aktywna
- **Warsztat:** 001
- **Doprecyzowuje:** D-031 §7, D-004
- **Decyzja:** Klucz AES-256 i IV eksportu zapisujemy **przy rekordzie
  oczekującego eksportu** w katalogu danych [D-032] i **kasujemy
  natychmiast** po odszyfrowaniu i zarchiwizowaniu paczki. **Nie trafiają
  do keyringu.**
- **Sprostowanie mojego wcześniejszego zapisu:** D-031 §7 stwierdza, że
  „[D-004] musi objąć klucze, nie tylko token". To było nieprecyzyjne.
  Klucze potrzebują **własnego cyklu życia**, a nie tego samego magazynu
  co token. [D-004] pozostaje decyzją o **tokenie**.
- **Dlaczego nie keyring:** keyring trzyma długowieczny sekret, którego
  utrata jest kosztowna (token wyświetla się jednorazowo). Klucz eksportu
  żyje minuty do godzin i po zarchiwizowaniu paczki jest bezwartościowy.
  Wrzucanie go do keyringu zapełniałoby magazyn wpisami, których nikt nie
  sprząta — a na headless [D-004] i tak schodzi na ścieżkę awaryjną.
- **Dlaczego trwały, a nie efemeryczny:** eksport jest **asynchroniczny i
  kolejkowany**, więc między inicjacją a pobraniem części mija czas, w
  którym serwer MCP pod `uvx` bywa ubijany razem z sesją agenta. Klucz w
  pamięci znika wtedy razem z procesem, a eksport staje się bezużyteczny
  — kosztem jednego z **20 eksportów na godzinę**.
- **Dlaczego to nie pogarsza bezpieczeństwa:** klucz nie jest wrażliwszy
  niż to, co chroni. Odszyfrowane faktury lądują w tym samym katalogu
  danych. Klucz na dysku obok archiwum nie otwiera niczego, co nie leży
  już obok w postaci jawnej.
- **Niezmiennik:** klucz nie przeżywa zakończonego eksportu. Kasowanie
  jest częścią operacji archiwizacji, nie osobnym sprzątaniem.

## D-034 — Archiwum bezterminowe, czyszczone jawną komendą

- **Status:** Aktywna
- **Warsztat:** 001
- **Domyka:** otwarty punkt z D-032
- **Decyzja:** Archiwum XML **nie wygasa samo**. Czyszczenie odbywa się
  **jawną komendą** użytkownika. Archiwum per podmiot mieszka w
  **osobnym podkatalogu** katalogu danych.
- **Uzasadnienie osobnych podkatalogów:** wymóg twardego rozdziału
  kontekstów w etapie 3 [D-009] oraz ostrzeżenie compliance, że wspólny
  katalog jest **głównym wektorem** pomieszania klientów biura
  rachunkowego — agent pobiera faktury klienta A, odpowiadając o
  kliencie B, a API nie zgłosi błędu, bo uprawnienie istnieje.
- **Uzasadnienie bezterminowości:** lokalne archiwum jest deklarowaną
  osią wartości produktu [D-025] i tym, czego MF wprost wymaga jako bazy
  dla operacji biznesowych [D-030]. Automatyczne wygasanie podkopywałoby
  jedno i drugie. Księgowa chce mieć historię pod ręką.
- **Przyjęte ryzyko, nazwane wprost:** compliance ostrzegał, że bez
  polityki po roku na laptopie leży komplet faktur wszystkich
  obsługiwanych podmiotów, wraz z danymi osobowymi kontrahentów.
  Świadomie wybieramy użyteczność kosztem minimalizacji danych —
  **komenda czyszcząca jest obowiązkowym elementem etapu 1**, nie
  dodatkiem, bo bez niej ta decyzja nie ma bezpiecznika.
- **Co to umożliwia:** rozdzielenie indeksu deduplikacji od treści
  [D-005] pozwala skasować faktury **nie tracąc idempotencji** — po
  wyczyszczeniu archiwum ponowna synchronizacja nie ściągnie ich
  powtórnie, bo indeks pamięta numery KSeF.
- **Zakres komendy czyszczącej — rozstrzygnięty:** **po obu wymiarach**,
  per podmiot oraz per okres, z możliwością połączenia. Oba przypadki są
  realne: biuro tnie po kliencie, który odszedł; jednoosobowa firma tnie
  po roku podatkowym. Wykluczenie któregokolwiek byłoby zgadywaniem.
  Etap 1 ma jeden podmiot, więc wymiar podmiotowy wygląda dziś na zbędny
  — ale archiwum leży już w **osobnych podkatalogach per podmiot**
  [D-032], więc wymiar istnieje w danych niezależnie od tego, czy komenda
  go wystawia. Dołożenie go później byłoby rozszerzeniem interfejsu, nie
  danych.

## D-015 — Nazwa dystrybucji `ksef-mcp` na PyPI

- **Status:** **Aktywna.** Potwierdzona i doprecyzowana przez [D-035] po
  przejściowym odstępstwie — patrz tam.
- **Warsztat:** 001
- **Decyzja:** Publikujemy jako `ksef-mcp`.
- **Kontekst:** [Verify] nazwa jest wolna na PyPI; repozytorium to
  `Dev10x-Guru/ksef-mcp`. Inny projekt (`olegtyshcneko/ksef-mcp`) używa tej
  nazwy lokalnie, ale nigdy jej nie opublikował — warto zająć ją świadomie.

## D-035 — Sześć powierzchni nazewniczych; wszystkie zbiegają się do `ksef-mcp`

- **Status:** Aktywna
- **Potwierdza:** D-015 — **nie zastępuje**, patrz „Odstępstwo" niżej
- **Decyzja:** Projekt ma **sześć odrębnych powierzchni nazewniczych**.
  Wszystkie noszą dziś nazwę `ksef-mcp` — i **mimo to pozostają
  rozdzielone**, bo rządzą nimi różne ograniczenia i mogą się kiedyś
  rozjechać.

| Powierzchnia | Wartość | Źródło |
|---|---|---|
| Repozytorium | `ksef-mcp` | GitHub |
| Dystrybucja PyPI | `ksef-mcp` | `pyproject.toml` `name` |
| Skrypt konsolowy | `ksef-mcp` | `[project.scripts]` → `ksef_mcp.cli:main` |
| Pakiet importu | `ksef_mcp` | `src/ksef_mcp/` |
| Nazwa serwera MCP | `ksef-mcp` | `metadata.SERVER_NAME` |
| Usługa w keyringu | importowany `SERVER_NAME` | [D-004] |
| Katalogi XDG | `ksef-mcp` | [D-032] |

### Dlaczego rozdzielone, skoro równe

- **`DISTRIBUTION_NAME` ≠ `SERVER_NAME` mimo identycznej wartości.**
  `importlib.metadata.version()` rozstrzyga po nazwie **dystrybucji**,
  więc sklejenie obu stałych w jedną jest bombą z opóźnionym zapłonem:
  wybuchnie `PackageNotFoundError` przy imporcie, gdy tylko któraś nazwa
  się zmieni. Rozdział kosztuje jedną stałą i dokumentuje rozróżnienie,
  które dziś jest niewidoczne.
- Trzy różne presje na tę samą literę: **dystrybucja** potrzebuje
  unikalności w rejestrze pakietów, **nazwa serwera MCP** jest
  tożsamością protokołu, na której opierają się konfiguracje klientów, a
  **katalogi i keyring** to nazwy produktu widziane przez użytkownika.
  Dlatego trzy osobne miejsca, a nie jedno.
- **Zgodność nazwy skryptu z dystrybucją jest celowa** — dzięki niej
  `uvx ksef-mcp` działa bez przełącznika `--from`.
- Punkt wejścia wskazuje `cli:main`, nie `server:main`. `ksef-mcp` bez
  argumentów nadal uruchamia serwer na stdio, więc konfiguracje klientów
  MCP się nie zmieniają — parser był potrzebny dla `onboarding` i operacji
  na tokenie.

### Niezmiennik: usługa w keyringu nie idzie za nazwą dystrybucji

Nazwą usługi jest **importowany `SERVER_NAME`, nigdy literał**. Zapisane
przy samym wywołaniu w kodzie, nie tylko w dokumentacji — żeby przetrwało
moment, w którym obie wartości znów się rozjadą.

Ryzyko jest **realne, nie teoretyczne**: token zapisany podczas przebiegu
na sucho leży pod `service=ksef-mcp`. Zmiana bez migracji dałaby objaw
„token zniknął", przed którym ostrzega [D-004]. Gdyby nazwa kiedykolwiek
musiała się zmienić, potrzebna jest migracja czytająca starą i zapisująca
nową.

Ta sama ostrożność dotyczy katalogów XDG [D-032] — przemianowanie
osieroci archiwum i punkty kontynuacji HWM, a ich utrata wymusza pełną
resynchronizację z budżetu 20 eksportów na godzinę.

### Odstępstwo i powrót — zapis dla czytającego za pół roku

Między GH-13 a GH-20 dystrybucja nosiła przejściowo nazwę
`ksef-dev10x-guru`. **Nie była to decyzja zastępująca D-015** — była to
zmiana wprowadzona w implementacji **wbrew decyzji o statusie Aktywna**,
na podstawie błędnego przekonania, że `ksef-mcp` jest zajęte na PyPI.
Sprawdzenie wykazało 404 dla obu nazw; sama treść GH-13 zresztą to
odnotowywała.

Powrót nie jest więc trzecią decyzją, tylko **przywróceniem D-015**. Bez
tego zapisu sekwencja czyta się jako dwie sprzeczne decyzje zamiast
jednej decyzji i jednego odstępstwa.

**Wniosek procesowy, ważniejszy od samej nazwy.** Model i implementacja
żyją w osobnych sesjach i **rozjeżdżają się w godzinach, nie w
tygodniach**. Rozjazd idzie w **obie strony** i oba kierunki kosztują:

- **Implementacja wyprzedza model** — nazwę zmieniono, nie sprawdziwszy
  `decisions.md`, wbrew decyzji o statusie Aktywna. Dokumentacja domenowa
  została scalona w tej samej godzinie, w której `main` dostał tę zmianę.
- **Dokumentacja wyprzedza kod** — w jednym repozytorium znalazły się
  **trzy odwołania w próżnię**: README opisywał pułapkę `fnm env` i
  odsyłał do `.node-version`, którego nie było; `CLAUDE.md` wymagał
  markera `ksef_live` i odsyłał do konfiguracji w `pyproject.toml`,
  której tam nie było; GH-4 powoływało się na decyzje niedostępne dla
  nikogo poza sesją, która je pisała.

Pierwszy kierunek daje sprzeczność, drugi — instrukcje, których nie da
się wykonać. Oba są niewidoczne dla autora, bo każdy widzi tylko swoją
stronę.

Jedyne, co temu zapobiegło, to **pytanie zadane właścicielowi produktu**
i **sprawdzenie stanu przeciw remote'owi zamiast przeciw dyskowi** — nie
żaden mechanizm.

### Bez zmian

Odwołania do `olegtyshcneko/ksef-mcp` w [D-030] dotyczą cudzego projektu.

## D-036 — Dwa rejestry decyzji: co gdzie mieszka

- **Status:** Aktywna
- **Decyzja:** Repozytorium prowadzi **dwa rejestry** i mają rozłączne
  zakresy:

| Rejestr | Trzyma | Przykład |
|---|---|---|
| `docs/domain/decisions.md` | decyzje **produktowe i dziedzinowe** | jak nazywa się pakiet [D-035], co wchodzi do MVP [D-001], którego klienta używamy [D-017] |
| `docs/adr/` | decyzje **o strukturze kodu** | dlaczego dwie stałe zamiast jednej i jaki wzorzec awarii to wyklucza |

- **Reguła rozstrzygająca:** pytaj, **czy decyzja przetrwałaby przepisanie
  implementacji od zera**. Jeśli tak — jest dziedzinowa i idzie do
  `decisions.md`. Jeśli znika razem z konkretnym kształtem kodu — jest
  architektoniczna i idzie do `docs/adr/`.
- **Warunek niepowielania:** każdy wpis **odsyła** do swojego
  odpowiednika w drugim rejestrze, nigdy go nie **streszcza**. Streszczenie
  starzeje się niezależnie od oryginału i to właśnie z niego biorą się dwa
  rejestry z dwiema teoriami.
- **Przypadek graniczny, na którym to ustalono:** nazwa pakietu jest
  decyzją produktową [D-035]; rozdział `SERVER_NAME` od
  `DISTRIBUTION_NAME` — wraz z uzasadnieniem, że
  `importlib.metadata.version()` rozstrzyga po nazwie dystrybucji — jest
  decyzją o strukturze i należy do ADR-a. To nie jest powielenie, dopóki
  każdy odsyła do drugiego.
- **Powód istnienia tej decyzji:** propozycja ADR-101 powstała w tym samym
  dniu co [D-035] i opisywała to samo rozróżnienie. Bez rozstrzygnięcia
  zakresów oba rejestry zaczęłyby rosnąć równolegle, a czytelnik nie
  wiedziałby, który jest wiążący.

## D-037 — Rejestr rozjazdów: gdzie wykonanie rozminęło się z decyzją

- **Status:** Aktywna
- **Decyzja:** Rozjazd między decyzją a tym, co pokazał kod albo SDK,
  **zostaje odnotowany tutaj**, a nie tylko w komentarzu pod zgłoszeniem.
  Decyzja sama nie jest przepisywana — czytelnik ma widzieć, co
  postanowiono, oraz co z tego wyszło w zderzeniu z rzeczywistością.
- **Powód:** ustalenia z nocnej zmiany 2026-09-13/14 miały trwały zapis
  rozsiany po sześciu zgłoszeniach i czterech ADR-ach. Osoba wracająca do
  modelu odkrywała te same rozjazdy po raz drugi, bo nie było miejsca,
  w którym są razem [#70].

### Rozjazdy wobec decyzji

| Decyzja | Co pokazało wykonanie | Zapis |
|---|---|---|
| [D-031] §8 wskazuje `GET /limits/context` jako źródło limitów | Ten endpoint zwraca rozmiary sesji; limity tempa są pod `GET /rate-limits`. Port czyta oba. | ADR-102, #35 |
| [D-031] §8 zakłada, że odpowiedź o limitach da się sparsować | Produkcja zwraca `/v2/rate-limits` bez pola `collectiveIdentifier`, którego model `ksef2` wymaga. Port degraduje się do wartości zachowawczych zamiast przerywać operację. | #76 |
| [D-031] §4 zaleca pomijać `DateRange.To` | `ksef2` nie pozwala; port wysyła „teraz" z `restrict_to_permanent_storage_hwm_date=True` | ADR-102 |
| [D-006] `temp → rename` jako jedyny wzorzec zapisu | Dziennik audytu jest tylko-dopisywany (`O_APPEND` + `fsync`) — świadome odstępstwo | docstring `audit.py`, PR #66 |
| [D-012] zabrania odsyłaczy weryfikacyjnych poza maszynę; #41 prosi o „link weryfikacyjny KOD I" | Zaimplementowany sam kod, bez adresu portalu | #41, PR #62 |
| [D-027] nazywa pakiet `@akmf/ksef-fe-invoice-converter` | Nazwa modułu w bundlu, nie współrzędna w npm. Źródłem jest portal MF: `/client-app/pdf-lib/ksef-fe-invoice-converter.<wersja>.js`. Wcześniejszy zapis o „404 w npm" opierał się na złej przesłance. | #42 |
| #57 zakłada liczenie pobrań części w budżecie | Nie liczone w żadnej rodzinie — adresy presigned nie niosą poświadczenia KSeF | #57, ADR-104 |

### [Verify] Do potwierdzenia na środowisku testowym

- Zapis skrótów części paczki: base64 czy hex — kod akceptuje oba [#37].
- Schema `_metadata.json`: nazwy kluczy — kod czyta kilka pisowni [#38].
- M2–M4 mają przebieg end-to-end wyłącznie na atrapach. Pierwszy przebieg
  produkcyjny (2026-09-14) wywrócił się na odczycie limitów [#76], czego
  żaden test na atrapach nie mógł pokazać — atrapy zwracały payload
  zgodny ze schematem SDK, a produkcja zwraca inny.
- `verify` **nie** przechodzi przez odczyt limitów, więc jego zielony wynik
  nigdy nie dowodził, że narzędzia MCP działają. Osobna luka w pokryciu,
  nie pojedynczy błąd [#76].

### Do rozstrzygnięcia przez właściciela produktu

- ~~#63 — waluta w CSV~~ — rozstrzygnięte 2026-09-14: kolumna „Waluta"
  obok kwot, które opisuje.
- ~~#75 — konflikt nazwy z `ksef-mcp.pl`~~ — rozstrzygnięte 2026-09-14:
  nazwa zostaje, odróżnienie idzie do dokumentacji [D-035].
- „Przejrzana" w #43 = zwrócona agentowi, nie zobaczona przez człowieka.
  Nadal otwarte.
