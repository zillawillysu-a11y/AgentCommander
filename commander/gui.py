"""Small Tkinter settings controller; it is not a daemon."""
import os
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk
from .config import load_config, local_data_root, save_config
from .doctor import run_doctor
from .entrypoint import helper_path
from .integration import install_codex, remove_codex
from .models import discover_pi_models
from .task_store import list_tasks

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("AgentCommander 0.2.0"); self.geometry("580x430"); self.resizable(False, False)
        self.mode = tk.StringVar(); self.default = tk.StringVar(); self.status = tk.StringVar(); self._build(); self.refresh()
    def _build(self):
        frame = ttk.Frame(self, padding=18); frame.pack(fill="both", expand=True); ttk.Label(frame, text="AgentCommander 0.2.0", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        row = ttk.Frame(frame); row.pack(fill="x", pady=12); ttk.Label(row, text="Mode:").pack(side="left")
        for mode in ("OFF", "AUTO", "FORCE"): ttk.Radiobutton(row, text=mode, value=mode, variable=self.mode, command=self.save_mode).pack(side="left", padx=8)
        ttk.Label(frame, textvariable=self.default).pack(anchor="w"); ttk.Label(frame, textvariable=self.status, justify="left").pack(anchor="w", pady=12)
        buttons = ttk.Frame(frame); buttons.pack(fill="x")
        for label, cmd in (("Install Codex Integration", self.install), ("Remove Codex Integration", self.remove), ("Worker Models", self.models), ("Doctor", self.doctor), ("Open Runtime Folder", self.open_runtime), ("Refresh", self.refresh), ("Exit", self.destroy)): ttk.Button(buttons, text=label, command=cmd).pack(fill="x", pady=2)
    def save_mode(self):
        config = load_config(); config["mode"] = self.mode.get(); save_config(config); self.refresh()
    def refresh(self):
        data = run_doctor(); config = load_config(); self.mode.set(config["mode"]); self.default.set(f"Default Worker: {config['worker'].get('default_profile') or 'Not selected'}")
        tasks = list_tasks(); latest = tasks[-1] if tasks else None
        worker = f"Latest Task         {latest['task_id']}  {latest['status']}" if latest else "Latest Task         NONE"
        c = data["checks"]; self.status.set("Status:\n" + "\n".join((f"Codex Integration   {'READY' if c['codex_integration'] else 'NOT INSTALLED'}", f"MCP                 {'READY' if c['codex'] else 'FAIL'}", f"Pi                  {'READY' if c['pi'] else 'FAIL'}", f"Local Qwen          {'READY' if c['pi_models'] else 'FAIL'}", f"Runtime             {'READY' if c['runtime'] else 'FAIL'}", worker)))
    def install(self):
        hp = helper_path()
        if hp is None or not hp.exists(): messagebox.showerror("AgentCommander", "請從 Portable 版本執行整合安裝。")
        else:
            try: install_codex(hp); messagebox.showinfo("AgentCommander", "Codex 整合已安裝。")
            except Exception as exc: messagebox.showerror("AgentCommander", str(exc))
        self.refresh()
    def remove(self): remove_codex(); self.refresh()
    def models(self): ModelWindow(self)
    def doctor(self):
        data = run_doctor(); messagebox.showinfo("Doctor", "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in data["checks"].items()))
    def open_runtime(self): local_data_root().mkdir(parents=True, exist_ok=True); os.startfile(local_data_root())

class ModelWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent); self.parent = parent; self.title("Worker Models"); self.geometry("760x360"); self.models = discover_pi_models(); self.profile = tk.StringVar(); self.model = tk.StringVar(); self.tree = ttk.Treeview(self, columns=("profile", "model", "state", "default"), show="headings")
        for col in self.tree["columns"]: self.tree.heading(col, text=col.title())
        self.tree.pack(fill="both", expand=True, padx=10, pady=10); form = ttk.Frame(self); form.pack(fill="x", padx=10); ttk.Entry(form, textvariable=self.profile, width=20).pack(side="left"); ttk.Combobox(form, textvariable=self.model, values=self.models, width=55).pack(side="left", padx=5); ttk.Button(form, text="Save / Default", command=self.save).pack(side="left"); ttk.Button(form, text="Delete", command=self.delete).pack(side="left", padx=4); self.reload()
    def reload(self):
        self.tree.delete(*self.tree.get_children()); config = load_config()
        for name, item in sorted(config["models"].items()): self.tree.insert("", "end", values=(name, item["pi_model"], "READY" if item["pi_model"] in self.models else "MISSING", "DEFAULT" if name == config["worker"].get("default_profile") else ""))
    def save(self):
        if not self.profile.get().strip() or not self.model.get(): return
        config = load_config(); name = self.profile.get().strip(); config["models"][name] = {"pi_model": self.model.get()}; config["worker"]["default_profile"] = name; save_config(config); self.reload(); self.parent.refresh()
    def delete(self):
        selected = self.tree.selection()
        if not selected: return
        name = self.tree.item(selected[0])["values"][0]; config = load_config(); config["models"].pop(name, None)
        if config["worker"].get("default_profile") == name: config["worker"]["default_profile"] = None
        save_config(config); self.reload(); self.parent.refresh()

def main(): App().mainloop()
