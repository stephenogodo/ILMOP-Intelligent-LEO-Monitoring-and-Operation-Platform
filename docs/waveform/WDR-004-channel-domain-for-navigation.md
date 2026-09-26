# WDR-004 — DD-domain channel model is the correct simulation path for navigation

**Date:** September 2026
**Status:** Accepted — discovered during test development
**Author:** Stephen O. Ogodo

---

## Context

`DelayDopplerChannel` provides two methods for applying the channel:

1. `apply(X_dd)` — applies the channel directly in the delay-Doppler domain (2D circular convolution).
2. `apply_through_time_domain(s, grid)` — applies the channel in the time domain by delaying and phase-rotating the transmitted samples.

The navigation pipeline test was initially written using `apply_through_time_domain` with a sub-symbol delay of `k_p = 5` samples. The echo was expected at delay bin 5 in the received `Y_DD`. It was not found there.

---

## Finding

In OTFS with rectangular pulse shaping:

- A time-domain delay of `k_p` **samples** (where `k_p < M`) does NOT map to delay bin `k_p` in the delay axis (axis 0 of `Y_DD`).
- It maps to Doppler bin `k_p` in axis 1 of `Y_DD`.

**Why:** The Heisenberg transform processes M samples per row (one OFDM symbol). A sub-symbol delay affects the phase relationship across sub-carriers within one symbol, which maps to the Doppler (sub-carrier) axis after the Wigner-Ville filter and SFFT, not the delay (symbol) axis.

A time-domain delay maps to delay bin `k_delay` (axis 0) only when the delay is an integer multiple of `M` samples (i.e., an integer number of OFDM symbol durations). This is because OTFS delay bins correspond to inter-symbol delays, not intra-symbol sample delays.

---

## Decision

**The DD-domain `channel.apply()` method is the correct and standard simulation path for navigation and all waveform research.**

The time-domain `apply_through_time_domain()` method is physically accurate for hardware simulation but requires symbol-aligned delays (multiples of `M` samples) to produce clean DD-domain tap positions. It is retained for hardware-in-the-loop experiments.

---

## Rationale

The DD-domain channel model `Y[k,l] = h_p · X[(k-k_p)%N, (l-l_p)%M]` is the standard formulation in the OTFS literature (Raviteja et al. 2018a, Eq. 9). All OTFS navigation and sensing papers use this model. The time-domain path is only needed when simulating hardware timing effects.

For the thesis navigation contribution, the relevant claim is: "the pilot echo at delay bin `k_echo` gives pseudorange `ρ̂ = k_echo × Δr`." This claim is about the DD-domain model, not the physical sample delay.

---

## Consequences

- All navigation tests use `channel.apply(X_dd)` (DD domain), not `apply_through_time_domain`.
- The navigation pipeline in `exp2_navigation.py` will use `channel.apply()`.
- The hardware-in-the-loop experiment (Chapter 5) will use `apply_through_time_domain` with symbol-aligned delays, and the echo will be found at the expected DD bin after accounting for the M-sample symbol duration.
- This distinction must be clearly stated in the thesis (Chapter 4, Section 4.2) to avoid reviewer confusion.
