use anyhow::{Context, Result, bail, ensure};
use skyward_sys::bcb::{Bcb, Work};
use std::{fs, path::Path};

mod request {
    include!(concat!(env!("OUT_DIR"), "/request.rs"));
}

fn regular(path: &Path) -> Result<()> {
    ensure!(fs::symlink_metadata(path)?.is_file(), "not a regular file: {}", path.display());
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [raw, scratch, state, by_name, mode] = args.as_slice() else {
        bail!("fuxi-twin-host RAW-MISC SCRATCH-MISC SYSTEMS.TOML BY-NAME MODE");
    };
    let (raw, scratch, state, by_name) = (Path::new(raw), Path::new(scratch), Path::new(state), Path::new(by_name));
    for p in [raw, state, &by_name.join("init_boot_a")] {
        regular(p)?;
    }
    let bytes = fs::read(raw)?;
    let bcb = Bcb::parse(&bytes).context("parse original misc")?;
    let selected = bcb.request();
    println!("BCB={bcb:?}\nselected={selected:?}");
    use std::io::Write;
    fs::File::create_new(scratch)?.write_all(&bytes)?;
    let outcome = request::Startup::read(&bcb, scratch,
        || request::offline_owner(state, by_name, "_a"),
        |work, why| println!("拒绝 {work:?}: {why}"))?;
    println!("dispatch={:?}", outcome.request);
    let consumed = fs::read(scratch)?;
    ensure!(consumed[2048..] == bytes[2048..], "P3.1 changed bytes outside BCB");
    ensure!(consumed[..2048].iter().all(|&b| b == 0), "P3.1 left an unexpected work request in misc");
    match outcome.request.work {
        Work::Menu if outcome.refusal.is_some() => {
            let matches = match mode.as_str() {
                "request:reset" => matches!(selected.work, Work::WipeData { .. } | Work::PromptWipeData),
                "request:rescue" => matches!(selected.work, Work::Rescue | Work::PromptWipeData | Work::WipeData { .. }),
                "request:ota" => matches!(selected.work, Work::Update(_)),
                "safe" => true,
                _ => false,
            };
            ensure!(matches, "refusal was not the expected outcome for {mode}");
        }
        Work::Menu if bcb == Bcb::default() && matches!(mode.as_str(), "safe" | "empty") => println!("无恢复请求（空 BCB）"),
        Work::SystemStopped if mode == "safe" || mode.starts_with("stopped:") => {
            ensure!(bcb.command == "boot-recovery" && bcb.args.iter().any(|x| x == "--system_stopped"), "not a system_stopped reboot");
            let reason = outcome.request.reason.filter(|x| !x.trim().is_empty()).context("empty stop reason")?;
            if let Some(want) = mode.strip_prefix("stopped:") {
                ensure!(reason.contains(want), "wrong stop reason: {reason:?}, want {want:?}");
            }
            println!("报告原因 {reason:?}");
        }
        _ => bail!("unsafe or unexpected dispatch: {mode} -> {:?}", outcome.request.work),
    }
    Ok(())
}
