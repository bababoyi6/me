use std::io;
use std::os::fd::RawFd;

// Keep automatic buffers and bound unsent data at the former 256KiB write
// request scale. A larger batch may reduce readiness wakeups; throughput and
// latency still require paired measurement, and in-flight bytes are not capped.
pub(super) fn configure_unsent_queue(fd: RawFd) -> io::Result<()> {
    let limit: libc::c_uint = 256 * 1024;
    let result = unsafe {
        libc::setsockopt(
            fd,
            libc::IPPROTO_TCP,
            libc::TCP_NOTSENT_LOWAT,
            (&limit as *const libc::c_uint).cast(),
            std::mem::size_of_val(&limit) as libc::socklen_t,
        )
    };
    if result == 0 { Ok(()) } else { Err(io::Error::last_os_error()) }
}
