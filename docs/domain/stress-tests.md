# Architecture Stress Tests

> Append-only. Każdy scenariusz waliduje architekturę.

## Stable Core

Elementy, które nie powinny się zmienić przy rozszerzeniach etapu 2 i 3:

- `NumerKSeF` jako klucz naturalny deduplikacji.
- Niezmiennik `WpisArchiwum` egzekwowany przez `temp → rename`.
- Indeks deduplikacji odrębny od treści.
- Granica: tool zwraca ścieżki i metadane, nigdy treść faktury.
- Kontekst podmiotu jako własność poświadczenia.

---

## ST-1 — Miesiąc z 5000 faktur

**Scenariusz:** biuro pobiera miesiąc dużego klienta; lista przekracza
sufit godzinowy zapytań metadanych.

| Etap | Wynik |
|---|---|
| Uwierzytelnienie | ZERO zmian |
| Zapytanie o listę | ZERO zmian przy `pageSize=250` [D-010] |
| Paginacja | **ŁAMIE SIĘ** powyżej ~5000 pozycji/h |
| Pobieranie treści | ZERO zmian |
| Deduplikacja | ZERO zmian |

**Szew:** ścieżka `POST /invoices/exports`. Wymaga szyfrowania, więc
wciąga AES/RSA do MVP czysto odczytowego.

**Ocena:** niezmiennik Archiwum trzyma — deduplikacja po numerze KSeF
działa niezależnie od tego, czy faktury przyszły listą czy paczką
eksportu. Szew leży we właściwym miejscu: wymiana **źródła** listy, nie
zmiana modelu.

**Do rozstrzygnięcia:** czy próg przełączenia na eksport jest
automatyczny, czy jawną decyzją człowieka. Persona księgowej wskazała, że
„interaktywna czy wsadowa" to decyzja techniczna spadająca na księgową —
sygnał, że powinna być automatyczna.

---

## ST-2 — Trzy dni pracy agenta z fan-outem subagentów

**Scenariusz:** agent AI uruchamia serwer per sesja; subagenty podnoszą
własne instancje; nikt nie sprząta.

**Precedens:** inny serwer MCP w tej samej konfiguracji urósł do **114
procesów i 3,79 GB RAM po trzech dniach**.

**Ocena:** model domenowy jest tu bez znaczenia — to architektura procesu.
`uvx` w procesie ma dokładnie tę charakterystykę [D-014].

**Szew:** cykl życia procesu musi być zaprojektowany w etapie 1, nie
dołożony później. Koszt teraz: mały. Koszt później: zmiana sposobu
uruchamiania u wszystkich użytkowników.

---

## ST-3 — Headless / WSL / kontener bez sesji D-Bus

**Scenariusz:** użytkownik uruchamia serwer tam, gdzie backend keyringu
nie ma sesji graficznej.

**Ocena:** **najpoważniejszy tryb śmierci projektu.** Jeśli keyring
poprosi o hasło interaktywnie, prompt trafia na stdio i **zawiesza
transport MCP**. Nikt nigdy nie dociera do „znajdź faktury za wrzesień",
a model domenowy nie ma znaczenia.

**Obrona [D-004]:** wykrycie niedostępnego backendu → natychmiastowy błąd
z instrukcją. Nigdy prompt. Zmienna środowiskowa jako jawna ścieżka
awaryjna.

**POTWIERDZONE WYKONANIEM (2026-09-13) — połowicznie.**

Uruchomiono `keyring` na Pythonie 3.13.14 z otoczeniem pozbawionym
`DBUS_SESSION_BUS_ADDRESS`, `DISPLAY` i `XDG_RUNTIME_DIR`:

```
wybrany backend: keyring.backends.fail.Keyring
czy null backend: True
```

`SecretService` **znika z listy dostępnych backendów**. Wykrycie nie
wymaga ani odczytu, ani zapisu, ani żadnej interakcji:

```python
isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
```

**Co to zamyka:** przypadek **braku backendu** (headless, WSL, kontener).
Obrona z [D-004] jest wykonalna jedną linijką przy starcie.

**Czego NIE zamyka — ryzyko rezydualne:** przypadek, w którym backend
**istnieje, ale jest zablokowany** (kwallet lub gnome-keyring wymagający
odblokowania hasłem). Wtedy `get_keyring()` zwróci działający backend z
priorytetem > 0, a dopiero `get_password()` zawiesi się na oknie
odblokowania — czyli na stdio zawiesi transport. To osobny tryb awarii,
niezweryfikowany.

---

## ST-4 — Faktura wpada do zamkniętego miesiąca

**Scenariusz:** JPK poszedł, faktura kosztowa dociera później.

**Ocena:** KSeF nie zna pojęcia zamknięcia okresu i nie powstrzyma
napływu. Niezmiennik Archiwum trzyma — ponowne odpytanie okresu zwraca
deltę, nie duplikaty [D-005].

**Czego model NIE rozstrzyga:** ujęcia podatkowego. To decyzja człowieka;
narzędzie ma ją zasygnalizować, nie podjąć.

---

## ST-5 — Etap 3: biuro z kilkudziesięcioma podmiotami

**Scenariusz:** przełączanie kontekstu między klientami w jednej sesji
agenta.

| Element | Wynik |
|---|---|
| `NumerKSeF`, deduplikacja | ZERO zmian |
| `WpisArchiwum` | ZERO zmian |
| Kontekst podmiotu | **ADDYTYWNY** — wiele poświadczeń [D-009] |
| Katalog docelowy | **ADDYTYWNY** — osobny per NIP |
| Ślad audytowy | **ADDYTYWNY** — podstawa uprawnienia |

**Ryzyko nr 1 wskazane przez compliance:** pomieszanie kontekstów —
agent pobiera faktury klienta A, odpowiadając o kliencie B. **API nie
zgłosi błędu**, bo uprawnienie istnieje. Wektor: wspólny katalog i wspólne
okno czatu.

**Obrona:** twarde przełączenie kontekstu — osobny katalog, osobny log,
wyczyszczony kontekst poprzedniego klienta. Nigdy dwa podmioty w jednym
katalogu.

**Ocena:** szew przeniesiony z zapytania do poświadczeń [D-009] trzyma.
Gdyby został tam, gdzie był pierwotnie (parametr zapytania), etap 3
wymagałby refaktoru, a nie rozszerzenia.
