//! Trusted board adapters. The descriptors are compiled in, never read from guest state.
use anyhow::{ensure, Context, Result};
use serde::Deserialize;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum DeviceProfile {
    Fuxi,
    Dada,
}

impl DeviceProfile {
    pub fn codename(self) -> &'static str {
        match self {
            Self::Fuxi => "fuxi",
            Self::Dada => "dada",
        }
    }
}

#[derive(Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum FormatProtocol { LegacyV1, TokenV2 }

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    schema_version: u32,
    id: String,
    pub storage: Controller,
    pub identity: Identity,
    pub mounts: Mounts,
    pub format_protocol: FormatProtocol,
    product: Product,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Controller {
    pub platform_path: String,
    pub bootdevice: String,
    pub required: bool,
}

impl Controller {
    pub fn observe(&self, misc: &std::path::Path, userdata: &std::path::Path) -> Result<Option<(&str, &str)>> {
        let prefix = std::path::Path::new("/sys/devices/platform").join(&self.platform_path);
        let matched = misc.starts_with(&prefix) && userdata.starts_with(&prefix);
        ensure!(!self.required || matched, "host partitions are not on the expected controller");
        Ok(matched.then_some((&self.platform_path, &self.bootdevice)))
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Identity { pub touch_property: String }

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Mounts {
    pub firmware: Vec<[String; 5]>,
    pub charger: Vec<[String; 5]>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Product { zygote: String }

fn safe_name(s: &str) -> bool {
    !s.is_empty() && s != "." && s != ".." && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.@,".contains(&b))
}

impl Profile {
    fn parse(text: &str, expected_id: &str) -> Result<Self> {
        let profile: Self = toml::from_str(text).context("parse embedded device.toml")?;
        ensure!(profile.schema_version == 1 && profile.id == expected_id && safe_name(&profile.id), "unknown profile version or wrong profile identity");
        ensure!(profile.storage.platform_path.split('/').all(safe_name) && safe_name(&profile.storage.bootdevice), "invalid controller path");
        ensure!(profile.identity.touch_property.split('/').all(safe_name), "invalid DT property path");
        ensure!(matches!(profile.product.zygote.as_str(), "zygote64" | "zygote64_32"), "unsupported guest ABI");
        for rows in [&profile.mounts.firmware, &profile.mounts.charger] {
            ensure!(rows.len() <= 16, "too many firmware mounts");
            for [source, target, fs, options, flags] in rows {
                ensure!(source.strip_prefix("/dev/block/bootdevice/by-name/").is_some_and(safe_name)
                    && target.strip_prefix("/vendor/").is_some_and(safe_name)
                    && matches!(fs.as_str(), "vfat" | "ext4") && flags == "wait,slotselect"
                    && options.split(',').filter(|&option| option == "ro").count() == 1
                    && !options.split(',').any(|option| matches!(option, "rw" | "remount" | "bind" | "rbind" | "move" | "loop"))
                    && (fs != "ext4" || options.split(',').any(|option| option == "noload"))
                    && options.bytes().all(|b| b.is_ascii_graphic() && b != b'#'), "unsafe firmware mount {source} -> {target}");
            }
        }
        Ok(profile)
    }
}

impl DeviceProfile {
    pub fn description(self) -> Result<&'static Profile> {
        static FUXI: OnceLock<Result<Profile>> = OnceLock::new();
        static DADA: OnceLock<Result<Profile>> = OnceLock::new();
        let (cache, text) = match self {
            Self::Fuxi => (&FUXI, include_str!("../../profiles/fuxi/device.toml")),
            Self::Dada => (&DADA, include_str!("../../profiles/dada/device.toml")),
        };
        cache.get_or_init(|| Profile::parse(text, self.codename())).as_ref().map_err(|e| anyhow::anyhow!("{e:#}"))
    }

    #[cfg(target_os = "android")]
    pub fn matches_live(self, bootconfig: &str, cmdline: &str, read_dt: impl Fn(&str) -> std::io::Result<Vec<u8>>) -> Result<bool> {
        Ok(device_matches(self, bootconfig, cmdline, DeviceTree {
            compatible: &read_dt("compatible")?,
            model: &read_dt("model")?,
            fuxi_touch_fw: &read_dt(&Self::Fuxi.description()?.identity.touch_property)?,
            dada_touch_fw: &read_dt(&Self::Dada.description()?.identity.touch_property)?,
        }))
    }
}

const DEVICE_KEYS: [&str; 3] = ["androidboot.product.device", "androidboot.device", "androidboot.product.vendor.device"];

fn params<'a>(bootconfig: &'a str, cmdline: &'a str) -> impl Iterator<Item = (&'a str, &'a str)> {
    bootconfig.lines().filter_map(|line| line.split_once('=')).chain(cmdline.split_ascii_whitespace().filter_map(|word| word.split_once('=')))
        .map(|(key, value)| {
            let value = value.trim();
            (key.trim(), value.strip_prefix('"').and_then(|v| v.strip_suffix('"')).unwrap_or(value))
        })
}

pub struct DeviceTree<'a> {
    pub compatible: &'a [u8],
    pub model: &'a [u8],
    pub fuxi_touch_fw: &'a [u8],
    pub dada_touch_fw: &'a [u8],
}

pub fn device_matches(profile: DeviceProfile, bootconfig: &str, cmdline: &str, dt: DeviceTree<'_>) -> bool {
    use DeviceProfile::{Dada, Fuxi};
    let mut named = false;
    let mut kalama = false;
    let mut sun = false;
    let mut twin = false;
    for (is_compatible, prop) in [(true, dt.compatible), (false, dt.model)] {
        for value in prop.split(|&b| b == 0) {
            let board = match value {
                b"fuxi" | b"xiaomi,fuxi" | b"Qualcomm Technologies, Inc. 8550 fuxi" => Fuxi,
                b"dada" | b"xiaomi,dada" => Dada,
                // Dada's stock DTBO has the stale Haotian model. It is not
                // board evidence; the applied touchscreen property is required below.
                b"Qualcomm Technologies, Inc. Haotian based on SM8750" if profile == Dada && !is_compatible => continue,
                b"qcom,kalama" | b"qcom,kalama-mtp" | b"qcom,kalamap" | b"qcom,kalamap-mtp" => {
                    kalama = true;
                    continue;
                }
                b"qcom,sun" | b"qcom,sun-mtp" | b"qcom,sunp" | b"qcom,sunp-mtp" if is_compatible => {
                    sun = true;
                    continue;
                }
                b"linux,dummy-virt" => {
                    twin = true;
                    continue;
                }
                b"haotian" | b"Haotian" => return false,
                other if other.starts_with(b"xiaomi,")
                    || other.starts_with(b"Qualcomm Technologies, Inc. 8550 ")
                    || (other.starts_with(b"Qualcomm Technologies, Inc. ") && other.ends_with(b" based on SM8750")) => return false,
                _ => continue,
            };
            if board != profile { return false; }
            named = true;
        }
    }

    let fuxi_touch = dt.fuxi_touch_fw == b"st_fts_m3.ftb\0";
    // Stock OS4.0.0.22 DTBO fragment@59 targets qupv3_se2_spi. The
    // firmware filename is also in Dada's proprietary file inventory.
    let dada_touch = dt.dada_touch_fw == b"synaptics_spi_dada_00.img\0";
    let mut twin_hardware = false;
    for (key, value) in params(bootconfig, cmdline) {
        if DEVICE_KEYS.contains(&key) {
            if value != profile.codename() { return false; }
            named = true;
        } else if matches!(key, "androidboot.hardware" | "androidboot.hardware.platform") {
            match (profile, value) {
                (Dada, "qcom" | "dada" | "sun" | "sunp" | "sm8750") => (),
                (Dada, _) => return false,
                (Fuxi, "dada" | "sun" | "sunp" | "sm8750" | "haotian" | "nuwa" | "ishtar" | "babylon" | "babybus" | "socrates" | "vermeer") => return false,
                (Fuxi, "fuxi") if key == "androidboot.hardware" => twin_hardware = true,
                _ => (),
            }
        }
    }
    match profile {
        Fuxi => !sun && !dada_touch && (named || (kalama && fuxi_touch) || (twin && twin_hardware)),
        Dada => sun && !kalama && !twin && dada_touch && !fuxi_touch,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_third_board_descriptor_and_rejects_unsafe_variants() {
        let sample = include_str!("../../profiles/virt/device.toml");
        assert!(Profile::parse(sample, "virt").is_ok());
        assert!(Profile::parse(sample, "fuxi").is_err());
        assert!(Profile::parse(&sample.replace("schema_version = 1", "schema_version = 2"), "virt").is_err());
        assert!(Profile::parse(&sample.replace("platform/virtio", "../virtio"), "virt").is_err());
        let sample = include_str!("../../profiles/fuxi/device.toml");
        assert!(Profile::parse(&sample.replace("ro,", "rw,"), "fuxi").is_err());
    }

    #[test]
    fn requires_both_partitions_under_the_configured_controller() {
        use std::path::Path;
        let sample = include_str!("../../profiles/virt/device.toml");
        let board = Profile::parse(sample, "virt").unwrap();
        let misc = Path::new("/sys/devices/platform/platform/virtio/block/vda/vda1");
        let userdata = Path::new("/sys/devices/platform/platform/virtio/block/vda/vda2");
        let wrong = Path::new("/sys/devices/platform/platform/virtio-foreign/block/vdb/vdb1");
        assert_eq!(board.storage.observe(misc, userdata).unwrap(), Some(("platform/virtio", "virtio")));
        assert!(board.storage.observe(misc, wrong).is_err());
    }
}
