# ADR-014: Isolation Forest for spacecraft telemetry anomaly detection

**Status:** Accepted
**Sprint:** 5
**Date:** 2026-09-11
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

---

## Alternatives considered

### Supervised classification (Random Forest, XGBoost with fault labels)

Would train on labelled examples of normal and anomalous telemetry,
learning a decision boundary between the two classes.

**Rejected because:** ILMOP has no real anomaly labels. Fault injection
(Sprint 5) produces synthetic labelled faults, but a model trained
exclusively on synthetic faults will not generalise to the real anomaly
patterns it has never seen. Supervised methods require representative
positive examples of every anomaly type of interest — an impossible
requirement for a spacecraft that has not yet experienced failures.
A semi-supervised approach using only normal training data is correct.

### One-Class SVM (sklearn.svm.OneClassSVM)

Trains a hyperplane boundary around normal data in a high-dimensional
kernel space. Effective for low-dimensional datasets with clear normal
boundaries.

**Rejected because:** OCSVM training cost scales as O(n²) to O(n³) with
the number of training samples. At 1 Hz with 24 satellites and a 7-day
retention window, the training dataset contains approximately 14.5 million
records — beyond practical OCSVM training time. Isolation Forest trains
in O(n log n) and scales to this dataset size comfortably. OCSVM also
requires careful kernel and hyperparameter tuning; Isolation Forest is
more robust to default settings.

### Autoencoder (neural network reconstruction error)

Trains a neural network to reconstruct normal telemetry records; high
reconstruction error on anomalous records triggers an alarm.

**Rejected because:** autoencoders require significantly more training
data than Isolation Forest to generalise well, and their training
stability (learning rate, architecture, vanishing gradients) requires
more tuning effort. For a research platform aiming to demonstrate
anomaly detection principles, not deep learning engineering, a simpler
interpretable algorithm is more appropriate. Autoencoder-based detection
is a Sprint 6+ enhancement if the Isolation Forest false positive rate
proves unacceptably high.

### Statistical threshold monitoring (Z-score, IQR)

Set upper and lower bounds on each telemetry field individually;
alarm when a value exceeds its bounds.

**Rejected because:** this approach misses the most operationally
significant anomaly class: physically inconsistent correlations. A
battery that is discharging during a sunlight pass, with solar panel
output at 1300W, would not trigger any individual-field threshold
alarm because each field value is within its individual normal range.
Only a multivariate method that learns the joint distribution of
(battery_pct, solar_panel_power_w, in_eclipse) can detect this class
of anomaly. The Isolation Forest operates on the full 10-feature vector
and captures these correlations.

---

## Rationale

Isolation Forest isolates anomalies by recursively partitioning the
feature space with random splits. Normal records — which cluster
together in the feature space due to eclipse-battery-thermal
correlations — require many splits to isolate. Anomalous records
— which sit in sparse, unusual regions of the feature space — are
isolated in fewer splits. The anomaly score (decision function output)
is the mean path length across the ensemble of trees; shorter path
= more anomalous.

Three properties make Isolation Forest the correct choice for ILMOP:

**1. Unsupervised by design.** The algorithm learns only from normal
data. No anomaly labels are required at training time. The `fault_injected`
filter in the training SQL (`WHERE fault_injected = FALSE`) ensures the
model learns genuine normal behaviour rather than the deliberately
degraded patterns from fault injection runs.

**2. Linear scaling.** O(n log n) training on arbitrarily large normal
datasets. A 7-day LEO dataset (≈600,000 records for a single satellite)
trains in seconds on a laptop CPU with `n_jobs=-1`.

**3. Multivariate anomaly detection.** The 10-feature vector includes
interaction terms (`battery_pct * solar_panel_power_w`,
`temperature_c * in_eclipse_int`) that capture the eclipse-correlated
joint structure. A battery at 87% with 1300W solar output and
`in_eclipse=False` is normal. A battery at 87% with 1300W solar output
and `in_eclipse=True` is physically impossible — and the interaction
feature `battery_pct * solar_panel_power_w` combined with the eclipse
flag gives the model the information to detect this.

**Severity thresholds** are set on the `decision_function` score:
- Score ≥ −0.05: normal — no alarm
- Score < −0.05: WARNING
- Score < −0.15: CRITICAL

These thresholds are configurable and should be calibrated after
generating a sufficient normal training dataset.

---

## Consequences

### Positive
- Fault-injected records are explicitly excluded from scoring via the
  `fault_injected` flag check in `detector.py`. Alarms are therefore
  guaranteed to originate from normal records (`fault_injected = False`)
  whose physical state has diverged from the trained baseline — not from
  records deliberately degraded for testing. This ensures the alarm
  pipeline is operationally meaningful even when fault injection and
  normal simulation run simultaneously.
- A further consequence: the detector catches the physical consequence
  of a fault in the honest post-fault telemetry, not the fault records
  themselves. When fault injection stops and normal simulation resumes,
  the satellite's damaged state (e.g. low battery) is reflected in
  normal records that the model scores as anomalous — making the
  detection physically meaningful rather than purely statistical.
- No anomaly labels required — the model trains on whatever normal
  telemetry the demo runner generates
- Fast training and inference — no GPU or specialised hardware needed
- Eclipse-battery-thermal correlations are learnable via interaction
  features in the 10-feature vector
- `contamination` parameter (default 0.05) explicitly models the
  expected fraction of anomalies, tunable per deployment
- Fully explainable at the feature level — when an alarm fires, the
  parameter most responsible for the anomaly score can be identified
  by feature contribution analysis

### Negative / trade-offs
- Isolation Forest is not inherently incremental — retraining requires
  a full batch run, not online updates
- The anomaly score is relative, not absolute — thresholds require
  calibration against real operational data to reduce false positives
- For very high-dimensional data or complex anomaly patterns, autoencoders
  or LSTM-based methods may outperform Isolation Forest (Sprint 6+
  enhancement path if needed)

### Implications for future sprints
- Sprint 6: per-satellite models can be trained once sufficient
  individual telemetry history accumulates (> 1 week), replacing the
  fleet-level model with satellite-specific baselines
- Sprint 7: MLflow model registry enables A/B comparison between
  Isolation Forest and autoencoder models on the same validation set
  without changing the scoring service interface
