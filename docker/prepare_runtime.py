"""Package the NAS's installed Python and libraries for a local Docker build."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile


def dependencies(binary):
    result = subprocess.run(['ldd', str(binary)], check=True, capture_output=True, text=True)
    for entry in result.stdout.splitlines():
        if '=> not found' in entry:
            raise RuntimeError(f'Missing library for {binary}: {entry.strip()}')
        match = re.search(r'(?:=>\s*|^\s*)(/\S+)\s+\(', entry)
        if match:
            yield Path(match[1])


def main():
    if not sys.platform.startswith('linux'):
        raise SystemExit('Run this script on the Linux host that runs Docker.')
    os.umask(0o022)
    docker = Path(__file__).resolve().parent
    output = docker / 'runtime'
    executable = Path(sys.executable).resolve()
    standard_library = Path(sysconfig.get_path('stdlib'))
    shared_modules = standard_library / 'lib-dynload'
    if not shared_modules.is_dir():
        raise RuntimeError(f'Python shared modules not found: {shared_modules}')
    library_files = set(dependencies(executable))
    for module in shared_modules.glob('*.so'):
        library_files.update(dependencies(module))

    with tempfile.TemporaryDirectory(prefix='.runtime-', dir=docker) as temporary:
        root = Path(temporary) / 'root'
        for source in sorted(library_files | {executable}):
            target = root / source.relative_to('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        python = root / 'usr/bin/python3'
        if python != root / executable.relative_to('/'):
            python.parent.mkdir(parents=True, exist_ok=True)
            python.symlink_to(executable)
        shutil.copytree(standard_library, root / standard_library.relative_to('/'),
                        dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', 'site-packages', 'dist-packages', 'test', 'tests'))
        for name in ('dev', 'proc', 'nas-volume'):
            (root / name).mkdir()
        if output.exists():
            shutil.rmtree(output)
        os.replace(root, output)
    print(f'Prepared Python {sys.version.split()[0]} and {len(library_files)} shared libraries in {output}')


if __name__ == '__main__':
    main()
