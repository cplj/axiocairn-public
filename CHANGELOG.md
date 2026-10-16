# Changelog

## 0.2.0

- Reject HTTP redirects and destinations outside the literal loopback development host.
- Bound input bytes and receipt counts with explicit errors and stopped recovery.
- Pin the non-root container base by patch version and digest, and pending actions by commit.

- Real local Temporal, Inngest and Restate workers using the official Python SDKs.
- HTTP file-object adapter with caller-selected file bytes and independent effect readback.
- Explicit lost-acknowledgement and unavailable-evidence fault switches.
- Offline `axiocairn verify --receipts DIR` with nonzero unresolved/error exits.
- Copyable worker files, local runner, pinned optional SDK dependencies and receipt schema.

## 0.1.0

- Standard-library Python SDK, CLI, and local HTTP recovery endpoint.
- Existing-only SQLite receipt recovery with explicit unresolved outcomes.
- Simulated ledger and append tools, synthetic examples, and contract tests.
- Preserve stored completion when evidence is unavailable or empty; retain manual review for conflicting or malformed receipts.
- Apache-2.0 licensing, machine-readable contracts, and local container recipe.

No hosted service or distributed reliability guarantee is included.
