"""Check effective cgroup limits before starting an untrusted renderer."""
import os
import sys
from pathlib import Path


def main() -> None:
    membership = Path('/proc/self/cgroup').read_text().splitlines()
    path = next(line[3:] for line in membership if line.startswith('0::'))
    scope = Path('/sys/fs/cgroup') / path.lstrip('/')
    for name, limit in (('memory.max', 2 * 1024**3), ('memory.swap.max', 0), ('pids.max', 128)):
        value = (scope / name).read_text().strip()
        if value == 'max' or int(value) > limit:
            raise RuntimeError('Renderer resource limits are unavailable')
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()
