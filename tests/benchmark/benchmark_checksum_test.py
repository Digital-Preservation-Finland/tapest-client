"""Benchmark SHA-256 checksum read buffer sizes.

Usage:
    pip install pytest-benchmark
    pytest --run-slow --benchmark-enable

    To benchmark on a specific directory (e.g. GlusterFS):
    BENCHMARK_DIR=/mnt/cache_vol/tapest pytest --run-slow --benchmark-enable
"""

import hashlib
import os

import pytest

FILE_SIZE = 256 * 1024 * 1024  # 256 MB
BUFFER_SIZES = [
    4096, 8192, 32768, 65536, 131072, 262144,
    524288, 1048576, 2097152, 4194304, 16777216, 67108864,
]


@pytest.fixture(scope="module")
def test_file(tmp_path_factory):
    """Create a 256MB test file with random data."""
    directory = os.environ.get("BENCHMARK_DIR")
    if directory:
        path = os.path.join(directory, "benchmark_checksum.bin")
        with open(path, "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        yield path
        os.unlink(path)
    else:
        path = tmp_path_factory.mktemp("benchmark") / "benchmark_checksum.bin"
        with open(path, "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        yield str(path)


def _label(size):
    if size >= 1048576:
        return f"{size // 1048576}MB"
    return f"{size // 1024}KB"


@pytest.mark.slow
@pytest.mark.benchmark(group="sha256-buffer-size")
@pytest.mark.parametrize("buffer_size", BUFFER_SIZES, ids=[_label(s) for s in BUFFER_SIZES])
def test_sha256_buffer_size(benchmark, test_file, buffer_size):
    """Benchmark SHA-256 hashing with different read buffer sizes."""
    def hash_file():
        h = hashlib.sha256()
        with open(test_file, "rb") as f:
            for block in iter(lambda: f.read(buffer_size), b""):
                h.update(block)
        return h.hexdigest()

    result = benchmark(hash_file)
    assert len(result) == 64  # valid SHA-256 hex digest
