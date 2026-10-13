"""Русский Tkinter-интерфейс демонстрационного Supervisor без внешних пакетов."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .core import ActionError, STATUSES
from .mock import MockBackend

BG = "#f3f5f9"
WHITE = "#ffffff"
NAVY = "#15243c"
MUTED = "#62718b"
ACCENT = "#235bd4"


class App:
    def __init__(self, root: tk.Tk, supervisor, lock):
        self.root = root
        self.controller = supervisor
        self.backend = MockBackend()
        self.lock = lock
        self.project_ids = []
        self.session_ids = []

        root.title("Codex Supervisor · прототип (демо)")
        root.geometry("1090x730")
        root.minsize(820, 560)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=NAVY, font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 17))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 7))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 9), padding=(12, 7))

        header = ttk.Frame(root, padding=(20, 16, 20, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Codex Supervisor", style="Header.TLabel").pack(side="left")
        ttk.Label(
            header, text="  MVP · фиктивный backend · без сети и вызовов модели",
            style="Muted.TLabel"
        ).pack(side="left", padx=(14, 0))

        main = ttk.Frame(root, padding=(20, 0, 20, 12))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0, minsize=235)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        side = ttk.Frame(main, padding=(0, 12, 16, 0))
        side.grid(row=0, column=0, sticky="nsew")
        ttk.Label(side, text="Проекты", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        self.project_list = tk.Listbox(
            side, selectmode="browse", exportselection=False,
            bg=WHITE, fg=NAVY, font=("Segoe UI", 10),
            selectbackground="#d7e6ff", selectforeground=NAVY,
            highlightthickness=0, relief="solid", borderwidth=1,
            activestyle="none", height=12,
        )
        self.project_list.pack(fill="both", expand=True, pady=(10, 10))
        self.project_list.bind("<<ListboxSelect>>", self.project_selected)
        ttk.Button(side, text="+ Новый демо-проект",
                   command=self.add_project).pack(fill="x")
        ttk.Label(
            side, text="Проекты здесь — только названия.\n"
                       "Реальные папки и файлы не читаются.",
            style="Muted.TLabel", justify="left"
        ).pack(anchor="w", pady=(14, 0))

        content = ttk.Frame(main, padding=(14, 10, 0, 0))
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        top = ttk.Frame(content)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.project_title = ttk.Label(
            top, text="", font=("Segoe UI Semibold", 13)
        )
        self.project_title.pack(side="left")
        self.status = ttk.Label(top, text="", style="Muted.TLabel")
        self.status.pack(side="right")

        options = ttk.Frame(content)
        options.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(options, text="Сессия:").pack(side="left", padx=(0, 8))
        self.session_choice = ttk.Combobox(options, state="readonly", width=36)
        self.session_choice.pack(side="left", fill="x", expand=True)
        self.session_choice.bind("<<ComboboxSelected>>", self.session_selected)
        ttk.Button(options, text="Новая сессия",
                   command=self.new_session).pack(side="left", padx=(9, 0))

        conversation = ttk.Frame(content)
        conversation.grid(row=2, column=0, sticky="nsew")
        conversation.rowconfigure(0, weight=1)
        conversation.columnconfigure(0, weight=1)
        self.history = tk.Text(
            conversation, wrap="word", state="disabled", padx=19, pady=16,
            bg=WHITE, fg=NAVY, font=("Segoe UI", 10),
            highlightbackground="#dbe2ed", highlightthickness=1,
            relief="flat", borderwidth=0, spacing3=14
        )
        self.history.grid(row=0, column=0, sticky="nsew")
        self.history.tag_configure("who_user", foreground=ACCENT,
                                   font=("Segoe UI Semibold", 10))
        self.history.tag_configure("who_assistant", foreground=NAVY,
                                   font=("Segoe UI Semibold", 10))
        self.history.tag_configure("who_system", foreground=MUTED,
                                   font=("Segoe UI Semibold", 10))
        self.history.tag_configure("body", spacing1=5)
        self.history.tag_configure("hint", foreground=MUTED,
                                   font=("Segoe UI", 10))
        scroller = ttk.Scrollbar(conversation, orient="vertical",
                                 command=self.history.yview)
        scroller.grid(row=0, column=1, sticky="ns")
        self.history.config(yscrollcommand=scroller.set)

        controls = ttk.Frame(content)
        controls.grid(row=3, column=0, sticky="ew", pady=(12, 7))
        for text, name, action in [
            ("Пауза", "pause", self.pause),
            ("Продолжить", "resume", self.resume),
            ("Стоп", "stop", self.stop),
            ("Завершить", "complete", self.complete),
            ("Лимит · демо", "limit", self.simulate_limit),
        ]:
            btn = ttk.Button(controls, text=text, command=action)
            btn.pack(side="left", padx=(0, 5))
            setattr(self, "btn_" + name, btn)

        ttk.Label(content, text="Сообщение для фиктивного агента · Ctrl+Enter — отправить",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        compose = ttk.Frame(content)
        compose.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        compose.columnconfigure(0, weight=1)
        self.input = tk.Text(
            compose, height=4, wrap="word", bg=WHITE, fg=NAVY,
            font=("Segoe UI", 10), padx=12, pady=9,
            highlightbackground="#dbe2ed", highlightthickness=1,
            relief="flat", borderwidth=0
        )
        self.input.grid(row=0, column=0, sticky="ew")
        self.input.bind("<Control-Return>", self.send_shortcut)
        self.btn_send = ttk.Button(
            compose, text="Отправить", style="Accent.TButton",
            command=self.send
        )
        self.btn_send.grid(row=0, column=1, sticky="se", padx=(10, 0))
        ttk.Label(
            root,
            text="Демо: не вставляйте секреты и реальные клиентские данные. "
                 "Текст хранится только локально в JSON вашего профиля.",
            style="Muted.TLabel", anchor="center"
        ).pack(fill="x", pady=(0, 12))
        self._render()

    def _run(self, action):
        try:
            return action()
        except (ActionError, ValueError) as exc:
            messagebox.showwarning("Supervisor · демо", str(exc), parent=self.root)
        except Exception as exc:
            messagebox.showerror(
                "Ошибка сохранения", f"Действие не удалось.\n{exc}", parent=self.root
            )
        return None

    def _render(self):
        state = self.controller.data
        active = self.controller.active()
        self.project_ids = [p["id"] for p in state["projects"]]
        self.project_list.delete(0, tk.END)
        for project in state["projects"]:
            self.project_list.insert(tk.END, project["name"])
        idx = self.project_ids.index(state["active_project_id"])
        self.project_list.selection_set(idx)
        self.project_list.activate(idx)
        self.project_title.config(text=state["projects"][idx]["name"])
        self.status.config(text=STATUSES[active["status"]])

        options = self.controller.sessions()
        self.session_ids = [s["id"] for s in options]
        self.session_choice["values"] = [
            f"{n}. {s['id'][:8]} · {STATUSES[s['status']]}"
            for n, s in enumerate(options, 1)
        ]
        self.session_choice.current(
            self.session_ids.index(state["active_session_id"])
        )

        self.history.config(state="normal")
        self.history.delete("1.0", "end")
        if not active["messages"]:
            self.history.insert("end", "Пока сообщений нет.\n", "hint")
            self.history.insert(
                "end", "Введите тестовое сообщение ниже. "
                       "Ответ будет сгенерирован простым Python-кодом, без Codex.",
                "hint"
            )
        else:
            labels = {"user": "Вы", "assistant": "Фиктивный агент", "system": "Система"}
            for msg in active["messages"]:
                self.history.insert("end", labels[msg["role"]] + "\n",
                                    "who_" + msg["role"])
                self.history.insert("end", msg["text"] + "\n\n", "body")
        if active["status"] == "needs_review":
            self.history.insert(
                "end", "\nПосле прошлого сбоя исход хода неизвестен. "
                       "«Продолжить» сбросит ожидающий ход БЕЗ повторной отправки.\n",
                "hint"
            )
        self.history.config(state="disabled")
        self.history.see("end")
        status = active["status"]
        def enabled(btn, flag):
            btn.configure(state="normal" if flag else "disabled")
        enabled(self.btn_send, status == "idle")
        enabled(self.btn_pause, status in ("idle", "running")
                and not active["pause_after"])
        enabled(self.btn_resume, status in (
            "paused", "needs_review", "await_user", "await_limit", "error"
        ))
        enabled(self.btn_stop, status not in ("stopped", "completed"))
        enabled(self.btn_complete, status in ("idle", "paused"))
        enabled(self.btn_limit, status == "idle")

    def project_selected(self, event=None):
        selection = self.project_list.curselection()
        if not selection:
            return
        chosen = self.project_ids[selection[0]]
        if chosen != self.controller.data["active_project_id"]:
            self._run(lambda: self.controller.select_project(chosen))
            self._render()

    def session_selected(self, event=None):
        index = self.session_choice.current()
        if index >= 0:
            chosen = self.session_ids[index]
            if chosen != self.controller.data["active_session_id"]:
                self._run(lambda: self.controller.select_session(chosen))
                self._render()

    def add_project(self):
        name = simpledialog.askstring(
            "Новый демо-проект", "Название проекта (без доступа к файлам):",
            parent=self.root
        )
        if name is not None:
            self._run(lambda: self.controller.add_project(name))
            self._render()

    def new_session(self):
        self._run(self.controller.new_session)
        self._render()

    def pause(self):
        self._run(self.controller.pause)
        self._render()

    def resume(self):
        self._run(self.controller.resume)
        self._render()

    def stop(self):
        self._run(self.controller.stop)
        self._render()

    def complete(self):
        self._run(self.controller.complete)
        self._render()

    def simulate_limit(self):
        self._run(self.controller.simulate_wait)
        self._render()

    def send_shortcut(self, event):
        self.send()
        return "break"

    def send(self):
        text = self.input.get("1.0", "end-1c")
        result = self._run(lambda: self.controller.submit(text))
        if result is None:
            return
        session_id, turn_id = result
        self.input.delete("1.0", "end")
        self._render()
        # Ответ искусственно отложен для проверки Паузы и Стоп.
        self.root.after(
            700, lambda sid=session_id, tid=turn_id, source=text:
            self._finish(sid, tid, source)
        )

    def _finish(self, sid, tid, source):
        answer = self.backend.reply(source)
        self._run(lambda: self.controller.finish_mock(sid, tid, answer))
        self._render()

    def close(self):
        try:
            self.lock.close()
        finally:
            self.root.destroy()
