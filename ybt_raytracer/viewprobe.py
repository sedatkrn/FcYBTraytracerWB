# -*- coding: utf-8 -*-
"""Görünüm yansıma sondası: sahneyi, sahnenin ortasından 6 yöne (küp yüzleri) render ile pişirip tek bir 3x2 atlas olarak
shader'a verir. Parlak yüzeyler ve cam, nesneleri / zemini bu atlastan (kutu paralaksı düzeltmeli) yansıtır; render'ın
yansımasına yakın görünür, ama gerçek zamanlıdır. Pişirme arka plan iş parçacığında çalışır; sahne / ışık / malzeme /
ortam değişince (imza) yeniden pişirilir.

Gökyüzü ayrı tutulur: her yüz iki kez (siyah ve beyaz arka planla) çizilir; fark = kapsama (alfa). Gökyüzü yerine
shader'daki tam çözünürlüklü HDRI kullanılır."""
import threading
import types

import numpy as np

FACE = 96
_AXES = [((1, 0, 0), (0, 0, 1)), ((-1, 0, 0), (0, 0, 1)), ((0, 1, 0), (0, 0, 1)),
         ((0, -1, 0), (0, 0, 1)), ((0, 0, 1), (0, -1, 0)), ((0, 0, -1), (0, 1, 0))]
_SIG_KEYS = ("ENV_MODE", "ENV_FILE", "ENV_INTENSITY", "ENV_ROT", "ENV_REFLECT", "ENV_BACKGROUND", "ENV_LIGHT", "ENV_SUN",
             "ENV_KEEP_LIGHTS", "AMBIENT", "AMBIENT_COLOR", "FLOOR", "FLOOR_COLOR", "FLOOR_REFLECT", "FLOOR_OPACITY",
             "FLOOR_OFFSET", "FLOOR_MATERIAL", "FLOOR_TEXTURE", "FLOOR_TEX_SCALE", "BG_TOP", "BG_BOTTOM", "SKY_GRADIENT",
             "SHADOWS", "GLASS", "REFLECTIONS", "REFLECT_GLASS", "REFLECT_SURF", "BOUNCES", "FRESNEL", "IOR", "LIGHT_MODE",
             "TONEMAP", "EXPOSURE", "SPEC", "SHININESS")

_st = {"sig": None, "data": None, "ver": 0, "thread": None, "result": None, "pending": None}
_lock = threading.Lock()


def basis():
    """Yüz tabanları [(sağ, yukarı, ileri)] — render.camera_basis ile aynı."""
    from .core import camera_basis
    out = []
    for f, u in _AXES:
        r, uu, ff = camera_basis(np.array(f, float), np.array(u, float))
        out.append((r, uu, ff))
    return out


def signature(sc, lights, S):
    parts = [hash(sc.tri.tobytes()), hash(sc.colors.tobytes()), hash(sc.alpha.tobytes()), hash(sc.mid.tobytes()),
             repr(sc.mtable), repr(lights)]
    parts += [repr(getattr(S, k, None)) for k in _SIG_KEYS]
    return hash(tuple(parts))


def current():
    return _st["data"]


def take_result():
    """Biten pişirmeyi alır (varsa) ve geçerli sondayı günceller. Döner: yeni sonda mı?"""
    with _lock:
        r = _st["result"]
        _st["result"] = None
    if r is None:
        return False
    _st["data"] = r
    return True


def busy():
    t = _st["thread"]
    return t is not None and t.is_alive()


def _bake(sc, lights, C, lo, hi, S, sig):
    from . import render as R
    ns = types.SimpleNamespace(**{k: getattr(S, k) for k in dir(S) if not k.startswith("__")})
    ns.AA = 1
    ns.SOFT_SHADOW = False
    ns.AO = False
    ns.GI = False
    ns.ENV_BACKGROUND = False
    ns.TONEMAP = "none"
    ns.EXPOSURE = 1.0
    ns.FLARE = False
    ns.BLOOM = False
    ns.FLARE_SUN = False
    ns._OBJ_LIGHTS = lights
    old = R.S
    R.S = ns
    try:
        F = FACE
        rgb = np.zeros((2 * F, 3 * F, 3))
        al = np.zeros((2 * F, 3 * F))
        for i, (f, u) in enumerate(_AXES):
            outs = []
            for col in ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)):
                ns.BG_TOP = ns.BG_BOTTOM = col
                ns.SKY_GRADIENT = []
                img = R.render(sc, dict(pos=np.array(C, float), fwd=f, up=u, persp=True, size=np.pi / 2.0), F, F, shadows=True)
                if img is None:
                    return
                outs.append(np.asarray(img, dtype=np.float64) / 255.0)
            a = np.clip(1.0 - (outs[1] - outs[0]).mean(2), 0.0, 1.0)
            r0, c0 = (i // 3) * F, (i % 3) * F
            rgb[r0:r0 + F, c0:c0 + F] = outs[0] ** 2.2
            al[r0:r0 + F, c0:c0 + F] = a
        ver = _st["ver"] + 1
        res = dict(sig=sig, ver=ver, C=tuple(float(x) for x in C), lo=tuple(float(x) for x in lo),
                   hi=tuple(float(x) for x in hi), rgb=rgb, al=al, basis=basis(), F=F)
        with _lock:
            if _st["sig"] == sig:
                _st["result"] = res
                _st["ver"] = ver
    except Exception:
        import traceback
        import FreeCAD
        FreeCAD.Console.PrintWarning("YBT yansıma sondası: " + traceback.format_exc() + "\n")
    finally:
        R.S = old


def request(sc, lights, S):
    """Sahne / ışık / ayar imzası değiştiyse arka planda yeniden pişirir. Döner: yeni bir pişirme başladı mı?"""
    sig = signature(sc, lights, S)
    if sig == _st["sig"]:
        return False
    if busy():
        _st["pending"] = (sc, lights, S)          # bitince en sonuncusu pişirilir
        return False
    _st["sig"] = sig
    flat = np.asarray(sc.tri, dtype=np.float64).reshape(-1, 3) + sc.center
    lo, hi = flat.min(0), flat.max(0)
    C = (lo + hi) / 2.0
    t = threading.Thread(target=_bake, args=(sc, lights, C, lo, hi, S, sig), daemon=True)
    _st["thread"] = t
    t.start()
    return True


def pending_request():
    p = _st["pending"]
    _st["pending"] = None
    return p


def pending_peek():
    return _st["pending"] is not None


def reset():
    with _lock:
        _st["sig"] = None
        _st["data"] = None
        _st["result"] = None
        _st["pending"] = None


# ----------------------------------------------------------------------------------- GLSL
def _v3(v):
    return "vec3(%.6f, %.6f, %.6f)" % (float(v[0]), float(v[1]), float(v[2]))


def glsl(p):
    """probelook(P, D) -> vec4(önceden çarpılmış renk, kapsama). Birimler ENVU_PRC (renk) ve ENVU_PRA (8 x alfa)."""
    ff = ", ".join(_v3(b[2]) for b in p["basis"])
    fr = ", ".join(_v3(b[0]) for b in p["basis"])
    fu = ", ".join(_v3(b[1]) for b in p["basis"])
    half = 0.5 / p["F"]
    return """vec4 probelook(vec3 P, vec3 D){
  D += vec3(1e-6);
  vec3 t1 = (%(LO)s - P) / D; vec3 t2 = (%(HI)s - P) / D;
  vec3 tm = max(t1, t2);
  float t = max(min(min(tm.x, tm.y), tm.z), 0.0);
  vec3 d = normalize(P + D * t - %(C)s);
  const vec3 FF[6] = vec3[6](%(FF)s);
  const vec3 FR[6] = vec3[6](%(FR)s);
  const vec3 FU[6] = vec3[6](%(FU)s);
  vec3 ad = abs(d);
  int ma = ad.x >= ad.y && ad.x >= ad.z ? 0 : (ad.y >= ad.z ? 1 : 2);
  int fi = ma * 2 + (d[ma] < 0.0 ? 1 : 0);
  float best = dot(d, FF[fi]);
  float x = dot(d, FR[fi]) / best, y = dot(d, FU[fi]) / best;
  float u = clamp(x * 0.5 + 0.5, %(H)s, 1.0 - %(H)s), v = clamp(0.5 - 0.5 * y, %(H)s, 1.0 - %(H)s);
  vec2 uv = vec2((float(fi - (fi / 3) * 3) + u) / 3.0, (float(fi / 3) + v) / 2.0);
  vec3 col = rgbm(texture2D(ENVU_PRC, uv));
  float a = clamp(rgbm(texture2D(ENVU_PRA, uv)).r / 8.0, 0.0, 1.0);
  return vec4(col, a);
}""" % dict(LO=_v3(p["lo"]), HI=_v3(p["hi"]), C=_v3(p["C"]), FF=ff, FR=fr, FU=fu, H="%.6f" % half)


def units(p):
    rgb = p["rgb"]
    al8 = np.repeat((p["al"] * 8.0)[:, :, None], 3, axis=2)
    key_c = ("rgbm", "prc", p["ver"])
    key_a = ("rgbm", "pra", p["ver"])
    return [(("rgbm", key_c, rgb, "clamp"), key_c), (("rgbm", key_a, al8, "clamp"), key_a)]
