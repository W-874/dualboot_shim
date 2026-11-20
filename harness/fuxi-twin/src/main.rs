use anyhow::{Context, Result, bail, ensure};
use skyward_block::area::{Area, HEADER};
use skyward_bootimg::{BootImage, Ramdisk};
use skyward_systems::{
    boot::{START, Start},
    state::{Saved, State, Store, System},
    store::OTHER,
};
use std::{fs, path::Path};

fn area(file: &str, id: &str, spec: &str) -> Result<Area> {
    let extents = spec
        .split(',')
        .map(|piece| {
            let (at, len) = piece.split_once(':').context("extent offset:length")?;
            Ok((at.parse::<u64>()?, len.parse::<u64>()?))
        })
        .collect::<Result<Vec<_>>>()?;
    ensure!(extents.iter().all(|&(at, len)| at % HEADER == 0 && len != 0 && len % HEADER == 0), "extent is not 4 KiB aligned");
    let size = extents.iter().map(|&(_, len)| len).sum();
    let area = Area { partition: "userdata".into(), file: file.into(), id: id.into(), size, extents };
    area.targets(0, size, (0, 0))?;
    Ok(area)
}

fn state(args: &[String]) -> Result<()> {
    let [out, id, root_id, root_spec, store_id, store_spec, boot_size, init_size, boot_sha, init_sha, vendor_boot_sha, dtbo_sha, misc, labels @ ..] = args else {
        bail!("state OUT ID ROOT_ID ROOT_EXTENTS STORE_ID STORE_EXTENTS BOOT_SIZE INIT_SIZE BOOT_SHA INIT_SHA VENDOR_BOOT_SHA DTBO_SHA MISC [NAME VERSION]");
    };
    let (name, version) = match labels {
        [] => ("uwuAOSP 17 (fuxi digital twin)", "p6-fixture"),
        [name, version] => (name.as_str(), version.as_str()),
        _ => bail!("expected both name and version"),
    };
    let out = Path::new(out);
    let root = area(&format!("/data/skyward/systems/{id}.img"), root_id, root_spec)?;
    let store_area = area("/data/skyward/systems/store", store_id, store_spec)?;
    let (boot, init_boot) = (boot_size.parse()?, init_size.parse()?);
    ensure!(store_area.size == Store::size_for(boot, init_boot), "wrong store size");
    let system = System {
        id: id.clone(),
        name: name.into(),
        version: version.into(),
        filesystem: "ext4".into(),
        init: "/guest-entry".into(),
        root,
        set: Saved { entry: OTHER, boot: boot_sha.clone(), init_boot: init_sha.clone() },
        own_ramdisk: None,
        vendor_boot: vendor_boot_sha.clone(),
        dtbo: dtbo_sha.clone(),
    };
    let start = system.start();
    let state = State { store: Some(Store { area: store_area, boot, init_boot }), system: Some(system), ..State::default() };
    let state_path = out.join("systems.toml");
    state.save(&state_path)?;
    ensure!(State::load(&state_path)? == state, "state does not round trip");
    let start_text = toml::to_string(&start)?;
    ensure!(Start::parse(&start_text)? == start, "Start does not round trip");
    fs::write(out.join("start.toml"), start_text)?;
    for (name, area) in [("root.header", &start.root), ("store.header", &state.store.as_ref().unwrap().area)] {
        let header = area.header()?;
        area.check(&header)?;
        fs::write(out.join(name), header)?;
    }
    ensure!(skyward_sys::bcb::read(Path::new(misc))? == skyward_sys::bcb::Bcb::default(), "misc does not start with an empty BCB");
    Ok(())
}

fn ramdisk(args: &[String]) -> Result<()> {
    let [start, init_boot, out] = args else { bail!("ramdisk START INIT_BOOT OUT") };
    let expected = Start::parse(&fs::read_to_string(start)?)?;
    let image = BootImage::read(Path::new(init_boot))?;
    ensure!(image.kernel.is_empty() && image.header.v4_signature_size.is_some(), "expected a v4 init_boot with no kernel");
    ensure!(image.ramdisk.starts_with(&[0x02, 0x21, 0x4c, 0x18]), "expected the P7.1 legacy LZ4 ramdisk");
    let rd = Ramdisk::decode(&image.ramdisk)?;
    let start = rd.find(START).context("P7.1 ramdisk is missing Start")?;
    ensure!(Start::parse(std::str::from_utf8(&start.data)?)? == expected, "P7.1 Start does not match the twin disk");
    for name in [START, "init"] {
        ensure!(rd.entries.iter().filter(|e| e.name == name).count() == 1, "duplicate {name} in P7.1 ramdisk");
    }
    let init = rd.find("init").context("P7.1 ramdisk is missing /init")?;
    ensure!(init.mode & 0o170000 == 0o100000 && init.mode & 0o111 != 0 && init.uid == 0 && init.gid == 0, "expected a root:root executable regular /init");
    let dir = Path::new(out);
    fs::write(dir.join("init.elf"), &init.data)?;
    fs::write(dir.join("skyward-ramdisk.lz4"), &image.ramdisk)?;
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.split_first() {
        Some((cmd, args)) if cmd == "state" => state(args),
        Some((cmd, args)) if cmd == "ramdisk" => ramdisk(args),
        _ => bail!("expected state or ramdisk"),
    }
}
