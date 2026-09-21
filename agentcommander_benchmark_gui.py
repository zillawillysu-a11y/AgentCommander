import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from commander.benchmark import prepare


class BenchmarkApp(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("AgentCommander Benchmark"); self.geometry("620x300"); self.resizable(False, False)
        self.root = None; self.output = tk.StringVar(value=str(Path.home() / "AgentCommander-Benchmarks")); self._build()

    def _build(self):
        frame = ttk.Frame(self, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="AgentCommander A/B Benchmark", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="建立 Direct 與 AgentCommander 兩份相同測試專案，再用同一份 prompt 比較。", wraplength=570).pack(anchor="w", pady=(8, 14))
        row = ttk.Frame(frame); row.pack(fill="x"); ttk.Label(row, text="輸出資料夾").pack(side="left"); ttk.Entry(row, textvariable=self.output, width=55).pack(side="left", padx=8); ttk.Button(row, text="選擇", command=self.choose).pack(side="left")
        buttons = ttk.Frame(frame); buttons.pack(fill="x", pady=18)
        ttk.Button(buttons, text="建立 A/B 測試", command=self.create).pack(fill="x", pady=3)
        ttk.Button(buttons, text="開啟輸出資料夾", command=lambda: os.startfile(self.output.get())).pack(fill="x", pady=3)
        ttk.Button(buttons, text="離開", command=self.destroy).pack(fill="x", pady=3)
        self.status = ttk.Label(frame, text="尚未建立測試"); self.status.pack(anchor="w")

    def choose(self):
        selected = filedialog.askdirectory(initialdir=self.output.get())
        if selected: self.output.set(selected)

    def create(self):
        try:
            root = prepare(Path(self.output.get())); self.status.config(text=f"已建立：{root}"); os.startfile(root)
        except Exception as exc:
            messagebox.showerror("Benchmark", str(exc))


if __name__ == "__main__":
    BenchmarkApp().mainloop()
