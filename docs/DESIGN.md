# Design notes — comfy-emergency v0.1
## The problem
Downloaded ComfyUI workflows fail for four common reasons: missing custom nodes,
missing model files, broken Python dependencies, or a ComfyUI that will not boot.
The dominant fixer, ComfyUI-Manager, is itself a ComfyUI extension, so it cannot
help when ComfyUI is dead. Its installs also write into the live environment and
can make dependency conflicts worse.

## Why out-of-process
`comfy_er.py` runs on any system Python 3.8+ and never imports ComfyUI. It reads
the install as data and invokes the target Python only through subprocesses for
`pip list`, `pip freeze` and `pip install`. Consequences:
1. It works when ComfyUI cannot start.
2. A broken ComfyUI site-packages cannot break the doctor itself.
3. The doctor can inspect an install without changing its import state.

All parsing is stdlib only. The tool must survive the exact situation it exists
to diagnose: a user environment with no working third-party packages.

## Read-only first
Every command except `workflow --apply` is strictly read-only. `--apply` is the
only code path allowed to write, and it runs this sequence before any clone:
1. Read `pip freeze` from the target Python.
2. Save it to `~/.comfy-er/snapshots/<timestamp>.pipfreeze.txt`.
3. Refuse if the current rule table already has an `error` finding.
4. Clone missing repos, then pre-check pinned requirements for new conflicts.
5. Stop before pip if the pre-check predicts a new rule hit.
6. Print the exact rollback command: `pip install -r <snapshot>`.

Models are never written or downloaded. The cache and snapshots live outside the
ComfyUI install so a failed update can still be rolled back.

## Rule-table format
Rules are data, not branches. Each entry has a stable id, Chinese title, severity,
explanation, fix command and confidence:
```python
RULE_TABLE = {
    "numpy2-torch": {
        "title": "NumPy 2 与旧版 PyTorch 冲突",
        "severity": "error",
        "why": "numpy 2.x 改变了底层 ABI……",
        "fix": '{python} -m pip install "numpy<2"',
        "confidence": "高",
    },
}
```
Only `error` rules block `--apply`; warnings are shown but do not prevent an
explicit install. The same table is reused by `doctor`, `workflow --apply` and
the offline self-test, so documentation and behavior cannot drift apart.

## Node-map resolution
`workflow` needs class-name-to-repository data. The default is the public
`extension-node-map.json` from ComfyUI-Manager. Resolution order:
1. `--offline`: local Manager file, then a local cache; never the network.
2. Fresh cache younger than 7 days.
3. Network fetch, written atomically to `~/.comfy-er/node-map.json`.
4. Stale cache or local Manager map.
5. Degrade to `未联网，节点归属未知`; never crash.

Installed directories are matched by `.git/config` URL and by directory name.
Core ComfyUI node types are embedded so they are never reported as missing
custom nodes. Log evidence can upgrade a missing node to "installed but failed
to import" and points at the owning `custom_nodes` directory.

## Deliberately not done
- No model downloads or mirrors; filenames and expected subdirectories only.
- No automatic torch/CUDA repair. Wrong GPU wheels are a hardware/driver decision.
- No web UI, telemetry, analytics or background service.
- No shell execution of generated commands in read-only mode.
- No claim that a workflow is "fixed" when only its node install commands ran.

## Compatibility
Source is UTF-8, parsed with `ast.parse(..., feature_version=(3, 8))`, and tested
on Python 3.12 and 3.14. Console output goes through `say()`, which falls back on
`UnicodeEncodeError` for GBK consoles. `--json` writes UTF-8 bytes even when the
console code page is GBK. Logs are decoded per line as UTF-8 then GB18030.

## Tests
`tests/` uses only `unittest` and ships fixed workflows, a broken startup log and
a conflicting pip list. `--self-test` runs the same checks offline.
