# WDR-008 — ZC-PAPR reduction and η_ISAC metric are published as one paper

**Date:** September 2026
**Status:** Accepted
**Author:** Stephen O. Ogodo

---

## Context

Contribution 5 of the thesis has two components:
1. Zadoff-Chu (ZC) pilot sequences for PAPR reduction in OTFS-ISAC waveforms.
2. The η_ISAC multi-function spectral efficiency metric.

The question was whether these should be published as one paper or two separate papers.

---

## Decision

**One paper (Paper 9): ZC pilot sequences + η_ISAC metric.**
Target venue: IEEE Transactions on Communications.

---

## History of this decision

The decision has evolved:

- **First framing:** Two separate papers (ZC-PAPR paper + η_ISAC letter).
- **Revised:** One paper — recognised that the metric and the ZC pilot are mutually
  reinforcing. The ZC pilot reduces PAPR while the metric proves that the pilot
  overhead is not a cost but a multi-function asset.
- **Further refined:** The metric is the primary novel contribution; the ZC pilot
  is the engineering vehicle. The paper leads with η_ISAC in the abstract, not
  with PAPR reduction.

---

## Correct argument for η_ISAC

**Conventional metrics understate ISAC waveform efficiency — they do not merely fail to account for ISAC overhead.**

The specific flaw: in a communication-only OTFS system, the pilot and guard overhead
delivers ONE function (channel estimation). In the OTFS-ISAC system, the same overhead
delivers THREE functions simultaneously (channel estimation + navigation + sensing).
Conventional bits/Hz metrics (Shannon 1948) credit neither system for the additional
functions delivered.

Under η_ISAC:
- Conventional OTFS (random pilot): η_conv higher, η_ISAC lower — pilot credited for 1 function.
- ZC-piloted OTFS-ISAC: η_conv lower (appears penalised), η_ISAC highest — pilot credited for 3 functions.

The inversion between the η_conv and η_ISAC columns is the paper's central result.

---

## Prior art risk

- ZC sequences for PAPR in OFDM/single-function OTFS: almost certainly published.
  ZC sequences are used in LTE/5G reference signals for precisely this property.
- η_ISAC metric reframing pilot overhead as multi-function value delivery: almost
  certainly NOT published. This is the differentiating claim.

**If ZC-PAPR for OTFS-ISAC is already published:** cite it, use it as the engineering
vehicle, and the paper focuses entirely on the metric as the novel contribution.

**If not published:** both contributions present, metric leads.

---

## Consequences

- `waveform/zc_pilot.py` implements the ZC sequences and PAPR analysis.
- `waveform/metrics/isac_efficiency.py` implements the η_ISAC metric.
- Literature search must be completed before writing the paper (not before writing the code).
- Paper 9 abstract must lead with: "We propose a multi-function spectral efficiency
  metric for ISAC waveforms that accounts for the aggregate value delivered by pilot,
  guard region, and sensing allocation across all operational functions."
