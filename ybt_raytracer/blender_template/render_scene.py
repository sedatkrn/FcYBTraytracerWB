# -*- coding: utf-8 -*-
# FC YBT Raytracer -> Blender Cycles köprüsü: scene.json + scene.glb dosyalarından sahne kurar, Cycles ile bir kare render eder.
# Kullanım: blender -b --factory-startup -P render_scene.py -- scene.json scene.glb cikti.png [örnek_sayısı] [CPU|GPU]
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

SUN_K = math.pi / 0.15   # köprü enerjisi = power*0.15 (Godot ayarı) -> Blender Sun gücü = power*pi (beyaz yüzeyde ~power)
POINT_K = 4.0 * math.pi * math.pi


def g2b(v):
    """glTF/Godot (Y-yukarı) -> Blender (Z-yukarı)."""
    return Vector((v[0], -v[2], v[1]))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    cfg_path, glb_path, out_png = argv[0], argv[1], argv[2]
    samples = int(argv[3]) if len(argv) > 3 else 64
    device = argv[4] if len(argv) > 4 else "CPU"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # --- geometri (glb): Godot ile aynı uzay; sahne köşegeni ~4 birim olacak ölçekte
    bpy.ops.import_scene.gltf(filepath=glb_path)
    k = float(cfg["scale"])
    holder = bpy.data.objects.new("holder", None)
    scene.collection.objects.link(holder)
    holder.scale = (k, k, k)
    for o in list(bpy.data.objects):
        if o is not holder and o.parent is None:
            o.parent = holder

    _materials(cfg)
    _world(scene, cfg)
    _camera(scene, cfg)
    for L in cfg["lights"]:
        _light(scene, L)

    w, h = int(cfg["size"][0]), int(cfg["size"][1])
    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
    r.filepath = out_png
    r.image_settings.file_format = "PNG"
    cy = scene.cycles
    cy.samples = samples
    cy.use_denoising = True
    try:
        cy.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    cy.device = "CPU"
    if device.upper() == "GPU":
        pr = bpy.context.preferences.addons["cycles"].preferences
        for t in ("HIP", "OPTIX", "CUDA", "ONEAPI", "METAL"):
            try:
                pr.compute_device_type = t
                pr.refresh_devices()
            except Exception:
                continue
            gpus = [d for d in pr.devices if d.type != "CPU"]
            if gpus:
                for d in pr.devices:
                    d.use = d.type != "CPU"
                cy.device = "GPU"
                print("YBT_GPU:", t, [d.name for d in gpus])
                break
        if cy.device != "GPU":
            print("YBT_WARN: GPU bulunamadı, CPU kullanılıyor")
    scene.view_settings.view_transform = "AgX" if cfg.get("tonemap") == "aces" else "Standard"
    scene.view_settings.exposure = math.log2(max(float(cfg.get("exposure", 1.0)), 1e-3))
    print("YBT_INFO: %dx%d | %d örnek | %s | %d malzeme, %d ışık" % (w, h, samples, cy.device, len(bpy.data.materials), len(cfg["lights"])))
    bpy.ops.render.render(write_still=True)
    print("YBT_OK:", out_png)


def _principled(mat):
    if not mat.use_nodes or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def _materials(cfg):
    """Köprünün yansıma/cam tablolarını Principled BSDF'e uygular (malzeme adı: mat<id>_<renk>)."""
    refl, glass, spec = cfg.get("mat_refl", {}), cfg.get("mat_glass", {}), cfg.get("mat_spec", {})
    bump = cfg.get("mat_bump", {})
    coat, absorb = cfg.get("mat_coat", {}), cfg.get("mat_absorb", {})
    uvnames = _uv_names()
    for mat in bpy.data.materials:
        if not mat.name.startswith("mat"):
            continue
        key = mat.name[3:].split("_")[0]
        pb = _principled(mat)
        if pb is None:
            continue
        if key in glass:
            g = glass[key]
            nt = mat.node_tree
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
            for lk in list(pb.inputs["Alpha"].links):
                nt.links.remove(lk)
            pb.inputs["Alpha"].default_value = 1.0
            pb.inputs["Transmission Weight"].default_value = min(max(float(g["transp"]), 0.0), 1.0)
            pb.inputs["IOR"].default_value = max(float(g.get("ior", 1.45)), 1.0)
            if out is not None:
                nt.links.new(pb.outputs["BSDF"], out.inputs["Surface"])
        if key in spec and key not in glass and "Specular IOR Level" in pb.inputs:
            pb.inputs["Specular IOR Level"].default_value = spec[key]      # yerleşik motorun 'spec' değeri (Blender varsayılanı 0.5)
        ex = mat.get("ybt")
        if ex:
            try:
                _chan_maps(mat, pb, ex, key, uvnames.get(mat.name), cfg, os.path.dirname(os.path.abspath(sys.argv[sys.argv.index("--") + 2])))
            except Exception as e:
                print("YBT_WARN: gri kanal haritası uygulanamadı (%s): %s" % (mat.name, e))
        if key in coat and "Coat Weight" in pb.inputs:
            pb.inputs["Coat Weight"].default_value = min(max(coat[key], 0.0), 1.0)
            pb.inputs["Coat Roughness"].default_value = 0.03
        if key in absorb:
            _absorb(mat, pb, absorb[key])
        if key in bump and key not in glass:
            bb = dict(bump[key])
            bb["dist"] = float(cfg.get("bump_rel", 0.012)) * float(bb.get("size", 0.0)) * float(cfg["scale"]) * float(bb["height"])
            _bump(mat, pb, bb, ex, uvnames.get(mat.name), os.path.dirname(os.path.abspath(sys.argv[sys.argv.index("--") + 2])))
        if key in glass:
            pass
        elif key in refl and not pb.inputs["Metallic"].is_linked:
            # Godot köprüsüyle aynı eşleme: yansıtıcılık ~ metalik = max(metal, refl)
            pb.inputs["Metallic"].default_value = max(pb.inputs["Metallic"].default_value, float(refl[key]))


def _uv_names():
    """Malzeme adı -> o malzemeyi kullanan ilk nesnenin UV katman adları (glb TEXCOORD_n sırasıyla)."""
    res = {}
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        names = [l.name for l in o.data.uv_layers]
        for sl in o.material_slots:
            if sl.material is not None and sl.material.name not in res:
                res[sl.material.name] = names
    return res


def _chan_maps(mat, pb, ex, key, names, cfg, gdir):
    """Gri kanal haritaları (spec, refl, lum): PNG'yi malzemenin kendi UV kümesiyle okuyup Principled girdisine bağlar."""
    nt = mat.node_tree
    for kind in ("spec", "refl", "lum"):
        e = ex.get(kind)
        if not e or not names or int(e["uv"]) >= len(names):
            continue
        img = bpy.data.images.load(os.path.join(gdir, str(e["file"])), check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = names[int(e["uv"])]
        tx = nt.nodes.new("ShaderNodeTexImage")
        tx.image = img
        nt.links.new(uvn.outputs["UV"], tx.inputs["Vector"])
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(tx.outputs["Color"], sep.inputs["Color"])
        src = sep.outputs["Red"]
        if e.get("inv"):
            inv = nt.nodes.new("ShaderNodeMath")
            inv.operation = "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            nt.links.new(src, inv.inputs[1])
            src = inv.outputs["Value"]
        base = {"spec": cfg.get("mat_spec", {}).get(key, 0.04), "refl": cfg.get("mat_refl", {}).get(key, 0.0),
                "lum": cfg.get("mat_lum", {}).get(key, 0.0)}[kind]
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "FLOAT"
        mix.inputs["Factor"].default_value = min(max(float(e.get("op", 1.0)), 0.0), 1.0)
        mix.inputs["A"].default_value = float(base)
        nt.links.new(src, mix.inputs["B"])
        val = mix.outputs["Result"]
        if kind == "spec":
            nt.links.new(val, pb.inputs["Specular IOR Level"])
        elif kind == "refl":
            mx = nt.nodes.new("ShaderNodeMath")
            mx.operation = "MAXIMUM"
            mx.inputs[1].default_value = float(pb.inputs["Metallic"].default_value)
            nt.links.new(val, mx.inputs[0])
            nt.links.new(mx.outputs["Value"], pb.inputs["Metallic"])
        else:
            bc = pb.inputs["Base Color"]
            if bc.is_linked:
                nt.links.new(bc.links[0].from_socket, pb.inputs["Emission Color"])
            else:
                pb.inputs["Emission Color"].default_value = bc.default_value
            sc = nt.nodes.new("ShaderNodeMath")
            sc.operation = "MULTIPLY"
            sc.inputs[1].default_value = 1.5
            nt.links.new(val, sc.inputs[0])
            nt.links.new(sc.outputs["Value"], pb.inputs["Emission Strength"])


def _absorb(mat, pb, k):
    """Cam emilimi: malzemenin hacmine Volume Absorption koyar (renk = malzeme rengi; kalın cam daha koyu)."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return
    col = pb.inputs["Base Color"].default_value
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.inputs["Color"].default_value = (col[0], col[1], col[2], 1.0)
    vol.inputs["Density"].default_value = 10.0 * min(max(float(k), 0.0), 1.0)      # birim: Blender birimi (sahne köşegeni ~4)
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])


def _bump(mat, pb, b, ex=None, names=None, gdir=""):
    """Yükseklik kaynağını Bump düğümüne bağlar: renk dokusuyla aynıysa renk düğümü, değilse ayrı bump PNG'si (kendi UV kümesi)."""
    nt = mat.node_tree
    bc = pb.inputs["Base Color"]
    tex = None
    height_sock = None
    e = (ex or {}).get("bump") if not b.get("same") else None
    if e and names and int(e["uv"]) < len(names):
        try:
            img = bpy.data.images.load(os.path.join(gdir, str(e["file"])), check_existing=True)
            img.colorspace_settings.name = "Non-Color"
            uvn = nt.nodes.new("ShaderNodeUVMap")
            uvn.uv_map = names[int(e["uv"])]
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        except Exception as err:
            print("YBT_WARN: bump görüntüsü yüklenemedi (%s): %s" % (mat.name, err))
            tex = None
    elif not b.get("same"):
        print("YBT_WARN: ayrı bump dokusu glb'de bulunamadı:", mat.name)
        return
    if tex is None and b.get("same"):
        tex = bc.links[0].from_node if bc.is_linked and bc.links[0].from_node.type == "TEX_IMAGE" else None
    if tex is None and b.get("same"):
        for lk in bc.links:                                       # renk, çarpma düğümü üzerinden gelebilir
            for ip in lk.from_node.inputs:
                if ip.is_linked and ip.links[0].from_node.type == "TEX_IMAGE":
                    tex = ip.links[0].from_node
    if tex is None:
        print("YBT_WARN: bump için görüntü düğümü bulunamadı:", mat.name)
        return
    node = nt.nodes.new("ShaderNodeBump")
    node.inputs["Strength"].default_value = min(max(float(b.get("opacity", 1.0)), 0.0), 1.0)
    node.inputs["Distance"].default_value = max(float(b.get("dist", 0.0)), 1e-5) if b.get("dist") else 0.02      # = doku genişliği x BUMP_REL x bump_height (Blender birimi)
    node.invert = bool(b.get("invert", False))
    hs = tex.outputs["Color"]
    lo, hi = float(b.get("lo", 0.0)), float(b.get("hi", 1.0))
    if lo > 0.0 or hi < 1.0:                                      # seviyeler: Map Range (kıstırmalı) - yerleşik motorla aynı
        sep = nt.nodes.new("ShaderNodeRGBToBW")
        nt.links.new(hs, sep.inputs["Color"])
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = lo
        mr.inputs["From Max"].default_value = max(hi, lo + 0.01)
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        nt.links.new(sep.outputs["Val"], mr.inputs["Value"])
        hs = mr.outputs["Result"]
    nt.links.new(hs, node.inputs["Height"])
    nt.links.new(node.outputs["Normal"], pb.inputs["Normal"])


def _world(scene, cfg):
    e = cfg["env"]
    world = bpy.data.worlds.new("YBT")
    world.use_nodes = True
    scene.world = world
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    hdri = e.get("hdri", "")
    top, bot = e["top"], e["bottom"]
    flat = [(top[i] + bot[i]) * 0.5 for i in range(3)] + [1.0]
    if hdri and os.path.isfile(hdri):
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Rotation"].default_value[2] = math.radians(float(e.get("hdri_rot", 0.0)))
        tex = nt.nodes.new("ShaderNodeTexEnvironment")
        tex.image = bpy.data.images.load(hdri)
        bg = nt.nodes.new("ShaderNodeBackground")
        bg.inputs["Strength"].default_value = float(e.get("hdri_energy", 1.0)) * (1.0 + 2.0 * float(e.get("ambient", 0.0)))   # yerleşik motorun ortam ışığı karşılığı (deneysel)
        nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bg.inputs["Color"])
        last = bg
        if not e.get("show_bg", True):                         # arka planda düz renk, aydınlatma/yansıma HDRI'den
            lp = nt.nodes.new("ShaderNodeLightPath")
            bg2 = nt.nodes.new("ShaderNodeBackground")
            bg2.inputs["Color"].default_value = flat
            mix = nt.nodes.new("ShaderNodeMixShader")
            nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
            nt.links.new(bg.outputs["Background"], mix.inputs[1])
            nt.links.new(bg2.outputs["Background"], mix.inputs[2])
            last = mix
        nt.links.new(last.outputs[0], out.inputs["Surface"])
    else:
        bg = nt.nodes.new("ShaderNodeBackground")
        bg.inputs["Color"].default_value = flat
        bg.inputs["Strength"].default_value = 1.0
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def _camera(scene, cfg):
    c = cfg["camera"]
    cam = bpy.data.cameras.new("cam")
    obj = bpy.data.objects.new("cam", cam)
    scene.collection.objects.link(obj)
    pos = g2b(c["pos"])
    fwd = g2b(c["fwd"]).normalized()
    up = g2b(c["up"]).normalized()
    right = fwd.cross(up).normalized()
    up2 = right.cross(fwd)
    m = Matrix(((right.x, up2.x, -fwd.x), (right.y, up2.y, -fwd.y), (right.z, up2.z, -fwd.z)))   # Blender kamerası -Z'ye bakar
    obj.matrix_world = Matrix.Translation(pos) @ m.to_4x4()
    cam.sensor_fit = "VERTICAL" if c.get("keep") == "height" else "HORIZONTAL"
    if c.get("persp", True):
        cam.type = "PERSP"
        cam.angle = math.radians(float(c["fov"]))
    else:
        cam.type = "ORTHO"
        cam.ortho_scale = float(c["size"])
    cam.clip_start, cam.clip_end = 0.05, 400.0
    scene.camera = obj


def _light(scene, L):
    kind = L["kind"]
    col = L.get("color", (1, 1, 1))
    if kind == "distant":
        ld = bpy.data.lights.new("sun", "SUN")
        ld.energy = float(L["energy"]) * SUN_K
        ld.angle = math.radians(float(L.get("angular", 0.5)))
    elif kind == "spot":
        ld = bpy.data.lights.new("spot", "SPOT")
        ld.energy = float(L["energy"]) * POINT_K
        ld.spot_size = math.radians(float(L.get("angle", 45.0)) * 2.0)
        ld.spot_blend = 0.3
        ld.shadow_soft_size = float(L.get("size", 0.0))
    else:
        ld = bpy.data.lights.new("point", "POINT")
        ld.energy = float(L["energy"]) * POINT_K
        ld.shadow_soft_size = float(L.get("size", 0.0))
    ld.color = (col[0], col[1], col[2])
    obj = bpy.data.objects.new(ld.name, ld)
    scene.collection.objects.link(obj)
    if kind in ("distant", "spot"):
        d = g2b(L["dir"]).normalized()
        obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    if kind != "distant":
        obj.location = g2b(L["pos"])


main()
