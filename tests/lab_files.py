"""Stream large lab artifacts without retaining whole binary-sized heap buffers."""
import hashlib
from pathlib import Path


def file_sha256(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(65536),b''):
            result.update(block)
    return result.hexdigest()
