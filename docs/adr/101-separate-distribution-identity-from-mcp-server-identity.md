# ADR-101: Rozdzielenie tożsamości dystrybucji od tożsamości serwera MCP

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** janusz-skonieczny
- **Authored-by:** human
- **Reviewed-by:** —
- **Sources:** wymagania publikacji pakietu na PyPI pod nową nazwą oraz stabilności konfiguracji klientów MCP

## Kontekst

Projekt publikuje serwer MCP na PyPI, a klienci (m.in. Claude Desktop) 
konfigurują się, wskazując nazwę serwera w `.mcp.json` czy `claude_desktop_config.json`. 

Nazwa dystrybucji (`name` w `pyproject.toml`) wcześniej służyła jednocześnie jako:
1. Identyfikator na PyPI (`ksef-mcp`)
2. Tożsamość serwera MCP, na którą klienci się konfigurują (`SERVER_NAME`)

Przy publikacji na PyPI w przestrzeni nazw organizacyjnej wymagana była zmiana 
nazwy dystrybucji z `ksef-mcp` na `ksef-dev10x-guru`, żeby:
- uniknąć kolizji nazw z innymi projektami
- wyrazić przynależność do Dev10x.Guru
- ułatwić użytkownikom znalezienie pakietu w katalogu

Jednak zmiana nazwy dystrybucji w `pyproject.toml` natychmiast łamie:
- odczyt wersji: `importlib.metadata.version()` rozstrzyga po nazwie dystrybucji, a nie po `SERVER_NAME`
- konfiguracje klientów: `uvx ksef-mcp` traciłaby wpis w `uv.lock`

## Decyzja

Rozdzielić tożsamość dystrybucji od tożsamości serwera MCP poprzez dwie stałe:

```python
# src/ksef_mcp/metadata.py
SERVER_NAME: Final[str] = "ksef-mcp"
DISTRIBUTION_NAME: Final[str] = "ksef-dev10x-guru"
VERSION: Final[str] = version(DISTRIBUTION_NAME)
```

- `SERVER_NAME` (`ksef-mcp`) pozostaje niezmienną tożsamością protokołu MCP — 
  ona, a nie nazwa dystrybucji, jest zapisana w konfiguracjach klientów
- `DISTRIBUTION_NAME` (`ksef-dev10x-guru`) to nazwa na PyPI, którą 
  `importlib.metadata.version()` używa do odczytania wersji
- Skrypt konsolowy w `pyproject.toml` wskazuje na `DISTRIBUTION_NAME`:
  ```toml
  [project.scripts]
  ksef-dev10x-guru = "ksef_mcp.server:main"
  ```

Dodatkowo dodano jawną konfigurację dla hatchling:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/ksef_mcp"]
```

ponieważ hatchling automatycznie wnioskuje pakowany katalog z nazwy dystrybucji, 
a zmiana nazwy zerwała tę heurystykę.

### Dlaczego rozdzielenie zamiast alternatyw?

| Podejście | Zalety | Wady |
|-----------|--------|------|
| **Rozdzielenie** | Zachowuje stabilność konfiguracji klientów; pozwala zmienić nazwę dystrybucji bez zmian u użytkownika; wyraźne rozróżnienie między packaging identity a protocol identity | Dodatkowa złożoność (dwie stałe zamiast jednej); może mylić nowych współtwórców |
| Zmiana konfiguracji u klientów | Jedna nazwa wszędzie | Wszystkie konfiguracje klientów traciły ważność; migracja byłaby obowiązkowa |
| Zarezerwowanie nazwy na PyPI | Nie wymaga zmian w kodzie | `ksef-dev10x-guru` i `ksef-mcp` to dwa różne pakiety; chaos w szukaniu; brak wyrażenia przynależności organizacyjnej |

## Uzasadnienie

Separacja realizuje zasadę SOLID: Single Responsibility. Dystrybucja to 
artefakt publikacji (odpowiedzialność PyPI i `uv`), tożsamość serwera to 
aspekt protokołu (odpowiedzialność klientów). Zmuszanie jednej nazwy do 
obsługiwania obu ról powoduje, że każda zmiana jednej powinna zmienić drugą — 
co jest fałszem. Tu dwie odpowiedzialności są niezawisłe.

Dodatkowo: `importlib.metadata` jest standardem biblioteki, a jego zachowanie 
(odczyt po nazwie dystrybucji, nie po nazwie serwera) jest ustalone. Zamiast 
walczyć z biblioteką, pracujemy z jej naturą.

## Konsekwencje

**Pozytywne:**
- Konfiguracje klientów (`uvx ksef-dev10x-guru` z `args: ["ksef-dev10x-guru"]`) 
  są odłączone od wewnętrznej tożsamości serwera
- Możliwa zmiana nazwy dystrybucji na PyPI bez zmuszania użytkowników do zmian
- Wyraźne rozróżnienie między sztuką publikacji a sztuką protokołu
- Odkryta podczas implementacji złożoność (hatchling wymaga jawnej konfiguracji) 
  jest teraz jawna zamiast ukrytej

**Negatywne:**
- Dwie stałe zamiast jednej; nowy współtwórca musi wiedzieć, którą zmienić w jakim kontekście
- Hatchling wymaga jawnej konfiguracji, zamiast polegać na wnioskowania
- Komentarz wyjaśniający *dlaczego* `DISTRIBUTION_NAME` istnieje (w `metadata.py`) 
  dodaje szum do małego pliku

## Powiązane

- [PR #14](https://github.com/Dev10x-Guru/ksef-mcp/pull/14) — zmiana nazwy dystrybucji
- [GH-13](https://github.com/Dev10x-Guru/ksef-mcp/issues/13) — zgłoszenie motywujące publikację na PyPI
- [ADR-100](100-decision-provenance-and-adversarial-re-derivation.md) — procedura proweniencji decyzji w tym repozytorium
