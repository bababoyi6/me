#[cfg(test)]
#[path = "../../../patches/flushed_copy.rs"]
mod flushed_copy;
#[cfg(test)]
use flushed_copy::copy_buf_flushed as copy;

#[cfg(test)]
mod tests {
    use super::copy;
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
