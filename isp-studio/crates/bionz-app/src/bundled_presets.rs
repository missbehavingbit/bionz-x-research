//! Presets compiled into the executable.
//!
//! `bionz-look-lab.exe` is meant to be portable: double-clicked from any folder, on a machine with no
//! Python, GCC or WSL. These are the twelve shipped `.bzlook` containers, byte-for-byte the files in
//! `mods/film_sims/presets/`, each carrying verified `av-cam.bin` stock tables for `a6400` and
//! `a6100` plus a Movie variant. Disk presets (`BIONZ_PRESETS`, `presets/`) still win by name, so a
//! user's own or updated presets are not shadowed.

/// `(file name, container bytes)`, in the order the library lists them.
pub const BUNDLED: &[(&str, &[u8])] = &[
    ("acros.bzlook", include_bytes!("../../../../mods/film_sims/presets/acros.bzlook")),
    ("astia.bzlook", include_bytes!("../../../../mods/film_sims/presets/astia.bzlook")),
    ("classic-chrome.bzlook", include_bytes!("../../../../mods/film_sims/presets/classic-chrome.bzlook")),
    ("classic-neg.bzlook", include_bytes!("../../../../mods/film_sims/presets/classic-neg.bzlook")),
    ("eterna.bzlook", include_bytes!("../../../../mods/film_sims/presets/eterna.bzlook")),
    ("eterna-bleach-bypass.bzlook", include_bytes!("../../../../mods/film_sims/presets/eterna-bleach-bypass.bzlook")),
    ("gold-200.bzlook", include_bytes!("../../../../mods/film_sims/presets/gold-200.bzlook")),
    ("portra-400.bzlook", include_bytes!("../../../../mods/film_sims/presets/portra-400.bzlook")),
    ("pro-negative.bzlook", include_bytes!("../../../../mods/film_sims/presets/pro-negative.bzlook")),
    ("reala-ace.bzlook", include_bytes!("../../../../mods/film_sims/presets/reala-ace.bzlook")),
    ("velvia.bzlook", include_bytes!("../../../../mods/film_sims/presets/velvia.bzlook")),
    ("vision3-800t.bzlook", include_bytes!("../../../../mods/film_sims/presets/vision3-800t.bzlook")),
];

/// Number of presets built into the executable.
pub const COUNT: usize = BUNDLED.len();
