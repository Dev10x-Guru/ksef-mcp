# ksef-mcp

Lokalny serwer MCP do KSeF (Krajowy System e-Faktur), zbudowany przez Dev10x.Guru.

Uruchamiany na własnej maszynie przez `uvx` — dane faktur nie przechodzą przez żadną
usługę pośredniczącą.

## Stan projektu

Wczesny etap. Obecnie repozytorium zawiera wyłącznie szkielet pakietu i jedno narzędzie
diagnostyczne (`server_info`). Narzędzia do wyszukiwania i pobierania faktur nie są
jeszcze zaimplementowane — model domenowy jest w trakcie ustalania.

## Wymagania

- Python 3.13.14 — pin dokładny, nie dolna granica (`.python-version`
  oraz `requires-python` w `pyproject.toml`). `uv` pobierze ten interpreter
  sam, więc nie trzeba instalować go ręcznie.

  Zasada obowiązuje w całym projekcie: przypinamy konkretne wersje, nigdy
  zakresy — także zależności. Rozjazd interpretera pociąga rozjazd
  rozwiązanych wersji bibliotek, a `uvx` i tak rozwiązuje wersję za nas.
- [`uv`](https://docs.astral.sh/uv/)

## Instalacja i uruchomienie

Serwer komunikuje się przez stdio i jest uruchamiany przez klienta MCP, nie ręcznie.

Konfiguracja w kliencie MCP (`.mcp.json`, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ksef": {
      "command": "uvx",
      "args": ["ksef-mcp"]
    }
  }
}
```

Aby uruchomić wersję z lokalnego katalogu roboczego zamiast z PyPI:

```json
{
  "mcpServers": {
    "ksef": {
      "command": "uvx",
      "args": ["--from", "/ścieżka/do/ksef-mcp", "ksef-mcp"]
    }
  }
}
```

## Rozwój

```bash
uv sync --group dev
uv run pytest
pre-commit run --all-files
```

Linting i formatowanie idą wyłącznie przez pre-commit — to ta sama ścieżka,
która blokuje commit, więc lokalny przebieg nie rozjeżdża się z hookiem.

Pokrycie testami jest egzekwowane na poziomie 100% (`fail_under` w `pyproject.toml`),
więc lokalny przebieg i CI stosują identyczny próg.

## Bezpieczeństwo danych

Token KSeF nie powinien trafiać do pliku konfiguracyjnego klienta MCP — te pliki są
zwykłym tekstem na dysku. Docelowo serwer będzie czytał poświadczenia z keyringu
systemowego.

## Licencja

AGPL-3.0-only. Pełny tekst w pliku [LICENSE](LICENSE).
