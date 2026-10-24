"""Stream large lab artifacts without retaining whole binary-sized heap buffers."""
import hashlib
import os
from pathlib import Path
import stat


def file_sha256(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(65536),b''):
            result.update(block)
    return result.hexdigest()


def link_private_binary(source, destination, root, expected_sha256):
    """Share a verified, root-owned inode; never chmod/write the source or copy on failure."""
    source, destination, root = Path(source), Path(destination), Path(root)
    if os.geteuid() != 0 or root.is_symlink() or source.is_symlink():
        raise ValueError('shared lab binary requires root and non-symlink paths')
    directory, binary = root.stat(), source.stat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or directory.st_mode & 0o077 or
            not root.name.startswith('tdh-audit-') or source.parent.resolve() != root.resolve() or
            not stat.S_ISREG(binary.st_mode) or binary.st_uid != 0 or binary.st_mode & 0o022 or
            not binary.st_mode & 0o111 or not 0 < binary.st_size <= 64*1024**2):
        raise ValueError('shared lab binary must be protected inside its private experiment root')
    os.link(source, destination, follow_symlinks=False)
    try:
        linked = destination.lstat()
        if ((linked.st_dev, linked.st_ino) != (binary.st_dev, binary.st_ino) or
                file_sha256(destination) != expected_sha256):
            raise ValueError('shared lab binary identity mismatch')
    except BaseException:
        destination.unlink()
        raise
