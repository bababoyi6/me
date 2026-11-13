#[cfg(target_os = "linux")]
#[tokio::test]
async fn tdh_client_reports_registered_unsent_threshold() {
    use std::os::fd::AsRawFd;

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let (client, accepted) = tokio::join!(
        tokio::net::TcpStream::connect(listener.local_addr().unwrap()),
        listener.accept()
    );
    let _client = client.unwrap();
    let (server, _) = accepted.unwrap();
    let send_buffer_before = socket2::SockRef::from(&server).send_buffer_size().unwrap();
    super::configure_client_socket(&server, 30, 30).unwrap();

    let mut value: libc::c_uint = 0;
    let mut length = std::mem::size_of_val(&value) as libc::socklen_t;
    let rc = unsafe {
        libc::getsockopt(
            server.as_raw_fd(),
            libc::IPPROTO_TCP,
            libc::TCP_NOTSENT_LOWAT,
            (&mut value as *mut libc::c_uint).cast(),
            &mut length,
        )
    };
    assert_eq!(rc, 0);
    assert_eq!(length as usize, std::mem::size_of_val(&value));
    assert_eq!(value, 256 * 1024);
    assert_eq!(socket2::SockRef::from(&server).send_buffer_size().unwrap(), send_buffer_before);
}
