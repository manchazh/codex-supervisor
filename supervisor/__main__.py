"""Запуск демонстрационного приложения: python -m supervisor."""

import tkinter as tk
from tkinter import messagebox

from .core import Supervisor
from .storage import AlreadyRunning, InstanceLock, StateError, StateStore, data_directory
from .ui import App


def main():
    directory = data_directory()
    lock = InstanceLock(directory)
    try:
        lock.acquire()
        controller = Supervisor(StateStore(directory))
    except (AlreadyRunning, StateError, OSError) as exc:
        lock.close()
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Supervisor не запущен", str(exc), parent=root)
        root.destroy()
        return 1

    try:
        root = tk.Tk()
        App(root, controller, lock)
        root.mainloop()
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
