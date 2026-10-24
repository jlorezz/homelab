#!/usr/bin/env python3
"""Add a verified image runtime to a private home; operator default is dry-run."""
import argparse
from contextlib import ExitStack
import hashlib
import os
from pathlib import PurePosixPath
import re
import secrets
import stat


DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_directory(path, stack):
    """Walk from / without following symlinks; retain descriptors against renames."""
    path = PurePosixPath(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute path without .. is required')
    fd = os.open('/', DIRECTORY)
    stack.callback(os.close, fd)
    for part in path.parts[1:]:
        fd = os.open(part, DIRECTORY, dir_fd=fd)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        # Root-owned sticky /tmp is safe to traverse; the private home is stricter.
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not sticky_root):
            raise ValueError('Unsafe ancestor directory')
    return fd


def validate_private(fd, home=False):
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & (0o077 if home else 0o022):
        raise ValueError('Home must be private and tool directories owned by current EUID without group/world write')


def destination_exists(fd):
    try:
        info = os.stat('bun', dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise ValueError('Existing bun must be a regular executable; refusing replacement')
    return True


def seed(home, source, sha256, apply=False):
    if not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('Expected a lowercase binary SHA-256 digest')
    with ExitStack() as stack:
        source_path = PurePosixPath(source)
        source_parent = open_directory(str(source_path.parent), stack)
        source_fd = os.open(source_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                            dir_fd=source_parent)
        stream = stack.enter_context(os.fdopen(source_fd, 'rb'))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Source must be a regular file')
        source_digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            source_digest.update(block)
        if source_digest.hexdigest() != sha256:
            raise ValueError('Source checksum mismatch')
        fd = open_directory(home, stack)
        validate_private(fd, home=True)
        for part in ('.bun', 'bin'):
            try:
                child = os.open(part, DIRECTORY, dir_fd=fd)
            except FileNotFoundError:
                if not apply:
                    return 'would seed missing bun'
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass  # A concurrent installer created it; validate below.
                child = os.open(part, DIRECTORY, dir_fd=fd)
            stack.callback(os.close, child)
            validate_private(child)
            fd = child
        if destination_exists(fd):
            return 'preserved existing bun'
        if not apply:
            return 'would seed missing bun'
        temporary = '.bun-bootstrap-' + secrets.token_hex(16)
        out_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=fd)
        try:
            with os.fdopen(out_fd, 'wb') as out:
                stream.seek(0)
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                    out.write(block)
                if digest.hexdigest() != sha256:
                    raise ValueError('Source changed while copying')
                out.flush()
                os.fchmod(out.fileno(), 0o755)
                os.fsync(out.fileno())
            try:
                os.link(temporary, 'bun', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                # Never overwrite even a concurrent unsafe destination; report it.
                if not destination_exists(fd):
                    raise ValueError('Concurrent destination disappeared; retry after inspection')
                return 'preserved concurrently installed bun'
            os.fsync(fd)
            return 'seeded missing bun'
        finally:
            os.unlink(temporary, dir_fd=fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--source', required=True, help='Verified public image binary, never a cache binary')
    parser.add_argument('--sha256', required=True, help='Binary digest, not archive digest')
    parser.add_argument('--apply', action='store_true', help='Apply additive repair (default: dry-run)')
    args = parser.parse_args()
    try:
        print(seed(args.home, args.source, args.sha256, args.apply))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Runtime bootstrap refused: {error}\n')


if __name__ == '__main__':
    main()
