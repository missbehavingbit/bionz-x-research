fn main() {
    println!("cargo:rerun-if-changed=assets/icon.ico");
    println!("cargo:rerun-if-changed=build.rs");
    #[cfg(windows)]
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/icon.ico")
            .set("ProductName", "BIONZ Look Lab")
            .set(
                "FileDescription",
                "BIONZ Look Lab - Sony BIONZ X ISP color studio and SMOD v2 exporter",
            )
            .set("LegalCopyright", "MIT License");
        res.compile().expect("failed to compile Windows resources");
    }
}
