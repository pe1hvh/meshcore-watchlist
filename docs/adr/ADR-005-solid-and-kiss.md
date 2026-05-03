# ADR-005: SOLID and KISS as design principles

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-01                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | All PE1HVH projects                                           |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

Code without design principles drifts in two directions:

- **Under-designed** — everything in one large class or one large
  function, no separation of responsibilities, every change touches
  half the code.
- **Over-designed** — abstractions on abstractions, factories for
  factories, design patterns applied because they exist and not
  because the problem demands them. AI-generated code falls
  particularly often into this second category.

Two common principle sets cover both pitfalls: **SOLID** as a
disciplining frame for the structure, **KISS** as the overarching
filter that cuts off over-design. Not an exotic choice; both are
broadly accepted and well documented.

## 2. Decision

PE1HVH projects follow SOLID and KISS as explicit design principles,
with KISS as the tie-breaker whenever a SOLID application leads to
more abstraction than the problem warrants.

## 3. Rationale

### SOLID, in plain terms

| Letter | Name                          | Concrete check during review                                            |
|--------|-------------------------------|-------------------------------------------------------------------------|
| **S**  | Single Responsibility         | Does this class / module / function have more than one reason to change? If so: split. |
| **O**  | Open / Closed                 | Can I add new behaviour without modifying existing, working code?       |
| **L**  | Liskov Substitution           | Can I substitute a subclass anywhere the superclass is used, without surprises? |
| **I**  | Interface Segregation         | Do consumers only need to know the interfaces / methods they actually use? |
| **D**  | Dependency Inversion          | Does high-level code depend on abstractions, not on concrete implementations? |

In practice, **S** (SRP) and **D** (DIP) do most of the work; the
other three often follow naturally once those two are in place.

### KISS

*Keep It Simple, Stupid* — the simplest solution that solves the
problem correctly wins. "Simple" here means:

- less code than an alternative, at equal functionality;
- fewer layers / abstractions / configuration options;
- fewer external dependencies;
- less mental load when reading.

KISS sits above SOLID: if applying SOLID makes the code more complex
without an actual or within-twelve-months-expected problem demanding
it, KISS wins.

## 4. Consequences

**What becomes easier:**

- Code review has a fixed question list (the five SRP/DIP/KISS
  questions above). No taste discussions, just tick the boxes.
- When judging AI-generated code, "is this the simplest thing that
  works?" is a direct rejection ground, and thereby a brake on
  over-design.
- Maintenance of code over the years stays manageable: an
  SRP-conformant class only needs to be modified for its one reason
  to change.

**What becomes harder:**

- For abstractions that are not directly needed but "may be later":
  explicit motivation for why they must be in place now. KISS enforces
  that speculative flexibility is not built in.
- "Quick hacks" are not automatically allowed under the KISS banner.
  KISS = simple, not sloppy. A hack that violates SRP is not
  KISS-conformant; it merely shifts the problem to later.

**What must be enforced:**

- The pull-request template (or its personal equivalent: a fixed-order
  check before commit) contains:
  - SRP question: name the one reason to change of the modified class
    / module.
  - KISS question: was it considered whether this could be simpler?
    Which simpler version was rejected and why?
- For every new design document (see ADR-template, sections
  *Rationale* and *Alternatives considered*): explicit testing against
  SOLID/KISS as part of the motivation.
- On suspicion of over-design during review: probe for the concrete
  problem the abstraction solves. "For flexibility" is not an answer.

## 5. Alternatives considered

**Alternative A — KISS only, no SOLID.** Rejected because KISS
without a structural counterforce slides into under-design: one big
"simple" function of five hundred lines is not rejectable on KISS
criteria.

**Alternative B — Domain-Driven Design (DDD) as the overarching
frame.** Rejected for PE1HVH scope. DDD is strong in large domains
with multiple teams and complex business logic. PE1HVH projects are
solo work with technical rather than business domains; the DDD
overhead (bounded contexts, ubiquitous language, aggregates) does not
pay off.

**Alternative C — no explicit design frame, judge code style per
review on feel.** Rejected because that is exactly the situation this
ADR resolves. "On feel" does not work when the user is also the
reviewer and has had a different feel since last week.

**Alternative D — YAGNI as the primary principle instead of KISS.**
*"You Aren't Gonna Need It"* overlaps strongly with KISS but focuses
specifically on rejecting speculative features. Not rejected, but
treated as a variant of KISS; KISS covers it and is broader.

## 6. References

- Robert C. Martin, *Agile Software Development: Principles, Patterns,
  and Practices*, 2002 — the original SOLID formulation.
- *The Art of Unix Programming*, Eric S. Raymond — source of the KISS
  principle in the software-engineering context.
- ADR-template, section 5 *Alternatives considered*: enforces KISS
  testing as part of decision-making.
- ADR-001 (channel_name as identity) — concrete example of KISS in
  action: secret_hex or UUID was rejected in favour of the simplest
  solution that works.
