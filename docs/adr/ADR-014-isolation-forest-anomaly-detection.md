# ADR-014: Isolation Forest for spacecraft telemetry anomaly detection

**Status:** Accepted (updated — contamination calibration corrected)
**Sprint:** 5
**Date:** 2026-09-11
**Last updated:** 2026-09-18
**Decider:** ILMOP Project

---

## Context

Sprint 5 introduces the anomaly detection layer. An algorithm must be
chosen to score incoming telemetry records and identify deviations from
normal spacecraft behaviour. The algorithm must satisfy four operational
requirements specific to ILMOP:

1. **Unsupervised** — there is no labelled dataset of known anomalies
   from which to learn. Real spacecraft anomaly data is proprietary and
   unavailable. The algorithm must learn "normal" from normal data only.

2. **Online-capable** — the algorithm must score each incoming record
   in real time (1 Hz per satellite, up to 24 satellites simultaneously)
   without retraining.

3. **Eclipse-correlation-aware** — the training data has strong physical
   correlation structure: battery discharges in eclipse, solar panel
   output drops to zero, temperature oscillates with the orbital period.
   The algorithm must learn this joint structure, not just univariate
   thresholds.

4. **Orbit-type separable** — LEO circular and Molniya HEO telemetry
   have incompatible statistical signatures. The algorithm must support
   training a separate model per orbit type (see ADR-016).

---

## Decision

**Isolation Forest** (`sklearn.ensemble.IsolationForest`) is the anomaly
detection algorithm for ILMOP Sprint 5. A separate model is trained per
`orbit_type` using eclipse-correlated feature vectors. Inference runs at
each incoming telemetry record via `decision_function()`.

**Contamination parameter:** The operational value is `contamination=0.01`.
The initial default of `contamination=0.05` was found to produce spurious
WARNING alarms on genuinely normal telemetry. See Consequences section.

**Training data:** The LEO_CIRCULAR model should be trained on Scenario 3
data (all 24 satellites) as a matter of good practice for a fleet anomaly
detection system. However, the training dataset does not affect the false
positive rate — that is governed exclusively by the contamination parameter.

---

## Alternatives considered

### Supervised classification (Random Forest, XGBoost with fault labels)

**Rejected because:** ILMOP has no real anomaly labels. Fault injection
produces synthetic labelled faults, but a model trained exclusively on
synthetic faults will not generalise to real anomaly patterns it has
never seen. A semi-supervised approach using only normal training data
is correct.

### One-Class SVM (sklearn.svm.OneClassSVM)

**Rejected because:** OCSVM training cost scales as O(n²) to O(n³).
At 1 Hz with 24 satellites and a 7-day retention window, the training
dataset contains approximately 14.5 million records — beyond practical
OCSVM training time. Isolation Forest trains in O(n log n) and scales
comfortably.

### Autoencoder (neural network reconstruction error)

**Rejected because:** autoencoders require significantly more training
data and tuning effort. For a research platform demonstrating anomaly
detection principles, a simpler interpretable algorithm is appropriate.

### Statistical threshold monitoring (Z-score, IQR)

**Rejected because:** this approach misses physically inconsistent
correlations — the most operationally significant anomaly class. Only
a multivariate method that learns the joint distribution of all 10
features can detect anomalies like a battery discharging during a
sunlight pass.

---

## Rationale

Isolation Forest isolates anomalies by recursively partitioning the
feature space with random splits. Normal records — which cluster
together due to eclipse-battery-thermal correlations — require many
splits to isolate. Anomalous records sit in sparse regions and are
isolated in fewer splits. The anomaly score is the mean path length
across the ensemble; shorter path = more anomalous.

**Severity thresholds** (set on the `decision_function` score):
- Score ≥ −0.05: normal — no alarm
- Score < −0.05: WARNING
- Score < −0.15: CRITICAL

---

## Consequences

### Positive
- Fault-injected records are explicitly excluded from scoring via the
  `fault_injected` flag in `detector.py`. Alarms originate exclusively
  from normal records (`fault_injected=False`) whose physical state has
  diverged from the trained baseline.
- The detector catches the physical consequence of a fault in honest
  post-fault telemetry. When fault injection stops and normal simulation
  resumes, the satellite's damaged state is reflected in normal records
  that score as anomalous — making detection physically meaningful.
- No anomaly labels required
- Fast training and inference — no GPU needed
- Eclipse-battery-thermal correlations captured via interaction features
- Fully explainable at the feature level

### Negative / trade-offs
- Not inherently incremental — full batch retraining required
- Anomaly score is relative — thresholds require calibration
- For very complex anomaly patterns, autoencoders may outperform
  Isolation Forest (Sprint 6+ enhancement path)

### Critical calibration finding — contamination parameter

**The contamination parameter is the primary determinant of false
positive rate and must be set to 0.01 for operational deployment.**

The contamination parameter sets the decision boundary so that exactly
`contamination × N` training records fall below it. At `contamination=0.05`
(the sklearn default), 5% of all records — including genuinely normal ones
— are expected to score below the WARNING threshold by design. With 24
satellites running simultaneously, this produces approximately one
satellite's worth of continuous spurious WARNING alarms on perfectly
normal telemetry.

This was verified experimentally:

| Training data | contamination | Result |
|---|---|---|
| SAT-A1 only | 0.05 | Continuous spurious WARNINGs on cross-plane satellites |
| All 24 satellites (Scenario 3) | 0.05 | Continuous spurious WARNINGs — problem persisted |
| All 24 satellites (Scenario 3) | 0.01 | No alarms on normal telemetry — correct behaviour |

The experiment confirms that **contamination=0.05 was the sole cause of
spurious alarms**, not the training dataset composition. Single-satellite
(SAT-A1) training was initially suspected as the cause but this hypothesis
was disproved — even after retraining on all 24 satellites, spurious alarms
persisted at contamination=0.05.

**Correct training command:**

```powershell
python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR --contamination 0.01
```

### Training data recommendation

Training on Scenario 3 (all 24 satellites) remains the recommended
practice for a fleet anomaly detection system. A model trained on the
full constellation learns a more representative normal operating envelope
than one trained on a single satellite, making its detection of genuine
anomalies more reliable. However, this is a quality improvement, not a
false positive fix — the contamination parameter governs false positives.

### Implications for future sprints
- Sprint 6: per-satellite models remain an enhancement path for detecting
  subtle early-stage degradation specific to individual satellites
- Sprint 7: MLflow model registry enables A/B comparison between
  Isolation Forest and autoencoder models without changing the scoring
  service interface
