use std::future::poll_fn;
use std::io;
use std::pin::Pin;
use std::task::{Poll, ready};

use tokio::io::{AsyncBufRead, AsyncWrite};

/// Keeps buffered writers progressing while an open source has no new bytes.
pub(super) async fn copy_buf_flushed<R, W>(reader: &mut R, writer: &mut W) -> io::Result<u64>
where
    R: AsyncBufRead + Unpin + ?Sized,
    W: AsyncWrite + Unpin + ?Sized,
{
    let mut transferred = 0;
    poll_fn(|cx| loop {
        let data = match Pin::new(&mut *reader).poll_fill_buf(cx) {
            Poll::Ready(result) => result?,
            Poll::Pending => {
                // CryptoWriter may accept plaintext before ciphertext reaches TCP.
                // Retain the source's registered wakeup and also drive its writer.
                ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                return Poll::Pending;
            }
        };
        if data.is_empty() {
            ready!(Pin::new(&mut *writer).poll_flush(cx))?;
            return Poll::Ready(Ok(transferred));
        }
        let written = ready!(Pin::new(&mut *writer).poll_write(cx, data))?;
        if written == 0 {
            return Poll::Ready(Err(io::ErrorKind::WriteZero.into()));
        }
        transferred += written as u64;
        Pin::new(&mut *reader).consume(written);
    })
    .await
}
