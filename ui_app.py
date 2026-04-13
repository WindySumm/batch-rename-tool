"""Tkinter 界面：分区布局、强化预览、一键预设（底层规则不暴露）、拖放支持、拖动排序。"""

from __future__ import annotations

from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from rename_core import (
    PRESET_CHOICES,
    RenameRuleConfig,
    UndoSession,
    build_plan,
    collect_files,
    execute_plan,
    preset_tip,
)


class RenameApp(ttk.Frame):
    def __init__(self, master: TkinterDnD.Tk) -> None:
        super().__init__(master, padding=8)
        self.master.title("批量重命名")
        self.master.iconbitmap("D:/Codes/batch-rename-tool/Re_icon.ico")
        self.master.minsize(810, 560)
        self.pack(fill=tk.BOTH, expand=True)

        self._folder = tk.StringVar(value="")
        self._recursive = tk.BooleanVar(value=False)
        self._ext_filter = tk.StringVar(value="")

        self._preset_titles = [t for _id, t, _tip in PRESET_CHOICES]
        self._preset_ids = [pid for pid, _t, _tip in PRESET_CHOICES]
        self._preset_combo_val = tk.StringVar(value=self._preset_titles[0])

        self._find = tk.StringVar(value="")
        self._replace = tk.StringVar(value="")
        self._find_case = tk.BooleanVar(value=True)

        self._prefix = tk.StringVar(value="")
        self._suffix = tk.StringVar(value="")

        self._use_number = tk.BooleanVar(value=False)
        self._num_start = tk.StringVar(value="1")
        self._num_step = tk.StringVar(value="1")
        self._num_width = tk.StringVar(value="3")
        self._num_before = tk.BooleanVar(value=True)
        self._num_sep = tk.StringVar(value="_")

        self._undo = UndoSession()
        self._files: list = []
        self._plans: list = []

        self._drag_item = None

        self._build()
        self._on_preset_change()
        self._status("请先选择文件夹或拖放文件/文件夹；改名的结果会实时显示在上方预览区。")

    def _current_preset_id(self) -> str:
        title = self._preset_combo_val.get()
        try:
            i = self._preset_titles.index(title)
            return self._preset_ids[i]
        except ValueError:
            return "none"

    def _on_preset_change(self, *_args: object) -> None:
        pid = self._current_preset_id()
        self._preset_tip.set(preset_tip(pid))
        if pid == "custom":
            self._custom_frame.pack(fill=tk.X, pady=(0, 6))
            self._schedule_scroll_advanced_into_view()
        else:
            self._custom_frame.pack_forget()
        self._preview()

    def _schedule_scroll_advanced_into_view(self) -> None:
        """布局与 scrollregion 往往晚于 pack 一步，多次延后执行以提高成功率。"""
        self.after_idle(self._scroll_advanced_panel_into_view)
        for ms in (24, 72, 160, 320):
            self.after(ms, self._scroll_advanced_panel_into_view)

    def _y_of_widget_below_inner_top(self) -> float | None:
        """自定义区相对 _upper_inner 顶部的 Y 偏移。优先屏幕坐标差；异常或明显不合理时用父链累加 winfo_y。"""
        y_root: float | None = None
        try:
            y_root = float(self._custom_frame.winfo_rooty() - self._upper_inner.winfo_rooty())
        except tk.TclError:
            pass

        y_chain = 0.0
        w: tk.Misc | None = self._custom_frame
        chain_ok = True
        while w is not None and w != self._upper_inner:
            try:
                y_chain += float(w.winfo_y())
            except tk.TclError:
                chain_ok = False
                break
            w = w.master
        if w is None:
            chain_ok = False

        if y_root is not None and y_root >= -5:
            return max(0.0, y_root)
        if chain_ok:
            return max(0.0, y_chain)
        return None

    def _scroll_advanced_panel_into_view(self) -> None:
        """展开「高级替换」后，把上方滚动区滚到能看见该区域（小窗口时有用）。"""
        if not self._custom_frame.winfo_ismapped():
            return
        self.update_idletasks()

        total_h = float(self._upper_inner.winfo_reqheight())
        bbox = self._upper_canvas.bbox("all")
        if bbox:
            bh = float(bbox[3] - bbox[1])
            total_h = max(total_h, bh)
        if total_h <= 1.0:
            return

        view_h = float(self._upper_canvas.winfo_height())
        if view_h <= 1.0 or total_h <= view_h:
            return

        y_rel = self._y_of_widget_below_inner_top()
        if y_rel is None:
            return

        pad = 10.0
        frac = max(0.0, (y_rel - pad) / total_h)
        max_frac = max(0.0, 1.0 - view_h / total_h)
        self._upper_canvas.yview_moveto(min(frac, max_frac))

    def _build(self) -> None:
        outer = ttk.Panedwindow(self, orient=tk.VERTICAL)
        outer.pack(fill=tk.BOTH, expand=True)

        upper = ttk.Frame(outer, padding=(0, 0, 0, 4))
        lower = ttk.Frame(outer, padding=(0, 4, 0, 0))
        outer.add(upper, weight=0)
        outer.add(lower, weight=1)

        scroll_wrap = ttk.Frame(upper)
        scroll_wrap.pack(fill=tk.BOTH, expand=True)

        try:
            _canvas_bg = self.master.cget("bg")
        except tk.TclError:
            _canvas_bg = ttk.Style().lookup("TFrame", "background", default="#f0f0f0")

        self._upper_canvas = tk.Canvas(
            scroll_wrap, highlightthickness=0, borderwidth=0, background=_canvas_bg
        )
        upper_vsb = ttk.Scrollbar(scroll_wrap, orient=tk.VERTICAL, command=self._upper_canvas.yview)
        self._upper_canvas.configure(yscrollcommand=upper_vsb.set)
        upper_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._upper_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._upper_inner = ttk.Frame(self._upper_canvas, padding=0)
        self._upper_canvas_window = self._upper_canvas.create_window((0, 0), window=self._upper_inner, anchor="nw")

        def _upper_on_inner_configure(_event: tk.Event) -> None:
            self._upper_canvas.configure(scrollregion=self._upper_canvas.bbox("all"))

        def _upper_on_canvas_configure(event: tk.Event) -> None:
            self._upper_canvas.itemconfigure(self._upper_canvas_window, width=event.width)

        self._upper_inner.bind("<Configure>", _upper_on_inner_configure)
        self._upper_canvas.bind("<Configure>", _upper_on_canvas_configure)

        def _upper_wheel(event: tk.Event) -> None:
            self._upper_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self._upper_canvas.bind("<MouseWheel>", _upper_wheel)

        pick = ttk.LabelFrame(self._upper_inner, text="① 选择要改名的文件", padding=6)
        pick.pack(fill=tk.X, pady=(0, 6))
        row1 = ttk.Frame(pick)
        row1.pack(fill=tk.X)
        ttk.Entry(row1, textvariable=self._folder, width=55, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(row1, text="选择文件夹", command=self._pick_folder).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(
            pick, text="包含里面的子文件夹", variable=self._recursive, command=self._refresh
        ).pack(anchor=tk.W, pady=(4, 0))
        row2 = ttk.Frame(pick)
        row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row2, text="只处理这些扩展名（例如: jpg png(可多选 用空格分隔); 默认全部）:").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self._ext_filter, width=36).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(row2, text="重新读取文件列表", command=self._refresh).pack(side=tk.LEFT, padx=(8, 0))

        nb = ttk.Notebook(self._upper_inner)
        nb.pack(fill=tk.BOTH, expand=True)

        tab_a = ttk.Frame(nb, padding=6)
        nb.add(tab_a, text="一键整理名字")
        ttk.Label(
            tab_a,
            text="可与「加前后缀 / 序号」一起用。",
            wraplength=680,
        ).pack(anchor=tk.W)
        combo_row = ttk.Frame(tab_a)
        combo_row.pack(fill=tk.X, pady=(8, 4))
        ttk.Label(combo_row, text="选择方式：").pack(side=tk.LEFT)
        self._preset_combo = ttk.Combobox(
            combo_row,
            textvariable=self._preset_combo_val,
            values=self._preset_titles,
            state="readonly",
            width=42,
        )
        self._preset_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._preset_combo.bind("<<ComboboxSelected>>", self._on_preset_change)

        self._preset_tip = tk.StringVar(value="")
        self._tip_label = ttk.Label(tab_a, textvariable=self._preset_tip, wraplength=680, foreground="#444")
        self._tip_label.pack(anchor=tk.W, pady=(4, 8))

        self._custom_frame = ttk.LabelFrame(
            tab_a,
            text="文本替换",
            padding=6,
        )
        self._custom_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(self._custom_frame, text="把这段文字：").grid(row=0, column=0, sticky=tk.W, pady=2)
        self._find_entry = ttk.Entry(self._custom_frame, textvariable=self._find, width=40)
        self._find_entry.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0), pady=2)
        ttk.Label(self._custom_frame, text="全部换成：").grid(row=1, column=0, sticky=tk.W, pady=2)
        self._replace_entry = ttk.Entry(self._custom_frame, textvariable=self._replace, width=40)
        self._replace_entry.grid(row=1, column=1, sticky=tk.EW, padx=(6, 0), pady=2)
        self._find_case_cb = ttk.Checkbutton(
            self._custom_frame, text="区分英文字母大小写", variable=self._find_case, command=self._preview
        )
        self._find_case_cb.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        self._custom_frame.bind("<Map>", lambda _e: self._schedule_scroll_advanced_into_view(), add="+")

        tab_b = ttk.Frame(nb, padding=6)
        nb.add(tab_b, text="加前后缀 / 序号")
        ttk.Label(
            tab_b,
            text="在「主文件名」（小数点前面那一段）前面或后面加字；也可以按顺序自动编号。",
            wraplength=680,
        ).pack(anchor=tk.W)
        r2 = ttk.Frame(tab_b)
        r2.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(r2, text="在前面加字：").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self._prefix, width=18).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(r2, text="在后面加字（仍在扩展名前面）：").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self._suffix, width=18).pack(side=tk.LEFT, padx=(4, 0))

        r3 = ttk.Frame(tab_b)
        r3.pack(fill=tk.X, pady=(10, 0))
        ttk.Checkbutton(r3, text="自动加序号", variable=self._use_number, command=self._preview).pack(side=tk.LEFT)
        ttk.Label(r3, text="从").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(r3, textvariable=self._num_start, width=5).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(r3, text="开始，每次加").pack(side=tk.LEFT)
        ttk.Entry(r3, textvariable=self._num_step, width=5).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(r3, text="位数").pack(side=tk.LEFT)
        ttk.Entry(r3, textvariable=self._num_width, width=5).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(r3, text="中间分隔符").pack(side=tk.LEFT)
        ttk.Entry(r3, textvariable=self._num_sep, width=4).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Radiobutton(r3, text="序号在名前", variable=self._num_before, value=True, command=self._preview).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Radiobutton(r3, text="序号在名后", variable=self._num_before, value=False, command=self._preview).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        for v in (
            self._find,
            self._replace,
            self._prefix,
            self._suffix,
            self._num_start,
            self._num_step,
            self._num_width,
            self._num_sep,
        ):
            v.trace_add("write", lambda *_: self._preview())

        for w in self._iter_descendants(self._upper_inner):
            w.bind("<MouseWheel>", _upper_wheel, add="+")

        preview_box = ttk.LabelFrame(lower, text="② 改完后的文件名预览（请在这里核对，再点「执行重命名」）\n💡 提示：可拖放文件/文件夹到此处，也可拖动行调整顺序", padding=6)
        preview_box.pack(fill=tk.BOTH, expand=True)

        self._preview_hint = tk.StringVar(value="")
        ttk.Label(preview_box, textvariable=self._preview_hint, wraplength=720).pack(anchor=tk.W, pady=(0, 6))

        mid = ttk.Frame(preview_box)
        mid.pack(fill=tk.BOTH, expand=True)
        cols = ("num", "old", "new", "msg")
        self._tree = ttk.Treeview(mid, columns=cols, show="headings", height=16, selectmode="browse")
        self._tree.heading("num", text="序号")
        self._tree.heading("old", text="现在的名字")
        self._tree.heading("new", text="改完之后会变成 ↓")
        self._tree.heading("msg", text="提示")
        self._tree.column("num", width=50, stretch=False)
        self._tree.column("old", width=240, stretch=True)
        self._tree.column("new", width=240, stretch=True)
        self._tree.column("msg", width=180, stretch=True)

        self._tree.tag_configure("change", foreground="#0b6e0b")
        self._tree.tag_configure("same", foreground="#666666")
        self._tree.tag_configure("err", foreground="#b00020")

        sy = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sy.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

        bot = ttk.Frame(lower)
        bot.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bot, text="刷新预览", command=self._preview).pack(side=tk.LEFT)
        ttk.Button(bot, text="执行重命名", command=self._apply).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bot, text="撤销上一次操作（仅本次打开程序有效）", command=self._undo_last).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self._stat = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._stat, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, pady=(8, 0))

        preview_box.drop_target_register(DND_FILES)
        preview_box.dnd_bind("<<Drop>>", self._on_drop)
        self._tree.drop_target_register(DND_FILES)
        self._tree.dnd_bind("<<Drop>>", self._on_drop)

        self._tree.bind("<ButtonPress-1>", self._on_tree_click)
        self._tree.bind("<B1-Motion>", self._on_tree_drag)
        self._tree.bind("<ButtonRelease-1>", self._on_tree_release)

    @staticmethod
    def _iter_descendants(widget: tk.Misc):
        yield widget
        for c in widget.winfo_children():
            yield from RenameApp._iter_descendants(c)

    def _status(self, text: str) -> None:
        self._stat.set(text)

    def _on_drop(self, event: tk.Event) -> None:
        paths = self._parse_drop_paths(event.data)
        if not paths:
            return

        files: list[Path] = []
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                files.extend(collect_files(path_obj, self._recursive.get(), self._ext_filter.get()))
            elif path_obj.is_file():
                files.append(path_obj)

        if files:
            self._files = list(dict.fromkeys(self._files + files))
            if paths[0]:
                first_path = Path(paths[0])
                if first_path.is_dir():
                    self._folder.set(str(first_path))
                else:
                    self._folder.set(str(first_path.parent))
            self._preview()
            self._status(f"已添加 {len(files)} 个文件")

    def _parse_drop_paths(self, data: str) -> list[str]:
        if not data:
            return []
        if data.startswith("{") and data.endswith("}"):
            data = data[1:-1]
        paths = []
        current = []
        in_quote = False
        for c in data:
            if c == "{":
                in_quote = True
            elif c == "}":
                in_quote = False
                if current:
                    paths.append("".join(current))
                    current = []
            elif c == " " and not in_quote:
                if current:
                    paths.append("".join(current))
                    current = []
            else:
                current.append(c)
        if current:
            paths.append("".join(current))
        return paths

    def _on_tree_click(self, event: tk.Event) -> None:
        item = self._tree.identify_row(event.y)
        if item:
            self._drag_item = item

    def _on_tree_drag(self, event: tk.Event) -> None:
        if self._drag_item is None:
            return
        target_item = self._tree.identify_row(event.y)
        if target_item and target_item != self._drag_item:
            self._tree.move(self._drag_item, "", self._tree.index(target_item))

    def _on_tree_release(self, event: tk.Event) -> None:
        if self._drag_item is not None:
            self._reorder_files_from_tree()
            self._preview()
        self._drag_item = None

    def _reorder_files_from_tree(self) -> None:
        items = self._tree.get_children("")
        new_files = []
        old_paths = {p.resolve(): p for p in self._files}
        for item in items:
            old_name = self._tree.item(item, "values")[1]
            for p in self._files:
                if p.name == old_name:
                    new_files.append(p)
                    break
        self._files = new_files

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory(title="选择要处理的文件夹")
        if path:
            self._folder.set(path)
            self._refresh()

    def _read_cfg(self) -> RenameRuleConfig | None:
        try:
            start = int(self._num_start.get().strip() or "1")
            step = int(self._num_step.get().strip() or "1")
            width = int(self._num_width.get().strip() or "1")
        except ValueError:
            messagebox.showerror("输入错误", "序号里的「从几开始」「每次加几」「几位数」必须是整数。")
            return None
        if width < 1:
            width = 1
        return RenameRuleConfig(
            replace_mode=self._current_preset_id(),
            find_text=self._find.get(),
            replace_text=self._replace.get(),
            find_case_sensitive=self._find_case.get(),
            prefix=self._prefix.get(),
            suffix=self._suffix.get(),
            use_number=self._use_number.get(),
            number_start=start,
            number_step=step,
            number_width=width,
            number_before_name=self._num_before.get(),
            number_sep=self._num_sep.get(),
        )

    def _refresh(self) -> None:
        folder = self._folder.get().strip()
        if not folder:
            self._files = []
            self._plans = []
            self._fill_tree()
            self._status("请先选择文件夹")
            self._preview_hint.set("")
            return
        root = Path(folder)
        self._files = collect_files(root, self._recursive.get(), self._ext_filter.get())
        self._preview()

    def _preview(self) -> None:
        if not self._files:
            self._plans = []
            self._fill_tree()
            self._status("还没有文件：请选择文件夹，或检查扩展名过滤是否过严。")
            self._preview_hint.set("暂无文件可预览。")
            return
        cfg = self._read_cfg()
        if cfg is None:
            return
        self._plans = build_plan(self._files, cfg)
        self._fill_tree()
        err = sum(1 for p in self._plans if p.error)
        ok = len(self._plans) - err
        will_change = sum(
            1
            for p in self._plans
            if not p.error and p.old_path.resolve() != p.new_path.resolve()
        )
        same = len(self._plans) - will_change - err
        self._preview_hint.set(
            f"共 {len(self._plans)} 个文件：其中约 {will_change} 个名字会变（绿色），"
            f"{same} 个与现在相同（灰色）；若有红色提示则该行不能执行，请先调整规则。"
        )
        self._status(f"预览已更新 · 可执行 {ok} 条 · 有问题 {err} 条 · 将实际改名约 {will_change} 个")

    def _fill_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, p in enumerate(self._plans, 1):
            msg = p.error or ("与现在相同" if p.old_path.resolve() == p.new_path.resolve() else "将改名")
            if p.error:
                tag = "err"
            elif p.old_path.resolve() == p.new_path.resolve():
                tag = "same"
            else:
                tag = "change"
            self._tree.insert("", tk.END, values=(i, p.old_path.name, p.new_path.name, msg), tags=(tag,))

    def _apply(self) -> None:
        cfg = self._read_cfg()
        if cfg is None:
            return
        if not self._folder.get().strip() and not self._files:
            messagebox.showwarning("提示", "请先选择文件夹或拖放文件。")
            return
        self._plans = build_plan(self._files, cfg)
        self._fill_tree()
        bad = [p for p in self._plans if p.error]
        if bad:
            messagebox.showerror(
                "无法执行",
                f"有 {len(bad)} 行存在问题（见预览里红色提示）。请改好规则后再试。",
            )
            self._status("存在错误，未执行")
            return
        ok_count = sum(
            1 for p in self._plans if not p.error and p.old_path.resolve() != p.new_path.resolve()
        )
        if ok_count == 0:
            messagebox.showinfo("提示", "没有需要改名的文件：预览里全都是「与现在相同」。")
            return
        if not messagebox.askyesno("最后确认", f"确定把 {ok_count} 个文件改成预览里的新名字吗？\n（本次运行内可以撤销上一批）"):
            return
        success, errors = execute_plan(self._plans)
        if errors:
            messagebox.showerror("重命名失败", "\n".join(errors))
            self._status("执行失败")
            self._refresh()
            return
        self._undo.push_batch(success)
        messagebox.showinfo("完成", f"已成功改名 {len(success)} 个文件。")
        self._refresh()

    def _undo_last(self) -> None:
        ok, msg = self._undo.undo_last()
        if ok:
            messagebox.showinfo("撤销", msg)
            self._refresh()
        else:
            messagebox.showerror("撤销", msg)


def run() -> None:
    root = TkinterDnD.Tk()
    RenameApp(root)
    root.mainloop()
