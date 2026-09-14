# Nota licencyjna — artefakt obcy

Nota dotyczy **wyłącznie** pliku `ksef-fe-invoice-converter.1.1.39.js`
w tym katalogu. Reszta projektu `ksef-mcp` jest objęta licencją
**AGPL-3.0-only** (plik `LICENSE` w korzeniu repozytorium).

## Co to za plik

Zbudowany moduł generatora wizualizacji faktur KSeF, wytwarzany przez
Ministerstwo Finansów i serwowany publicznie przez portal weryfikacyjny
pod adresem:

```
https://qr.ksef.mf.gov.pl/client-app/pdf-lib/ksef-fe-invoice-converter.1.1.39.js
```

Nazwa pliku niesie wersję generatora (`1.1.39`) i celowo jest zachowana
bajt w bajt taka, jak na portalu — po niej rozpoznaje się podmianę.
Tę samą wersję wypisuje stopka każdego wygenerowanego PDF-a
(„ksef-pdf-generator - wersja 1.1.39").

| | |
|---|---|
| Pobrano | 2026-09-14 |
| Rozmiar | 3 135 550 bajtów |
| SHA-256 | `52210230e5c6ee8d5195541eacb5b81e9cb64fc51845118ec39484d5f7d0fed8` |
| `Last-Modified` u źródła | 2026-09-08 20:12:54 GMT |

## Licencja

Moduł jest rozpowszechniany na licencji **MIT**, której treść bundel
niesie w sobie. Licencja MIT pozwala na włączenie artefaktu do projektu
objętego licencją silniej copyleftową, pod warunkiem zachowania noty —
i to jest powód istnienia tego pliku.

Plik jest wersjonowany w repozytorium i trafia do dystrybucji razem
z pakietem, więc nota musi jechać razem z nim, a nie zostać w repo.

## Dlaczego wersjonujemy, zamiast pobierać

Pobieranie w czasie działania lub przy budowaniu uzależniłoby
renderowanie od kanału bez kontraktu i odebrało determinizm buildu.
Zwendorowany artefakt renderuje offline i nie przestaje działać, gdy
Ministerstwo wyda nową wersję. Aktualizacja jest krokiem listy
kontrolnej przed wydaniem, a nie automatem — patrz `D-027` w
`docs/domain/decisions.md`.

**Uwaga przy ponownym pobraniu:** portal dwukrotnie zerwał połączenie
w połowie transferu. Pobieraj z wznawianiem (`curl -C -`) i sprawdź
rozmiar, zanim uznasz plik za kompletny.

## Czego w tym katalogu nie ma

Plik `package.json` obok bundla deklaruje wyłącznie `"type": "module"`.
Jest potrzebny, bo bundel jest modułem ES o rozszerzeniu `.js`, a bez
tej deklaracji Node potraktowałby go jako CommonJS i odmówiłby
załadowania. Nie jest to pakiet npm i nie ma zależności.

Kod uruchamiający generator (`render.mjs`) **nie leży tutaj** — jest
nasz i objęty licencją AGPL-3.0-only, więc mieszka w
`src/ksef_mcp/node/`. Ten katalog trzyma wyłącznie artefakty obce.
