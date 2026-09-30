# ADR-101: Przepływ wiedzy dziedzinowej do agenta Claude Code poprzez skill-e

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** janusz-skonieczny
- **Authored-by:** agent (claude-haiku-4-5-20251001)
- **Reviewed-by:** —
- **Sources:** Integracja z Claude Code runtime, istniejące konwencje JTBD w `references/git-jtbd.md`, ograniczenia bezpieczeństwa KSeF z `CLAUDE.md`

## Kontekst

Agenty Claude Code obsługujące KSeF muszą opierać się na pięciu zasadach dziedzinowych (zob. `CLAUDE.md`):
1. Odpowiadaj z lokalnego archiwum, nie pytając API
2. Synchronizacja ma własny rytm i bez ponawiania po odmowie
3. Treść faktury nie wchodzi do kontekstu modelu
4. Każda odpowiedź nazwana środowisko (test/demo/produkcja)
5. Zapytania do API niosą koszt godzinowy — brak zgadywania

Bez przekazania tych zasad do agenta księgowa musi zgadywać, co kosztuje budżet zapytań. Jedynym kanałem do edukacji agenta jest plik `SKILL.md` czytany przez środowisko Claude Code w chwili, gdy agent jest wywoływany.

Samo wpisanie skilla na sztywno byłoby ostateczne i niemożliwe do aktualizacji. Seria ręcznych edycji byłaby podatna na omyłkę. Brakuje też rozwiązania na konflikt między automatyczną aktualizacją serwera a lokalnymi zmianami bookkeeper'a.

## Decyzja

Przyjąć model, w którym:

1. **Skill jest plikiem przechowywanych reguł**: Zamiast hardcoded'ować reguły w dokumentacji, serwer emituje plik `SKILL.md` zawierający treść dla człowieka i metadane dla środowiska.

2. **Zainstalowanie skilla jest jawne, nie domyślne**: Komenda `ksef-mcp skill install --scope user|project` zapisuje skill:
   - Przy `--scope user` do `~/.claude/skills/ksef-mcp/SKILL.md`
   - Przy `--scope project` do `./.claude/skills/ksef-mcp/SKILL.md`
   
   Zakres **nie ma domyślnie** — `uvx` bywa uruchamiany z przypadkowego katalogu; cicho wybrane miejsce byłoby ostatnim, w którym szukano by pliku.

3. **Aktualizacja jest bezpieczna**: Gdy skill już istnieje:
   - Porównaj zawartość i wyznacz różnicę
   - Pokaż różnicę księgowej (unified diff)
   - Dopiero po potwierdzeniu nadpisz plik
   - Odmowa kończy się kodem wyjścia 5, nie milczącą ignorancją

4. **Metadane są w języku angielskim**: Pola `name` i `description` czyta runtime agenta, jak docstringi narzędzi MCP. Treść dla człowieka (sekcje Pytania, Synchronizacja, Treść faktury, Środowisko, Komendy) jest po polsku.

5. **Versioning jest mocnikiem**: Plik niesie wersję serwera, dla którego powstał skill. Po aktualizacji serwera księgowa uruchamia `skill install` ponownie — sam serwer nie podmienia pliku bez pytania.

### Dlaczego skill file zamiast alternatyw?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Skill file (wybrany)** | Edytowalny przez księgową, nie znika przy aktualizacji serwera, integruje się z edytorem Claude Code, reguły nie zaśmiecają logów, samo wpisanie serwera nic nie zmienia | Wymaga dodatkowego kroku (install), może zapaść w zapomnienie |
| Environment variables | Jeden krok przy startup | Nietrwały (przepada przy restarcie), niezejrzysta dla człowieka |
| Hardcoded w instrukcji systemowej | Zawsze aktualny przy aktualizacji | Agent nie może wybrać zakresu, konflikt z lokalnymi zmianami |
| Wbudowany w konfigurację serwera | Trzyma wiedzę w jednym miejscu | Mieszanina zagadnień (KSeF + procedury Claude), chleb z białą linią — mył się mogą co najmniej dwa |
| Wiki / dokumentacja online | Uniwersalny dostęp | Wymaga połączenia, zmieniający się URL, bez wersjonowania względem serwera |

## Uzasadnienie

Plik na dysku jest czytany przez narzędzie Claire Code bez żadnej zmowy serwera — sam serwer go nie edytuje ani nie usuwa. Daje to księgowej możliwość:
- Dostosowania reguł do swoich procedur
- Bezpiecznego odłączenia od serwera (skill zostaje)
- Wyboru, kiedy aktualizować (nie automatycznie)
- Przeglądu zmian (diff) przed zaakceptowaniem

Wymaganie jawnego zakresu (bez default'u) unika molestującego błędu: `uvx ksef-mcp` uruchamiane z `/tmp` byłoby zainstalować skill do `/tmp/.claude`, nigdy nieznalezionego ponownie.

Potwierdzanie zmian (unified diff + prompt) jest kosztem akceptowalnym: skill zmienia się bardzo rzadko, a bezpieczeństwo przed cichym utraty edycji jest krytyczne w narzędziach dla księgowych.

## Konsekwencje

**Pozytywne:**
- Księgowa edukuje agenta bez zaglądania do kodu serwera
- Aktualizacje serwera nie przełamują lokalnych zmian
- Jawne kroki (install, confirm) unikają niespodzianek
- Skill zostaje dostępny i po odłączeniu serwera
- Wersjonowanie skilla względem serwera unika rozbieżności (test vs produkcja)
- Oszczędzenie godzinowego budżetu zapytań dzięki wychowaniu agenta

**Negatywne:**
- Dodatkowy krok (`ksef-mcp skill install`) przed pierwszym użyciem
- Może zapaść w zapomnienie, jeśli serwer będzie aktualizowany bez powtórnego instalowania
- Konflikt zdań między zainstalowanym skillem a zmienioną instrukcją systemową (prowadzi do ADR o hierarchii źródeł wiedzy)
- Rozmawianie kilka vez „czy nadpisać" może być uciążliwe dla użytkownika, który chce automatycznych aktualizacji

## Powiązane

- [ADR-100](100-decision-provenance-and-adversarial-re-derivation.md) — decyzje dotyczące KSeF są ważone przez człowieka i wymagają periodycznej weryfikacji
- [CLAUDE.md](../CLAUDE.md#zasady-bezpieczeństwa-ksef-nienegocjowalne) — pięć zasad behawioralnych, które skill ucieleśnia
- GH-34 — motywacja: księgowa powinna uniknąć zgadywania, które kosztuje budżet
- `.claude/skills/ksef-mcp/SKILL.md` — wygenerowana zawartość skilla po instalacji
