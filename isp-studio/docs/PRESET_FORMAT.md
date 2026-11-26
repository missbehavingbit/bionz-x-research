# `.bzlook` preset format

A `.bzlook` file is a ZIP container. The intent is camera-independent. Compiled tables are optional and can be rebuilt on load.

```
look.toml
intent/recipe.toml             # FilmRecipe3D fields, or omitted when the intent is a cube
intent/look.cube               # optional reference cube
compiled/<target>.bin          # Photo table: Still-JPEG chain (BZLK v1)
compiled/<target>.json         # Photo fit report
compiled/<target>_movie.bin    # optional Movie table: Movie EC record-31 (BZLK v2)
compiled/<target>_movie.json   # Movie fit report
thumb.png
```

A preset without `_movie.bin` still loads: Movie mode falls back to the Photo table (documented in
the app status line). `look.toml` records `name`, `author`, `version`, `uuid`, `intent_kind`, `tags`,
`notes`, `created_with`, and `cube_sha256` when a cube is present.

## BZLK v1

Little-endian payload, then a CRC32 of that payload:

| Field | Type |
| --- | --- |
| magic | `BZLK` |
| version | u16 = 1 |
| target key | 32 bytes, NUL padded |
| av-cam SHA-256 | 32 bytes, zero when the stock is the synthetic fixture |
| flags | u32, bit 0 = synthetic stock |
| LUT level | u16 |
| EC sim | 96 × u32 |
| FC sim | 96 × u16 |
| A0, A1, B2 | 9 × i16 each |
| RefAdr | 1215 bytes |
| stock EC, FC, A0, A1, B2 | same shapes as the sim tables |

No camera image bytes are stored. A preset that holds only an intent is valid.

## BZLK v2 extension (Movie table)

`compiled/<target>_movie.bin` is a BZLK payload with version 2, followed after the stock tables by:

| Field | Type |
| --- | --- |
| magic | `BZX2` |
| mask | u32: bit 0 = preset base matrix, bit 1 = stock base matrix, bit 2 = EC exponents |
| base matrix | 9 × i16, when bit 0 is set (Color Mode / B8) |
| stock base matrix | 9 × i16, when bit 1 is set |
| EC exponents | 9 × u8, when bit 2 is set (record 31 = `[3,3,4,4,3,3,3,4,3]`) |

Movie tables keep the creative look in EC (record-31 knots), the A0 colour matrix and the RefAdr
residual LUT; FC is bypassed at render time. `bionz-cli pack` deliberately refuses tables with a base
matrix or spatial `/usr/sabin` data: the Film Sims header addresses are still/live-view, and the Movie
B8/record-31 path belongs to `mods/arri_isp/arrivederci`.

`bionz-cli pack` reads these tables and writes `film_sim_data.h` or `film_sim_live_data.h` in the form `live_picker/film_sim_live.c` already includes.
