# Dziennik zmian

Format wzorowany na [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/);
wersjonowanie zgodne z [SemVer](https://semver.org/lang/pl/).

Sekcję `## Bez wydania` prowadzi człowiek. `bin/release.py` przenosi jej
treść pod nowy numer wersji i odmawia wydania, gdy sekcja jest pusta —
wydanie bez opisu zmian jest gorsze niż brak dziennika, bo wygląda na
udokumentowane.

## Bez wydania

### Zmiany łamiące

Powierzchnia (2) decyzji D-040 — pola modeli odpowiedzi. Wydanie
zawierające tę sekcję musi podnieść wersję major, nawet jeśli żadne
narzędzie MCP nie zmieniło nazwy ani sygnatury. Precedensem jest
GH-144, gdzie `subject_types`/`subject_type` ujednolicono bez okresu
przejściowego z tego samego powodu: trwały dualizm nazw byłby gorszy
niż jedna zmiana łamiąca.

- Wszystkie pięć narzędzi odpowiada tym samym czterem polom na
  szczycie: `nip`, `environment`, `message` i `warnings`. Dotąd pole
  ze zdaniem o statusie miało trzy odpowiedzi — `detail` przy roli
  podmiotu w synchronizacji, `message` w zestawieniu, przeglądzie i
  liście, a w odpowiedzi renderu nie było go wcale. Klientem tych
  narzędzi jest model językowy, który nie ma jak się domyślić, która
  nazwa akurat obowiązuje. Zmienia się nazwa `detail` na `message`,
  a odpowiedź `synchronise_invoices` — jedyna, która kazała
  wnioskować podmiot ze ścieżki katalogu — niesie teraz `nip`.
  Integrator czytający odpowiedzi po polu `detail` musi przestawić
  się na `message`; nic innego w kształcie pól nie ubyło (GH-171).

- Moduły trwałego stanu zebrano w pakiecie `ksef_mcp.storage`.
  Zmieniają się wyłącznie ścieżki importu — nazwa dystrybucji,
  skrypt konsolowy, `SERVER_NAME` i `DISTRIBUTION_NAME` zostają bez
  zmian, a żadne narzędzie MCP nie zmieniło nazwy ani sygnatury.
  Integrator importujący te moduły przestawia się tak (GH-129):

  - moduł `sync_store` przeniesiony z `ksef_mcp.sync_store` do
    `ksef_mcp.storage.sync_store`,
  - moduł `archive` przeniesiony z `ksef_mcp.archive` do
    `ksef_mcp.storage.archive`,
  - moduł `period_cache` przeniesiony z `ksef_mcp.period_cache` do
    `ksef_mcp.storage.period_cache`,
  - moduł `audit` przeniesiony z `ksef_mcp.audit` do
    `ksef_mcp.storage.audit`,
  - moduł `token_store` przeniesiony z `ksef_mcp.token_store` do
    `ksef_mcp.storage.token_store`,
  - moduł `storage` przeniesiony z `ksef_mcp.storage` do
    `ksef_mcp.storage.durability`. Ta jedna przeprowadzka zmienia
    nazwę modułu, a nie tylko jego miejsce: nazwa `ksef_mcp.storage`
    należy teraz do pakietu, a nazwa `durability` mówi, co ten moduł
    naprawdę trzyma — wyłączność zapisu i trwałość podmiany.

- Moduły kontekstu faktur zebrano w pakiecie `ksef_mcp.invoices`.
  Podział jest po kontekstach, nie warstwowy: przy około sześciu i pół
  tysiącach linii warstwy rozdzieliłyby pliki, które zmieniają się
  razem. Zmieniają się wyłącznie ścieżki importu (GH-130):

  - moduł `package` przeniesiony z `ksef_mcp.package` do
    `ksef_mcp.invoices.package`,
  - moduł `synchronisation` przeniesiony z `ksef_mcp.synchronisation`
    do `ksef_mcp.invoices.synchronisation`,
  - moduł `listing` przeniesiony z `ksef_mcp.listing` do
    `ksef_mcp.invoices.listing`,
  - moduł `review` przeniesiony z `ksef_mcp.review` do
    `ksef_mcp.invoices.review`,
  - moduł `statement` przeniesiony z `ksef_mcp.statement` do
    `ksef_mcp.invoices.statement`.

- Renderowanie PDF zebrano w pakiecie `ksef_mcp.rendering` razem z
  zasobami, które zużywa — shim `node/render.mjs` i zwendorowany
  generator Ministerstwa w `vendor/` idą tą samą przeprowadzką, więc
  manifest paczki (`[tool.hatch.build] artifacts`) i `license-files`
  nadal wskazują pliki, które istnieją (GH-131):

  - moduł `pdf` przeniesiony z `ksef_mcp.pdf` do
    `ksef_mcp.rendering.pdf`,
  - moduł `node_preflight` przeniesiony z `ksef_mcp.node_preflight` do
    `ksef_mcp.rendering.node_preflight`,
  - zasoby przeniesione z `src/ksef_mcp/node/` i `src/ksef_mcp/vendor/`
    do `src/ksef_mcp/rendering/node/` i
    `src/ksef_mcp/rendering/vendor/`. Kto pakuje własną dystrybucję
    albo sięga po notę licencyjną MF po ścieżce, przestawia się na
    nową.

- Konfigurację klienta zebrano w pakiecie `ksef_mcp.setup` — ustawienie
  środowiska to osobny kontekst od logiki faktur (GH-132):

  - moduł `skill` przeniesiony z `ksef_mcp.skill` do
    `ksef_mcp.setup.skill`,
  - moduł `client` przeniesiony z `ksef_mcp.client` do
    `ksef_mcp.setup.client`.

  Uzasadnienie całego podziału na pakiety opisuje
  [ADR-110](docs/adr/110-podzial-pakietu-po-kontekstach.md).

- Import samego pakietu `ksef_mcp` nie buduje już serwera MCP. Ta
  pozycja różni się od czterech powyżej: nic się nie przeniosło,
  ubyła natomiast fasada. `ksef_mcp/__init__.py` re-eksportował
  `server`, `main` i `ServerInfo`, a `server/app.py` tworzy instancję
  `MCPServer` i rejestruje narzędzia już na poziomie modułu — więc
  odczytanie numeru wersji przez `from ksef_mcp import VERSION`
  wciągało czternaście modułów i konstruowało serwer. Niweczyło to
  wysiłek pięciu miejsc odraczających import `ksef2` (GH-154).

  Wprost: `import ksef_mcp; ksef_mcp.main()` przestaje działać.
  Zamiast tego `from ksef_mcp.cli import main` albo skrypt konsolowy
  `ksef-mcp`. Na szczycie pakietu zostają wyłącznie `SERVER_NAME`
  i `VERSION`.

  Ścieżka `uvx ksef-mcp` nie jest dotknięta — skrypt konsolowy celuje
  w `ksef_mcp.cli:main`, nie w fasadę pakietu. Serwer i jego symbole
  pozostają dostępne pod `ksef_mcp.server`.

### Dodane

- Zestawienie okresu ostrzega, gdy w okresie jest faktura korygująca.
  Korekta niesie różnicę wobec faktury korygowanej, a nie tę fakturę na
  nowo — schemat Ministerstwa mówi to wprost przy polu `P_15` i
  dopuszcza tam wartość ujemną — więc suma całej kolumny Brutto była
  sumą dokumentów, nie zobowiązania, i wyglądała przy tym dokładnie tak
  samo jak suma poprawna. Ostrzeżenie nazywa liczbę korekt i ich
  numery, żeby nie trzeba było szukać ich wśród stu trzydziestu
  wierszy. Dokument o rodzaju, którego ta wersja nie zna, też jest
  zgłaszany — rodzajów KSeF-u przybywało i przybędzie, a milczenie o
  nierozpoznanym dokumencie czytałoby się jak „korekt tu nie ma"
  (GH-120).

- Nieudana synchronizacja zostawia po sobie ślad techniczny, więc nie
  trzeba już prosić o jej powtórzenie, żeby dowiedzieć się, co poszło
  nie tak. Dotąd jedyną odpowiedzią na „nie pobrało mi się" było
  uruchomienie wszystkiego jeszcze raz — czyli wydanie kolejnej porcji
  z dwudziestu eksportów na godzinę, które KSeF przyznaje. Dziennik
  idzie na standardowe wyjście błędów, a kto chce mieć go w pliku,
  ustawia `KSEF_DIAGNOSTIC_DIRECTORY`: plik powstaje wtedy w trybie
  `0600`, z rotacją, i nie niesie ani treści faktury, ani tokenu
  (GH-116).

- Odpowiedź `synchronise_invoices` niesie pole `correlation` —
  identyfikator tego jednego wywołania. Wszystkie żądania, jakie
  poszły przez nie do KSeF, są nim opatrzone w dzienniku, więc
  odtworzenie „co zrobiło to wywołanie" nie polega już na dopasowywaniu
  wpisów po znaczniku czasu — zawodnym dokładnie wtedy, gdy dwa
  przebiegi działają równolegle. Zgłaszając problem, wystarczy podać tę
  jedną wartość (GH-117).

- Odpowiedź `synchronise_invoices` mówi wprost, czy sufit sesji —
  rozmiar faktury, rozmiar z załącznikiem, liczba faktur na sesję —
  został przyznany przez KSeF, czy tylko założony. Serwer odczytywał te
  wartości przy każdej sesji i nie pokazywał ich nikomu, a gdy KSeF
  odpowiadał o limitach w kształcie, którego nie dało się odczytać,
  po cichu wchodził ostrożny zapas. Właśnie tego sygnału zabrakło przy
  diagnozie GH-76. Pole `session_ceilings.assumed` odpowiada na to
  pytanie jednym słowem (GH-118).

### Zmienione

- `bin/release.py` rozróżnia zaległość, brak pushu i rozjazd, zamiast
  nazywać rozjazdem każdą nierówność `main` i `origin/main`. Opiekun
  projektu wracający do wydania po przerwie czyta odtąd, że gałąź jest
  po prostu w tyle, i dostaje gotowe `git merge --ff-only origin/main`;
  gałąź do przodu prosi o `push`, a prawdziwy rozjazd zostaje odmową,
  bo tam wybór należy do człowieka. Skrypt niczego nie przewija sam —
  przewinięcie wciągnęłoby do wydania commity, których wydający nie
  oglądał, a tag i numer na PyPI są nieodwracalne (GH-189).

- Odmowa własnego licznika godzinowego ma własny wyjątek
  `BudgetExhausted` spod korzenia `KsefMcpError` i nie udaje już
  zdarzenia z portu KSeF. Integrator rozpoznający odmowy po typie
  wyjątku odróżnia odtąd „to serwer nie wysłał zapytania" od „to KSeF
  odmówił", zamiast szukać usterki w połączeniu, które działa.
  Komunikat mówi wprost, że próg jest lokalny, i podaje godzinę, o
  której okno się zwalnia — a przy pułapie zero mówi, że żadne
  czekanie go nie zwolni (GH-211).

- `ksef-mcp doctor` przegląda cały katalog `subjects/`, a nie tylko
  podmiot z bieżącej konfiguracji. Biuro rachunkowe, które onboardowało
  kolejnych klientów różnymi zapisami NIP-u, widzi odtąd każdy katalog
  zapisany starym sposobem wraz z nazwą katalogu docelowego i
  informacją, czy ten docelowy już istnieje. Nic nie jest przenoszone —
  to wciąż wykrycie, nie migracja faktur bez pytania (GH-210).

- Bieżący czas pochodzi odtąd z jednej funkcji `now_utc()` w module
  `ksef_mcp.clock`, a nie z dziewięciu identycznych kopii rozsianych
  po modułach. Zachowanie się nie zmienia — to wciąż ten sam
  `datetime.now(UTC)`. Zmienia się to, że test zamrażający czas ma
  jedno miejsce do podmiany, więc podmiana niewłaściwej kopii nie
  może już przejść niezauważona (GH-213).

- Adaptery wejścia — serwer MCP i wiersz poleceń — są odtąd pakietami
  `ksef_mcp.server` i `ksef_mcp.cli` zamiast dwóch plików po kilkaset
  linii. **To nie jest zmiana łamiąca i tym różni się od przeprowadzek
  opisanych wyżej w sekcji „Zmiany łamiące”.** Tamte (storage,
  invoices, rendering, setup) zmieniły ścieżki importu; te dwie nie
  zmieniają żadnej. `from ksef_mcp.server import server` działa dalej,
  bo `server/__init__.py` re-eksportuje `server`, `main` i
  `ServerInfo`, a skrypt konsolowy `ksef-mcp = "ksef_mcp.cli:main"`
  celuje w atrybut, który `cli/__init__.py` re-eksportuje tak samo jak
  wcześniej robił to moduł. `uvx ksef-mcp` nie jest w ogóle dotknięte.
  Dla integratora to zmiana wyłącznie wewnętrzna (GH-133, GH-134).

- Tłumaczenie raportów na wpisy dziennika audytu mieszka odtąd w
  modułach, które te raporty produkują — synchronizacja, lista,
  zestawienie, przegląd i render — zamiast w jednym pliku serwera.
  Dla podatnika nic się nie zmienia; zmienia się to, że druga
  powierzchnia dostarczania nie musi powtarzać przepływu audytowego
  ręcznie, tak jak musiało to robić `purge` (GH-135).

- Zapis do dziennika audytu przestał być czymś, o czym można
  zapomnieć. Ramka, która i tak opakowuje każde narzędzie MCP, wydaje
  teraz uchwyt do zapisu i odmawia wydania wyniku, jeśli narzędzie
  odpowiedziało bez wpisu. Wartość dziennika opiera się na jego
  kompletności (D-011), a dotąd nic jej nie pilnowało (GH-136).

- Wiersz poleceń rozdziela podkomendy jedną konwencją: każdy podparser
  niesie funkcję, która go obsługuje. Literówka w nazwie komendy
  kończy się odtąd błędem, a nie cichym uruchomieniem serwera MCP —
  co dla integratora debugującego skrypt wyglądało jak zawieszenie
  (GH-137).

- Obietnica, że przerwany zapis nigdy nie zostawia połowy pliku, jest
  odtąd własnością jednego miejsca w kodzie, a nie siedmiu kopii, które
  musiały się zgadzać. Dla podatnika nic się nie zmienia dziś — zmienia
  się to, że jutrzejsza poprawka trwałości zapisu trafi do wszystkich
  plików, jakie narzędzie prowadzi, a nie do sześciu z siedmiu (GH-145).

- To, jak narzędzie zachowuje się wobec pliku zapisanego przez starszą
  wersję, jest odtąd wyborem nazwanym przy każdym magazynie, a nie
  skutkiem tego, który fragment kodu skopiowano. Bez zmiany zachowania:
  zapisy, od których zależy rozliczenie podatnika — punkty kontynuacji,
  rejestr przeglądu, indeks archiwum — nadal odmawiają zgadywania, a
  podręczna pamięć okresów i liczniki limitów nadal traktują taki plik
  jak jego brak, bo odbudowują się same (GH-146).

- Odpowiedzi narzędzi `synchronise_invoices`, `list_recent_invoices`
  i `review_new_invoices` nazywają rolę podmiotu jednym słowem: pola
  `subject_types` i `subject_type` nazywają się teraz `subject_roles`
  i `subject_role`. To samo pojęcie miało dotąd cztery nazwy — dwie
  z nich w jednej linii kodu — a „kierunek" obiecywał dwie wartości,
  choć podmiot trzeci i podmiot upoważniony kierunkiem nie są. Agent
  czytający odpowiedź dostaje jedną nazwę zamiast czterech. Zapisy na
  dysku — punkty kontynuacji i podręczna pamięć okresów — zachowują
  dotychczasowy klucz, więc nic nie wymaga ponownej synchronizacji
  (GH-144).

### Bezpieczeństwo

- Komunikat odmowy przy archiwizacji paczki nie pokazuje już pełnego
  numeru KSeF. Numer zaczyna się od NIP-u podmiotu, dla którego fakturę
  wystawiono, więc dla faktur zakupowych był to NIP kontrahenta trafiający
  do kontekstu modelu językowego przy każdym wadliwym manifeście.
  Odmowa nazywa fakturę krótkim uchwytem `ksef:…`, który wystarczy, żeby
  odróżnić wpisy i rozpoznać ten sam wpis w kolejnym przebiegu. Pełny
  numer zostaje po stronie podatnika — w dzienniku technicznym, którego
  agent nie czyta. Dotyczy wszystkich odmów archiwizacji i synchronizacji
  cytujących numer, nie tylko tej jednej (GH-91, decyzja D-038).

- Wydruk faktury do PDF-a ostrzega o katalogu synchronizowanym z chmurą
  dokładnie tak, jak robi to zestawienie CSV. PDF faktury nazywa
  kontrahenta z imienia, nazwiska i adresu tak samo jak zestawienie, a
  ostrzeżenie o tym, że kopia trafi na cudzy serwer, powstawało i było
  wyrzucane, zanim ktokolwiek je zobaczył. Odpowiedź narzędzia niesie je
  teraz w polu `warnings` — razem z uwagą o katalogu szerszym niż `0700`
  (GH-172).

### Poprawione

- Onboarding mówi wreszcie, gdzie lądują faktury. Pytał o „katalog na
  pobrane faktury" i przy ścieżce synchronizowanej do chmury ostrzegał,
  że są w nim dane osobowe kontrahentów — tyle że XML-e nigdy tam nie
  trafiały. Wskazany katalog przyjmuje zestawienia i PDF-y, a faktury
  idą do archiwum w katalogu danych, o który nikt nie pyta (D-032).
  Podatnik oceniał więc kopię zapasową i prywatność po niewłaściwej
  ścieżce. Pytanie nazywa teraz katalog roboczy tym, czym jest, a zaraz
  po nim onboarding wypisuje ścieżkę archiwum, mówi, że to ją trzeba
  objąć kopią zapasową, i to jej dotyczy ostrzeżenie o synchronizacji
  (GH-188, decyzja D-041).

- Typ podmiotu, którego eksport KSeF zostawił w budowie, przestaje być
  zablokowany na zawsze. Dotąd taki wpis nie miał żadnego ograniczenia
  czasowego: punkt kontynuacji nie ruszał, dopóki paczka się nie
  domknęła, więc paczka, która nigdy się nie domykała, zatrzymywała ten
  typ podmiotu na stałe — a raport wyglądał zdrowo, bo „trwa" to przecież
  stan normalny. Zmieniają się trzy rzeczy. Eksport zamknięty przez KSeF
  bez ani jednej faktury jest odtąd rozpoznawany jako zakończony, a nie
  jako wciąż budowany, i punkt kontynuacji przechodzi przez puste okno
  zamiast o nie zawadzać. Eksport, który mimo to wisi dłużej niż dobę,
  jest porzucany, a to samo okno pytane od nowa — jeden eksport, dokładnie
  tyle, ile kosztuje odmowa KSeF. A dopóki eksport mieści się w tym progu,
  odpowiedź mówi, od kiedy trwa, więc paczka budowana od trzynastu godzin
  nie wygląda już tak samo jak poproszona przed chwilą (GH-190,
  decyzja D-039).

- Uszkodzony plik konfiguracyjny jest nazywany po imieniu, zamiast
  kończyć się śladem wyjątku. Przerwany zapis zostawia plik, który
  istnieje, więc nic nie uznaje serwera za nieskonfigurowany — a
  komendy `ksef-mcp` wywracały się wtedy na gołym błędzie parsera, z
  którego nie wynikało, że wystarczy przepisać jeden plik. Teraz każda
  komenda kończy się kodem wyjścia 9, podaje ścieżkę i przypomina o
  `ksef-mcp onboarding`. Serwer MCP mówił to już wcześniej; od tej
  poprawki oba wejścia mówią to samo (GH-119).

- README opisuje projekt taki, jaki jest dziś, a nie taki, jaki był na
  starcie. Tabela stanu i opis „tylko `server_info`" pochodziły sprzed
  pięciu wydań, które dobudowały synchronizację, listowanie, eksport
  CSV i przegląd nowych faktur — README tego nie odnotowywało, więc
  kto czytał tylko go, widział szkielet zamiast działającego narzędzia
  (GH-156).

- Generowanie PDF-a uruchamia dokładnie ten binarny plik Node, którego
  wersję sprawdziło uruchomienie wstępne, zamiast pozwalać systemowi
  rozwiązać literał `"node"` drugi raz. Pod `fnm` te dwie ścieżki mogły
  się rozjechać — sprawdzona wersja i uruchomiona wersja to bywały dwa
  różne pliki (GH-160).

- Komunikaty, które trafiają do klienta MCP albo do terminala, mówią
  teraz jednym językiem. Odmowa braku konfiguracji istniała w czterech
  różnie brzmiących kopiach, a dwa wyjątki odmawiające odczytu pliku,
  którego dana wersja nie rozumie (stan synchronizacji, rejestr
  przeglądu, indeks archiwum, dziennik audytu, konfiguracja),
  przemawiały po angielsku, mimo że trafiają do tego samego odbiorcy
  co ich polskojęzyczne odpowiedniki (GH-163, ADR-108).

- Synchronizacja sesji z dużą liczbą faktur nie przepisuje już całej
  historii archiwum przy każdej pojedynczej fakturze. Indeks rośnie przez
  całe życie archiwum i celowo nie jest przycinany, więc przy suficie
  10 000 faktur na sesję koszt narastał z każdą kolejną synchronizacją —
  a płaciło się go tak samo przy odczycie indeksu, jak przy zapisie
  (GH-147).
- Zestawienie miesięczne nie czyta już całego XML-a każdej faktury po to,
  by policzyć kod weryfikacyjny. Indeks archiwum trzyma ten skrót od
  chwili zapisu faktury, więc miesiąc z 400 fakturami to 400 pełnych
  odczytów mniej. Kod nadal opisuje bajty leżące na dysku: gdy indeks
  o fakturze nie wie albo jest nieczytelny, plik zostaje czytany tak jak
  dotąd (GH-148).
- Niedostępny KSeF ani odmowa uwierzytelnienia nie zostaną już po cichu
  zamienione na „nie ma tego w podręcznej pamięci". Pamięć okresów
  celowo traktuje uszkodzony wpis jako chybienie, bo da się go odtworzyć
  jednym zapytaniem — ale łapała przy tym korzeń obejmujący także brak
  połączenia i odrzucone logowanie, więc awaria wyglądała jak zwykłe
  chybienie i podatnik nie dowiadywał się o niej. Odrzucenie niepoprawnego
  numeru faktury albo niemożliwego okna jest teraz osobnym rodzajem błędu
  niż awaria połączenia (GH-170).
- Przerwany zapis konfiguracji nie zablokuje już startu. Plik
  `configuration.json` powstawał dotąd przez nadpisanie samego siebie —
  jako jedyny z siedmiu zapisów w pakiecie — więc brak miejsca na dysku
  albo zamknięta sesja zostawiały go obciętym, a serwer i wiersz poleceń
  odmawiały startu bez słowa wyjaśnienia; jedynym wyjściem było ręczne
  skasowanie pliku. Teraz zapis idzie przez plik przejściowy i podmianę,
  więc poprzednia konfiguracja zostaje nietknięta, a uszkodzony plik jest
  nazwany wprost, razem z poleceniem `ksef-mcp onboarding` (GH-168).
- Konfiguracja mówi, w jakim formacie została zapisana, więc przyszła
  zmiana pola zostanie rozpoznana jako zmiana formatu, a nie pomylona
  z uszkodzeniem pliku. Plik konfiguracji był jedynym z sześciu
  dokumentów trwałych bez tej informacji (GH-169).
- Po uśpieniu laptopa narzędzia MCP mówią, że kolekcja keyringu jest
  zamknięta i jak ją otworzyć, zamiast odpowiadać „Error executing tool".
  Kolekcja zamyka się sama, gdy maszyna zasypia, a token czytają cztery
  z pięciu narzędzi — zdanie z instrukcją wyjścia było napisane od dawna
  i pokazywał je wyłącznie wiersz poleceń. Serwer wymieniał odmowy
  z nazwiska w jednym miejscu i wyliczenie się zestarzało; teraz wszystkie
  wyjątki tej aplikacji mają wspólny korzeń, więc odmowa napisana dla
  człowieka dociera do niego bez dopisywania jej do żadnej listy
  (GH-167).
- Faktura, która nie zmieściła się w odpowiedzi KSeF, nie zniknie już na
  zawsze z przeglądu. Przegląd pyta o okno dziewięćdziesięciu dni, a KSeF
  oddaje naraz najwyżej dwieście pięćdziesiąt pozycji. Rejestr zapisywał
  jako pokazane to, co się zmieściło, więc przy kolejnym wywołaniu okno
  przesuwało się ponad resztą i te faktury nie wracały. Dopóki odpowiedź
  nie jest kompletem, nic nie idzie do rejestru: przegląd powtórzy te
  pozycje, zamiast po cichu je zgubić (GH-180).
- Miesiąc z setkami faktur wraca w całości, a nie w kawałku, który zmieścił
  się na jednej stronie. KSeF oddaje metadane po dwieście pięćdziesiąt
  pozycji i mówi, że ma więcej — narzędzie to raportowało i nie umiało o tę
  resztę poprosić. Teraz dociąga kolejne strony, płacąc za każdą z tego
  samego godzinowego przydziału, który liczy się między wywołaniami, i
  zatrzymuje się na rezerwie, żeby starczyło na pozostałe typy podmiotu.
  Gdy okno mimo to nie jest kompletem, odpowiedź mówi, czy zabrakło
  przydziału — wtedy warto ponowić za godzinę — czy uciął je sam KSeF.
  Niekompletnego okna narzędzie nie zapamiętuje, więc kolejne wywołanie
  dokończy je zamiast podać z dysku to, co zdążyło dojść (GH-182).
- Skrócone zestawienie miesięczne mówi o tym samo, a nie dopiero w przypisie.
  Produktem tego narzędzia jest liczba, którą człowiek wysyła księgowej, a
  suma policzona z części miesiąca wygląda dokładnie tak samo jak pełna.
  Ostrzeżenie trafiało wyłącznie do odpowiedzi narzędzia — do samej księgowej
  jechał plik CSV, po którym nic nie było widać. Teraz zdanie podające sumę
  zaczyna się od wyraźnego ostrzeżenia, a plik niekompletnego okresu nazywa
  się `...-NIEKOMPLETNE.csv`, więc widać to już w skrzynce pocztowej.
  Ostrzeżenie rozróżnia też brak godzinowego przydziału — wtedy warto ponowić
  za godzinę — od okresu uciętego przez sam KSeF (GH-181).

## 0.3.4 — 2026-09-19


### Bezpieczeństwo

- Paczka z PyPI nie zgubi po cichu generatora PDF. Pliki, których
  renderowanie potrzebuje — shim Node i zwendorowany generator
  Ministerstwa — jechały w dystrybucji tylko dlatego, że nikt nie wymienił
  ich w `.gitignore`. Dopisanie tam kiedykolwiek `*.js` albo `package.json`
  wypadłoby je z koła, a instalacja, import i wszystkie testy nadal by
  przeszły: awarię zobaczyłby dopiero podatnik przy pierwszej próbie
  wydrukowania faktury. Manifest wymienia teraz te pliki wprost, a test
  buduje paczkę i zagląda do archiwum, zamiast wierzyć manifestowi na
  słowo (GH-106).
- Wydania nie da się podmienić przez przejęcie cudzej akcji GitHub
  Actions. Wszystkie akcje we wszystkich przebiegach wskazują teraz
  konkretny commit, a nie ruchomy tag czy gałąź — łącznie z akcją
  publikującą, która działa z uprawnieniem do wydania paczki jako
  `ksef-mcp`. Ruchomy tag wygląda jak wersja, a jest wskaźnikiem, który
  właściciel akcji może przestawić w dowolnej chwili (GH-107).
- Integrator może maszynowo sprawdzić, skąd wzięła się paczka z PyPI.
  Wydanie niesie atestację pochodzenia wiążącą skrót każdego archiwum
  z tym repozytorium, tym przebiegiem i tym commitem. Zaufane wydawanie
  OIDC mówiło dotąd tylko, kto wgrał plik; teraz da się sprawdzić także,
  z czego on powstał (GH-110).
- Obcięty generator Ministerstwa nie dojedzie już do podatnika. Portal MF
  potrafi zerwać transfer w połowie, a niepełny plik wygląda jak pełny —
  nota `LICENCJA-MF.md` zapisywała jego skrót i rozmiar, ale nikt tych
  liczb nie przeliczał. Teraz przelicza je hook przed commitem i bramka
  w skrypcie wydania: rozjazd zatrzymuje pracę u nas, zamiast wychodzić
  u podatnika przy pierwszej próbie wydrukowania faktury. Nota zostaje
  źródłem prawdy, zmieniło się tylko to, że ktoś ją wreszcie sprawdza
  (GH-108).
- O podatności w bibliotece dowiadujemy się my, a nie podatnik. Co
  tydzień — i przy każdej zmianie przypięć — skanowane są wszystkie
  wersje z `uv.lock`, czyli dokładnie to, co instaluje się razem z
  paczką, a nie to, co akurat rozwiązałby świeży instalator. Osobno
  pilnowana jest aktualność samych przypięć: biblioteki, z których
  korzysta działający serwer, dostają każda własne zgłoszenie zmiany, a
  narzędzia deweloperskie jedno wspólne. Podniesienie `cryptography`,
  która strzeże kluczy do paczek z danymi osobowymi, ma być widoczne, a
  nie schowane w zbiorczej zmianie obok poprawki formatowania kodu
  (GH-109).
- `purge --nip` nie wyprowadzi już kasowania poza katalog podmiotu.
  Wartość tego parametru staje się członem ścieżki, a komenda kasuje
  pliki bezpowrotnie — zapis w rodzaju `../../..` przechodził dotąd bez
  sprawdzenia aż do planu czyszczenia. Jedyną ochroną było pytanie o
  potwierdzenie, a to chroni przed pomyłką, nie przed złym wejściem. NIP
  jest teraz czytany na wejściu i musi być dziesięcioma cyframi; wszystko
  inne kończy się odmową, zanim cokolwiek zostanie zaplanowane (GH-112).
- Token KSeF nie trafi do śladu stosu po nieudanym połączeniu.
  Poświadczenie było rozpakowywane do gołego napisu zaraz na wejściu i
  wędrowało tak przez siedem wywołań — czyli dokładnie przez ten
  fragment, w którym powstają wyjątki sieciowe, a razem z nimi ślady z
  wartościami zmiennych lokalnych. Teraz jest rozpakowywane linijkę
  przed użyciem, w jednym miejscu (GH-115).

### Zmienione

- Jeden NIP to jeden podatnik, niezależnie od zapisu. `123-456-32-18`,
  `123 456 32 18`, `PL1234563218` i `1234563218` to dla urzędu ten sam
  numer, a dla tego serwera bywały dwoma podmiotami: dwa katalogi z
  fakturami i dwa wpisy tokenu w keyringu, bez żadnego ostrzeżenia.
  Zapis jest teraz sprowadzany do samych cyfr przy każdym wejściu.

  **Jeśli podczas konfiguracji wpisałeś NIP z myślnikami albo ze
  spacjami**, Twoje dotychczasowe pliki leżą pod tamtym zapisem i ta
  wersja już tam nie zajrzy. Niczego nie przenosimy za Ciebie — w tych
  katalogach są faktury z danymi kontrahentów, a przekładanie ich bez
  pytania byłoby gorsze niż katalog, którego nie widać. `ksef-mcp
  doctor` wskaże taki katalog po nazwie; przeniesienie zawartości do
  katalogu obok, nazwanego samymi cyframi, wystarczy (GH-111).

- Dwa klienty MCP na jeden podmiot przestają gubić sobie nawzajem zapisy.
  Claude Desktop i Claude Code obok siebie to zwykła konfiguracja, a
  narzędzia MCP są synchroniczne, więc równoległy zapis zdarzał się nawet
  w jednym procesie. Każdy magazyn wczytywał cały dokument, zmieniał swój
  fragment i zapisywał całość, więc ten, kto kończył drugi, wymazywał
  zmianę pierwszego — razem z punktami kontynuacji i kluczami AES
  zamówionych paczek, których bez nich nie da się odszyfrować. Katalog
  podmiotu ma teraz jednego pisarza naraz; drugi dostaje jasną odmowę z
  nazwą zajętego katalogu, zamiast pisać w próżnię. Czekania nie ma
  celowo: zawieszone narzędzie to zawieszona sesja agenta, a dwie
  synchronizacje jednego podmiotu wydają przydział dwa razy (GH-101).
- Faktura nie zniknie z indeksu, bo drugi zapis zaczął od tej samej
  migawki. Sam plik faktury był chroniony od początku — nazwa pliku jest
  tożsamością i nic jej nie nadpisze — ale wpis w indeksie deduplikacji
  był tylko sprawdzany, nie wymuszany. Skutek widziała księgowa: faktura
  leżała na dysku, indeks o niej nie wiedział, więc następna
  synchronizacja pobierała ją ponownie i wydawała na to przydział
  (GH-103).
- Plik przejściowy nie da się już popsuć drugim pisarzem. Wszystkie
  magazyny używały tej samej nazwy roboczej, więc dwa równoczesne zapisy
  obcinały się nawzajem, zanim którykolwiek zdążył podmienić plik
  docelowy. Przy okazji znika okno, w którym dokument niosący klucze AES
  albo dane kontrahenta istniał przez moment z prawami dostępu
  odziedziczonymi po systemie, a nie z własnymi (GH-102).
- Dziennik audytowy nie przeplecie linii dwóch równoczesnych zapisów, a
  uszkodzoną linię nazwie po numerze zamiast wysypać się niezrozumiałym
  błędem. Ślad audytowy ma dowodzić, kto sięgnął po które faktury —
  nieczytelny dowodzi tylko własnej nieczytelności, i to dokładnie wtedy,
  gdy jest najbardziej potrzebny. Komunikat o uszkodzeniu nie cytuje
  treści linii, bo ta niesie numery KSeF (GH-104).
- Zamiana pliku przeżywa zanik zasilania, nie tylko zabicie procesu.
  Zapisywana była sama treść pliku, ale już nie wpis w katalogu, który
  nadaje jej nazwę — czyli ten krok, na którym opiera się cała ochrona
  przed połową dokumentu pod nazwą obiecującą całość (GH-105).
- Limit godzinowy jest liczony przez całą godzinę, a nie przez jedno
  wywołanie narzędzia. Serwer pod `uvx` ginie razem z sesją agenta, więc
  licznik zaczynał od zera przy każdym uruchomieniu: trzy narzędzia w
  ciągu minuty wydawały trzy pełne przydziały i nic nie odmawiało ani
  razu. Zużycie zapisuje się teraz na dysku, osobno dla każdego NIP-u i
  środowiska, obok stanu synchronizacji — nie w cache'u, który czyszczarka
  dysku ma prawo skasować. Egzekwowane są też limity sekundowy i minutowy,
  dotąd odczytywane z KSeF i nigdy niesprawdzane (GH-97).
- `ksef-mcp verify` przestaje po cichu wydawać limit, który ma pilnować.
  Polecenie diagnostyczne pytało KSeF z pominięciem licznika i cache'u, a
  uruchamia się je zwykle kilka razy pod rząd — właśnie wtedy, gdy coś już
  szwankuje. Teraz zapytanie jest liczone jak każde inne, a okno kończy się
  na pełnej godzinie, więc powtórzenie w tej samej godzinie nie kosztuje
  nic. Limity kontekstu, dwa żądania przy każdym otwarciu sesji i dotąd
  nieliczone, zapamiętywane są na godzinę (GH-98).
- Po serii odmów z KSeF-u serwer przestaje pytać sam z siebie. Limit
  chronił przed zbyt częstym pytaniem zakończonym powodzeniem; nic nie
  chroniło przed zbyt częstym pytaniem zakończonym odmową — a to właśnie
  ten wzorzec Ministerstwo Finansów analizuje jako próbę obchodzenia
  limitów i odpowiada na niego blokadą tym dłuższą, im częściej się
  powtarza. Po pięciu odmowach pod rząd serwer odmawia lokalnie i podaje
  moment, od którego wolno spróbować ponownie. Licznik leży na dysku, bo
  szkodę robi wytrwałość klienta, a nie pojedyncze wywołanie: zerowany
  przy każdym starcie pozwalałby wznawiać serię bez końca. Jedna udana
  odpowiedź kończy serię (GH-99).
- Polityka ponawiania jest wreszcie używana. Klasa umiejąca uszanować
  czas oczekiwania podany przez KSeF miała testy i żadnego wywołującego
  w kodzie produkcyjnym — testy przechodziły, a mechanizm nie działał.
  Ruch do KSeF-u nie zmienia się ani o jedno żądanie: domyślnie wciąż
  jedna próba, bez wycofania wykładniczego, bo zgadywanie czasu
  oczekiwania to wzorzec, za który blokada się wydłuża (GH-100).
- Odrzucony eksport przestaje blokować swój typ podmiotu. Gdy KSeF odmówił
  zbudowania paczki, jej wpis zostawał w kolejce roboczej na zawsze —
  a kolejka rozstrzyga, o co prosić dalej, więc ten typ podmiotu zamawiał
  nową paczkę co piętnaście minut i nie odbierał żadnej. Bez końca i bez
  śladu w wyniku. Odmowa idzie teraz do dziennika: odnośnik zostaje, żeby
  dało się go później wyjaśnić, ale nie udaje już pracy do dokończenia.
  Plik zapisany przez wcześniejszą wersję rozplątuje się sam przy
  pierwszym odczycie (GH-94).
- Odmowa limitem nie unieważnia odpowiedzi, które już kosztowały budżet.
  Wypisanie faktur i przegląd nowych pytają KSeF osobno o każdy z czterech
  typów podmiotu; odpowiedź 429 w połowie tej pętli przerywała całość,
  więc przepadały odpowiedzi opłacone z dwudziestu zapytań na godzinę.
  Teraz odmowę dostaje ten jeden typ podmiotu, reszta wyniku zostaje,
  a komunikat podaje czas oczekiwania, o ile KSeF sam go wskazał —
  nigdy zgadywany (GH-95).
- Klucz do paczki przyjętej przez KSeF nie ginie przez awarię przy innym
  typie podmiotu. Stan synchronizacji zapisywał się dopiero po obsłużeniu
  wszystkich czterech typów, więc błąd przy ostatnim zabierał klucz AES
  paczki zamówionej przy pierwszym — a takiej paczki nie da się odszyfrować
  już nigdy. Stan idzie na dysk po każdym typie podmiotu (GH-96).
## 0.3.3 — 2026-09-19


### Poprawione

- Synchronizacja wychodzi z zakleszczenia na wygasłej paczce. Odnośnik do
  części paczki wygasa na własnym zegarze, niezależnym od eksportu, a punkt
  kontynuacji zdążył się już przesunąć za okno, które ta paczka niosła —
  więc kolejne przebiegi ani jej nie pobierały, ani nie prosiły o ten
  zakres ponownie. Odmowa z wygasłym odnośnikiem odpytuje teraz KSeF
  o status eksportu, a gdy KSeF już go nie podaje, wpis zostaje zdjęty,
  punkt cofnięty przed utracone okno, i ten sam przebieg prosi o nie
  jeszcze raz (GH-93).
## 0.3.2 — 2026-09-19


### Poprawione

- Gotowa paczka trafia na dysk. Synchronizacja dociągała faktury, po czym
  archiwizacja odmawiała ich zapisu, bo szukała w manifeście nazwy pliku —
  a manifest KSeF jej nie zawiera i nigdy nie zawierał. Faktura jest teraz
  wiązana z numerem po skrócie własnej treści, czyli mocniej, niż wiązałaby
  ją nazwa (GH-87).
- Komunikat odmowy wymienia klucze, które w manifeście zastał. Dotąd mówił
  wyłącznie, czego nie znalazł, więc rozpoznanie rozbieżności wymagało
  odszyfrowania produkcyjnej paczki (GH-87).
- Odmowa generatora PDF nie niesie już fragmentów odrzucanej faktury.
  Generator Ministerstwa cytuje w komunikacie wartości odczytane z pliku,
  a komunikat od wersji 0.3.1 dociera do klienta (GH-85).
## 0.3.1 — 2026-09-18


### Poprawione

- Pierwsza synchronizacja nowego podmiotu dochodzi do skutku. Sięgała
  wstecz o sto dni, a KSeF odpowiada na okna do trzech miesięcy, więc
  każdy pierwszy przebieg wracał z błędem walidacji, archiwum zostawało
  puste, a wizualizacja PDF odmawiała potem każdego numeru. Pułap okna
  jest teraz limitem API, nie ograniczeniem biblioteki klienckiej, a
  podmiot, który nie synchronizował się dłużej, nadrabia kolejnymi
  przebiegami zamiast prosić o okno nie do odpowiedzenia (GH-84).
- `review_new_invoices` pytało o okno dziewięćdziesięciodniowe, czyli
  również ponad limit. Okno przeglądu trzyma się teraz tego samego
  pułapu (GH-84).
- `render_invoice_pdf` mówi, co poszło nie tak. Faktura spoza archiwum,
  odrzucony katalog roboczy, brak Node i odmowa generatora docierały do
  klienta jako gołe „Error executing tool" — mimo że opis narzędzia
  obiecywał każdy z tych komunikatów (GH-84).

### Uwaga o aktualizacji

- Pamięć podręczna okresów zaczyna się od nowa. Zapis okna zmienił
  format, więc wersja schematu poszła w górę i wpisy sprzed aktualizacji
  są pomijane. Skutek jest jednorazowy: pierwsze pytanie po aktualizacji
  pójdzie do KSeF zamiast trafić w pamięć, kosztem jednej operacji
  z dwudziestu na godzinę. Archiwum faktur i punkty kontynuacji
  synchronizacji pozostają nietknięte.
## 0.3.0 — 2026-09-14


Wydanie 0.2.0 sprowadzało faktury na dysk, ale zostawiało podatnika
z XML-em. To wydanie domyka drogę do dokumentu: faktura otwiera się
jako PDF wygenerowany oficjalnym modułem Ministerstwa Finansów,
bez sieci i bez wydawania godzinowego budżetu KSeF. Obok tego
narzędzie zaczyna działać na produkcji — odczyt limitów wywracał tam
wszystkie cztery narzędzia liczące budżet — a pierwsze uruchomienie
prowadzi od instalacji do pierwszego pytania, zamiast kończyć się
instrukcją do przepisania.

### Dodane

- Narzędzie `render_invoice_pdf` otwiera fakturę jako dokument, nie
  jako dane. Z badania person, dosłownie: „XML to dla mnie nie jest
  faktura". PDF powstaje oficjalnym generatorem Ministerstwa Finansów
  uruchamianym pod Node, wyłącznie z tego, co archiwum już trzyma:
  zero zapytań do KSeF, zero z dwudziestu na godzinę, działa bez
  sieci. Faktura niezsynchronizowana jest odmawiana, nie dociągana —
  inaczej odpowiedź zależałaby od budżetu, którego pytający nie widzi
  ([GH-42]).
- Brak Node to degradacja, nie awaria: komunikat mówi, co
  zainstalować, i wymienia to, co działa dalej — XML, CSV, listę.
  Wersja Node rozjechana z `.node-version` czytana jest wprost jako
  brak `fnm env` w profilu powłoki ([GH-42]).
- Generator jedzie w dystrybucji jako zwendorowany bundel spod portalu
  MF, z sumą SHA-256 zapisaną obok pliku i notą licencyjną MIT.
  Osobny test pilnuje tej sumy, żeby cicha podmiana bundla nie
  przeszła niezauważona ([GH-42]).
- Onboarding prowadzi podatnika od instalacji do pierwszego pytania:
  środowisko wybiera się numerem z listy z opisem różnicy między test
  a demo, a rejestracja serwera w kliencie MCP i instalacja skilla są
  proponowane na miejscu, zamiast zostawać jako cztery kroki do
  wykonania poza narzędziem ([GH-25], [GH-71], [GH-72], [GH-74]).
- Zestawienie CSV niesie kolumnę „Waluta" tuż za kwotami, które
  opisuje. Miesiąc z fakturą w euro obok złotówkowej dawał dotąd
  w pliku kwoty nie do odróżnienia — a to plik, nie odpowiedź
  narzędzia, księgowa dostaje mailem ([GH-63]).
- `doctor` wypisuje dystrybucję, wersję, ścieżkę wykonywalną, podmiot
  i środowisko. Przy dwóch skryptach o tej samej nazwie ścieżka jest
  jedyną rozstrzygającą odpowiedzią, a podmiot i środowisko to jedyne
  darmowe miejsce, gdzie da się je sprawdzić — `verify` wydaje na to
  wywołanie do KSeF ([GH-75]).
- D-037 zbiera w jednym miejscu rozjazdy między decyzjami a
  wykonaniem, rozsiane dotąd po sześciu zgłoszeniach i czterech
  ADR-ach ([GH-70]).

### Zmienione

- Onboarding domyślnie wskazuje środowisko testowe, więc seria
  Enterów nigdy nie ląduje na produkcji. Sprawdzenie połączenia jest
  proponowane na końcu, ale domyślnie odrzucane: przebieg poprawkowy
  nie może wydawać godzinowego budżetu. Nazwy środowisk wpisane
  słownie nadal działają ([GH-25]).
- Zestawienie CSV ma dziesięć kolumn zamiast dziewięciu. Ostrzeżenie
  o wielu walutach przepisane, bo mówiło nieprawdę — plik walutę
  teraz nazywa; niezmienne zostaje to, że suma całej kolumny Brutto
  wciąż dodawałaby waluty do siebie ([GH-63]).
- Numer wersji na `main` między wydaniami niesie sufiks
  `X.Y.(Z+1).dev0`, więc paczka zbudowana z gałęzi jest odróżnialna od
  tej, która poszła na PyPI. Wydanie zdejmuje sufiks, żaden numer nie
  jest pomijany, a na PyPI nadal trafiają numery czyste ([GH-73]).

### Poprawione

- Na produkcji nie działało nic, co liczy budżet. KSeF odpowiada tam
  na `GET /v2/rate-limits` bez pola, którego model wymaga, więc odczyt
  limitów wywracał się na walidacji i pociągał za sobą wszystkie
  cztery narzędzia. Nieczytelna odpowiedź degraduje się teraz do
  wartości zachowawczych zamiast przerywać operację, a `verify`
  przechodził wcześniej, bo limitów nie czyta — jego zielony wynik
  nigdy nie dowodził sprawności narzędzi MCP ([GH-76], [GH-70]).
- Narzędzia MCP tłumaczą błędy na `ToolError`, bo SDK ukrywa treść
  wyjątków nieprzewidzianych — stąd gołe „Error executing tool"
  zamiast przyczyny ([GH-76]).
- CI przestaje przepuszczać zepsute renderowanie: filtr ścieżek
  obejmuje cały `src/**` wraz z shimem i bundlem, a runner dostaje
  Node w wersji z `.node-version`. Bez tego testy renderu pomijały
  się, a bieg świecił się na zielono dokładnie dlatego, że
  najważniejszy test się nie wykonał ([GH-42]).
- `bin/release.py` odmawia wydania numeru, który nosi już każdy wheel
  zbudowany z `main` — czysty numer bez taga lokalnego i zdalnego jest
  odtąd stanem do naprawienia, nie zaproszeniem do publikacji.
  Odmowa nazywa obie drogi powrotu ([GH-78]).

### Bezpieczeństwo

- **Numer faktury nie wyprowadza już zapisu poza archiwum.** Numer
  KSeF przychodzi od wywołującego i staje się nazwą pliku po obu
  stronach renderu, a walidacja liczyła jedynie człony rozdzielone
  myślnikiem — więc `../../../../tmp/x-20260817-y-56` przechodziła.
  Numer idzie teraz przez zakotwiczony wzorzec portu, bez ukośnika
  i kropki w alfabecie ([GH-42]).
- **PDF powstaje pod nazwą tymczasową i trafia na miejsce dopiero po
  `chmod 0600`.** Node tworzył go pod umaskiem procesu, więc dokument
  z danymi kontrahenta bywał chwilę czytelny dla innych kont na
  maszynie ([GH-42]).
- Komunikat generatora jest ucinany do 200 znaków, a docstring
  narzędzia mówi wprost, że próbę przeszło wyłącznie FA(3) ([GH-42]).
- Link weryfikacyjny drukowany jest tylko na produkcji. Test i demo
  nie mają powierzchni weryfikacyjnej, a zmyślony adres wydrukowałby
  na dokumencie odsyłacz donikąd ([GH-42]).
- **Wartości awaryjne limitów są realnymi liczbami, nie `None`.**
  Licznik budżetu czyta brak sufitu jako brak ograniczenia i przestaje
  odmawiać, więc `None` wyłączyłby po cichu ochronę przed limitem
  Ministerstwa ([GH-76]).
- **Współpracownik z forka dostaje ten sam przegląd co wszyscy, bez
  wystawiania sekretów.** GitHub nie wydaje tokenu OIDC dla zdarzenia
  `pull_request` z forka, więc oba przeglądy były dla forków pomijane.
  Przeglądy przechodzą na `workflow_run` w kontekście repozytorium
  bazowego; uprzywilejowany workflow pobiera gałąź bazową, nigdy head
  PR-a, a numer zgłoszenia wiązany jest z `head_sha`, którego
  zgłaszający nie kontroluje. `pull_request_target` odrzucony
  świadomie ([GH-6]).
- **README otwiera się ostrzeżeniem o niepowiązanym projekcie o tej
  samej nazwie**, który wystawia zdalny serwer MCP pod
  `https://ksef-mcp.pl/mcp`. Tam faktury i uwierzytelnienie
  przechodzą przez cudzą usługę. Rozpoznawalny objaw podany wprost:
  ten serwer działa lokalnie i o nic nie pyta w przeglądarce
  ([GH-75]).
- Testy `doctor` dostają jawną ścieżkę konfiguracji — dotąd sięgały do
  prawdziwej konfiguracji osoby uruchamiającej zestaw ([GH-75]).

[GH-6]: https://github.com/Dev10x-Guru/ksef-mcp/issues/6
[GH-25]: https://github.com/Dev10x-Guru/ksef-mcp/issues/25
[GH-63]: https://github.com/Dev10x-Guru/ksef-mcp/issues/63
[GH-70]: https://github.com/Dev10x-Guru/ksef-mcp/issues/70
[GH-71]: https://github.com/Dev10x-Guru/ksef-mcp/issues/71
[GH-72]: https://github.com/Dev10x-Guru/ksef-mcp/issues/72
[GH-73]: https://github.com/Dev10x-Guru/ksef-mcp/issues/73
[GH-74]: https://github.com/Dev10x-Guru/ksef-mcp/issues/74
[GH-75]: https://github.com/Dev10x-Guru/ksef-mcp/issues/75
[GH-76]: https://github.com/Dev10x-Guru/ksef-mcp/issues/76
[GH-78]: https://github.com/Dev10x-Guru/ksef-mcp/issues/78
## 0.2.0 — 2026-09-14


Wydanie 0.1.1 umiało jedno: potwierdzić, że token działa. To wydanie
zamienia narzędzie w takie, które samo ściąga faktury z KSeF na dysk —
przyrostowo, od punktu kontynuacji, w tempie mieszczącym się
w godzinowych limitach Ministerstwa. Podatnik może zobaczyć w rozmowie,
co przyszło, uzgodnić z tego miesiąc dla księgowej, przejrzeć to, co
doszło od ostatniego spojrzenia, i skasować to, czego nie musi już
trzymać. Wizualizacji PDF tutaj nie ma: oficjalnego generatora
Ministerstwa nie ma dziś w publicznym rejestrze npm, więc zadanie
zostało odłożone ([GH-42]).

### Dodane

- Komenda `ksef-mcp skill install --scope user|project` zapisuje skill
  dla Claude Code, dzięki czemu agent od razu wie, jak korzystać
  z serwera: że odpowiada z lokalnego archiwum, że synchronizacja ma
  własny rytm, że treść faktury nie wchodzi do kontekstu i że każda
  odpowiedź nazywa środowisko. Zakres podaje się jawnie — `uvx` bywa
  uruchamiany z przypadkowego katalogu, więc cicho wybrane miejsce
  byłoby ostatnim, w którym ktokolwiek szukałby pliku ([GH-34]).
- Aktualizacja skilla pokazuje różnicę wobec zainstalowanego pliku
  i pyta o zgodę przed nadpisaniem, więc własne zmiany nie znikają
  niezauważone ([GH-34]).
- Narzędzie `synchronise_invoices` ściąga to, czego w archiwum jeszcze
  nie ma, i samo pilnuje tempa: najwyżej jeden eksport na typ podmiotu
  w jednym przebiegu, sprzedawca i nabywca nie częściej niż co
  piętnaście minut, a role rzadkie — `Podmiot 3` i podmiot upoważniony
  — raz na dobę w oknie nocnym. Narzędzie nie przyjmuje żadnych
  argumentów: ani okna, ani paginacji. Agent sterujący tymi parametrami
  spaliłby godzinową pulę w dwie minuty, a Ministerstwo czyta taki
  wzorzec jako próbę obchodzenia limitu ([GH-36]).
- Postęp synchronizacji przeżywa restart: punkty kontynuacji — osobne
  dla każdego typu podmiotu — oraz rekord zakolejkowanego eksportu leżą
  w katalogu danych, a nie w katalogu podręcznym. Ubicie serwera
  kosztuje najwyżej jedno odpytanie o status, nigdy eksportu ani pełnej
  resynchronizacji ([GH-36]).
- Gotowa paczka eksportu jest odczytywana do końca: części pobierane
  z osobnych adresów, odszyfrowane kluczem AES-256 z inicjalizacji,
  złożone i rozpakowane. Podatnik dostaje faktury, a nie zaszyfrowany
  ZIP w kawałkach ([GH-37]).
- Jedno wywołanie `synchronise_invoices` kończy się fakturami na dysku.
  Paczka, którą KSeF ogłosi gotową, jest w tym samym przebiegu pobrana,
  odszyfrowana i zapisana w archiwum podmiotu — wcześniej przebieg
  kończył się wiedzą, że paczka czeka. Odpowiedź mówi, gdzie faktury
  wylądowały i które numery KSeF przyszły ([GH-57]).
- Nieudane pobranie części albo nieudany zapis nie przybliża okresu do
  „kompletnego": paczka zostaje na dysku razem z kluczem i dokańcza ją
  kolejny przebieg, bez wydawania drugiego z dwudziestu eksportów na
  godzinę. Punkt kontynuacji przesuwa to, co potwierdził KSeF, a nie
  to, czy temu przebiegowi udało się zapisać pliki ([GH-37], [GH-57]).
- Faktury lądują w archiwum pod numerem KSeF — `<NumerKSeF>.xml` —
  w podkatalogu osobnym dla każdego podmiotu i środowiska. Biuro
  rachunkowe nie pomiesza więc faktur dwóch klientów, a plik da się
  przekazać i zaimportować bez zgadywania, co w nim jest ([GH-38]).
- Powtórzona synchronizacja tego samego okresu nie tworzy duplikatów.
  Rozpoznanie idzie po numerze KSeF z manifestu paczki, nie po nazwie
  pliku — nazwy potrafiły dawać fałszywe wyniki, numer nie. Odpowiedź
  wymienia numery, których nie pobierano ponownie ([GH-38], [GH-57]).
- Pamięć o tym, co już pobrano, leży w osobnym pliku obok faktur.
  Podatnik może więc skasować same faktury — dla oszczędności miejsca
  albo z powodów ochrony danych — a kolejna synchronizacja i tak nie
  ściągnie ich po raz drugi ([GH-38]).
- Pytanie o ten sam okres drugi raz nie kosztuje ani jednego
  z dwudziestu zapytań na godzinę. Odpowiedź na zamknięty przedział dat
  jest zapisywana na dysku razem ze znacznikiem chwili, w której
  naprawdę zapłacono za nią budżetem, i przy powtórzeniu wraca stamtąd
  — także po restarcie serwera ([GH-39], [GH-40]).
- Zapis jest osobny dla każdego typu podmiotu, więc odpowiedź na
  pytanie „co sprzedałem we wrześniu" nie zostanie podana jako
  odpowiedź na „co kupiłem" — ta sama firma bywa sprzedawcą na jednej
  fakturze i nabywcą na następnej ([GH-39]).
- Po pobraniu widać w rozmowie, co przyszło, bez otwierania katalogu:
  narzędzie `list_recent_invoices` wypisuje metadane faktur z ostatnich
  trzydziestu dni osobno dla każdej roli podmiotu — numer KSeF, numer
  faktury sprzedawcy, datę wystawienia, NIP i nazwę sprzedawcy oraz
  kwoty. Odczyt nie pyta o zgodę ([GH-40]).
- Lista dłuższa niż pięćdziesiąt pozycji nie jest po cichu ucinana:
  zamiast pozycji wraca liczba faktur i suma brutto — osobno dla każdej
  waluty — a odpowiedź mówi wprost, że progu nie da się przekroczyć.
  Gdy to sam KSeF nie zmieścił okna w jednej odpowiedzi, też jest to
  napisane ([GH-40]).
- Pusty wynik jest osobnym komunikatem i powtarza pytanie, które go
  wywołało: NIP, środowisko, rolę podmiotu i oba końce okresu. Dzięki
  temu „nic nie przyszło" da się odróżnić od „zapytałeś o zły
  miesiąc" ([GH-40]).
- Miesiąc da się przekazać księgowej jednym załącznikiem: narzędzie
  `export_period_statement` zapisuje zestawienie faktur zakupowych za
  wskazany miesiąc jako plik CSV w zadeklarowanym katalogu roboczym.
  Nazwa pliku mówi, czym on jest — `zestawienie-2026-08-1234567890.csv`
  — więc nie trzeba jej rozszyfrowywać po odebraniu poczty ([GH-41]).
- Zestawienie niesie dokładnie to, czego potrzeba do uzgodnienia
  okresu: numer KSeF, numer faktury sprzedawcy, datę wystawienia, NIP
  i nazwę sprzedawcy oraz brutto, netto i VAT. Adresów, numerów
  rachunków, pozycji faktury ani ścieżek lokalnych w pliku nie ma — CSV
  jest z założenia przesyłany dalej, a te dane nie są tam do niczego
  potrzebne ([GH-41]).
- Każda pozycja ma kod weryfikacyjny KOD I złożony z NIP-u sprzedawcy,
  daty wystawienia i skrótu SHA-256 faktury leżącej w archiwum. Kwoty
  przechodzą z KSeF-u do pliku bez zaokrąglenia, więc suma brutto
  uzgadnia się z Aplikacją Podatnika co do grosza ([GH-41]).
- Widać, co doszło od ostatniego spojrzenia — a nie tylko, co jest.
  Narzędzie `review_new_invoices` porównuje ostatnie dziewięćdziesiąt
  dni z zapisem tego, co już zostało pokazane, i wypisuje wyłącznie
  różnicę. Tego darmowa Aplikacja Podatnika nie robi: pokazuje stan,
  nigdy przyrost. Zapis jest trwały i przeżywa restart, więc pytanie
  zadane w poniedziałek nie zaczyna liczyć od zera we wtorek
  ([GH-43]).
- Faktura, która wpadła do miesiąca już rozliczonego, przestaje być
  niewidoczna. KSeF nie zna pojęcia zamkniętego okresu i nie
  powstrzyma takiego napływu, więc narzędzie liczy osobno te nowe
  faktury, które numer KSeF dostały przed bieżącym miesiącem, i podaje
  dni ich nadania. To sygnał do sprawdzenia, nie rozstrzygnięcie —
  o ujęciu podatkowym decyduje księgowa, nie narzędzie ([GH-43]).
- Data otrzymania faktury czytana jest z numeru KSeF, a nie z momentu,
  w którym akurat po nią sięgnięto. Faktura z numerem nadanym w lipcu
  jest lipcowa niezależnie od tego, kiedy ktokolwiek o nią zapytał —
  tak samo przy przeglądzie, jak przy cięciu archiwum po okresie
  ([GH-43], [GH-44]).
- Powyżej progu pięćdziesięciu nowych pozycji wiersze nie są wypisywane
  i wtedy nic nie zostaje oznaczone jako pokazane — skoro nie było ich
  widać pojedynczo, kolejne wywołanie je powtórzy. Odpowiedź mówi to
  wprost ([GH-43]).
- Archiwum da się wyczyścić jedną komendą — `ksef-mcp purge` — i to bez
  utraty wiedzy o tym, co już pobrano. Bezterminowa retencja przestaje
  więc oznaczać, że po roku na laptopie leży komplet faktur wszystkich
  obsługiwanych podmiotów wraz z danymi osobowymi kontrahentów
  ([GH-44]).
- Ciąć można po podmiocie (`--nip`), po okresie (`--od`, `--do`) albo
  po obu naraz: „faktury klienta X starsze niż rok" to jedno
  wywołanie. Komenda pracuje wyłącznie w katalogu wskazanego podmiotu
  i nigdy nie zagląda do sąsiedniego ([GH-44]).
- Po wyczyszczeniu ponowna synchronizacja **nie ściąga skasowanych
  faktur powtórnie** — indeks deduplikacji jest osobnym plikiem od
  treści i zostaje nietknięty. Nietknięte zostają też punkty
  kontynuacji i zapis tego, co już zostało pokazane człowiekowi:
  zwolnienie miejsca na dysku nie cofa ani pytań zadanych KSeF-owi, ani
  przeglądu ([GH-44]).
- Każdy odczyt zostawia trwały ślad, więc po fakcie da się odtworzyć,
  kto sięgnął po które faktury, na jakiej podstawie i o co pytał. Wpis
  niesie moment, NIP, z którego uprawnienia skorzystano, źródło tego
  uprawnienia, kryteria zapytania, liczbę dokumentów, numery KSeF,
  ścieżkę zapisanego pliku i jego format ([GH-45]).
- Ślad rozróżnia to, co wylądowało w pliku, od tego, co zobaczył model
  w oknie rozmowy — to dwa różne zdarzenia i tylko rozdzielone
  odpowiadają na pytanie, co komu ujawniono. Osobno zapisywane jest też
  pominięcie faktury rozpoznanej jako już posiadana: bez tego ślad
  czytałby się tak, jakby nikt jej nie dotknął ([GH-45]).
- Skasowanie faktur także zostawia wpis w dzienniku: kiedy, czyje
  faktury, z jakim zakresem i które numery KSeF przestały istnieć.
  Dziennik przeżywa faktury, które opisuje ([GH-44], [GH-45]).

### Zmienione

Poniższe zadziała inaczej u kogoś, kto używa 0.1.1.

- Gdy KSeF odmówi, `verify` podaje w jednym zdaniu, **którego podmiotu**
  i **którego środowiska** dotyczy odmowa oraz z jakiego powodu. Przy
  dwóch skonfigurowanych NIP-ach samo „token odrzucony" kazało zgadywać
  ([GH-33]).
- Doszły dwie komendy: `ksef-mcp skill install` ([GH-34]) oraz
  `ksef-mcp purge` ([GH-44]). Dotychczasowe — `onboarding`, `doctor`,
  `token`, `verify` — działają jak dotąd.
- Doszły cztery narzędzia MCP: `synchronise_invoices` ([GH-36]),
  `list_recent_invoices` ([GH-40]), `export_period_statement`
  ([GH-41]) oraz `review_new_invoices` ([GH-43]). Konfiguracja klienta
  MCP zostaje bez zmian — serwer startuje tym samym poleceniem.
- Doszły dwie zależności bezpośrednie: `cryptography` do odszyfrowania
  paczki eksportu oraz `secretstorage` wyłącznie na Linuksie do odczytu
  stanu blokady magazynu haseł. Obie przychodziły dotąd jako zależności
  przechodnie, ale kod, który je importuje, nie ma prawa polegać na
  cudzym drzewie zależności ([GH-33], [GH-37]).
- Serwer rozmawia z KSeF przez własną warstwę pośredniczącą, a nie
  bezpośrednio przez bibliotekę klienta. Dla podatnika oznacza to
  jedno: gdy biblioteka się zmieni albo zostanie wymieniona, narzędzia
  i ich odpowiedzi zostaną takie same ([GH-35]).
- Serwer odczytuje z KSeF rzeczywiste limity zapytań i liczy, ile z
  nich już zużył, zamiast zakładać wartości z dokumentacji. Podmiot,
  któremu Ministerstwo podniosło limit, dostaje tyle, ile mu przyznano
  ([GH-35]).
- Zbyt szerokie okno dat i numer, który nie jest numerem KSeF, są
  odrzucane, zanim cokolwiek poleci do KSeF. Wcześniej kosztowały jedno
  zapytanie z godzinowej puli i wracały jako błąd serwera ([GH-35]).
- Kwoty faktur są liczone dokładnie, a nie w przybliżeniu — porównanie
  archiwum z ewidencją nie pokaże już różnicy o grosz, której nie ma
  ([GH-35]).

### Poprawione

- Gałąź `main` przestała czerwienieć po każdym udanym wydaniu. Test
  narzędzi wydawniczych czytał żywy `CHANGELOG.md` i wymagał treści
  w sekcji, którą skrypt wydania właśnie stamtąd zabierał, więc
  czerwień mówiła o stanie repozytorium, nie o kodzie. To samo
  twierdzenie sprawdzane jest teraz na dokumencie syntetycznym.

### Bezpieczeństwo

- Po odmowie z powodu wyczerpanego limitu serwer czeka dokładnie tyle,
  ile podał KSeF, i tylko wtedy, gdy KSeF to podał — a domyślnie nie
  ponawia wcale i oddaje decyzję człowiekowi. Ministerstwo odnotowuje
  przekroczenia limitów i wydłuża blokadę przy powtórzeniach, więc
  wytrwałość klienta szkodzi bardziej niż pojedyncze niepowodzenie
  ([GH-35]).
- Zanim narzędzie sięgnie po token, sprawdza, czy magazyn haseł jest
  odblokowany — i gdy nie jest, mówi to wprost zamiast otwierać okno
  z prośbą o hasło. Takie okno zawieszało całą rozmowę z agentem, bo
  pojawiało się w środku czynności wyglądającej na zwykły odczyt
  ([GH-33]).
- Klucz, którym zaszyfrowana jest paczka, znika w chwili trafienia
  faktur do archiwum — nie zostaje na dysku ani chwili dłużej i nie
  czeka na osobne sprzątanie. Eksport odrzucony przez KSeF również nie
  zachowuje klucza ([GH-37]).
- Paczka niezgodna z tym, co KSeF o niej podał — rozmiarem albo skrótem
  którejkolwiek części — nie jest rozpakowywana. Nie trafi też do
  archiwum plik, którego nazwa wskazuje poza paczkę ([GH-37]).
- Paczka, której manifest nie wiąże numeru KSeF z plikiem, nie jest
  archiwizowana wcale, zamiast trafić do archiwum pod zgadniętą nazwą.
  Faktura już zapisana nie jest po cichu nadpisywana ([GH-38]).
- Pobranie części paczki nie jest liczone w godzinowym budżecie
  zapytań. Adresy części są jednorazowe i nie niosą poświadczenia
  KSeF, a pułap sześćdziesięciu czterech pobrań na godzinę dotyczy
  sięgania po fakturę po numerze. Liczenie ich tam przerywałoby
  archiwizację paczki, za której eksport już zapłacono ([GH-57]).
- Treść faktury nie wchodzi do żadnej odpowiedzi narzędzia. Dokument
  FA(2)/FA(3) zawiera dane osobowe kontrahenta, więc narzędzie nazywa
  plik i go nie otwiera — do rozmowy trafiają wyłącznie metadane
  ([GH-40], [GH-57]).
- Dziennik audytu nie niesie ani tokenu, ani treści faktury: zapisuje
  źródło uprawnienia, nigdy sam sekret. Leży w katalogu danych, osobno
  dla każdego podmiotu i środowiska, z prawami tylko dla właściciela,
  i wyłącznie rośnie — dopisanie wiersza nie stawia pod ryzykiem tego,
  co już zapisano ([GH-45]).
- Archiwum, katalog podręczny z odpowiedziami o okresy oraz katalog
  roboczy z zestawieniami powstają z prawami wyłącznie dla właściciela:
  katalogi `0700`, pliki `0600`. Katalog roboczy jest przy tym
  produktem, nie magazynem — ścieżka wyglądająca na synchronizowaną do
  chmury jest nazwana wprost w odpowiedzi, a katalog wskazany wewnątrz
  archiwum albo cache'u zostaje odrzucony, dzięki czemu skasowanie
  zestawień nigdy nie zabiera pobranych faktur ([GH-38], [GH-39],
  [GH-41]).
- Cache odpowiedzi leży w katalogu podręcznym systemu, osobno od
  katalogu danych z punktami kontynuacji, indeksem deduplikacji
  i archiwum. Skasowanie katalogu podręcznego — ręcznie albo przez
  czyszczarkę dysku — kosztuje jedno ponowne odpytanie, nigdy pełnej
  resynchronizacji. Uszkodzony albo obcięty wpis nie jest błędem, tylko
  brakiem trafienia ([GH-39]).
- Zanim `purge` cokolwiek skasuje, wypisuje numery KSeF faktur do
  skasowania, ile miejsca zwolnią i co zostaje, a potem pyta o zgodę —
  domyślną odpowiedzią jest „nie" i nie ma przełącznika, który by to
  pytanie pominął. Plik, którego nazwa nie jest numerem KSeF, zostaje
  na dysku i jest zgłoszony; operacja nieodwracalna nie zgaduje
  ([GH-44]).

[GH-33]: https://github.com/Dev10x-Guru/ksef-mcp/issues/33
[GH-34]: https://github.com/Dev10x-Guru/ksef-mcp/issues/34
[GH-35]: https://github.com/Dev10x-Guru/ksef-mcp/issues/35
[GH-36]: https://github.com/Dev10x-Guru/ksef-mcp/issues/36
[GH-37]: https://github.com/Dev10x-Guru/ksef-mcp/issues/37
[GH-38]: https://github.com/Dev10x-Guru/ksef-mcp/issues/38
[GH-39]: https://github.com/Dev10x-Guru/ksef-mcp/issues/39
[GH-40]: https://github.com/Dev10x-Guru/ksef-mcp/issues/40
[GH-41]: https://github.com/Dev10x-Guru/ksef-mcp/issues/41
[GH-42]: https://github.com/Dev10x-Guru/ksef-mcp/issues/42
[GH-43]: https://github.com/Dev10x-Guru/ksef-mcp/issues/43
[GH-44]: https://github.com/Dev10x-Guru/ksef-mcp/issues/44
[GH-45]: https://github.com/Dev10x-Guru/ksef-mcp/issues/45
[GH-57]: https://github.com/Dev10x-Guru/ksef-mcp/issues/57
## 0.1.1 — 2026-09-13


**Pierwsze wydanie tego pakietu.** Nie ma tu więc zmian zachowania wobec
poprzedniej wersji — nie było poprzedniej. Wszystko poniżej jest nowe
i cała lista opisuje stan początkowy, a nie przyrost.

Narzędzie na tym etapie **konfiguruje się i potwierdza połączenie**.
Wyszukiwanie i pobieranie faktur oraz wizualizacja PDF są planowane;
README nazywa je wprost jako niegotowe.

### Dodane

- Komenda `ksef-mcp onboarding` prowadząca przez konfigurację: kontrola
  Node, jawny wybór magazynu keyringu, wybór środowiska KSeF, zapis tokenu
  i katalog na faktury ([GH-4]).
- Komenda `ksef-mcp doctor` sprawdzająca warunki wstępne bez sięgania
  do KSeF — mówi też, którego `node` używa, co przy przełącznikach wersji
  bywa całą odpowiedzią ([GH-4]).
- Komendy `ksef-mcp token set|delete|status` obsługujące token w keyringu.
  Token wchodzi bez echa, nigdy nie jest argumentem procesu i nigdy nie
  jest pokazywany — tylko długość i końcówka ([GH-4]).
- Komenda `ksef-mcp verify` potwierdzająca połączenie z KSeF i pokazująca
  ostatnie faktury zakupowe. Osobna od onboardingu, bo każde zapytanie
  wydaje godzinowy budżet, także przy przebiegu poprawkowym ([GH-4]).
- Ścieżka awaryjna przez zmienną `KSEF_TOKEN` dla maszyn bez keyringu —
  headless, WSL, kontener. Gdy jest ustawiona, ma pierwszeństwo przed
  keyringiem, a `token status` mówi, z którego źródła token pochodzi
  ([GH-4]).
- `CHANGELOG.md` oraz `bin/release.py`: wydanie idzie jedną komendą,
  pod kontrolami biegnącymi zanim cokolwiek dotrze do PyPI, a każdy krok
  wykrywa, czy już się wykonał, więc zerwana sieć nie zostawia publikacji
  w połowie ([GH-22]).

### Zmienione

- Pakiet nazywa się `ksef-mcp` — tak jak repozytorium, nagłówek README
  i serwer MCP. Instaluje się go przez `uvx ksef-mcp`, bez przełącznika
  `--from`. Konfiguracje klientów MCP pozostają bez zmian, bo `ksef-mcp`
  bez argumentów nadal uruchamia serwer na stdio ([GH-20], [GH-21]).
- Katalog na faktury: gdy tworzymy go sami, dostaje uprawnienia `0700`;
  gdy już istniał, zostawiamy jego uprawnienia bez zmian i mówimy, jakie
  są. Ktoś może wskazać katalog domowy albo współdzielony, a zaostrzanie
  cudzych uprawnień jest zmianą, o którą nie prosił ([GH-4]).

### Bezpieczeństwo

- **Wbudowane ponawianie żądań w `ksef2` ograniczone do jednej próby.**
  Jego okno wynosi cztery sekundy wobec limitów liczonych w minutach, więc
  pętla nie doczekałaby końca limitu — dokładałaby tylko prób do wzorca,
  który Ministerstwo Finansów czyta jako obchodzenie limitu, a czas
  blokady rośnie przy powtórzeniach. Przy odmowie limitu `verify` podaje
  czas oczekiwania i **nie ponawia sam** ([GH-4]).
- **Środowisko KSeF podawane jawnie przy każdym konstruowaniu klienta**,
  ponieważ domyślnym w bibliotece `ksef2` jest produkcja. Domyślnym
  w konfiguracji narzędzia jest środowisko testowe ([GH-4]).
- Plik konfiguracyjny powstaje od razu z trybem `0600`. Wcześniej między
  zapisem treści a nadaniem uprawnień istniało okno, w którym NIP był
  czytelny dla każdego konta na maszynie ([GH-4]).
- **`ksef-mcp token delete` nie twierdzi, że odwołał dostęp**, gdy token
  nadal podaje zmienna `KSEF_TOKEN` — mówi wprost, że trzeba ją wycofać
  z powłoki. Wpis w keyringu jest kluczowany NIP-em, zmienna nie jest,
  więc `verify` ostrzega, że przy niej nie ma gwarancji, do którego
  podmiotu token należy ([GH-4]).
- Wartość tokenu nie trafia do tekstowej reprezentacji obiektu, a NIP nie
  trafia do treści wyjątków — te przeżywają w śladzie stosu, w błędzie
  klienta MCP i w przechwyconym wyjściu ([GH-4]).
- Zawartość faktur nie przechodzi przez narzędzie: `verify` operuje
  wyłącznie na metadanych — numer KSeF, data, sprzedawca, kwota ([GH-4]).

[GH-4]: https://github.com/Dev10x-Guru/ksef-mcp/issues/4
[GH-20]: https://github.com/Dev10x-Guru/ksef-mcp/issues/20
[GH-21]: https://github.com/Dev10x-Guru/ksef-mcp/issues/21
[GH-22]: https://github.com/Dev10x-Guru/ksef-mcp/issues/22
