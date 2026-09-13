# Domain Documentation — ksef-mcp

Lokalny serwer MCP do KSeF, uruchamiany w procesie przez `uvx ksef-mcp`,
zbudowany na bazie biblioteki klienta KSeF z PyPI.

Sześć powierzchni nazewniczych zbiega się dziś do `ksef-mcp`, ale
pozostaje rozdzielonych — patrz [D-035], w szczególności niezmiennik, że
**usługa w keyringu i katalogi XDG nie idą za nazwą dystrybucji**.

## Mapa dokumentów

| Plik | Odpowiada na pytanie | Mutowalność |
|---|---|---|
| `model.md` | Czym domena JEST teraz — typy, agregaty, konteksty | Mutable |
| `decisions.md` | Dlaczego wybraliśmy to, co wybraliśmy | Append-only |
| `glossary.md` | Co znaczą terminy (język wszechobecny) | Mutable |
| `stress-tests.md` | Co zwalidowaliśmy | Append-only |
| `epics.md` | Co budujemy — tickety z JTBD | Mutable |
| `calculator.md` | Jak liczymy (jeśli dotyczy) | Mutable |
| `workshops/NNN-*.md` | Co się wydarzyło na sesji | Immutable |

## Konwencje odsyłaczy

- Decyzje: `[D-NNN]`
- Terminy glosariusza: `[G:termin]`
- ADR: `ADR-NNN`
- Scenariusze stress-testów: `ST-N`
