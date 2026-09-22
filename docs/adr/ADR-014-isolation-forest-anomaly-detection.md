# ADR-014 — Isolation Forest for Unsupervised Anomaly Detection

**Date:** 2024-03-01  
**Status:** Accepted (revised 2024-09)  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP requires an anomaly detection model to score incoming satellite telemetry records against a trained normal envelope and raise alarms for records that deviate significantly. No labelled dataset of anomalous satellite telemetry exists — the system must detect anomalies without prior examples of what anomalies look like.

---

## Decision

An Isolation Forest model (Liu, Ting, & Zhou, 2008, 2012) trained on normal telemetry is the Layer 2 anomaly detection mechanism. A rule-based limit-checking layer (Layer 1) handles extreme single-parameter violations that the Isolation Forest may not reliably detect at contamination=0.01.

**Revised September 2024:** Contamination parameter corrected to 0.01 (from initial 0.05). Experimental evidence showed contamination=0.05 caused continuous spurious WARNING alarms on normal Scenario 3 telemetry from all 24 satellites regardless of training dataset size. Correcting contamination=0.01 eliminated all false alarms while preserving detection of genuine fault-injected anomalies. Training SQL additionally filters out records with battery_pct ≤ 5% and temperature_c ≥ 50°C, which are artefacts of high-speed simulation rather than genuinely normal operational records.

---

## Rationale

Isolation Forest is the appropriate algorithm for this use case for three reasons. First, it is explicitly designed for unsupervised anomaly detection where no labelled anomaly examples exist — it learns the structure of normal data and identifies records that are "easy to isolate" (far from the bulk of normal records in feature space) (Liu et al., 2008). Second, it scales in O(n log n) time, making it practical for training on 530,000+ records (Liu et al., 2012). Third, it is robust to high-dimensional data and feature correlations, which is important given the 10 engineered features including the `battery_solar_product` and `temp_eclipse_product` cross-terms.

Chandola, Banerjee, & Kumar (2009) survey identifies isolation-based methods as the most effective general-purpose approach for multivariate anomaly detection when no labelled training data exists — exactly ILMOP's situation.

The decision function output provides a continuous anomaly score rather than a binary label, enabling the two-tier WARNING/CRITICAL severity classification. The score is more negative for records that are easier to isolate (more anomalous).

**Why contamination=0.01:** The contamination parameter sets the decision boundary so that approximately this fraction of training records fall below it. At contamination=0.05, 5% of normal training records are flagged as anomalous by design — given that the Scenario 3 training dataset contains 530,000 records, this creates a continuous stream of spurious alarms during normal operation. At contamination=0.01, the boundary is conservative enough that normal cross-satellite telemetry produces no alarms, while genuine fault-injection physical states (battery=0% in sunlight) trigger the Layer 1 limit checker.

**Why Layer 1 is necessary:** Experimental testing (September 2024) showed that the Isolation Forest at contamination=0.01 scores battery=0% in sunlight as 0.0273 (positive — normal). This is because battery_solar_product=0 is common in eclipse records, so the model does not isolate this record quickly. Layer 1 rule-based limit checking catches unambiguous physical violations independently of the Isolation Forest score.

---

## Consequences

**Positive:**
- No labelled anomaly dataset required — trains on normal telemetry only
- Continuous score enables WARNING/CRITICAL severity tiers
- O(n log n) training scales to 530,000+ records
- contamination=0.01 eliminates cross-satellite false alarms (verified experimentally)
- Layer 1 limit checking catches extreme single-parameter violations reliably

**Negative:**
- Isolation Forest may not reliably detect single-feature extremes at contamination=0.01 — mitigated by Layer 1
- Model must be retrained when orbit type changes (ADR-016)
- Training SQL health filter (battery_pct > 5%, temperature_c < 50°C) must be maintained as operational conditions change

---

## Alternatives Considered

- **One-Class SVM:** Higher accuracy for some distributions but O(n²) to O(n³) training complexity — infeasible at 530,000 records
- **Autoencoder:** Deep learning-based reconstruction error; strong performance but requires GPU training and hyperparameter tuning
- **Statistical threshold monitoring alone:** Simple but cannot detect multivariate anomalies (e.g., low battery during expected charging window)

---

## References

- Chandola, V., Banerjee, A., & Kumar, V. (2009). Anomaly detection: A survey. *ACM Computing Surveys*, 41(3), 15:1–15:58. https://doi.org/10.1145/1541880.1541882
- Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008). Isolation forest. *Proceedings of the 8th IEEE International Conference on Data Mining (ICDM 2008)*, 413–422. https://doi.org/10.1109/ICDM.2008.17
- Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2012). Isolation-based anomaly detection. *ACM Transactions on Knowledge Discovery from Data*, 6(1), 3:1–3:39. https://doi.org/10.1145/2133360.2133363
- Pedregosa, F., Varoquaux, G., Gramfort, A., et al. (2011). Scikit-learn: Machine learning in Python. *Journal of Machine Learning Research*, 12, 2825–2830. http://jmlr.org/papers/v12/pedregosa11a.html
