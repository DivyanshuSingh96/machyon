"""The system's folder dialog for ``machyon ui``, shown by Tkinter in a process of its own.

The server runs this file with ``python -I picker.py --initial FOLDER`` (an argument list, never a shell), so a
dialog left open can never block it. It uses only the standard library and prints one JSON line:
``{"path": "..."}``, ``{"cancelled": true}`` or ``{"error": "..."}``. JSON's ASCII escapes keep any folder name
intact whatever the console's encoding.
"""
import argparse
import json
import os
import sys


def emit(answer: dict) -> int:
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 0 if "error" not in answer else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--initial", default=None)
    ap.add_argument("--title", default="Choose where Machyon saves its runs")
    a = ap.parse_args(argv)
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return emit({"error": "This Python has no Tkinter, so no folder dialog can be shown."})
    if sys.platform == "win32":
        try:   # a sharp dialog on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        return emit({"error": f"There is no desktop to show a folder dialog on ({e})."})
    try:
        root.withdraw()
        root.attributes("-topmost", True)   # in front of the browser that asked for it
        root.update()
        initial = a.initial if a.initial and os.path.isdir(a.initial) else None
        chosen = filedialog.askdirectory(parent=root, initialdir=initial, title=a.title, mustexist=False)
    except tkinter.TclError as e:
        return emit({"error": f"The folder dialog failed ({e})."})
    finally:
        try:
            root.destroy()
        except tkinter.TclError:
            pass
    if not chosen:
        return emit({"cancelled": True})
    return emit({"path": os.path.abspath(chosen)})


if __name__ == "__main__":
    sys.exit(main())
