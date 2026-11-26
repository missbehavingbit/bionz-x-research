//! Headless entry points: the same preset library and `.mod` export the Export dialog uses, without
//! opening a window or touching the GPU.
//!
//! `bionz-look-lab.exe --export-mod <DIR> [--kind single|live] [--look <NAME>] [--bodies both|a6400|a6100]`
//! prints a JSON receipt and exits. This is how the portable build proves, from a folder that holds
//! nothing but the executable, that the twelve built-in presets loaded and that the export wrote the
//! files the dialog writes. Without a recognised flag the process starts the GUI as usual.

use std::ffi::OsString;
use std::path::PathBuf;

use crate::bundled_presets;
use crate::looks::{self, ModTargetSelection};

pub const USAGE: &str = "usage: bionz-look-lab.exe [--export-mod <DIR> [--kind single|live] [--look <NAME>] [--bodies both|a6400|a6100]]";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Single,
    Live,
}

fn json_receipt(value: serde_json::Value) -> i32 {
    println!("{value}");
    0
}

/// Run a headless request, or return `None` when the arguments are a normal GUI launch.
pub fn run(args: &[OsString]) -> Option<i32> {
    let mut dir: Option<PathBuf> = None;
    let mut look: Option<String> = None;
    let mut targets = ModTargetSelection::Both;
    let mut kind = Kind::Single;
    let mut help = false;
    let mut export_requested = false;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.to_str().unwrap_or_default() {
            "--export-mod" => {
                export_requested = true;
                dir = iter.next().map(PathBuf::from);
            }
            "--kind" => {
                let value = iter.next().and_then(|v| v.to_str()).unwrap_or_default().to_string();
                kind = match value.as_str() {
                    "single" => Kind::Single,
                    "live" => Kind::Live,
                    other => {
                        eprintln!("--kind takes single or live (got {other:?})\n{USAGE}");
                        return Some(2);
                    }
                };
            }
            "--look" => look = iter.next().map(|v| v.to_string_lossy().into_owned()),
            "--bodies" => {
                let value = iter.next().and_then(|v| v.to_str()).unwrap_or_default().to_string();
                targets = match value.as_str() {
                    "both" => ModTargetSelection::Both,
                    "a6400" => ModTargetSelection::A6400,
                    "a6100" => ModTargetSelection::A6100,
                    other => {
                        eprintln!("--bodies takes both, a6400 or a6100 (got {other:?})\n{USAGE}");
                        return Some(2);
                    }
                };
            }
            "--help" | "-h" if args.len() == 1 => help = true,
            _ => {}
        }
    }
    if !export_requested {
        if help {
            println!("{USAGE}");
            return Some(0);
        }
        return None;
    }
    let Some(dir) = dir else {
        eprintln!("--export-mod needs a destination folder\n{USAGE}");
        return Some(2);
    };
    Some(export_mod(args, dir, look.as_deref(), targets, kind))
}

/// The Export dialog's write path, driven from the command line.
fn export_mod(args: &[OsString], dir: PathBuf, look: Option<&str>, targets: ModTargetSelection, kind: Kind) -> i32 {
    let preset_dir = looks::find_preset_dir();
    let (library, errors) = looks::load_library_with_bundled(preset_dir.as_deref());
    if library.is_empty() {
        println!(
            "{}",
            serde_json::json!({
                "ok": false,
                "error": "no presets loaded",
                "errors": errors,
                "args": args.iter().map(|a| a.to_string_lossy().into_owned()).collect::<Vec<_>>(),
            })
        );
        return 1;
    }
    let (chosen, result) = match kind {
        Kind::Live => (library[0].name.clone(), looks::export_live_mods_report(&dir, &library, targets)),
        Kind::Single => match look {
            Some(wanted) => {
                let needle = wanted.to_ascii_lowercase();
                match library.iter().find(|e| e.name.to_ascii_lowercase().contains(&needle)) {
                    Some(entry) => (entry.name.clone(), looks::export_mods_report(&dir, entry, targets)),
                    None => {
                        println!(
                            "{}",
                            serde_json::json!({
                                "ok": false,
                                "error": format!("no preset name contains {wanted:?}"),
                                "available": library.iter().map(|e| e.name.clone()).collect::<Vec<_>>(),
                            })
                        );
                        return 1;
                    }
                }
            }
            None => (library[0].name.clone(), looks::export_mods_report(&dir, &library[0], targets)),
        },
    };
    match result {
        Ok((summary, detail)) => {
            let files: Vec<serde_json::Value> = detail
                .lines()
                .map(|line| {
                    let path = PathBuf::from(line);
                    let bytes = std::fs::read(&path).unwrap_or_default();
                    let mut magic = [0u8; 4];
                    magic.copy_from_slice(&bytes[..4.min(bytes.len())]);
                    serde_json::json!({
                        "path": line,
                        "bytes": bytes.len(),
                        "magic": String::from_utf8_lossy(&magic),
                        "sha256": bionz_compare::file_sha256(&path).unwrap_or_default(),
                    })
                })
                .collect();
            json_receipt(serde_json::json!({
                "ok": true,
                "kind": match kind { Kind::Single => "single", Kind::Live => "live" },
                "presets_loaded": library.len(),
                "presets_built_in": bundled_presets::COUNT,
                "preset_dir": preset_dir.as_ref().map(|p| p.display().to_string()),
                "warnings": errors,
                "look": chosen,
                "bodies": targets.targets(),
                "destination": dir.display().to_string(),
                "files": files,
                "dialog_status": summary,
            }))
        }
        Err(err) => {
            println!("{}", serde_json::json!({ "ok": false, "look": chosen, "kind": match kind { Kind::Single => "single", Kind::Live => "live" }, "error": err }));
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_plain_image_argument_is_not_a_headless_request() {
        assert!(run(&[OsString::from("photo.ARW")]).is_none());
        assert!(run(&[]).is_none());
    }

    #[test]
    fn bad_arguments_are_rejected_without_a_window() {
        assert_eq!(run(&[OsString::from("--export-mod")]), Some(2), "--export-mod needs a folder");
        let bad = [OsString::from("--export-mod"), OsString::from("out"), OsString::from("--bodies"), OsString::from("a7")];
        assert_eq!(run(&bad), Some(2));
        assert_eq!(run(&[OsString::from("--help")]), Some(0));
    }

    #[test]
    fn headless_export_writes_both_bodies_from_the_built_in_presets() {
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-headless-export");
        let _ = std::fs::remove_dir_all(&dir);
        let args = [
            OsString::from("--export-mod"),
            dir.clone().into_os_string(),
            OsString::from("--look"),
            OsString::from("Classic Chrome"),
            OsString::from("--bodies"),
            OsString::from("both"),
        ];
        assert_eq!(run(&args), Some(0));
        let a6400 = dir.join("a6400").join("mods").join("ClassicChrome.mod");
        let a6100 = dir.join("a6100").join("mods").join("ClassicChrome.mod");
        for path in [&a6400, &a6100] {
            let blob = std::fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
            assert_eq!(&blob[..4], b"SMOD");
            assert_eq!(u32::from_le_bytes(blob[4..8].try_into().unwrap()), 2);
            assert_eq!(u32::from_le_bytes(blob[8..12].try_into().unwrap()), 16, "FLAG_ACTION");
            assert_eq!(&blob[12..26], b"Classic Chrome");
        }
        let bad = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-headless-missing");
        let _ = std::fs::remove_dir_all(&bad);
        let args = [OsString::from("--export-mod"), bad.clone().into_os_string(), OsString::from("--look"), OsString::from("No Such Look")];
        assert_eq!(run(&args), Some(1), "an unknown look name must fail, not write a different preset");
        assert!(!bad.exists());
    }
}
