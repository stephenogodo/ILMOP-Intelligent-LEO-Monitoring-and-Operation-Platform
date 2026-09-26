# WDR-002 — Delay-Doppler grid axis convention

**Date:** September 2026
**Status:** Accepted
**Author:** Stephen O. Ogodo

---

## Context

The OTFS delay-Doppler grid `X_DD` is a 2D NumPy array of shape `(N, M)`. A consistent axis convention must be chosen and documented so that every module in `waveform/` and every test agrees on which axis is delay and which is Doppler.

The OTFS literature uses different conventions in different papers. Hadani et al. (2017) and Raviteja et al. (2018a) both use `X[k, l]` where `k` is the delay index and `l` is the Doppler index, but do not specify which NumPy axis corresponds to which.

---

## Decision

```
X_DD.shape = (N, M)
    axis 0  (rows)    →  delay bins     k = 0, 1, ..., N-1
    axis 1  (columns) →  Doppler bins   l = 0, 1, ..., M-1
```

The corresponding time-frequency grid `X_TF.shape = (N, M)`:

```
    axis 0  (rows)    →  time slots     n = 0, 1, ..., N-1
    axis 1  (columns) →  sub-carriers   m = 0, 1, ..., M-1
```

---

## Rationale

This convention matches Raviteja et al. (2018a), Eq. (3), which defines:

```
X_TF[n, m] = Σ_k Σ_l X_DD[k, l] · exp(+j2πnk/N) · exp(-j2πml/M)
```

Mapping: delay index `k` → time slot index `n` via IDFT (axis 0).
         Doppler index `l` → sub-carrier index `m` via DFT (axis 1).

The ISFFT implementation follows directly:

```python
X_TF = np.fft.ifft(np.fft.fft(X_DD, axis=1), axis=0)
#                               ^^^^^^ DFT along Doppler (l→m)
#              ^^^^ IDFT along delay (k→n)
```

---

## Consequences

- Every call to `np.roll`, `np.fft.fft`, `np.fft.ifft` in `waveform/` must specify `axis=0` for delay operations and `axis=1` for Doppler operations.
- Test assertions on echo positions must match this convention: a channel tap at delay `k_p` produces an echo at row `k_p` of `Y_DD` (not column `k_p`).
- The `OTFSGrid` class enforces: `N` = number of delay bins, `M` = number of Doppler bins.
