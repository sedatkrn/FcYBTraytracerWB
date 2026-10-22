# -*- coding: utf-8 -*-
"""Blender Cycles köprüsü (prototip): Godot köprüsünün glb+json paketini Blender'a verip Cycles ile render eder."""
import json
import os
import shutil
import subprocess
import sys

from . import godot_bridge as gb
from . import settings as S

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_template", "render_scene.py")


def find_blender(path=None):
    cands = [path, os.environ.get("YBT_BLENDER"), getattr(S, "BLENDER_EXE", ""), shutil.which("blender"),
             os.path.expanduser("~/Applications/blender-5.2.2-linux-x64/blender")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def build_package(scene, outdir, **kw):
    pkg = gb.build_package(scene, outdir, **kw)
    with open(pkg["json"], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    glass = {}
    for i, m in enumerate(scene.mtable):
        if m and float(m.get("transp", 0.0)) > 0.05:
            glass[str(i + 1)] = dict(transp=float(m["transp"]), ior=float(m.get("ior", 1.45)))
    for i, m in enumerate(scene.mtable):
        if m and str(i + 1) in glass:
            glass[str(i + 1)]["absorb"] = float(m.get("absorb", 0.0))
    cfg["mat_glass"] = glass
    cfg["mat_lum"] = {str(i + 1): float(m.get("lum", 0.0)) for i, m in enumerate(scene.mtable) if m}
    cfg["mat_coat"] = {str(i + 1): float(m.get("coat", 0.0)) for i, m in enumerate(scene.mtable) if m and float(m.get("coat", 0.0)) > 0.0}
    cfg["mat_absorb"] = {str(i + 1): float(m.get("absorb", 0.0)) for i, m in enumerate(scene.mtable) if m and float(m.get("absorb", 0.0)) > 0.0}
    cfg["mat_spec"] = {str(i + 1): min(max(float(m.get("spec", 0.04)), 0.0), 1.0) for i, m in enumerate(scene.mtable)
                       if m and float(m.get("transp", 0.0)) <= 0.05 and float(m.get("metal", 0.0)) < 0.5}
    bump = {}
    for i, m in enumerate(scene.mtable):
        if not m or float(m.get("bump_height", 1.0)) <= 0.0:
            continue
        bl, cl = gb._gray_layer(m, "bump"), gb._color_layer(m)
        if bl is not None and cl is not None and bl.get("tex") == cl.get("tex") and gb._same_place(bl, cl):
            # bump dokusu renk dokusuyla aynı görüntü ve yerleşimde: aynı UV ile, renk düğümünün parlaklığı yükseklik olur
            bump[str(i + 1)] = dict(height=float(m.get("bump_height", 1.0)), invert=bool(bl.get("invert", False)),
                                    opacity=float(bl.get("opacity", 1.0)))
    cfg["mat_bump"] = bump
    with open(pkg["json"], "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    return pkg


def run_blender(exe, pkg, out_png, samples=64, device="CPU", timeout=3600):
    cmd = [exe, "-b", "--factory-startup", "-P", TEMPLATE, "--", pkg["json"], pkg["glb"], os.path.abspath(out_png), str(samples), device]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    for ln in (p.stdout + p.stderr).splitlines():
        if ln.startswith(("YBT_", "Error", "Traceback")) or "Error" in ln:
            print("[YBT Cycles] " + ln)
    if not os.path.isfile(out_png):
        raise RuntimeError("Blender çıktı üretmedi:\n" + (p.stdout + p.stderr)[-1500:])
    return out_png


def render(scene, out_png, blender=None, samples=64, device="CPU", workdir=None, keep=False, **kw):
    import tempfile
    exe = find_blender(blender)
    if exe is None:
        raise RuntimeError("Blender bulunamadı.")
    tmp = os.path.abspath(workdir) if workdir else tempfile.mkdtemp(prefix="ybt_cycles_")
    pkg = build_package(scene, tmp, **kw)
    try:
        return run_blender(exe, pkg, out_png, samples, device)
    finally:
        if not keep and workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)
