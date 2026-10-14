"""ZIP по белому списку: без проектов, учётных данных и пользовательских логов."""

from pathlib import Path
import zipfile


FILES = ("launch.py", "start-codex.cmd", "start.sh", "README.md")


def build(root, output):
    root = Path(root)
    output = Path(output)
    # Проверить все источники до создания архива.
    sources = [(name, (root / "launcher" / name).read_bytes()) for name in FILES]
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in sources:
            if name == "start.sh":
                contents = contents.replace(b"\r\n", b"\n")
            elif name == "start-codex.cmd":
                contents = contents.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            archive.writestr("codex-launcher/" + name, contents)
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    result = build(root, root / "dist" / "codex-launcher.zip")
    print("Создан dist/codex-launcher.zip")
