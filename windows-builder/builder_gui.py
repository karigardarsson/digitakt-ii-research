"""Single-window Windows GUI for the DT2 Déjà Vu v0.1.1 builder."""

from __future__ import annotations

import sys
import threading
import time
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from builder_core import BuildError, ZOOM_PROFILE, build_firmware, default_output_path, detect_profile


PROGRESS_BY_MESSAGE = {
    "Checking official firmware…": 10,
    "Reading firmware sections…": 30,
    "Building Déjà Vu firmware…": 55,
    "Building Déjà Vu + ONESHOT Zoom firmware…": 55,
    "Verifying all six sections and checksums…": 80,
    "Writing verified output…": 95,
}


def accepted_dialog_path(value: str) -> str | None:
    """Normalize a file-dialog result without changing state on cancellation."""
    return value or None


class BuilderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DT2 Déjà Vu + Zoom Builder v0.2.0")
        self.root.geometry("680x410")
        self.root.minsize(620, 340)
        self.stock = tk.StringVar()
        self.output = tk.StringVar()
        self.detected = tk.StringVar(value="Detected OS: none")
        self.zoom = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Select the official Digitakt II OS 1.16 or OS 1.17 firmware.")
        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="DT2 Déjà Vu + Zoom v0.2.0 Beta", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            frame,
            text="Build Déjà Vu for OS 1.16 or 1.17. OS 1.17 can also include ONESHOT waveform Zoom.",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 18))

        ttk.Label(frame, text="Official Digitakt II firmware").grid(row=2, column=0, columnspan=3, sticky="w")
        self.stock_entry = ttk.Entry(frame, textvariable=self.stock)
        self.stock_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        self.stock_button = ttk.Button(frame, text="Browse…", command=self.choose_stock)
        self.stock_button.grid(row=3, column=2, padx=(8, 0), pady=(4, 12))

        ttk.Label(frame, textvariable=self.detected, foreground="#245a9b").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        self.zoom_option = ttk.Checkbutton(frame, text="Include ONESHOT Zoom (OS 1.17 only)", variable=self.zoom, command=self._change_edition)
        self.zoom_option.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.zoom_option.configure(state="disabled")
        ttk.Label(frame, text="Output firmware").grid(row=6, column=0, columnspan=3, sticky="w")
        self.output_entry = ttk.Entry(frame, textvariable=self.output)
        self.output_entry.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 16))
        self.output_button = ttk.Button(frame, text="Choose…", command=self.choose_output)
        self.output_button.grid(row=7, column=2, padx=(8, 0), pady=(4, 16))

        self.build_button = ttk.Button(frame, text="Build Déjà Vu", command=self.start_build)
        self.build_button.grid(row=8, column=0, sticky="w")
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100, length=180)
        self.progress.grid(row=8, column=1, sticky="w", padx=(12, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=620).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(16, 0)
        )
        ttk.Label(
            frame,
            text="This tool only creates a file. It does not connect to or update your Digitakt II.",
            foreground="#555555",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(14, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

    def choose_stock(self):
        selected = filedialog.askopenfilename(
            title="Select official Digitakt II OS 1.16 or OS 1.17 firmware",
            filetypes=[("Elektron firmware", "*.syx"), ("All files", "*.*")],
        )
        selected = accepted_dialog_path(selected)
        if selected is None:
            return
        self._accept_stock_path(selected)

    def _accept_stock_path(self, selected: str):
        self.stock.set(selected)
        profile = detect_profile(selected)
        if profile is None:
            self.detected.set("Detected OS: unsupported or modified firmware")
            self.output.set("")
            self.status.set("This file is not a supported official OS 1.16 or OS 1.17 image.")
        else:
            self.detected.set(f"Detected OS: Digitakt II {profile.os_version}")
            self.zoom.set(False)
            self.zoom_option.configure(state="normal" if profile.os_version == "1.17" else "disabled")
            self.output.set(str(default_output_path(Path(selected), profile)))
        self.status.set(f"Ready to build Déjà Vu for OS {profile.os_version}.")

    def _change_edition(self):
        profile = detect_profile(self.stock.get())
        if profile is None or profile.os_version != "1.17":
            self.zoom.set(False)
            return
        selected = ZOOM_PROFILE if self.zoom.get() else profile
        self.output.set(str(default_output_path(Path(self.stock.get()), selected)))
        self.status.set(f"Ready to build {selected.edition} for OS 1.17.")

    def choose_output(self):
        initial = self.output.get().strip()
        selected = filedialog.asksaveasfilename(
            title="Choose output firmware location",
            defaultextension=".syx",
            initialdir=str(Path(initial).parent) if initial else None,
            initialfile=Path(initial).name if initial else "DT2_Deja_Vu_v0.1.1-beta.syx",
            filetypes=[("Elektron firmware", "*.syx")],
        )
        selected = accepted_dialog_path(selected)
        if selected is not None:
            self.output.set(selected)

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for widget in (
            self.stock_entry, self.output_entry, self.stock_button,
            self.output_button, self.build_button, self.zoom_option,
        ):
            widget.configure(state=state)

    def _update_progress(self, message: str):
        self.status.set(message)
        if message.startswith("Detected Digitakt II OS "):
            self.progress["value"] = 20
        else:
            self.progress["value"] = PROGRESS_BY_MESSAGE.get(message, self.progress["value"])

    def _begin_progress(self):
        self.progress["value"] = 0
        self._set_busy(True)
        self.status.set("Starting…")

    def start_build(self):
        stock = self.stock.get().strip()
        output = self.output.get().strip()
        if not output and stock:
            output = str(default_output_path(Path(stock)))
            self.output.set(output)
        self._begin_progress()
        threading.Thread(target=self._worker, args=(stock, output, self.zoom.get()), daemon=True).start()

    def _worker(self, stock: str, output: str, zoom: bool):
        try:
            result = build_firmware(
                stock, output,
                progress=lambda text: self.root.after(0, self._update_progress, text), zoom=zoom,
            )
        except BuildError as exc:
            self.root.after(0, self._finished_error, str(exc))
        except Exception:
            self.root.after(
                0, self._finished_error,
                "An unexpected error stopped the build. No output was written.",
            )
        else:
            self.root.after(0, self._finished_ok, result)

    def _finished_error(self, message: str):
        self._set_busy(False)
        self.progress["value"] = 0
        self.status.set(message)
        messagebox.showerror("Build failed", message, parent=self.root)

    def _finished_ok(self, result: dict):
        self._set_busy(False)
        self.progress["value"] = 100
        self.status.set(f"Success: {result['path']}")
        self.root.update_idletasks()
        self.root.after(150, self._show_success, result)

    def _show_success(self, result: dict):
        messagebox.showinfo(
            "Déjà Vu firmware built",
            f"The verified firmware was created successfully.\n\n{result['path']}",
            parent=self.root,
        )


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--build-for-test":
        try:
            build_firmware(sys.argv[2], sys.argv[3])
        except BuildError:
            return 2
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "--gui-smoke-test":
        root = tk.Tk()
        BuilderWindow(root)
        root.update_idletasks()
        root.update()
        root.destroy()
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "--gui-progress-test":
        root = tk.Tk()
        root.withdraw()
        window = BuilderWindow(root)
        observations = {}
        original_info = messagebox.showinfo
        original_error = messagebox.showerror
        messagebox.showinfo = lambda *_args, **_kwargs: observations.update(
            success_value=int(float(window.progress["value"])),
            success_status=window.status.get(),
        )
        messagebox.showerror = lambda *_args, **_kwargs: observations.update(
            error_value=int(float(window.progress["value"])),
            error_status=window.status.get(),
        )
        window.progress["value"] = 73
        window._finished_error("test failure")
        window.progress["value"] = 73
        window._begin_progress()
        reset_value = int(float(window.progress["value"]))
        window._update_progress("Writing verified output…")
        window._finished_ok({"path": "test.syx"})
        deadline = time.monotonic() + 1.0
        while "success_value" not in observations and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        passed = all((
            observations.get("error_value") == 0,
            observations.get("error_status") == "test failure",
            reset_value == 0,
            observations.get("success_value") == 100,
            observations.get("success_status") == "Success: test.syx",
        ))
        messagebox.showinfo = original_info
        messagebox.showerror = original_error
        root.destroy()
        return 0 if passed else 3
    if len(sys.argv) == 4 and sys.argv[1] in {"--gui-e2e-test", "--gui-e2e-zoom-test"}:
        root = tk.Tk()
        window = BuilderWindow(root)
        observations = {}
        original_info = messagebox.showinfo
        original_error = messagebox.showerror
        def record_result(result):
            observations["result"] = result
            root.after_idle(root.quit)
        messagebox.showinfo = lambda *_args, **_kwargs: record_result("success")
        messagebox.showerror = lambda *_args, **_kwargs: record_result("error")
        window._accept_stock_path(sys.argv[2])
        if sys.argv[1] == "--gui-e2e-zoom-test":
            window.zoom.set(True)
            window._change_edition()
        window.output.set(sys.argv[3])
        root.after(0, window.start_build)
        root.after(60_000, lambda: record_result("timeout"))
        root.mainloop()
        passed = all((
            observations.get("result") == "success",
            int(float(window.progress["value"])) == 100,
            window.status.get().startswith("Success: "),
            Path(sys.argv[3]).is_file(),
        ))
        Path(sys.argv[3] + ".gui-test.json").write_text(json.dumps({
            "detected": window.detected.get(),
            "status": window.status.get(),
            "progress": int(float(window.progress["value"])),
            "result": observations.get("result"),
            "output_exists": Path(sys.argv[3]).is_file(),
        }, indent=2), encoding="utf-8")
        messagebox.showinfo = original_info
        messagebox.showerror = original_error
        root.destroy()
        return 0 if passed else 4
    root = tk.Tk()
    BuilderWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
