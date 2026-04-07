"""批量重命名核心逻辑：规则应用、冲突检测、安全改名与会话内撤销。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path


WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# 一键预设：(id, 界面标题, 说明)。具体替换逻辑在 apply_text_stage 中，用户无需了解正则。
PRESET_CHOICES: list[tuple[str, str, str]] = [
    ("none", "不额外整理", "保持文件名原样，仅应用「加前后缀 / 序号」等设置。"),
    (
        "spaces_to_underscore",
        "空格改成下划线",
        "例：我的 假期 照片.jpg → 我的_假期_照片.jpg",
    ),
    (
        "spaces_to_dash",
        "空格改成短横线",
        "例：项目 终稿.pdf → 项目-终稿.pdf",
    ),
    (
        "merge_spaces",
        "合并多个空格",
        "例：我的成绩  26年.jpg → 我的成绩 26年.jpg",
    ),
    (
        "merge_underscore",
        "合并多个下划线",
        "例：报告__初稿___v2.docx → 报告_初稿_v2.docx",
    ),
    (
        "remove_bracket_number",
        "去掉末尾(n)",
        "去掉 Windows 同名文件常见的(1)后缀，扩展名不变。",
    ),
    (
        "remove_leading_digits",
        "去掉开头的数字和下划线/横线",
        "例：001_说明.txt → 说明.txt；012-图.png → 图.png",
    ),
    ("ext_lower", "扩展名改成小写", "例：照片.JPG → 照片.jpg"),
    ("name_lower", "整名改成小写", "主文件名与扩展名都改为小写。"),
    ("name_upper", "主文件名改成英文大写", "仅将主文件名中的字母变大写，扩展名保持原样。"),
    (
        "keep_safe_chars",
        "奇怪符号改成下划线",
        "只保留中文、英文、数字、点与短横线，其余替换为下划线并合并重复下划线。",
    ),
    (
        "custom",
        "文本替换",
        "把上面填的字全部换成下面的字。",
    ),
]


def preset_tip(mode_id: str) -> str:
    for pid, _title, tip in PRESET_CHOICES:
        if pid == mode_id:
            return tip
    return ""


@dataclass
class RenameRuleConfig:
    """界面收集的规则配置。"""

    # 替换模式 none不替换 custom自定义替换 spaces_to_dash空格变短横-
    replace_mode: str = "none"
    # 替换模式 custom 将被替换的文字
    find_text: str = ""
    # 替换模式 custom 替换文字
    replace_text: str = ""
    # 替换模式 是否区分大小写
    find_case_sensitive: bool = True
    # 文件名的前缀
    prefix: str = ""
    # 文件名的后缀
    suffix: str = ""
    # 是否启用批量编号
    use_number: bool = False
    # 序号的起始数字
    number_start: int = 1
    # 序号的步长
    number_step: int = 1
    # 序号的位数 自动补零
    number_width: int = 3
    # 序号添加在文件名前或后
    number_before_name: bool = True
    # 序号和文件之间的分隔符
    number_sep: str = "_"


@dataclass
class PlannedRename:
    # Path是Python的一种对象类型 路径对象
    old_path: Path
    new_path: Path
    error: str | None = None

# 判断文件名是否合规
def is_valid_windows_filename(name: str) -> str | None:
    """
    :param name: 文件名
    若非法则返回错误说明，否则 None。
    """
    if not name or name.strip() != name:
        return "文件名不能为空或首尾含空白"
    if name in {".", ".."}:
        return "不能使用 . 或 .. 作为文件名"
    if WIN_INVALID.search(name):
        return "包含 Windows 不允许的字符: \\ / : * ? \" < > |"
    return None

# 重命名的各种操作方法
def apply_text_stage(name: str, cfg: RenameRuleConfig) -> str:
    """
    :param name: 文件名
    :param cfg: RenameRuleConfig 重命名的规则
    在拆分序号/前后缀之前，对文件名做「一键预设」或「自定义替换」。
    预设内部可使用正则；对界面只展示 PRESET_CHOICES 中的说明文字。
    """
    mode = (cfg.replace_mode or "none").strip() or "none"

    if mode == "custom":
        if not cfg.find_text:
            return name
        if cfg.find_case_sensitive:
            return name.replace(cfg.find_text, cfg.replace_text)
        return re.sub(re.escape(cfg.find_text), cfg.replace_text, name, flags=re.IGNORECASE)

    if mode == "none":
        return name

    stem, ext = Path(name).stem, Path(name).suffix

    if mode == "spaces_to_underscore":
        stem = re.sub(r"\s+", "_", stem)
    elif mode == "spaces_to_dash":
        stem = re.sub(r"\s+", "-", stem)
    elif mode == "merge_spaces":
        stem = re.sub(r" {2,}", " ", stem).strip()
    elif mode == "merge_underscore":
        stem = re.sub(r"_+", "_", stem).strip("_")
    elif mode == "remove_bracket_number":
        stem = re.sub(r"\s*\(\d+\)$", "", stem)
    elif mode == "remove_leading_digits":
        stem = re.sub(r"^[\d_\-\s]+", "", stem)
    elif mode == "ext_lower":
        ext = ext.lower()
    elif mode == "name_lower":
        stem = stem.lower()
        ext = ext.lower()
    elif mode == "name_upper":
        stem = stem.upper()
    elif mode == "keep_safe_chars":
        stem = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", stem, flags=re.UNICODE)
        stem = re.sub(r"_+", "_", stem).strip("_")
    else:
        return name

    return stem + ext


def apply_rules_to_filename(original: str, cfg: RenameRuleConfig) -> str:
    """对单个文件名（含扩展名）应用规则，返回新文件名。"""
    name = apply_text_stage(original, cfg)

    stem, ext = Path(name).stem, Path(name).suffix
    if cfg.use_number:
        # 序号在多次刷新时会按当前列表顺序重算，由调用方传入序号
        raise ValueError("带序号时请调用 apply_rules_with_index")

    if cfg.prefix:
        stem = cfg.prefix + stem
    if cfg.suffix:
        stem = stem + cfg.suffix
    return stem + ext


def apply_rules_with_index(original: str, cfg: RenameRuleConfig, index: int) -> str:
    """index 为从 0 开始的序号，用于计算实际数字。"""
    name = apply_text_stage(original, cfg)

    stem, ext = Path(name).stem, Path(name).suffix
    n = cfg.number_start + index * cfg.number_step
    num_str = str(n).zfill(max(1, cfg.number_width))
    if cfg.use_number:
        sep = cfg.number_sep
        if cfg.number_before_name:
            stem = f"{num_str}{sep}{stem}" if sep else f"{num_str}{stem}"
        else:
            stem = f"{stem}{sep}{num_str}" if sep else f"{stem}{num_str}"
    if cfg.prefix:
        stem = cfg.prefix + stem
    if cfg.suffix:
        stem = stem + cfg.suffix
    return stem + ext


def collect_files(root: Path, recursive: bool, extensions: str | None) -> list[Path]:
    """
    extensions: 例如 ".jpg;.png" 或 ".jpg" ，空表示全部文件。
    仅包含文件，不包含目录。
    """
    root = root.resolve()
    if not root.is_dir():
        return []

    exts: set[str] = set()
    if extensions and extensions.strip():
        for part in re.split(r"[;,，\s]+", extensions.strip()):
            p = part.strip().lower()
            if not p:
                continue
            if not p.startswith("."):
                p = "." + p
            exts.add(p)

    out: list[Path] = []
    if recursive:
        for p in root.rglob("*"):
            if p.is_file():
                if not exts or p.suffix.lower() in exts:
                    out.append(p)
    else:
        for p in root.iterdir():
            if p.is_file():
                if not exts or p.suffix.lower() in exts:
                    out.append(p)
    out.sort(key=lambda x: str(x).lower())
    return out


def build_plan(files: list[Path], cfg: RenameRuleConfig) -> list[PlannedRename]:
    """生成预览计划；冲突与非法名写入 PlannedRename.error。"""
    plans: list[PlannedRename] = []
    new_names: list[str] = []

    for i, path in enumerate(files):
        old_name = path.name
        try:
            if cfg.use_number:
                new_name = apply_rules_with_index(old_name, cfg, i)
            else:
                new_name = apply_rules_to_filename(old_name, cfg)
        except Exception as e:  # noqa: BLE001
            plans.append(PlannedRename(path, path, error=f"规则错误: {e}"))
            new_names.append("")
            continue

        err = is_valid_windows_filename(new_name)
        new_path = path.with_name(new_name)
        if err:
            plans.append(PlannedRename(path, new_path, error=err))
        else:
            plans.append(PlannedRename(path, new_path, error=None))
        new_names.append(new_name)

    # 目标重名（批内互撞）
    target_count: dict[str, int] = {}
    for p in plans:
        if p.error:
            continue
        key = p.new_path.resolve().as_posix()
        target_count[key] = target_count.get(key, 0) + 1

    for p in plans:
        if p.error:
            continue
        key = p.new_path.resolve().as_posix()
        if target_count.get(key, 0) > 1:
            p.error = "与其他行目标文件名相同"

    # 与磁盘冲突：目标已存在且不是本批中的某个“源路径”
    old_resolved = {f.resolve() for f in files}
    for p in plans:
        if p.error:
            continue
        if p.old_path.resolve() == p.new_path.resolve():
            continue
        if p.new_path.exists():
            nr = p.new_path.resolve()
            if nr not in old_resolved:
                p.error = "目标位置已存在其他文件"
            # 若存在且是批内某一旧路径，允许（由两阶段改名处理）

    return plans


def _two_phase_rename(pairs: list[tuple[Path, Path]]) -> None:
    """pairs: (旧路径, 新路径)。使用临时名避免互斥覆盖。"""
    work = [(o, n) for o, n in pairs if o.resolve() != n.resolve()]
    if not work:
        return
    temps: list[tuple[Path, Path, Path]] = []
    for old, new in work:
        tmp = old.parent / f".rn_tmp_{uuid.uuid4().hex}"
        old.rename(tmp)
        temps.append((tmp, new, old))
    for tmp, new, _old in temps:
        tmp.rename(new)


def execute_plan(plans: list[PlannedRename]) -> tuple[list[tuple[Path, Path]], list[str]]:
    """
    执行无 error 的计划项。
    返回 (成功项列表 (改名前完整路径, 改名后完整路径), 失败信息列表)。
    """
    ok_plans = [p for p in plans if not p.error and p.old_path.resolve() != p.new_path.resolve()]
    if not ok_plans:
        return [], []

    pairs = [(p.old_path, p.new_path) for p in ok_plans]
    try:
        _two_phase_rename(pairs)
    except OSError as e:
        return [], [str(e)]

    success = [(p.old_path, p.new_path) for p in ok_plans]
    return success, []


@dataclass
class UndoSession:
    """仅内存：记录每一批成功改名的 (旧路径, 新路径)，用于撤销。"""

    batches: list[list[tuple[Path, Path]]] = field(default_factory=list)

    def push_batch(self, batch: list[tuple[Path, Path]]) -> None:
        if batch:
            self.batches.append(batch)

    def can_undo(self) -> bool:
        return bool(self.batches)

    def undo_last(self) -> tuple[bool, str]:
        """
        撤销上一批：将新路径改回旧路径。
        返回 (是否成功, 消息)。
        """
        if not self.batches:
            return False, "没有可撤销的操作"
        batch = self.batches.pop()
        reverse_pairs: list[tuple[Path, Path]] = []
        for old, new in batch:
            if not new.exists():
                self.batches.append(batch)
                return False, f"无法撤销：文件已不存在\n{new}"
            reverse_pairs.append((new, old))
        try:
            _two_phase_rename(reverse_pairs)
        except OSError as e:
            self.batches.append(batch)
            return False, f"撤销失败: {e}"
        return True, f"已撤销 {len(batch)} 个文件"
