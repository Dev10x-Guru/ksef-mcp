# Dziennik zmian

Format wzorowany na [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/);
wersjonowanie zgodne z [SemVer](https://semver.org/lang/pl/).

Sekcję `## Bez wydania` prowadzi człowiek. `bin/release.py` przenosi jej
treść pod nowy numer wersji i odmawia wydania, gdy sekcja jest pusta —
wydanie bez opisu zmian jest gorsze niż brak dziennika, bo wygląda na
udokumentowane.

## Bez wydania

### Dodane

- Komenda `ksef-mcp onboarding` prowadząca przez konfigurację: kontrola
  Node, jawny wybór magazynu keyringu, wybór środowiska KSeF, zapis tokenu
  i katalog na faktury z uprawnieniami `0700` ([GH-4]).
- Komenda `ksef-mcp doctor` sprawdzająca warunki wstępne bez sięgania
  do KSeF ([GH-4]).
- Komendy `ksef-mcp token set|delete|status` obsługujące token w keyringu.
  Token wchodzi bez echa, nigdy nie jest argumentem procesu i nigdy nie
  jest pokazywany — tylko długość i końcówka ([GH-4]).
- Komenda `ksef-mcp verify` potwierdzająca połączenie z KSeF i pokazująca
  ostatnie faktury zakupowe. Osobna od onboardingu, bo każde zapytanie
  wydaje godzinowy budżet ([GH-4]).
- Ścieżka awaryjna przez zmienną `KSEF_TOKEN` dla maszyn bez keyringu
  (headless, WSL, kontener) ([GH-4]).
- Plik `.node-version` przypinający Node do wersji, na której zweryfikowano
  renderowanie ([GH-4]).

### Zmienione

- Nazwa dystrybucji wraca na `ksef-mcp`, przywracając decyzję D-015.
  Skrypt konsolowy wskazuje teraz `ksef_mcp.cli:main`; `ksef-mcp` bez
  argumentów nadal uruchamia serwer MCP na stdio, więc konfiguracje
  klientów pozostają bez zmian ([GH-20]).
- Marker `ksef_live` zarejestrowany w `pyproject.toml` i odfiltrowany
  z domyślnego przebiegu testów ([GH-4]).

### Bezpieczeństwo

- Wbudowane ponawianie żądań w `ksef2` ograniczone do jednej próby.
  Jego okno wynosi cztery sekundy wobec limitów liczonych w minutach, więc
  pętla nie doczekałaby końca limitu — dokładałaby tylko prób do wzorca,
  który Ministerstwo Finansów czyta jako obchodzenie limitu ([GH-4]).
- Środowisko KSeF podawane jawnie przy każdym konstruowaniu klienta,
  ponieważ domyślnym w SDK jest produkcja. Domyślnym w konfiguracji
  narzędzia jest środowisko testowe ([GH-4]).
- Plik konfiguracyjny powstaje od razu z trybem `0600`, bez okna między
  zapisem treści a nadaniem uprawnień ([GH-4]).

[GH-4]: https://github.com/Dev10x-Guru/ksef-mcp/issues/4
[GH-20]: https://github.com/Dev10x-Guru/ksef-mcp/issues/20
