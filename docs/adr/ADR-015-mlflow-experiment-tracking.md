# ADR-015 — MLflow for ML Experiment Tracking and Model Registry

**Date:** 2024-03-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP trains Isolation Forest models on satellite telemetry and deploys them to a live anomaly detection service. Multiple training runs occur as more data is collected, contamination parameters are tuned, and orbit types are added. Without experiment tracking, it is impossible to reproduce a specific model, compare training runs, or audit which model version produced a specific alarm.

---

## Decision

MLflow is used for experiment tracking, model logging, and model registry. Trained models are registered as versioned artifacts (`ilmop-anomaly-leo_circular`, `ilmop-anomaly-heo_molniya`) and the detector service loads the "latest" production version from the registry.

---

## Rationale

MLflow (Zaharia et al., 2018) provides the three components required by ILMOP's ML lifecycle: an experiment tracking server (logs parameters, metrics, and artifacts per training run), a model registry (versioned model storage with promotion workflow), and a model loading API (`mlflow.sklearn.load_model("models:/name/latest")`).

The file store backend (`mlruns/`) is appropriate for development: no database or object storage server is required. The Sprint 7 Azure migration will switch to an MLflow tracking server backed by Azure Blob Storage and Azure SQL — a configuration-only change enabled by MLflow's pluggable backend architecture.

Logging the training SQL filter parameters (`battery_pct_filter`, `temperature_c_filter`), feature list, contamination value, and validation metrics per run ensures full reproducibility: given a training run ID, the exact model can be reproduced from the same database snapshot.

The model registry's versioning enables rollback: if a new training run produces a model with higher validation anomaly rate than expected, the previous version can be promoted back to "production" without retraining.

---

## Consequences

**Positive:**
- Full reproducibility: every training run logs all parameters and the trained model artifact
- Model registry versioning enables rollback to previous models
- `mlflow.sklearn.load_model` in the detector decouples model deployment from training
- File store backend requires no additional infrastructure for development
- Azure migration: switch backend to Azure Blob Storage + Azure SQL — zero application code changes

**Negative:**
- `MLFLOW_ALLOW_FILE_STORE=true` environment variable required for MLflow 3.16+ — a regression in the default file store policy
- File store grows unboundedly without periodic cleanup; production deployments should use Azure Blob Storage with lifecycle policies

---

## Alternatives Considered

- **Direct file serialisation (joblib/pickle):** Simple but no versioning, experiment comparison, or reproducibility metadata
- **DVC (Data Version Control):** Strong data versioning but more complex for model-only tracking
- **Weights & Biases:** Excellent tracking UI but cloud-only; unsuitable for air-gapped or privacy-sensitive deployments

---

## References

- MLflow Documentation. (2024). *MLflow — An open source platform for the machine learning lifecycle*. https://mlflow.org/docs/latest/index.html
- Zaharia, M., Chen, A., Davidson, A., et al. (2018). Accelerating the machine learning lifecycle with MLflow. *IEEE Data Engineering Bulletin*, 41(4), 39–45. https://databricks.com/wp-content/uploads/2018/10/PTED-mlflow.pdf
