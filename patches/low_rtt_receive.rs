use std::io;
use std::os::fd::RawFd;

// Keep automatic receive sizing on unknown/long-RTT entry links. On a short
// entry-to-backend hop, a 256KiB request avoids multi-MiB receive bursts without
// changing the client send buffer or the high-RTT DC receive path.
pub(super) fn receive_request(rtt_us: u32) -> Option<libc::c_int> {
    (rtt_us > 0 && rtt_us <= 5_000).then_some(256 * 1024)
}

pub(super) fn configure_low_rtt_receive(fd: RawFd) -> io::Result<Option<u32>> {
    let mut info: libc::tcp_info = unsafe { std::mem::zeroed() };
    let mut size = std::mem::size_of_val(&info) as libc::socklen_t;
    let result = unsafe {
        libc::getsockopt(fd, libc::IPPROTO_TCP, libc::TCP_INFO,
                        (&mut info as *mut libc::tcp_info).cast(), &mut size)
    };
    let required = std::mem::offset_of!(libc::tcp_info, tcpi_rtt) + std::mem::size_of::<u32>();
    if result != 0 || (size as usize) < required {
        return Ok(None);
    }
    let Some(request) = receive_request(info.tcpi_rtt) else { return Ok(None) };
    let result = unsafe {
        libc::setsockopt(fd, libc::SOL_SOCKET, libc::SO_RCVBUF,
                        (&request as *const libc::c_int).cast(),
                        std::mem::size_of_val(&request) as libc::socklen_t)
    };
    if result == 0 { Ok(Some(info.tcpi_rtt)) } else { Err(io::Error::last_os_error()) }
}
