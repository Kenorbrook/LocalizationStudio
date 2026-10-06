"""Native Windows close chooser and notification-area icon."""

import json, threading
from pathlib import Path
from project_lifecycle import stop_worker


def read_choice(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("close_choice")
    except (OSError, ValueError, TypeError):
        return None
    return value if value in {"tray", "exit"} else None


def write_choice(path, value):
    if value not in {None, "tray", "exit"}:
        raise ValueError("Некорректный выбор")
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"close_choice": value}), encoding="utf-8")
    temporary.replace(path)


def stop_jobs(store, stopper=stop_worker):
    with store.launch_lock:
        store.closing = True
        with store.db() as db:
            jobs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM jobs WHERE state IN ('running','queued','paused') OR worker_active=1"
                )
            ]
            db.execute(
                "UPDATE jobs SET state='paused',error='Приложение закрыто; очередь сохранена' WHERE state IN ('running','queued')"
            )
            db.execute("UPDATE jobs SET state='held' WHERE state='waiting'")
        try:
            for job in jobs:
                stopper(store, job)
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET worker_active=0 WHERE state IN ('paused','held')"
                )
        except Exception:
            store.closing = False
            raise
    return len(jobs)


class CloseController:
    def __init__(self, store):
        self.store = store
        store.launch_lock = threading.RLock()
        store.closing = False
        self.path = store.path.with_name("desktop_preferences.json")
        self.window = None
        self.tray = None
        self.pending = False
        self.allow_exit = False
        self.lock = threading.Lock()

    def form(self):
        from webview.platforms.winforms import BrowserView

        return BrowserView.instances[self.window.uid]

    def ui(self, callback):
        from System import Action

        form = self.form()
        if form.InvokeRequired:
            form.Invoke(Action(callback))
        else:
            callback()

    def initialize(self):
        def create():
            from System.Windows.Forms import NotifyIcon, ContextMenuStrip
            from System.Drawing import SystemIcons

            self.tray = NotifyIcon()
            self.tray.Icon = SystemIcons.Application
            self.tray.Text = "Localization Studio"
            self.tray.Visible = False
            menu = ContextMenuStrip()
            menu.Items.Add("Открыть").Click += lambda *_: self.show()
            menu.Items.Add(
                "Снова спрашивать при закрытии"
            ).Click += lambda *_: self.reset_close_choice()
            menu.Items.Add(
                "Закрыть и остановить перевод"
            ).Click += lambda *_: self.request("exit")
            self.tray.ContextMenuStrip = menu
            self.tray.DoubleClick += lambda *_: self.show()

        self.ui(create)

    def show(self):
        def restore():
            from System.Windows.Forms import FormWindowState

            form = self.form()
            form.Show()
            form.WindowState = FormWindowState.Normal
            form.Activate()
            if self.tray:
                self.tray.Visible = False

        self.ui(restore)

    def reset_close_choice(self):
        write_choice(self.path, None)
        return {"choice": None}

    def get_close_state(self):
        return {
            "choice": read_choice(self.path),
            "in_tray": bool(self.tray and self.tray.Visible),
            "pending": self.pending,
        }

    def chooser(self):
        result = {"choice": "cancel", "remember": False}

        def display():
            from System.Windows.Forms import (
                Form,
                Label,
                Button,
                CheckBox,
                FormBorderStyle,
                FormStartPosition,
            )
            from System.Drawing import Color, Size, Point, Font

            dialog = Form()
            dialog.Text = "Закрытие Localization Studio"
            dialog.ClientSize = Size(650, 225)
            dialog.FormBorderStyle = FormBorderStyle.FixedDialog
            dialog.MaximizeBox = False
            dialog.MinimizeBox = False
            dialog.ShowInTaskbar = False
            dialog.StartPosition = FormStartPosition.CenterParent
            dialog.Font = Font("Segoe UI", 10)
            dialog.BackColor = Color.FromArgb(26, 34, 40)
            dialog.ForeColor = Color.White
            title = Label()
            title.Text = "Что сделать при закрытии программы?"
            title.Location = Point(22, 20)
            title.Size = Size(600, 27)
            dialog.Controls.Add(title)
            remember = CheckBox()
            remember.Text = "Запомнить мой выбор"
            remember.Location = Point(22, 111)
            remember.Size = Size(600, 28)
            dialog.Controls.Add(remember)
            for index, (choice, text) in enumerate(
                [
                    ("tray", "Свернуть в трей"),
                    ("exit", "Закрыть с выгрузкой"),
                    ("cancel", "Отмена"),
                ]
            ):
                button = Button()
                button.Text = text
                button.Location = Point(22 + index * 206, 59)
                button.Size = Size(194, 40)
                button.BackColor = Color.FromArgb(45, 60, 68)

                def clicked(*_, choice=choice):
                    result.update(choice=choice, remember=bool(remember.Checked))
                    dialog.Close()

                button.Click += clicked
                dialog.Controls.Add(button)
                if choice == "cancel":
                    dialog.CancelButton = button
            warning = Label()
            warning.Text = "При закрытии с выгрузкой из памяти перевод остановится.\nПрогресс и очередь сохранятся; их можно продолжить при следующем запуске."
            warning.Location = Point(22, 153)
            warning.Size = Size(600, 58)
            dialog.Controls.Add(warning)
            dialog.ShowDialog(self.form())
            dialog.Dispose()

        self.ui(display)
        return result

    def request(self, explicit=None):
        with self.lock:
            if self.pending:
                return
            self.pending = True

        def act():
            try:
                choice = explicit or read_choice(self.path)
                remember = False
                if not choice:
                    selected = self.chooser()
                    choice = selected["choice"]
                    remember = selected["remember"]
                if choice == "cancel":
                    return
                if choice == "tray":

                    def hide():
                        if not self.tray:
                            raise RuntimeError("Иконка трея ещё не готова")
                        self.tray.Visible = True
                        self.form().Hide()

                    self.ui(hide)
                    (
                        write_choice(self.path, "tray" if remember else None)
                        if not explicit and not read_choice(self.path)
                        else None
                    )
                elif choice == "exit":
                    if self.window.evaluate_js(
                        'typeof studioState!=="undefined" && studioState.dirty.size>0'
                    ):
                        raise ValueError(
                            "Есть несохранённые правки. Сохраните или отмените их перед полным закрытием."
                        )
                    stop_jobs(self.store)
                    if not explicit and remember:
                        write_choice(self.path, "exit")
                    self.allow_exit = True
                    self.window.destroy()
            except Exception as error:

                def message():
                    from System.Windows.Forms import MessageBox

                    MessageBox.Show(
                        self.form(), str(error), "Не удалось закрыть программу"
                    )

                self.ui(message)
            finally:
                self.pending = False

        threading.Thread(target=act, daemon=True).start()

    def on_closing(self):
        if self.allow_exit:
            return True
        self.request()
        return False

    def dispose(self):
        if self.tray:
            self.tray.Visible = False
            self.tray.Dispose()


class CloseApi:
    def __init__(self, controller):
        self._controller = controller

    def reset_close_choice(self):
        return self._controller.reset_close_choice()

    def get_close_state(self):
        return self._controller.get_close_state()
