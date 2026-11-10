// CI regression for the optional native port; no public peer or account.
#[cfg(target_os = "linux")]
#[test]
fn tdh_outgoing_autotune_and_unsent_limit() {
    use std::os::fd::AsRawFd;
    let address = "127.0.0.1:1".parse().unwrap();
    let baseline = socket2::Socket::new(socket2::Domain::IPV4, socket2::Type::STREAM,
                                      Some(socket2::Protocol::TCP)).unwrap();
    let configured = super::create_outgoing_socket_bound(address, None).unwrap();
    assert_eq!(configured.send_buffer_size().unwrap(), baseline.send_buffer_size().unwrap());
    assert_eq!(configured.recv_buffer_size().unwrap(), baseline.recv_buffer_size().unwrap());
    let mut actual: libc::c_uint = 0;
    let mut length = std::mem::size_of_val(&actual) as libc::socklen_t;
    let rc = unsafe { libc::getsockopt(configured.as_raw_fd(), libc::IPPROTO_TCP,
        libc::TCP_NOTSENT_LOWAT, (&mut actual as *mut libc::c_uint).cast(), &mut length) };
    assert_eq!(rc, 0);
    assert_eq!(actual, 256 * 1024);
    assert_eq!(super::low_rtt_receive::receive_request(0), None);
    assert_eq!(super::low_rtt_receive::receive_request(5_001), None);
    assert_eq!(super::low_rtt_receive::receive_request(5_000), Some(256 * 1024));
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn tdh_client_send_stays_automatic() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let (client, accepted) = tokio::join!(tokio::net::TcpStream::connect(listener.local_addr().unwrap()), listener.accept());
    let _client = client.unwrap();
    let (server, _) = accepted.unwrap();
    let socket = socket2::SockRef::from(&server);
    let previous = socket.send_buffer_size().unwrap();
    super::configure_client_socket(&server, 30, 30).unwrap();
    assert_eq!(socket.send_buffer_size().unwrap(), previous);
    assert!(socket.tcp_nodelay().unwrap());
}
