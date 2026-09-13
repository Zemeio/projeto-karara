"""Validate and unpack a .karara file (a zip containing one .eaf + one audio file)."""
import zipfile
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2GB guard against zip bombs


class InvalidKararaFile(ValueError):
    pass


def _is_junk(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return name.endswith("/") or base.startswith(".") or "__MACOSX" in name


def inspect(zip_path: Path):
    """Validate structure without extracting. Returns (eaf_name, audio_name)."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidKararaFile(f"Not a valid zip file: {exc}") from exc

    with zf:
        infos = [info for info in zf.infolist() if not _is_junk(info.filename)]

        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise InvalidKararaFile(
                f"Archive expands to {total_uncompressed / 1_048_576:.0f}MB, over the "
                f"{MAX_UNCOMPRESSED_BYTES / 1_048_576:.0f}MB limit"
            )

        eaf_files = [i.filename for i in infos if i.filename.lower().endswith(".eaf")]
        audio_files = [i.filename for i in infos if Path(i.filename.lower()).suffix in AUDIO_EXTENSIONS]

        if len(eaf_files) != 1:
            raise InvalidKararaFile(f"Expected exactly one .eaf file, found {len(eaf_files)}")
        if len(audio_files) != 1:
            raise InvalidKararaFile(
                f"Expected exactly one audio file ({', '.join(sorted(AUDIO_EXTENSIONS))}), "
                f"found {len(audio_files)}"
            )

        return eaf_files[0], audio_files[0]


def extract(zip_path: Path, dest_dir: Path):
    """Validate then extract just the .eaf and audio file into dest_dir. Returns (eaf_path, audio_path)."""
    eaf_name, audio_name = inspect(zip_path)
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        eaf_dest = dest_dir / Path(eaf_name).name
        audio_dest = dest_dir / Path(audio_name).name
        with zf.open(eaf_name) as src, open(eaf_dest, "wb") as dst:
            dst.write(src.read())
        with zf.open(audio_name) as src, open(audio_dest, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)

    return eaf_dest, audio_dest
