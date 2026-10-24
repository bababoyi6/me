#[cfg(test)]
#[path = "../../../patches/flushed_copy.rs"]
mod flushed_copy;
#[cfg(test)]
#[path = "../../../patches/fair_copy.rs"]
mod fair_copy;

#[cfg(all(test, target_os = "linux"))]
#[path = "../../../patches/unsent_queue.rs"]
mod unsent_queue;

#[cfg(all(test, target_os = "linux"))]
#[path = "../../../patches/unsent_queue_bulk.rs"]
mod unsent_queue_bulk;

#[cfg(all(test, target_os = "linux"))]
#[path = "../../../patches/low_rtt_receive.rs"]
mod low_rtt_receive;

#[cfg(all(test, target_os = "linux"))]
#[test]
fn receive_policy_preserves_unknown_and_long_rtt_links() {
    assert_eq!(low_rtt_receive::receive_request(0), None);
    assert_eq!(low_rtt_receive::receive_request(1), Some(262144));
    assert_eq!(low_rtt_receive::receive_request(5000), Some(262144));
    assert_eq!(low_rtt_receive::receive_request(5001), None);
    assert_eq!(low_rtt_receive::receive_request(100000), None);
    assert_eq!(low_rtt_receive::configure_low_rtt_receive(-1).unwrap(), None);
}

#[cfg(all(test, target_os = "linux"))]
#[test]
fn real_receive_policy_does_not_change_send_buffer() {
    use std::os::fd::AsRawFd;
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let _client = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let stream = listener.accept().unwrap().0;
    let fd = stream.as_raw_fd();
    fn buffer(fd: i32, key: i32) -> i32 {
        let mut value = 0i32;
        let mut size = std::mem::size_of_val(&value) as libc::socklen_t;
        assert_eq!(unsafe { libc::getsockopt(fd, libc::SOL_SOCKET, key, (&mut value as *mut i32).cast(), &mut size) }, 0);
        value
    }
    let before = buffer(fd, libc::SO_SNDBUF);
    if low_rtt_receive::configure_low_rtt_receive(fd).unwrap().is_some() {
        assert!(buffer(fd, libc::SO_RCVBUF) <= 524288);
    }
    assert_eq!(buffer(fd, libc::SO_SNDBUF), before);
}

#[cfg(all(test, target_os = "linux"))]
#[test]
fn queue_policy_preserves_automatic_buffer_sizes() {
    assert_queue_policy(unsent_queue::configure_unsent_queue, 65536);
}

#[cfg(all(test, target_os = "linux"))]
#[test]
fn bulk_queue_policy_preserves_automatic_buffer_sizes() {
    assert_queue_policy(unsent_queue_bulk::configure_unsent_queue, 262144);
}

#[cfg(all(test, target_os = "linux"))]
fn assert_queue_policy(configure: fn(i32) -> std::io::Result<()>, expected: u32) {
    use std::os::fd::AsRawFd;
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let stream = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let _peer = listener.accept().unwrap();
    fn option(fd: i32, level: i32, key: i32) -> u32 {
        let mut value = 0u32;
        let mut size = std::mem::size_of_val(&value) as libc::socklen_t;
        assert_eq!(unsafe { libc::getsockopt(fd, level, key, (&mut value as *mut u32).cast(), &mut size) }, 0);
        value
    }
    let fd = stream.as_raw_fd();
    let before = (option(fd, libc::SOL_SOCKET, libc::SO_RCVBUF), option(fd, libc::SOL_SOCKET, libc::SO_SNDBUF));
    configure(fd).unwrap();
    assert_eq!(option(fd, libc::IPPROTO_TCP, libc::TCP_NOTSENT_LOWAT), expected);
    assert_eq!(before, (option(fd, libc::SOL_SOCKET, libc::SO_RCVBUF), option(fd, libc::SOL_SOCKET, libc::SO_SNDBUF)));
}

macro_rules! relay_contract_tests {
    ($name:ident, $copy:path) => {
#[cfg(test)]
mod $name {
    use $copy as copy;
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader, BufWriter, duplex};

    #[tokio::test]
    async fn buffered_output_reaches_peer_while_source_remains_open() {
        let (mut source, input) = duplex(128);
        let (output, mut peer) = duplex(128);
        let mut input = BufReader::new(input);
        let mut output = BufWriter::with_capacity(128, output);
        source.write_all(b"tail").await.unwrap();
        let transfer = copy(&mut input, &mut output);
        tokio::pin!(transfer);
        let mut received = [0; 4];
        tokio::select! {
            result = &mut transfer => panic!("source stayed open, copy returned {result:?}"),
            result = tokio::time::timeout(Duration::from_millis(200), peer.read_exact(&mut received)) => {
                result.expect("buffered output stalled waiting for new source data").unwrap();
            }
        }
        assert_eq!(&received, b"tail");
    }

    #[tokio::test]
    async fn backpressure_preserves_all_bytes_and_shutdown() {
        let payload: Vec<u8> = (0..8199).map(|i| (i % 251) as u8).collect();
        let expected = payload.clone();
        let (mut source, input) = duplex(37);
        let (output, mut peer) = duplex(23);
        let mut input = BufReader::with_capacity(41, input);
        let mut output = BufWriter::with_capacity(31, output);
        let producer = async move {
            source.write_all(&payload).await.unwrap();
            source.shutdown().await.unwrap();
        };
        let relay = async {
            let count = copy(&mut input, &mut output).await.unwrap();
            output.shutdown().await.unwrap();
            count
        };
        let consumer = async move {
            let mut data = Vec::new();
            peer.read_to_end(&mut data).await.unwrap();
            data
        };
        let (_, count, actual) = tokio::time::timeout(Duration::from_secs(2), async {
            tokio::join!(producer, relay, consumer)
        }).await.unwrap();
        assert_eq!(count as usize, expected.len());
        assert_eq!(actual, expected);
    }

    #[tokio::test]
    async fn writer_error_is_preserved() {
        let (output, peer) = duplex(32);
        drop(peer);
        let mut input = &b"payload"[..];
        let mut output = BufWriter::new(output);
        let error = copy(&mut input, &mut output).await.unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::BrokenPipe);
    }
}
    };
}

relay_contract_tests!(baseline_contract, crate::flushed_copy::copy_buf_flushed);
relay_contract_tests!(fair_contract, crate::fair_copy::copy_buf_flushed);

#[cfg(test)]
mod fairness_tests {
    use std::future::Future;
    use std::io;
    use std::pin::Pin;
    use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
    use std::task::{Context, Poll, Wake, Waker};
    use tokio::io::AsyncWrite;

    #[derive(Default)]
    struct Counter(AtomicUsize);
    impl Wake for Counter {
        fn wake(self: Arc<Self>) { self.0.fetch_add(1, Ordering::Relaxed); }
        fn wake_by_ref(self: &Arc<Self>) { self.0.fetch_add(1, Ordering::Relaxed); }
    }
    #[derive(Default)]
    struct Sink { bytes: usize, flushes: usize }
    impl AsyncWrite for Sink {
        fn poll_write(mut self: Pin<&mut Self>, _: &mut Context<'_>, data: &[u8]) -> Poll<io::Result<usize>> {
            self.bytes += data.len();
            Poll::Ready(Ok(data.len()))
        }
        fn poll_flush(mut self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            self.flushes += 1;
            Poll::Ready(Ok(()))
        }
        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }

    #[test]
    fn ready_burst_yields_without_losing_bytes_or_wakeup() {
        let payload = vec![42; 8 * 1024 * 1024];
        let mut input = &payload[..];
        let mut output = Sink::default();
        let wakeups = Arc::new(Counter::default());
        let waker = Waker::from(Arc::clone(&wakeups));
        let mut context = Context::from_waker(&waker);
        let mut baseline_input = &payload[..];
        let mut baseline_output = Sink::default();
        {
            let mut baseline = Box::pin(super::flushed_copy::copy_buf_flushed(&mut baseline_input, &mut baseline_output));
            assert!(baseline.as_mut().poll(&mut context).is_ready());
        }
        assert_eq!(baseline_output.bytes, payload.len());
        assert_eq!(wakeups.0.load(Ordering::Relaxed), 0);
        let mut transfer = Box::pin(super::fair_copy::copy_buf_flushed(&mut input, &mut output));
        assert!(transfer.as_mut().poll(&mut context).is_pending());
        assert_eq!(wakeups.0.load(Ordering::Relaxed), 1);
        drop(transfer);
        assert!(output.bytes > 0 && output.bytes <= 64 * 1024);
        assert_eq!(output.flushes, 1);
        assert_eq!(payload.len() - input.len(), output.bytes);
    }
}
