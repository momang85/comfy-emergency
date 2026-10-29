#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comfy-emergency: a zero-dependency, read-only-first ComfyUI ER doctor.

Works out-of-process on system Python. Doctor, workflow, log and report modes.
Only ``workflow --apply`` writes, and only after a pip freeze snapshot.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = "0.1.0"
TOOL = "ComfyUI 急诊医生"
REPO_URL = os.environ.get("COMFY_ER_REPO_URL",
                          "https://github.com/momang85/comfy-emergency")
NODE_MAP_URL = "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/extension-node-map.json"
NODE_MAP_TTL = 7 * 24 * 60 * 60
CACHE_DIR_NAME = ".comfy-er"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OWNER_RE = re.compile(r"custom_nodes[\\/]([^\\/\r\n\"':]+)", re.IGNORECASE)
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")

# Built-in ComfyUI nodes. Deliberately embedded so a broken/absent install does
# not turn every core node into a false "missing custom node".
CORE_NODES = set("""
CheckpointLoaderSimple CheckpointLoader unCLIPCheckpointLoader ImageOnlyCheckpointLoader
UNETLoader UnetLoaderGGUF CLIPLoader DualCLIPLoader CLIPLoaderGGUF DualCLIPLoaderGGUF
VAELoader LoraLoader LoraLoaderModelOnly ControlNetLoader DiffControlNetLoader
UpscaleModelLoader CLIPVisionLoader StyleModelLoader GLIGENLoader HypernetworkLoader
CLIPSetLastLayer CLIPVisionEncode StyleModelApply GLIGENTextBoxApply ControlNetApply
ControlNetApplyAdvanced SetUnionControlNetType CLIPTextEncode CLIPTextEncodeSDXL
CLIPTextEncodeSDXLRefiner CLIPTextEncodeFlux T5TextEncode TextEncodeQwenImageEdit
TextEncodeQwenImageEditPlus ConditioningCombine ConditioningConcat ConditioningAverage
ConditioningSetArea ConditioningSetAreaPercentage ConditioningSetMask
ConditioningSetTimestepRange ConditioningZeroOut InpaintModelConditioning FluxGuidance
ReferenceLatent KSampler KSamplerAdvanced SamplerCustom SamplerCustomAdvanced
KSamplerSelect BasicGuider CFGGuider DualCFGGuider BasicScheduler KarrasScheduler
ExponentialScheduler PolyexponentialScheduler SDTurboScheduler VPScheduler
BetaSamplingScheduler SamplerDPMPP_2M_SDE SamplerDPMPP_SDE SamplerEulerAncestral
SamplerEuler SamplerLMS SamplerDPMAdaptative SplitSigmas SplitSigmasDenoise FlipSigmas
SetFirstSigma AddNoise DisableNoise RandomNoise EmptyLatentImage EmptySD3LatentImage
EmptyFluxLatentImage VAEEncode VAEDecode VAEEncodeTiled VAEDecodeTiled LatentUpscale
LatentUpscaleBy LatentFromBatch RepeatLatentBatch SetLatentNoiseMask LatentComposite
LatentCompositeMasked LatentBlend LatentRotate LatentFlip LatentCrop SaveImage
PreviewImage LoadImage LoadImageMask ImageScale ImageScaleBy ImageScaleToTotalPixels
ImageUpscaleWithModel ImageInvert ImageBatch ImageCrop ImagePadForOutpaint
ImageCompositeMasked ImageSharpen ImageColorToMask MaskToImage MaskComposite GrowMask
FeatherMask SolidMask InvertMask ThresholdMask MaskPreview ImageBlend ImageAdd
ImageSubtract ImageMultiply ImageDivide ImageDesaturate ImagePosterize ImageQuantize
ImageBlur ImageComposite EmptyImage ImageFromBatch JoinImageWithAlpha SplitImageWithAlpha
ColorToMask ImageToMask SaveAnimatedWEBP SaveAnimatedPNG SaveWEBM SaveVideo CreateVideo
LoadVideo PreviewAny FreeU FreeU_V2 ModelSamplingAuraFlow ModelSamplingSD3
ModelSamplingFlux ModelSamplingStableCascade ModelSamplingDiscrete ModelMergeSimple
ModelMergeBlocks ModelMergeSubtract ModelMergeAdd ModelMergeSD1 ModelMergeSDXL
SaveLatent LoadLatent VAEDecodeAudio VAEEncodeAudio CLIPTextEncodeHunyuanDiT
FluxDisableGuidance FluxProCannyNode FluxProControlNetCannyNode
""".split())
VIRTUAL_NODES = set("Note MarkdownNote Reroute PrimitiveNode GroupNode".split())
LOADER_SPECS = {
    "CheckpointLoaderSimple": ("checkpoints", (0,)),
    "CheckpointLoader": ("checkpoints", (0,)),
    "unCLIPCheckpointLoader": ("checkpoints", (0,)),
    "ImageOnlyCheckpointLoader": ("checkpoints", (0,)),
    "UNETLoader": ("unet", (0,)),
    "UnetLoaderGGUF": ("unet", (0,)),
    "CLIPLoader": ("clip", (0,)),
    "DualCLIPLoader": ("clip", (0, 1)),
    "CLIPLoaderGGUF": ("clip", (0,)),
    "DualCLIPLoaderGGUF": ("clip", (0, 1)),
    "VAELoader": ("vae", (0,)),
    "LoraLoader": ("loras", (0,)),
    "LoraLoaderModelOnly": ("loras", (0,)),
    "ControlNetLoader": ("controlnet", (0,)),
    "DiffControlNetLoader": ("controlnet", (0,)),
    "UpscaleModelLoader": ("upscale_models", (0,)),
    "CLIPVisionLoader": ("clip_vision", (0,)),
    "StyleModelLoader": ("style_models", (0,)),
    "GLIGENLoader": ("gligen", (0,)),
    "HypernetworkLoader": ("hypernetworks", (0,)),
    "IPAdapterModelLoader": ("ipadapter", (0,)),
    "InstantIDModelLoader": ("instantid", (0,)),
    "PhotoMakerLoader": ("photomaker", (0,)),
    "ModelPatchLoader": ("model_patches", (0,)),
}
INPUT_DIRS = {
    "ckpt_name": "checkpoints", "checkpoint_name": "checkpoints",
    "lora_name": "loras", "vae_name": "vae", "control_net_name": "controlnet",
    "clip_name": "clip", "clip_name1": "clip", "clip_name2": "clip",
    "clip_vision_name": "clip_vision", "unet_name": "unet",
    "upscale_model_name": "upscale_models", "style_model_name": "style_models",
    "gligen_name": "gligen", "hypernetwork_name": "hypernetworks",
    "ipadapter_file": "ipadapter", "instantid_file": "instantid",
    "photomaker_model_name": "photomaker", "model_patch_name": "model_patches",
}
SEVERITY_CN = {"error": "错误", "warn": "警告", "info": "提示"}
IMPORT_RULE_IDS = {"import-failed", "cannot-import", "module-missing", "dll-missing",
                   "numpy-abi", "xformers"}


def say(text=""):
    """Encoding-safe print for GBK consoles and broken text encodings."""
    text = str(text)
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            sys.stdout.write(text.encode(enc, "backslashreplace").decode(enc) + "\n")
            sys.stdout.flush()
        except Exception:
            try:
                sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
                sys.stdout.flush()
            except Exception:
                pass
    except (BrokenPipeError, OSError):
        pass


def emit_json(obj):
    """Emit UTF-8 JSON even when Windows console stdio defaults to GBK."""
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
            return
        except (BrokenPipeError, OSError):
            return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True, indent=2) + "\n")
        sys.stdout.flush()


def strip_ansi(text):
    return ANSI_RE.sub("", text or "")


def decode_bytes(raw):
    """Decode UTF-8/GBK-mixed logs one line at a time."""
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    out = []
    for line in text.split(b"\n"):
        try:
            out.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            try:
                out.append(line.decode("gb18030"))
            except UnicodeDecodeError:
                out.append(line.decode("utf-8", "replace"))
    return "\n".join(out)


def read_text_file(path):
    return decode_bytes(Path(path).read_bytes())


def one_line(value, limit=240):
    text = " ".join(str(value).replace("\x00", "").split())
    if len(text) > limit:
        return text[:limit - 3] + "..."
    return text


def run_capture(cmd, timeout=60, cwd=None, env=None):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, cwd=str(cwd) if cwd else None, env=env)
    except FileNotFoundError:
        return {"ok": False, "code": None, "out": b"", "err": b"",
                "error": "找不到可执行文件：{}".format(cmd[0])}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "out": b"", "err": b"",
                "error": "执行超时（{} 秒）".format(timeout)}
    except OSError as exc:
        return {"ok": False, "code": None, "out": b"", "err": b"",
                "error": "无法启动命令：{}".format(exc)}
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "out": proc.stdout, "err": proc.stderr, "error": None}


def _clean_path(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value).strip().strip('"'))))


def _is_comfy_root(path):
    try:
        path = Path(path)
        return (path.is_dir() and (path / "main.py").is_file()
                and (path / "comfy").is_dir() and (path / "custom_nodes").is_dir())
    except OSError:
        return False


def _root_candidates(base):
    return [base, base / "ComfyUI", base / "ComfyUI_windows_portable" / "ComfyUI"]


def locate_comfy(explicit=None):
    """Find a ComfyUI root using explicit path, env, cwd and shallow cwd scan."""
    seen = set()
    searched = []
    warnings = []

    def consider(path, source):
        path = Path(path)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = os.path.normcase(str(resolved))
        if key in seen:
            return None
        seen.add(key)
        if len(searched) < 80:
            searched.append(str(path))
        if _is_comfy_root(path):
            return {"path": str(resolved), "source": source}
        return None

    if explicit:
        base = _clean_path(explicit)
        if not base.exists():
            return {"path": None, "source": None, "searched": searched,
                    "warnings": warnings,
                    "error": "--comfy 指定的路径不存在：{}".format(base)}
        for candidate in _root_candidates(base):
            found = consider(candidate, "--comfy 参数")
            if found:
                return {"path": found["path"], "source": found["source"],
                        "searched": searched, "warnings": warnings, "error": None}
        return {"path": None, "source": None, "searched": searched,
                "warnings": warnings,
                "error": "这个路径不像 ComfyUI：需要同时包含 main.py、comfy/、custom_nodes/：{}".format(base)}

    env_path = os.environ.get("COMFYUI_PATH")
    if env_path:
        for candidate in _root_candidates(_clean_path(env_path)):
            found = consider(candidate, "COMFYUI_PATH 环境变量")
            if found:
                return {"path": found["path"], "source": found["source"],
                        "searched": searched, "warnings": warnings, "error": None}
        warnings.append("COMFYUI_PATH 指向的路径无效，继续自动查找。")

    cwd = Path.cwd()
    for candidate in [cwd, cwd / "ComfyUI",
                      cwd / "ComfyUI_windows_portable" / "ComfyUI"]:
        found = consider(candidate, "当前目录")
        if found:
            return {"path": found["path"], "source": found["source"],
                    "searched": searched, "warnings": warnings, "error": None}

    try:
        children = sorted([p for p in cwd.iterdir() if p.is_dir()],
                          key=lambda p: p.name.lower())[:200]
    except OSError:
        children = []
    for child in children:
        for candidate in _root_candidates(child):
            found = consider(candidate, "当前目录浅层扫描")
            if found:
                return {"path": found["path"], "source": found["source"],
                        "searched": searched, "warnings": warnings, "error": None}

    return {"path": None, "source": None, "searched": searched,
            "warnings": warnings, "error": None}


def detect_python(comfy_root):
    if not comfy_root:
        return {"path": None, "label": None, "fallback": False}
    root = Path(comfy_root)
    candidates = [
        (root.parent / "python_embeded" / "python.exe", r"python_embeded\python.exe（便携版）"),
        (root / "python_embeded" / "python.exe", r"python_embeded\python.exe"),
        (root / ".venv" / "Scripts" / "python.exe", r".venv\Scripts\python.exe"),
        (root / "venv" / "Scripts" / "python.exe", r"venv\Scripts\python.exe"),
        (root / ".venv" / "bin" / "python3", ".venv/bin/python3"),
        (root / "venv" / "bin" / "python3", "venv/bin/python3"),
        (root / "python.exe", r"python.exe"),
    ]
    for path, label in candidates:
        if path.is_file():
            return {"path": str(path), "label": label, "fallback": False}
    return {"path": sys.executable,
            "label": "当前运行本工具的 Python（未找到 ComfyUI 专用环境）",
            "fallback": True}


def list_custom_nodes(root):
    base = Path(root) / "custom_nodes"
    nodes = []
    if not base.is_dir():
        return nodes
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return nodes
    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.is_dir():
            git_config = entry / ".git" / "config"
            urls = []
            if git_config.is_file():
                try:
                    for match in re.finditer(r"^\s*url\s*=\s*(.+?)\s*$",
                                             read_text_file(git_config), re.MULTILINE):
                        urls.append(match.group(1).strip())
                except OSError:
                    pass
            nodes.append({
                "name": entry.name,
                "path": str(entry),
                "urls": urls,
                "disabled": entry.name.lower().endswith(".disabled"),
                "requirements": (entry / "requirements.txt").is_file(),
            })
    return nodes


def scan_models(root):
    base = Path(root) / "models"
    info = {"available": base.is_dir(), "count": 0, "names": set(), "rels": set()}
    if not info["available"]:
        return info
    for dirpath, dirnames, filenames in os.walk(str(base), onerror=lambda exc: None):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            info["count"] += 1
            info["names"].add(filename.casefold())
            try:
                rel = Path(dirpath, filename).relative_to(base).as_posix()
                info["rels"].add(rel.casefold())
            except ValueError:
                pass
    return info


def _strip_yaml_comment(line):
    quote = None
    for index, char in enumerate(line):
        if char in ("'", '"'):
            quote = None if quote == char else (quote or char)
        elif char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _yaml_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def read_extra_model_paths(path):
    """Tiny YAML-subset reader for extra_model_paths.yaml (no PyYAML)."""
    path = Path(path)
    result = {"present": path.is_file(), "path": str(path), "sections": [], "error": None}
    if not result["present"]:
        return result
    try:
        lines = read_text_file(path).splitlines()
    except OSError as exc:
        result["error"] = "无法读取 extra_model_paths.yaml：{}".format(exc)
        return result
    current = None
    pending = None
    last_indent = 0
    for raw in lines:
        line = _strip_yaml_comment(raw.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if indent == 0 and content.endswith(":"):
            current = {"name": content[:-1].strip(), "base_path": "",
                       "paths": {}}
            result["sections"].append(current)
            pending = None
            last_indent = indent
            continue
        if current is None:
            continue
        if content.startswith("- "):
            if pending:
                current["paths"].setdefault(pending, []).append(_yaml_value(content[2:]))
            continue
        if ":" in content:
            key, value = content.split(":", 1)
            key = key.strip()
            value = _yaml_value(value)
            if key == "base_path":
                current["base_path"] = value
                pending = None
            elif value in ("|", ">"):
                current["paths"].setdefault(key, [])
                pending = key
            elif value:
                current["paths"].setdefault(key, []).append(value)
                pending = None
            else:
                current["paths"].setdefault(key, [])
                pending = key
            last_indent = indent
        elif pending and indent > last_indent:
            current["paths"].setdefault(pending, []).append(content)
    return result


def pip_list(python_path):
    if not python_path:
        return {"packages": {}, "error": "没有可用的 Python 解释器", "raw": ""}
    result = run_capture([str(python_path), "-m", "pip", "list", "--format", "json"], timeout=60)
    text = decode_bytes(result["out"])
    if not result["ok"]:
        detail = one_line(decode_bytes(result["err"])) or result["error"] or "pip 返回非零退出码"
        return {"packages": {}, "error": detail, "raw": text}
    start = text.find("[")
    try:
        items = json.loads(text[start:] if start >= 0 else text)
        packages = {}
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                packages[str(item["name"]).casefold()] = str(item.get("version", ""))
        return {"packages": packages, "error": None, "raw": text}
    except (ValueError, TypeError) as exc:
        return {"packages": {}, "error": "pip list 输出不是 JSON：{}".format(exc), "raw": text}


def _canonical_name(name):
    return re.sub(r"[-_.]+", "-", str(name).strip().casefold())


def _repo_id(url):
    text = str(url).strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    text = text.replace("\\", "/")
    if text.lower().startswith("git@"):
        text = text[4:].replace(":", "/", 1)
    match = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", text, re.IGNORECASE)
    if match:
        return (match.group(1) + "/" + match.group(2)).casefold()
    return text.casefold()


def _repo_basename(url):
    text = str(url).strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return re.split(r"[/\\:]", text)[-1] or "custom-node"


def _version_tuple(value):
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part is not None)


def _pkg_version(packages, name):
    return packages.get(name.casefold(), "")


def _requirement_records(path):
    records = []
    try:
        lines = read_text_file(path).splitlines()
    except OSError:
        return records
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        if not match:
            continue
        display = match.group(1)
        rest = line[len(display):]
        if rest.startswith("["):
            close = rest.find("]")
            rest = rest[close + 1:] if close >= 0 else ""
        rest = rest.split(";", 1)[0].strip()
        records.append({"name": _canonical_name(display), "display": display,
                        "spec": rest, "line": raw.strip()})
    return records


def _node_requirement_records(custom_nodes):
    result = []
    for node in custom_nodes or []:
        req = Path(node["path"]) / "requirements.txt"
        if req.is_file():
            result.append({"node": node, "path": req,
                           "records": _requirement_records(req)})
    return result


RULE_TABLE = {
    "numpy2-torch": {
        "title": "NumPy 2 与旧版 PyTorch 冲突", "severity": "error",
        "why": "numpy 2.x 改变了底层 ABI，torch < 2.3 常报 numpy.core.multiarray failed to import。",
        "fix": "先降级 NumPy：{python} -m pip install \"numpy<2\"", "confidence": "高",
    },
    "xformers-torch": {
        "title": "xformers 与 PyTorch 版本不匹配", "severity": "warn",
        "why": "xformers 的二进制 wheel 依赖特定 torch 版本，混装会在启动或采样时崩溃。",
        "fix": "先卸载：{python} -m pip uninstall -y xformers；再安装与该 torch 对应的 wheel。",
        "confidence": "高",
    },
    "torchvision-torch": {
        "title": "torchvision 与 PyTorch 版本不匹配", "severity": "warn",
        "why": "torchvision 与 torch 是配套发布的，版本错位会导致 DLL/ABI 导入错误。",
        "fix": "按 https://pytorch.org/get-started/locally/ 选择配套版本重装 torch 与 torchvision。",
        "confidence": "高",
    },
    "transformers-new": {
        "title": "transformers 版本偏新", "severity": "warn",
        "why": "transformers >= 4.46 改动了部分 tokenizer/API，较旧的自定义节点可能导入失败。",
        "fix": "如相关节点报错：{python} -m pip install \"transformers<4.46\"", "confidence": "中",
    },
    "pydantic2-old-nodes": {
        "title": "Pydantic 2 与旧节点冲突", "severity": "warn",
        "why": "部分旧自定义节点仍按 Pydantic 1 编写，升级到 2.x 后会在导入时 TypeError。",
        "fix": "优先更新节点；临时兼容可执行 {python} -m pip install \"pydantic<2\"", "confidence": "中",
    },
    "missing-requirements": {
        "title": "自定义节点依赖未安装", "severity": "warn",
        "why": "requirements.txt 声明的包不在目标 Python 的 pip list 中。",
        "fix": "{python} -m pip install -r \"{requirements}\"", "confidence": "中",
    },
    "torch-cpu-on-gpu": {
        "title": "有 NVIDIA GPU，但 torch 是 CPU 版", "severity": "warn",
        "why": "检测到 nvidia-smi，但 torch 版本号包含 +cpu；ComfyUI 会极慢或无法使用 CUDA。",
        "fix": "卸载 CPU 版 torch 后，按 https://pytorch.org/get-started/locally/ 安装 CUDA 版。",
        "confidence": "高",
    },
}

XFORMERS_TORCH = {
    (0, 0, 22): (2, 0), (0, 0, 23): (2, 1), (0, 0, 24): (2, 1),
    (0, 0, 25): (2, 2), (0, 0, 26): (2, 2), (0, 0, 27): (2, 3),
    (0, 0, 28): (2, 3), (0, 0, 29): (2, 4), (0, 0, 30): (2, 5),
    (0, 0, 31): (2, 6), (0, 0, 32): (2, 7),
}
TORCHVISION_TORCH = {
    (0, 15): (2, 0), (0, 16): (2, 1), (0, 17): (2, 2), (0, 18): (2, 3),
    (0, 19): (2, 4), (0, 20): (2, 5), (0, 21): (2, 6), (0, 22): (2, 7),
}


def _finding(rule_id, **overrides):
    item = dict(RULE_TABLE[rule_id])
    item["id"] = rule_id
    item.update(overrides)
    return item


def _python_label(python_exe):
    return '"{}"'.format(python_exe) if python_exe else "<ComfyUI 的 python>"


def _missing_requirements(custom_nodes, packages):
    installed = set(packages.keys())
    missing = []
    for item in _node_requirement_records(custom_nodes):
        seen = set()
        names = []
        for record in item["records"]:
            if record["name"] == "python" or record["name"] in seen:
                continue
            seen.add(record["name"])
            if record["name"] not in installed:
                names.append(record["display"])
        if names:
            missing.append({"node": item["node"]["name"], "path": str(item["path"]),
                            "names": names})
    return missing


def evaluate_rules(packages, custom_nodes=None, python_exe=None, gpu_present=None):
    """Evaluate the embedded dependency rule table. Pure and side-effect free."""
    if not packages:
        return []
    if gpu_present is None:
        gpu_present = shutil.which("nvidia-smi") is not None
    findings = []
    py = _python_label(python_exe)
    numpy_v = _pkg_version(packages, "numpy")
    torch_v = _pkg_version(packages, "torch")
    numpy_t = _version_tuple(numpy_v)
    torch_t = _version_tuple(torch_v)
    if numpy_t and torch_t and numpy_t[0] >= 2 and torch_t < (2, 3):
        findings.append(_finding(
            "numpy2-torch",
            why="当前 numpy {}，torch {}；numpy 2.x 的 ABI 不能被 torch < 2.3 正常加载。"
                .format(numpy_v, torch_v),
            fix="{py} -m pip install \"numpy<2\"".format(py=py)))
    xf_v = _pkg_version(packages, "xformers")
    xf_t = _version_tuple(xf_v)
    if xf_t and torch_t:
        expected = XFORMERS_TORCH.get(xf_t[:3])
        if expected and (torch_t[0], torch_t[1]) != expected:
            findings.append(_finding(
                "xformers-torch",
                why="当前 xformers {} 配套 torch {}.{}，但实际 torch 是 {}。"
                    .format(xf_v, expected[0], expected[1], torch_v)))
    tv_v = _pkg_version(packages, "torchvision")
    tv_t = _version_tuple(tv_v)
    if tv_t and torch_t:
        expected = TORCHVISION_TORCH.get(tv_t[:2])
        if expected and (torch_t[0], torch_t[1]) != expected:
            findings.append(_finding(
                "torchvision-torch",
                why="当前 torchvision {} 配套 torch {}.{}，但实际 torch 是 {}。"
                    .format(tv_v, expected[0], expected[1], torch_v)))
    tr_v = _pkg_version(packages, "transformers")
    if _version_tuple(tr_v) >= (4, 46):
        findings.append(_finding(
            "transformers-new",
            why="当前 transformers {}，较旧的自定义节点可能需要降级。".format(tr_v),
            fix="{py} -m pip install \"transformers<4.46\"".format(py=py)))
    pyd_v = _pkg_version(packages, "pydantic")
    if _version_tuple(pyd_v) and _version_tuple(pyd_v)[0] >= 2:
        old_nodes = []
        for item in _node_requirement_records(custom_nodes):
            for record in item["records"]:
                if record["name"] == "pydantic" and (
                        "<2" in record["spec"].replace(" ", "")
                        or "==1." in record["spec"].replace(" ", "")):
                    old_nodes.append(item["node"]["name"])
                    break
        if old_nodes:
            findings.append(_finding(
                "pydantic2-old-nodes",
                why="已安装 pydantic {}，但节点 {} 的 requirements 要求 Pydantic 1.x。"
                    .format(pyd_v, "、".join(sorted(set(old_nodes)))),
                fix="{py} -m pip install \"pydantic<2\"".format(py=py)))
    for item in _missing_requirements(custom_nodes, packages):
        shown = "、".join(item["names"][:8])
        suffix = " 等 {} 个".format(len(item["names"])) if len(item["names"]) > 8 else ""
        findings.append(_finding(
            "missing-requirements",
            why="节点 {} 声明了 {}{}，但目标 Python 的 pip list 中没有。"
                .format(item["node"], shown, suffix),
            fix="{py} -m pip install -r \"{req}\"".format(py=py, req=item["path"]),
            node=item["node"]))
    if torch_v and "+cpu" in torch_v.casefold() and gpu_present:
        findings.append(_finding(
            "torch-cpu-on-gpu",
            why="检测到 NVIDIA 驱动，但 torch {} 是 CPU 构建。".format(torch_v)))
    return findings


LOG_RULES = [
    {"id": "numpy-abi", "title": "NumPy ABI 不兼容",
     "pattern": re.compile(r"numpy\.core\.multiarray failed to import|compiled using NumPy 1\.x cannot be run in NumPy 2|_ARRAY_API not found", re.I),
     "cause": "当前 NumPy 2.x 与旧版 torch/自定义节点编译产物 ABI 不兼容。",
     "fix": "{python} -m pip install \"numpy<2\"；若仍失败，再按 torch 版本重装 torch。",
     "confidence": "高"},
    {"id": "dll-missing", "title": "缺少 DLL 或原生模块加载失败",
     "pattern": re.compile(r"\[WinError 126\]|DLL load failed|The specified module could not be found|找不到指定的模块", re.I),
     "cause": "依赖的原生 DLL 缺失、VC++ 运行库未安装，或 torch/xformers 的 CUDA 版本彼此不匹配。",
     "fix": "安装 Microsoft VC++ 2015-2022 Redistributable；再按同一 CUDA 版本重装 torch、torchvision、xformers。",
     "confidence": "中"},
    {"id": "xformers", "title": "xformers wheel 不兼容",
     "pattern": re.compile(r"xformers.*(?:incompatible|undefined symbol|missing|not compatible)|(?:undefined symbol|incompatible).*xformers", re.I),
     "cause": "xformers 的二进制 wheel 与当前 torch/CUDA 版本不匹配。",
     "fix": "{python} -m pip uninstall -y xformers，再安装与 torch 配套的版本；或启动时加 --disable-xformers。",
     "confidence": "中"},
    {"id": "cuda-oom", "title": "显存不足（CUDA out of memory）",
     "pattern": re.compile(r"CUDA out of memory|OutOfMemoryError.*CUDA", re.I),
     "cause": "分辨率、批次数或模型太大，显卡显存不够。",
     "fix": "降低分辨率/批量，关闭其他占显存程序；启动 ComfyUI 时加 --lowvram 或 --medvram。",
     "confidence": "高"},
    {"id": "torch-no-cuda", "title": "torch 不是 CUDA 版",
     "pattern": re.compile(r"Torch not compiled with CUDA enabled|torch.*not compiled with CUDA", re.I),
     "cause": "当前安装的是 CPU 版 torch，无法调用 NVIDIA 显卡。",
     "fix": "安装 CUDA 版 torch（https://pytorch.org/get-started/locally/），并确认没有设置 CUDA_VISIBLE_DEVICES=-1。",
     "confidence": "高"},
    {"id": "module-missing", "title": "Python 模块缺失",
     "pattern": re.compile(r"ModuleNotFoundError:\s*No module named\s+['\"]?([A-Za-z0-9_.-]+)", re.I),
     "cause": "某个节点或 ComfyUI 依赖未装进目标 Python 环境。",
     "fix": "先看 traceback 里 custom_nodes 的节点目录，再对该节点执行 {python} -m pip install -r requirements.txt。",
     "confidence": "高"},
    {"id": "import-failed", "title": "自定义节点导入失败",
     "pattern": re.compile(r"\bIMPORT FAILED\b", re.I),
     "cause": "节点在启动时抛出异常，ComfyUI 已把它标记为导入失败。",
     "fix": "按同一段 traceback 修复该节点依赖，或更新/移除该节点；不要只重复安装 ComfyUI。",
     "confidence": "高"},
    {"id": "cannot-import", "title": "节点 Cannot import 错误",
     "pattern": re.compile(r"Cannot import\b", re.I),
     "cause": "ComfyUI 无法导入该路径下的自定义节点，通常是依赖或脚本错误。",
     "fix": "查看该节点 requirements.txt，并用 ComfyUI 实际使用的 Python 安装；再检查 Python 版本兼容性。",
     "confidence": "高"},
    {"id": "port-busy", "title": "端口被占用",
     "pattern": re.compile(r"address already in use|Only one usage of each socket address|WinError 10048|port 8188.*(?:busy|in use)", re.I),
     "cause": "8188 端口已有另一个 ComfyUI/进程在监听。",
     "fix": "查找占用：netstat -ano | findstr :8188；或换端口启动：python main.py --port 8189。",
     "confidence": "高"},
    {"id": "segfault", "title": "进程崩溃或访问冲突",
     "pattern": re.compile(r"Segmentation fault|0xC0000005|STATUS_ACCESS_VIOLATION|access violation", re.I),
     "cause": "原生扩展、驱动、xformers 或超频不稳定导致进程直接崩溃。",
     "fix": "先用 --cpu 启动排除显卡问题；更新/移除可疑自定义节点，关闭 xformers 后重试。",
     "confidence": "中"},
    {"id": "invalid-model", "title": "模型加载失败",
     "pattern": re.compile(r"Invalid model|Error while loading checkpoint|Error\(s\) in loading state_dict|cannot load (?:model|checkpoint)", re.I),
     "cause": "模型文件不完整、下载被截断、类型不匹配或与当前节点版本不兼容。",
     "fix": "核对文件名和放置目录，重新下载模型；不要用其他类型模型强行改名。",
     "confidence": "中"},
    {"id": "keyerror-input", "title": "节点输入缺少 Key",
     "pattern": re.compile(r"KeyError:\s*['\"]?[A-Za-z0-9_\u4e00-\u9fff .-]+", re.I),
     "cause": "工作流和节点版本不一致，节点缺少预期的输入字段。",
     "fix": "更新对应自定义节点与 ComfyUI 到兼容版本；必要时让工作流作者重新保存一份。",
     "confidence": "中"},
    {"id": "ssl-hf", "title": "SSL / HuggingFace 访问失败",
     "pattern": re.compile(r"SSLError|CERTIFICATE_VERIFY_FAILED|Max retries exceeded|ConnectionError|huggingface\.co.*(?:timed out|connection)|ProxyError", re.I),
     "cause": "网络、代理、证书或 HuggingFace 访问问题，模型/节点下载失败。",
     "fix": "检查代理与系统时间；可设置 HF_ENDPOINT=https://hf-mirror.com 后重试，离线则先手动下载模型。",
     "confidence": "中"},
    {"id": "ffmpeg-missing", "title": "缺少 ffmpeg",
     "pattern": re.compile(r"ffmpeg.*(?:not found|No such file)|FileNotFoundError:.*ffmpeg", re.I),
     "cause": "视频合成节点需要 ffmpeg，但 PATH 中找不到。",
     "fix": "安装 ffmpeg，把 ffmpeg.exe 所在目录加入 PATH，重启终端和 ComfyUI。",
     "confidence": "高"},
]


def _owner_from_line(line):
    match = OWNER_RE.search(line or "")
    if not match:
        return None
    owner = match.group(1).strip().strip("()[]{}<>,;:\"'")
    owner = re.split(r"\s+(?:module for custom nodes|\(IMPORT FAILED\)|IMPORT FAILED)",
                     owner, flags=re.I)[0]
    owner = re.sub(r"\s+\((?:IMPORT FAILED|disabled)\)$", "", owner, flags=re.I)
    return owner.strip() or None


def _owner_near(lines, index):
    for offset in range(0, 61):
        pos = index - offset
        if pos >= 0:
            owner = _owner_from_line(lines[pos])
            if owner:
                return owner
    for offset in range(1, 21):
        pos = index + offset
        if pos < len(lines):
            owner = _owner_from_line(lines[pos])
            if owner:
                return owner
    return None


def _log_cause_fix(rule, line, python_exe):
    py = _python_label(python_exe)
    cause = rule["cause"]
    fix = rule["fix"].replace("{python}", py)
    if rule["id"] == "module-missing":
        match = rule["pattern"].search(line)
        if match:
            module = match.group(1)
            cause = "缺少 Python 模块 '{}'；通常是节点 requirements 未装进目标环境。".format(module)
            fix = ("先确认报错节点：如果模块名就是包名，执行 {} -m pip install {}；"
                   "更稳妥的是进入该节点目录执行 {} -m pip install -r requirements.txt。"
                   .format(py, module, py))
    return cause, fix


def scan_log(text, python_exe=None):
    lines = strip_ansi(text).splitlines()
    hits = []
    seen = set()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        for rule in LOG_RULES:
            if not rule["pattern"].search(line):
                continue
            owner = _owner_from_line(line) or _owner_near(lines, index)
            cleaned = one_line(line)
            key = (rule["id"], cleaned.casefold(), owner or "")
            if key in seen:
                continue
            seen.add(key)
            cause, fix = _log_cause_fix(rule, line, python_exe)
            hits.append({
                "id": rule["id"], "title": rule["title"], "line": cleaned,
                "line_no": index + 1, "owner": owner, "cause": cause, "fix": fix,
                "confidence": rule["confidence"],
            })
            break
        if len(hits) >= 80:
            break
    return hits


def find_newest_log(comfy_root, explicit=None):
    if explicit:
        path = _clean_path(explicit)
        if not path.is_file():
            return {"path": str(path), "source": "--log 参数",
                    "error": "--log 指定的文件不存在：{}".format(path), "count": 0}
        return {"path": str(path), "source": "--log 参数", "error": None, "count": 1}
    candidates = []
    seen = set()

    def add(path, source):
        path = Path(path)
        key = os.path.normcase(str(path))
        if key in seen or not path.is_file():
            return
        seen.add(key)
        try:
            candidates.append((path.stat().st_mtime, path, source))
        except OSError:
            pass

    if comfy_root:
        root = Path(comfy_root)
        for folder, source in [(root / "user", "ComfyUI/user"),
                               (root, "ComfyUI 根目录")]:
            if folder.is_dir():
                try:
                    for entry in folder.iterdir():
                        low = entry.name.lower()
                        if low.startswith("comfyui") and low.endswith((".log", ".txt")):
                            add(entry, source)
                except OSError:
                    pass
    temp_dirs = []
    for key in ("TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            temp_dirs.append(value)
    temp_dirs.append(tempfile.gettempdir())
    for value in sorted(set(temp_dirs)):
        try:
            for entry in Path(value).iterdir():
                low = entry.name.lower()
                if "comfy" in low and low.endswith((".log", ".txt")):
                    add(entry, "临时目录")
        except OSError:
            pass
    if not candidates:
        return {"path": None, "source": None, "error": None, "count": 0}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return {"path": str(candidates[0][1]), "source": candidates[0][2],
            "error": None, "count": len(candidates)}


def failed_dirs_from_hits(hits):
    failed = set()
    for hit in hits:
        if hit.get("owner") and hit.get("id") in IMPORT_RULE_IDS:
            failed.add(_canonical_name(hit["owner"]))
    return failed


def default_cache_path():
    return Path.home() / CACHE_DIR_NAME / "node-map.json"


def parse_node_map(data):
    class_index = {}
    repo_count = 0
    if not isinstance(data, dict):
        return {"class_index": {}, "repo_count": 0, "class_count": 0}
    for repo, value in data.items():
        if not isinstance(repo, str) or not isinstance(value, list) or not value:
            continue
        classes = value[0]
        if not isinstance(classes, list):
            continue
        meta = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
        title = meta.get("title_aux") or _repo_basename(repo)
        repo_count += 1
        for class_name in classes:
            if isinstance(class_name, str) and class_name:
                class_index[class_name] = {"repo": repo, "title": title}
    return {"class_index": class_index, "repo_count": repo_count,
            "class_count": len(class_index)}


def _read_node_map(path):
    try:
        data = json.loads(read_text_file(path))
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "节点表根节点不是对象"
    return data, None


def _local_manager_map(comfy_root):
    if not comfy_root:
        return None
    base = Path(comfy_root) / "custom_nodes"
    if not base.is_dir():
        return None
    try:
        for entry in base.iterdir():
            if entry.is_dir() and _canonical_name(entry.name) == "comfyui-manager":
                candidate = entry / "extension-node-map.json"
                if candidate.is_file():
                    return candidate
    except OSError:
        pass
    return None


def load_node_map(comfy_root, refresh=False, offline=False, timeout=15):
    """Load class->repo map with local-manager fallback, TTL cache and no crashes."""
    cache_path = default_cache_path()
    warnings = []
    local = _local_manager_map(comfy_root)

    def result(path, source, available=True, stale=False, data=None):
        parsed = parse_node_map(data or {})
        return {"class_index": parsed["class_index"], "repo_count": parsed["repo_count"],
                "class_count": parsed["class_count"], "source": source,
                "available": available, "cache_path": str(cache_path),
                "local_path": str(local) if local else None,
                "warnings": warnings, "stale": stale}

    if offline:
        if local:
            data, error = _read_node_map(local)
            if data is not None:
                return result(local, "本地 ComfyUI-Manager 节点表", True, False, data)
            warnings.append("本地节点表解析失败：{}".format(error))
        if cache_path.is_file():
            data, error = _read_node_map(cache_path)
            if data is not None:
                warnings.append("离线模式：不发起网络请求，使用本地缓存。")
                return result(cache_path, "本地缓存（离线模式）", True, True, data)
            warnings.append("本地节点缓存损坏：{}".format(error))
        warnings.append("未联网，节点归属未知。")
        return result(None, "未联网（离线模式）", False, False, {})

    if cache_path.is_file() and not refresh:
        age = time.time() - cache_path.stat().st_mtime
        if age <= NODE_MAP_TTL:
            data, error = _read_node_map(cache_path)
            if data is not None:
                days = int(age // 86400)
                return result(cache_path, "本地缓存（{} 天前）".format(days), True, False, data)
            warnings.append("节点表缓存损坏：{}".format(error))

    if not offline:
        try:
            request = urllib.request.Request(NODE_MAP_URL, headers={
                "User-Agent": "comfy-emergency/{}".format(VERSION)})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            data = json.loads(decode_bytes(raw))
            if not isinstance(data, dict):
                raise ValueError("根节点不是对象")
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = cache_path.with_suffix(".tmp")
                temp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.replace(str(temp_path), str(cache_path))
                source = "在线节点表（已缓存）"
            except OSError as exc:
                warnings.append("节点表已获取，但缓存写入失败：{}".format(exc))
                source = "在线节点表（未缓存）"
            return result(None, source, True, False, data)
        except Exception as exc:
            warnings.append("节点表联网获取失败（{}），改用本地数据。".format(one_line(exc)))

    if local:
        data, error = _read_node_map(local)
        if data is not None:
            warnings.append("使用 ComfyUI-Manager 本地节点表。")
            return result(local, "本地 ComfyUI-Manager 节点表", True, False, data)
        warnings.append("本地节点表解析失败：{}".format(error))
    if cache_path.is_file():
        data, error = _read_node_map(cache_path)
        if data is not None:
            warnings.append("使用过期的本地节点表缓存。")
            return result(cache_path, "过期本地缓存（离线降级）", True, True, data)
        warnings.append("本地节点缓存损坏：{}".format(error))
    warnings.append("未联网，节点归属未知。")
    return result(None, "未联网（节点表不可用）", False, False, {})


def index_installed(custom_nodes):
    by_repo = {}
    by_name = {}
    for node in custom_nodes:
        by_name[_canonical_name(node["name"])] = node
        if node["disabled"]:
            by_name.setdefault(_canonical_name(node["name"][:-9]), node)
        for url in node.get("urls", []):
            by_repo[_repo_id(url)] = node
    return {"by_repo": by_repo, "by_name": by_name}


def find_installed_repo(installed, repo_url):
    if not installed:
        return None
    node = installed["by_repo"].get(_repo_id(repo_url))
    if node:
        return node
    return installed["by_name"].get(_canonical_name(_repo_basename(repo_url)))


def _is_model_file(value):
    return isinstance(value, str) and value.strip().casefold().endswith(MODEL_EXTS)


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            for text in _iter_strings(item):
                yield text
    elif isinstance(value, (list, tuple)):
        for item in value:
            for text in _iter_strings(item):
                yield text


def _input_dir(key, node_type):
    if key in INPUT_DIRS:
        return INPUT_DIRS[key]
    spec = LOADER_SPECS.get(node_type)
    if spec:
        return spec[0]
    if key.endswith("_name"):
        return key[:-5].replace("_", " ")
    return "未知"


def _add_model(bucket, name, directory, source, node_type, origin):
    if not isinstance(name, str):
        return
    name = name.strip()
    if not name or name.casefold() in ("none", "null", "undefined"):
        return
    directory = (directory or "未知").strip()
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    key = (directory.casefold(), basename.casefold())
    if key not in bucket:
        bucket[key] = {"name": name, "basename": basename, "directory": directory,
                       "sources": [], "node_types": [], "origin": origin}
    entry = bucket[key]
    for value, target in ((source, entry["sources"]), (node_type, entry["node_types"])):
        if value and value not in target:
            target.append(value)


def _collect_ui(doc):
    required = set()
    models = {}
    definitions = doc.get("definitions") if isinstance(doc.get("definitions"), dict) else {}
    subgraphs = definitions.get("subgraphs") if isinstance(definitions.get("subgraphs"), list) else []
    if not subgraphs and isinstance(doc.get("subgraphs"), list):
        subgraphs = doc["subgraphs"]
    skip_types = set()
    for subgraph in subgraphs:
        if isinstance(subgraph, dict):
            if subgraph.get("id"):
                skip_types.add(str(subgraph["id"]))
            if subgraph.get("name"):
                skip_types.add(str(subgraph["name"]))
    groups = [doc.get("nodes") or []]
    for subgraph in subgraphs:
        if isinstance(subgraph, dict):
            groups.append(subgraph.get("nodes") or [])
    for nodes in groups:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("mode") in (2, 4):
                continue
            node_type = str(node.get("type") or "")
            if (not node_type or node_type in skip_types or node_type in VIRTUAL_NODES
                    or node_type.startswith("workflow>")):
                continue
            required.add(node_type)
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            listed = props.get("models") if isinstance(props.get("models"), list) else []
            for item in listed:
                if not isinstance(item, dict):
                    continue
                _add_model(models, item.get("name"), item.get("directory"),
                           "properties.models", node_type, "ui")
            widgets = node.get("widgets_values")
            spec = LOADER_SPECS.get(node_type)
            if spec and isinstance(widgets, list):
                for index in spec[1]:
                    if index < len(widgets):
                        _add_model(models, widgets[index], spec[0], "加载器控件",
                                   node_type, "ui")
            for value in _iter_strings(widgets):
                if _is_model_file(value):
                    _add_model(models, value,
                               spec[0] if spec else _input_dir("", node_type),
                               "模型扩展名", node_type, "ui")
    return {"format": "UI", "required": required, "models": models,
            "subgraphs": len(subgraphs), "skip_types": skip_types}


def _collect_api(doc):
    payload = doc
    if isinstance(doc.get("prompt"), dict):
        payload = doc["prompt"]
    required = set()
    models = {}
    for node in payload.values():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        node_type = str(node.get("class_type") or "")
        if (not node_type or node_type in VIRTUAL_NODES
                or node_type.startswith("workflow>")):
            continue
        required.add(node_type)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for key, value in inputs.items():
            for text in _iter_strings(value):
                if _is_model_file(text) or key in INPUT_DIRS:
                    _add_model(models, text, _input_dir(key, node_type),
                               "API inputs", node_type, "api")
    return {"format": "API", "required": required, "models": models,
            "subgraphs": 0, "skip_types": set()}


def _looks_api(doc):
    if not isinstance(doc, dict):
        return False
    if isinstance(doc.get("prompt"), dict):
        return True
    return any(isinstance(value, dict) and "class_type" in value for value in doc.values())


def parse_workflow_doc(doc):
    if not isinstance(doc, dict):
        raise ValueError("JSON 根节点不是对象")
    if isinstance(doc.get("nodes"), list):
        return _collect_ui(doc)
    if _looks_api(doc):
        return _collect_api(doc)
    raise ValueError("既不是 UI 工作流（需要 nodes 数组），也不是 API 工作流（需要 class_type）")


def load_workflow_files(path):
    path = Path(path)
    if not path.exists():
        raise ValueError("工作流路径不存在：{}".format(path))
    files = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        for entry in sorted(path.rglob("*.json"), key=lambda p: str(p).lower()):
            if any(part.startswith(".") for part in entry.parts):
                continue
            files.append(entry)
            if len(files) >= 300:
                break
    if not files:
        raise ValueError("目录里没有找到 JSON 工作流：{}".format(path))
    docs = []
    warnings = []
    for file_path in files:
        try:
            doc = json.loads(read_text_file(file_path))
        except (OSError, ValueError) as exc:
            if path.is_file():
                raise ValueError("无法读取工作流 JSON：{}".format(exc))
            warnings.append("跳过无法解析的文件 {}：{}".format(file_path.name, one_line(exc)))
            continue
        try:
            collection = parse_workflow_doc(doc)
        except ValueError as exc:
            if path.is_file():
                raise ValueError(str(exc))
            warnings.append("跳过 {}：{}".format(file_path.name, exc))
            continue
        docs.append({"path": str(file_path), "collection": collection})
    if not docs:
        raise ValueError("没有解析到有效工作流：{}".format(path))
    return {"path": str(path), "is_dir": path.is_dir(), "docs": docs,
            "files": [item["path"] for item in docs], "warnings": warnings}


def _workflow_warning_checks(warnings):
    return list(dict.fromkeys(warnings))


def _find_installed_failure(node, failed_dirs):
    variants = [_canonical_name(node["name"]),
                _canonical_name(node["name"][:-9] if node["name"].lower().endswith(".disabled") else node["name"])]
    return any(name in failed_dirs for name in variants)


def build_missing_nodes(required, node_map, installed, failed_dirs):
    missing = []
    offline_unknown = not node_map.get("available")
    for class_name in sorted(required):
        if class_name in CORE_NODES or class_name in VIRTUAL_NODES:
            continue
        match = node_map.get("class_index", {}).get(class_name)
        if not match:
            if offline_unknown:
                reason = "未联网，节点归属未知。请联网刷新节点表，或按类名搜索对应扩展。"
                status = "无法判定"
            else:
                reason = "节点归属表未收录此节点；可能是新扩展、私有节点或类名已改名。"
                status = "否（节点归属未知）"
            missing.append({"class": class_name, "repo": None, "repo_url": None,
                            "repo_title": None, "installed": None,
                            "import_failed": False, "status": status,
                            "reason": reason})
            continue
        repo_url = match["repo"]
        repo_title = match.get("title") or _repo_basename(repo_url)
        node = find_installed_repo(installed, repo_url)
        if node and not node["disabled"] and not _find_installed_failure(node, failed_dirs):
            continue
        import_failed = bool(node and _find_installed_failure(node, failed_dirs))
        if node and node["disabled"]:
            status = "否（节点目录已被禁用）"
            reason = "节点目录被改成了 .disabled，ComfyUI 不会加载它；恢复目录名即可。"
        elif import_failed:
            status = "是（启动日志显示导入失败）"
            reason = "节点已安装但启动时导入失败，通常是缺少依赖或版本不兼容。"
        else:
            status = "否（仓库未安装）"
            reason = "缺少节点扩展；按修复计划安装后重启 ComfyUI。"
        missing.append({"class": class_name, "repo": repo_title,
                        "repo_url": repo_url, "repo_title": repo_title,
                        "installed": bool(node), "import_failed": import_failed,
                        "installed_dir": node["name"] if node else None,
                        "installed_path": node["path"] if node else None,
                        "status": status, "reason": reason})
    return missing


def model_presence(models, inventory):
    missing = []
    for item in models.values():
        basename = item["basename"].casefold()
        name_path = item["name"].replace("\\", "/").casefold()
        present = basename in inventory["names"] or name_path in inventory["rels"]
        if present:
            continue
        directory = item["directory"] or "未知"
        missing.append({"name": item["name"], "filename": item["basename"],
                        "directory": directory,
                        "expected": "models/" + directory if directory != "未知" else "models/<按文件名查找>",
                        "sources": item["sources"], "node_types": item["node_types"],
                        "note": "请自行下载（不提供模型文件）"})
    missing.sort(key=lambda value: (value["directory"].casefold(), value["filename"].casefold()))
    return missing


def _build_fix_plan(missing_nodes, missing_models, comfy_root, python_info, node_map):
    plan = {"clone_commands": [], "pip_commands": [], "manual_nodes": [],
            "model_downloads": missing_models, "notes": [],
            "custom_nodes_path": None, "python": None, "effective_python": None}
    if not comfy_root:
        plan["notes"].append("未找到 ComfyUI，无法生成安装目录；请先加 --comfy 指定。")
        plan["notes"].append("模型缺失只给出文件名与相对目录，不自动下载。")
        return plan
    custom_nodes = Path(comfy_root) / "custom_nodes"
    plan["custom_nodes_path"] = str(custom_nodes)
    python_path = python_info.get("path") if python_info else None
    effective = None
    if python_path and not python_info.get("fallback"):
        effective = python_path
        plan["effective_python"] = effective
        plan["python"] = effective
    else:
        plan["notes"].append("未找到 ComfyUI 专用 Python，安装命令里使用占位符。")
    py = _python_label(effective)
    seen_repos = set()
    for item in missing_nodes:
        repo_url = item.get("repo_url")
        if item.get("installed"):
            node_name = item.get("installed_dir") or item.get("repo_title")
            node_path = item.get("installed_path")
            req = Path(node_path) / "requirements.txt" if node_path else custom_nodes / str(node_name) / "requirements.txt"
            if req.is_file():
                plan["pip_commands"].append("{} -m pip install -r \"{}\"".format(py, req))
                plan["manual_nodes"].append(
                    "{}：已安装但导入失败，请先修复依赖并重启。".format(node_name))
            else:
                plan["manual_nodes"].append(
                    "{}：已安装但导入失败，且没有 requirements.txt；请查看启动日志或重装该节点。"
                    .format(node_name))
            continue
        if not repo_url:
            plan["manual_nodes"].append("{}：所属仓库未知，无法生成精确 git clone。"
                                         .format(item["class"]))
            continue
        if repo_url in seen_repos:
            continue
        seen_repos.add(repo_url)
        repo_name = _repo_basename(repo_url)
        target = custom_nodes / repo_name
        plan["clone_commands"].append("git clone \"{}\" \"{}\"".format(repo_url, target))
        plan["pip_commands"].append(
            "{} -m pip install -r \"{}\"".format(py, target / "requirements.txt"))
        plan["manual_nodes"].append(
            "{}：git clone 后先看 requirements.txt，再执行上面的 pip 命令。".format(repo_name))
    if not plan["clone_commands"] and not plan["pip_commands"] and not plan["manual_nodes"]:
        plan["notes"].append("没有可自动安装的节点。")
    if missing_models:
        plan["notes"].append("模型必须由你自行下载，本工具不会请求或写入模型文件。")
    return plan


def analyze_workflow(path, comfy_arg=None, offline=False, refresh_map=False):
    loaded = load_workflow_files(path)
    location = locate_comfy(comfy_arg)
    if comfy_arg and location.get("error"):
        raise ValueError(location["error"])
    comfy_root = location.get("path")
    python_info = detect_python(comfy_root)
    node_map = load_node_map(comfy_root, refresh=refresh_map, offline=offline)
    custom = list_custom_nodes(comfy_root) if comfy_root else []
    installed = index_installed(custom)
    inventory = scan_models(comfy_root) if comfy_root else {
        "available": False, "count": 0, "names": set(), "rels": set()}
    log_info = find_newest_log(comfy_root)
    log_hits = []
    if log_info.get("path") and not log_info.get("error"):
        try:
            log_hits = scan_log(read_text_file(log_info["path"]), python_info.get("path"))
        except OSError:
            log_hits = []
    failed_dirs = failed_dirs_from_hits(log_hits)
    required = set()
    all_models = {}
    formats = []
    subgraphs = 0
    warnings = list(location.get("warnings", []))
    warnings.extend(loaded["warnings"])
    warnings.extend(node_map.get("warnings", []))
    for entry in loaded["docs"]:
        collection = entry["collection"]
        required.update(collection["required"])
        for key, value in collection["models"].items():
            if key in all_models:
                for source in value["sources"]:
                    if source not in all_models[key]["sources"]:
                        all_models[key]["sources"].append(source)
                for node_type in value["node_types"]:
                    if node_type not in all_models[key]["node_types"]:
                        all_models[key]["node_types"].append(node_type)
            else:
                all_models[key] = value
        if collection["format"] not in formats:
            formats.append(collection["format"])
        subgraphs += collection["subgraphs"]
    if not comfy_root:
        warnings.append("没有找到 ComfyUI，模型库存与启动日志无法检查；可用 --comfy 指定根目录。")
    if inventory["available"]:
        inventory_state = "已读取 models/，共 {} 个文件".format(inventory["count"])
    else:
        inventory_state = "未找到 models/，缺失模型均按未安装展示"
    missing_nodes = build_missing_nodes(required, node_map, installed, failed_dirs)
    missing_models = model_presence(all_models, inventory)
    plan = _build_fix_plan(missing_nodes, missing_models, comfy_root,
                           python_info, node_map)
    if not node_map.get("available"):
        warnings.append("未联网，节点归属未知。")
    if offline:
        warnings.append("离线模式：未发起任何网络请求。")
    warnings = _workflow_warning_checks(warnings)
    required_custom = [name for name in sorted(required)
                       if name not in CORE_NODES and name not in VIRTUAL_NODES]
    return {
        "tool": TOOL, "version": VERSION, "path": loaded["path"],
        "files": loaded["files"], "is_dir": loaded["is_dir"],
        "format": "/".join(formats) if formats else "未知",
        "subgraphs": subgraphs,
        "comfy": {"path": comfy_root, "source": location.get("source"),
                  "python": python_info.get("path"), "python_label": python_info.get("label"),
                  "python_fallback": python_info.get("fallback")},
        "node_map": {"source": node_map.get("source"),
                     "available": node_map.get("available"),
                     "class_count": node_map.get("class_count"),
                     "cache_path": node_map.get("cache_path")},
        "node_counts": {"required": len(required), "core": len(required) - len(required_custom),
                        "custom": len(required_custom),
                        "missing": len(missing_nodes)},
        "missing_nodes": missing_nodes,
        "missing_models": missing_models,
        "model_inventory": {"available": inventory["available"],
                            "count": inventory["count"], "summary": inventory_state},
        "startup_failures": sorted(failed_dirs),
        "log": {"path": log_info.get("path"), "error": log_info.get("error"),
                "hits": len(log_hits)},
        "fix_plan": plan, "warnings": warnings,
    }


def build_doctor_report(comfy_arg=None, log_arg=None):
    location = locate_comfy(comfy_arg)
    data = {"tool": TOOL, "version": VERSION,
            "comfy": {"path": None, "source": None, "error": location.get("error")},
            "searched": location.get("searched", []), "python": None,
            "custom_nodes": [], "model_count": 0, "models_available": False,
            "extra_model_paths": None, "pip": {"ok": False, "error": None, "count": 0},
            "rules": [], "log": None, "log_hits": [], "failed_nodes": [],
            "conclusion": "", "next_steps": [], "warnings": list(location.get("warnings", [])),
            "read_only": True}
    if not location.get("path"):
        data["conclusion"] = "没找到 ComfyUI。请用 --comfy 指向包含 main.py、comfy、custom_nodes 的目录。"
        data["next_steps"] = [
            "找到 ComfyUI 根目录（里面应有 main.py、comfy、custom_nodes 三个东西）。",
            "运行：python comfy_er.py doctor --comfy \"D:\\ComfyUI\"",
            "秋叶整合包通常填：...\\ComfyUI-aki-v1.4\\ComfyUI",
            "如果 ComfyUI 能启动但日志不在默认位置，再追加 --log \"完整日志路径\"。",
        ]
        return data
    comfy_root = location["path"]
    data["comfy"] = {"path": comfy_root, "source": location.get("source"), "error": None}
    data["python"] = detect_python(comfy_root)
    custom = list_custom_nodes(comfy_root)
    data["custom_nodes"] = custom
    inventory = scan_models(comfy_root)
    data["model_count"] = inventory["count"]
    data["models_available"] = inventory["available"]
    extra = read_extra_model_paths(Path(comfy_root) / "extra_model_paths.yaml")
    data["extra_model_paths"] = extra
    pip = pip_list(data["python"].get("path"))
    data["pip"] = {"ok": not pip["error"], "error": pip["error"],
                   "count": len(pip["packages"])}
    rules = evaluate_rules(pip["packages"], custom,
                           data["python"].get("path"),
                           gpu_present=shutil.which("nvidia-smi") is not None)
    data["rules"] = rules
    log_info = find_newest_log(comfy_root, log_arg)
    log_data = {"path": log_info.get("path"), "source": log_info.get("source"),
                "error": log_info.get("error"), "hits": []}
    if log_info.get("path") and not log_info.get("error"):
        try:
            hits = scan_log(read_text_file(log_info["path"]), data["python"].get("path"))
            log_data["hits"] = hits
            data["log_hits"] = hits
            data["failed_nodes"] = sorted(failed_dirs_from_hits(hits))
        except OSError as exc:
            log_data["error"] = "读取日志失败：{}".format(exc)
    data["log"] = log_data
    errors = [rule for rule in rules if rule["severity"] == "error"]
    warnings_ = [rule for rule in rules if rule["severity"] == "warn"]
    if errors:
        conclusion = "发现 {} 个会直接导致启动/加载失败的问题。".format(len(errors))
    elif data["failed_nodes"]:
        conclusion = "发现 {} 个自定义节点导入失败，ComfyUI 可能能启动但工作流会缺节点。".format(
            len(data["failed_nodes"]))
    elif warnings_:
        conclusion = "没有致命错误，但存在 {} 条依赖风险，建议先处理再装新节点。".format(
            len(warnings_))
    elif pip["error"]:
        conclusion = "ComfyUI 目录已找到，但环境依赖无法读取，请先修复 Python/pip。"
    else:
        conclusion = "没有发现明显的依赖冲突或导入失败。"
    data["conclusion"] = conclusion
    steps = []
    if pip["error"]:
        steps.append("检查 Python 是否可用：\"{}\" -m pip --version".format(
            data["python"].get("path")))
    for rule in (errors + warnings_)[:3]:
        steps.append("{}：{}".format(rule["title"], rule["fix"]))
    for node in data["failed_nodes"][:3]:
        steps.append("查看节点 {} 的导入 traceback，先修依赖再重启 ComfyUI。".format(node))
    steps.append("用工作流做一次检查：python comfy_er.py workflow \"你的工作流.json\" --comfy \"{}\"".format(comfy_root))
    if not steps:
        steps.append("环境检查通过。下一步检查工作流缺失节点和模型。")
    data["next_steps"] = steps
    if not data["models_available"]:
        data["warnings"].append("没有找到 models/ 目录，模型数量显示为 0。")
    return data


def render_doctor(data):
    say("{} v{}".format(TOOL, VERSION))
    say("=" * 64)
    if not data["comfy"].get("path"):
        say("[定位] 未找到 ComfyUI")
        if data["comfy"].get("error"):
            say("  {}".format(data["comfy"]["error"]))
        say("  已尝试：显式路径、COMFYUI_PATH、当前目录及浅层子目录。")
        say("")
        say("结论：{}".format(data["conclusion"]))
        say("")
        say("下一步：")
        for index, step in enumerate(data["next_steps"], 1):
            say("  {}. {}".format(index, step))
        say("")
        say("只读模式：本次运行没有修改任何文件或环境。")
        return
    say("[ComfyUI] {}".format(data["comfy"]["path"]))
    say("  来源：{}".format(data["comfy"].get("source") or "未知"))
    python = data["python"]
    say("[Python] {}".format(python.get("path")))
    say("  类型：{}".format(python.get("label")))
    if python.get("fallback"):
        say("  注意：未找到 ComfyUI 专用 Python，依赖检查结果可能不是它实际使用的环境。")
    say("[自定义节点] {} 个".format(len(data["custom_nodes"])))
    for node in data["custom_nodes"][:60]:
        mark = "（已禁用）" if node["disabled"] else ""
        req = "，含 requirements.txt" if node["requirements"] else ""
        say("  - {}{}{}".format(node["name"], mark, req))
    if len(data["custom_nodes"]) > 60:
        say("  ... 其余 {} 个省略".format(len(data["custom_nodes"]) - 60))
    say("[模型库存] {} 个文件".format(data["model_count"]))
    extra = data["extra_model_paths"]
    if extra and extra.get("present"):
        sections = extra.get("sections", [])
        say("[extra_model_paths.yaml] 存在，{} 个配置段".format(len(sections)))
        for section in sections[:6]:
            base = section.get("base_path") or "（未设置 base_path）"
            say("  - {}：{}；路径键 {}".format(
                section.get("name"), base, "、".join(section.get("paths", {}).keys()) or "无"))
    else:
        say("[extra_model_paths.yaml] 不存在")
    say("")
    say("[环境依赖]")
    if not data["pip"]["ok"]:
        say("  无法读取 pip list：{}".format(data["pip"].get("error") or "未知错误"))
    elif not data["rules"]:
        say("  已检查 {} 个包，规则表未发现冲突。".format(data["pip"]["count"]))
    else:
        for finding in data["rules"]:
            say("  [{}] {} ({})".format(SEVERITY_CN.get(finding["severity"], finding["severity"]),
                                       finding["title"], finding["id"]))
            say("     为什么：{}".format(finding["why"]))
            say("     怎么修：{}".format(finding["fix"]))
            say("     可信度：{}".format(finding["confidence"]))
    say("")
    say("[启动日志]")
    log = data.get("log") or {}
    if log.get("error"):
        say("  {}".format(log["error"]))
    elif not log.get("path"):
        say("  未找到启动日志（这不影响其他检查）。")
        say("  可手动指定：--log \"%USERPROFILE%\\...\\comfyui.log\"")
    else:
        say("  {}".format(log["path"]))
        if log.get("source"):
            say("  来源：{}".format(log["source"]))
        if not data["log_hits"]:
            say("  未匹配到已知导入错误。")
        else:
            say("  命中 {} 个已知模式：".format(len(data["log_hits"])))
            for hit in data["log_hits"][:12]:
                say("  - [{}] {}".format(hit["title"], hit["line"]))
                if hit.get("owner"):
                    say("    疑似节点目录：{}".format(hit["owner"]))
            if len(data["log_hits"]) > 12:
                say("  ... 另外 {} 条见 --json".format(len(data["log_hits"]) - 12))
    say("")
    say("结论：{}".format(data["conclusion"]))
    say("")
    say("下一步：")
    for index, step in enumerate(data["next_steps"], 1):
        say("  {}. {}".format(index, step))
    say("")
    say("只读模式：本次运行没有修改任何文件或环境。")


def _model_table_lines(missing_models):
    lines = ["  文件名 | 期望子目录 | 处理"]
    for model in missing_models:
        lines.append("  {} | {} | {}".format(
            model["name"], model["expected"], model["note"]))
    return lines


def render_workflow(data):
    say("{} v{} · 工作流检查".format(TOOL, VERSION))
    say("=" * 64)
    if data["is_dir"]:
        say("[工作流] {}（目录，解析 {} 个工作流 JSON）".format(data["path"], len(data["files"])))
    else:
        say("[工作流] {}".format(data["path"]))
    format_label = data["format"]
    if data.get("subgraphs"):
        format_label += "（含 {} 个子图）".format(data["subgraphs"])
    say("[格式] {}".format(format_label))
    if data["comfy"].get("path"):
        say("[ComfyUI] {}".format(data["comfy"]["path"]))
        say("[Python] {}".format(data["comfy"].get("python")))
    else:
        say("[ComfyUI] 未找到（仍可检查节点名和模型名；用 --comfy 指定根目录）")
    say("[节点归属] {}".format(data["node_map"].get("source") or "未知"))
    counts = data["node_counts"]
    say("[节点统计] 共 {} 种；内置 {} 种；待确认自定义 {} 种".format(
        counts["required"], counts["core"], counts["custom"]))
    say("[模型库存] {}".format(data["model_inventory"]["summary"]))
    say("")
    say("缺失节点（{} 个）".format(len(data["missing_nodes"])))
    if not data["missing_nodes"]:
        say("  未发现缺失节点。")
    for index, node in enumerate(data["missing_nodes"], 1):
        say("  {}. {}".format(index, node["class"]))
        if node["repo_url"]:
            repo = node["repo"] or _repo_basename(node["repo_url"])
            say("     所属仓库：{} ({})".format(repo, node["repo_url"]))
        elif node["repo"]:
            say("     所属仓库：{}".format(node["repo"]))
        else:
            say("     所属仓库：未知（未联网，节点归属未知）")
        say("     是否已装但导入失败：{}".format(node["status"]))
        say("     说明：{}".format(node["reason"]))
    say("")
    say("缺失模型（{} 个）".format(len(data["missing_models"])))
    if not data["missing_models"]:
        say("  未发现缺失模型。")
    else:
        for line in _model_table_lines(data["missing_models"]):
            say(line)
        say("  说明：模型只按文件名在 models/ 下比对；存在同名文件但也可能放错子目录。")
    say("")
    say("修复计划（只读预演，尚未执行）")
    plan = data["fix_plan"]
    step = 1
    if plan["clone_commands"]:
        say("  节点安装命令：")
        for command in plan["clone_commands"]:
            say("    {}. {}".format(step, command))
            step += 1
    if plan["pip_commands"]:
        say("  依赖安装命令：")
        for command in plan["pip_commands"]:
            say("    {}. {}".format(step, command))
            step += 1
    for note in plan["manual_nodes"]:
        say("  - {}".format(note))
    if not plan["clone_commands"] and not plan["pip_commands"] and not plan["manual_nodes"]:
        say("  没有可自动执行的节点安装项。")
    for note in plan["notes"]:
        say("  - {}".format(note))
    if data["missing_models"]:
        say("  模型：请自行下载（不提供模型文件），放到上面的期望子目录。")
    say("")
    if data["warnings"]:
        say("提示：")
        for warning in data["warnings"]:
            say("  - {}".format(warning))
        say("")
    if plan["clone_commands"] or plan["pip_commands"]:
        if data["comfy"].get("python_fallback"):
            say("当前没有 ComfyUI 专属 Python，--apply 会被拒绝；请先用 --comfy 指向完整安装目录。")
        else:
            say("确认无误后可执行：python comfy_er.py workflow \"{}\" --apply".format(data["path"]))
    say("本次运行未修改任何环境；--apply 是唯一写入路径，且会先保存 pip freeze 快照。")
    if data["missing_models"]:
        say("本工具永远不会自动下载模型。")


def _markdown_escape(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_report(doctor, workflow=None):
    lines = [
        "# ComfyUI 诊断报告",
        "",
        "> 请把这份报告整段复制到群里，或发给熟悉 ComfyUI 的大佬。",
        "> 报告只含环境摘要、缺失项和错误片段，不含模型文件、账号或密钥。",
        "",
        "## 环境摘要",
        "",
        "- 工具：{} v{}（只读诊断）".format(TOOL, VERSION),
    ]
    if doctor["comfy"].get("path"):
        lines.append("- ComfyUI：`{}`".format(doctor["comfy"]["path"]))
        python = doctor.get("python") or {}
        lines.append("- Python：`{}`（{}）".format(
            python.get("path"), python.get("label")))
        lines.append("- 自定义节点：{} 个".format(len(doctor["custom_nodes"])))
        lines.append("- 模型文件：{} 个".format(doctor["model_count"]))
        extra = doctor.get("extra_model_paths") or {}
        lines.append("- extra_model_paths.yaml：{}".format(
            "存在" if extra.get("present") else "不存在"))
    else:
        lines.append("- ComfyUI：未找到（{}）".format(
            doctor["comfy"].get("error") or "没有提供可用路径"))
    lines.append("- 结论：{}".format(doctor["conclusion"]))
    lines.extend(["", "## 缺失节点", ""])
    if workflow is None:
        lines.append("未提供工作流；使用 `--workflow 文件.json` 可补充检查。")
    elif not workflow["missing_nodes"]:
        lines.append("没有发现缺失节点。")
    else:
        lines.append("| 节点类名 | 所属仓库 | 已装但导入失败 | 说明 |")
        lines.append("|---|---|---|---|")
        for node in workflow["missing_nodes"]:
            repo = node["repo_url"] or (node["repo"] or "未知（未联网，节点归属未知）")
            lines.append("| `{}` | {} | {} | {} |".format(
                _markdown_escape(node["class"]), _markdown_escape(repo),
                _markdown_escape(node["status"]), _markdown_escape(node["reason"])))
    lines.extend(["", "## 缺失模型", ""])
    if workflow is None:
        lines.append("未提供工作流。")
    elif not workflow["missing_models"]:
        lines.append("没有发现缺失模型。")
    else:
        lines.append("| 文件名 | 期望子目录 | 处理 |")
        lines.append("|---|---|---|")
        for model in workflow["missing_models"]:
            lines.append("| `{}` | `{}` | {} |".format(
                _markdown_escape(model["name"]), _markdown_escape(model["expected"]),
                _markdown_escape(model["note"])))
    lines.extend(["", "## 冲突规则", ""])
    if not doctor["rules"]:
        lines.append("规则表未命中。")
    else:
        lines.append("| 级别 | 规则 | 原因 | 修复命令 | 可信度 |")
        lines.append("|---|---|---|---|---|")
        for rule in doctor["rules"]:
            lines.append("| {} | {} | {} | {} | {} |".format(
                SEVERITY_CN.get(rule["severity"], rule["severity"]),
                _markdown_escape(rule["title"]), _markdown_escape(rule["why"]),
                _markdown_escape(rule["fix"]), rule["confidence"]))
    lines.extend(["", "## 日志命中", ""])
    hits = doctor.get("log_hits") or []
    if not hits:
        lines.append("没有匹配到已知错误模式，或没有提供启动日志。")
    else:
        for hit in hits:
            owner = "（疑似节点：{}）".format(hit["owner"]) if hit.get("owner") else ""
            lines.append("- `{}`{}".format(_markdown_escape(hit["line"]), owner))
            lines.append("  - 原因：{}".format(_markdown_escape(hit["cause"])))
            lines.append("  - 修复：{}".format(_markdown_escape(hit["fix"])))
            lines.append("  - 可信度：{}".format(hit["confidence"]))
    lines.extend(["", "## 下一步", ""])
    for step in doctor.get("next_steps", []):
        lines.append("- {}".format(step))
    lines.append("")
    lines.append("---")
    lines.append("{} v{} · 单文件、零依赖、默认只读".format(TOOL, VERSION))
    lines.append("项目地址：{}".format(REPO_URL))
    return "\n".join(lines) + "\n"


def render_log(data):
    say("{} v{} · 日志诊断".format(TOOL, VERSION))
    say("=" * 64)
    say("[输入] {}".format(data["source"]))
    if data.get("python"):
        say("[参考 Python] {}".format(data["python"]))
    say("[命中] {} 个已知模式".format(len(data["hits"])))
    if not data["hits"]:
        say("")
        say("没有匹配到已知模式。")
        say("这不代表环境没问题；可能只是这条日志不包含关键报错。")
        say("")
        say("建议：")
        say("  1. 贴日志时至少保留最前面 40 行，以及最下面 40 行 traceback。")
        say("  2. 用 ComfyUI 的 Python 重新运行一次，确认不是窗口自动关闭。")
        say("  3. 同时贴出工作流缺哪个节点、最近装过什么。")
        say("")
        say("常见三件事：")
        say("  - 自定义节点缺依赖：进节点目录，用 ComfyUI 的 Python 安装 requirements.txt。")
        say("  - torch / numpy / xformers 版本互相打架：不要盲目升级，先看兼容表。")
        say("  - 模型文件名或目录不对：对照工作流加载器中的文件名。")
        return
    for index, hit in enumerate(data["hits"], 1):
        say("")
        say("{}. [{}] {}".format(index, hit["title"], hit["id"]))
        say("   匹配原文：{}".format(hit["line"]))
        if hit.get("owner"):
            say("   疑似节点：{}".format(hit["owner"]))
        say("   原因：{}".format(hit["cause"]))
        say("   怎么修：{}".format(hit["fix"]))
        say("   可信度：{}".format(hit["confidence"]))
    say("")
    say("如果修复后仍报错，用 report 命令生成一份可转发的诊断报告。")


def run_self_test():
    root = Path(__file__).resolve().parent
    fixtures = root / "tests" / "fixtures"
    checks = []

    def check(name, func):
        try:
            detail = func()
            checks.append((name, True, detail or "通过"))
        except Exception as exc:
            checks.append((name, False, "{}: {}".format(type(exc).__name__, exc)))

    def workflow_checks():
        data = analyze_workflow(fixtures / "workflow_ui.json", offline=True)
        classes = [node["class"] for node in data["missing_nodes"]]
        assert "ImpactWildcardProcessor" in classes, classes
        assert all(name not in classes for name in
                   ("KSampler", "CheckpointLoaderSimple", "VAEEncode", "ImageScaleBy"))
        names = [model["name"] for model in data["missing_models"]]
        assert "国风大模型_v3.safetensors" in names, names
        assert "水墨风格_细节增强.safetensors" in names, names
        assert data["subgraphs"] == 1, data["subgraphs"]
        return "UI 工作流、子图、3 个中文模型"

    check("工作流解析", workflow_checks)

    def api_checks():
        data = analyze_workflow(fixtures / "workflow_api.json", offline=True)
        assert data["format"] == "API", data["format"]
        assert data["missing_nodes"], "API fixture 应有缺失节点"
        assert any(model["directory"] == "loras" for model in data["missing_models"])
        return "API 格式与模型提取正常"

    check("API 工作流", api_checks)

    def rule_checks():
        packages = {item["name"].casefold(): item["version"]
                    for item in json.loads(read_text_file(fixtures / "pip_list_conflict.json"))}
        findings = evaluate_rules(packages, [], python_exe="python", gpu_present=False)
        ids = {item["id"] for item in findings}
        assert "numpy2-torch" in ids, ids
        assert "xformers-torch" in ids, ids
        return "命中 NumPy 2 + torch、xformers 版本错配"

    check("依赖规则表", rule_checks)

    def log_checks():
        hits = scan_log(read_text_file(fixtures / "startup_broken.log"), "python")
        assert len(hits) >= 3, len(hits)
        ids = {item["id"] for item in hits}
        for wanted in ("import-failed", "module-missing", "numpy-abi"):
            assert wanted in ids, (wanted, ids)
        return "命中 {} 个模式".format(len(hits))

    check("启动日志", log_checks)

    def map_checks():
        parsed = parse_node_map({
            "https://github.com/owner/Demo-Nodes": [["DemoNodeA", "DemoNodeB"],
                                                     {"title_aux": "Demo 节点"}]})
        assert parsed["class_index"]["DemoNodeA"]["repo"].endswith("Demo-Nodes")
        assert parsed["class_count"] == 2
        return "class -> repo 索引正常"

    check("节点归属表", map_checks)

    def extra_paths_checks():
        parsed = read_extra_model_paths(fixtures / "extra_model_paths.yaml")
        assert parsed["sections"][0]["base_path"] == r"D:\秋叶整合包\ComfyUI", parsed
        assert "loras" in parsed["sections"][0]["paths"]
        assert len(parsed["sections"]) == 2, parsed
        return "extra_model_paths.yaml 子集解析正常"

    check("模型额外路径", extra_paths_checks)

    failures = [item for item in checks if not item[1]]
    say("{} v{} · 离线自检".format(TOOL, VERSION))
    say("=" * 64)
    for name, ok, detail in checks:
        say("[{}] {}：{}".format("通过" if ok else "失败", name, detail))
    say("")
    if failures:
        say("结论：{} / {} 项通过，{} 项失败。".format(
            len(checks) - len(failures), len(checks), len(failures)))
        return 1
    say("结论：全部 {} 项通过。未联网，未修改任何环境。".format(len(checks)))
    return 0


def apply_plan(data):
    """Execute node installs after snapshot and pre-check. Never downloads models."""
    plan = data["fix_plan"]
    if not data["comfy"].get("path"):
        say("无法执行 --apply：没有找到 ComfyUI。")
        return {"ok": False, "reason": "没有找到 ComfyUI", "commands": []}
    if not data["comfy"].get("python"):
        say("无法执行 --apply：没有找到可用的 Python。")
        return {"ok": False, "reason": "没有找到 Python", "commands": []}
    if data["comfy"].get("python_fallback"):
        say("无法执行 --apply：没有找到 ComfyUI 专属 Python，当前只能回退到系统 Python。")
        say("请用 --comfy 指向完整安装目录（含 python_embeded 或 .venv）后再试。")
        return {"ok": False, "reason": "没有 ComfyUI 专属 Python", "commands": []}
    commands = list(plan.get("clone_commands", [])) + list(plan.get("pip_commands", []))
    if not commands:
        say("修复计划里没有可自动执行的节点安装项；模型不会被自动下载。")
        return {"ok": False, "reason": "没有可执行项", "commands": []}
    python_exe = data["comfy"]["python"]
    baseline = pip_list(python_exe)
    if baseline["error"]:
        say("无法执行 --apply：读取 pip list 失败：{}".format(baseline["error"]))
        return {"ok": False, "reason": "pip list 失败", "commands": []}
    baseline_findings = evaluate_rules(baseline["packages"], [],
                                       python_exe, gpu_present=None)
    blockers = [item for item in baseline_findings if item["severity"] == "error"]
    if blockers:
        say("冲突预检未通过，拒绝写入。已有错误规则：")
        for item in blockers:
            say("  - {}：{}".format(item["title"], item["why"]))
        say("请先按规则修复，再运行 --apply。")
        return {"ok": False, "reason": "预检发现当前环境错误规则", "commands": []}
    snapshot_dir = Path.home() / CACHE_DIR_NAME / "snapshots"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        snapshot = snapshot_dir / "{}.pipfreeze.txt".format(stamp)
        freeze = run_capture([python_exe, "-m", "pip", "freeze"], timeout=120)
        if not freeze["ok"]:
            say("无法执行 --apply：pip freeze 失败，未写入任何环境。")
            return {"ok": False, "reason": "快照失败", "commands": []}
        snapshot.write_text(decode_bytes(freeze["out"]), encoding="utf-8")
    except OSError as exc:
        say("无法执行 --apply：写快照失败：{}；未执行安装。".format(exc))
        return {"ok": False, "reason": "快照写入失败", "commands": []}
    say("[快照] {}".format(snapshot))
    say("[回滚] \"{}\" -m pip install -r \"{}\"".format(python_exe, snapshot))
    say("")
    executed = []
    baseline_ids = {item["id"] for item in baseline_findings}
    for command in plan.get("clone_commands", []):
        say("[执行] {}".format(command))
        pieces = _split_simple_command(command)
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = run_capture(pieces, timeout=300, env=env)
        executed.append(command)
        if not result["ok"]:
            say("  失败：{}".format(one_line(decode_bytes(result["err"])) or result["error"]))
            say("  已停止。回滚命令见上方。")
            return {"ok": False, "reason": "git clone 失败", "commands": executed,
                    "snapshot": str(snapshot)}
        say("  完成")
    # Re-check pinned versions against the freshly cloned requirements before pip.
    for command in plan.get("pip_commands", []):
        req_match = re.search(r'-r\s+"([^"]+)"', command)
        if req_match and not Path(req_match.group(1)).is_file():
            say("[跳过] {}".format(command))
            say("  未找到 requirements.txt，跳过依赖安装；请按该节点 README 手动检查。")
            continue
        say("[执行] {}".format(command))
        if req_match:
            records = _requirement_records(req_match.group(1))
            merged = dict(baseline["packages"])
            pins = {}
            for record in records:
                if record["name"] in ("numpy", "torch", "torchvision", "xformers",
                                      "transformers", "pydantic"):
                    exact = re.match(r"^==\s*([0-9][^\s;]*)", record["spec"].strip())
                    if exact:
                        pins[record["name"]] = exact.group(1)
            for name, version in pins.items():
                merged[name] = version
            predicted = evaluate_rules(merged, [], python_exe, gpu_present=False)
            new_ids = {item["id"] for item in predicted} - baseline_ids
            if new_ids:
                say("  冲突预检未通过：安装 requirements 会新增规则命中：{}".format(
                    "、".join(sorted(new_ids))))
                say("  已停止，未执行 pip。回滚命令见上方。")
                return {"ok": False, "reason": "requirements 预检新增冲突",
                        "commands": executed, "snapshot": str(snapshot)}
        result = run_capture(_split_simple_command(command), timeout=1800)
        executed.append(command)
        if not result["ok"]:
            say("  失败：{}".format(one_line(decode_bytes(result["err"])) or result["error"]))
            say("  已停止。回滚命令见上方。")
            return {"ok": False, "reason": "pip install 失败", "commands": executed,
                    "snapshot": str(snapshot)}
        say("  完成")
    say("")
    say("节点安装完成。请重启 ComfyUI；模型仍需自行下载，未执行任何模型下载。")
    say("如需回滚：\"{}\" -m pip install -r \"{}\"".format(python_exe, snapshot))
    return {"ok": True, "commands": executed, "snapshot": str(snapshot)}


def _split_simple_command(command):
    """Split our own simple quoted commands without invoking a shell."""
    parts = re.findall(r'"([^"]*)"|(\S+)', command)
    return [quoted or bare for quoted, bare in parts]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="comfy_er.py", description="ComfyUI 急诊医生：零依赖、默认只读的独立诊断工具。")
    parser.add_argument("--version", action="version",
                        version="{} {}".format(TOOL, VERSION))
    parser.add_argument("--self-test", action="store_true", dest="self_test",
                        help="离线运行内置 fixture 自检")
    sub = parser.add_subparsers(dest="command")
    doctor = sub.add_parser("doctor", help="体检 ComfyUI 安装、依赖和启动日志")
    doctor.add_argument("--comfy", help="ComfyUI 根目录（含 main.py、comfy、custom_nodes）")
    doctor.add_argument("--log", help="手动指定启动日志文件")
    doctor.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    workflow = sub.add_parser("workflow", help="检查工作流缺失节点和模型")
    workflow.add_argument("path", help="工作流 JSON 文件或目录")
    workflow.add_argument("--comfy", help="ComfyUI 根目录")
    workflow.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    workflow.add_argument("--offline", action="store_true", help="禁止访问网络节点表")
    workflow.add_argument("--refresh-map", action="store_true", help="强制刷新节点归属表")
    workflow.add_argument("--apply", action="store_true",
                          help="先保存快照再执行节点安装（模型永不自动下载）")
    log_parser = sub.add_parser("log", help="翻译一段 traceback 或控制台日志")
    log_parser.add_argument("file", nargs="?", default="-",
                            help="日志文件；用 - 从 stdin 读取（默认）")
    log_parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    report = sub.add_parser("report", help="生成可复制到群聊的 Markdown 诊断报告")
    report.add_argument("--comfy", help="ComfyUI 根目录")
    report.add_argument("--workflow", help="同时分析这个工作流 JSON")
    report.add_argument("--out", help="把 Markdown 写入文件")
    return parser


def cmd_doctor(args):
    data = build_doctor_report(args.comfy, args.log)
    if args.json:
        emit_json(data)
        return 0
    render_doctor(data)
    return 0


def cmd_workflow(args):
    try:
        data = analyze_workflow(args.path, args.comfy, args.offline, args.refresh_map)
    except ValueError as exc:
        say("工作流检查失败：{}".format(exc))
        return 2
    if args.json and not args.apply:
        emit_json(data)
        return 0
    if not args.json:
        render_workflow(data)
    if args.apply:
        if args.json:
            say("--json 与 --apply 暂不支持同时使用。")
            return 2
        result = apply_plan(data)
        return 0 if result.get("ok") else 1
    return 0


def cmd_log(args):
    source = args.file
    if source == "-":
        try:
            if hasattr(sys.stdin, "buffer"):
                text = decode_bytes(sys.stdin.buffer.read())
            else:
                text = sys.stdin.read()
        except OSError as exc:
            say("读取 stdin 失败：{}".format(exc))
            return 2
        display = "stdin"
    else:
        path = _clean_path(source)
        if not path.is_file():
            say("日志文件不存在：{}".format(path))
            return 2
        try:
            text = read_text_file(path)
        except OSError as exc:
            say("读取日志失败：{}".format(exc))
            return 2
        display = str(path)
    location = locate_comfy(None)
    python_info = detect_python(location.get("path"))
    hits = scan_log(text, python_info.get("path"))
    data = {"tool": TOOL, "version": VERSION, "source": display,
            "lines": len(strip_ansi(text).splitlines()), "hits": hits}
    if not location.get("path"):
        data["python"] = None
    else:
        data["python"] = python_info.get("path")
    if args.json:
        emit_json(data)
        return 0
    render_log(data)
    return 0


def cmd_report(args):
    doctor = build_doctor_report(args.comfy)
    workflow = None
    if args.workflow:
        try:
            workflow = analyze_workflow(args.workflow, args.comfy, offline=False)
        except ValueError as exc:
            say("工作流检查失败，报告未生成：{}".format(exc))
            return 2
    markdown = render_report(doctor, workflow)
    if args.out:
        out = _clean_path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            say("写入报告失败：{}".format(exc))
            return 2
        say("诊断报告已写入：{}".format(out))
        say("请把文件内容整段复制到群里；这是 v0.1 的可分享诊断结果。")
    else:
        sys.stdout.write(markdown)
        sys.stdout.flush()
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "workflow":
            return cmd_workflow(args)
        if args.command == "log":
            return cmd_log(args)
        if args.command == "report":
            return cmd_report(args)
    except KeyboardInterrupt:
        say("已取消。")
        return 130
    except Exception as exc:
        if os.environ.get("COMFY_ER_DEBUG"):
            raise
        say("程序遇到未预期错误：{}: {}".format(type(exc).__name__, exc))
        say("这不代表你的 ComfyUI 坏了；请附上刚才执行的命令反馈。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
