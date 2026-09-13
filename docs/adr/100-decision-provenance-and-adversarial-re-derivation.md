# ADR-100: Prowenienacja decyzji i adwersarialna ponowna derywacja

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** dotychczasowa praktyka na kodowych bazach tworzonych przez AI;
  procedura jest tu zaimplementowana przez `bin/check-adr-numbers.py`,
  `bin/check-adr-drift.py`, `bin/decision_provenance.py` oraz
  `bin/rederive-sample.py`

## Kontekst

Ta baza kodu jest pisana niemal wyłącznie przez agentów AI, commitujących
pod tożsamością git operatora-człowieka. Wynikają z tego dwie
konsekwencje, obie obserwowane w praktyce na bazach kodu o takim
kształcie:

1. **Autorstwo jest strukturalnie nie do ustalenia po fakcie.** Pole
   `Deciders:` wypełnia ten, kto tworzy plik, więc nie da się nim
   odróżnić „człowiek to zdecydował" od „agent wpisał nazwisko
   człowieka". Prowenienacja zapisana w chwili decyzji jest tania;
   odtworzenie jej później jest niemożliwe.
2. **Przegląd działa tylko w jedną stronę.** Decyzja AI dostaje ludzkiego
   recenzenta. Decyzja człowieka nie dostaje niczego — nawet gdy grunt
   pod nią się przesuwa. Nic nie sprawdza ponownie, czy implementacja
   wciąż ją honoruje, w żadnym z obu kierunków.

Ponieważ niemal każdy commit tutaj jest autorstwa agenta, oznaczanie
autorstwa AI to niemal wszechobecny szum. Istotnym celem nie jest
księgowanie autorstwa, lecz **ochrona podzbioru decyzji ważonych przez
człowieka przed cichym odwróceniem przez AI**.

## Decyzja

Przyjąć procedurę prowenienacji decyzji złożoną z pięciu części.
Autorstwo jest zapisywane w chwili decyzji, a *obydwa* kierunki
podejmowania decyzji podlegają przeglądowi.

1. **Pola nagłówka prowenienacji** (zob. [TEMPLATE.md](TEMPLATE.md)):
   - `Authored-by:` — `human` albo `agent (<identyfikator modelu>)`.
     Brak oznaczenia oznacza, że decyzję podjęło AI, co jest tu
     wartością domyślną.
   - `Reviewed-by:` — nazwani człowiek/ludzie, którzy przeczytali cały
     tekst przed akceptacją. Puste oznacza **niezrecenzowane**; ADR
     niezrecenzowany nie może rościć sobie statusu `Accepted`.
   - `Sources:` — inspiracje i źródła pochodzenia wzorców. Nieprzejrzyste
     odwołania są dozwolone, żeby zapożyczone założenia były widoczne,
     zamiast sprawiać wrażenie wywiedzenia z pierwszych zasad.
   - `Deciders:` — jeden kanoniczny ciąg znaków na osobę.

   To `Authored-by: human` albo niepuste `Reviewed-by:` oznacza decyzję
   jako ważoną przez człowieka, a więc wymagającą podwyższonej uwagi.

2. **Ostrzeżenie o dryfie na ADR-ach ważonych przez człowieka.** PR
   modyfikujący ADR z jednym z tych znaczników podnosi ostrzeżenie CI
   (`adr-provenance-drift.yml`). Jest to kontrola doradcza, nie
   blokująca — chodzi o widoczność, nie o bramkę.

3. **Bramka akceptacji (Proposed → Accepted).** Status przełącza
   człowiek. PR, który to robi, musi przejść:
   - **(a) skan kolizji numerów** — brak duplikatu numeru
     `docs/adr/NNN-*` w scalonym drzewie. Egzekwowane przez
     `adr-numbering.yml`.
   - **(b) kontrolę realizacji** — każde twierdzenie o zachowaniu
     w czasie działania w sekcji Decyzja jest zweryfikowane wobec kodu
     albo jawnie oznaczone jako „jeszcze niepodłączone, śledzone w
     GH-XXXX".
   - **(c) odsyłacze zwrotne supersesji** — starsze ADR-y, których
     decyzje ten dokument zmienia, dostają wskaźnik zwrotny.

4. **Okresowa adwersarialna ponowna derywacja.** Kwartalnie (albo na
   żądanie), zdolny model ponownie wyprowadza próbkę decyzji ważonych
   przez człowieka z bieżących okoliczności i zgłasza rekomendacje
   potwierdź / popraw / zastąp / dryf, cytując dowody w formacie
   `ścieżka:linia`. Podłączone przez `adr-rederivation.yml`; wynikiem
   jest pakiet do przeglądu w zgłoszeniu GitHub, nigdy automatyczna
   zmiana.

5. **Higiena statusów.** Używaj `Superseded` dla decyzji w pełni
   zastąpionych, z maszynowo sprawdzalnym wskaźnikiem `Superseded-by:`;
   używaj stylu datowanej poprawki (blok
   `> **Amendment (YYYY-MM-DD):**` na górze) dla częściowej supersesji.

## Uzasadnienie

Uczynienie `Reviewed-by:` warunkiem koniecznym dla `Accepted` przywraca
znaczenie „zostało scalone" jako „jest aktualną, zweryfikowaną
wytyczną" — właściwość, którą korpus ADR traci, gdy każdy wpis dryfuje
do gołego `Accepted`.

Adwersarialna ponowna derywacja zamyka asymetrię z części 2 Kontekstu.
Traktuje „decyzję, która zmieniła się w nowych okolicznościach" jako
coś do świadomego ponownego zdecydowania, zamiast domyślnie pozostawiać
to temu, kto ostatnio dotknął kodu.

## Konsekwencje

**Pozytywne:**
- Autorstwo człowiek-kontra-AI jest ustalane w chwili decyzji, a nie
  przez archeologię.
- Niezrecenzowane ADR-y nie mogą udawać zaakceptowanej wytycznej.
- Decyzje człowieka zyskują ścieżkę ponownego rozpatrzenia symetryczną
  do przeglądu AI.

**Negatywne:**
- Dodaje pola nagłówka i kontrole CI do każdego PR-a dotykającego ADR.
- Adwersarialna ponowna derywacja potrzebuje okresowego właściciela
  i budżetu na API; bez harmonogramu wygasa.
- Bramkowanie `Accepted` przez `Reviewed-by:` spowalnia ADR-y pisane
  solo, dopóki nie przeczyta ich drugi człowiek.

## Powiązane

- [TEMPLATE.md](TEMPLATE.md) — niesie pola nagłówka prowenienacji
- `.github/workflows/adr-numbering.yml` — bramka 3a
- `.github/workflows/adr-provenance-drift.yml` — część 2
- `.github/workflows/adr-rederivation.yml` — część 4
