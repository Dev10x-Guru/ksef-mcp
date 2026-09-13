# ksef-mcp

Lokalny serwer MCP do KSeF (Krajowy System e-Faktur), zbudowany przez Dev10x.Guru.

Uruchamiany na własnej maszynie przez `uvx ksef-mcp` — dane faktur nie
przechodzą przez żadną usługę pośredniczącą.

## Stan projektu

**Wczesny etap.** Repozytorium zawiera dziś wyłącznie szkielet pakietu i jedno
narzędzie diagnostyczne (`server_info`). Wszystko, co poniżej oznaczono jako
🚧 **planowane**, jeszcze nie istnieje w kodzie — opisujemy to, żeby kierunek był
jawny, nie żeby sugerować gotowość.

| Obszar | Stan |
|---|---|
| Pakiet, uruchamianie przez `uvx`, testy | ✅ działa |
| Wyszukiwanie i pobieranie faktur | 🚧 planowane |
| Wizualizacja PDF | 🚧 planowane |
| Komenda `onboarding` | 🚧 planowane |

## Co ten projekt robi

MVP jest wąski i celowo: **znajdź faktury za wybrany miesiąc i pobierz je.**

Mapa drogowa ma trzy etapy:

1. jeden podmiot, faktury **zakupowe**, wyłącznie odczyt,
2. ten sam podmiot, faktury sprzedażowe,
3. biura rachunkowe z przełączaniem podmiotów.

**Poza zakresem:** wysyłka faktur, korekty, zarządzanie uprawnieniami. To nie jest
zapomniane — to jest świadomie niezbudowane.

## Dla kogo

Odbiorcą jest **użytkownik techniczny**. Instalacja wymaga terminala, menedżera
wersji i ręcznej edycji pliku konfiguracyjnego klienta MCP. Nie udajemy, że jest
to instalacja dla osoby nietechnicznej — dystrybucja dla takiego odbiorcy
(instalator albo rozszerzenie do klienta) to osobny, przyszły etap.

## Wymagania wstępne

**Python 3.13.14** — przypięty dokładnie, nie zakresem (`.python-version` oraz
`requires-python` w `pyproject.toml`). `uv` pobierze ten interpreter sam, więc nie
trzeba instalować go ręcznie.

Zasada obowiązuje w całym projekcie: przypinamy konkretne wersje, nigdy zakresy —
także zależności. Rozjazd interpretera pociąga rozjazd rozwiązanych wersji
bibliotek, a `uvx` i tak rozwiązuje wersję za nas.

🚧 **Node 22.17.0 przez [fnm](https://github.com/Schniz/fnm)** — planowane, potrzebne
**wyłącznie** do generowania PDF-ów.

```powershell
winget install Schniz.fnm   # Windows
```

⚠️ Sama obecność pliku `.node-version` nie wystarczy. Automatyczne przełączanie
wersji wymaga `fnm env` w profilu powłoki — bez tego plik jest deklaracją bez
egzekucji i można pracować na innej wersji Node, nie wiedząc o tym.

Bez Node narzędzie **działa** i oddaje XML, CSV oraz listę faktur. Traci wyłącznie
PDF. To degradacja, nie awaria.

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

Po instalacji pierwszym krokiem jest `ksef-mcp onboarding` — sprawdzi
zależności, przeprowadzi przez konfigurację poświadczeń i wybór środowiska.

## Wizualizacja PDF

🚧 Planowane.

PDF-y będą generowane **oficjalnym generatorem Ministerstwa Finansów**
(`@akmf/ksef-fe-invoice-converter`, licencja MIT), zwendorowanym w
`src/ksef_mcp/vendor/`. Wynik jest tożsamy z tym, co daje portal MF — zweryfikowane
uruchomieniem, nie tylko lekturą kodu. Dokument niesie kod QR, link weryfikacyjny
i numer KSeF.

Generator obsługuje FA(1), FA(2), FA(3), UPO i PEF, ale **przetestowaliśmy wyłącznie
FA(3)**. Pozostałe schematy traktujemy jako niepotwierdzone.

## Bezpieczeństwo danych

Token KSeF nie powinien trafiać do pliku konfiguracyjnego klienta MCP — te pliki są
zwykłym tekstem na dysku. Docelowo serwer będzie czytał poświadczenia z keyringu
systemowego.

Domyślnym środowiskiem jest TEST. Produkcja wymaga świadomego włączenia.

## Limity zapytań

API KSeF ogranicza liczbę zapytań o metadane. Krążące wartości to 8/s, 16/min
i 20/h, ale **nie potwierdziliśmy ich** — nie opierajcie na nich planowania, dopóki
nie zostaną zweryfikowane wobec dokumentacji MF.

## Rozwój

`make help` wypisuje wszystkie dostępne komendy.

```bash
make install              # uv sync --group dev
make hooks                # instalacja hooków pre-commit i commit-msg
make test                 # uv run pytest z pokryciem
make lint                 # pre-commit na całym drzewie
make coverage-report      # testy + otwarcie raportu HTML
make upgrade-requirements # uv lock --upgrade
make build-requirements   # eksport do requirements/*.txt
```

Linting i formatowanie idą wyłącznie przez pre-commit — to ta sama ścieżka,
która blokuje commit, więc lokalny przebieg nie rozjeżdża się z hookiem.

Pokrycie testami jest egzekwowane na poziomie 100% (`fail_under` w `pyproject.toml`),
więc lokalny przebieg i CI stosują identyczny próg.

## Licencje

Projekt jest na licencji **AGPL-3.0-only** — pełny tekst w pliku [LICENSE](LICENSE).

🚧 Zwendorowany generator PDF Ministerstwa Finansów jest osobnym artefaktem na
licencji **MIT**. Jego nota licencyjna będzie leżeć obok niego w
`src/ksef_mcp/vendor/` i dotyczy wyłącznie tego pliku, nie reszty projektu.
