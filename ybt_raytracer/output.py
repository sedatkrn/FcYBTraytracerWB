# -*- coding: utf-8 -*-
import json
import os
import struct
import time
import zlib
from . import settings as S
from . import paths as _paths

META_KEY = "YBTRayTracer"          # tüm ayarlar (JSON) bu anahtarla gömülür


def write_png(path, arr, meta=None):
    """Bağımlılıksız PNG yazıcı. arr: (h, w, 3) uint8. meta: {anahtar: metin} -> iTXt (UTF-8) parçaları olarak gömülür."""
    h, w, _ = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    for k, v in (meta or {}).items():               # iTXt: anahtar \0 sıkıştırma(0) yöntem(0) dil \0 çeviri \0 metin
        png += chunk(b"iTXt", str(k).encode("latin-1", "replace")[:79] + b"\x00\x00\x00\x00\x00" + str(v).encode("utf-8"))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def read_png_meta(path):
    """PNG içindeki tEXt / iTXt metinleri -> {anahtar: metin}."""
    out = {}
    with open(path, "rb") as f:
        data = f.read()
    pos = 8
    while pos + 8 <= len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"tEXt":
            k, _, v = body.partition(b"\x00")
            out[k.decode("latin-1")] = v.decode("latin-1")
        elif typ == b"iTXt":
            k, _, rest = body.partition(b"\x00")
            comp, rest = rest[0], rest[2:]
            _lang, _, rest = rest.partition(b"\x00")
            _tr, _, v = rest.partition(b"\x00")
            out[k.decode("latin-1")] = (zlib.decompress(v) if comp else v).decode("utf-8", "replace")
        elif typ == b"IEND":
            break
        pos += 12 + n
    return out


def _onoff(v):
    return "on" if v else "off"


def summary():
    """Ayarların okunabilir özeti (PNG 'Description' alanı için, İngilizce: her programda okunsun)."""
    env = str(S.ENV_MODE)
    lines = [
        "Quality: tess %.4g, AA %s, bounces %d" % (S.TESS_FACTOR, ("%dx%d" % (S.AA, S.AA)) if int(S.AA) >= 2 else "off", int(S.BOUNCES)),
        "Shadows %s, soft shadow %s, reflections %s, refraction %s, glass %s, smooth %s" % (
            _onoff(S.SHADOWS), ("%g deg x%g" % (S.SOFT_SHADOW, S.SOFT_SCALE)) if S.SOFT_SHADOW > 0 else "off",
            _onoff(S.REFLECTIONS), _onoff(S.REFRACTION), _onoff(S.GLASS), _onoff(S.SMOOTH)),
        "AO %s" % (("on (strength %d%%, reach %d%%, %d rays)" % (S.AO_STRENGTH * 100, S.AO_RADIUS * 100, S.AO_SAMPLES)) if S.AO else "off"),
        "Indirect light (GI) %s" % (("on (strength %d%%, %d rays, blur %d px, ambient x%d%%)" % (
            S.GI_STRENGTH * 100, max(int(S.GI_SAMPLES), int(S.AO_SAMPLES)), int(S.GI_BLUR), S.GI_AMBIENT * 100)
            + ("" if getattr(S, "GI_CAVITY", True) else ", no enclosed-space darkening"))
            if getattr(S, "GI", False) else "off"),
        "Sky light %s" % (("on (%d%%)" % (S.SKY_LIGHT * 100)) if float(getattr(S, "SKY_LIGHT", 0.0)) > 0 and env == "off" else "off"),
        "Lights: %s, ambient %d%%" % ("light objects (panel lights if the scene has none)" if str(getattr(S, "LIGHT_MODE", "")) == "objects" else
                                       "FreeCAD" if getattr(S, "USE_FC_LIGHTS", False) else
                                       ", ".join("%d%%%s" % (l["power"] * 100, " shadow" if l.get("shadow") else "")
                                                 for l in S.LIGHTS if l.get("on", True)) or "none", S.AMBIENT * 100),
        "Environment: %s" % ("off" if env == "off" else "%s%s, intensity %d%%, light %s, keep lights %s" % (
            env, (" " + os.path.basename(S.ENV_FILE)) if env == "hdri" and S.ENV_FILE else "", S.ENV_INTENSITY * 100,
            _onoff(S.ENV_LIGHT), _onoff(S.ENV_KEEP_LIGHTS))),
        "Floor: %s" % (("on, texture %s (%s), reflect %d%%%s" % (
            os.path.basename(str(S.FLOOR_TEXTURE)) or "none", ("%g mm" % S.FLOOR_TEX_SCALE) if S.FLOOR_TEX_SCALE > 0 else "auto size",
            S.FLOOR_REFLECT * 100, (", material " + str(S.FLOOR_MATERIAL)) if getattr(S, "FLOOR_MATERIAL", "") else ""))
            if S.FLOOR else "off"),
        "Tone mapping %s, exposure %d%%, Fresnel %s" % (S.TONEMAP, S.EXPOSURE * 100, _onoff(S.FRESNEL)),
        "Lens flare %s, bloom %s" % (
            ("on (%d%%%s)" % (float(getattr(S, "FLARE_INTENSITY", 1.0)) * 100,
                              (", sun %s %d%%" % (getattr(S, "FLARE_SUN_STYLE", "Sun"), float(getattr(S, "FLARE_SUN_INTENSITY", 1.0)) * 100))
                              if getattr(S, "FLARE_SUN", False) else "")) if getattr(S, "FLARE", True) else "off",
            ("on (strength %d%%, threshold %d%%, radius %g%%)" % (float(S.BLOOM_STRENGTH) * 100, float(S.BLOOM_THRESHOLD) * 100,
                                                                 float(S.BLOOM_RADIUS))) if getattr(S, "BLOOM", False) else "off"),
    ]
    return "\n".join(lines)


def render_meta(title="", extra=None):
    """PNG'ye gömülecek bilgiler: yazılım, başlık, zaman, okunabilir özet ve tüm ayarlar (JSON, geri yüklemeye uygun)."""
    from . import __version__
    from . import userdefaults as _ud
    desc = summary()
    if extra:
        desc = "\n".join("%s: %s" % (k, v) for k, v in extra.items()) + "\n" + desc
    snap = {k: v for k, v in _ud.snapshot().items() if k not in ("THREADS", "PARALLEL")}
    return {"Software": "FC YBT Raytracer v%s (FreeCAD)" % __version__,
            "Title": str(title or ""),
            "Creation Time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "Description": desc,
            META_KEY: json.dumps(snap, ensure_ascii=False, sort_keys=True)}


def pick_out_path(doc_filename):
    """Yazılabilir bir klasör seç: ayarlı çıktı klasörü (DIR_OUT) -> belge klasörü -> ~/Pictures -> ev dizini -> geçici klasör."""
    if S.OUT_NAME:
        return S.OUT_NAME
    name = (os.path.splitext(os.path.basename(doc_filename))[0] + "_render.png"
            if doc_filename else "FreeCAD_render.png")
    od = _paths.out_dir(doc_filename)                        # ayarlı çıktı klasörü (yoksa oluşturulur) en başta denenir
    if od:
        try:
            os.makedirs(od, exist_ok=True)
        except OSError:
            od = ""
        if od and os.access(od, os.W_OK):
            return os.path.join(od, name)
    dirs = []
    if doc_filename:
        dirs.append(os.path.dirname(doc_filename))
    home = os.path.expanduser("~")
    dirs += [os.path.join(home, "Pictures"), os.path.join(home, "Resimler"), home]
    import tempfile
    dirs.append(tempfile.gettempdir())
    for d in dirs:
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, name)
    return os.path.join(home, name)
