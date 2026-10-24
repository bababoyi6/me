use std::future::poll_fn;
use std::io;
use std::pin::Pin;
use std::task::{Poll, ready};

use tokio::io::{AsyncBufRead, AsyncWrite};

// A ready TCP socket can otherwise let one relay monopolize a task poll.
// Bound work in bytes, including AsyncBufRead implementations with cached data.
const COPY_QUANTUM: usize = 64 * 1024;

/// Flush buffered ciphertext on idle/EOF and yield after a bounded ready burst.
pub(super) async fn copy_buf_flushed<R, W>(reader: &mut R, writer: &mut W) -> io::Result<u64>
where
    R: AsyncBufRead + Unpin + ?Sized,
    W: AsyncWrite + Unpin + ?Sized,
{
    let mut transferred = 0;
    poll_fn(|cx| {
        let mut remaining = COPY_QUANTUM;
        loop {
            let data = match Pin::new(&mut *reader).poll_fill_buf(cx) {
                Poll::Ready(result) => result?,
                Poll::Pending => {
                    ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                    return Poll::Pending;
                }
            };
            if data.is_empty() {
                ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                return Poll::Ready(Ok(transferred));
            }
            let count = data.len().min(remaining);
            let written = ready!(Pin::new(&mut *writer).poll_write(cx, &data[..count]))?;
            if written == 0 {
                return Poll::Ready(Err(io::ErrorKind::WriteZero.into()));
            }
            transferred += written as u64;
            remaining -= written;
            Pin::new(&mut *reader).consume(written);
            if remaining == 0 {
                // CryptoWriter may have accepted bytes into its private queue.
                // Drive it before yielding; a blocked flush owns the next wakeup.
                ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                cx.waker().wake_by_ref();
                return Poll::Pending;
            }
        }
    })
    .await
}
