"""Windows desktop entry point. Run app.py, or build the portable executable."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from audio_format import ToolError
from backend import Cancelled, device_ports, generate


def app_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[3] / "湾湾小何语音生成工具"


class VoiceApp:
    def __init__(self, root):
        self.root = root
        self.messages, self.cancel = queue.Queue(), threading.Event()
        self.busy, self.closing, self.result = False, False, None
        self.ports = {}
        root.title("湾湾小何 · 文字转 OGG")
        root.geometry("920x790")
        root.minsize(820, 730)
        root.configure(bg="#F3F5F9")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#F3F5F9")
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="#F3F5F9", foreground="#18243B")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"))
        style.configure("Hint.TLabel", foreground="#607087", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background="white")
        style.configure("TButton", padding=(12, 8), font=("Microsoft YaHei UI", 10))
        style.configure("Primary.TButton", background="#2364DF", foreground="white", padding=(22, 10))
        style.map("Primary.TButton", background=[("disabled", "#C8D2E3"), ("active", "#184FBA")])
        style.configure("TEntry", padding=7)
        style.configure("TCombobox", padding=7)
        outer = ttk.Frame(root, padding=(28, 20))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="把文字变成小智的声音", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="湾湾小何  /  本地 OGG 导出", style="Hint.TLabel").pack(anchor="w", pady=(4, 14))
        card = ttk.Frame(outer, style="Card.TFrame", padding=18)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="SparkBot 串口", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.port_value = tk.StringVar()
        self.port_box = ttk.Combobox(card, textvariable=self.port_value, state="readonly")
        self.port_box.grid(row=0, column=1, sticky="ew")
        self.refresh_button = ttk.Button(card, text="刷新设备", command=self.refresh_ports)
        self.refresh_button.grid(row=0, column=2, padx=(10, 0))
        ttk.Label(card, text="连接 USB，并保持小智角色为“湾湾小何”。生成期间会临时暂停设备，结束后自动恢复。",
                  style="Card.TLabel", font=("Microsoft YaHei UI", 9), foreground="#64748B", wraplength=790).grid(
                      row=1, column=0, columnspan=3, sticky="w", pady=(9, 0))
        ttk.Label(outer, text="想让它说什么", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(17, 7))
        self.text = tk.Text(outer, height=4, font=("Microsoft YaHei UI", 14), wrap="word", bd=0,
                            padx=14, pady=12, bg="white", fg="#18243B", insertbackground="#2364DF", undo=True)
        self.text.pack(fill="x")
        self.text.insert("1.0", "您好，找我有什么事情吗？")
        ttk.Label(outer, text="建议用简短句子。工具会核对返回文字，并检查固件要求的 8 秒时长上限。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 10))
        files = ttk.Frame(outer)
        files.pack(fill="x")
        files.columnconfigure(3, weight=1)
        ttk.Label(files, text="用途").grid(row=0, column=0, sticky="w")
        self.purpose = tk.StringVar(value="问候语")
        self.purpose_box = ttk.Combobox(files, textvariable=self.purpose, values=("问候语", "告别语", "其他提示语"), state="readonly", width=12)
        self.purpose_box.grid(row=0, column=1, padx=(8, 18))
        self.purpose_box.bind("<<ComboboxSelected>>", self.change_purpose)
        ttk.Label(files, text="文件名前缀").grid(row=0, column=2, sticky="w")
        self.stem = tk.StringVar(value="touch_greeting")
        self.stem_entry = ttk.Entry(files, textvariable=self.stem)
        self.stem_entry.grid(row=0, column=3, padx=(8, 0), sticky="ew")
        self.output = tk.StringVar(value=str(app_directory() / "生成结果"))
        output = ttk.Frame(outer)
        output.pack(fill="x", pady=(10, 13))
        ttk.Label(output, text="输出目录").pack(side="left", padx=(0, 8))
        self.output_entry = ttk.Entry(output, textvariable=self.output)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.browse = ttk.Button(output, text="选择目录", command=self.choose_output)
        self.browse.pack(side="left", padx=(10, 0))
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.start = ttk.Button(actions, text="生成 OGG", style="Primary.TButton", command=self.start_generation)
        self.start.pack(side="left")
        self.stop = ttk.Button(actions, text="取消", state="disabled", command=self.cancel_generation)
        self.stop.pack(side="left", padx=8)
        self.play = ttk.Button(actions, text="试听", state="disabled", command=self.preview)
        self.play.pack(side="left")
        ttk.Button(actions, text="打开输出目录", command=self.open_output).pack(side="right")
        self.status = tk.StringVar(value="准备就绪")
        ttk.Label(outer, textvariable=self.status, font=("Microsoft YaHei UI", 10, "bold"), wraplength=820).pack(anchor="w", pady=(14, 5))
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        self.log = tk.Text(outer, height=5, wrap="word", font=("Microsoft YaHei UI", 9), bd=0,
                           bg="#EAF0F8", fg="#465970", padx=10, pady=8, state="disabled")
        self.log.pack(fill="both", expand=True)
        ttk.Label(outer, text="导出格式：OGG / Opus · 单声道 16kHz · 60ms 帧 · 250ms 静音尾部。导出后需重新编译烧录。",
                  style="Hint.TLabel", wraplength=830).pack(anchor="w", pady=(9, 0))
        self.refresh_ports()
        self.root.after(100, self.poll)

    def refresh_ports(self):
        if self.busy:
            return
        self.ports = {f"{port}  ·  {description}": port for port, description in device_ports()}
        self.port_box["values"] = tuple(self.ports)
        if self.port_value.get() not in self.ports:
            self.port_value.set(next(iter(self.ports), ""))
        self.status.set("准备就绪" if self.ports else "请连接 SparkBot 的原生 USB 接口，然后点击“刷新设备”。")

    def change_purpose(self, _event=None):
        values = {"问候语": ("touch_greeting", "您好，找我有什么事情吗？"),
                  "告别语": ("touch_farewell", "再见啊，下次有需求可以再来找我"), "其他提示语": ("prompt", "")}
        stem, text = values[self.purpose.get()]
        current = self.text.get("1.0", "end").strip()
        if current in ("", "您好，找我有什么事情吗？", "再见啊，下次有需求可以再来找我"):
            self.text.delete("1.0", "end")
            self.text.insert("1.0", text)
        self.stem.set(stem)

    def choose_output(self):
        directory = filedialog.askdirectory(title="选择 OGG 输出目录", initialdir=self.output.get())
        if directory:
            self.output.set(directory)

    def write_log(self, text):
        self.status.set(text.splitlines()[0])
        self.log.configure(state="normal")
        self.log.insert("end", datetime.now().strftime("%H:%M:%S  ") + text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.start, self.refresh_button, self.browse, self.output_entry, self.stem_entry):
            control.configure(state="disabled" if busy else "normal")
        self.port_box.configure(state="disabled" if busy else "readonly")
        self.purpose_box.configure(state="disabled" if busy else "readonly")
        self.text.configure(state="disabled" if busy else "normal")
        self.stop.configure(state="normal" if busy else "disabled")
        self.play.configure(state="normal" if self.result and not busy else "disabled")
        self.progress.start(12) if busy else self.progress.stop()

    def start_generation(self):
        port = self.ports.get(self.port_value.get())
        if not port:
            messagebox.showinfo("连接设备", "请先连接 SparkBot 的原生 USB 接口，再刷新并选择串口。")
            return
        text, output, stem = self.text.get("1.0", "end").strip(), self.output.get().strip(), self.stem.get().strip()
        if not text or not output:
            messagebox.showinfo("填写内容", "请输入要生成的文字，并选择输出目录。")
            return
        self.cancel.clear()
        self.set_busy(True)
        self.write_log("开始生成，设备当前会话将暂时暂停。")

        def work():
            try:
                result = generate(text, port, output, stem, lambda value: self.messages.put(("log", value)), self.cancel)
                self.messages.put(("done", result))
            except Cancelled as error:
                self.messages.put(("cancelled", str(error)))
            except ToolError as error:
                self.messages.put(("error", str(error)))
            except Exception:
                self.messages.put(("error", "生成失败。请确认设备与电脑均可联网，重新启动设备后重试。"))

        threading.Thread(target=work, daemon=False).start()

    def cancel_generation(self):
        self.cancel.set()
        self.stop.configure(state="disabled")
        self.write_log("正在取消并恢复设备，请稍候……")

    def poll(self):
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "log":
                self.write_log(value)
                continue
            self.set_busy(False)
            if kind == "done":
                self.result = value
                self.play.configure(state="normal")
                self.write_log(f"生成成功 · 文字核对通过 · {value['seconds']:.2f} 秒\n{value['ogg']}")
            else:
                self.write_log(value)
                if kind == "error" and not self.closing:
                    messagebox.showinfo("未导出音频", value)
            if self.closing:
                self.root.destroy()
                return
        self.root.after(100, self.poll)

    def preview(self):
        if self.result:
            import winsound
            winsound.PlaySound(self.result["wav"], winsound.SND_FILENAME | winsound.SND_ASYNC)

    def open_output(self):
        directory = Path(self.output.get()).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel_generation()
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="SparkBot 当前角色文字转 OGG")
    parser.add_argument("--generate")
    parser.add_argument("--port")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--name", default="prompt")
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    if args.generate is not None:
        if not args.port or not args.output:
            parser.error("--generate 需要 --port 和 --output")
        def report(text):
            if args.log_file:
                with args.log_file.open("a", encoding="utf-8") as log:
                    log.write(text + "\n")
            elif sys.stdout:
                print(text, flush=True)
        try:
            result = generate(args.generate, args.port, args.output, args.name, report, threading.Event())
            report(json.dumps(result, ensure_ascii=False))
            return 0
        except ToolError as error:
            report(str(error))
            return 1
    root = tk.Tk()
    VoiceApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
