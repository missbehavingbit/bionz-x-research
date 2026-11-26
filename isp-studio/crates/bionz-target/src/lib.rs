use serde::Deserialize;

#[derive(Clone, Debug, Deserialize)]
pub struct TargetFile {
    pub key: String,
    pub label: String,
    pub addresses_known: bool,
    pub avcam_sha256: String,
    pub ec_gamma_pa: u32,
    pub fc_gamma_pa: u32,
    pub color_a0_pa: u32,
    pub color_a1_pa: u32,
    pub color_b2_pa: u32,
    pub cc_tuning_pa: u32,
    pub refadr0_pa: u32,
    pub color_mode_va: u32,
}

pub fn bundled() -> Vec<TargetFile> {
    let texts = [
        include_str!("../profiles/a6400.toml"),
        include_str!("../profiles/a6100.toml"),
        include_str!("../profiles/ilce_6400a.toml"),
    ];
    texts.into_iter().map(|text| toml::from_str(text).expect("target profile")).collect()
}

pub fn get(key: &str) -> Option<TargetFile> {
    bundled().into_iter().find(|t| t.key == key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a6400_addresses_match_studio_core() {
        let t = get("a6400").unwrap();
        assert!(t.addresses_known);
        assert_eq!(t.ec_gamma_pa, 0x03E55330);
        assert_eq!(t.refadr0_pa, 0x044ACD27);
        assert!(get("ilce_6400a").unwrap().addresses_known == false);
        assert!(bionz_preset::known_target("a6400").is_some());
    }
}
