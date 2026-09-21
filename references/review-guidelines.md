# Wytyczne przeglądu kodu Claude Code

Zasady **przebiegu pracy** przy przeglądzie — jak prowadzić przegląd,
zarządzać wątkami, pisać podsumowania i współpracować z autorem. Co
sprawdzać **w kodzie** — patrz specyfikacje agentów wyspecjalizowanych
dziedzinowo w `.claude/agents/`.

## Kontrola stanu zatwierdzenia

Przed poproszeniem o przegląd (lub ponowny przegląd) PR-a sprawdź jego
bieżący stan przeglądu, żeby nie niepokoić recenzentów na już
zatwierdzonych PR-ach.

**Zasada decyzyjna:**

1. Pobierz stan przez `gh pr view N --json reviewDecision,reviews,headRefOid`.
2. Jeśli `reviewDecision == "APPROVED"` ORAZ `commit.oid` ostatniego
   przeglądu zgadza się z `headRefOid` → PR jest zatwierdzony na
   bieżącym HEAD. **Przerwij** prośbę o przegląd i zasugeruj zamiast
   tego merge.
3. Jeśli `reviewDecision == "APPROVED"`, ale nowsze commity
   unieważniły zatwierdzenie (SHA przeglądu != SHA HEAD) → przejdź do
   ponownej prośby, ale **odfiltruj** każdego recenzenta, którego
   najnowszy przegląd na bieżącym HEAD jest już `APPROVED`.
4. W przeciwnym razie (`CHANGES_REQUESTED`, `REVIEW_REQUIRED` lub
   `null`) → przejdź normalnie.

**Dlaczego?** Ponowne powiadomienia na zatwierdzonych PR-ach powodują
zmęczenie recenzentów i niepotrzebny ruch — kolejnym krokiem jest
merge, nie kolejny cykl przeglądu.

## Przebieg przeglądu

1. Sprawdź istniejące komentarze przeglądu, żeby nie powielać
   informacji zwrotnej
2. Sprawdź wcześniejsze komentarze podsumowujące
   (`gh pr view {PR_NUMBER} --json comments`), żeby zidentyfikować
   nieaktualne podsumowania
3. Przeanalizuj bieżący diff (`gh pr diff`)
4. Dla każdego wcześniejszego wątku Claude Code Review:
   - Poprawiony/usunięty → odpowiedz „Poprawione" (NIE rozwiązuj wątku
     — zostaw to człowiekowi)
   - Utrzymuje się w niezmienionym kodzie → odpowiedz „Nadal aktualne";
     nie powielaj
   - Zmieniony, ale problem pozostaje → odpowiedz z aktualizacją
5. Używaj narzędzi komentarzy inline TYLKO dla NOWYCH problemów
6. Ukryj nieaktualne podsumowania przeglądu przed opublikowaniem
   nowego:
   a. Odpytaj wątki przeglądu przez API wątków przeglądu GitHuba
      (nigdy ręcznie sklejanym GraphQL) — dla każdego wątku sprawdź
      `isResolved` i pogrupuj wg `pullRequestReview.databaseId`
   b. Dla każdego wcześniejszego przeglądu Claude z niepustą treścią:
      - WSZYSTKIE wątki `isResolved: true` → zminimalizuj jako OUTDATED
      - JAKIKOLWIEK wątek nierozwiązany → zostaw widoczny
      - Przegląd BEZ wątków inline (tylko podsumowanie) → zminimalizuj
   c. Minimalizacja: `minimizeComment(input: {subjectId: "<node_id>",
      classifier: OUTDATED})`
   d. Pomiń własny przegląd bieżącego cyklu
   e. Rozwiązywanie wątków musi pochodzić od nadzorującego człowieka —
      recenzent NIE MOŻE rozwiązywać wątków, żeby wywołać tę bramkę
7. Utwórz JEDEN podsumowujący komentarz przeglądu
   (`gh pr review --comment`) zawierający:
   - Obserwacje wysokopoziomowe i ocenę jakości
   - Zagadnienia przekrojowe niezwiązane z konkretnymi liniami
   - Potwierdzenie rozwiązanych problemów
   - BEZ powtarzania treści komentarzy inline
8. Użyj statusu COMMENT (nie REQUEST_CHANGES ani APPROVE)
9. Jeśli opublikowano komentarze inline, przełącz PR na draft:
   `gh pr ready --undo $PR_NUMBER`
   - Zapobiega to szumowi ponownych przeglądów przy commitach fixup
   - Autor oznacza „Ready for review", gdy poprawki są gotowe
   - NIE przełączaj, jeśli przegląd był czysty (brak komentarzy
     inline)

## Zakres i szum

- Skup się na liniach zmienionych w diffie PR-a
- Problemy w **niezmienionym** kodzie → „zastane, poza zakresem"
  (informacyjne, nie blokujące)
- NIGDY nie powtarzaj informacji zwrotnej z poprzednich cykli
  przeglądu
- Grupuj powiązane problemy tematycznie; podsumowanie zwięzłe
- Każdy komentarz inline odwołuje się do ścieżki pliku i numeru linii
- **Wyjątek bootstrappingu** — gdy PR wprowadza nową regułę, sam PR
  może ją naruszać, bo reguła nie obowiązywała w chwili zgłoszenia.
  Oznacz jako informacyjne, nie blokujące.

## Głębokość przeglądu zależna od kontekstu

| Kontekst        | Skup się na                              | Unikaj                               |
|----------------|------------------------------------------|---------------------------------------|
| Produkcja      | Wszystkich standardach                   | Bikeshedding                          |
| POC/Test       | Czy działa? Bezpieczeństwo. Błędna logika. | YAGNI, obsługa błędów, przypadki brzegowe |
| Refaktoryzacja | Zachowanie działania                     | Nowe funkcje, rozrost zakresu         |
| Infrastruktura | Zmiany zachowania, dokładność opisów pomocy | Kwestionowanie deklarowanej intencji projektowej |

### Wykrywanie POC

Sprawdź tytuł PR-a (🧪, „POC", „test", „demo"), ścieżki plików
(`test`, `poc`) i opis („temporary", „exploratory"). Jeśli to POC:
- Rozpocznij podsumowanie od „Reviewing as POC/test code with relaxed
  standards"
- Sprawdzaj tylko: błędy, bezpieczeństwo, problemy integracji

### Wyjaśnienia projektowe autora

Gdy autor wyjaśnia, że zgłoszone zachowanie jest zamierzone:
1. Zweryfikuj, czy tytuł/treść PR-a potwierdza to twierdzenie
2. Potwierdź i zamknij wątek
3. Nigdy nie zgłaszaj ponownie w kolejnych cyklach
4. Nigdy nie wymagaj ponownego wyjaśnienia po force-pushu

## Strategia komentarza podsumowującego

- JEDNO podsumowanie na cykl przeglądu (nie na commit)
- **Publikuj tylko, gdy** są nowe problemy lub istotne zmiany do
  przejrzenia
- **Jeśli nie ma nowych problemów, NIE publikuj żadnego przeglądu.**
  Przegląd COMMENTED z pustą treścią dodaje szum bez wartości.
- Po poprawkach: opublikuj JEDNO krótkie potwierdzenie, nie komentarze
  per plik
- Po 3+ podsumowaniach mówiących „looks good": NIE publikuj kolejnego

### Struktura podsumowania ponownego przeglądu

```markdown
## Review Summary (Round N)

### Addressed since last review
- [list items that were fixed]

### Remaining issues
- [only genuinely new or previously unfixed items]
```

Numeracja rund śledzi wywołania przeglądu, nie commity poprawek autora.

## Ograniczanie rozpadu kontekstu

### Przed każdym ponownym przeglądem

1. Przeczytaj ponownie opis PR-a; nie polegaj na pamięci
2. Porównaj z poprzednim stanem przeglądu; skup się na tym, co się
   zmieniło
3. Zweryfikuj, że rozwiązane wątki są faktycznie poprawione
4. Przeczytaj WSZYSTKIE odpowiedzi autora; zbuduj listę „odrzuconych
   sugestii"

### Podczas ponownego przeglądu

5. Bez komentarzy-zombie — nie zgłaszaj ponownie świadomie odrzuconych
   problemów
6. Grupuj powiązaną informację zwrotną w JEDEN komentarz ze
   wszystkimi lokalizacjami
7. Wyraźnie potwierdzaj postęp
8. Po 3 rundach: skup się wyłącznie na poprawności (błędy,
   bezpieczeństwo)
9. Czytaj rzeczywisty plik na HEAD, nie kontekst diffa (force-push
   przesuwa linie)

## Świadomość przeglądu wielu commitów

1. Pierwszy przegląd: zgłoś problemy w bieżącym kodzie
2. Po nowych commitach: sprawdź, czy wcześniejsze problemy są już
   poprawione
3. Potwierdź poprawki; zgłaszaj tylko nowe lub utrzymujące się
   problemy
4. Sprawdź istniejące wątki przed utworzeniem nowych

## Format sugestii kodu

Użyj składni sugestii GitHuba dla poprawek gotowych do zatwierdzenia:

```suggestion
fixed code here
```

- Jedna linia: komentarz w linii N
- Wiele linii: ustaw start_line=N, line=M
- Dodaj wyjaśnienie przed blokiem sugestii
- Używaj do prostych poprawek; dla złożonych zmian opisz podejście
- NIE używaj bloków sugestii dla zmian niekodowych (uprawnienia, zmiany
  nazw plików, `git mv`). Zamiast tego użyj zwykłego tekstu z
  instrukcją.

## Format komentarza przeglądu i warianty JTBD

Formułuj ustalenia jako **[REQUIRED/RECOMMENDED]** — [tytuł],
wyjaśnienie, odwołanie do reguły, poprawka. Dla gramatyki JTBD: nazwij
konkretnego beneficjenta w klauzuli rezultatu — „**żeby integrator
mógł** uzgodnić status faktury" (wg `git-jtbd.md`). Gdy rola i
beneficjent to ta sama osoba, powtórzenie jej jest w porządku; różniący
się beneficjent musi być nazwany wprost. Bezosobowe „**żeby użytkownik
mógł**" lub pierwszoosobowe „**żebym mógł**" to poprawka RECOMMENDED w
stronę konkretnej roli.

## Pozytywna walidacja

Gdy PR demonstruje doskonałe praktyki:
- Wskaż mocne strony z odwołaniami plik:linia
- Nie każdy PR wymaga prośby o zmiany
- Jedno pozytywne podsumowanie na rozwiązany cykl

## Zmiana łamiąca — lista kontrolna przeglądu

Gdy PR zawiera zmianę łamiacą (breaking change) widoczną dla
użytkownika końcowego (zmiana API, zmiana zachowania, usunięcie
funkcji dostępnej publicznie):

1. **Treść PR-a** — zmiana łamiąca jest wyraźnie oznaczona
   nagłówkiem (`## Zmiana łamiąca`) lub sekcją analogiczną, umieszczona
   *przed* wszelkimi nagłówkami szczegółów (patrz również punkt 12
   w `review-checks-common.md` o pozycji nagłówka JTBD)
2. **CHANGELOG.md** — wpis o zmianie łamiaccej pojawia się
   *przed* nagłówkiem `### Dodane` (zgodnie ze strukturą sekcji
   niezwolnionej); opisuje starą ścieżkę i nową dla użytkowników
3. **Migracja dostępna** — jeśli zmiana ma ścieżkę migracji
   (np. „użyj X zamiast Y"), jest ona wyjaśniona w treści PR-a
   i/lub CHANGELOG-u
4. **Zgodność z regułami KSeF** — zmiana nie narusza zasad bezpieczeństwa
   KSeF: ani zmiana w wywołaniu API do KSeF, ani zmiana w obsłudze
   poświadczeń, ani zmiana w obsłudze XML faktury bez przejrzenia
   bezpieczeństwa (patrz `review-checks-common.md` § Zagadnienia
   specyficzne dla KSeF)
5. **Wersjonowanie** — zmiana powinna być poprzedzona podwyższeniem
   numeru wersji (major bump dla publicznego API; patrz polityka
   wersjonowania w CLAUDE.md albo ADR dotyczącej wersjonowania)

## Unikaj bezwartościowych sugestii

- NIGDY nie sugeruj kodu identycznego z oryginałem
- NIGDY nie sugeruj zmian formatowania (obsługują to ruff/lintery)
- ZWERYFIKUJ znak po znaku przed zasugerowaniem
- Sugeruj tylko: błędy, bezpieczeństwo, architekturę, wydajność,
  logikę, nazewnictwo
- W razie wątpliwości: pomiń
