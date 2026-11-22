# Adapted ZV-E10 menu on the upstream QEMU board

This experiment uses upstream's pinned QEMU 11.1.3 `sony-a6400` board with an
explicit `zve10-menu-boot=on` policy. It runs a rebuilt UI kernel and a prepared,
private AP-only menu image. It does not qualify stock ZV-E10 boot, a hardware
body profile, physical buttons, capture, recording, or live view.

The earlier [external menu receipt](evidence/external-zve10-menu/README.md)
preserves the standalone-machine observation and adaptation details. The new
launcher lives in this repository and uses the existing board and shared QMP
client. The stock board, launcher and target qualification remain the baseline.

## Boot and display contracts

| Contract | Stock default | Explicit adapted menu |
| --- | --- | --- |
| Kernel entry/load | `0x00108000` | `0x00138000` |
| Initrd address / maximum | `0x00700000` / 8 MiB | `0x10000000` / 256 MiB |
| CPU count | Existing board setting | Exactly one; launcher selects Cortex-A9 |
| Boot parameters | Sony `kemco` at `0x00103000` | Linux ATAGS at `0x100`, plus existing `kemco` block |
| Display | Existing VPH model | Same VPH model; guest publishes completed RGBA4444 frames to ch53 |
| Input | Existing physical-control experiments | Adapted native FunctionID events over guest UART |

The guest bridge is [zve10_menu_osd.h](../qemu-a6400/guest/zve10_menu_osd.h).
Call `pmca_menu_present(ne_latest.physical)` only when the adapter retires the
coherent latest frame. It programs VPH format, pitch, row count and framebuffer
address. It directly publishes the guest-owned framebuffer; it does not copy,
flatten or redraw pixels. Include it as `menu_osd.h` in the prepared
adapter build. Preserve original inputs and append a separate generated CPIO
overlay containing the recompiled adapter.

The application adapter still selects AP-only services, replaces supported
GPU/decode work with CPU implementations, and injects native function events.
The English option overlays original English banks at the paths selected by
the captured locale. It does not modify the source capture or translate text.
Some vertical-focus redraws remain incomplete. These policies are inherited
from the external experiment, not inferred physical camera contracts.

## Private input declaration

Build the board using [the QEMU setup workflow](../qemu-a6400/README.md).
The experiment consumes prepared inputs; a complete upstream source-to-image
adapter builder is not supplied in this increment. Do not run scripts from a
read-only reference checkout. Record the exact source manifest and toolchain
used to build the adapter, and keep generated artifacts below `artifacts/`.

Create ignored `qemu-a6400/zve10-menu.inputs.local.json` with this schema,
replacing the explanatory hash strings with actual SHA-256 digests:

```json
{
  "schema_version": 1,
  "profile": "zve10-adapted-menu-v1",
  "language": "english",
  "source_manifest_sha256": "SHA256_OF_SOURCE_MANIFEST",
  "guest_bridge_sha256": "SHA256_OF_TRACKED_GUEST_BRIDGE",
  "inputs": {
    "kernel": {"path": "artifacts/zve10-menu/inputs/Image", "sha256": "SHA256_OF_RAW_IMAGE"},
    "initrd": {"path": "artifacts/zve10-menu/inputs/menu.cpio", "sha256": "SHA256_OF_DERIVED_CPIO"},
    "source_manifest": {"path": "artifacts/zve10-menu/inputs/source-manifest.json", "sha256": "SHA256_OF_SOURCE_MANIFEST"}
  }
}
```

The raw kernel must be built for `0x00138000`; a uImage wrapper is rejected.
The CPIO must include the adapted UART transport, the tracked VPH guest bridge,
and a PID 1 that waits for its application rather than repeatedly forking
process diagnostics. `language` is metadata about the prepared image, not a
runtime language patch. `captured` is also accepted.

Every input is size bounded and hash checked. Paths must resolve below this
repository's ignored `artifacts` directory, including symlink resolution.
The source manifest pin is checked against its declared file, and the bridge
pin against the tracked header. Pins establish input identity, not proof that
a supplied CPIO was built honestly from the declared sources. The replay
checks actual guest behavior separately.

## Launch and replay

From the repository root, using the installed research Python environment:

```bash
python -m tools.research.qemu_zve10_menu --qemu /path/to/qemu-system-arm --check
python -m tools.research.qemu_zve10_menu --qemu /path/to/qemu-system-arm \
  --output artifacts/zve10-menu/run-001 --seconds 180 --keys RIGHT
```

The same module runs with native Windows QEMU. Provide the built executable's
runtime DLL dependencies on `PATH`. On Windows, the unrelated POSIX `hostwin`
option is explicitly rejected. No WSL installation is required for this
launcher; Linux native checks are a separate validation platform.

`--check` verifies the declaration and prints the exact command without
executing it. Runs bind UART and QMP only to numeric loopback endpoints, attach
no storage or USB device, and create a new output directory. A port allocation
race fails the run; it never selects another endpoint silently.

Only `UP`, `DOWN`, `LEFT`, and `RIGHT` are supported. The launcher corrects the
reference transport's reversed vertical wire labels, checks input sequence,
and waits for a later retired frame with the consumed sequence. It requires
initial glyph readiness, compares upstream QMP scanout to guest physical
RGBA4444 memory with alpha over black, and requires three seconds of stable
pixels. A nonblank matching frame alone is not a semantic menu-page oracle.
The private acceptance replay supplies reviewed initial-image and per-event
title hashes to the same bounded capture loop. It waits for those images
without resending input or replacing pixels; an unmatched oracle fails.

A bounded background reader continuously drains and records UART output,
including while QMP performs screenshots. The host does not leave a verbose
guest blocked on an unread serial socket. Kernel panic and exception-monitor
markers fail the observation; no input or boot retry is automatic.

The receipt records input/executable hashes, command, QMP version, native
receipts, frame identity, and shutdown. `NE_STOPPED` and QEMU exit zero are
required for completion. Failure receipts and serial/stderr logs are retained;
forced process cleanup is identified separately. Reports and raw framebuffer
bytes stay ignored. Publish only reviewed, sanitized metadata and screenshots.

## Tests and remaining work

```bash
python -m unittest discover -s tests -p 'test_qemu_zve10_menu*.py' -v
BIONZX_QEMU_TEST_BINARY=/path/to/qemu-system-arm \
  python -m unittest discover -s tests -p test_qemu_zve10_menu_native.py -v
```

The constructed native tests inspect both boot stubs and actual loaded bytes,
check ATAG initrd metadata, exercise an initrd above the stock 8 MiB limit,
and reject multi-CPU adapted startup. They use no firmware. The dedicated
[Linux CI workflow](../.github/workflows/qemu-boot-contracts.yml) builds pinned
QEMU and runs these checks. An unset binary skips native coverage explicitly.

The private replay test opts in only when both `BIONZX_QEMU_TEST_BINARY` and
`BIONZX_ZVE10_MENU_INPUTS` are set. It checks the reviewed complete initial RGB image hash,
the page-2 title RGB hash, a later completed frame, and normal transport/process
shutdown. Do not enable private inputs in public CI or upload them as artifacts.

Remaining work includes an upstream adapter builder, broader reviewed page
oracles, physical controls, and replacing
adaptations with measured stock service/hardware contracts. The existing body
target remains reference-only. See [integration validation](evidence/external-zve10-menu/integration-validation.md)
for the actual executed platform and replay result; CI submission is not a CI pass.
