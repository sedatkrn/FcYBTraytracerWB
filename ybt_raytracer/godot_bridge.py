# -*- coding: utf-8 -*-
"""Godot köprüsü: FreeCAD sahnesini (core.Scene) Godot 4 ile render eder.

Akış:
  1. `export_glb`     Scene üçgenleri + malzemeler  -> scene.glb (glTF 2.0; yalnız numpy ile yazılır)
  2. `build_package`  scene.glb + scene.json (kamera, ışıklar, ortam, zemin) + Godot proje şablonu -> bir klasör
  3. `render`         Godot'u (pencereli ya da xvfb içinde) çalıştırıp PNG alır

Koordinatlar: FreeCAD mm, Z-yukarı  ->  glTF/Godot metre, Y-yukarı: (x, y, z) -> (x, z, -y). Sahne ışın izleyicideki gibi
sahne merkezine göredir. Godot betiği (godot_template/render_scene.gd) glb'yi çalışma zamanında yükler; içe aktarma adımı yok.
JSON içindeki kamera/ışık/zemin koordinatları, glb'nin Godot'ta uygulanan ölçeğiyle (`scale`) çarpılmış halde verilir.
Kapsam: geometri, köşe normalleri, renk, saydamlık, metal/pürüzlülük, kendi ışığı (emission), yönlü/konumlu/spot ışık,
perspektif/ortografik kamera, gökyüzü gradyanı ya da HDRI, sanal zemin. Renk kanalının en üstteki görüntü dokusu glb'ye UV + gömülü PNG
olarak aktarılır (kübik/düzlemsel/silindirik/küresel izdüşüm üçgen/köşe başına UV'ye pişirilir). Prosedürel katmanlar, gri kanal
(pürüzlülük, bump...) dokuları ve katman karışım kipleri (çarpma hariç) henüz aktarılmıyor.
"""
import json
import math
import os
import shutil
import struct
import subprocess

import numpy as np

from . import settings as S

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(_HERE, "godot_template")
MM = 0.001                      # mm -> m

# Işın izleyici ışık gücü (0..1) -> Godot light_energy; görsel olarak ayarlanmış kaba eşleme
ENERGY = dict(distant=0.15)        # ışın izleyici gücü (0..1) -> Godot light_energy çarpanı (düz beyaz yüzeyde ışın izleyiciyle eşleşecek şekilde ayarlı)
AMB_K = 0.95                    # ışın izleyici ortam ışığı -> Godot ambient_light_energy çarpanı
TONEMAP = dict(none="linear", soft="reinhard", aces="aces")


def _to_gl(v):
    """FreeCAD (Z-yukarı) -> glTF (Y-yukarı); son eksen 3 bileşen olmalı."""
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[..., 0], v[..., 2], -v[..., 1]], axis=-1)


def _srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _roughness(gloss):
    g = min(max(float(gloss), 0.0), 1.0)
    return float(min(1.0, (1.0 - g) ** 1.5 + 0.03))


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v



# ------------------------------------------------------------------ doku (UV + PNG)
def _png_bytes(rgb8):
    """(h,w,3) uint8 -> PNG baytları (yalnız zlib + struct)."""
    import zlib
    a = np.ascontiguousarray(rgb8, dtype=np.uint8)
    h, w = a.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), a.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _color_layer(mat):
    """Malzemenin renk kanalındaki en üstteki etkin görüntü katmanı (prosedürel üstteyse ya da yoksa None)."""
    if not mat:
        return None
    for L in reversed((mat.get("tex_layers") or {}).get("color", [])):
        if not L.get("on", True):
            continue
        if L.get("type") == "proc" or not L.get("tex"):
            if L.get("type") == "proc":
                return None
            continue
        if str(L.get("blend", "normal")) not in ("normal", "mul"):
            continue
        return L
    return None


def _gray_layer(mat, chan):
    """Gri kanalın (örn. "gloss") en üstteki etkin görüntü katmanı (normal karışım); yoksa None."""
    if not mat:
        return None
    for L in reversed((mat.get("tex_layers") or {}).get(chan, [])):
        if not L.get("on", True):
            continue
        if L.get("type") == "proc":
            return None
        if L.get("tex") and str(L.get("blend", "normal")) == "normal":
            return L
    return None


_PLACE = ("tex", "tex_scale", "tex_proj", "tex_axis", "tex_sx", "tex_sy", "tex_sz", "tex_px", "tex_py", "tex_pz",
          "tex_rx", "tex_ry", "tex_rz", "tex_world", "tex_fit", "tile")


def _same_place(a, b):
    return a is not None and b is not None and all(a.get(k) == b.get(k) for k in _PLACE)


def _rough_image(tx, base_gloss, op, invert):
    """Parlaklık (gloss) dokusu -> glTF metalik-pürüzlülük görüntüsü: G = pürüzlülük, B = 1 (metal çarpanı korunur)."""
    lv = tx.levels[0]
    lum = 0.2126 * lv[:, :, 0] + 0.7152 * lv[:, :, 1] + 0.0722 * lv[:, :, 2]
    g = np.clip(lum, 0.0, 1.0) ** (1.0 / 2.2)
    if invert:
        g = 1.0 - g
    g = (1.0 - op) * float(base_gloss) + op * g
    rough = np.clip((1.0 - g) ** 1.5 + 0.03, 0.0, 1.0)
    out = np.zeros(rough.shape + (3,), dtype=np.uint8)
    out[:, :, 1] = np.rint(rough * 255.0).astype(np.uint8)
    out[:, :, 2] = 255
    return out


def _layer_uv(scene, L, tx, tw, idx):
    """Üçgenler (idx) için (m,3,2) UV: ışın izleyicideki doku yerleşimini köşe başına pişirir.
    Kübik izdüşümde her üçgen kendi baskın ekseninden düzlemsel eşlenir (karışım yok)."""
    from . import render as R
    tp = R._tex_params(L)
    m = len(idx)
    Pc = scene.tri[idx].reshape(-1, 3)
    ng = np.repeat(scene.n_unit[idx], 3, axis=0)
    tid = np.repeat(idx, 3)
    Q, nl, hext = R._tex_space(scene, tp, Pc, ng, tid)
    per = tw * tx.span
    once = bool(getattr(tx, "clamp", False))
    fit = bool(tp.get("fit")) and hext is not None
    if tp["proj"] == "cubic":
        ax = np.argmax(np.abs(nl.reshape(m, 3, 3)[:, 0, :]), axis=1)           # üçgen başına baskın eksen
        u = np.zeros(m * 3)
        v = np.zeros(m * 3)
        axv = np.repeat(ax, 3)
        for a in range(3):
            sel = np.nonzero(axv == a)[0]
            if len(sel) == 0:
                continue
            ia, ib = R._PLANE[a]
            if fit:
                su = R._face_sign(a, nl[sel])
                u[sel] = (su * Q[sel, ia] / np.maximum(2.0 * hext[sel, ia], 1e-9) + 0.5) / float(tx.span)
                v[sel] = (0.5 - Q[sel, ib] / np.maximum(2.0 * hext[sel, ib], 1e-9)) / float(tx.span)
            else:
                u[sel] = Q[sel, ia] / per
                v[sel] = Q[sel, ib] / (per * tx.aspect)
        if once and not fit:
            u, v = u + 0.5, v + 0.5
    else:
        u, v, kind, aux = R._tex_uv(tx, tp, Q, per, hext, scene, nl)
        if once and (fit or tp["proj"] == "planar"):
            u, v = u + 0.5, v + 0.5
        if kind in ("cyl", "sph"):                                              # açı dikişi: üçgen içinde u sürekli olsun
            if fit:
                period = np.full(m * 3, 1.0 / float(tx.span))
            else:
                rep = aux[0] if isinstance(aux, tuple) else aux
                period = np.asarray(rep, dtype=np.float64) * np.ones(m * 3)
            u3 = u.reshape(m, 3)
            p3 = period.reshape(m, 3)[:, :1]
            u3 = u3 - p3 * np.round((u3 - u3[:, :1]) / p3)
            u = u3.reshape(-1)
    return np.stack([u, v], axis=1).reshape(m, 3, 2).astype(np.float32)


# ------------------------------------------------------------------ glTF yazımı
def _gltf_material(scene, key, tex_index=None, white=False, rough=None):
    mid, r, g, b, a = key
    mat = scene.mtable[mid - 1] if 0 < mid <= len(scene.mtable) else None
    lin = _srgb_to_linear(np.array([r, g, b], dtype=np.float64) / 255.0)
    alpha = a / 100.0
    metal = float(mat["metal"]) if mat else 0.0
    gloss = float(mat["gloss"]) if mat else 0.28
    m = {"name": ("mat%d_%02x%02x%02x" % (mid, r, g, b)),
         "pbrMetallicRoughness": {"baseColorFactor": [float(lin[0]), float(lin[1]), float(lin[2]), float(alpha)],
                                  "metallicFactor": float(min(max(metal, 0.0), 1.0)),
                                  "roughnessFactor": _roughness(gloss)},
         "doubleSided": True}
    if tex_index is not None:
        m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": int(tex_index), "texCoord": 0}
        if white:                                                   # "normal" karışım: doku yüzey rengini değiştirir
            m["pbrMetallicRoughness"]["baseColorFactor"][:3] = [1.0, 1.0, 1.0]
    if rough is not None:                                           # rough = (texture indeksi, texCoord)
        m["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": int(rough[0]), "texCoord": int(rough[1])}
        m["pbrMetallicRoughness"]["roughnessFactor"] = 1.0
    if alpha < 0.999:
        m["alphaMode"] = "BLEND"
    lum = float(mat["lum"]) if mat else 0.0
    if lum > 0.0:
        m["emissiveFactor"] = [float(min(1.0, lin[0] * lum)), float(min(1.0, lin[1] * lum)), float(min(1.0, lin[2] * lum))]
    return m


def export_glb(scene, path, smooth=None):
    """Scene -> .glb. Nesne başına bir düğüm/mesh, (malzeme, renk, saydamlık) başına bir primitive. Döner: sahne sınırları
    (metre, glTF ekseni) olarak (min, max) dizileri."""
    tri = scene.tri
    n = len(tri)
    if smooth is None:
        smooth = bool(getattr(S, "SMOOTH", True)) and n <= int(getattr(S, "SMOOTH_MAX_TRIS", 1500000))
    if smooth:
        nrm = scene.smooth_normals(float(getattr(S, "SMOOTH_ANGLE", 45.0)))
    else:
        nrm = np.repeat(scene.n_unit[:, None, :], 3, axis=1)
    pos_gl = (_to_gl(tri) * MM).astype(np.float32)                  # (n,3,3)
    nrm_gl = _to_gl(nrm).astype(np.float32)
    col8 = np.clip(np.rint(np.asarray(scene.colors) * 255.0), 0, 255).astype(np.int64)
    a8 = np.clip(np.rint(np.asarray(scene.alpha) * 100.0), 0, 100).astype(np.int64)
    keys = np.column_stack([scene.oid.astype(np.int64), scene.mid.astype(np.int64), col8, a8])
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(len(uniq) + 1))

    buf = bytearray()
    views, accs, mats, mat_index, by_obj = [], [], [], {}, {}
    images, samplers, textures, tex_cache = [], [], [], {}

    def add_attr(arr, with_minmax):
        off = len(buf)
        buf.extend(arr.tobytes())
        views.append({"buffer": 0, "byteOffset": off, "byteLength": int(arr.nbytes), "target": 34962})
        acc = {"bufferView": len(views) - 1, "componentType": 5126, "count": int(len(arr)),
               "type": "VEC3" if arr.shape[1] == 3 else "VEC2"}
        if with_minmax:
            acc["min"] = [float(x) for x in arr.min(0)]
            acc["max"] = [float(x) for x in arr.max(0)]
        accs.append(acc)
        return len(accs) - 1

    def add_rgb(ck, rgb, clamp):
        """8 bit RGB görüntü -> glTF texture indeksi (PNG gömülür; aynı anahtar bir kez)."""
        if ck in tex_cache:
            return tex_cache[ck]
        data = _png_bytes(rgb)
        off = len(buf)
        buf.extend(data)
        buf.extend(b"\x00" * ((4 - len(buf) % 4) % 4))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        images.append({"bufferView": len(views) - 1, "mimeType": "image/png"})
        wrap = 33071 if clamp else 10497
        samplers.append({"magFilter": 9729, "minFilter": 9987, "wrapS": wrap, "wrapT": wrap})
        textures.append({"sampler": len(samplers) - 1, "source": len(images) - 1})
        tex_cache[ck] = len(textures) - 1
        return tex_cache[ck]

    def add_texture(tx, clamp):
        rgb = np.clip(np.rint(np.power(np.clip(tx.levels[0], 0.0, 1.0), 1.0 / 2.2) * 255.0), 0, 255).astype(np.uint8)
        return add_rgb((id(tx), bool(clamp)), rgb, clamp)

    from . import textures as _tex
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for u in range(len(uniq)):
        oid, mid, r, g, b, a = (int(x) for x in uniq[u])
        idx = order[bounds[u]:bounds[u + 1]]
        if len(idx) == 0:
            continue
        p = pos_gl[idx].reshape(-1, 3)
        q = nrm_gl[idx].reshape(-1, 3)
        lo = np.minimum(lo, p.min(0))
        hi = np.maximum(hi, p.max(0))
        mk = (mid, r, g, b, a)
        mat = scene.mtable[mid - 1] if 0 < mid <= len(scene.mtable) else None
        L = _color_layer(mat)
        tx = _tex.load(L["tex"], L.get("tile")) if L else None
        G = _gray_layer(mat, "gloss")
        gx = _tex.load(G["tex"], G.get("tile")) if G else None
        if gx is None:
            G = None
        g_uv1 = (G is not None) and not (tx is not None and _same_place(L, G))      # pürüzlülük kendi UV kümesini ister
        if mk not in mat_index:
            mat_index[mk] = len(mats)
            ti = add_texture(tx, bool(getattr(tx, "clamp", False))) if tx is not None else None
            rg = None
            if gx is not None:
                gloss0 = float(mat["gloss"]) if mat else 0.28
                gop, ginv = float(G.get("opacity", 1.0)), bool(G.get("invert", False))
                gi = add_rgb((id(gx), "rough", gloss0, gop, ginv), _rough_image(gx, gloss0, gop, ginv),
                             bool(getattr(gx, "clamp", False)))
                rg = (gi, 1 if (g_uv1 and tx is not None) else 0)
            mats.append(_gltf_material(scene, mk, ti, white=(L is not None and str(L.get("blend", "normal")) == "normal"), rough=rg))
        attrs = {"POSITION": add_attr(p, True), "NORMAL": add_attr(q, False)}

        def uv_of(LL, xx):
            w_ = float(LL.get("tex_scale", 0.0))
            w_ = w_ if w_ > 0 else xx.rel * scene.diag
            return add_attr(_layer_uv(scene, LL, xx, max(w_, 1e-6 * scene.diag), idx).reshape(-1, 2), False)
        if tx is not None:
            attrs["TEXCOORD_0"] = uv_of(L, tx)
        if gx is not None and (g_uv1 or tx is None):
            attrs["TEXCOORD_1" if tx is not None else "TEXCOORD_0"] = uv_of(G, gx)
        prim = {"attributes": attrs, "material": mat_index[mk], "mode": 4}
        by_obj.setdefault(oid, []).append(prim)

    meshes, nodes = [], []
    for oid in sorted(by_obj):
        nm = scene.onames[oid] if 0 <= oid < len(scene.onames) else "Nesne"
        meshes.append({"name": nm, "primitives": by_obj[oid]})
        nodes.append({"name": nm, "mesh": len(meshes) - 1})
    gltf = {"asset": {"version": "2.0", "generator": "FC YBT Raytracer godot_bridge"},
            "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes, "meshes": meshes,
            "materials": mats, "accessors": accs, "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}]}
    if textures:
        gltf["images"], gltf["samplers"], gltf["textures"] = images, samplers, textures
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binc = bytes(buf) + b"\x00" * ((4 - len(buf) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binc)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(binc), 0x004E4942))
        f.write(binc)
    return lo, hi


# ------------------------------------------------------------------ sahne açıklaması (json)
def _cam_basis(cam):
    fwd = _unit(cam["fwd"])
    right = _unit(np.cross(fwd, np.asarray(cam["up"], dtype=np.float64)))
    up = np.cross(right, fwd)
    return fwd, right, up


def default_lights(cam):
    """Panelin klasik ışıkları (S.LIGHTS; açılar kameraya göre) -> dünya uzayında yönlü ışık sözlükleri."""
    fwd, right, up = _cam_basis(cam)
    res = []
    for L in getattr(S, "LIGHTS", []):
        if not L.get("on", True):
            continue
        h, v = math.radians(float(L["h"])), math.radians(float(L["v"]))
        to_light = -fwd * math.cos(v) * math.cos(h) + right * math.cos(v) * math.sin(h) + up * math.sin(v)
        res.append(dict(kind="distant", wdir=tuple(to_light), color=tuple(L.get("color", (1, 1, 1))),
                        power=float(L.get("power", 0.5)), shadow=bool(L.get("shadow", False))))
    return res


def _light_json(L, center, k):
    kind = L.get("kind", "distant")
    d = dict(kind=kind, color=[float(c) for c in L.get("color", (1, 1, 1))[:3]], shadow=bool(L.get("shadow", False)),
             energy=float(L.get("power", 0.5)) * ENERGY.get(kind, 1.0))
    if kind == "distant":
        d["dir"] = [float(x) for x in _to_gl(-_unit(L["wdir"]))]
        d["angular"] = float(min(max(float(L.get("soft", 0.5)), 0.0), 10.0))      # ışığın görünür açısı (derece): yumuşak gölge
        return d
    p = (np.asarray(L["wpos"], dtype=np.float64) - center) * MM * k
    d["pos"] = [float(x) for x in _to_gl(p)]
    ref = float(L.get("ref", 0.0))
    if ref <= 0.0:                                                  # 0 = otomatik: ışığın sahne merkezine uzaklığı (ışın izleyiciyle aynı)
        ref = float(np.linalg.norm(np.asarray(L["wpos"], dtype=np.float64) - center))
    ref = max(ref, 1.0) * MM * k
    d["size"] = float(max(0.0, float(L.get("radius", 0.0))) * MM * k)               # ışık kaynağı boyu (m): yumuşak gölge
    fall = str(L.get("falloff", "inverse_square"))
    # Godot: (1 - d/menzil)^üs. Menzil = 4 x referans uzaklık; üs 2 ~ ters kare (1-2 referans aralığında yakın), 1 = doğrusal, 0 = sönme yok
    d["range"] = float(max(ref * 4.0, 1.0)) if fall != "none" else float(max(ref * 400.0, 1000.0))
    d["atten"] = {"inverse_square": 2.0, "linear": 1.0}.get(fall, 0.0)
    at_ref = (1.0 - 0.25) ** d["atten"]
    d["energy"] = 1.3 * float(L.get("power", 0.5)) * ENERGY["distant"] / max(at_ref, 1e-3)    # 1.3: ışın izleyicide yakın yüzeyde ters kare sıcak nokta verir
    if kind == "spot":
        d["dir"] = [float(x) for x in _to_gl(_unit(L["axis"]))]
        cone = float(L.get("cone", 45.0))
        d["angle"] = float(min(max(cone / 2.0, 1.0), 89.0))
        edge = max(0.0, float(L.get("edge", 10.0)))
        d["angle_atten"] = float(min(1.0, 0.6 / (1.0 + edge / 10.0)))
    return d


def build_package(scene, outdir, camera=None, aspect=None, lights=None, width=1280, name="scene", supersample=2):
    """glb + json + Godot proje şablonunu `outdir` klasörüne yazar. Döner: yollar sözlüğü ve çıktı boyutu."""
    if camera is None:
        from . import api
        camera = api.fit_camera(scene)
    camera = dict(camera)
    aspect = float(aspect) if aspect else 16.0 / 9.0
    w = int(width)
    h = max(1, int(round(w / aspect)))
    outdir = os.path.abspath(outdir)                                # Godot göreli yolları kendi çalışma klasörüne göre çözer
    os.makedirs(outdir, exist_ok=True)
    if os.path.isdir(TEMPLATE_DIR):
        for fn in os.listdir(TEMPLATE_DIR):
            shutil.copy2(os.path.join(TEMPLATE_DIR, fn), os.path.join(outdir, fn))
    glb = os.path.join(outdir, name + ".glb")
    lo, hi = export_glb(scene, glb)
    diag_m = float(np.linalg.norm(hi - lo))
    k = 4.0 / max(diag_m, 1e-9)                                     # Godot'ta sahne köşegeni ~4 birim (ışık/gölge ayarları ölçekten bağımsız kalır)
    center = np.asarray(scene.center, dtype=np.float64)

    fwd, _right, up = _cam_basis(camera)
    pos = (np.asarray(camera["pos"], dtype=np.float64) - center) * MM * k
    cam = dict(pos=[float(x) for x in _to_gl(pos)], fwd=[float(x) for x in _to_gl(fwd)], up=[float(x) for x in _to_gl(up)],
               persp=bool(camera.get("persp", True)), keep="height" if aspect >= 1.0 else "width")
    if cam["persp"]:
        cam["fov"] = float(math.degrees(float(camera["size"])))
    else:
        cam["size"] = float(camera["size"]) * MM * k
    if lights is None:
        lights = []
    if not lights:
        lights = default_lights(camera)
    mode = str(getattr(S, "ENV_MODE", "off"))
    env = dict(top=[float(c) for c in S.BG_TOP], bottom=[float(c) for c in S.BG_BOTTOM],
               ambient=float(S.AMBIENT) * AMB_K, ambient_color=[float(c) for c in S.AMBIENT_COLOR],
               hdri="", hdri_rot=float(S.ENV_ROT), hdri_energy=float(S.ENV_INTENSITY), show_bg=bool(S.ENV_BACKGROUND))
    if mode == "hdri" and S.ENV_FILE and os.path.isfile(S.ENV_FILE):
        env["hdri"] = os.path.abspath(S.ENV_FILE)
    floor = dict(on=bool(S.FLOOR), y=float((lo[1] + float(S.FLOOR_OFFSET) * MM) * k), color=[float(c) for c in S.FLOOR_COLOR],
                 size=float(4.0 * float(S.FLOOR_RADIUS) / 2.0), reflect=float(S.FLOOR_REFLECT))
    cfg = dict(version=1, size=[w, h], supersample=int(max(1, supersample)), scale=float(k),
               camera=cam, lights=[_light_json(L, center, k) for L in lights], env=env, floor=floor,
               tonemap=TONEMAP.get(str(S.TONEMAP), "linear"), exposure=float(S.EXPOSURE))
    jpath = os.path.join(outdir, name + ".json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    return dict(dir=outdir, glb=glb, json=jpath, size=(w, h), scale=k)


# ------------------------------------------------------------------ Godot çalıştırma
def find_godot(path=None):
    """Godot çalıştırılabilir dosyası: verilen yol > GODOT_PATH ayarı > GODOT_BIN ortam değişkeni > PATH."""
    for p in (path, getattr(S, "GODOT_PATH", ""), os.environ.get("GODOT_BIN", "")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    for nm in ("godot4", "godot"):
        p = shutil.which(nm)
        if p:
            return p
    return None


def _run_godot_once(exe, pkg, out_png, ss, renderer, timeout, extra_args):
    w, h = pkg["size"]
    cmd = [exe, "--path", pkg["dir"], "--rendering-method", renderer, "--resolution", "%dx%d" % (w * ss, h * ss),
           "-s", "res://render_scene.gd", "--", pkg["json"], pkg["glb"], out_png]
    if extra_args:
        cmd[1:1] = list(extra_args)
    have_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.name == "nt")
    if not have_display and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 %dx%dx24" % (w * ss + 64, h * ss + 64)] + cmd
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    log = res.stdout.decode("utf-8", "replace")
    if res.returncode != 0 or not os.path.isfile(out_png):
        raise RuntimeError("Godot render başarısız (kod %s):\n%s" % (res.returncode, log[-2000:]))


def run_godot(exe, pkg, out_png, supersample=2, renderer="forward_plus", timeout=600, extra_args=None):
    """`build_package` çıktısıyla Godot'u çalıştırıp PNG yazar (iş parçacığından da çağrılabilir; FreeCAD'e dokunmaz).
    Ekran yoksa (DISPLAY/WAYLAND_DISPLAY tanımsızsa) xvfb-run kullanılır.
    renderer: forward_plus (Vulkan; AO, yumuşak gölge, yansıma) / mobile (Vulkan) / gl_compatibility (her yerde çalışır, AO ve
    yansıma yok). Vulkan başlatılamazsa otomatik gl_compatibility ile yeniden denenir. Döner: out_png yolu."""
    ss = max(1, int(supersample))
    out_png = os.path.abspath(out_png)
    if os.path.isfile(out_png):
        os.remove(out_png)                                          # eski dosya başarılı sanılmasın
    try:
        _run_godot_once(exe, pkg, out_png, ss, renderer, timeout, extra_args)
    except Exception:
        if renderer == "gl_compatibility":
            raise
        if os.path.isfile(out_png):
            os.remove(out_png)
        _run_godot_once(exe, pkg, out_png, ss, "gl_compatibility", timeout, extra_args)
    return out_png


def render(scene, out_png, godot=None, camera=None, aspect=None, lights=None, width=1280, workdir=None,
           supersample=2, renderer="forward_plus", timeout=600, keep=False, extra_args=None):
    """Sahneyi Godot ile render edip `out_png` olarak kaydeder (paketi kurar + çalıştırır). Döner: out_png yolu."""
    import tempfile
    exe = find_godot(godot)
    if exe is None:
        raise RuntimeError("Godot bulunamadı (Seçenekler'den yolunu belirtin).")
    tmp = os.path.abspath(workdir) if workdir else tempfile.mkdtemp(prefix="ybt_godot_")
    pkg = build_package(scene, tmp, camera, aspect, lights, width, supersample=supersample)
    try:
        return run_godot(exe, pkg, out_png, supersample, renderer, timeout, extra_args)
    finally:
        if not keep and workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)
