#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcu_dbg.py -- cl skill 的统一调试 CLI（探针直读内存 + 寄存器 + 变量）。

来源：stm32debug-skill 的 stm32_debug.py + stm32_monitor.py，
合并重叠代码后移植为 Windows 原生统一版。
依赖：pyelftools（已装）、arm-none-eabi-gdb（env GDB）、openocd（env OPENOCD_BIN）。

用法：
    python mcu_dbg.py start --elf <.axf> --svd <.svd> --target stm32f4x
    python mcu_dbg.py read counter voltage GPIOA.ODR --type uint
    python mcu_dbg.py poll counter --interval 1 --count 10
    python mcu_dbg.py cycle counter --flash --wait 10
    python mcu_dbg.py swo --freq 216000000
    python mcu_dbg.py break main.c:145
    python mcu_dbg.py step 3
    python mcu_dbg.py backtrace
    python mcu_dbg.py history --limit 20
    python mcu_dbg.py reset / halt / resume / stop

退出码 0=成功；非 0=失败（结构化 FAILURE:<category>）。
"""

import argparse, atexit, ctypes, json, os, re, shlex, signal
import socket, struct, subprocess, sys, tempfile, time
from datetime import datetime
from pathlib import Path

kernel32 = ctypes.windll.kernel32

# ═══════════════════════════════════════════════════════════════════════
#  工具定位
# ═══════════════════════════════════════════════════════════════════════

OPENOCD_SEARCH = [
    os.path.expandvars(r"%APPDATA%\xPacks\@xpack-dev-tools\openocd"),
    r"C:\Program Files\OpenOCD",
]
GDB_SEARCH = [
    os.path.expandvars(r"%APPDATA%\xPacks\@xpack-dev-tools\arm-none-eabi-gcc"),
]


def _walk(root, name, depth=4):
    hits, base = [], len(root.split(os.sep))
    for d, _, fns in os.walk(root):
        if len(d.split(os.sep)) - base >= depth:
            break
        if name in fns:
            hits.append(os.path.join(d, name))
    return hits


def find_openocd():
    for key in ("OPENOCD",):
        v = os.environ.get(key)
        if v and os.path.isfile(v):
            return v
    v2 = os.environ.get("OPENOCD_BIN")
    if v2:
        c = os.path.join(v2, "openocd.exe")
        if os.path.isfile(c):
            return c
        if os.path.isfile(v2):
            return v2
    for d in OPENOCD_SEARCH:
        if os.path.isdir(d):
            h = _walk(d, "openocd.exe")
            if h:
                return h[-1]
    from shutil import which
    return which("openocd") or "openocd"


def find_gdb():
    v = os.environ.get("GDB")
    if v and os.path.isfile(v):
        return v
    for d in GDB_SEARCH:
        if os.path.isdir(d):
            h = _walk(d, "arm-none-eabi-gdb.exe")
            if h:
                return h[-1]
    from shutil import which
    for n in ("arm-none-eabi-gdb", "gdb-multiarch"):
        w = which(n)
        if w:
            return w
    return "arm-none-eabi-gdb"


# ═══════════════════════════════════════════════════════════════════════
#  Windows 进程管理
# ═══════════════════════════════════════════════════════════════════════

def is_pid_alive(pid):
    if not pid:
        return False
    try:
        h = kernel32.OpenProcess(0x1000, False, pid)
        if h:
            kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def terminate_pid(pid):
    if not pid:
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, timeout=15)


def kill_stale_openocd():
    n = 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq openocd.exe",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        for m in re.finditer(r'"openocd\.exe","(\d+)"', out):
            subprocess.run(["taskkill", "/F", "/PID", m.group(1)],
                           capture_output=True, timeout=15)
            n += 1
    except Exception:
        pass
    return n


# ═══════════════════════════════════════════════════════════════════════
#  路径 / 状态持久化
# ═══════════════════════════════════════════════════════════════════════

def _state_dir():
    d = Path.cwd() / ".cl" / "dbg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rotate(path, max_bytes=1_000_000, keep=4):
    """多档日志轮转：超过 max_bytes 时把当前文件归档为 <名>.1，旧档 <名>.1→<名>.2→…→<名>.keep，
    最旧档丢弃，当前文件清空重写。用日志分文件，避免单文件超 git 提交尺寸（默认 4 档@1MB）。"""
    try:
        if path.exists() and path.stat().st_size <= max_bytes:
            return
        for i in range(keep - 1, 0, -1):
            old = path.with_name("%s.%d" % (path.name, i))
            nxt = path.with_name("%s.%d" % (path.name, i + 1))
            if nxt.exists():
                try:
                    nxt.unlink()
                except OSError:
                    pass
            if old.exists():
                try:
                    old.rename(nxt)
                except OSError:
                    pass
        cur = path.with_name(path.name + ".1")
        try:
            cur.write_bytes(path.read_bytes())
        except OSError:
            pass
        path.write_bytes(b"")
    except OSError:
        pass


def _session():
    return _state_dir() / "session.json"


def _history():
    return _state_dir() / "history.jsonl"


def _openocd_log():
    return _state_dir() / "openocd.log"


def _swo_log():
    return _state_dir() / "swo.log"


DEFAULT_CTX = {
    "elf": None, "svd": None, "source_root": None,
    "build_cmd": None, "flash_elf": None,
    "openocd_cfg": None,
}


def load_state():
    default = {
        "ctx": dict(DEFAULT_CTX),
        "ocd": {"host": "127.0.0.1", "tcl": 6666, "gdb": 3333,
                "telnet": 4444, "pid": None, "managed": False},
        "bps": [], "wps": [], "next_bp": 1, "next_wp": 1,
    }
    p = _session()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in default.items():
                if k not in data:
                    data[k] = v
                elif isinstance(v, dict):
                    for kk, vv in v.items():
                        data[k].setdefault(kk, vv)
            return data
        except Exception:
            pass
    return default


def save_state(state):
    _session().write_text(json.dumps(state, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def merge_ctx(state, args):
    c = state["ctx"]
    for attr, key in [("elf", "elf"), ("svd", "svd"), ("source_root", "source_root"),
                      ("build_cmd", "build_cmd"), ("flash_elf", "flash_elf")]:
        v = getattr(args, attr, None)
        if v:
            c[key] = v
    if getattr(args, "target", None):
        c["openocd_cfg"] = (
            f"-f interface/stlink.cfg -f target/{args.target}.cfg")
    if getattr(args, "openocd_config", None):
        c["openocd_cfg"] = args.openocd_config
    if getattr(args, "extra_cfg", None):
        c["openocd_cfg"] = (c.get("openocd_cfg") or "") + " " + args.extra_cfg
    state["ocd"]["tcl"] = getattr(args, "tcl_port", 6666) or 6666
    state["ocd"]["gdb"] = getattr(args, "gdb_port", 3333) or 3333
    state["ocd"]["telnet"] = getattr(args, "telnet_port", 4444) or 4444


def require_ctx(state, key):
    v = state["ctx"].get(key)
    if not v:
        raise RuntimeError(f"Missing context '{key}'. Pass --{key} or run 'start' first.")
    return v
# ═══════════════════════════════════════════════════════════════════════
#  OpenOCD 管理（Windows 原生）
# ═══════════════════════════════════════════════════════════════════════

class OCManager:
    def __init__(self, state):
        o = state["ocd"]
        cfg = state["ctx"].get("openocd_cfg") or DEFAULT_OPENOCD_CONFIG
        self.host = o["host"]
        self.tcl = o["tcl"]
        self.cfg = cfg
        self.state = state

    def is_running(self):
        try:
            with socket.create_connection((self.host, self.tcl), timeout=0.5):
                return True
        except OSError:
            return False

    def start(self, retries=3):
        ocd = find_openocd()
        n = kill_stale_openocd()
        if n:
            print(f"[pre-clean] 杀 {n} 个残留 openocd")
        log = _openocd_log()
        _rotate(log)
        last_err = None
        for attempt in range(1, retries + 1):
            with open(log, "a", encoding="utf-8") as lf:
                p = subprocess.Popen(
                    [ocd] + shlex.split(self.cfg),
                    stdout=lf, stderr=subprocess.STDOUT,
                    creationflags=0x00000200)  # CREATE_NEW_PROCESS_GROUP
            deadline = time.time() + 8
            while time.time() < deadline:
                if self.is_running():
                    self.state["ocd"]["pid"] = p.pid
                    self.state["ocd"]["managed"] = True
                    save_state(self.state)
                    return f"OpenOCD started (pid={p.pid})"
                if p.poll() is not None:
                    last_err = f"OpenOCD exited unexpectedly (rc={p.poll()})"
                    break
                time.sleep(0.2)
            else:
                terminate_pid(p.pid)
                last_err = f"Timed out waiting for TCL port {self.tcl}"
            self.state["ocd"]["pid"] = None
            self.state["ocd"]["managed"] = False
            save_state(self.state)
            if attempt < retries:
                time.sleep(attempt)
        raise RuntimeError(last_err or "Failed to start OpenOCD.")

    def stop(self):
        o = self.state["ocd"]
        if o.get("managed") and o.get("pid"):
            terminate_pid(o["pid"])
        o["pid"] = None
        o["managed"] = False
        save_state(self.state)

    def ensure(self, auto=True):
        if self.state["ocd"].get("managed") and not is_pid_alive(
                self.state["ocd"].get("pid")):
            self.state["ocd"]["pid"] = None
            self.state["ocd"]["managed"] = False
            save_state(self.state)
        if self.is_running():
            return False
        if not auto:
            raise RuntimeError("OpenOCD not running. Run 'start' first.")
        self.start()
        return True


DEFAULT_OPENOCD_CONFIG = "interface/stlink.cfg"


def tcl_cmd(state, cmd):
    host = state["ocd"]["host"]
    port = state["ocd"]["tcl"]
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall((cmd + "\x1a").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\x1a" in chunk:
                break
    return data.decode("utf-8", errors="replace").strip().replace("\x1a", "")


def ocd_read(addr, width=32, count=1, state=None):
    cmd_map = {8: "mdb", 16: "mdh", 32: "mdw"}
    cmd = f"{cmd_map.get(width, 'mdw')} 0x{addr:08X} {count}"
    if state:
        return tcl_cmd(state, cmd)
    host, port = "127.0.0.1", 6666
    with socket.create_connection((host, port), timeout=2) as s:
        s.sendall((cmd + "\x1a").encode())
        data = b""
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
            if b"\x1a" in chunk:
                break
    return data.decode("utf-8", errors="replace").strip().replace("\x1a", "")


def parse_values(resp):
    if not resp:
        return []
    try:
        _, payload = resp.split(":", 1)
    except ValueError:
        return []
    vals = []
    for tok in payload.split():
        try:
            vals.append(int(tok, 16))
        except ValueError:
            pass
    return vals


def select_width(size_bytes):
    if size_bytes <= 1:
        return 8
    if size_bytes <= 2:
        return 16
    return 32


def fmt_value(raw, vtype, width):
    if vtype == "float" and width == 32:
        return f"{struct.unpack('f', struct.pack('I', raw & 0xFFFFFFFF))[0]:.6f}"
    if vtype == "int":
        mask = (1 << width) - 1
        raw &= mask
        if raw & (1 << (width - 1)):
            raw -= (1 << width)
        return str(raw)
    if vtype == "uint":
        return str(raw)
    return f"0x{raw:X}"
# ═══════════════════════════════════════════════════════════════════════
#  SVD / ELF 符号解析
# ═══════════════════════════════════════════════════════════════════════

_svd_cache = {}


def load_svd(path):
    if path in _svd_cache:
        return _svd_cache[path]
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    _svd_cache[path] = tree.getroot()
    return _svd_cache[path]


def _svd_text(node, tag, default=None):
    ch = node.find(tag) if node is not None else None
    return ch.text.strip() if ch is not None and ch.text else default


def svd_get_periph(root, name):
    for p in root.findall(".//peripheral"):
        if _svd_text(p, "name") == name:
            return p
    raise KeyError(f"Peripheral '{name}' not found")


def svd_get_reg(root, periph, reg=None):
    p = svd_get_periph(root, periph)
    base = int(p.find("baseAddress").text, 0) if p.find("baseAddress") is not None else 0
    if reg is None:
        return base
    for r in p.findall(".//register"):
        if _svd_text(r, "name") == reg:
            offset = int(r.find("addressOffset").text, 0) if r.find("addressOffset") is not None else 0
            return base + offset
    raise KeyError(f"Register '{periph}.{reg}' not found")


def svd_get_field(root, periph, reg, field):
    p = svd_get_periph(root, periph)
    for r in p.findall(".//register"):
        if _svd_text(r, "name") == reg:
            for f in r.findall(".//field"):
                if _svd_text(f, "name") == field:
                    bit_offset = int(_svd_text(f, "bitOffset", "0"))
                    bit_width = int(_svd_text(f, "bitWidth", "32"))
                    mask = (1 << bit_width) - 1
                    return {"offset": bit_offset, "width": bit_width, "mask": mask}
    raise KeyError(f"Field '{periph}.{reg}.{field}' not found")


def elf_resolve(elf_path, name):
    """pyelftools 解析全局符号地址/大小（免 GDB）。"""
    from elftools.elf.elffile import ELFFile
    with open(elf_path, "rb") as f:
        ef = ELFFile(f)
        for section in ef.iter_sections():
            if not hasattr(section, "iter_symbols"):
                continue
            for sym in section.iter_symbols():
                if sym.name == name and sym.entry["st_info"]["type"] == "STT_OBJECT":
                    addr = sym.entry["st_value"]
                    size = sym.entry["st_size"] or 4
                    return addr, size
    raise KeyError(f"Symbol '{name}' not found in {elf_path}")


def gdb_resolve(elf_path, expr):
    """用 GDB 解析任意 C 表达式地址和大小。"""
    gdb = find_gdb()
    for flag, pattern in [
        (["--batch", "-ex", f"file {elf_path}",
          "-ex", f"print &({expr})"], r"0x([0-9a-fA-F]+)"),
        (["--batch", "-ex", f"file {elf_path}",
          "-ex", f"print sizeof({expr})"], r"=\s*(\d+)"),
    ]:
        pass
    addr_out = subprocess.run(
        [gdb, "--batch", "-ex", f"file {elf_path}",
         "-ex", f"print &({expr})"],
        capture_output=True, text=True, timeout=30)
    m = re.search(r"0x([0-9a-fA-F]+)", addr_out.stdout)
    if not m:
        raise RuntimeError(f"GDB failed to resolve '{expr}':\n{addr_out.stdout}")
    addr = int(m.group(1), 16)
    sz_out = subprocess.run(
        [gdb, "--batch", "-ex", f"file {elf_path}",
         "-ex", f"print sizeof({expr})"],
        capture_output=True, text=True, timeout=30)
    m2 = re.search(r"=\s*(\d+)", sz_out.stdout)
    size = int(m2.group(1)) if m2 else 4
    return addr, size


def detect_type_from_source(var, src_root):
    if not src_root:
        return None
    type_map = {
        "uint8_t": ("uint", 1), "int8_t": ("int", 1),
        "uint16_t": ("uint", 2), "int16_t": ("int", 2),
        "uint32_t": ("uint", 4), "int32_t": ("int", 4),
        "float": ("float", 4), "double": ("float", 8),
        "char": ("int", 1), "short": ("int", 2),
        "int": ("int", 4), "long": ("int", 4),
    }
    pat = re.compile(rf"\b([A-Za-z_][A-Za-z0-9_\s\*]+)\b{re.escape(var)}\b")
    root = Path(src_root)
    if not root.exists():
        return None
    for ext in (".c", ".h", ".cpp", ".hpp"):
        for fp in root.rglob(f"*{ext}"):
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                m = pat.search(line)
                if not m:
                    continue
                for kw, info in type_map.items():
                    if kw in m.group(1):
                        return {"type": info[0], "size": info[1], "src": str(fp)}
    return None
# ═══════════════════════════════════════════════════════════════════════
#  目标解析 / 读取 / 历史
# ═══════════════════════════════════════════════════════════════════════

def resolve_target(target, state, default_type="hex"):
    svd_path = state["ctx"].get("svd")
    elf = state["ctx"].get("elf")
    src = state["ctx"].get("source_root")

    # 直接地址
    if target.startswith("0x"):
        return {"name": target, "addr": int(target, 16),
                "size": 4, "type": default_type, "kind": "addr"}

    # SVD 寄存器 PER->REG
    if "->" in target:
        if not svd_path:
            raise RuntimeError(f"'{target}' looks like register, no --svd configured")
        per, reg = target.split("->", 1)
        root = load_svd(svd_path)
        addr = svd_get_reg(root, per, reg)
        return {"name": target, "addr": addr, "size": 4,
                "type": default_type, "kind": "register"}

    # SVD 点号 PER.REG.FIELD
    if "." in target and svd_path:
        parts = target.split(".")
        if len(parts) in (2, 3):
            try:
                root = load_svd(svd_path)
                if len(parts) == 2:
                    addr = svd_get_reg(root, parts[0], parts[1])
                    return {"name": target, "addr": addr, "size": 4,
                            "type": default_type, "kind": "register"}
                f = svd_get_field(root, parts[0], parts[1], parts[2])
                addr = svd_get_reg(root, parts[0], parts[1])
                return {"name": target, "addr": addr, "size": 4,
                        "type": default_type, "kind": "field", "field": f}
            except (KeyError, IndexError):
                pass

    # 变量名
    if not elf:
        raise RuntimeError(f"'{target}' looks like variable, no --elf configured")
    if re.search(r"[\[\]&*+\->]", target):
        addr, size = gdb_resolve(elf, target)
    else:
        try:
            addr, size = elf_resolve(elf, target)
        except KeyError:
            addr, size = gdb_resolve(elf, target)
    ti = detect_type_from_source(target, src)
    dtype = ti["type"] if ti else default_type
    dsize = ti["size"] if ti else size
    return {"name": target, "addr": addr, "size": dsize,
            "type": dtype, "kind": "variable"}


def read_one(t, state):
    w = select_width(t["size"])
    resp = ocd_read(t["addr"], width=w, count=1, state=state)
    vals = parse_values(resp)
    if not vals:
        raise RuntimeError(f"Read failed for {t['name']}")
    raw = vals[0]
    vw = w
    if t.get("field"):
        f = t["field"]
        raw = (raw & f["mask"]) >> f["offset"]
        vw = f["width"]
    disp = fmt_value(raw, t["type"], vw)
    interp = _interpret(t["name"], raw)
    ts = datetime.now().isoformat(timespec="seconds")
    entry = {"ts": ts, "name": t["name"], "addr": f"0x{t['addr']:08X}",
             "raw": raw, "val": disp, "interp": interp, "kind": t["kind"]}
    return entry


def _interpret(name, raw):
    vname = re.sub(r"[^A-Za-z0-9_].*$", "", name)
    cfg = _state_dir() / "vars.yaml"
    if not cfg.exists():
        cfg = _state_dir() / "vars.json"
    if not cfg.exists():
        return None
    try:
        if cfg.suffix == ".json":
            data = json.loads(cfg.read_text(encoding="utf-8"))
        else:
            data = _parse_simple_yaml(cfg)
        mapping = data.get("variables", data).get(vname, {})
        return mapping.get(str(raw))
    except Exception:
        return None


def _parse_simple_yaml(path):
    variables, cur, in_v = {}, None, False
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if re.fullmatch(r"variables:\s*", line):
                in_v = True
                continue
            if not in_v:
                continue
            m = re.fullmatch(r"\s{2}([A-Za-z_]\w*)\s*:\s*", line)
            if m:
                cur = m.group(1)
                variables[cur] = {}
                continue
            m = re.fullmatch(r"\s{4}([^:]+)\s*:\s*(.+)\s*", line)
            if m and cur:
                variables[cur][m.group(1).strip().strip("\"'")] = \
                    m.group(2).strip().strip("\"'")
    return variables


def append_history(entries):
    _rotate(_history())
    with _history().open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
# ═══════════════════════════════════════════════════════════════════════
#  GDB 运行器（断点/单步/回溯/寄存器 等）
# ═══════════════════════════════════════════════════════════════════════

def run_gdb(state, commands, batch=True):
    elf = require_ctx(state, "elf")
    gdb = find_gdb()
    host = state["ocd"]["host"]
    port = state["ocd"]["gdb"]
    base = ["set confirm off", "set pagination off",
            "set width 0", "set height 0", "set print pretty on"]
    conn = [f"target remote {host}:{port}"]
    for bp in state["bps"]:
        if bp.get("cond"):
            conn.append(f"break {bp['loc']} if {bp['cond']}")
        else:
            conn.append(f"break {bp['loc']}")
    for wp in state["wps"]:
        if wp["expr"].startswith("0x"):
            conn.append(f"watch *(unsigned int*)({wp['expr']})")
        else:
            conn.append(f"watch {wp['expr']}")
    cmds = base + [f"file {elf}"] + conn + commands

    if batch:
        cf = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as h:
                cf = h.name
                for c in cmds:
                    h.write(c + "\n")
            r = subprocess.run([gdb, "--batch", "-x", cf],
                               capture_output=True, text=True, timeout=30)
            out = r.stdout or ""
            if r.stderr:
                out += ("\n" if out else "") + r.stderr
            return out
        finally:
            if cf and os.path.exists(cf):
                os.unlink(cf)
    else:
        cmd = [gdb, elf]
        for c in cmds:
            cmd.extend(["-ex", c])
        subprocess.call(cmd)


def gdb_simple(state, commands):
    state["ocd"].setdefault("host", "127.0.0.1")
    return run_gdb(state, ["monitor halt"] + commands)
def cmd_start(args, state):
    merge_ctx(state, args)
    n = kill_stale_openocd()
    if n:
        print(f"[pre-clean] killed {n}")
    ocd = OCManager(state)
    msg = ocd.start()
    print(msg)
    save_state(state)
    return 0

def cmd_stop(args, state):
    OCManager(state).stop()
    print("OpenOCD stopped.")
    return 0

def cmd_status(args, state):
    o, c = state["ocd"], state["ctx"]
    alive = is_pid_alive(o.get("pid"))
    running = OCManager(state).is_running()
    print(f"OpenOCD: {'running' if running else 'stopped'} (pid={o.get('pid') if alive else 'n/a'}, tcl={o['tcl']}, gdb={o['gdb']})")
    print(f"ELF:     {c.get('elf') or 'n/a'}")
    print(f"SVD:     {c.get('svd') or 'n/a'}")
    print(f"Source:  {c.get('source_root') or 'n/a'}")
    print(f"Config:  {c.get('openocd_cfg') or 'n/a'}")
    print(f"Breaks:  {len(state['bps'])}  Watches: {len(state['wps'])}")
    return 0

def cmd_read(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    results = []
    for tn in args.targets:
        t = resolve_target(tn, state, args.type)
        e = read_one(t, state)
        results.append(e)
        line = f"{e['name']} = {e['val']} @ {e['addr']}"
        if e["interp"]:
            line += f" ({e['interp']})"
        print(line)
    append_history(results)
    return 0

def cmd_poll(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    t = resolve_target(args.target, state, args.type)
    count = args.count or 0
    n = 0
    try:
        while True:
            e = read_one(t, state)
            interp = f" ({e['interp']})" if e["interp"] else ""
            print(f"[{time.strftime('%H:%M:%S')}] {e['name']} = {e['val']}{interp}")
            append_history([e])
            n += 1
            if count > 0 and n >= count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped ({n} samples)")
    return 0

def cmd_history(args, state):
    p = _history()
    if not p.exists():
        print("No history.")
        return 0
    entries = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.targets and e["name"] not in args.targets:
                continue
            entries.append(e)
    for e in entries[-args.limit:]:
        line = f"{e['ts']} {e['name']} = {e['val']}"
        if e.get("interp"):
            line += f" ({e['interp']})"
        print(line)
    return 0

def cmd_reset(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    tcl_cmd(state, "reset run")
    print("Reset (running).")
    return 0

def cmd_halt(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    tcl_cmd(state, "halt")
    print("Halted.")
    return 0

def cmd_resume(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    tcl_cmd(state, "resume")
    print("Resumed.")
    return 0
def cmd_swo(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    freq = args.freq or 16000000
    log = str(_swo_log())
    openocd = find_openocd()
    cfg = state["ctx"].get("openocd_cfg") or DEFAULT_OPENOCD_CONFIG
    cmd_str = f"tpiu config internal {log} uart off {freq}"
    print(f"SWO -> {log} (freq={freq})")
    print("Ctrl+C to stop.")
    try:
        subprocess.run(
            [openocd] + shlex.split(cfg) + ["-c", cmd_str, "-c", "itm port 0 on"],
            timeout=0)  # no timeout = runs until killed
    except KeyboardInterrupt:
        print("\nSWO stopped.")
    return 0

def cmd_cycle(args, state):
    merge_ctx(state, args)
    save_state(state)
    build_cmd = args.build_cmd or state["ctx"].get("build_cmd")
    flash_elf = args.flash_elf or state["ctx"].get("flash_elf") or state["ctx"].get("elf")
    if build_cmd:
        print(f"Build: {build_cmd}")
        r = subprocess.run(shlex.split(build_cmd), text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Build failed (rc={r.returncode})")
    if args.flash and flash_elf:
        from pathlib import Path as P
        hf = P(flash_elf).with_suffix(".hex")
        if not hf.exists():
            hf = P(flash_elf)
        OCManager(state).ensure()
        cfg = state["ctx"].get("openocd_cfg") or DEFAULT_OPENOCD_CONFIG
        openocd = find_openocd()
        print(f"Flash: {hf}")
        subprocess.run(
            [openocd] + shlex.split(cfg) + ["-c", f"program {hf} verify reset exit"],
            timeout=120)
    if args.wait > 0:
        print(f"Wait {args.wait}s...")
        time.sleep(args.wait)
    OCManager(state).ensure()
    results = []
    for tn in args.targets:
        t = resolve_target(tn, state, args.type)
        e = read_one(t, state)
        line = f"{e['name']} = {e['val']} @ {e['addr']}"
        if e["interp"]:
            line += f" ({e['interp']})"
        print(line)
        results.append(e)
    append_history(results)
    return 0

def cmd_break(args, state):
    if args.list:
        for i in state["bps"]:
            cond = f" if {i['cond']}" if i.get("cond") else ""
            print(f"  {i['id']}: {i['loc']}{cond}")
        return 0
    if args.delete:
        ids = set()
        for tok in args.delete:
            if "-" in tok:
                a, b = tok.split("-", 1)
                ids.update(range(int(a), int(b) + 1))
            else:
                ids.add(int(tok))
        state["bps"] = [b for b in state["bps"] if b["id"] not in ids]
        save_state(state)
        return 0
    if args.clear_all:
        state["bps"] = []
        save_state(state)
        return 0
    for loc in args.locations:
        bp = {"id": state["next_bp"], "loc": loc, "cond": args.condition}
        state["bps"].append(bp)
        state["next_bp"] += 1
        print(f"BP {bp['id']} at {loc}" + (f" if {args.condition}" if args.condition else ""))
    save_state(state)
    return 0

def cmd_watch(args, state):
    if args.list:
        for i in state["wps"]:
            print(f"  {i['id']}: {i['expr']}")
        return 0
    if args.delete:
        ids = set(int(x) for x in args.delete)
        state["wps"] = [w for w in state["wps"] if w["id"] not in ids]
        save_state(state)
        return 0
    if args.expression:
        wp = {"id": state["next_wp"], "expr": args.expression}
        state["wps"].append(wp)
        state["next_wp"] += 1
        print(f"WP {wp['id']} for {args.expression}")
    save_state(state)
    return 0
def cmd_continue(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, ["continue"]).rstrip())
    return 0

def cmd_step(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, [f"step {args.count}"]).rstrip())
    return 0

def cmd_next(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, [f"next {args.count}"]).rstrip())
    return 0

def cmd_finish(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, ["finish"]).rstrip())
    return 0

def cmd_restart(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, ["monitor reset halt"]).rstrip())
    return 0

def cmd_backtrace(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    depth = f" {args.depth}" if args.depth else ""
    print(run_gdb(state, [f"bt{depth}"]).rstrip())
    return 0

def cmd_locals(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, ["info locals"]).rstrip())
    return 0

def cmd_registers(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    names = " " + " ".join(args.names) if args.names else ""
    print(run_gdb(state, [f"info registers{names}"]).rstrip())
    return 0

def cmd_frame(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, [f"frame {args.index}", "bt", "info locals"]).rstrip())
    return 0

def cmd_info(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    print(run_gdb(state, ["frame", "info line *$pc", "info registers pc sp lr xpsr"]).rstrip())
    return 0

def cmd_interactive(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    run_gdb(state, [], batch=False)
    return 0

def cmd_generate_script(args, state):
    merge_ctx(state, args)
    save_state(state)
    OCManager(state).ensure()
    outpath = Path(args.output) if args.output else Path(tempfile.mkstemp(suffix=".gdb")[1])
    lines = ["set pagination off"]
    for tn in args.targets:
        t = resolve_target(tn, state, args.type)
        w = select_width(t["size"])
        fmt = {8: "x/1bx", 16: "x/1hx"}.get(w, "x/1wx")
        lines.append(f"{fmt} 0x{t['addr']:08X}  # {t['name']}")
    outpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(outpath)
    return 0
# ═══════════════════════════════════════════════════════════════════════
#  Argparse / Main
# ═══════════════════════════════════════════════════════════════════════

def _ctx_args(p):
    p.add_argument("--elf")
    p.add_argument("--svd")
    p.add_argument("--source-root")
    p.add_argument("--build-cmd")
    p.add_argument("--flash-elf")
    p.add_argument("--target", help="OpenOCD target cfg name (e.g. stm32f4x)")
    p.add_argument("--openocd-config", help="Full OpenOCD config string")
    p.add_argument("--extra-cfg", help="Extra config appended")
    p.add_argument("--tcl-port", type=int)
    p.add_argument("--gdb-port", type=int)
    p.add_argument("--telnet-port", type=int)

def build_parser():
    ap = argparse.ArgumentParser(prog="mcu_dbg")
    sp = ap.add_subparsers(dest="command", required=True)

    p = sp.add_parser("start"); _ctx_args(p); p.set_defaults(func=cmd_start)
    p = sp.add_parser("stop"); p.set_defaults(func=cmd_stop)
    p = sp.add_parser("status"); p.set_defaults(func=cmd_status)

    p = sp.add_parser("read"); _ctx_args(p)
    p.add_argument("targets", nargs="+")
    p.add_argument("--type", choices=["float","int","uint","hex"], default="hex")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sp.add_parser("poll"); _ctx_args(p)
    p.add_argument("target")
    p.add_argument("--type", choices=["float","int","uint","hex"], default="hex")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--count", type=int, default=0)
    p.set_defaults(func=cmd_poll)

    p = sp.add_parser("history")
    p.add_argument("targets", nargs="*")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sp.add_parser("reset"); _ctx_args(p); p.set_defaults(func=cmd_reset)
    p = sp.add_parser("halt"); _ctx_args(p); p.set_defaults(func=cmd_halt)
    p = sp.add_parser("resume"); _ctx_args(p); p.set_defaults(func=cmd_resume)

    p = sp.add_parser("swo"); _ctx_args(p)
    p.add_argument("--freq", type=int)
    p.set_defaults(func=cmd_swo)

    p = sp.add_parser("cycle"); _ctx_args(p)
    p.add_argument("targets", nargs="+")
    p.add_argument("--type", choices=["float","int","uint","hex"], default="hex")
    p.add_argument("--wait", type=float, default=10)
    p.add_argument("--flash", action="store_true")
    p.set_defaults(func=cmd_cycle)

    p = sp.add_parser("break")
    p.add_argument("locations", nargs="*")
    p.add_argument("--condition")
    p.add_argument("--list", action="store_true")
    p.add_argument("--delete", nargs="+")
    p.add_argument("--clear-all", action="store_true")
    p.set_defaults(func=cmd_break)

    p = sp.add_parser("watch")
    p.add_argument("expression", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--delete", nargs="+")
    p.set_defaults(func=cmd_watch)

    p = sp.add_parser("continue"); _ctx_args(p); p.set_defaults(func=cmd_continue)
    p = sp.add_parser("run"); _ctx_args(p); p.set_defaults(func=cmd_continue)
    p = sp.add_parser("step"); _ctx_args(p)
    p.add_argument("count", nargs="?", type=int, default=1)
    p.set_defaults(func=cmd_step)
    p = sp.add_parser("next"); _ctx_args(p)
    p.add_argument("count", nargs="?", type=int, default=1)
    p.set_defaults(func=cmd_next)
    p = sp.add_parser("finish"); _ctx_args(p); p.set_defaults(func=cmd_finish)
    p = sp.add_parser("restart"); _ctx_args(p); p.set_defaults(func=cmd_restart)
    p = sp.add_parser("backtrace"); _ctx_args(p)
    p.add_argument("depth", nargs="?", type=int)
    p.set_defaults(func=cmd_backtrace)
    p = sp.add_parser("locals"); _ctx_args(p); p.set_defaults(func=cmd_locals)
    p = sp.add_parser("registers"); _ctx_args(p)
    p.add_argument("names", nargs="*")
    p.set_defaults(func=cmd_registers)
    p = sp.add_parser("frame"); _ctx_args(p)
    p.add_argument("index", type=int)
    p.set_defaults(func=cmd_frame)
    p = sp.add_parser("info"); _ctx_args(p); p.set_defaults(func=cmd_info)
    p = sp.add_parser("interactive"); _ctx_args(p); p.set_defaults(func=cmd_interactive)
    p = sp.add_parser("gdb"); _ctx_args(p); p.set_defaults(func=cmd_interactive)

    p = sp.add_parser("generate-script"); _ctx_args(p)
    p.add_argument("targets", nargs="+")
    p.add_argument("--type", choices=["float","int","uint","hex"], default="hex")
    p.add_argument("--output")
    p.set_defaults(func=cmd_generate_script)

    return ap

def main():
    parser = build_parser()
    args = parser.parse_args()
    state = load_state()
    try:
        return args.func(args, state)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
