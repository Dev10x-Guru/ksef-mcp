# Wytyczne dotyczące commitów i branchy w git

Standardy commitów i branchy w tym repozytorium.

## Zasady kierowania branchy

- **Wszystkie PR-y**: zawsze kieruj na `main` — ten projekt nie ma
  brancha `develop`; `main` jest jedynym pniem.
- **Zasada CLI**: zawsze przekazuj `--base main` do `gh pr create`.

*Dlaczego?* Cała praca kierowana jest na `main`, dzięki czemu bramki
jakości (CI, przegląd kodu) działają na tym samym branchu, który
trafia na produkcję.

## Konwencja nazywania branchy

Format: `username/NUMER-ZGŁOSZENIA/krótki-opis`
Worktree: `username/NUMER-ZGŁOSZENIA/nazwa-worktree/krótki-opis`

Przykłady:
- `janusz/42/add-invoice-status-tool`
- `maria/57/fix-token-refresh`
- `janusz/63/ksef-mcp-3/port-github-actions-ci` (worktree)

## Format treści commita

### Struktura

```
<gitmoji> <ISSUE-NUMBER> <short description>

<problem explanation - what was wrong and why it needed fixing>

Solution:
- <change 1>
- <change 2>

Fixes: <ISSUE-NUMBER>
```

### Zasada pisania tytułu

Skup się na tym, co zmiana **umożliwia**, a nie na tym, co zmienia w
kodzie. Pełny format Job Story i przykłady — patrz `git-jtbd.md`.

**Funkcje widoczne dla użytkownika** (wymagane):
- Źle: `Add get_invoice_status MCP tool` (implementacja)
- Dobrze: `Enable checking invoice status from an MCP client` (rezultat)

**Praca porządkowa** (dokumentacja, narzędzia — preferowane, ale nie
wymagane):
- Dopuszczalne: `Add missing ruff workflow`
- Też dobrze: `Prevent lint regressions in CI`

### Zasady

1. **Linia tytułu**: maks. 72 znaki (gitmoji + spacja + numer zgłoszenia + spacja + opis)
2. **Linie treści**: maks. 72 znaki każda
3. **Gitmoji**: użyj znaku emoji, nie formatu `:code:`
4. **Bez współautorstwa**: nigdy nie dodawaj stopki „Co-Authored-By: Claude"

### Tabela gitmoji

| Emoji | Kod | Do czego |
|-------|------|---------|
| ✅ | `:white_check_mark:` | Dodawanie/poprawianie testów |
| 🐛 | `:bug:` | Poprawki błędów |
| ♻️ | `:recycle:` | Refaktoryzacja |
| ✨ | `:sparkles:` | Nowe funkcje |
| 📝 | `:memo:` | Dokumentacja |
| 🔒 | `:lock:` | Poprawki bezpieczeństwa |
| ⚡ | `:zap:` | Wydajność |
| 🔧 | `:wrench:` | Konfiguracja |
| 🔖 | `:bookmark:` | Podbicie wersji |
| 🩹 | `:adhesive_bandage:` | Drobne poprawki |
| 🔥 | `:fire:` | Usuwanie kodu/plików |

### Przykładowy commit

```
🐛 42 Fix token refresh race under concurrent MCP calls

get_access_token() could issue two refresh requests when two tool
calls raced past the expiry check, invalidating the first token.

Solution:
- Guard refresh with an asyncio lock
- Add regression test for concurrent refresh

Fixes: 42
```

## Commity atomowe

Każdy commit powinien odzwierciedlać **jedną logiczną zmianę**:

- ✅ Jedna funkcja, jeden commit
- ✅ Jedna poprawka błędu, jeden commit
- ✅ Jedna refaktoryzacja, jeden commit
- ❌ Wiele niezwiązanych zmian w jednym commicie
- ❌ Niedokończona praca w commicie

### Kolejność commitów

Gdy funkcja dotyka wielu warstw, twórz commity w kolejności zależności:

1. Narzędzia pomocnicze (bez zależności)
2. Konfiguracja i infrastruktura
3. Główna implementacja
4. Dokumentacja i reguły
5. Testy

Wytyczne dotyczące PR-ów i porządkowania branchy — patrz `git-pr.md`.
