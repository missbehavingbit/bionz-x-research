//! PyO3 entry points. Python keeps its fitter when this module is absent.

use std::path::Path;

use bionz_core::{apply_ycc, ycc_to_rgb, Profile};
use bionz_fit::decompose;
use bionz_preset::{emit_film_sim_data, emit_live_header, load_bzlook, parse_cube, CompiledTables};
use pyo3::prelude::*;
use pyo3::types::PyDict;

fn value_error(msg: impl Into<String>) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(msg.into())
}

/// The exact target table, never another target's table under this target's addresses. Synthetic
/// stock is refused unless allowed, because the mod restores the stock arrays on the camera.
fn target_table(path: &str, target: &str, allow_synthetic_stock: bool) -> PyResult<(String, CompiledTables)> {
    let look = load_bzlook(Path::new(path)).map_err(|e| value_error(e.to_string()))?;
    let table = look
        .compiled
        .iter()
        .find(|(key, _)| key == target)
        .map(|(_, t)| t.clone())
        .ok_or_else(|| value_error(format!("{path} has no {target} table")))?;
    if table.has_synthetic_stock() && !allow_synthetic_stock {
        return Err(value_error(format!(
            "{path}: the {target} stock tables are synthetic; a camera header would restore them (imaging hazard H01)"
        )));
    }
    Ok((look.meta.name, table))
}

#[pyfunction]
fn simulate(cube_text: &str, u: Vec<f64>) -> PyResult<Vec<f64>> {
    let cube = parse_cube(cube_text).map_err(|e| value_error(e.to_string()))?;
    let profile = Profile::a6400();
    let (preset, _) = decompose(&cube, &profile);
    let ycc = apply_ycc(&preset, &profile, &u);
    let rgb = ycc_to_rgb(&ycc, &profile);
    Ok(rgb.into_iter().map(|v| v.clamp(0.0, 1.0)).collect())
}

#[pyfunction]
fn fit_cube<'py>(py: Python<'py>, cube_text: &str) -> PyResult<Bound<'py, PyDict>> {
    let cube = parse_cube(cube_text).map_err(|e| value_error(e.to_string()))?;
    let (preset, report) = decompose(&cube, &Profile::a6400());
    let dict = PyDict::new(py);
    dict.set_item("gamma", preset.gamma)?;
    dict.set_item("matrix", preset.matrix.to_vec())?;
    dict.set_item("delta", preset.delta)?;
    dict.set_item("mean_de76", report.mean_de76)?;
    dict.set_item("at_rail_nodes", report.at_rail_nodes)?;
    Ok(dict)
}

#[pyfunction]
#[pyo3(signature = (path, target, allow_synthetic_stock = false))]
fn compile_preset(path: &str, target: &str, allow_synthetic_stock: bool) -> PyResult<String> {
    let (name, table) = target_table(path, target, allow_synthetic_stock)?;
    emit_film_sim_data(&name, target, &table).map_err(|e| value_error(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (paths, target, allow_synthetic_stock = false))]
fn pack_live(paths: Vec<String>, target: &str, allow_synthetic_stock: bool) -> PyResult<String> {
    let looks = paths.iter().map(|p| target_table(p, target, allow_synthetic_stock)).collect::<PyResult<Vec<_>>>()?;
    emit_live_header(target, &looks).map_err(|e| value_error(e.to_string()))
}

#[pymodule]
fn bionz_isp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simulate, m)?)?;
    m.add_function(wrap_pyfunction!(fit_cube, m)?)?;
    m.add_function(wrap_pyfunction!(compile_preset, m)?)?;
    m.add_function(wrap_pyfunction!(pack_live, m)?)?;
    Ok(())
}
