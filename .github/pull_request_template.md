## Summary

<!-- Describe what changed and why. Link any relevant E0xxx evidence record or issue. -->
<!-- For camera work, link the model's tracking issue. Credit anyone whose research or code this PR incorporates and link the original work or evidence. -->

## Verification

- [ ] `python -m tools.research.ci_check` passed locally
- [ ] `python -m unittest discover -s tests -q` passed (or note hardware/firmware-gated skips)
- [ ] If status/evidence changed: `python -m tools.research.render_research_status` updated and `--check` passed

## Safety & Fidelity Guardrails

- [ ] No proprietary firmware, raw dumps, `.bin`/`.elf`/`.ko`/`.so`/`.img` files, or secrets included
- [ ] Observations, inferences, fixtures, and physical verification remain clearly separated
- [ ] Any new hardware/emulator contracts are opt-in and preserve default boundaries
