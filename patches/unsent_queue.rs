use std::io;
use std::os::fd::RawFd;

// Limit unsent bytes, not SO_SNDBUF or bytes already in flight. This preserves
// automatic TCP window/send-buffer sizing while propagating backpressure early.
pub(super) fn configure_unsent_queue(fd: RawFd) -> io::Result<()> {
    let limit: libc::c_uint = 64 * 1024;
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
