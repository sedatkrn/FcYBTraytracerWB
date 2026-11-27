# -*- coding: utf-8 -*-
"""3B görünümde malzeme önizlemesi (gerçek zamanlı): malzemesi atanmış nesneler FreeCAD görünümünde YBT malzemesiyle
(renk, yayılı ışık, parlaklık vurgusu, metal, saydamlık, kendi ışığı ve renk kanalındaki görüntü doku katmanları) çizilir.

Nasıl çalışır
  * Her nesnenin ViewObject.RootNode'unun başına bir SoSeparator-dışı düğüm grubu eklenir: doku birimleri (SoTextureUnit +
    SoTexture2) ve GLSL SoShaderProgram. Gölgelendirici kaynağı malzemeden ÜRETİLİR (değerler sabit olarak gömülür); malzeme
    değişince yalnız kaynak metni değişir, Coin yeniden derler (birkaç ms).
  * Doku yerleşimi render.py ile aynıdır: dünya eksenleri, nesnenin sınır kutusu merkezine göre (ya da "dünya" seçeneğiyle mutlak),
    cubic / planar / silindirik / küresel izdüşüm, boyut, konum, dönme, ölçek, "nesneye sığdır", tek kez / kenar modları ve
    katman karışım modları. Dünya koordinatı gölgelendiricide kameranın ters görüş matrisiyle hesaplanır (sahne grafiğinin başındaki
    SoCallback her çizimde günceller); böylece Link / Assembly kopyaları (paylaşılan geometri) da doğru yerde dokulanır.
  * Kenar ve nokta çizgileri gölgelendiriciden etkilenmesin diye onların Separator'larına düz renk çizen küçük bir program eklenir.
  * Belgeye HİÇBİR ŞEY yazılmaz; önizleme kapanınca tüm düğümler kaldırılır.

Sınırlar: prosedürel katmanlar, gri kanal katmanları (parlaklık / yansıma ... haritaları), bump, yansıma, gölge, cam kırılması ve HDRI
yalnız render'da. Işık: görünümün ilk ışığı (FreeCAD headlight ya da viewlight önizlemesi) + ortam. FreeCAD'in ekran dışı görüntü
kaydı (Save picture) gölgelendiriciyi çalıştırmaz: önizleme açıkken resim kaydetmeden önce kapatın.
"""
import math

from . import materials as M

_NAME = "YBT_VIEWMAT"
_PLAIN_NAME = "YBT_VIEWMAT_PLAIN"
_CB_NAME = "YBT_VIEWMAT_CB"
MAX_LAYERS = 4
MAX_TEX = 1024                 # görünüme yüklenen doku en çok bu kenar boyunda (mip düzeyinden seçilir)

_st = {"on": False, "items": {}, "plain": [], "cbs": [], "iv": None, "tex": {}, "override": {}, "obs": None,
       "timer": None, "diag": 1000.0, "doc": None, "env": None}


# ------------------------------------------------------------------ GLSL
_VS = """
uniform vec4 ivc0; uniform vec4 ivc1; uniform vec4 ivc2;
varying vec3 wpos; varying vec3 wnrm; varying vec3 vpos; varying vec3 vnrm; varying vec3 wcen; varying vec3 whe; varying vec4 dcol; varying vec3 wcam;
vec3 toW(vec4 e){ return vec3(dot(ivc0, e), dot(ivc1, e), dot(ivc2, e)); }
void main(){
  vec4 e = gl_ModelViewMatrix * gl_Vertex;
  wpos = toW(e);
  vec3 en = gl_NormalMatrix * gl_Normal;
  wnrm = toW(vec4(en, 0.0));
  vpos = e.xyz; vnrm = en;
  wcen = toW(gl_ModelViewMatrix * vec4(%(LC)s, 1.0));
  vec3 ax = toW(gl_ModelViewMatrix * vec4(1.0, 0.0, 0.0, 0.0));
  vec3 ay = toW(gl_ModelViewMatrix * vec4(0.0, 1.0, 0.0, 0.0));
  vec3 az = toW(gl_ModelViewMatrix * vec4(0.0, 0.0, 1.0, 0.0));
  vec3 lh = %(LH)s;
  whe = abs(ax) * lh.x + abs(ay) * lh.y + abs(az) * lh.z;
  dcol = gl_Color;
  wcam = toW(vec4(0.0, 0.0, 0.0, 1.0));
  gl_Position = ftransform();
}
"""

_FS_HEAD = """
varying vec3 wpos; varying vec3 wnrm; varying vec3 vpos; varying vec3 vnrm; varying vec3 wcen; varying vec3 whe; varying vec4 dcol;
varying vec3 wcam;
%(SAMPLERS)s
const float PI2 = 6.28318530718;
vec3 lin(vec3 c){ return pow(max(c, 0.0), vec3(2.2)); }
float fsign(int ax, vec3 nl){ bool p = (ax == 0 ? nl.x : (ax == 1 ? nl.y : nl.z)) > 0.0; return (p == (ax != 1)) ? 1.0 : -1.0; }
float comp(vec3 v, int i){ return i == 0 ? v.x : (i == 1 ? v.y : v.z); }
vec3 blend(vec3 r, vec3 v, int m, float op){
  vec3 n = v;
  if (m == 1) n = r + v; else if (m == 2) n = r - v; else if (m == 3) n = r * v;
  else if (m == 4) n = r / max(v, vec3(1e-3)); else if (m == 5) n = abs(r - v);
  else if (m == 6) n = 1.0 - (1.0 - r) * (1.0 - v); else if (m == 7) n = max(r, v); else if (m == 8) n = min(r, v);
  return mix(r, n, op);
}
"""

_FS_MAIN = """
void main(){
  vec3 n = normalize(vnrm); if (!gl_FrontFacing) n = -n;
  vec3 wn = normalize(wnrm);
  vec3 dc = lin(dcol.rgb);
  vec3 alb = %(BASE)s;
%(LAYERS)s
  alb = max(alb, 0.0);
  vec3 albraw = alb;
  alb *= %(DIF)s;
  vec3 Ve = normalize(-vpos);
  vec3 Vw = normalize(wcam - wpos);
  vec3 wnf = gl_FrontFacing ? wn : -wn;
  float shin = %(SHIN)s;
  vec3 lit = %(AMB)s;
  vec3 spc = vec3(0.0);
%(LIGHTS)s
  spc *= %(SPEC)s * ((1.0 - %(METAL)s) + %(METAL)s * min(albraw * 1.4, vec3(1.0)));
  vec3 surf = (alb * lit + spc + %(LUM)s * albraw * 1.5) * %(EXPO)s;
%(TONEMAP)s
  vec3 outc = pow(clamp(surf, 0.0, 1.0), vec3(1.0 / 2.2));
%(HILITE)s
  gl_FragColor = vec4(outc, %(ALPHA)s);
}
"""

# tek ışık: Ln (yüzeyden ışığa birim yön), N, V, güç (pw), renk (lc), etki (ad: yayılı, as: parlama) -> lit / spc
_LIGHT_ONE = """  {
    vec3 N = %(N)s; vec3 V = %(V)s;
%(DIR)s
    float ndl = max(dot(N, Ln), 0.0);
    lit += %(AD)s * pw * ndl * %(LC)s;
    vec3 H = normalize(Ln + V);
    float sp = (ndl > 0.0) ? pow(max(dot(N, H), 0.0), shin) : 0.0;
    spc += %(AS)s * min(pw / 0.7, 1.5) * sp * %(LC)s;
  }"""


def _lights_glsl(doc, center):
    """Render'ın ışıkları -> GLSL: ışık nesneleri (dünya uzayı) ya da panelin varsayılan ışıkları (kamera uzayı)."""
    from . import settings as S
    out = []
    obj_lights = []
    if str(getattr(S, "LIGHT_MODE", "objects")) == "objects" and doc is not None:
        try:
            from . import lightobj
            obj_lights = lightobj.collect(doc)
        except Exception:
            obj_lights = []
    if obj_lights:
        for lt in obj_lights:
            col = [max(0.0, float(c)) ** 2.2 for c in lt["color"]]
            pw = float(lt["power"])
            if "wpos" in lt:
                lp = [float(x) for x in lt["wpos"]]
                ref = float(lt.get("ref", 0.0))
                if ref <= 0.0:
                    ref = max(1e-6, math.sqrt(sum((lp[k] - center[k]) ** 2 for k in range(3))))
                fall = str(lt.get("falloff", "inverse_square"))
                d = ["    vec3 dv = %s - wpos; float dd = max(length(dv), 1e-9); vec3 Ln = dv / dd;" % _v3(lp)]
                if fall == "inverse_square":
                    d.append("    float att = min(pow(%s / dd, 2.0), 16.0);" % _f(ref))
                elif fall == "linear":
                    d.append("    float att = min(%s / dd, 16.0);" % _f(ref))
                else:
                    d.append("    float att = 1.0;")
                if lt.get("kind") == "spot":
                    half = math.radians(max(0.5, min(float(lt.get("cone", 45.0)), 178.0)) / 2.0)
                    inn = max(0.0, half - math.radians(max(0.0, float(lt.get("edge", 10.0)))))
                    co, ci = math.cos(half), math.cos(inn)
                    d.append("    float cs = -dot(Ln, %s);" % _v3(lt.get("axis", (0.0, 0.0, -1.0))))
                    d.append("    float xs = clamp((cs - %s) / %s, 0.0, 1.0); att *= xs * xs * (3.0 - 2.0 * xs);"
                             % (_f(co), _f(max(ci - co, 1e-6))))
                d.append("    float pw = %s * att;" % _f(pw))
            else:
                w = [float(x) for x in lt["wdir"]]
                nn = math.sqrt(sum(x * x for x in w)) or 1.0
                d = ["    vec3 Ln = %s; float pw = %s;" % (_v3([x / nn for x in w]), _f(pw))]
            out.append(_LIGHT_ONE % dict(N="wnf", V="Vw", DIR="\n".join(d), LC=_v3(col),
                                         AD=_f(1.0 if lt.get("diffuse", True) else 0.0),
                                         AS=_f(1.0 if lt.get("spec", True) else 0.0)))
    else:                                                     # panelin ışıkları: kamera uzayında (sağ x, yukarı y, göze doğru +z)
        for lt in getattr(S, "LIGHTS", []):
            if not lt.get("on", True):
                continue
            if "dir" in lt:
                dx, dy, dz = lt["dir"]
                e = (-dx, -dy, -dz)
            else:
                hh, vv = math.radians(float(lt["h"])), math.radians(float(lt["v"]))
                e = (math.cos(vv) * math.sin(hh), math.sin(vv), math.cos(vv) * math.cos(hh))
            nn = math.sqrt(sum(x * x for x in e)) or 1.0
            col = [max(0.0, float(c)) ** 2.2 for c in lt["color"]]
            d = ["    vec3 Ln = %s; float pw = %s;" % (_v3([x / nn for x in e]), _f(float(lt["power"])))]
            out.append(_LIGHT_ONE % dict(N="n", V="Ve", DIR="\n".join(d), LC=_v3(col), AD="1.0", AS="1.0"))
    amb = [float(getattr(S, "AMBIENT", 0.28)) * max(0.0, float(c)) ** 2.2 for c in getattr(S, "AMBIENT_COLOR", (1, 1, 1))]
    tm = str(getattr(S, "TONEMAP", "none"))
    if tm == "soft":
        tms = "  surf = surf * (1.0 + surf * 0.25) / (1.0 + surf);"
    elif tm == "aces":
        tms = "  surf = (surf * (2.51 * surf + 0.03)) / (surf * (2.43 * surf + 0.59) + 0.14);"
    else:
        tms = ""
    return dict(LIGHTS="\n".join(out), AMB=_v3(amb), EXPO=_f(float(getattr(S, "EXPOSURE", 1.0))), TONEMAP=tms)

_PLAIN_VS = "void main(){ gl_FrontColor = gl_Color; gl_BackColor = gl_Color; gl_Position = ftransform(); }"
_PLAIN_FS = "void main(){ gl_FragColor = gl_Color; }"

_BLEND_ID = {"normal": 0, "add": 1, "sub": 2, "mul": 3, "div": 4, "diff": 5, "screen": 6, "max": 7, "min": 8}
_PLANE = ((1, 2), (0, 2), (0, 1))
_AX = "xyz"


def _f(x):
    """GLSL float sabiti."""
    x = float(x)
    if not math.isfinite(x):
        x = 0.0
    s = repr(x)
    return s if ("." in s or "e" in s or "n" in s) else s + ".0"


def _v3(c):
    return "vec3(%s, %s, %s)" % (_f(c[0]), _f(c[1]), _f(c[2]))


def _rot(L):
    """render._tex_params ile aynı dönme matrisi (R = Rz Ry Rx); Q' = Q @ R -> Q'_j = dot(Q, R[:, j])."""
    rx, ry, rz = [math.radians(float(L.get(k, 0.0))) for k in ("tex_rx", "tex_ry", "tex_rz")]
    if not (rx or ry or rz):
        return None
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))

    def mm(A, B):
        return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    R = mm(mm(Rz, Ry), Rx)
    return [(R[0][j], R[1][j], R[2][j]) for j in range(3)]


def _layer_glsl(i, L, tx, diag):
    """Tek görüntü katmanı -> GLSL (render._tex_sample ile aynı yerleşim). Değişkenler: alb (vec3, doğrusal renk)."""
    w_ = float(L.get("tex_scale", 0.0))
    tw = w_ if w_ > 0 else tx.rel * diag
    tw = max(tw, 1e-6 * diag)
    per = tw * tx.span
    sp = float(tx.span)
    asp = float(tx.aspect)
    s = [max(abs(float(L.get(k, 1.0))), 1e-6) * (1.0 if float(L.get(k, 1.0)) >= 0 else -1.0)
         for k in ("tex_sx", "tex_sy", "tex_sz")]
    pos = [float(L.get(k, 0.0)) for k in ("tex_px", "tex_py", "tex_pz")]
    world = bool(L.get("tex_world", False))
    fit = bool(L.get("tex_fit", False))
    proj = str(L.get("tex_proj", "cubic"))
    ax = int(L.get("tex_axis", 2))
    once = bool(getattr(tx, "clamp", False))
    cov_on = bool(getattr(tx, "once", False))
    shift = 0.5 if (once and not fit) else 0.0
    op = max(0.0, min(1.0, float(L.get("opacity", 1.0))))
    bm = _BLEND_ID.get(str(L.get("blend", "normal")), 0)
    inv = bool(L.get("invert", False))
    R = _rot(L)
    sav = sum(s) / 3.0
    o = []
    o.append("  {")
    o.append("    vec3 Q = wpos - %s - %s;" % ("vec3(0.0)" if world else "wcen", _v3(pos)))
    o.append("    vec3 nl = wn;")
    if R is not None:
        o.append("    Q = vec3(dot(Q, %s), dot(Q, %s), dot(Q, %s));" % (_v3(R[0]), _v3(R[1]), _v3(R[2])))
        o.append("    nl = vec3(dot(nl, %s), dot(nl, %s), dot(nl, %s));" % (_v3(R[0]), _v3(R[1]), _v3(R[2])))
    o.append("    Q /= %s;" % _v3(s))
    o.append("    vec3 he = %s;" % ("vec3(%s)" % _f(diag / 4.0) if world else "whe"))
    o.append("    vec3 tc = vec3(0.0); float cv = 0.0;")

    def samp(uexpr, vexpr, wexpr):
        lines = ["      vec2 uv = vec2(%s, %s) + vec2(%s);" % (uexpr, vexpr, _f(shift))]
        if once:
            lines.append("      float inb = (uv.x >= 0.0 && uv.x < 1.0 && uv.y >= 0.0 && uv.y < 1.0) ? 1.0 : 0.0;")
            lines.append("      uv = clamp(uv, 0.0, 0.9999);")
        else:
            lines.append("      float inb = 1.0;")
        lines.append("      tc += %s * lin(texture2D(ytex%d, uv).rgb); cv += %s * inb;" % (wexpr, i, wexpr))
        return lines

    if proj == "cubic":
        o.append("    vec3 w3 = pow(abs(nl), vec3(4.0)); w3 /= (w3.x + w3.y + w3.z);")
        for axx, (a_, b_) in enumerate(_PLANE):
            A, B = _AX[a_], _AX[b_]
            o.append("    if (w3.%s > 0.02) {" % _AX[axx])
            if fit:
                o.append("      float su = fsign(%d, nl);" % axx)
                u = "(su * Q.%s / max(2.0 * he.%s, 1e-9) + 0.5) / %s" % (A, A, _f(sp))
                v = "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (B, B, _f(sp))
            else:
                u = "Q.%s / %s" % (A, _f(per))
                v = "Q.%s / %s" % (B, _f(per * asp))
            o += samp(u, v, "w3.%s" % _AX[axx])
            o.append("    }")
        o.append("    tc /= max(w3.x * step(0.02, w3.x) + w3.y * step(0.02, w3.y) + w3.z * step(0.02, w3.z), 1e-9);")
        o.append("    cv /= max(w3.x * step(0.02, w3.x) + w3.y * step(0.02, w3.y) + w3.z * step(0.02, w3.z), 1e-9);")
    else:
        a_, b_ = _PLANE[ax]
        A, B, X = _AX[a_], _AX[b_], _AX[ax]
        o.append("    {")
        if fit and proj in ("cylindrical", "spherical"):
            o.append("      float ang = atan(Q.%s, Q.%s) / PI2 + 0.5;" % (B, A))
            if proj == "cylindrical":
                u, v = "ang / %s" % _f(sp), "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (X, X, _f(sp))
            else:
                o.append("      float rr = max(length(Q), 1e-12);")
                o.append("      float lat = asin(clamp(Q.%s / rr, -1.0, 1.0)) / 3.14159265 + 0.5;" % X)
                u, v = "ang / %s" % _f(sp), "(1.0 - lat) / %s" % _f(sp)
        elif fit and proj == "planar":
            o.append("      float su = fsign(%d, nl);" % ax)
            u = "(su * Q.%s / max(2.0 * he.%s, 1e-9) + 0.5) / %s" % (A, A, _f(sp))
            v = "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (B, B, _f(sp))
        elif proj in ("cylindrical", "spherical"):
            o.append("      float ang = atan(Q.%s, Q.%s) / PI2;" % (B, A))
            if proj == "cylindrical":
                o.append("      float R0 = %s;" % ("%s" % _f(diag / 4.0) if world else "max(he.%s, he.%s)" % (A, B)))
            else:
                o.append("      float R0 = %s;" % ("%s" % _f(diag / 4.0) if world else "max(he.x, max(he.y, he.z))"))
            o.append("      float rep = max(1.0, floor(PI2 * R0 / %s + 0.5));" % _f(sav * per))
            if proj == "cylindrical":
                u, v = "ang * rep", "Q.%s / %s" % (X, _f(per * asp))
            else:
                o.append("      float rr = max(length(Q), 1e-12);")
                o.append("      float lat = asin(clamp(Q.%s / rr, -1.0, 1.0)) / 3.14159265 + 0.5;" % X)
                u, v = "ang * rep", "lat * rep / 2.0 / %s" % _f(asp)
        else:
            u, v = "Q.%s / %s" % (A, _f(per)), "Q.%s / %s" % (B, _f(per * asp))
        o += samp(u, v, "1.0")
        o.append("    }")
    if inv:
        o.append("    tc = 1.0 - tc;")
    o.append("    vec3 nw = blend(alb, tc, %d, %s);" % (bm, _f(op)))
    if cov_on:
        o.append("    nw = alb + (nw - alb) * cv;")
    o.append("    alb = nw;")
    o.append("  }")
    return "\n".join(o)


def _color_layers(mat):
    """Renk kanalındaki etkin görüntü katmanları (alttan üste), en çok MAX_LAYERS. Döner [(katman, Texture)]."""
    from .render import load_texture
    out = []
    for L in M.layers_of(mat, "color"):
        if not (L.get("on", True) and L.get("type", "image") == "image" and L.get("tex")):
            continue
        t_ = load_texture(L["tex"], L.get("tile"))
        if t_ is None:
            continue
        out.append((L, t_))
        if len(out) >= MAX_LAYERS:
            break
    return out


def _hilite_glsl(dcols):
    """Seçim / ön seçim vurgusu: FreeCAD vurgulu yüzü kendi rengiyle (gl_Color) çizer. Gelen renk nesnenin bilinen ekran
    renklerinden hiçbirine benzemiyorsa vurgu sayılır ve gösterilir. Renk listesi yoksa / çok uzunsa algılama kapalı."""
    if not dcols or len(dcols) > 24:
        return ""
    conds = " && ".join("distance(dcol.rgb, %s) > 0.03" % _v3(c) for c in dcols)
    return "  if (%s) outc = mix(outc, dcol.rgb, 0.75);" % conds


def build_shaders(mat, lc, lh, diag, light=None, dcols=None):
    """Malzeme -> (vertex, fragment kaynağı, [Texture]). mat None: nesnenin kendi rengi, varsayılan yüzey.
    light: _lights_glsl çıktısı (None: panelin varsayılan ışıkları). dcols: nesnenin ekran renkleri (vurgu algılama)."""
    m = M.normalize(mat) if mat is not None else dict(M.DEFAULT)
    lays = _color_layers(m) if mat is not None else []
    vs = _VS % dict(LC=_v3(lc), LH=_v3(lh))
    samplers = "\n".join("uniform sampler2D ytex%d;" % i for i in range(len(lays)))
    base = "lin(%s)" % _v3(m["color"]) if m.get("color") is not None else "dc"
    lsrc = "\n".join(_layer_glsl(i, L, tx, diag) for i, (L, tx) in enumerate(lays))
    if light is None:
        light = _lights_glsl(None, (0.0, 0.0, 0.0))
    keys = dict(BASE=base, LAYERS=lsrc, DIF=_f(m["diffuse"]), SHIN=_f(M.shininess(m["gloss"])), SPEC=_f(m["spec"]),
                METAL=_f(m["metal"]), LUM=_f(m["lum"]), ALPHA=_f(max(0.0, min(1.0, 1.0 - float(m["transp"])))),
                HILITE=_hilite_glsl(dcols))
    keys.update(light)
    fs = (_FS_HEAD % dict(SAMPLERS=samplers)) + (_FS_MAIN % keys)
    return vs, fs, [tx for _L, tx in lays]


# ------------------------------------------------------------------ Coin düğümleri
def _coin():
    from pivy import coin
    return coin


def _tex_node(tx):
    """Texture -> paylaşılan SoTexture2 (sRGB 8 bit; mip düzeyi MAX_TEX'e sığan)."""
    import numpy as np
    coin = _coin()
    key = id(tx)
    node = _st["tex"].get(key)
    if node is not None:
        return node
    lv = 0
    while lv + 1 < len(tx.levels) and max(tx.levels[lv].shape[:2]) > MAX_TEX:
        lv += 1
    a = np.clip(np.asarray(tx.levels[lv], dtype=np.float64), 0.0, 1.0) ** (1.0 / 2.2)
    a8 = np.ascontiguousarray((a * 255.0 + 0.5).astype(np.uint8)[:, :, :3])
    h, w = a8.shape[:2]
    node = coin.SoTexture2()
    node.image.setValue(coin.SbVec2s(w, h), 3, a8.tobytes())
    node.model = coin.SoTexture2.MODULATE
    wrap = coin.SoTexture2.CLAMP if getattr(tx, "clamp", False) else coin.SoTexture2.REPEAT
    node.wrapS = wrap
    node.wrapT = wrap
    node.ref()
    _st["tex"][key] = (node, tx)                       # tx: id() geçerli kalsın (önbellek nesnesi silinmesin)
    return _st["tex"][key]


def _iv_params():
    """Ters görüş matrisi sütunları: tüm programlarda paylaşılan 3 parametre düğümü (SoCallback her çizimde doldurur)."""
    if _st["iv"] is None:
        coin = _coin()
        ps = []
        for k in range(3):
            p = coin.SoShaderParameter4f()
            p.name = "ivc%d" % k
            p.value = coin.SbVec4f(*[1.0 if j == k else 0.0 for j in range(4)])
            p.ref()
            ps.append(p)
        _st["iv"] = ps
    return _st["iv"]


def _cam_cb(_ud, action):
    """Çizim sırasında (kameradan SONRA): kameranın ters görüş matrisini parametrelere yaz. Yalnız değiştiyse yazılır: aynı
    karede aşağıdaki gölgelendiriciler yeni değeri alır; bildirim tek bir ek çizim ister, değer aynı kalınca döngü durur."""
    try:
        coin = _coin()
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        vm = coin.SoViewingMatrixElement.get(action.getState()).inverse()
        for j, p in enumerate(_iv_params()):
            v = (vm[0][j], vm[1][j], vm[2][j], vm[3][j])
            old = p.value.getValue().getValue()
            if max(abs(v[k] - old[k]) for k in range(4)) > 1e-9:
                p.value.setValue(coin.SbVec4f(*v))
    except Exception:
        pass


def _install_callbacks(gdoc):
    """Her 3B görünümün model kökünün (kamera ve ışıklardan sonra gelen düğüm) başına SoCallback ekler."""
    coin = _coin()
    for v in gdoc.mdiViewsOfType("Gui::View3DInventor"):
        try:
            sg = v.getSceneGraph()                         # FreeCAD'in model kökü: kamera bunun üstünde, görüş matrisi hazır
        except Exception:
            continue
        if any(s is sg or s == sg for s, _cb in _st["cbs"]):
            continue
        try:
            if any(sg.getChild(i).getName().getString() == _CB_NAME for i in range(sg.getNumChildren())):
                continue
        except Exception:
            pass
        cb = coin.SoCallback()
        cb.setName(_CB_NAME)
        cb.setCallback(_cam_cb)
        sg.ref()                                          # belge / görünüm kapansa da düğüm silinmesin (kaldırırken güvenli)
        cb.ref()
        sg.insertChild(cb, 0)
        _st["cbs"].append((sg, cb))


def _remove_callbacks():
    for sg, cb in _st["cbs"]:
        try:
            i = sg.findChild(cb)
            if i >= 0:
                sg.removeChild(i)
        except Exception:
            pass
        _unref(cb)
        _unref(sg)
    _st["cbs"] = []


def _unref(node):
    try:
        node.unref()
    except Exception:
        pass


def _program(vs_src, fs_src, ntex):
    coin = _coin()
    prog = coin.SoShaderProgram()
    vs = coin.SoVertexShader()
    vs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    vs.sourceProgram = vs_src
    fs = coin.SoFragmentShader()
    fs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    fs.sourceProgram = fs_src
    ivs = _iv_params()
    vs.parameter.setValues(0, len(ivs), ivs)
    _set_samplers(fs, ntex, ivs)
    prog.shaderObject.setValues(0, 2, [vs, fs])
    return prog, vs, fs


def _set_samplers(fs, ntex, extra=()):
    coin = _coin()
    ps = list(extra)
    for i in range(ntex):
        p = coin.SoShaderParameter1i()
        p.name = "ytex%d" % i
        p.value = i
        ps.append(p)
    fs.parameter.deleteValues(0)
    if ps:
        fs.parameter.setValues(0, len(ps), ps)


def _plain_program():
    coin = _coin()
    prog = coin.SoShaderProgram()
    prog.setName(_PLAIN_NAME)
    vs = coin.SoVertexShader()
    vs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    vs.sourceProgram = _PLAIN_VS
    fs = coin.SoFragmentShader()
    fs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    fs.sourceProgram = _PLAIN_FS
    prog.shaderObject.setValues(0, 2, [vs, fs])
    return prog


def _protect_lines(root):
    """root altındaki kenar / nokta çizimlerine düz program ekler (yüz gölgelendiricisi çizgileri boyamasın)."""
    coin = _coin()
    sa = coin.SoSearchAction()
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.setSearchingAll(True)
    found = []
    for tname in ("SoBrepEdgeSet", "SoBrepPointSet", "SoLineSet", "SoIndexedLineSet", "SoPointSet", "SoMarkerSet"):
        try:
            t = coin.SoType.fromName(tname)
        except Exception:
            continue
        if t.isBad():
            continue
        sa.reset()
        sa.setType(t)
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.setSearchingAll(True)
        sa.apply(root)
        pl = sa.getPaths()
        for k in range(pl.getLength()):
            p = pl[k]
            if p.getLength() >= 2:
                found.append(p.getNodeFromTail(1))
    for par in found:
        try:
            if not par.isOfType(coin.SoGroup.getClassTypeId()):
                continue
            if par.getNumChildren() and par.getChild(0).getName().getString() == _PLAIN_NAME:
                continue
            prog = _plain_program()
            par.ref()
            prog.ref()
            par.insertChild(prog, 0)
            _st["plain"].append((par, prog))
        except Exception:
            pass


def _remove_plain():
    for par, prog in _st["plain"]:
        try:
            i = par.findChild(prog)
            if i >= 0:
                par.removeChild(i)
        except Exception:
            pass
        _unref(prog)
        _unref(par)
    _st["plain"] = []


def _maybe_transparent(m, env):
    """Malzeme görünümde saydam (ya da cam yansımalı) çizilebilir mi: Coin karışımı açılmalı."""
    if not env["flags"].get("glass", True):
        lays = M.layers_of(m, "transp")
        return any(L.get("on", True) for L in lays)
    return float(m.get("transp", 0.0)) > 0.0 or any(L.get("on", True) for L in M.layers_of(m, "transp"))


def _unit_node(key, spec):
    """Doku birimi tanımı -> paylaşılan (önbellekli) SoTexture2."""
    import numpy as np
    coin = _coin()
    if spec[0] == "img":
        return _tex_node(spec[1])[0]
    hit = _st["tex"].get(key)
    if hit is not None:
        return hit[0]
    node = coin.SoTexture2()
    if spec[0] == "rgbm":
        img = np.maximum(np.asarray(spec[2], dtype=np.float64), 0.0)
        mx = np.clip(img.max(2, keepdims=True), 1e-6, 8.0)
        rgb = np.clip(img / mx, 0.0, 1.0)
        a = np.concatenate([rgb, mx / 8.0], 2)
        a8 = np.ascontiguousarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8))
        wrap = coin.SoTexture2.CLAMP if spec[3] == "clamp" else coin.SoTexture2.REPEAT
    else:                                                 # "tab": stokastik döşeme ötelemeleri (texelFetch ile okunur)
        a8 = np.ascontiguousarray(spec[2])
        wrap = coin.SoTexture2.REPEAT
    h, w = a8.shape[:2]
    node.image.setValue(coin.SbVec2s(w, h), a8.shape[2], a8.tobytes())
    node.model = coin.SoTexture2.REPLACE
    node.wrapS = wrap
    node.wrapT = wrap
    node.ref()
    _st["tex"][key] = (node, spec)
    return node


class _Item(object):
    """Bir nesnenin RootNode'una eklenen düğümler."""

    def __init__(self, obj, vo, lc, lh, dcols=None, inst=None):
        coin = _coin()
        self.name = obj.Name
        self.root = vo.RootNode                           # ViewObject değil kök düğüm tutulur (ref'li): nesne / belge
        self.root.ref()                                   # silinse de düğüm yaşar, kaldırma güvenli olur
        self.lc, self.lh = lc, lh
        self.dcols = dcols
        self.inst = inst
        self.grp = coin.SoGroup()
        self.grp.ref()
        self.grp.setName(_NAME)
        self.mat_node = None
        self.prog = None
        self.sig = None

    def apply(self, mat, diag):
        if self.root is None:
            return
        coin = _coin()
        from . import viewmat_glsl as G
        env = _st.get("env")
        if env is None:
            return
        vs_src, fs_src, units, dropped = G.build(mat if mat is not None else M.DEFAULT, self.lc, self.lh, diag, env,
                                                 self.inst, self.dcols)
        sig = (vs_src, fs_src, tuple(k for k, _s in units))
        if sig == self.sig:
            return
        self.sig = sig
        g = self.grp
        g.removeAllChildren()
        cx = coin.SoComplexity()                             # trilineer mip süzme (yansımada bulanıklık düzeyi de buna dayanır)
        cx.textureQuality = 1.0
        cx.value.setIgnored(True)
        cx.type.setIgnored(True)
        g.addChild(cx)
        for i, (key, spec) in enumerate(units):
            u = coin.SoTextureUnit()
            u.unit = i
            g.addChild(u)
            g.addChild(_unit_node(key, spec))
        if units:
            u = coin.SoTextureUnit()
            u.unit = 0
            g.addChild(u)
        m = M.normalize(mat) if mat is not None else dict(M.DEFAULT)
        if _maybe_transparent(m, env):                       # saydam olabilir: Coin karışımı açsın (renk gölgelendiricide)
            sm = coin.SoMaterial()
            sm.transparency = 0.01
            for f_ in (sm.ambientColor, sm.diffuseColor, sm.specularColor, sm.emissiveColor, sm.shininess):
                f_.setIgnored(True)                      # yalnız saydamlık geçersiz kılınsın (seçim rengi gelmeye devam etsin)
            sm.setOverride(True)
            g.addChild(sm)
        self.prog, _vs, _fs = _program(vs_src, fs_src, len(units))
        g.addChild(self.prog)
        root = self.root
        if root.findChild(g) < 0:
            root.insertChild(g, 0)
        if dropped:
            import FreeCAD
            FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: %s: doku birimi sınırı (%d) aşıldı, %d katman görünümde yok\n"
                                         % (self.name, G.MAX_UNITS, dropped))

    def remove(self):
        try:
            root = self.root
            i = root.findChild(self.grp)
            if i >= 0:
                root.removeChild(i)
        except Exception:
            pass
        self.grp.removeAllChildren()
        _unref(self.root)
        self.root = None


# ------------------------------------------------------------------ sahne
def _local_bounds(shp):
    """Şeklin kendi (yerleşimsiz) koordinatlarında sınır kutusu: (merkez, yarı boyut)."""
    import FreeCAD
    s = shp.copy()
    s.Placement = FreeCAD.Placement()
    b = s.BoundBox
    return ((b.XMin + b.XMax) / 2.0, (b.YMin + b.YMax) / 2.0, (b.ZMin + b.ZMax) / 2.0), \
        (max(b.XLength, 1e-6) / 2.0, max(b.YLength, 1e-6) / 2.0, max(b.ZLength, 1e-6) / 2.0)


def _disp_colors(o, tgt, shp):
    """Nesnenin ekran renkleri (yüz başına olanlar dahil, tekrarsız), vurgu algılamada karşılaştırmak için."""
    try:
        from .freecad_scene import _display_colors
        base, dc, _how = _display_colors(o, tgt, len(shp.Faces))
        cols = [tuple(base)] + [tuple(c) for c in (dc or ())]
        out = []
        for c in cols:
            c = tuple(round(float(x), 3) for x in c[:3])
            if c not in out:
                out.append(c)
        return out
    except Exception:
        return None


def _targets(doc):
    """Önizlenecek nesneler: render'ın gerçekten çizeceği görünür yapraklar (Link / Assembly / Body dahil).
    Döner [(nesne, hedef, yerel merkez, yerel yarı boyut)] ve sahne köşegeni."""
    import FreeCAD
    from .freecad_scene import _gather
    items = _gather(doc, lambda *_a: None)
    out, seen = [], set()
    inst = {}
    for o, _tgt, shp, _sub in items:                        # nesnenin tüm kopyalarının dünya sınır kutuları (stokastik tablo)
        try:
            b = shp.BoundBox
            inst.setdefault(o.Name, []).append(((b.XMin, b.YMin, b.ZMin), (b.XMax, b.YMax, b.ZMax)))
        except Exception:
            pass
    bb = FreeCAD.BoundBox()
    for o, tgt, shp, _sub in items:
        try:
            bb.add(shp.BoundBox)
        except Exception:
            pass
        if o.Name in seen or getattr(shp, "is_mesh", False):
            continue
        if not M.included(o):
            continue
        seen.add(o.Name)
        try:
            lc, lh = _local_bounds(shp)
        except Exception:
            continue
        out.append((o, tgt, lc, lh, _disp_colors(o, tgt, shp), inst.get(o.Name)))
    diag = bb.DiagonalLength if bb.isValid() else 1000.0
    cen = (bb.Center.x, bb.Center.y, bb.Center.z) if bb.isValid() else (0.0, 0.0, 0.0)
    return out, max(diag, 1e-6), cen


def _material_for(o, tgt):
    ov = _st["override"]
    for x in (o, tgt):
        if x is not None and x.Name in ov:
            return ov[x.Name]
    name = M.assigned(o) or M.assigned(tgt)
    return M.get(name) if name else None


def is_on():
    return bool(_st["on"])


def refresh(doc=None):
    """Nesne listesini ve malzemeleri yeniden okuyup düğümleri günceller (açık değilse bir şey yapmaz)."""
    if not _st["on"]:
        return
    import FreeCAD
    import FreeCADGui as Gui
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        return
    try:
        gdoc = Gui.getDocument(doc.Name)
    except Exception:
        return
    if _st["doc"] is not None and _st["doc"] != doc.Name:
        _clear_nodes()
    _st["doc"] = doc.Name
    _install_callbacks(gdoc)
    try:
        targets, diag, cen = _targets(doc)
    except Exception as ex:
        FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: sahne taranamadı (%s)\n" % ex)
        return
    _st["diag"] = diag
    try:
        from . import viewmat_glsl as G
        _st["env"] = G.env_setup(doc, cen)
    except Exception as ex:
        import traceback
        FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: ortam / ışıklar okunamadı (%s)\n%s" % (ex, traceback.format_exc()))
        _st["env"] = None
    keep = set()
    for o, tgt, lc, lh, dcols, inst in targets:
        mat = _material_for(o, tgt)
        if mat is None:
            continue                                  # malzemesi olmayan nesne FreeCAD'in kendi görünümüyle kalır
        vo = getattr(o, "ViewObject", None)
        if vo is None:
            continue
        it = _st["items"].get(o.Name)
        if it is None or it.lc != lc or it.lh != lh or it.dcols != dcols or it.inst != inst:
            if it is not None:
                it.remove()
            it = _Item(o, vo, lc, lh, dcols, inst)
            _st["items"][o.Name] = it
        try:
            it.apply(mat, diag)
            keep.add(o.Name)
            _protect_lines(vo.RootNode)
        except Exception as ex:
            FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: %s: %s\n" % (o.Label, ex))
    for nm in list(_st["items"]):
        if nm not in keep:
            _st["items"].pop(nm).remove()
    _redraw(doc)


def update_material(names, mat):
    """Canlı düzenleme: adlı nesnelerde (kaydedilmemiş) mat malzemesini göster. names boş / mat None: geçersiz kıl."""
    ov = _st["override"]
    ov.clear()
    if mat is not None:
        for n in names or ():
            ov[n] = mat
    if not _st["on"]:
        return
    if mat is None:
        refresh()
        return
    hit = False
    for n in names or ():
        it = _st["items"].get(n)
        if it is not None:
            try:
                it.apply(mat, _st["diag"])
                hit = True
            except Exception:
                pass
    if not hit and names:
        refresh()                                     # henüz malzemesi olmayan nesneye ilk atama: listeye girsin
    else:
        _redraw()


def clear_override():
    if _st["override"]:
        _st["override"].clear()
        refresh()


def _redraw(doc=None):
    try:
        import FreeCADGui as Gui
        gdoc = Gui.getDocument(doc.Name) if doc is not None else Gui.ActiveDocument
        for v in gdoc.mdiViewsOfType("Gui::View3DInventor"):
            v.redraw()
    except Exception:
        pass


def _clear_nodes():
    for it in list(_st["items"].values()):
        it.remove()
    _st["items"].clear()
    _remove_plain()
    _remove_callbacks()


# ------------------------------------------------------------------ belge gözlemcisi
_WATCH = ("YBTMaterial", "YBTInclude", "Shape", "Visibility", "LinkedObject", "Group", "Mesh", "ElementCount",
          "ShapeAppearance", "Placement")


def _is_light(obj):
    try:
        from . import lightobj
        return bool(lightobj._is_light(obj))
    except Exception:
        return False


class _Obs(object):
    def slotChangedObject(self, obj, prop):
        if prop in _WATCH or _is_light(obj):
            _schedule()

    def slotCreatedObject(self, obj):
        _schedule()

    def slotDeletedObject(self, obj):
        _schedule()

    def slotActivateDocument(self, doc):
        _schedule()

    def slotDeletedDocument(self, doc):
        if getattr(doc, "Name", None) == _st["doc"]:
            _clear_nodes()                                # düğümler ref'li: kaldırma, belge silinirken de güvenli
            _st["doc"] = None


def _schedule(ms=250):
    from PySide import QtCore
    t = _st["timer"]
    if t is None:
        t = QtCore.QTimer()
        t.setSingleShot(True)
        t.timeout.connect(lambda: refresh())
        _st["timer"] = t
    t.start(ms)


def start():
    import FreeCAD
    if _st["on"]:
        refresh()
        return
    _st["on"] = True
    if _st["obs"] is None:
        _st["obs"] = _Obs()
        FreeCAD.addDocumentObserver(_st["obs"])
    refresh()


def stop():
    import FreeCAD
    _st["on"] = False
    if _st["obs"] is not None:
        try:
            FreeCAD.removeDocumentObserver(_st["obs"])
        except Exception:
            pass
        _st["obs"] = None
    if _st["timer"] is not None:
        _st["timer"].stop()
    _clear_nodes()
    _st["override"].clear()
    _st["doc"] = None
    _redraw()


def toggle():
    if _st["on"]:
        stop()
    else:
        start()
    return _st["on"]
