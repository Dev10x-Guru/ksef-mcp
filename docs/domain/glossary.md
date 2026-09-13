# Glossary — Ubiquitous Language

> Termin → definicja → mapowanie na kod. Alfabetycznie.

| Termin | Definicja | Typ / funkcja w kodzie |
|---|---|---|
| **Archiwum** | Kontekst odpowiadający na pytanie „które faktury za ten okres już mamy". Zapytania, paginacja, pobieranie, deduplikacja, delta. | kontekst ograniczony |
| **Delta okresu** | Faktury, które pojawiły się w danym okresie od czasu ostatniego pobrania. Wynik ponownego odpytania okresu. | wynik `PobranieOkresu` |
| **Dostęp** | Kontekst odpowiadający na pytanie „kim jesteśmy wobec KSeF i czy wolno nam czytać". Uwierzytelnienie, sesja, poświadczenia, środowisko, kontekst podmiotu. | kontekst ograniczony |
| **Indeks deduplikacji** | Zbiór numerów KSeF i skrótów, przechowywany **osobno od treści faktur**, żeby dało się skasować treść bez utraty idempotencji. | — |
| **Kierunek faktur** | Czy podmiot występuje jako nabywca czy jako sprzedawca. W API pole **wymagane** `subjectType`. Etap 1 przybity do `Subject2` (nabywca). | `KierunekFaktur` (VO) |
| **KOD I** | Link weryfikacyjny faktury: `/invoice/{NIP}/{DD-MM-RRRR}/{SHA-256 base64url pliku}`. Otwiera portal weryfikacyjny, który renderuje wizualizację w przeglądarce. | `LinkWeryfikacyjny` (VO) |
| **KOD II** | Link weryfikacyjny certyfikatu, wyłącznie dla faktur wystawionych offline. Poza zakresem. | — |
| **Kontekst podmiotu** | NIP i rola, w których działamy. **Własność poświadczenia**, nie parametr zapytania. | `KontekstPodmiotu` (VO) |
| **Numer KSeF** | Identyfikator nadany fakturze przez KSeF. Klucz naturalny deduplikacji — **nigdy** numer własny sprzedawcy. | `NumerKSeF` (VO) |
| **Okres** | Zakres dat **wraz z `DateType`**. Bez `DateType` „sierpień" ma trzy różne znaczenia. | `Okres` (VO) |
| **Pobranie okresu** | Powtarzalny proces skompletowania faktur za okres. **Serwis aplikacyjny, nie agregat.** | serwis aplikacyjny |
| **Poświadczenie** | Referencja do sekretu w keyringu. W konfiguracji trzymamy nazwę konta, nigdy sam token. | `Poświadczenie` (VO) |
| **Sesja KSeF** | Uwierzytelniony dostęp o ograniczonym czasie życia. Obiekt wartości, nie agregat. | `SesjaKSeF` (VO) |
| **Środowisko** | TEST / DEMO / PROD. Oznaczane w każdej odpowiedzi narzędzia. | `Środowisko` (VO) |
| **Token KSeF** | Sekret uwierzytelniający, wyświetlany przy generowaniu **jednorazowo**. | — |
| **Wpis archiwum** | Pojedyncza zarchiwizowana faktura. Jedyny agregat w modelu. | `WpisArchiwum` (agregat) |
| **`DateType`** | Parametr API określający, po której dacie filtrowany jest okres: `Issue`, `Invoicing`, `PermanentStorage`. | składowa `Okres` |
| **`subjectType`** | Parametr API określający kierunek: `Subject1` (sprzedawca), `Subject2` (nabywca), `Subject3`, `SubjectAuthorized`. | `KierunekFaktur` |
