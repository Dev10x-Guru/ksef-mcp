# ADR-111: Powiązanie korekty z fakturą pierwotną

- **Date:** 2026-09-20
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** audyt architektury 2026-09-19, zgłoszenia #120 i #121, źródła `ksef2==0.19.0`
- **Depends-on:** [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md)

## Kontekst

Zestawienie okresu ostrzega już, że w okresie jest faktura korygująca
(#120). Ostrzeżenie zamyka ryzyko „suma wygląda na zobowiązanie, a nim
nie jest", ale nie odpowiada na pytanie, które księgowa zadaje zaraz
potem: **którą fakturę ta korekta koryguje**. Dopóki odpowiedzi nie ma,
zestawienie flaguje problem i zostawia jego rozwiązanie człowiekowi z
dwiema listami obok siebie.

Warunkiem wstępnym było rozstrzygnięcie, czy metadane KSeF w ogóle
niosą materiał na takie powiązanie. Zgłoszenie #120 zapisało to wprost:
bez odpowiedzi rekomendacja jest bezpodstawna. Weryfikacja wobec
zainstalowanego `ksef2==0.19.0` jest zamknięta i wypadła twierdząco.

### Co niesie model metadanych

Ścieżki względem `ksef2/`:

- `domain/models/invoices.py:253` — `InvoiceMetadata.invoice_type`, z
  wartościami `kor`, `kor_zal`, `kor_roz`, `kor_pef`, `kor_vat_rr` dla
  pięciu odmian korygujących (`domain/models/invoices.py:49-63`,
  opis w `infra/schema/api/spec/models.py:5329-5347`).
- `domain/models/invoices.py:259` — `invoice_hash`: skrót SHA256 samej
  faktury, kodowany Base64, 44 znaki
  (`infra/schema/api/spec/models.py:5370-5373`).
- `domain/models/invoices.py:260` — `hash_of_corrected_invoice`: skrót
  SHA256 **faktury korygowanej**, w tym samym kodowaniu i tej samej
  długości, `None` na dokumencie, który niczego nie koryguje
  (`infra/schema/api/spec/models.py:5374-5379`).

Dwa ostatnie pola to gotowa krawędź grafu: korekta wskazuje na fakturę
pierwotną wartością, którą ta faktura nosi o sobie.

### Dlaczego kwota sama nie wystarczy

`infra/schema/fa3/definitions/schemat.xsd:2631-2635` — `P_15` („Kwota
należności ogółem") ma na korekcie znaczenie „korekta kwoty wynikającej
z faktury korygowanej", a jej typ `TKwotowy`
(`schemat.xsd:1142-1151`, wzorzec `-?([1-9]\d{0,15}|0)(\.\d{1,2})?`)
dopuszcza minus. Sam SDK liczy sumę korekty ze znakiem, mnożąc wiersze
sprzed korekty przez `-1`
(`domain/models/fa3/body/root.py:357-366`, `387-418`).

To znaczy, że w szczególnym przypadku suma kolumny Brutto po korekcie i
fakturze pierwotnej wychodzi poprawnie sama z siebie. Ale tylko wtedy,
gdy **obie** są w tym samym okresie — a korekta wystawiona w
październiku do faktury z września nie ma w oknie czego pomniejszyć.
Powiązanie jest więc potrzebne nie po to, żeby dodać kwoty, lecz po to,
żeby powiedzieć, czy jest co dodawać.

## Decyzja

**Powiązanie po skrócie faktury, nie po numerze.** Port przenosi dwa
pola dalej, a zestawienie łączy korektę z fakturą pierwotną, gdy
`hash_of_corrected_invoice` korekty równa się `invoice_hash` innej
pozycji.

Zakres tego ADR to projekt. Kod nie wchodzi razem z nim — zgłoszenie
#121 wycenia pracę na L i sam nakład jest powodem, żeby rozstrzygnięcie
zapisać przed implementacją, a nie po niej.

### Kształt danych

`InvoiceMetadata` w porcie (`src/ksef_mcp/ksef_port/types.py`) zyskuje
dwa pola obok `document_type`, które weszło z #120:

```python
content_hash: str
corrected_content_hash: str | None
```

Nazwy nasze, nie drutowe — port tłumaczy słownik SDK na własny (D-017),
tak samo jak przy `SubjectRole`. `as_metadata` czyta je z
`record.invoice_hash` i `record.hash_of_corrected_invoice`.

Dalej wystarczy jedna funkcja w `statement.py`, w rodzinie tego, co już
tam stoi:

```python
def corrections_matched(
    rows: tuple[StatementRow, ...],
) -> tuple[CorrectionLink, ...]:
```

gdzie `CorrectionLink` niesie korektę, znalezioną fakturę pierwotną
(albo jej brak) i jest wejściem dla ostrzeżenia z #120 — które przestaje
mówić samo „jest korekta" i zaczyna rozróżniać dwa położenia:

- **korekta z fakturą w tym samym oknie** — suma kolumny Brutto jest
  poprawna, bo różnica i kwota pierwotna są obie w środku;
- **korekta bez faktury w oknie** — suma nie odda zobowiązania i żadne
  sumowanie w tym pliku tego nie naprawi; trzeba sięgnąć po okres, w
  którym faktura pierwotna była wystawiona.

Drugie położenie jest tym, o które w #121 chodzi, i dzisiejsze
ostrzeżenie nie umie go odróżnić od pierwszego.

### Dlaczego skrót zamiast numeru faktury?

| Klucz powiązania | Zalety | Wady |
|---|---|---|
| **`hash_of_corrected_invoice` → `invoice_hash`** | Pole istnieje w metadanych, jest wypełniane przez KSeF, jednoznaczne i niezależne od tego, co wystawca wpisał w treści | Dopasowanie tylko wtedy, gdy faktura pierwotna też jest w pobranym zbiorze |
| Numer faktury z `nr_fa_korygowany` / `dane_fa_korygowanej` | Czytelny dla człowieka | Żyje w **treści XML**, nie w metadanych — sięgnięcie po niego znaczy parsowanie faktury, czyli wpuszczenie danych osobowych kontrahenta tam, gdzie D-011 ich nie chce |
| Numer KSeF faktury korygowanej | Byłby najwygodniejszy | Metadane go nie niosą — nie ma takiego pola |
| Heurystyka po kwocie i kontrahencie | Nie wymaga nowych pól | Zgaduje; dwie faktury o tej samej kwocie dla tego samego kontrahenta w miesiącu to sytuacja zwykła, nie wyjątkowa |

Rozstrzygające jest drugie: numer faktury korygowanej leży w ciele
FA(3), a ciało faktury nie przekracza tej granicy (D-011). Skrót z
metadanych daje to samo powiązanie bez sięgania po dane osobowe.

## Uzasadnienie

Powiązanie po skrócie jest tanie i nie zmienia niczego w tym, co
przekracza granicę portu: oba pola już przychodzą w odpowiedzi, którą i
tak odbieramy, więc nie kosztuje to ani jednego zapytania z dwudziestu
na godzinę.

Świadomie przyjęty kompromis: dopasowanie działa w obrębie tego, co
mamy. Korekta do faktury sprzed pobranego okna zostanie rozpoznana jako
korekta bez pary — i **to jest dobra odpowiedź**, nie porażka
dopasowania, bo dokładnie o tym księgowa ma się dowiedzieć.
Świadomie odrzucone: doczytywanie brakujących faktur pierwotnych
własnymi zapytaniami. Okno wstecz byłoby zgadywane, a cena zgadywania
to wydane zapytania i — przy powtórzeniach — uwaga Ministerstwa
(D-020, D-031 §8).

Skrót nie trafia do kolumn CSV. Osiem kolumn plus waluta i KOD I
rozstrzygnięto w D-023; 44 znaki Base64 nie są dla czytelnika tego
pliku żadną informacją, a plik opuszcza maszynę.

## Konsekwencje

**Pozytywne:**

- Ostrzeżenie o korekcie przestaje być jednym zdaniem dla dwóch bardzo
  różnych położeń, z których tylko jedno wymaga pracy.
- Powiązanie powstaje bez dotykania XML-a faktury, więc D-011 zostaje
  nienaruszone.
- Zero dodatkowych zapytań do KSeF.

**Negatywne:**

- Wersja schematu cache znów rośnie (4 → 5): zapamiętany miesiąc bez
  skrótów nie umiałby odpowiedzieć na pytanie o parę.
- Dopasowanie jest ograniczone do pobranego okna i zostanie takim,
  dopóki ktoś nie zdecyduje inaczej osobnym ADR-em.
- Pole `hash_of_corrected_invoice` zna tylko tę jedną krawędź. Korekta
  korekty da łańcuch, którego ten projekt nie rozwija — wystarczy do
  zgłoszenia „ta pozycja czegoś dotyczy", nie do odtworzenia pełnej
  historii dokumentu.

## Powiązane

- [ADR-102](102-warstwa-antykorupcyjna-nad-klientem-ksef.md) — port jest
  miejscem, w którym słownik SDK zamienia się na nasz
- [#120](https://github.com/Dev10x-Guru/ksef-mcp/issues/120) —
  ostrzeżenie o korekcie i weryfikacja wobec `ksef2`, która ten dokument
  umożliwiła
- [#121](https://github.com/Dev10x-Guru/ksef-mcp/issues/121) — zgłoszenie,
  którego decyzyjną część ten ADR zamyka; implementacja zostaje otwarta
