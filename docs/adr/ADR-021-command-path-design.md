# ADR-021 — Command Path Design: Approval Chain, Effectively-Once Delivery and Authentication

**Date:** 2026-11-21  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

Until Sprint 8 ILMOP only listened to a satellite: telemetry came down, alarms went out, and nothing went up. A mission operations system also has to send commands, and a command can change the state of a spacecraft in a way that cannot easily be undone (safe mode, a subsystem reboot, a change to the downlink rate). Sprint 8 (stories S8-03 to S8-08) builds the first end-to-end command path, and I have to decide how it behaves when things go wrong: an operator double-clicks, a client retries after a timeout, a message is delivered twice, the satellite is out of contact, or an automated service tries to do something that only a person should do.

The ConOps already fixes two rules that the design must respect. Every command passes through an explicit approval chain, and no automation may approve a command (ConOps sections 9.2 and 12). The ADD already defines the Kafka topics `commands.{sat}.requested`, `.approved` and `.uplinked`. This ADR records how the command logic itself is built and why.

---

## Decision

I built the command path as a layer of pure logic with no network, database or Kafka dependency, made of these decisions:

**D1 — The command path is pure logic with no I/O.** Authoring, the state machine, the store, the service and the authentication code (`services/command/`) take and return plain objects. Kafka topics, the REST API and database persistence are adapters that I add around them in later work. This keeps the safety-critical rules testable without Docker.

**D2 — An explicit state machine with one transition table.** A command moves DRAFT → APPROVED → UPLINKED → CONFIRMED, and can end as FAILED (from APPROVED or UPLINKED) or REJECTED (from DRAFT). Every change goes through one function that checks the table, so no state can be skipped, and the final states accept no further change. I added REJECTED, which the backlog did not list: without it, a draft that an operator does not want would stay in DRAFT indefinitely.

**D3 — Approval is human-only, enforced in code.** `approve()` and `reject()` refuse an actor of kind SYSTEM. In the authenticated layer (D9) the same rule applies on top of roles: an account must hold the APPROVER role and be a human OPERATOR account, so giving a service account the APPROVER role by mistake still cannot let automation approve. An optional two-person rule (the author may not approve their own command) is available and is off by default, because the project currently has a single operator.

**D4 — Commands are immutable and carry their own audit trail.** A command is a frozen object. Each transition returns a new object with one more history entry (status, actor, time, reason). Nothing is edited in place, so the history cannot be quietly rewritten and two parts of the system cannot change one command at the same moment.

**D5 — Idempotency keys at submission.** A caller may attach a key. A repeat with the same key and the same content (a fingerprint of satellite, command type and parameters) returns the original command and creates nothing. The same key with different content raises an error, because silently returning the old command would hide that the second request was different. The check and the insert happen under one lock, so concurrent submissions cannot both be created.

**D6 — The uplink guard claims before it sends (at-most-once).** To uplink an approved command, the service first moves it APPROVED → UPLINKED with an atomic compare-and-swap. Only the caller that wins that move calls the sender. Any other caller finds the command already past APPROVED and is told it was a duplicate. The trade-off is explicit: if the system crashes after the claim but before the transmission, the command is recorded as UPLINKED but was never sent. I accept that because the failure is visible (the command never reaches CONFIRMED), the operator decides whether to submit it again as a new command, and the alternative (send first, then record) can transmit a command twice, which is worse for commands such as a reboot. A sender that raises an error causes the command to be marked FAILED with the reason.

**D7 — The satellite also refuses duplicates.** The simulated satellite remembers the IDs of commands it has executed and returns the original acknowledgement if it receives the same command again, without executing it again. D6 guarantees that the ground side does not send a command twice; D7 guarantees that the satellite would not execute it twice even if it were sent twice. Together they give effectively-once handling. A rejected command is deliberately not remembered, so the ground side can resend it later (for example after contact is established).

**D8 — Uplink is checked on both sides.** The ground side only dispatches APPROVED commands (the transition table forbids anything else). The satellite independently rejects a command unless it is addressed to it, it is in contact, the command arrives with status UPLINKED, and the command type is known. A bypass of the ground logic therefore still fails at the satellite.

**D9 — Authentication by opaque session tokens; authorisation by three roles.** Accounts store a salted PBKDF2-HMAC-SHA256 hash of the secret and never the secret. Login returns a random session token, and only a hash of the token is stored. The caller never builds an `Actor`; the authenticated service builds a verified one from the token, so an identity cannot be forged. Unknown names and wrong secrets give the same error. After five consecutive failures an account is locked for five minutes. Tokens expire after thirty minutes and can be revoked. Three roles map to the stages of the path: AUTHOR (submit), APPROVER (approve, reject) and UPLINKER (uplink, confirm). Every attempt, allowed or denied, is written to a security audit log that is separate from a command's own history. I used only Python's standard library for this, with no custom cryptography.

**D10 — A catalogue defines the commands that can exist.** Each command type is declared once with its parameters, types and limits (`services/command/catalogue.py`). Validation rejects an unknown type, a malformed satellite ID, a missing or unexpected parameter, a wrong type (a boolean is not accepted where a number is expected) and an out-of-range value. It reports every problem at once. The limits are illustrative values for the simulator; the real values would come from a satellite's command database, and changing one is a one-line edit.

---

## Rationale

**Why a state machine and not a status field.** A bare status string lets any code set any value. A transition table makes the legal moves data, in one place, which can be tested exhaustively (there is a test that every status appears in the table and that final states have no exits).

**Why effectively-once and not exactly-once.** True exactly-once delivery is not achievable over an unreliable link: a sender that does not hear back cannot know whether the other side acted. Space link protocols face the same problem. COP-1, the procedure used with the CCSDS TC Space Data Link Protocol, exists to deliver frames in sequence and without gaps or duplication (CCSDS 232.1-B-2). I do not implement COP-1 here (it works at the frame level, which belongs to S8-07 and later), but D5 to D7 solve the same problem at the command level: allow retries, and make the repeat harmless through idempotency and duplicate detection.

**Why two layers of defence.** D3 and D8 each check the same properties (approved, uplinked, addressed correctly) at two independent points. A single check can be bypassed by a bug or by a caller using an internal function directly; two independent checks mean one failure is not a failure of the system.

**Why opaque tokens and not signed self-contained tokens.** A signed token (such as a JWT) can be verified without a lookup, but it cannot be revoked before it expires, and it needs key management. For a command system I want logout and lockout to take effect immediately, and the single-process design makes the lookup free. The password hashing follows the key-derivation approach in NIST SP 800-132, and the lockout and token lifetime follow the general direction of NIST SP 800-63B.

**Why the audit log is separate from the command history.** The command history records what happened to a command. The security log records who tried to do what, including attempts that were refused and attempts with no valid identity, which never touch a command at all.

---

## Consequences

**Positive:**
- The whole command path, including the race between 20 simultaneous uplink attempts, is tested without Kafka, a database or a network.
- A command cannot be uplinked unless a human approved it, cannot be sent twice by the ground side, and cannot be executed twice by the satellite.
- Every command's history names the real people and services that acted on it, because identities come from tokens.
- The command logic does not change when the transport does: Kafka topics, a database store and a real radio link replace the in-memory parts behind the same interfaces.

**Negative:**
- Claim-before-send (D6) can leave a command UPLINKED but unsent after a crash. An operator must notice a command that never reaches CONFIRMED. A later story should add a timeout that marks such commands FAILED.
- The in-memory store loses all commands, idempotency keys and sessions on restart. The idempotency guarantee therefore holds only within one process lifetime until a persistent store exists.
- The two-person rule is off by default, so with one operator an author can approve their own command.

---

## Alternatives Considered

- **Free-form commands with no catalogue.** Simpler to write, but typos and impossible parameter values would reach the approval step, where a person is poorly placed to catch them.
- **Send first, then record the uplink.** Gives at-least-once behaviour, which can transmit a command twice. For commands that are not safe to repeat, I judged a visible gap (D6) to be better than a silent duplicate.
- **Promising exactly-once delivery.** Not achievable; stating it would be misleading. The ADR claims effectively-once and says how.
- **Signed self-contained tokens (JWT).** Rejected for now for the revocation and key-management reasons above.
- **A third-party authentication library or an external identity provider.** The right choice for a production deployment, and where this should go (Sprint 9 cloud work), but too heavy for this stage and not needed to prove the command logic.
- **A single role for all privileged actions.** Rejected because authoring, approving and uplinking are different judgements made by different actors, and the approval step has the extra human-only rule.

---

## Limitations and deferred work

- Accounts are created in code at startup; there is no persistent account store or administration interface.
- Sessions are in memory. There is no TLS, no multi-factor authentication and no external identity provider.
- The REST API is still read-only and does not yet call the authenticated service; Kafka topics `commands.{sat}.*` are not yet wired.
- `SET_TELEMETRY_INTERVAL` and `REBOOT_SUBSYSTEM` only record that they ran in the simulator; the tick loop does not use the interval, and there is no real subsystem restart.
- Lost acknowledgements (the satellite executed but the ground never heard) are not simulated.
- Idempotency keys are never expired.

---

## References

- CCSDS. (2021). *TC Space Data Link Protocol* (CCSDS 232.0-B-4, Blue Book, Issue 4, incorporating Technical Corrigendum 1, October 2023). Consultative Committee for Space Data Systems.
- CCSDS. (2010). *Communications Operation Procedure-1* (CCSDS 232.1-B-2, Blue Book, Issue 2). Consultative Committee for Space Data Systems. (Also published as ISO 22667:2013.)
- Turan, M. S., Barker, E., Burr, W., & Chen, L. (2010). *Recommendation for Password-Based Key Derivation, Part 1: Storage Applications* (NIST Special Publication 800-132). National Institute of Standards and Technology.
- Grassi, P. A., Garcia, M. E., & Fenton, J. L. (2017). *Digital Identity Guidelines: Authentication and Lifecycle Management* (NIST Special Publication 800-63B). National Institute of Standards and Technology.
