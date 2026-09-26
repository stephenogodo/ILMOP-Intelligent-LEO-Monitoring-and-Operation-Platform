# Waveform Design Records (WDR) — Index

Design records for the OTFS-ISAC waveform layer (`waveform/`).
These mirror the platform Architecture Decision Records (ADRs) in `docs/adr/`.

A WDR is written for every significant design decision: when a non-obvious choice
is made, when a bug reveals a design constraint, or when a decision is likely to
be questioned by a reviewer. The goal is that no decision is lost between sessions.

---

## Record register

| ID | Title | Status | Date |
|----|-------|--------|------|
| [WDR-001](WDR-001-repository-structure.md) | Waveform code lives inside the ILMOP repository | Accepted | Sep 2026 |
| [WDR-002](WDR-002-axis-convention.md) | Delay-Doppler grid axis convention | Accepted | Sep 2026 |
| [WDR-003](WDR-003-channel-shift-convention.md) | DD channel circular shift uses +k_p not -k_p | Accepted — corrected after test failure | Sep 2026 |
| [WDR-004](WDR-004-channel-domain-for-navigation.md) | DD-domain channel model is the correct simulation path for navigation | Accepted — discovered during test development | Sep 2026 |
| [WDR-005](WDR-005-grid-sizing-constraint.md) | OTFS grid must be rectangular (N ≤ 80) for full LEO Doppler coverage | Accepted — discovered during pilot_frame.py implementation | Sep 2026 |
| [WDR-006](WDR-006-guard-region-two-class-design.md) | guard_region.py contains both FixedGuardRegion and AdaptiveGuardRegion | Accepted | Sep 2026 |
| [WDR-007](WDR-007-spectral-efficiency-baseline.md) | Spectral efficiency comparison baseline is conventional OTFS, not OFDM | Accepted | Sep 2026 |
| [WDR-008](WDR-008-zc-papr-and-eta-isac-one-paper.md) | ZC-PAPR reduction and η_ISAC metric are published as one paper | Accepted | Sep 2026 |

---

## Format

Each WDR follows the same structure as ILMOP ADRs:

- **Date** — when the decision was made
- **Status** — Proposed / Accepted / Superseded
- **Context** — what problem or question prompted the decision
- **Decision** — the choice made, stated precisely
- **Alternatives considered** — what else was evaluated
- **Rationale** — why this choice was made
- **Consequences** — what this decision requires of all future code

---

## When to write a new WDR

Write a WDR when:
- A non-obvious implementation choice is made (e.g. axis convention, shift direction)
- A test failure reveals a design constraint (e.g. WDR-003, WDR-004, WDR-005)
- A paper framing decision is made that affects what gets implemented (e.g. WDR-007, WDR-008)
- Any decision that would take more than five minutes to reconstruct from the code alone
