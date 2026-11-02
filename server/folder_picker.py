"""
server/folder_picker.py
Standalone GUI process for native Windows folder selection.

Why:
Tkinter requires execution on the main thread of a dedicated process to avoid
'main thread is not in main loop' errors and pop up the native explorer window
directly in front of the browser.
"""

import sys
import os
import tkinter as tk
from tkinter import filedialog


def main():
    initial_dir = sys.argv[1] if (len(sys.argv) > 1 and sys.argv[1] and os.path.isdir(sys.argv[1])) else os.getcwd()

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()

    selected = filedialog.askdirectory(
        title="Select Audio Source Directory",
        initialdir=initial_dir,
        mustexist=True,
    )
    root.destroy()

    if selected:
        print(os.path.normpath(selected))


if __name__ == "__main__":
    main()
