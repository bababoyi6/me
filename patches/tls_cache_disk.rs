//! Bounded, descriptor-anchored TLS cache I/O for the pinned 3.5.5 core.
//! This file is copied into `src/tls_front/cache/disk_io.rs` only in the CI candidate.

use std::ffi::OsStr;
use std::io::{self, Read, Write};
use std::os::fd::OwnedFd;
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path};
use std::time::SystemTime;

use nix::fcntl::{open, openat, renameat, OFlag};
use nix::sys::stat::{mkdirat, Mode};
use nix::unistd::{fsync, unlinkat, UnlinkatFlags};

pub(super) const MAX_BYTES: usize = 1024 * 1024;
const DIR_FLAGS: OFlag = OFlag::O_RDONLY
    .union(OFlag::O_DIRECTORY)
    .union(OFlag::O_NOFOLLOW)
    .union(OFlag::O_CLOEXEC);

fn io_error(error: nix::errno::Errno) -> io::Error {
    io::Error::from_raw_os_error(error as i32)
}

fn open_dir(path: &Path, create: bool) -> io::Result<OwnedFd> {
    let start = if path.is_absolute() { "/" } else { "." };
    let mut directory = open(start, DIR_FLAGS, Mode::empty()).map_err(io_error)?;
    for component in path.components() {
        let name = match component {
            Component::RootDir | Component::CurDir => continue,
            Component::Normal(name) => name,
            Component::ParentDir | Component::Prefix(_) => {
                return Err(io::Error::new(io::ErrorKind::InvalidInput, "unsafe cache directory"));
            }
        };
        let next = match openat(&directory, name, DIR_FLAGS, Mode::empty()) {
            Ok(fd) => fd,
            Err(nix::errno::Errno::ENOENT) if create => {
                match mkdirat(&directory, name, Mode::from_bits_truncate(0o700)) {
                    Ok(()) | Err(nix::errno::Errno::EEXIST) => {}
                    Err(error) => return Err(io_error(error)),
                }
                openat(&directory, name, DIR_FLAGS, Mode::empty()).map_err(io_error)?
            }
            Err(error) => return Err(io_error(error)),
        };
        directory = next;
    }
    Ok(directory)
}

pub(super) fn valid_domain(domain: &str) -> bool {
    !domain.is_empty()
        && domain.len() <= 255
        && domain.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'.' || c == b'-')
}

fn file_name(domain: &str) -> io::Result<String> {
    if !valid_domain(domain) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid TLS cache domain"));
    }
    Ok(format!("{domain}.json"))
}

fn read_entry(path: &Path, domain: &str) -> io::Result<(Vec<u8>, SystemTime)> {
    let directory = open_dir(path, false)?;
    let name = file_name(domain)?;
    let descriptor = openat(
        &directory,
        OsStr::new(&name),
        OFlag::O_RDONLY | OFlag::O_NONBLOCK | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
        Mode::empty(),
    )
    .map_err(io_error)?;
    let mut file = std::fs::File::from(descriptor);
    let before = file.metadata()?;
    if !before.is_file() || before.nlink() != 1 || before.len() > MAX_BYTES as u64 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "unsafe TLS cache entry"));
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    Read::take(&mut file, (MAX_BYTES + 1) as u64).read_to_end(&mut bytes)?;
    let after = file.metadata()?;
    if bytes.len() > MAX_BYTES
        || before.dev() != after.dev()
        || before.ino() != after.ino()
        || before.len() != after.len()
        || before.mtime() != after.mtime()
        || before.mtime_nsec() != after.mtime_nsec()
        || before.ctime() != after.ctime()
        || before.ctime_nsec() != after.ctime_nsec()
    {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "TLS cache entry changed"));
    }
    Ok((bytes, after.modified()?))
}

pub(super) async fn read_entry_async(
    path: std::path::PathBuf,
    domain: String,
) -> io::Result<(Vec<u8>, SystemTime)> {
    tokio::task::spawn_blocking(move || read_entry(&path, &domain))
        .await
        .map_err(|error| io::Error::other(format!("TLS cache read task failed: {error}")))?
}

fn write_entry(path: &Path, domain: &str, bytes: &[u8]) -> io::Result<()> {
    if bytes.len() > MAX_BYTES {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "TLS cache entry too large"));
    }
    let directory = open_dir(path, true)?;
    let name = file_name(domain)?;
    let temp_name = format!(".telemt-cache-{:016x}.tmp", rand::random::<u64>());
    let descriptor = openat(
        &directory,
        temp_name.as_str(),
        OFlag::O_WRONLY | OFlag::O_CREAT | OFlag::O_EXCL | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
        Mode::from_bits_truncate(0o600),
    )
    .map_err(io_error)?;
    let result = (|| {
        let mut file = std::fs::File::from(descriptor);
        file.write_all(bytes)?;
        file.sync_all()?;
        renameat(&directory, temp_name.as_str(), &directory, name.as_str()).map_err(io_error)?;
        fsync(&directory).map_err(io_error)
    })();
    if result.is_err() {
        let _ = unlinkat(&directory, temp_name.as_str(), UnlinkatFlags::NoRemoveDir);
    }
    result
}

pub(super) async fn write_entry_async(
    path: std::path::PathBuf,
    domain: String,
    bytes: Vec<u8>,
) -> io::Result<()> {
    tokio::task::spawn_blocking(move || write_entry(&path, &domain, &bytes))
        .await
        .map_err(|error| io::Error::other(format!("TLS cache write task failed: {error}")))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn regular_entry_is_bounded_and_replaced_atomically() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("cache");
        write_entry(&cache, "a.example", b"first").unwrap();
        assert_eq!(read_entry(&cache, "a.example").unwrap().0, b"first");
        write_entry(&cache, "a.example", b"second").unwrap();
        assert_eq!(read_entry(&cache, "a.example").unwrap().0, b"second");
        assert_eq!(std::fs::read_dir(&cache).unwrap().count(), 1);
    }

    #[test]
    fn symlink_and_hardlink_entries_are_rejected_without_touching_target() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("cache");
        std::fs::create_dir(&cache).unwrap();
        let target = root.path().join("target");
        std::fs::write(&target, b"protected").unwrap();
        symlink(&target, cache.join("a.example.json")).unwrap();
        assert!(read_entry(&cache, "a.example").is_err());
        write_entry(&cache, "a.example", b"new").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"protected");
        assert_eq!(read_entry(&cache, "a.example").unwrap().0, b"new");
        std::fs::hard_link(&target, cache.join("b.example.json")).unwrap();
        assert!(read_entry(&cache, "b.example").is_err());
    }

    #[test]
    fn oversized_entry_and_symlink_directory_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("cache");
        std::fs::create_dir(&cache).unwrap();
        std::fs::write(cache.join("a.example.json"), vec![0; MAX_BYTES + 1]).unwrap();
        assert!(read_entry(&cache, "a.example").is_err());
        assert!(write_entry(&cache, "a.example", &vec![0; MAX_BYTES + 1]).is_err());
        symlink(&cache, root.path().join("linked")).unwrap();
        assert!(read_entry(&root.path().join("linked"), "a.example").is_err());
        assert!(write_entry(&root.path().join("linked"), "a.example", b"x").is_err());
    }

    #[test]
    fn invalid_domains_cannot_be_used_as_paths() {
        for domain in ["", "../escape", "a/b", "a\\b", "a:b"] {
            assert!(!valid_domain(domain));
        }
    }
}
