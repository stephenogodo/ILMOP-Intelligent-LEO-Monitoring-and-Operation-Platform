# WDR-003 — Delay-Doppler channel circular shift convention

**Date:** September 2026
**Status:** Accepted — corrected after test failure
**Author:** Stephen O. Ogodo

---

## Context

The delay-Doppler channel input-output relationship is (Raviteja et al. 2018a, Eq. 9):

```
Y_DD[k, l] = Σ_p h_p · X_DD[(k - k_p) % N, (l - l_p) % M] + noise
```

This means a tap at `(k_p, l_p)` shifts the input `X_DD` so that the pilot at
`(0, 0)` produces an echo at `(k_p, l_p)` in the output `Y_DD`.

The question is: what NumPy `np.roll` call implements `X_DD[(k - k_p) % N, ...]`?

---

## Decision

**Use `+k_p` (positive shift), not `-k_p`.**

```python
Y_dd += h_p * np.roll(np.roll(X_dd, +k_p, axis=0), +l_p, axis=1)
```

---

## Discovery process

The original implementation incorrectly used `-k_p`:

```python
Y_dd += h_p * np.roll(np.roll(X_dd, -k_p, axis=0), -l_p, axis=1)  # WRONG
```

This placed the echo at `(N - k_p, M - l_p)` instead of `(k_p, l_p)`.

**Proof of correct convention:**

`np.roll(X, shift=+k_p, axis=0)[k, l] = X[(k - k_p) % N, l]`

Therefore:

```python
np.roll(X_dd, +k_p, axis=0)[k_p, 0] = X_dd[(k_p - k_p) % N, 0] = X_dd[0, 0]
```

So for a pilot at `X_dd[0, 0] = 1`, `np.roll(X_dd, +k_p, axis=0)[k_p, 0] = 1`.
The echo correctly appears at delay bin `k_p`. ✓

This was caught by `TestDelayDopplerChannel::test_pilot_echo_at_correct_delay`
in commit 1c40ee0.

---

## Consequences

- All uses of `np.roll` for channel delay operations use **positive** shift values.
- The test `test_pilot_echo_at_correct_delay` is the regression guard: it places a pilot at `(0, 0)`, applies a tap at `(k_p=2, 0)`, and asserts the echo is at `(2, 0)`.
- This convention must be documented in any paper that describes the `DelayDopplerChannel` implementation to avoid confusion with sign conventions in some papers.
