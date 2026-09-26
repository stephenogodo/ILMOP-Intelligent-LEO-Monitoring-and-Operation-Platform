# WDR-006 — guard_region.py contains both FixedGuardRegion and AdaptiveGuardRegion

**Date:** September 2026
**Status:** Accepted
**Author:** Stephen O. Ogodo

---

## Context

Contribution 2 of the thesis is the AdaptiveGuardRegion: a guard region sized to
the satellite's instantaneous Doppler shift `ν(t) = ṙ(t) × f_c / c` rather than
the worst-case horizon Doppler `ν_max`. The spectral efficiency gain of the adaptive
scheme is only meaningful when compared against a fixed baseline.

The question is whether `FixedGuardRegion` and `AdaptiveGuardRegion` should live in
the same module or in separate files.

---

## Decision

**Both classes live in `waveform/guard_region.py` (same module).**

---

## Rationale

The adaptive contribution only has scientific weight measured against the fixed
worst-case baseline. If the two classes are in separate files:

1. The comparison computation in `metrics/isac_efficiency.py` must import from two
   separate modules with asymmetric coupling.
2. A reader of the code sees the adaptive design without immediately seeing what it
   is being compared against.
3. The paper Figure 1 (pass-elevation profile showing fixed guard vs adaptive guard
   with recovered spectral efficiency shaded between them) requires both objects to
   exist and be comparable in the same namespace.

By placing both in the same module, the relationship between them is explicit:
`AdaptiveGuardRegion` is defined immediately after `FixedGuardRegion`, making it
clear that the adaptive design is a refinement of the fixed baseline, not a
standalone contribution.

---

## Class design

```python
class FixedGuardRegion:
    """
    Baseline: guard sized to ν_max (horizon Doppler) throughout the pass.
    Standard approach in the OTFS literature [Raviteja 2018a, 2018b].
    Constant l_guard = ceil(ν_max / Δν) regardless of elevation.
    """

class AdaptiveGuardRegion:
    """
    Contribution 2: guard sized to ν(t) = ṙ(t) × f_c / c from ILMOP
    SGP4 range rate profile [OrbitalProfileExporter].
    l_guard(t) = ceil((ν(t) + δ) / Δν) where δ = safety margin.
    Shrinks toward δ/Δν at zenith where ν(t) → 0.
    """
```

---

## Consequences

- `waveform/guard_region.py` is the single import point for both classes.
- `metrics/isac_efficiency.py` imports both from `waveform.guard_region`.
- The thesis paper argument is: "Under η_ISAC, the adaptive scheme recovers
  X% spectral efficiency relative to the fixed baseline." This comparison is
  only valid because both baselines use the same metric formulation.
- The FixedGuardRegion is the comparison baseline in Paper 4 (not OFDM, which
  was already disqualified on Doppler grounds — see WDR-007).
