# WDR-007 — Spectral efficiency comparison baseline is conventional OTFS, not OFDM

**Date:** September 2026
**Status:** Accepted
**Author:** Stephen O. Ogodo

---

## Context

Paper 9 (ZC pilot + η_ISAC metric) requires a comparison baseline to demonstrate
that the proposed waveform achieves higher effective spectral efficiency. The choice
of baseline determines the strength and defensibility of the paper's central claim.

Two candidates were considered:

1. **OFDM** — the dominant waveform in cellular and satellite communications.
2. **Conventional OTFS with random pilot** (Raviteja et al. 2018a) — the same
   modulation with a standard pilot, communication-only.

---

## Decision

**The comparison baseline is conventional OTFS with random pilot (communication-only). OFDM is not used as a primary baseline.**

Three baselines are used in the comparison table:

| Row | Waveform | Role |
|-----|----------|------|
| 1 | Conventional OTFS, random pilot | Primary η_ISAC baseline |
| 2 | Conventional OTFS, ZC pilot, comms-only | Isolating baseline |
| 3 | ZC-piloted OTFS-ISAC (proposed) | Proposed system |

---

## Rationale

**Why not OFDM as primary baseline:**

OFDM has already been disqualified as a LEO ISAC waveform on Doppler grounds:
at 62 kHz maximum Doppler, the continuous Doppler shift creates an irreducible
inter-carrier interference (ICI) floor that renders OFDM unsuitable for the LEO
ISAC application (see ILMOP ADR-001). Comparing η_ISAC of the proposed OTFS-ISAC
waveform against OFDM conflates two separate problems:
  - The modulation choice (OTFS vs OFDM) — already settled.
  - The PAPR and efficiency of the ISAC pilot design — the actual claim.

A reviewer will correctly point out that OFDM was already eliminated in Chapter 4
on Doppler grounds, and comparing against it looks like cherry-picking a weak baseline.

**Why the isolating baseline (Row 2) is essential:**

Row 2 (conventional OTFS with ZC pilot, comms-only) proves that the η_ISAC gain
in Row 3 comes from three-function ISAC operation, not from the ZC sequence choice
alone. Without Row 2, a reviewer could argue: "your η_ISAC gain is just a consequence
of the ZC pilot's lower PAPR, not of the three-function capability."

---

## Consequences

- `metrics/isac_efficiency.py` must compute η_ISAC for all three rows.
- The paper abstract leads with the η_ISAC metric, not with PAPR reduction.
- OFDM appears in the thesis in Chapter 2 (why OTFS was chosen) and Chapter 5
  (BER floor comparison), but not in Chapter 8 (η_ISAC comparison table).
- The literature search before paper submission must verify: "OTFS PAPR reduction
  Zadoff-Chu" (likely published — if so, Row 1 becomes the novel contribution);
  "ISAC spectral efficiency metric" (expected near-empty — η_ISAC is the primary
  novel contribution regardless of prior art on ZC-PAPR for OTFS).
