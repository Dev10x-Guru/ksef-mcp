# Wytyczne Job Story JTBD

Zasady pisania Job Story używanych w tytułach PR-ów, opisach PR-ów,
treściach commitów i zgłoszeniach.

> **Zakres**: ten format obowiązuje w Job Story w commitach i opisach
> PR-ów. Dotyczy też tytułów i treści zgłoszeń.
> **Krytyczna zależność**: parsowanie release notes wymaga precyzyjnej
> strukturalnej formy JTBD — `**Gdy** … **[rola] chce** … **żeby
> [beneficjent] mógł** …`. Pominięcie znaczników `**[rola] chce**` /
> `**żeby [beneficjent] mógł**` psuje automatyczne zbieranie release
> notes.

> **UWAGA — ryzyko dla automatyzacji PR-ów**: polskie znaczniki
> `**Gdy**`, `**chce**`, `**żeby ... mógł**` opisane niżej **nie są
> rozpoznawane** przez walidator `mcp__plugin_Dev10x_cli__create_pr`
> ani przez workflow `claude-pr-hygiene.yml`. Oba te mechanizmy szukają
> dosłownych angielskich znaczników `**When**`, `**wants to**`,
> `**so ... can**`. Dopóki wtyczka Dev10x nie zostanie dostosowana,
> tworzenie PR-a przez tę umiejętność **odrzuci polskie Job Story**, a
> kontrola higieny PR-a **zgłosi jego brak** — mimo że treść jest
> poprawna wg zasad tego pliku. Do czasu tej zmiany traktuj wymóg
> polskich znaczników jako decyzję właściciela projektu, świadomą tego
> ograniczenia narzędzi.

## Format

```
**Gdy** [sytuacja], **[rola] chce** [motywacja], **żeby [beneficjent] mógł** [oczekiwany rezultat].
```

Jedno zdanie. Bez wypunktowań. Bez szczegółów implementacyjnych.

Nazwij konkretną dziedzinową **rolę** (kto ma potrzebę) i konkretnego
**beneficjenta** (kto zyskuje na rezultacie). Często to ta sama rola —
wtedy nazwij ją w obu miejscach („**księgowa chce** … **żeby księgowa
mogła** …"). Gdy się różnią, nazwij obie wprost. Patrz § Wybór roli, jak
ją dobrać.

## Wymóg głosu

Job Story musi używać **głosu trzecioosobowego, z konkretną rolą
dziedzinową**: nazwij rolę i beneficjenta jako konkretne role. Zarówno
pierwsza osoba („chcę"), jak i bezosobowe „użytkownik chce" są błędne.

| Forma | Przykład | Status |
|------|---------|--------|
| ✅ Trzecia osoba, konkretna rola | **integrator chce** sprawdzić status faktury | WYMAGANE |
| ✅ Wyraźny beneficjent | **żeby księgowa mogła** uzgodnić zgłoszenia | WYMAGANE |
| ❌ Pierwsza osoba | **chcę** sprawdzić status faktury | ŹLE (przestarzałe) |
| ❌ Bezosobowa rola | **użytkownik chce** sprawdzić status faktury | ŹLE (brak roli) |

Różnica: nazwij rolę („integrator chce"), nigdy „ja" i nigdy ogólnego
„użytkownika"/„klienta". Gdy rezultat korzysta innej roli, powiedz to
wprost: `**żeby [rola/system] mógł** ...`.

## Wybór roli

- **Role chcą rezultatów, nie pracy.** Nikt nie chce *pracować* — ludzie
  chcą **rezultatów**. Rola rzadko chce *coś zrobić*; w idealnym
  przypadku chce, żeby rezultat nastąpił bez żadnego jej wysiłku. Jeśli
  rola byłaby najszczęśliwsza, nic nie robiąc, nazwij rezultat, który
  chce, żeby *się wydarzył* — a potem sprawdź, czy prawdziwym
  beneficjentem jest *inna* rola niż ta wykonująca czynność. Gdy tak
  jest, wykonawca jest **mechanizmem**, a Job Story należy do
  beneficjenta.
- Nazwij konkretną dziedzinową rolę, nigdy bezosobowego „użytkownika"
  ani „klienta". W ksef-mcp typowe role to **księgowa**, **integrator**
  (deweloper/system podłączający klienta MCP do KSeF) oraz
  **podatnik/właściciel firmy**.
- Ten zbiór jest otwarty — odkrywaj nowe role wraz z rozwojem dziedziny.
- Osoby utrzymujące projekt rzadko są rolą w Job Story. Gdy faktycznie
  nią są, nazwij korzyść wprost (obniża koszt, zwiększa niezawodność).
  Narzędzia deweloperskie to uczciwy wyjątek — Job Story, której rolą
  jest osoba utrzymująca projekt, jest zasadna, gdy zmiana dotyczy
  narzędzi (CI, pakowanie, proces wydania).

## Kluczowe zasady

### 1. Bez person — skup się na sytuacji

Job Story zastępuje „Jako [persona]..." **sytuacją** — kontekstem, który
tworzy potrzebę.

### 2. Sytuacja ważniejsza niż implementacja

Klauzula „Gdy" opisuje realny kontekst, nie interakcję z interfejsem.

- Dobrze: „Gdy faktura nie przejdzie walidacji KSeF"
- Źle:  „Gdy wywoływane jest narzędzie validate_invoice"

### 3. Motywacja ujawnia obawę

Klauzula „[rola] chce" opisuje, co rola próbuje osiągnąć.

- Dobrze: „księgowa chce od razu zobaczyć powód odrzucenia"
- Źle:  „księgowa chce nowego narzędzia MCP"

### 4. Oczekiwany rezultat pokazuje wartość

Klauzula „żeby [beneficjent] mógł" opisuje mierzalną korzyść lub
problem, który znika. Powinna kontrastować z obecnym, wadliwym stanem.

- Dobrze: „żeby podatnik mógł poprawić i wysłać ponownie przed terminem"
- Źle:  „żeby system miał walidację"

## Antywzorce

| Antywzorzec | Problem | Poprawka |
|---|---|---|
| Język techniczny | Niezrozumiały dla interesariuszy | Użyj języka biznesowego/dziedzinowego |
| „Gdy" nastawione na rozwiązanie | Narzuca implementację | Opisz realny wyzwalacz |
| „Gdy" odwołujące się do polecenia/CLI | „Gdy uruchamiane jest `uvx ksef-mcp`" narzuca narzędzie | Opisz realny wyzwalacz: „Gdy wysyłka faktury przekroczy limit czasu" |
| Niejasny rezultat | Nie da się zweryfikować | Sprecyzuj, co konkretnie się poprawia |
| Brak kontrastu z obecnym stanem | Niejasne, dlaczego to ważne | Pokaż, co dziś jest nie tak |
| Bezosobowa rola („użytkownik chce") | Brak konkretnej roli — nie da się jej przypisać do dziedziny | Nazwij rolę: „księgowa chce" (patrz § Wybór roli) |
| Motywacja nastawiona na rozwiązanie | „integrator chce wywołać nowy endpoint" nazywa implementację, nie potrzebę | Opisz motywację: „integrator chce wykrywać awarie KSeF bez ręcznego odpytywania" |
| Motywacja czasownikiem interfejsu („chce zobaczyć/przeglądać/zarządzać X") | Opisuje obsługę funkcji, nie rezultat | Nazwij stan końcowy: „chce być poinformowana, gdy zgłoszenie się nie powiedzie", nie „chce oglądać stronę statusu" |

## Zasada pisania tytułu

Przesuń perspektywę z tego, co zmieniło się w kodzie, na to, co
umożliwia roli. Klauzula „żeby [beneficjent] mógł" ujmuje rezultat.

### Typowe wzorce

| Rodzaj zmiany | Źle (implementacja) | Dobrze (rezultat) |
|---|---|---|
| Nowe narzędzie MCP | `Dodaje narzędzie get_invoice_status` | `Umożliwia sprawdzenie statusu faktury z klienta MCP` |
| Poprawka błędu | `Poprawia race condition odświeżania tokenu` | `Zapobiega podwójnemu odświeżeniu tokenu przy równoległych wywołaniach` |
| CI | `Dodaje workflow ruff` | `Wyłapuje błędy lintu przed merge'em` |
| Refaktoryzacja | `Wydziela klienta KSeF z server.py` | `Umożliwia ponowne użycie klienta KSeF w różnych narzędziach` |
| Dokumentacja | `Dodaje plik reguł przeglądu` | `Standaryzuje przebieg przeglądu kodu` |
| Wydanie | `Podbija wersję do 0.2.0` | `Wydaje poprawki statusu faktury i odświeżania tokenu` |

### Test przemianowania

Jeśli tytuł czyta się jak podsumowanie git diff, przeformułuj go.
Zadaj pytanie: *„Co rola może teraz zrobić, czego nie mogła
wcześniej?"* — ta odpowiedź jest Twoim tytułem.

## Przykłady

### Nowe narzędzie MCP
**Gdy** klient MCP potrzebuje statusu faktury bez otwierania portalu
webowego KSeF, **integrator chce** udostępnić narzędzie
`get_invoice_status`, **żeby księgowa mogła** potwierdzić wynik wysyłki
w swoim kliencie czatu.

### Poprawka błędu
**Gdy** dwa wywołania narzędzia wyścigują się nad kontrolą wygaśnięcia
tokenu, **osoba utrzymująca projekt chce** zserializować odświeżanie
tokenu blokadą, **żeby integrator mógł** polegać na kliencie bez
sporadycznych błędów 401.

### Dokumentacja
**Gdy** onboardowany jest nowy współtwórca, **osoba utrzymująca
projekt chce** mieć jasne konwencje commitów i PR-ów, **żeby
współtwórcy mogli** je stosować bez czytania każdego wcześniejszego PR-a.

### Wydanie
**Gdy** paczka poprawek jest gotowa, **osoba utrzymująca projekt chce**
opublikować wydanie semver na PyPI, **żeby integratorzy mogli**
przypiąć `ksef-mcp` do stabilnej wersji przez `uvx`.

## Zobacz też

`.claude/rules/INDEX.md` dokumentuje, skąd te opracowania są wczytywane.
Jeśli dokumentacja własna jakiejś umiejętności różni się od tego
formatu, ten plik jest rozstrzygający dla treści JTBD w PR-ach i
commitach.
