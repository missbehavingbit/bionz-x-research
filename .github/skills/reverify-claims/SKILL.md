---
name: reverify-claims
description: Verify byte and binary-structure hypotheses and restore a hash-keyed ledger of observations and refutations with the workspace reverify MCP. Use to avoid repeating disproven firmware hypotheses across research sessions.
---

# Byte claims and durable refutations

This is a repository-authored usage skill, not an upstream skill package.
See [the toolchain guide](../../../docs/TOOLCHAIN.md) for the isolated installation.

1. Discover the `reverify` MCP tools and inspect `re_backends`. Use only an
   explicitly selected, hash-checked copy under the workspace MCP inputs root.
   Never pass physical devices, camera paths or arbitrary host directories.
2. Restore `re_ledger` for the exact binary before proposing a new claim.
   Retain refuted hypotheses and their original receipts; do not clear evidence.
3. Use `re_verify_claim` for bytes, typed reads, strings, patterns and binary
   sections/imports/exports. State the address space and independently verify
   offset translations. Check every verdict and receipt, not just tool success.
4. Keep observed bytes, inferred meaning and runtime verification distinct.
   A correct field value does not verify its interpretation or a hardware/OS
   contract. The ledger complements, not replaces, repository evidence records.
5. Preserve source hashes, tool/backend versions, claims, actual observations
   and counterexamples in a new report directory. Restore the ledger after
   context reset instead of re-proposing a known-false explanation.

The workspace adapter rejects instruction, CPU-emulation, source-execution and
equivalence claims. Upstream ARM/Thumb mode handling and zero-filled read
fallbacks are not qualified for this project. Use the existing guarded
ARM/Thumb tools for those experiments, never an upstream fallback to advance
firmware execution. No external model, rollover hook or Frida process is used.
