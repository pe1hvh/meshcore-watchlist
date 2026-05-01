# ADR-NNN: <Korte, beschrijvende titel>

| Veld              | Waarde                                                        |
|-------------------|---------------------------------------------------------------|
| **Status**        | Voorgesteld \| Geaccepteerd \| Vervangen \| Vervallen          |
| **Datum**         | YYYY-MM-DD                                                    |
| **Auteur**        | PE1HVH (Hans)                                                 |
| **Scope**         | <package, subsysteem of project>                              |
| **Vervangt**      | ADR-XXX (alleen invullen als van toepassing)                  |
| **Vervangen door**| ADR-XXX (alleen invullen als van toepassing)                  |

---

## 1. Context

Wat is de situatie? Welk probleem, welke spanning of welke ervaring uit
productie maakt deze beslissing nodig? Houd het feitelijk: wat is er
gebeurd, wat is er gemeten, wat botst er met wat.

## 2. Beslissing

Wat wordt besloten, in één tot drie zinnen. Geen bijzinnen met "tenzij"
of "afhankelijk van". Een ADR met een onduidelijke beslissing is geen ADR.

## 3. Argumentatie

Waarom dit besluit, en niet iets anders? Welke principes, feiten of
metingen dragen het. Eén alinea is vaak genoeg.

## 4. Gevolgen

Wat verandert er door dit besluit:

- **Wat wordt makkelijker** — concreet, per onderdeel.
- **Wat wordt moeilijker** — eerlijk, geen verstoppertje spelen met
  trade-offs.
- **Wat moet afgedwongen worden** — code-review-checks, lint-regels,
  acceptatiecriteria, test-fixtures.

## 5. Overwogen alternatieven

Welke andere opties zijn serieus bekeken, en waarom afgevallen? Eén
korte paragraaf per alternatief. Géén "we hebben ook gedacht aan X" zonder
reden van afwijzing.

## 6. Referenties

- Code-locaties die het besluit raken.
- Andere ADRs (vervangen, gerelateerd).
- Externe specs, RFC's, normen.
- Eventuele commits / issues / changelog-entries.

---

*Conventies voor dit ADR-register staan in `README.md` in deze folder.*
