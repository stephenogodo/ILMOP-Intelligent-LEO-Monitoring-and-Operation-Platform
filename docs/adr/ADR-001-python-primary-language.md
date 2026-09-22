# ADR-001 — Python as the Primary Development Language

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP requires a language capable of orchestrating multiple concerns simultaneously: orbital mechanics computation, event streaming, machine learning, REST API serving, and interactive dashboards. The language must have mature libraries for all of these domains, a strong scientific computing ecosystem, and wide availability of satellite-domain tooling.

---

## Decision

Python 3.12 is the primary development language for all ILMOP services.

---

## Rationale

Python's scientific computing ecosystem is unmatched for this combination of requirements. NumPy provides the numerical array operations underpinning the orbital mechanics and feature engineering layers (Harris et al., 2020). scikit-learn provides the Isolation Forest implementation used in the anomaly detection service (Pedregosa et al., 2011). The SGP4 orbital propagator is available as a well-maintained Python library that directly implements the Vallado et al. (2006) formulation. FastAPI provides the REST layer with automatic OpenAPI documentation (Ramírez, 2018).

No alternative language offered this combination without significant development overhead. MATLAB has the orbital mechanics tooling but lacks the event streaming and ML deployment ecosystem. Java has the streaming ecosystem but lacks the scientific computing ergonomics and the specific satellite libraries.

Python's type annotation support (PEP 484, PEP 526) combined with Pydantic v2 enables rigorous schema validation without sacrificing the productivity advantages of dynamic typing. The virtual environment system (.venv) provides reproducible dependency isolation across development and deployment environments.

---

## Consequences

**Positive:**
- Access to the full scientific Python stack: NumPy, SciPy, scikit-learn, Pandas, Matplotlib
- Direct integration with the sgp4 library implementing Vallado et al. (2006)
- FastAPI, Streamlit, and MLflow are all Python-native
- 152 automated tests run via pytest with zero configuration

**Negative:**
- Python's GIL limits true parallelism; mitigated by the event-driven architecture (services are I/O-bound, not CPU-bound)
- Runtime errors not caught at compile time; mitigated by Pydantic schema validation and comprehensive test coverage

---

## Alternatives Considered

- **Java/Kotlin:** Strong typing and concurrency, but no equivalent of scikit-learn or the sgp4 library for Python
- **Go:** Excellent concurrency model, but scientific computing ecosystem is immature
- **MATLAB:** Strong orbital mechanics tooling but poor support for production services, streaming, and ML deployment

---

## References

- Harris, C. R., Millman, K. J., van der Walt, S. J., et al. (2020). Array programming with NumPy. *Nature*, 585, 357–362. https://doi.org/10.1038/s41586-020-2649-2
- Pedregosa, F., Varoquaux, G., Gramfort, A., et al. (2011). Scikit-learn: Machine learning in Python. *Journal of Machine Learning Research*, 12, 2825–2830. http://jmlr.org/papers/v12/pedregosa11a.html
- Ramírez, S. (2018). FastAPI framework. https://fastapi.tiangolo.com
- Vallado, D. A., Crawford, P., Hujsak, R., & Kelso, T. S. (2006). Revisiting spacetrack report #3. *AIAA 2006-6753*. https://doi.org/10.2514/6.2006-6753
- Van Rossum, G., & Drake, F. L. (2009). *Python 3 Reference Manual*. CreateSpace.
