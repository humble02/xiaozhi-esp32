"""Build a portable GUI. Keep generated user audio outside the app directory."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.output, args.work = args.output.resolve(), args.work.resolve()
    source = Path(__file__).resolve().parent
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
                    "--name", "WanwanVoice", "--distpath", str(args.work / "dist"),
                    "--workpath", str(args.work / "build"), "--specpath", str(args.work),
                    "--collect-all", "esptool", "--collect-data", "imageio_ffmpeg",
                    "--hidden-import", "win32timezone", "--hidden-import", "win32com.client",
                    str(source / "app.py")], check=True, cwd=source)
    shutil.copytree(args.work / "dist/WanwanVoice", args.output / "app", dirs_exist_ok=True)
    (args.output / "双击启动.cmd").write_text('@echo off\r\nstart "" "%~dp0app\\WanwanVoice.exe"\r\n', encoding="ascii")
    shutil.copyfile(source / "README.md", args.output / "使用说明.md")
    (args.output / "生成结果").mkdir(exist_ok=True)
    print(str(args.output / "双击启动.cmd"))


if __name__ == "__main__":
    main()
