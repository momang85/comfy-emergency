# comfy-emergency 🩺

**ComfyUI 急诊室：只诊断、不乱装。ComfyUI 打不开的时候，它是唯一还能用的那个工具。**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#)
[![Tests](https://img.shields.io/badge/tests-20%20passing-brightgreen.svg)](tests/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 下载来的工作流一片红框？装了一晚上还是打不开？问 AI 越问越乱，最后只能删掉整个 Python 包？

**它只做一件事**：把你这台机器上到底缺什么、冲突在哪、日志在骂什么，翻译成一段看得懂的中文，并给出现成的修复命令。

单文件 2150 行 · **0 个第三方依赖** · 7 条依赖冲突规则 · 14 类日志模式 · 158 个内置节点名 · 默认只读。

---

## 为什么不能只靠 ComfyUI-Manager

ComfyUI-Manager 很好，但它有个前提：**ComfyUI 得能打开**。

真实场景是——ComfyUI 打不开。红框、模块导入报错、torch 和 numpy 打架，加载器直接退出。插件式工具全部失效，因为宿主已经死了。

comfy-emergency 是独立进程，用你系统的 Python 跑：

| | ComfyUI-Manager | comfy-emergency |
|---|---|---|
| ComfyUI 打不开时 | ❌ 完全失效 | ✅ 照常工作 |
| 依赖冲突检测 | ❌ | ✅ 7 条规则（NumPy 2 / xformers / pydantic…） |
| 报错日志翻译 | 原始 traceback | 中文人话 + 修复命令 |
| 动你的环境 | 直接装 | 默认只读；`--apply` 前先拍 pip freeze 快照 |
| 第三方依赖 | 一堆 | 0 |

**不是替代，是互补**：它管「能打开时的日常安装」，我们管「打不开时怎么把命救回来」。

---

## 30 秒上手

### Windows（整合包用户看这里）

1. 下载仓库，解压到任意目录
2. 双击 **`一键运行.bat`**
3. 看屏幕上的中文报告

不用装依赖，不用命令行，不用先启动 ComfyUI。脚本会自动找 `py` / `python` / 便携包里的 `python_embeded\python.exe`。

### 命令行

```bash
python comfy_er.py doctor                         # 体检：ComfyUI 在哪、环境有什么毛病
python comfy_er.py workflow 工作流.json            # 这个工作流缺什么节点、缺什么模型
python comfy_er.py log comfyui.log                # 打不开？把日志丢给它
python comfy_er.py report --comfy D:\ComfyUI      # 生成可贴群的 Markdown 报告
python comfy_er.py --self-test                    # 离线自检，不联网、不改环境
```

---

## 它长什么样（真实输出）

```
> python comfy_er.py workflow 国风工作流.json --comfy D:\ComfyUI

[格式] UI（含 1 个子图）
[节点统计] 共 10 种；内置 9 种；待确认自定义 1 种
[模型库存] 已读取 models/，共 1 个文件

缺失节点（1 个）
  1. ImpactWildcardProcessor
     所属仓库：ComfyUI Impact Pack (https://github.com/ltdrdata/ComfyUI-Impact-Pack)
     是否已装但导入失败：否（仓库未安装）

缺失模型（3 个）
  文件名 | 期望子目录 | 处理
  国风大模型_v3.safetensors | models/checkpoints | 请自行下载（不提供模型文件）
  水墨风格_细节增强.safetensors | models/loras | 请自行下载（不提供模型文件）
  国风_vae.safetensors | models/vae | 请自行下载（不提供模型文件）

修复计划（只读预演，尚未执行）
  节点安装命令：
    1. git clone "https://github.com/ltdrdata/ComfyUI-Impact-Pack" "D:\ComfyUI\custom_nodes\ComfyUI-Impact-Pack"
  依赖安装命令：
    2. <ComfyUI 的 python> -m pip install -r "D:\ComfyUI\custom_nodes\ComfyUI-Impact-Pack\requirements.txt"

本次运行未修改任何环境；--apply 是唯一写入路径，且会先保存 pip freeze 快照。
本工具永远不会自动下载模型。
```

打不开的时候：

```
> python comfy_er.py log comfyui.log

[命中] 6 个已知模式

1. [Python 模块缺失] ModuleNotFoundError: No module named 'impact'
   疑似节点：ComfyUI-Impact-Pack
   原因：缺少 Python 模块 'impact'；通常是节点 requirements 未装进目标环境。
   怎么修：进入该节点目录执行 <ComfyUI 的 python> -m pip install -r requirements.txt
   可信度：高

6. [NumPy ABI 不兼容] numpy.core.multiarray failed to import
   原因：当前 NumPy 2.x 与旧版 torch/自定义节点编译产物 ABI 不兼容。
   怎么修：<ComfyUI 的 python> -m pip install "numpy<2"
   可信度：高
```

---

## 它能查出什么

| 检查项 | 说明 |
|---|---|
| 缺失节点 | UI / API 两种工作流格式，含**子图**展开；class 名反查所属仓库 |
| 缺失模型 | 比对 `models/` 各子目录，大小写不敏感；同名不同目录会提示「可能放错子目录」 |
| 装错但导入失败 | 日志里能对上号的节点，直接指出「已安装但 import 失败」 |
| 依赖冲突 | 7 条规则：NumPy 2 + 旧 torch、xformers / torch 版本错配、pydantic v1/v2 等 |
| 启动日志 | 14 类模式，traceback 翻译成中文 + 修复命令 + 可信度 |
| 额外模型路径 | 解析 `extra_model_paths.yaml`，秋叶包把模型放别处也能找到 |

**诚实边界**：工作流逻辑坏了、模型下错了、显存不够，它只告诉你是哪一类，不假装能修。

---

## 安全承诺

1. **默认只读**。不加 `--apply`，一个字节都不写进你的环境。
2. **动手前拍照**。`--apply` 先执行 `pip freeze` 存快照到 `~/.comfy-er/snapshots/`，失败时回滚就一行命令。
3. **规则不通过不许装**。`--apply` 前若已命中 `error` 级冲突，直接拒绝。
4. **不碰模型、不碰工作流**。只读；模型永远不下载。
5. **纯标准库**。这正是它能在废墟里跑起来的原因。
6. **不联网也能用**。`--offline` 完全离线，只是节点归属会退化成「未知」。

---

## 常见问题

**Q：能修好吗？**
只能修「装」和「配」的问题（缺节点、缺依赖、冲突）。别的它只诊断，不骗你。

**Q：会不会把我环境搞坏？**
默认不动手。真要动手先存快照，回滚一行命令。而且在有 `error` 级冲突时它会直接拒绝执行。

**Q：秋叶整合包的便携 Python 能用吗？**
能用。`一键运行.bat` 会自动找 `python_embeded\python.exe`；也可以手动 `--comfy` 指向整合包的 ComfyUI 目录。

**Q：和 ComfyUI-Manager 冲不冲突？**
不冲突。它优先读 Manager 已经生成好的 `extension-node-map.json`，只读不写。

**Q：为什么不做模型自动下载？**
版权、带宽、断点续传、镜像失效，全是坑，写错一次路径就是灾难。只给文件名和目标目录。

---

## Roadmap

- [x] v0.1 只读诊断：节点 / 模型 / 依赖冲突 / 日志翻译 / 报告
- [ ] 模型库去重（SHA256 相同文件 hardlink，多个 ComfyUI 共用一份，容量直接减半）
- [ ] 规则库持续扩充（每条都来自真实 issue，欢迎贴日志）
- [ ] 给完全不想看命令行的用户做个图形界面

---

## English (short)

**comfy-emergency** is a zero-dependency, read-only-first doctor for ComfyUI. It runs as a
standalone process on your system Python, so it still works when ComfyUI itself refuses to
boot — the exact case where in-process tools like ComfyUI-Manager cannot help at all.

It parses workflows (UI + API format, subgraphs included), maps node classes to the
custom-node repos that own them, checks model filenames across `models/*`, flags known
pip dependency conflicts, and translates startup tracebacks into plain Chinese with a fix
command. Nothing is written unless you pass `--apply`, and `--apply` snapshots
`pip freeze` first. Models are never downloaded.

```bash
python comfy_er.py --self-test
python comfy_er.py workflow my_workflow.json --comfy /path/to/ComfyUI
python comfy_er.py log comfyui.log
```

MIT licensed. Issues with your real log are the most useful contribution.

---

MIT License · 用得上就点个 star，用不上就当没看见。
