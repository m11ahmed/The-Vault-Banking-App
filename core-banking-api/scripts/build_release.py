"""Build a source/demo ZIP from an explicit allowlist; never include secrets/data."""
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'dist' / 'vault-core-banking-v2.zip'
ROOT_FILES = ('.gitignore', '.dockerignore', '.env.example', 'README.md', 'REVIEW.md', 'DEMO_GUIDE.md', 'VALIDATION.md', 'requirements.txt', 'requirements-dev.txt', 'Dockerfile', 'compose.yaml')
ALLOWED = {
    'app': {'.py', '.json', '.html', '.js', '.css'},
    'tests': {'.py'},
    'samples': {'.py', '.txt', '.png'},
    'scripts': {'.py'},
    'artifacts': {'.jpg', '.gif'},
}


def main():
    files = [ROOT / name for name in ROOT_FILES]
    for directory, extensions in ALLOWED.items():
        files.extend(path for path in (ROOT / directory).rglob('*') if path.is_file() and path.suffix.lower() in extensions and '__pycache__' not in path.parts)
    OUTPUT.parent.mkdir(exist_ok=True)
    with ZipFile(OUTPUT, 'w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, 'core-banking-api/' + path.relative_to(ROOT).as_posix())
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert all(not name.endswith(('.db', '.log', '/.env')) for name in names)
        assert all('/.venv/' not in name and '/.data/' not in name for name in names)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    OUTPUT.with_suffix('.zip.sha256').write_text(digest + '  ' + OUTPUT.name + '\n', encoding='utf-8')
    print(f'Built {OUTPUT.name}: {len(files)} files, {OUTPUT.stat().st_size:,} bytes. ZIP integrity verified; no live keys/databases included.')


if __name__ == '__main__':
    main()
