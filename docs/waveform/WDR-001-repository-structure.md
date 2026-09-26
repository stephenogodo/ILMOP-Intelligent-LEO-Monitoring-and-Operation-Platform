# WDR-001 — Waveform code lives inside the ILMOP repository

**Date:** September 2026
**Status:** Accepted
**Author:** Stephen O. Ogodo

---

## Context

The OTFS-ISAC waveform research layer needs a home. Two options were considered:

- **Option A:** Separate repository (`OTFS-ISAC-LEO`) importing ILMOP as a package via `pip install -e`.
- **Option B:** `waveform/` directory at the root of the ILMOP project.

The waveform layer is tightly coupled to the ILMOP platform layer. Specifically:

- `waveform/experiments/exp2_navigation.py` imports `services/navigation/validator.py`
- `waveform/guard_region.py` imports `services/otfs/orbital_profile.py` for the SGP4 range rate profile
- `waveform/receiver/navigation_rx.py` uses `services/otfs/channel_emulator.py`

All three imports cross the boundary between waveform research and platform infrastructure.

---

## Decision

**Option B adopted.** `waveform/` is a top-level directory inside the ILMOP repository.

---

## Alternatives considered

**Option A — Separate repository:**
- Pros: clean separation; `waveform/` repo can be kept private until paper submission.
- Cons: every cross-layer import requires a versioned package dependency. When `services/otfs/orbital_profile.py` changes, the waveform repo must be updated to pin the new version. Two repositories, two virtual environments, two CI pipelines. Overhead is prohibitive for a solo researcher.

---

## Rationale

- One repository, one virtual environment, one test run covers everything.
- `from services.otfs.orbital_profile import OrbitalProfileExporter` works immediately — no packaging, no version pinning.
- The separation is still visible and permanent: `waveform/` and `services/` are distinct top-level directories in the GitHub file tree.
- When papers are ready for submission, the `waveform/` directory is the citable unit.
- The dependency arrow points only downward: `waveform/` imports from `services/`, never the reverse. This invariant must be maintained.

---

## Consequences

- All waveform code is committed to the same `main` branch as the platform code.
- A GitHub branch (`waveform-dev`) was briefly considered for separation but rejected: the work streams (`waveform/` and `services/`) do not touch the same files, so branch management adds overhead with no benefit for a solo researcher.
- When Papers 4–6 are submitted, `waveform/` will be referenced as a subdirectory of the public ILMOP repository.
