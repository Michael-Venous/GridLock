"""Run parser code the ingest agent wrote, cut off from the network, the files and the AWS credentials. Standard library only.

Before it runs, the code is read (not executed): it may import only text-processing modules (ALLOWED), may not name
builtins that open files, evaluate code or reach into objects (BANNED), and may not touch attributes that lead from
an object back to the interpreter (underscore, frame and code attributes, and str.format, whose fields can walk them).
Those checks are not the boundary: the code runs in its own Python process with an empty environment, CPU, memory and
file-size limits and OS isolation. Registration and production builds require working bubblewrap, which hides the host
files and network. For a dry run or test only, GRIDLOCK_ALLOW_UNISOLATED=1 allows a weaker network-only unshare mode
(or the code checks alone when no namespace is available). Its output goes to files the file-size limit caps, so it
can't fill the host's memory. It receives the PDF's page texts and returns records.
Regexes the agent wrote (a count pattern) run the same way under a time limit, since one that backtracks can run for hours.
"""
import ast
import functools
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile

ALLOWED = {"re", "json", "datetime", "math", "collections", "itertools", "functools", "statistics", "decimal", "fractions",
           "calendar", "unicodedata", "textwrap", "difflib", "bisect", "heapq"}
BANNED = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars", "getattr",
          "setattr", "delattr", "hasattr", "memoryview", "help", "exit", "quit", "object", "type", "super", "classmethod",
          "staticmethod", "property"}
ATTR_PREFIXES = ("_", "f_", "gi_", "cr_", "ag_", "tb_", "co_", "mro")
# a format field ('{0.__globals__}') walks attributes inside a string, where the attribute check can't see it
FORMAT_ATTRS = {"format", "format_map"}
TIMEOUT_S = 120
FIND_TIMEOUT_S = 30
MAX_OUTPUT = 32 * 1024 * 1024
UNISOLATED = "GRIDLOCK_ALLOW_UNISOLATED"

RUNNER = r"""
import builtins, json, sys
ALLOWED, BANNED = %s, %s
data = json.loads(sys.stdin.read())
# print() in the parser goes to stderr, not into the records
out, sys.stdout = sys.stdout, sys.stderr
real = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in ALLOWED:
        raise ImportError(f"import of {name!r} is not allowed")
    return real(name, globals, locals, fromlist, level)
safe = {k: v for k, v in vars(builtins).items() if k not in BANNED}
safe["__import__"] = guarded
ns = {"__builtins__": safe, "__name__": "parser"}
exec(compile(data["code"], "parser.py", "exec"), ns)
out.write(json.dumps(ns["parse"](data["pages"])))
out.flush()
"""

FINDER = r"""
import json, re, sys
data = json.loads(sys.stdin.read())
try:
    rx = re.compile(data["pattern"])
    scope = re.compile(data["scope"]) if data["scope"] else None
except re.error as e:
    sys.stdout.write(json.dumps({"error": str(e)}))
    sys.exit(0)
sys.stdout.write(json.dumps({"spans": [[i, m.start(), m.end()] for i, t in enumerate(data["texts"])
                                       if scope is None or scope.search(t) for m in rx.finditer(t)]}))
"""


def check_code(src):
    """Problems that keep the code from running at all, as a list of strings (empty when it may run)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error line {e.lineno}: {e.msg}"]
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            out += [f"line {line}: import of {a.name!r} is not allowed" for a in node.names if a.name.split(".")[0] not in ALLOWED]
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED:
                out.append(f"line {line}: import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Name) and (node.id in BANNED or node.id.startswith("__")):
            out.append(f"line {line}: {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith(ATTR_PREFIXES):
            out.append(f"line {line}: attribute {node.attr!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in FORMAT_ATTRS:
            out.append(f"line {line}: .{node.attr}() is not allowed (its fields can reach object internals); use an f-string")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.append(f"line {line}: global/nonlocal is not allowed")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "parse" for n in tree.body):
        out.append("no top-level function parse(pages)")
    return out


def _isolation():
    """The strongest isolation this host offers: bubblewrap, else a network-less namespace, else none."""
    py = os.path.realpath(sys.executable)
    if bwrap := shutil.which("bwrap"):
        cmd = [bwrap, "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
               "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--chdir", "/tmp",
               "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
        if not py.startswith("/usr/"):
            cmd += ["--ro-bind", sys.base_prefix, sys.base_prefix]
        if _works(cmd + [py, "-I", "-c", "pass"]):
            return "bubblewrap", cmd + [py]
    if (unshare := shutil.which("unshare")) and _works([unshare, "-rn", py, "-I", "-c", "pass"]):
        return "unshare", [unshare, "-rn", py]
    return "none", [py]


def _works(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_ISO = None


def isolation():
    global _ISO
    if _ISO is None:
        _ISO = _isolation()
    return _ISO


def require_bubblewrap():
    """Generated code must have filesystem and network isolation before it can be registered or published."""
    if isolation()[0] != "bubblewrap":
        raise RuntimeError("agent-written parser registration and production builds require working bubblewrap; "
                           "GRIDLOCK_ALLOW_UNISOLATED permits dry runs and tests only")


def _limits(cpu_s):
    # the wall-clock limit fires first; the CPU limit stops a child whose parent is gone
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))
    # caps the stdout and stderr files too, so output is bounded while it is written, not after
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT + 1, MAX_OUTPUT + 1))


def _spawn(script, payload, timeout):
    """Run script in the isolated interpreter with payload as JSON on stdin. Returns (stdout, stderr tail, failure),
    failure being None or (kind, detail) with kind one of "isolation", "time", "size", "exit"."""
    mode, cmd = isolation()
    if mode == "none" and os.environ.get(UNISOLATED) != "1":
        return None, "", ("isolation", "this host offers no OS isolation for agent-written code (neither bubblewrap nor "
                          f"'unshare -rn' works), so GridLock won't run it; install bubblewrap, or set {UNISOLATED}=1 to run "
                          "it with only the code checks and resource limits")
    with tempfile.TemporaryFile() as fin, tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        fin.write(json.dumps(payload).encode())
        fin.seek(0)
        p = subprocess.Popen(cmd + ["-I", "-B", "-S", "-c", script], stdin=fin, stdout=fout, stderr=ferr,
                             env={} if mode != "none" else {"PATH": "/usr/bin:/bin"}, cwd=tempfile.gettempdir(),
                             preexec_fn=functools.partial(_limits, timeout + 5), start_new_session=True)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # the whole group: nothing the child started may keep running (or writing) past the limit
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            return None, "", ("time", f"{timeout} s")
        out_size, err_size = os.fstat(fout.fileno()).st_size, os.fstat(ferr.fileno()).st_size
        if max(out_size, err_size) > MAX_OUTPUT:
            return None, "", ("size", f"{MAX_OUTPUT >> 20} MB")
        ferr.seek(max(0, err_size - 16384))
        err = ferr.read().decode(errors="replace")
        if p.returncode != 0:
            return None, err, ("exit", str(p.returncode))
        fout.seek(0)
        return fout.read().decode(errors="replace"), err, None


def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON allows; use null")


def run(src, pages, timeout=TIMEOUT_S):
    """Run parse(pages) from src in isolation. Returns (records or None, error or None)."""
    problems = check_code(src)
    if problems:
        return None, "code rejected before running:\n" + "\n".join(problems)
    if isolation()[0] != "bubblewrap" and os.environ.get(UNISOLATED) != "1":
        return None, ("agent-written parser needs working bubblewrap to run safely; "
                      f"set {UNISOLATED}=1 only for a dry run or test")
    out, err, fail = _spawn(RUNNER % (repr(ALLOWED), repr(BANNED)), {"code": src, "pages": pages}, timeout)
    if fail:
        kind, detail = fail
        tail = "\n".join(err.strip().splitlines()[-15:])
        return None, {"isolation": detail, "time": f"parser ran longer than {detail}",
                      "size": f"parser output (its records, or text it prints) is larger than {detail}",
                      "exit": f"parser raised an error (exit {detail}):\n{tail}"}[kind]
    try:
        return json.loads(out, parse_constant=_no_constant), None
    except ValueError as e:
        return None, f"parser output is not valid JSON: {e}"


def find(pattern, texts, scope=None, timeout=None):
    """Where a regex the agent wrote matches: [[text index, start, end], ...] in the texts that the scope regex matches
    (all of them without one). Returns (spans or None, error or None)."""
    timeout = timeout or FIND_TIMEOUT_S
    out, err, fail = _spawn(FINDER, {"pattern": pattern, "scope": scope or "", "texts": texts}, timeout)
    if fail:
        kind, detail = fail
        return None, {"isolation": detail, "time": f"regex {pattern!r} ran longer than {detail} (nested repeats such as (\\w+\\s?)+ backtrack); simplify it",
                      "size": f"regex {pattern!r} matches too often ({detail} of matches)",
                      "exit": f"regex {pattern!r} failed: {err.strip()[-300:]}"}[kind]
    res = json.loads(out)
    if "error" in res:
        return None, f"regex {pattern!r} is not valid: {res['error']}"
    return res["spans"], None
