# Wytyczne dotyczące pull requestów i porządkowania branchy

Standardy pull requestów i porządkowania branchy w tym repozytorium.

## Porządkowanie brancha

Przebuduj historię commitów, aby powstały atomowe, dobrze
zorganizowane commity.

### Kiedy porządkowanie jest dopuszczalne

- ✅ Przed utworzeniem PR-a
- ✅ Gdy PR jest w statusie **draft**
- ✅ Po informacji zwrotnej z CI (przed rozpoczęciem przeglądu przez
  człowieka)
- ✅ Gdy CI blokuje merge z powodu commitów fixup

### Kiedy porządkowanie jest odradzane

- ❌ Po tym, jak recenzent-człowiek zaczął przegląd

*Dlaczego?* Przepisywanie historii po rozpoczęciu przeglądu przez
człowieka tworzy szum i zamieszanie. Recenzenci tracą kontekst, a
GitHub pokazuje „force-pushed", co ukrywa diff tego, co się zmieniło.

### Strategie

**A. Fixup + Autosquash** (małe, celowane poprawki):
```bash
git commit --fixup=<target-sha>
git rebase -i --autosquash $(git merge-base main HEAD)
```

**B. Miękki reset** (duża reorganizacja):
```bash
git reset --soft $(git merge-base main HEAD)
git reset HEAD  # unstage
git add -p      # selektywne stage'owanie
git commit -m "First logical change"
# powtórz dla każdej logicznej jednostki
```

**C. Interaktywny rebase** (zmiana kolejności/edycja/podział):
```bash
git rebase -i $(git merge-base main HEAD)
```

### Bezpieczeństwo

- Twórz backup przed złożonym przepisywaniem: `git branch backup-before-rewrite`
- Przy pushu przepisanej historii używaj `--force-with-lease`, nie `--force`
- Uzgadniaj z zespołem przed force-pushem wspólnych branchy

## Wytyczne dotyczące pull requestów

### Przed utworzeniem PR-a

1. Upewnij się, że wszystkie commity są atomowe i dobrze zorganizowane
2. Zeskładaj (squash) wszystkie commity fixup
3. Uruchom kontrole jakości lokalnie (`uv run pytest`, ruff, mypy jeśli skonfigurowane)
4. Sprawdź, czy branch jest aktualny względem `main`
5. Zrób rebase, aby zlinearyzować historię — bez commitów merge przed
   otwarciem PR-a

### Tytuł PR-a

Użyj linii tytułu głównego commita (gitmoji + numer zgłoszenia + opis).

**Ważne**: gitmoji pojawia się w **treści commita**, nie w polu tytułu
PR-a na GitHubie. Interfejs GitHuba pokazuje je osobno — gitmoji
głównego commita i tak pojawi się automatycznie w release notes i
w git logu, niezależnie od tego, jak wypełnione jest pole tytułu PR-a.
Kluczowy wymóg to obecność gitmoji w commicie; GitHub renderuje je w
interfejsie PR-a.

### Treść PR-a

Treść powinna być **zwięzła**, aby nie zaśmiecać podglądów na Slacku.

**Wymagane elementy** (w tej kolejności):
1. Job Story JTBD jako **pierwszy akapit** (1-3 linie, patrz
   `git-jtbd.md`) — **bez żadnego nagłówka nad nim**. Nagłówki sekcji
   (np. `## Podsumowanie`) wolno stawiać dopiero **pod** Job Story,
   nigdy nad nią.
2. Link `Fixes:` — musi być **bezwzględnie ostatnią linią** treści:
   - `Fixes: https://github.com/Dev10x-Guru/ksef-mcp/issues/NUMBER`
     (dla pracy powiązanej ze zgłoszeniem)
   - `Fixes: none — self-motivated refactor` (dla wewnętrznych
     usprawnień, funkcji lub eksperymentów bez powiązanego zgłoszenia)
   - NIE dodawaj ręcznie `---`, pustych linii ani separatorów po `Fixes:`

**Elementy opcjonalne** (zachowaj zwięzłość):
- Zwięzła lista commitów z linkami (jedna linia na commit)
- Krytyczny kontekst, który recenzenci muszą poznać od razu

**Czego nie umieszczać w treści**:
- Szczegółowe podsumowania (umieść w pierwszym komentarzu)
- Listy kontrolne implementacji (umieść w pierwszym komentarzu)
- Znane ograniczenia lub TODO (umieść w pierwszym komentarzu)

### Przykłady

**WYMÓG GŁOSU**: głos trzecioosobowy, z konkretną rolą dziedzinową,
jest obowiązkowy — nazwij konkretnego aktora i beneficjenta
(`**integrator chce** … **żeby księgowa mogła** …`). Nigdy nie używaj
pierwszej osoby („chcę") ani bezosobowego „użytkownik chce". Patrz
`references/git-jtbd.md` § Wymóg głosu oraz § Wybór aktora.

**ŹLE** — nagłówek przed Job Story (psuje zbieranie notatek
wydaniowych):
```markdown
## Podsumowanie

**Gdy** uzgadnia faktury, **księgowa chce** widzieć bieżący status
w KSeF, **żeby księgowa mogła** wcześnie wychwycić odrzucenia.

[Szczegóły...]

Fixes: ...
```

**DOBRZE** — Job Story jako bezwzględnie pierwszy element:
```markdown
**Gdy** uzgadnia faktury, **księgowa chce** widzieć bieżący status
w KSeF, **żeby księgowa mogła** wcześnie wychwycić odrzucenia.

[Szczegóły albo lista commitów — opcjonalnie...]

Fixes: ...
```

### Właściwy format

```markdown
**Gdy** klient MCP musi sprawdzić status faktury bez przeglądarki,
**integrator chce** odpytać KSeF wywołaniem narzędzia, **żeby księgowa
mogła** potwierdzić wysyłkę bez opuszczania okna rozmowy.

[`b3a015a`](REPO_URL/commit/HASH) ✨ 42 Umożliwia sprawdzenie statusu faktury
[`fec4999`](REPO_URL/commit/HASH) 📝 42 Opisuje nowe narzędzie

Fixes: https://github.com/Dev10x-Guru/ksef-mcp/issues/42
```

*Dlaczego?* Job Story JTBD musi być pierwszym akapitem, ponieważ
proces tworzenia release notes parsuje opisy PR-ów po pozycji.

*Wyjątek bootstrappingu:* PR wprowadzający nowy wymóg dotyczący
treści PR-a może sam go nie spełniać — reguła nie obowiązywała jeszcze
w chwili zgłoszenia tego PR-a.

## Znane ograniczenia automatyzacji: znaczniki JTBD

Polskie znaczniki JTBD (`**Gdy**`, `**chce**`, `**żeby ... mógł**`) są
wymagane przez standard projektu. Jednak walidator `mcp__plugin_Dev10x_cli__create_pr`
szuka dosłownie angielskich znaczników (`**When**`, `**wants to**`,
`**so ... can**`). Dopóki wtyczka Dev10x nie rozpoznaje polskich znaczników,
postępuj tak:

1. **Przy tworzeniu PR-a** (via `create_pr`): użyj angielskich znaczników.
   Walidator je zaakceptuje.
2. **Po otwarciu PR-a**: zmień znaczniki na polskie w treści PR-a
   (edycja przez interfejs GitHub). Release notes parser później czyta już
   polskie znaczniki z merge'a do main.

To nie jest błąd — to świadoma kompromis. Gdy wtyczka Dev10x zostanie
dostosowana do polskich znaczników, krok 1 stanie się zbędny (patrz
`references/git-jtbd.md` § UWAGA — ryzyko dla automatyzacji).

### Jeśli przegląd wykryje problemy

Jeśli Claude znajdzie problemy z kodem lub metadanymi podczas
przeglądu, Twój PR zostanie automatycznie przełączony na status
**draft**. Zapobiega to udostępnieniu przycisku merge, dopóki
zgłoszone problemy pozostają nierozwiązane.

Po poprawieniu wszystkich zgłoszonych problemów kliknij **„Ready for
review"** na stronie PR-a, aby ponownie uruchomić workflow przeglądu i
umożliwić merge po przejściu kontroli.

### Pierwszy komentarz w PR-ze (podsumowanie + lista kontrolna)

Szczegółowy kontekst dla recenzentów bez zaśmiecania podglądu na Slacku.

```markdown
### Summary

- Added the get_invoice_status MCP tool
- Wired it to the KSeF test-environment client
```

### Lista kontrolna PR-a

- [ ] Diff przejrzany samodzielnie
- [ ] Dokumentacja zaktualizowana, jeśli potrzeba
- [ ] Nie zostały żadne commity fixup

## Obsługa informacji zwrotnej z przeglądu

1. Twórz commity fixup dla każdego komentarza z przeglądu
2. Odwołaj się do komentarza w treści commita fixup
3. Odpowiedz na komentarz z SHA commita
4. Przed ostatecznym pushem zeskładaj wszystkie fixupy:
   ```bash
   git rebase -i --autosquash $(git merge-base main HEAD)
   git push --force-with-lease
   ```

Format commitów i konwencja nazywania branchy — patrz `git-commits.md`.
