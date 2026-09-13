<!--
  Konwencja tytułu: <gitmoji> <rezultat>
  np. ✨ Umożliwia wyszukanie faktury w środowisku testowym KSeF
-->

## Job Story

<!-- Trzecia osoba i konkretna rola — księgowa, integrator, podatnik.
     Nigdy „chcę", nigdy bezosobowy „użytkownik".
     Gdy <sytuacja>, <rola> chce <motywacja>, żeby <beneficjent> mógł
     <oczekiwany rezultat>.

     UWAGA: walidator `create_pr` we wtyczce Dev10x oraz workflow
     claude-pr-hygiene.yml szukają dosłownych angielskich znaczników
     **When** / **wants to** / **so ... can**. Polskie znaczniki nie są
     przez nie rozpoznawane — patrz references/git-jtbd.md. -->

## Podsumowanie

<!-- Co się zmieniło i dlaczego. Podlinkuj zamykane zgłoszenia. -->

Fixes:

## Bramka bezpieczeństwa KSeF

Wysłanie faktury do produkcyjnego KSeF tworzy dokument o skutkach
podatkowych i **jest nieodwracalne**. Wszystko, co może sięgnąć API
KSeF, musi się tutaj wytłumaczyć.

- [ ] Ten PR **nie** dodaje ani nie zmienia kodu wołającego API KSeF,
      **albo** każde nowe wywołanie ogranicza się do środowiska
      testowego.
- [ ] Żaden dodany tu test nie sięga domyślnie działającego punktu
      końcowego KSeF — testy dotykające sieci noszą marker `ksef_live`
      i pozostają odfiltrowane z domyślnego zestawu.
- [ ] Żadne poświadczenie, token ani NIP nie trafia do repozytorium,
      do logów ani do artefaktu CI; żaden XML faktury nie jest
      wysyłany jako artefakt budowania.

> Uzasadnienie (wymagane, jeśli któreś pole zostało niezaznaczone):

## Testy

- [ ] Testy przechodzą (`uv run pytest`) przy pokryciu 100%
- [ ] Testy narzędzi z `bin/` przechodzą, jeśli były dotykane
      (`uv run --no-project --with pytest --with pytest-cov pytest bin/ --no-cov`)
- [ ] Weryfikacja ręczna, jeśli zmiana jest widoczna dla użytkownika

## Wycofanie

<!-- Jak wycofać, gdy to coś zepsuje. „Wycofać PR-a" jest poprawną
     odpowiedzią tylko wtedy, gdy zmiana jest samowystarczalna.
     Wydanej wersji nie da się usunąć z PyPI — opisz zamiast tego, jak
     ją wycofać ze sprzedaży (yank) i wydać ponownie. -->
