---
name: pyghidra-analysis
description: Reuse persistent Ghidra projects through the workspace pyghidra MCP for firmware decompilation, symbols, cross-references and call graphs. Use when investigating large firmware libraries or repeated static-analysis queries.
---

# Persistent PyGhidra analysis

This is a repository-authored usage skill, not an upstream skill package.
The installed server and source revisions are recorded in
[the toolchain guide](../../../docs/TOOLCHAIN.md).

1. Consult [related research](../../../docs/RELATED_REPOSITORIES.md),
   [REA guidance](../../../docs/REA_WORKFLOW.md) and
   [dependency guidance](../../../docs/DEPENDENCY_WORKFLOW.md).
   Reuse matching retained evidence before starting new analysis.
2. Discover the `pyghidra` MCP tools. List the project binaries and inspect
   metadata before querying. Use the returned program identifiers, which can
   include a hash suffix, not an assumed source basename. Use only declared,
   hash-checked inputs; never recursively import the repository, firmware tree,
   physical drives or devices.
3. Keep original inputs unchanged. Record the input SHA-256, server revision,
   Ghidra version, analysis state, address translation and exact queries in a
   new evidence directory. An existing project is not proof of matching bytes.
4. Batch related decompilation/xref queries. Check each result for errors and
   readiness; a responsive server or partial index is not completed analysis.
   Verify inferred signatures, structures and Thumb state against instructions.
5. Treat pseudocode as a hypothesis. Confirm proposed contracts with bounded
   original-code experiments, not invented OS/device replies or host application
   behavior. Do not patch firmware, run camera probes or change the REA baseline.

The stdio server keeps a persistent on-disk project, but simultaneous processes
cannot own that same project. Report a project-lock conflict rather than
deleting locks or falling back to another binary. Semantic indexing requires
its local embedding model; offline/unready search must remain an explicit limit.
