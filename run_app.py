"""
run_app.py — Start The Settler App without typing terminal commands.

Put this file in the same folder as main.py, then either:
  • Double-click run_app.py, or
  • Run:  python run_app.py
"""
import os
import subprocess
import sys
import time
import webbrowser

PORT = 8000
URL = f"http://localhost:{PORT}"


def main():
    here = os.path.dirname(os.path.abspath(__file__))

    print("=" * 62)
    print("  THE SETTLER APP")
    print("=" * 62)
    print(f"  Folder : {here}")
    print(f"  URL    : {URL}")
    print()
    print("  First load pulls several years of Dallas crime data.")
    print("  Expect 30-90 seconds. Progress appears below and in the app.")
    print()
    print("  Press Ctrl+C here to stop the server.")
    print("=" * 62)
    print()

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=here,
    )

    time.sleep(2.5)
    try:
        webbrowser.open(URL)
    except Exception:
        print(f"Could not open a browser automatically. Visit {URL}")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        proc.terminate()
        proc.wait()
        print("Stopped.")


if __name__ == "__main__":
    main()
