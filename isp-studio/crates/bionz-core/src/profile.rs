use serde::Deserialize;

#[derive(Clone, Debug, Deserialize)]
pub struct Profile {
    pub name: String,
    pub gamma_size: usize,
    pub gamma_max_val: i64,
    pub matrix_q: i64,
    pub y_axis: Vec<f64>,
    pub c_axis: Vec<f64>,
    pub delta_range: Vec<i32>,
    pub permutation: Vec<usize>,
    pub permutation_verified: bool,
    pub ycc_kr_kb: Vec<f64>,
    pub y_scale: f64,
    pub output_space: String,
}

impl Profile {
    pub fn a6400() -> Self {
        let text = include_str!("../../../../artifacts/tests/bionz-golden/profile.json");
        serde_json::from_str(text).expect("a6400 golden profile")
    }

    pub fn grid(&self) -> [usize; 3] {
        [self.y_axis.len(), self.c_axis.len(), self.c_axis.len()]
    }

    pub fn n_nodes(&self) -> usize {
        let [a, b, c] = self.grid();
        a * b * c
    }

    pub fn kr(&self) -> f64 {
        self.ycc_kr_kb[0]
    }

    pub fn kb(&self) -> f64 {
        self.ycc_kr_kb[1]
    }
}
