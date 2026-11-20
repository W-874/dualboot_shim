use skyward_systems::{boot::{self, Start}, package};
use std::{env, error::Error, fs::{self, File}, io::Read};
use zip::ZipArchive;

fn qemu_wrap(args: &[String]) -> Result<(), Box<dyn Error>> {
    let [start, template, program, output] = args else {
        return Err("usage: skyward-package-check --qemu-wrap START TEMPLATE SYSTEM_INIT OUTPUT".into());
    };
    let start = Start::parse(&fs::read_to_string(start)?)?;
    fs::write(output, boot::init_boot(&fs::read(template)?, &fs::read(program)?, &start)?)?;
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args().skip(1).peekable();
    if args.peek().is_some_and(|arg| arg == "--qemu-wrap") {
        args.next();
        return qemu_wrap(&args.collect::<Vec<_>>());
    }
    const USAGE: &str = "usage: skyward-package-check ZIP DEVICE VERSION MINIMUM BOOT_BYTES ROOT_BYTES EXPECTED_MANIFEST";
    let [Some(path), Some(device), Some(version), Some(minimum), Some(boot_size), Some(root_size), Some(expected_manifest)] =
        std::array::from_fn(|_| args.next())
    else {
        return Err(USAGE.into());
    };
    if args.next().is_some() {
        return Err(USAGE.into());
    }
    let mut zip = ZipArchive::new(File::open(path)?)?;
    let contents = package::read(&mut zip)?;
    let m = &contents.manifest;
    let minimum: u64 = minimum.parse()?;
    let mut raw = Vec::new();
    zip.by_name(package::MANIFEST)?.read_to_end(&mut raw)?;
    if zip.len() != 3
        || contents.init_boot
        || contents.boot != boot_size.parse::<u64>()?
        || contents.root != root_size.parse::<u64>()?
        || contents.least() != minimum
        || m.minimum != minimum
        || m.id.strip_prefix("uwu-") != Some(device.as_str())
        || m.name != "uwuAOSP 17"
        || m.version != version
        || m.filesystem != "ext4"
        || m.init != "/guest-entry"
        || m.device.as_ref() != Some(&device)
        || raw != fs::read(expected_manifest)?
    {
        return Err("SkyWard package::read did not return the approved package fields".into());
    }
    println!("SkyWard package::read OK: {} {} (root {} bytes)", m.id, m.version, contents.root);
    Ok(())
}
