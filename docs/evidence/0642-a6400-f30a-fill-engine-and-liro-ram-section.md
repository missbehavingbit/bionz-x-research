# E0642 — 0xF30A memory-fill engine and the LIRO `ram` section consumer search

Date: 2026-11-19. Offline only: QEMU MMIO/RAM traces of the stock boot, static Thumb/ARM
disassembly of `av-cam.bin` (SHA-256 `90d515cf…125e`, base `0x635C6000`), `liro.ko`
strings (`nflasha15` `kmod/liro.ko`, SHA-256 `72cb5700…40d9f7`). No camera access.

## Question

E0641 showed that the work DMA copies `0xCEE64` bytes from `0x044D3380` to
`0x045ACF00` inside a routine that also programs the `0xF30A0000` block. Who uses the
copy, and what is `0xF30A`?

## Results

### 1. `0xF30A` is a memory-fill engine; the routine zeroes LIRO's bss [C]

The tool is the guard's `trace=0xF30A0000:0xF30B0000` (90 s, `WORK_DMA=1`, QEMU exit 0;
`e0642-f30a-trace-1`). It shows 21 accesses, all from `0x635DC810`, which runs once:

| Order | Access | Value |
| --- | --- | --- |
| 1 | `0xF3400260`, `0xF3400060` | `0x15` |
| 2 | `0xF30A1280` | `0x20` |
| 3 | `0xF30A128C` | 0 |
| 4 | `0xF30A12A0` | `0x0403A000` = PA of GOT symbol `+0x6BC4` (`0x6403A000`) |
| 5 | `0xF30A12A4` | `0x00497A26` = GOT `+0x940` (`0x644D1A26`) − base |
| 6 | `0xF30A12AC`, `+0x12B4`, `+0x12BC`, `+0x12C4` | 0 |
| 7 | `0xF30A1280` | `0x21` |
| 8 | work DMA copy (E0640) | — |
| 9 | poll `0xF30A1284` until bit 31 clears | — |
| 10 | `0xF30A1200` | `0x10` |
| 11 | `0xF3400064`, `0xF3400264` | `0x15` |
| 12 | call `0x635DCAFC` | table registration |

The routine `0x635DCA88` uses the same five GOT symbols, and its effect matches the
hardware path step for step:

- `memset(0x6403A000, 0, 0x497A26)` via `blx 0x63AB877C`.
- `memcpy`-style `(src 0x644D3380, dst 0x645ACF00, len)` via `blx 0x63AB8098`.
- Then the same `0x635DCAFC`.

So `0xF30A` is a hardware memory-fill engine: base `+0x12A0`, byte length `+0x12A4`,
fill words `+0x12AC…` (0 here), control `0x20` then `0x21` to start, busy at `+0x1284`
bit 31. The work DMA is the hardware `memcpy`. `liro.ko` prints the same size as LIRO's
`bss` (`bss 63f8a5a7 + 00497a26`). The printed base `0x63F8A5A7` differs from the GOT
base `0x6403A000`; why is not established.

**QEMU impact:** the fill region lies inside the preloaded `av-cam.bin`, and those
4,815,398 file bytes are all zero. A cold boot therefore starts with the state the fill
produces, and the unmodelled fill engine is harmless there. It would matter for any input
where that region is not pre-zeroed.

### 2. Which path runs [C]

Neither routine has a direct `bl`/`blx` caller or a stored pointer. Both are reached from
the av-cam header stubs that `liro.ko` calls:

- The `entry_pre` stub at `0x635C6044`: ARM `blx` at `0x635C6048` → `0x635DC810`
  (hardware path).
- The stub at `0x635C6068`, printed by `liro.ko` as `suspend`: `blx` at `0x635C606C` →
  `0x635DCA88` (CPU path).

On the console, `start entry_pre(635c6044)` → `end entry_pre` spans 3.6180 → 3.6227 s,
which brackets the copy. Why a suspend stub re-initialises with the CPU is not established.

### 3. The copied block is LIRO's `ram` section, built for another address map [C/I]

`liro.ko` prints `ram 644d3380 + 000cee64` (av-cam header `+0xB8`) and
`tail 6467be00 + 00000004`, which is the copy's run end `0x6467BD64` rounded up to
`0x100`. The block starts with ARM vectors whose literal targets are `0x0100C000` and
`0x01005400`…`0x0100541C`. The vectors are followed by armcc-style scatter-load startup
code (`add r0, pc, #0x28; ldm r0, {r10, r11}; add r10, r10, r0; …`). That means it is
linked for a core that sees its memory at `0x0100xxxx`, not av-cam's `0x6xxxxxxx` [I].

### 4. No main-CPU access to the run region in a bounded boot [C]

`handover_guard.c` (SHA-256 `1a38b27ee3a13528f6a219aa0751dad7873638db24172b9827ce416dbb1e1e8c`)
gains `trace_ram=1`, which also logs CPU RAM accesses in the trace range. Device (DMA)
writes are not CPU accesses and are not seen.

| Run | Range | Result |
| --- | --- | --- |
| `e0642-ram-region-workdma-1` (120 s, `WORK_DMA=1`, exit 0) | `0x045ACF00–0x0467BDFF` | 0 accesses |
| `e0642-ram-region-baseline-1` (120 s, exit 0) | same | 0 accesses |
| `e0642-ram-control-2` (40 s, positive control) | GOT word `0x045AA238` | 1 read, `pc=0x635DC890`, value `0x645ACF00` |

The control proves the mechanism works. So no main-CPU code reads or writes the `ram`
run region in this bounded boot. Its consumer is outside the emulated CPUs (another core
or engine fetching from PA `0x045ACF00`) or appears later [I]. It is not identified.

## Failures preserved

`e0642-ram-control-1.plugin-missing`: WSL `/tmp` was cleared between invocations, so QEMU
could not load the plugin (exit 1). The rerun is `-2`.

## Limits

- Fill-engine semantics for non-zero fill words, alignment and interrupts are unknown;
  only the zero fill is observed.
- Bounded boot only (≤ 120 s), stalled at the E0525 power-off-factor boundary.
  Later stages may touch the `ram` region.

## Next boundary

- Find what starts the `0x0100xxxx` core: search av-cam and kernel modules for PA
  `0x045ACF00`, for `tail`, or for a remap window programmed to that address. The writes
  `0xF3400060/0x260/0x064/0x264 = 0x15` around the routine are candidates (clock or
  reset gates [I]).
- An opt-in fill model only becomes necessary for inputs where the bss region is not
  pre-zeroed.
