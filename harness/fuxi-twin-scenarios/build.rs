use std::{env, fs, path::Path};

fn main() {
    let request = "../../SkyWard-uwu-dualboot/crates/skyward/src/request.rs";
    println!("cargo:rerun-if-changed={request}");
    let source = fs::read_to_string(request).expect("read production request module");
    // include! expands in item position, where inner module docs cannot appear.
    let mut source = source.replacen("//!", "//", 1);
    source.push_str(r#"
pub(crate) fn offline_owner(state: &Path, by_name: &Path, slot: &str) -> Result<bool> {
    other_boot_set(&State::load(state)?, by_name, || {
        Slot::from_suffix(slot).context("invalid booted slot")
    })
}
"#);
    fs::write(Path::new(&env::var_os("OUT_DIR").unwrap()).join("request.rs"), source)
        .expect("write production request module with offline entry point");
}
