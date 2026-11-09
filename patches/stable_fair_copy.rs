use std::future::poll_fn;
use std::io;
use std::pin::Pin;
use std::task::{Poll, ready};

use tokio::io::{AsyncBufRead, AsyncWrite};

// Experimental 3.5.17 port of the existing 1.6 fairness mechanism. This bounds
// work per poll, not bandwidth, buffers, in-flight TCP bytes or network queues.
const COPY_POLL_QUANTUM: usize = 64 * 1024;

pub(super) async fn copy_with_flush<R, W>(reader: &mut R, writer: &mut W) -> io::Result<u64>
where
    R: AsyncBufRead + Unpin + ?Sized,
    W: AsyncWrite + Unpin + ?Sized,
{
    let mut copied = 0;
    let mut need_flush = false;
    poll_fn(|cx| {
        let mut remaining = COPY_POLL_QUANTUM;
        loop {
            let buffer = match Pin::new(&mut *reader).poll_fill_buf(cx) {
                Poll::Ready(result) => result?,
                Poll::Pending => {
                    if need_flush {
                        ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                        need_flush = false;
                    }
                    return Poll::Pending;
                }
            };
            if buffer.is_empty() {
                ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                return Poll::Ready(Ok(copied));
            }
            let count = buffer.len().min(remaining);
            let written = ready!(Pin::new(&mut *writer).poll_write(cx, &buffer[..count]))?;
            if written == 0 {
                return Poll::Ready(Err(io::ErrorKind::WriteZero.into()));
            }
            Pin::new(&mut *reader).consume(written);
            copied += written as u64;
            need_flush = true;
            remaining -= written;
            if remaining == 0 {
                ready!(Pin::new(&mut *writer).poll_flush(cx))?;
                need_flush = false;
                // Preserve upstream's immediate EOF completion at the exact
                // quantum boundary. Peek once without copying more bytes.
                match Pin::new(&mut *reader).poll_fill_buf(cx) {
                    Poll::Ready(Ok(buffer)) if buffer.is_empty() => return Poll::Ready(Ok(copied)),
                    Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                    _ => {}
                }
                cx.waker().wake_by_ref();
                return Poll::Pending;
            }
        }
    })
    .await
}
