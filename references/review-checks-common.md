# Kontrole przeglądu — zagadnienia przekrojowe

Uniwersalne kontrole przeglądu, niezależnie od agenta
wyspecjalizowanego dziedzinowo. Zasady przebiegu pracy — patrz
`review-guidelines.md`.

## Poziomy egzekwowania

- **CRITICAL/WARNING** lub **REQUIRED**: egzekwowane przez ochronę
  CI/merge lub przegląd kodu; blokuje merge
- **INFO** lub **RECOMMENDED**: wyłącznie doradcze; nie blokuje merge
- Nie oznaczaj jako REQUIRED, jeśli nie istnieje mechanizm
  egzekwowania (CI/ochrona merge/bramka przeglądu kodu)

## Bramka zapobiegania fałszywym trafieniom

Przed opublikowaniem **jakiegokolwiek** komentarza inline:

0. **Zakres diffa** — czy ten plik jest zmieniony w bieżącym diffie
   PR-a? Uruchom `gh pr diff --name-only`, aby potwierdzić. Jeśli
   plik nie jest w diffie, oznacz jako zastany i pomiń.
1. Czy to narusza udokumentowaną regułę z CLAUDE.md? (Brak reguły =
   preferencja)
2. Czy to przeczy ustalonemu wzorcowi w bazie kodu? (5+ wystąpień)
3. Czy dokumentacja, do której się odwołujesz, istnieje? (Zweryfikuj
   narzędziem Read)
4. Poprawa jakości czy tylko preferencja?

**Jeśli którakolwiek odpowiedź zawodzi, NIE publikuj.**

## Protokół weryfikacji kodu

1. Przeczytaj rzeczywisty plik kodu — nigdy nie polegaj wyłącznie na
   fragmentach diffa
2. Zweryfikuj dokładne numery linii i wartości
3. Sprawdź, czy poprawka nie pojawiła się już w późniejszych commitach
4. Cytuj dokładny kod, formułując twierdzenia

## Znane pułapki fałszywych trafień

Przed zgłoszeniem którejkolwiek z nich **zweryfikuj rzeczywisty kod**:

1. **Formatowanie już poprawne**: przeczytaj bieżący kod, nie kontekst diffa
2. **Kod, który już nie istnieje**: zweryfikuj, czy linia istnieje po force-pushu
3. **Typ zwracany już obecny**: przeczytaj rzeczywistą sygnaturę
4. **YAGNI**: zaakceptuj osąd autora „odłóżmy to"
5. **Zamierzone usunięcie zachowania**: gdy tytuł PR-a/zgłoszenie
   stwierdza, że usunięcie jest zamierzone, zewnętrzne boty
   zgłaszające to jako błąd są fałszywymi trafieniami
6. **Ponowne zgłoszenie wyjątku po efekcie ubocznym**:
   `except E: side_effect(); raise` NIE połyka wyjątku — wywołujący
   nadal go otrzymuje
7. **Styl skryptu powłoki**: różne skrypty używają różnych powłok
   (bash, sh) — sprawdź shebang przed zgłoszeniem błędu składni
8. **Linki do commitów `/pull/new/`**: artefakty tworzone przed
   nadaniem numeru PR-a — zgłoś jako RECOMMENDED aktualizację do
   `/pull/<number>/commits/<sha>`, nie jako REQUIRED
9. **Numer zgłoszenia przy pracy self-motivated** — jeśli treść PR-a
   zawiera `Fixes: none — self-motivated`, nie zgłaszaj braku numeru
   zgłoszenia w tytule PR-a ani w treściach commitów. Nie istnieje
   zgłoszenie, do którego można się odwołać.
10. **Konwencja nazwy brancha** — zgłaszaj naruszenia konwencji nazwy
    brancha jako INFORMATIONAL tylko w Rundzie 1. Nie zgłaszaj ponownie
    w kolejnych rundach — nazwa brancha jest niezmienna, dopóki PR jest
    otwarty.
11. **Elementy RECOMMENDED po potwierdzeniu przez autora** — gdy autor
    potwierdził sugestię RECOMMENDED, ale jej nie wdrożył, nie zgłaszaj
    jej ponownie w kolejnych rundach. Tylko problemy REQUIRED blokują
    merge.
12. **Pozycja nagłówka w treści PR-a** — gdy treść PR-a zawiera
    nagłówki Markdown (np. `## Summary`, `## Details`), zweryfikuj, że
    Job Story JTBD pojawia się PRZED wszystkimi nagłówkami. Nagłówek
    przed JTBD psuje parsowanie release notes.
13. **Naruszenia głosu JTBD** — gdy treść PR-a, treść commita lub
    tytuł zgłoszenia zawiera Job Story w pierwszej osobie („I want
    to") lub z bezosobową rolą („the user wants to"), zgłoś jako
    REQUIRED. Wymagany jest głos trzecioosobowy, z konkretną rolą i
    beneficjentem. Patrz `references/git-jtbd.md` § Wymóg głosu oraz
    § Wybór roli.

## Lista kontrolna architektury

Kontrole strukturalne dla nowych lub istotnie zmodyfikowanych plików.
Wyłapują naruszenia, których nie wykrywa powierzchowne szukanie
błędów.

1. **Warstwowanie** — nowe narzędzia/handlery MCP w `src/ksef_mcp/`
   MUSZĄ delegować wywołania API KSeF i logikę biznesową do
   dedykowanego modułu klienta/usługi, a nie wbudowywać wywołanie HTTP
   bezpośrednio w handler narzędzia.
2. **Rozmiar funkcji** — funkcje/metody przekraczające 50 linii
   prawdopodobnie naruszają SRP. Zgłoś jako WARNING z sugestią
   wydzielenia.
3. **Użycie DTO** — inline'owe słowniki z 4+ kluczami przekraczające
   granice modułów powinny być typowane (modele pydantic lub
   dataclasses). Zgłoś jako INFO.
4. **Walidacja danych wejściowych** — ręczne parsowanie argumentów
   narzędzia MCP bez walidacji schematem/pydantic to WARNING.
   Waliduj wcześnie.
5. **Wykrywanie god function** — pojedyncza funkcja wykonująca
   walidację + logikę biznesową + operacje I/O + formatowanie
   odpowiedzi to naruszenie strukturalne niezależnie od liczby linii.

**Kiedy stosować:** przy każdym PR-ze, który dodaje lub istotnie
modyfikuje kod narzędzia/serwera MCP. Pomiń dla PR-ów wyłącznie
dokumentacyjnych, konfiguracyjnych lub testowych.

## Analiza zmian parametrów

Gdy parametry są dodawane/usuwane/stają się opcjonalne:
- Wyszukaj grepem **wszystkie** miejsca wywołania (nie tylko diff)
- Sprawdź, czy opcjonalność służy kompatybilności wstecznej
- Sugerując uczynienie parametru wymaganym: wypisz wszystkie miejsca
  wywołania, żeby to potwierdzić

## Wykrywanie martwego kodu

Dla nowych klas/funkcji/stałych w PR-ze:
- Wyszukaj grepem importy i odwołania poza plikiem definicji
- Jeśli nie znaleziono odwołań, zgłoś jako potencjalny martwy kod
- Wyklucz klasy testowe, abstrakcyjne klasy bazowe, eksporty `__init__`

## Weryfikacja konwencji nazewnictwa

Przed zasugerowaniem zmiany nazwy:
1. Najpierw wyszukaj wzorce w bazie kodu (5+ plików = ustalona
   konwencja)
2. Sugeruj tylko nazwy rzeczywiście niejasne/mylące
3. NIE zgłaszaj: ustalonych wzorców, sufiksów wersji, prefiksów
   dziedzinowych

## Weryfikacja plików i poleceń w dokumentacji

Gdy dokumentacja odwołuje się do poleceń CLI, plików lub katalogów
(np. instrukcje instalacji, przykłady kodu):
- **Polecenia**: zweryfikuj, że występują w sekcji Development w
  CLAUDE.md lub są znanymi wbudowanymi poleceniami `uv`/CLI
- **Pliki i katalogi**: użyj Glob, aby zweryfikować, że istnieją w
  bieżącym commicie (np. `src/ksef_mcp/server.py`)
- **Planowane funkcje**: jeśli dokumentujesz przyszłe funkcje jeszcze
  niezaimplementowane, wyraźnie oznacz jako `[PLANNED]` lub
  `[NOT YET IMPLEMENTED]`
- Niezweryfikowane odwołania w dokumentacji widocznej dla użytkownika
  mają poziom WARNING

## Antywzorce powłoki

- **Ciche połykanie błędów**: `|| true` w krokach konfiguracyjnych i
  `2>/dev/null` w poleceniach, których wynik steruje rozgałęzieniem,
  ukrywają niepowodzenia; zastąp działaniem zapasowym albo usuń
  przekierowanie.
- **Domyślne wartości sterujące rozgałęzieniem w sposób ukryty** (np.
  `jq '.field // ""'`) powinny walidować wprost:
  `[[ -z "$VAR" ]] && { error; exit 1; }`.

## Kontrole lintu Pythona

- **f-stringi bez wyrażeń**: `f"static string"` bez placeholderów `{}`
  to ruff F541. Zgłoś i zaproponuj usunięcie prefiksu `f`.

## Zagadnienia specyficzne dla KSeF

To zagadnienia krytyczne dla dziedziny tego projektu — traktuj
naruszenia jako REQUIRED/CRITICAL, nie jako preferencje stylistyczne:

1. **Nigdy nie wołaj produkcyjnego KSeF z testów ani przykładów** —
   każdy test, skrypt lub fragment dokumentacji, który mógłby trafić
   do produkcyjnego środowiska KSeF (`KSEF_ENV=prod` lub odpowiednik)
   zamiast środowiska testowego/demonstracyjnego, jest CRITICAL.
   Zweryfikuj, że środowisko domyślnie wskazuje test/demo.
2. **Poświadczenia są sekretami** — `KSEF_TOKEN`, `KSEF_NIP`,
   `KSEF_ENV` (oraz inne materiały uwierzytelniające KSeF) nigdy nie
   mogą trafić do kodu na sztywno, do logów, do fixture'ów w
   repozytorium ani do komunikatów błędów. Zgłoś każdy f-string lub
   wywołanie logu, które interpoluje te wartości bezpośrednio.
3. **Nigdy nie loguj ani nie zapisuj XML-a faktury** — treść faktury
   (XML FA(2)/FA(3)) zawiera dane osobowe i biznesowe podatnika.
   Zgłoś każdą ścieżkę kodu zapisującą surowy XML faktury do logów,
   fixture'ów testowych w repozytorium, artefaktów CI lub wyjścia
   debugowego. Zamiast tego redaguj dane albo użyj syntetycznych
   fixture'ów.
4. **Testy dotykające sieci wymagają markera `ksef_live`** — każdy
   test wykonujący rzeczywiste wywołanie sieciowe do KSeF (nawet do
   środowiska testowego) musi być oznaczony (np.
   `@pytest.mark.ksef_live`) i wykluczony z domyślnego uruchomienia
   `pytest` (odfiltrowany przez addopts w `pyproject.toml` lub
   konfigurację CI). Test sieciowy uruchamiany bez oznaczenia w
   domyślnym zestawie to WARNING; taki, który mógłby trafić na
   produkcję, to CRITICAL.
5. **Numery KSeF w komunikatach do klienta** — każdy komunikat
   trafiający do klienta MCP (odpowiedź narzędzia, komunikat błędu,
   ostrzeżenie), który wymienia numer KSeF musi używać `short_reference()`
   z modułu `diagnostics`. Pełny numer KSeF zawiera NIP, który może być
   kontrahentem, nie podmiotem odpytującym (D-038). Zgłoś każdy
   komunikat zawierający pełny numer w polu odpowiedzi (CRITICAL),
   ale zauważ, że `technical_log().info(...)` z pełnym numerem to OK
   (kanał techniczny nie przechodzi sieć).
