---
name: ghidra-dataflow
description: Use the workspace Ghidra MCP server for typed data-flow, C++ structure and state-machine investigation, and identity-checked cross-binary comparisons.
---

# Ghidra MCP data-flow research

This is a repository-authored usage skill, not an upstream skill package.
The headless server is built against the qualified Ghidra version; it does not
install a plugin into the existing REA environment. See
[the toolchain guide](../../../docs/TOOLCHAIN.md).

1. Start from a concrete unsupported boundary and consult
   [related research](../../../docs/RELATED_REPOSITORIES.md) and
   [the REA workflow](../../../docs/REA_WORKFLOW.md).
2. Discover the `ghidra` MCP tools and inspect metadata. Use a separate project
   under the workspace MCP data root. Stage only explicitly selected,
   hash-checked inputs in its inputs directory; preserve the originals.
3. Name the program explicitly in every program-scoped request. Record model,
   firmware version, SHA-256, Ghidra version, load bias and ARM/Thumb coordinates.
   Verify the processor dialect rather than trusting the loader's default.
   Open the actual `.gpr` file, not a guessed extensionless path. Never switch
   to a different instance to work around an error.
4. Investigate callers, callees, xrefs and actual field accesses before assigning
   a structure or guard condition. Trace a value to its original instructions.
   Preserve unresolved indirect calls, runtime state and missing dependencies.
5. Cross-binary function matches are leads. Verify instructions, relocations,
   ABI and relevant runtime state before transferring a conclusion between bodies.
   Do not automatically propagate annotations or assume shared hardware contracts.
6. Preserve failures and refutations in evidence. Static data flow and isolated
   emulation are not boot, UI, device compatibility or deployment-safety proof.

Script execution and debugger proxies are disabled. The launcher owns and
stops its backend when the stdio client exits. It uses authenticated loopback
transport with a per-launch token; do not expose it on a network or change the
qualified REA/Ghidra installation to make a tool call work.
