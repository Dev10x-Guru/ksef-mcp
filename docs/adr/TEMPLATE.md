# ADR-NNN: [Tytuł]

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Deprecated | Superseded
- **Deciders:** [nazwiska]
- **Authored-by:** human | agent (<identyfikator modelu>)
- **Reviewed-by:** [nazwani człowiek/ludzie, którzy przeczytali cały tekst przed akceptacją; puste = niezrecenzowane]
- **Sources:** [inspiracje / źródła pochodzenia wzorców; nieprzejrzyste odwołania dozwolone]
- **Supersedes:** [odwołanie do wcześniejszego ADR, który ten w pełni zastępuje, jeśli dotyczy]
- **Superseded-by:** [odwołanie do ADR, który zastępuje ten dokument, jeśli dotyczy]
- **Refines:** [odwołanie do ADR, który ten dokument rozwija bez zastępowania, jeśli dotyczy]
- **Depends-on:** [odwołanie do ADR, na którego mechanizmie ten dokument się opiera, jeśli dotyczy]

> **Pola relacji i cyklu życia.** Ustaw `Superseded-by:` razem ze
> `Status: Superseded` (albo `Deprecated`), gdy późniejszy ADR w pełni
> zastępuje tę decyzję. Gdy zmienia się tylko *część* decyzji, zachowaj
> `Status: Accepted`, zapisz zastępujący ADR w `Superseded-by:` z jawną
> notatką o zakresie i dodaj bezpośrednio pod tym blokiem nagłówka
> datowany cytat-poprawkę, np. `> **Amendment (YYYY-MM-DD):** [co się
> zmieniło i dlaczego].`
> `Refines:` i `Depends-on:` opisują relacje niebędące supersesją, żeby
> narzędzie budujące graf widziało każdą krawędź, nie tylko te z
> `Supersedes:`. Usuń każde pole relacji, które nie ma zastosowania.

> **Pola proweniencji.** `Authored-by:` odróżnia decyzje człowieka od
> decyzji agenta w chwili ich podejmowania; `Reviewed-by:` nazywa
> człowieka/ludzi, którzy przeczytali cały tekst przed akceptacją —
> pusta wartość oznacza niezrecenzowane, a ADR niezrecenzowany nie może
> rościć sobie statusu `Accepted`. `Sources:` zapisuje inspiracje/źródła
> pochodzenia wzorców (nieprzejrzyste odwołania dozwolone). Zob.
> [ADR-100](100-decision-provenance-and-adversarial-re-derivation.md).

## Kontekst

[Jaki problem albo potrzeba umotywowały tę decyzję? Jakie istnieją
ograniczenia? Odwołaj się do wcześniejszych ADR-ów dających kontekst.]

## Decyzja

[Co zostało zdecydowane? Dołącz przykłady kodu albo fragmenty
konfiguracji, jeśli wyjaśniają wybór.]

### Dlaczego [wybrana opcja] zamiast alternatyw?

| Narzędzie / podejście | Zalety | Wady |
|-----------------------|--------|------|
| **Wybrane** | ... | ... |
| Alternatywa A | ... | ... |
| Alternatywa B | ... | ... |

[Usuń tabelę porównawczą, jeśli nie rozważano alternatyw.]

## Uzasadnienie

[Dlaczego to podejście? Jakie kompromisy zaakceptowano?]

## Konsekwencje

**Pozytywne:**
- [korzyść 1]
- [korzyść 2]

**Negatywne:**
- [kompromis 1]
- [kompromis 2]

## Powiązane

- [ADR-NNN](NNN-slug.md) — [opis relacji]
- [link do PR-a albo zgłoszenia] — [kontekst]
