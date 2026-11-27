# -*- coding: utf-8 -*-
"""Görünümde gerçek zamanlı gölge (viewmat önizlemesi için): GPU gölge haritaları, render ile aynı gölge kuralları.

Neden Coin'in SoSceneTexture2 / SoShadowGroup'u değil: Coin bu düğümler için ekran dışı ayrı bir GL bağlamı açar; Wayland'de
(GLX bağlamı açılamaz) FreeCAD çöker. Burada haritalar FreeCAD'in görünümünün KENDİ bağlamında, çizim sırasında bir SoCallback
içinden Qt OpenGL sınıflarıyla (QOpenGLTexture, QOpenGLFramebufferObject, QOpenGLShaderProgram) çizilir.

  * Geometri: render'ın üçgenleri (freecad_scene.collect_scene): gölgeyi atan şekil render'dakiyle aynı.
  * Haritalar iki derinlik DİZİSİ dokusunda: A (güneş / yönlü / spot; ışık başına 1 katman) ve P (nokta ışık; ışık başına 6 yüz).
    Sahnede cam varsa her harita için ikinci katman (cam dahil): render gibi opak engel = gölge, yalnız cam = %55 ışık.
  * Derinlik doğrusal saklanır (yönlüde ışık eksenine uzaklık, konumluda ışığa uzaklık) -> PCSS engel uzaklığı doğrudan.
  * Yumuşak gölge (PCSS): yarı gölge yarıçapı yönlüde engel uzaklığı x tan(yumuşaklık) x ölçek (render "fast" yöntemi),
    konumluda ışık yarıçapı x (alıcı - engel) / engel (render ışık küresi örneklemesi). Keskin gölgede küçük PCF (kenar yumuşatma).
  * Haritalar yalnız sahne / ışık değişince yeniden çizilir; kamera dönerken maliyet yok.
Gölge veren ışık sayısı Seçenekler > Genel'den (viewmat.shadow_lights, en çok 8); en güçlü ışıklar seçilir.
"""
import math
import os

import numpy as np

_DBG = os.environ.get("YBT_SHADOW_DEBUG", "")


def _dbg(msg):
    if not _DBG:
        return
    try:
        with open(_DBG, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass

UNIT_A = 15                 # yönlü / spot derinlik dizisi
UNIT_P = 14                 # nokta ışık derinlik dizisi
SIZE_A_BIG = 4096           # A dizisi katman sayısı az iken
SIZE_A = 2048
SIZE_P = 1024
GLASS_VIS = 0.55            # render._vis_of: yalnız cam engeli

_CUBE = (((1, 0, 0), (0, -1, 0)), ((-1, 0, 0), (0, -1, 0)), ((0, 1, 0), (0, 0, 1)),
         ((0, -1, 0), (0, 0, -1)), ((0, 0, 1), (0, -1, 0)), ((0, 0, -1), (0, -1, 0)))


def _f(x):
    x = float(x)
    if not math.isfinite(x):
        x = 0.0
    s = repr(x)
    return s if ("." in s or "e" in s or "n" in s) else s + ".0"


def _v4(r):
    return "vec4(%s, %s, %s, %s)" % tuple(_f(v) for v in r)


def _v3(r):
    return "vec3(%s, %s, %s)" % tuple(_f(v) for v in r)


def _look(eye, fwd, up_hint):
    f = np.asarray(fwd, dtype=np.float64)
    f = f / np.linalg.norm(f)
    u0 = np.asarray(up_hint, dtype=np.float64)
    if abs(np.dot(u0, f)) > 0.99:
        u0 = np.array([1.0, 0.0, 0.0]) if abs(f[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    r = np.cross(f, u0)
    r = r / np.linalg.norm(r)
    u = np.cross(r, f)
    V = np.eye(4)
    V[0, :3], V[1, :3], V[2, :3] = r, u, -f
    V[:3, 3] = -V[:3, :3] @ np.asarray(eye, dtype=np.float64)
    return V


def _ortho(h, near, far):
    P = np.eye(4)
    P[0, 0] = P[1, 1] = 1.0 / h
    P[2, 2] = -2.0 / (far - near)
    P[2, 3] = -(far + near) / (far - near)
    return P


def _persp(fov, near, far):
    t = 1.0 / math.tan(fov / 2.0)
    P = np.zeros((4, 4))
    P[0, 0] = P[1, 1] = t
    P[2, 2] = -(far + near) / (far - near)
    P[2, 3] = -2.0 * far * near / (far - near)
    P[3, 2] = -1.0
    return P


def set_dir(s, Ld):
    """Yönlü ışığın haritasını Ld (yüzeyden ışığa, dünya) yönüne kurar."""
    Ld = np.asarray(Ld, dtype=np.float64)
    Ld = Ld / (np.linalg.norm(Ld) or 1.0)
    eye = s["C"] + Ld * (2.0 * s["Rb"])
    s["VP"] = [_ortho(s["Rb"], s["near"], s["far"]) @ _look(eye, -Ld, (0, 0, 1))]
    s["eye"], s["fwd"], s["Ld"] = eye, -Ld, Ld


def plan(lights, lo, hi, nmax, has_glass, soft_deg, soft_scale):
    """Gölge verecek ışıkları seçip harita düzenini çıkarır.
    lights: viewmat_glsl.env_setup'ın ışık sözlükleri (cast=True olanlar aday). Döner (specs, layout) ya da (None, None)."""
    cand = [(i, d) for i, d in enumerate(lights) if d.get("cast")]
    if not cand or nmax <= 0:
        return None, None
    cand.sort(key=lambda t: -float(t[1]["power"]) * max(t[1]["col"]))
    cand = cand[:nmax]
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    C = (lo + hi) / 2.0
    Rb = max(float(np.linalg.norm(hi - lo)) / 2.0, 1e-3)
    nl = 2 if has_glass else 1
    specs = []
    la = lp = 0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    for i, d in cand:
        s = dict(light=i)
        if "pos" in d:
            L = np.asarray(d["pos"], dtype=np.float64)
            far = float(np.max(np.linalg.norm(corners - L, axis=1))) * 1.02 + 1.0
            near = max(far * 1e-4, 0.5)
            s.update(pos=L, near=near, far=far, radius=float(d.get("radius", 0.0)))
            if d.get("spot"):
                half = math.acos(max(min(d["co"], 1.0), -1.0))
                fov = min(max(2.0 * half * 1.06, math.radians(2.0)), math.radians(165.0))
                ax = np.asarray(d["axis"], dtype=np.float64)
                VP = _persp(fov, near, far) @ _look(L, ax, (0, 0, 1))
                s.update(kind="spot", arr="A", layer=la, VP=[VP], ts=2.0 * math.tan(fov / 2.0))
                la += nl
            else:
                VPs = [_persp(math.radians(90.0), near, far) @ _look(L, fwd, upv) for fwd, upv in _CUBE]
                s.update(kind="point", arr="P", layer=lp, VP=VPs, ts=2.0)
                lp += 6 * nl
        else:                                   # yönlü: sahneye sığan ortografik; kameraya bağlı (panel) ışıkta yön her karede
            near, far = Rb * 0.98, 3.0 * Rb * 1.02
            tan_t = math.tan(math.radians(min(float(d.get("soft", soft_deg)), 30.0))) * max(0.1, float(soft_scale))
            s.update(kind="dir", arr="A", layer=la, near=near, far=far, half=Rb, tan_t=tan_t, C=C, Rb=Rb,
                     edir=(np.asarray(d["edir"], dtype=np.float64) if "edir" in d else None))
            Ld = np.asarray(d["wdir"], dtype=np.float64) if "wdir" in d else np.array([0.0, 0.0, 1.0])
            set_dir(s, Ld)
            la += nl
        specs.append(s)
    na, np_ = la, lp
    size_a = SIZE_A_BIG if na <= 2 else SIZE_A
    layout = dict(na=na, np=np_, size_a=size_a, size_p=SIZE_P, glass=has_glass, nl=nl)
    for s in specs:
        if s["kind"] == "dir":
            s["texel"] = 2.0 * s["half"] / size_a
        else:
            s["texel"] = s["ts"] / (size_a if s["kind"] == "spot" else SIZE_P)     # alıcı uzaklığıyla çarpılır
    return specs, layout


# ======================================================================== GLSL (malzeme gölgelendiricisi)
_GLSL_COMMON = """
uniform sampler2DArray ySA; uniform sampler2DArray ySP;
const vec2 PD[24] = vec2[24](vec2(-0.613, 0.617), vec2(0.170, -0.040), vec2(-0.299, 0.791), vec2(0.645, 0.493),
  vec2(-0.651, 0.718), vec2(0.421, 0.027), vec2(-0.817, -0.271), vec2(-0.705, -0.668), vec2(0.977, -0.108),
  vec2(0.063, -0.732), vec2(0.792, 0.493), vec2(-0.854, 0.111), vec2(-0.384, 0.276), vec2(0.553, -0.818),
  vec2(-0.040, 0.441), vec2(-0.155, -0.957), vec2(0.282, 0.877), vec2(0.384, -0.411), vec2(-0.476, -0.264),
  vec2(0.963, 0.226), vec2(-0.951, -0.031), vec2(0.172, 0.335), vec2(-0.172, -0.412), vec2(0.648, -0.286));
float shrand(){ return fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453); }
// dizi katmanından doğrusal derinlik (0..1); cam katmanı gl > 0
float shtap(sampler2DArray s, vec2 uv, float layer){ return texture(s, vec3(clamp(uv, 0.0005, 0.9995), layer)).r; }
// PCSS: zr alıcı derinliği (0..1), lr = harita birimi başına dünya uzunluğu çarpanı, kind 0 = yönlü (penumbra = d * tanT),
// 1 = konumlu (penumbra = rad * d / zb), tex = doku birimi başına dünya boyu (alıcıda)
float shpcss(sampler2DArray s, vec2 uv, float zr, float layer, float glayer, float scale, float tanT, float rad, int kind,
             float texw, float bias){
  float ang = shrand() * 6.2831853; mat2 rot = mat2(cos(ang), sin(ang), -sin(ang), cos(ang));
  float zw = zr * scale;
  float srch = kind == 0 ? max(zw * tanT, texw) : max(rad, texw);
  srch = min(srch, texw * 64.0);
  float bsum = 0.0, bn = 0.0;
  for (int i = 0; i < 12; i++){
    vec2 o = rot * PD[i * 2] * srch / texw;
    float d = shtap(s, uv + o / SIZEV(s), layer);
    if (d < zr - bias){ bsum += d; bn += 1.0; }
  }
  if (bn == 0.0 && glayer < 0.0) return 1.0;
  float zb = bn > 0.0 ? bsum / bn * scale : zw;
  float pen = kind == 0 ? (zw - zb) * tanT : rad * (zw - zb) / max(zb, 1e-6);
  float rpx = clamp(pen / texw, 0.75, 64.0);
  float vis = 0.0;
  for (int i = 0; i < 24; i++){
    vec2 o = rot * PD[i] * rpx / SIZEV(s);
    float occ = shtap(s, uv + o, layer) < zr - bias ? 1.0 : 0.0;
    float v = 1.0 - occ;
    if (glayer >= 0.0 && occ == 0.0) v = shtap(s, uv + o, glayer) < zr - bias ? %(GV)s : 1.0;
    vis += v;
  }
  return vis / 24.0;
}
"""


def glsl(specs, layout):
    """Malzeme gölgelendiricisine eklenecek gölge fonksiyonları: float shvis<k>(vec3 P, vec3 N) -> 0..1 görünürlük.
    Döner (kod, {ışık indisi: fonksiyon adı})."""
    if not specs:
        return "", {}
    code = [_GLSL_COMMON.replace("SIZEV(s)", "float(textureSize(s, 0).x)") % dict(GV=_f(GLASS_VIS))]
    uni = []
    names = {}
    nl = layout["nl"]
    for k, s in enumerate(specs):
        nm = "shvis%d" % k
        names[s["light"]] = nm
        smp = "ySA" if s["arr"] == "A" else "ySP"
        o = ["float %s(vec3 P, vec3 N){" % nm]
        if s["kind"] == "dir":                  # yön uniform'larla (kameraya bağlı ışık her karede değişebilir)
            texel = s["texel"]
            u = "yShd%d" % k
            uni.append("uniform vec4 %s_0; uniform vec4 %s_1; uniform vec4 %s_2; uniform vec4 %s_e;" % (u, u, u, u))
            s["uname"] = u
            o.append("  vec3 Pb = P + N * %s;" % _f(texel * 1.5))
            o.append("  vec4 q = vec4(Pb, 1.0);")
            o.append("  vec2 uv = vec2(dot(%s_0, q), dot(%s_1, q)) * 0.5 + 0.5;" % (u, u))
            o.append("  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return 1.0;")
            o.append("  float zr = (dot(Pb - %s_e.xyz, %s_2.xyz) - %s) / %s;" % (u, u, _f(s["near"]), _f(s["far"] - s["near"])))
            lay = s["layer"]
            gl = (lay + 1) if nl == 2 else -1
            o.append("  return shpcss(%s, uv, zr, %s, %s, %s, %s, 0.0, 0, %s, %s);" %
                     (smp, _f(lay), _f(gl), _f(s["far"] - s["near"]), _f(s["tan_t"]), _f(texel), _f(texel * 0.75 / (s["far"] - s["near"]))))
        else:
            L = s["pos"]
            o.append("  vec3 dv = P - %s; float dist = length(dv);" % _v3(L))
            o.append("  float texw = dist * %s;" % _f(s["texel"]))
            o.append("  vec3 Pb = P + N * texw * 1.5; dv = Pb - %s; dist = length(dv);" % _v3(L))
            o.append("  float zr = dist / %s;" % _f(s["far"]))
            if s["kind"] == "spot":
                M = s["VP"][0]
                o.append("  vec4 c = vec4(%s);" % ", ".join("dot(%s, vec4(Pb, 1.0))" % _v4(M[r]) for r in range(4)))
                o.append("  if (c.w <= 0.0) return 1.0;")
                o.append("  vec2 uv = c.xy / c.w * 0.5 + 0.5;")
                o.append("  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return 1.0;")
                o.append("  float lay = %s;" % _f(s["layer"]))
                o.append("  float glay = %s;" % (_f(s["layer"] + 1) if nl == 2 else "-1.0"))
            else:
                o.append("  vec3 a = abs(dv); int face;")
                o.append("  if (a.x >= a.y && a.x >= a.z) face = dv.x > 0.0 ? 0 : 1; else if (a.y >= a.z) face = dv.y > 0.0 ? 2 : 3; else face = dv.z > 0.0 ? 4 : 5;")
                o.append("  vec4 c;")
                for fi, M in enumerate(s["VP"]):
                    o.append("  %sif (face == %d) c = vec4(%s);" % ("" if fi == 0 else "else ", fi,
                                                                  ", ".join("dot(%s, vec4(Pb, 1.0))" % _v4(M[r]) for r in range(4))))
                o.append("  vec2 uv = c.xy / c.w * 0.5 + 0.5;")
                o.append("  float lay = %s + float(face) * %s;" % (_f(s["layer"]), _f(nl)))
                o.append("  float glay = %s;" % ("lay + 1.0" if nl == 2 else "-1.0"))
            o.append("  return shpcss(%s, uv, zr, lay, glay, %s, 0.0, %s, 1, texw, texw * 0.75 / %s);" %
                     (smp, _f(s["far"]), _f(max(s["radius"], 0.0)), _f(s["far"])))
        o.append("}")
        code.append("\n".join(o))
    return "\n".join(uni) + "\n" + "\n".join(code), names


# ======================================================================== GL tarafı (FreeCAD görünümünün bağlamında)
_DEPTH_VS = """#version 130
in vec3 pos; uniform mat4 VP; out vec3 wp;
void main(){ wp = pos; gl_Position = VP * vec4(pos, 1.0); }"""
_DEPTH_FS = """#version 130
in vec3 wp; uniform vec4 mode;   // mode.x: 0 yönlü (ışık ekseni), 1 konumlu (uzaklık); yz: göz / eksen için ek
uniform vec3 eye; uniform vec3 fwd; uniform vec2 nf;
void main(){
  float d = mode.x < 0.5 ? (dot(wp - eye, fwd) - nf.x) / (nf.y - nf.x) : length(wp - eye) / nf.y;
  gl_FragDepth = clamp(d, 0.0, 1.0);
}"""


class Manager(object):
    """Gölge haritalarının GL kaynakları ve çizimi (bağlam başına). SoCallback içinden render(action) çağrılır."""

    def __init__(self):
        self.tri = None
        self.n_opaque = 0
        self.specs = None
        self.layout = None
        self.ctx = {}             # id(bağlam) -> kaynaklar
        self.gen = 0              # sahne / ışık sürümü (değişince haritalar yeniden çizilir)
        self._pn = {}             # uniform adı -> parametre düğümleri

    def params(self):
        """Yönlü ışık uniform düğümleri (tüm malzeme programlarında paylaşılır): ışık-uzayı satırları ve göz / eksen."""
        from pivy import coin
        out = []
        for s in self.specs or ():
            if s["kind"] != "dir" or "uname" not in s:
                continue
            if "pnodes" not in s:                 # düğümler ad başına kalıcı: yeniden derlenmeyen programlar da güncel değeri alır
                ps = self._pn.get(s["uname"])
                if ps is None:
                    ps = []
                    for suf in ("_0", "_1", "_2", "_e"):
                        p = coin.SoShaderParameter4f()
                        p.name = s["uname"] + suf
                        p.ref()
                        ps.append(p)
                    self._pn[s["uname"]] = ps
                s["pnodes"] = ps
                self._push(s)
            out += s["pnodes"]
        return out

    @staticmethod
    def _push(s):
        from pivy import coin
        M = np.asarray(s["VP"][0])
        vals = (M[0], M[1], np.append(s["fwd"], 0.0), np.append(s["eye"], 1.0))
        for p, v in zip(s["pnodes"], vals):
            p.value.setValue(coin.SbVec4f(*[float(x) for x in v]))

    def update_camera(self, ivm):
        """Kameraya bağlı (panel) ışıklar: kameranın ters görüş matrisinden dünya yönü; değiştiyse harita yeniden çizilir.
        ivm: SbMatrix (satır vektörü düzeni, Coin)."""
        changed = False
        for s in self.specs or ():
            if s["kind"] != "dir" or s.get("edir") is None:
                continue
            e = s["edir"]
            w = np.array([e[0] * ivm[0][j] + e[1] * ivm[1][j] + e[2] * ivm[2][j] for j in range(3)])
            w = w / (np.linalg.norm(w) or 1.0)
            if np.allclose(w, s.get("Ld", w * 0), atol=1e-6) and "pnodes" in s:
                continue
            set_dir(s, w)
            if "pnodes" in s:
                self._push(s)
            changed = True
        if changed:
            self.gen += 1

    def set_scene(self, tri_opaque, tri_glass, specs, layout):
        a = np.ascontiguousarray(np.concatenate([tri_opaque.reshape(-1, 9), tri_glass.reshape(-1, 9)]) if len(tri_glass)
                                 else tri_opaque.reshape(-1, 9), dtype=np.float32)
        self.tri = a
        self.n_opaque = len(tri_opaque)
        self.specs, self.layout = specs, layout
        self.params()
        self.gen += 1

    # -- kaynaklar
    def _res(self, ctx):
        from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLBuffer
        key = id(ctx)
        r = self.ctx.get(key)
        if r is None:
            prog = QOpenGLShaderProgram()
            prog.addShaderFromSourceCode(QOpenGLShader.Vertex, _DEPTH_VS)
            prog.addShaderFromSourceCode(QOpenGLShader.Fragment, _DEPTH_FS)
            prog.bindAttributeLocation("pos", 0)
            if not prog.link():
                raise RuntimeError("derinlik programı: " + prog.log())
            r = dict(prog=prog, vbo=None, texA=None, texP=None, fbo=None, gen=-1, ctx=ctx, geo=-1, shape=None)
            self.ctx[key] = r
        return r

    def _alloc(self, r):
        from PySide6.QtOpenGL import QOpenGLTexture, QOpenGLFramebufferObject, QOpenGLBuffer
        lay = self.layout
        shape = (lay["na"], lay["size_a"], lay["np"], lay["size_p"])
        if r["shape"] != shape:
            for k in ("texA", "texP"):
                if r[k] is not None:
                    r[k].destroy()
                    r[k] = None

            def mk(n, size):
                if n <= 0:
                    return None
                t = QOpenGLTexture(QOpenGLTexture.Target2DArray)
                t.setFormat(QOpenGLTexture.D32F)
                t.setSize(size, size)
                t.setLayers(n)
                t.setMinificationFilter(QOpenGLTexture.Nearest)
                t.setMagnificationFilter(QOpenGLTexture.Nearest)
                t.setWrapMode(QOpenGLTexture.ClampToEdge)
                t.allocateStorage(QOpenGLTexture.Depth, QOpenGLTexture.Float32)
                return t
            r["texA"] = mk(lay["na"], lay["size_a"])
            r["texP"] = mk(lay["np"], lay["size_p"])
            r["shape"] = shape
        if r["fbo"] is None:
            r["fbo"] = QOpenGLFramebufferObject(16, 16, QOpenGLFramebufferObject.NoAttachment)
        if r["geo"] != id(self.tri):
            if r["vbo"] is not None:
                r["vbo"].destroy()
            vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            vbo.create()
            vbo.bind()
            data = self.tri.tobytes()
            vbo.allocate(data, len(data))
            vbo.release()
            r["vbo"] = vbo
            r["geo"] = id(self.tri)

    def render(self):
        """Gerekirse haritaları çizer ve dizileri UNIT_A / UNIT_P birimlerine bağlar. GL bağlamı etkin olmalı."""
        if not self.specs or self.tri is None:
            return
        from PySide6.QtGui import QOpenGLContext, QMatrix4x4
        ctx = QOpenGLContext.currentContext()
        if ctx is None:
            return
        f = ctx.functions()
        ef = ctx.extraFunctions()
        r = self._res(ctx)
        if r["gen"] != self.gen:
            self._alloc(r)
            GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT = 0x8D40, 0x8D00
            GL_DEPTH_TEST, GL_DEPTH_BUFFER_BIT, GL_TRIANGLES, GL_FLOAT = 0x0B71, 0x0100, 0x0004, 0x1406
            GL_CULL_FACE, GL_BLEND, GL_SCISSOR_TEST, GL_LESS = 0x0B44, 0x0BE2, 0x0C11, 0x0201
            prev_fbo = ctx.defaultFramebufferObject()
            vp = (ctx.surface().size() if ctx.surface() is not None else None)
            prog, vbo, fbo = r["prog"], r["vbo"], r["fbo"]
            fbo.bind()
            blend_on = f.glIsEnabled(GL_BLEND)
            cull_on = f.glIsEnabled(GL_CULL_FACE)
            sc_on = f.glIsEnabled(GL_SCISSOR_TEST)
            f.glDisable(GL_BLEND)
            f.glDisable(GL_CULL_FACE)
            f.glDisable(GL_SCISSOR_TEST)
            f.glEnable(GL_DEPTH_TEST)
            f.glDepthFunc(GL_LESS)
            f.glDepthMask(True)
            f.glColorMask(False, False, False, False)
            prog.bind()
            vbo.bind()
            prog.enableAttributeArray(0)
            prog.setAttributeBuffer(0, GL_FLOAT, 0, 3)
            nl = self.layout["nl"]
            n_all = len(self.tri)
            for s in self.specs:
                tex = r["texA"] if s["arr"] == "A" else r["texP"]
                size = self.layout["size_a"] if s["arr"] == "A" else self.layout["size_p"]
                if s["kind"] == "dir":
                    prog.setUniformValue("mode", 0.0, 0.0, 0.0, 0.0)
                    prog.setUniformValue("eye", *[float(x) for x in s["eye"]])
                    prog.setUniformValue("fwd", *[float(x) for x in s["fwd"]])
                    prog.setUniformValue("nf", float(s["near"]), float(s["far"]))
                else:
                    prog.setUniformValue("mode", 1.0, 0.0, 0.0, 0.0)
                    prog.setUniformValue("eye", *[float(x) for x in s["pos"]])
                    prog.setUniformValue("nf", float(s["near"]), float(s["far"]))
                for fi, M in enumerate(s["VP"]):
                    prog.setUniformValue("VP", QMatrix4x4(*[float(v) for v in np.asarray(M).reshape(-1)]))
                    base = s["layer"] + fi * nl
                    f.glViewport(0, 0, size, size)
                    for g in range(nl):                       # 0: yalnız opak, 1: cam dahil
                        ef.glFramebufferTextureLayer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, tex.textureId(), 0, base + g)
                        if _DBG:
                            _dbg("layer %s/%d status=0x%x err=0x%x" % (s["arr"], base + g, f.glCheckFramebufferStatus(GL_FRAMEBUFFER), f.glGetError()))
                        f.glClear(GL_DEPTH_BUFFER_BIT)
                        cnt = self.n_opaque if g == 0 else n_all
                        if cnt:
                            f.glDrawArrays(GL_TRIANGLES, 0, cnt * 3)
            prog.disableAttributeArray(0)
            vbo.release()
            prog.release()
            f.glColorMask(True, True, True, True)
            fbo.release()
            f.glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
            if blend_on:
                f.glEnable(GL_BLEND)
            if cull_on:
                f.glEnable(GL_CULL_FACE)
            if sc_on:
                f.glEnable(GL_SCISSOR_TEST)
            r["gen"] = self.gen
            r["restore_vp"] = True
            if _DBG:
                _dbg("pass done gen=%d prev_fbo=%d err=0x%x specs=%s" % (self.gen, prev_fbo, f.glGetError(),
                                                                       [(x["kind"], x["layer"]) for x in self.specs]))
        GL_TEXTURE_2D_ARRAY, GL_TEXTURE0 = 0x8C1A, 0x84C0
        if r["texA"] is not None:
            f.glActiveTexture(GL_TEXTURE0 + UNIT_A)
            f.glBindTexture(GL_TEXTURE_2D_ARRAY, r["texA"].textureId())
        if r["texP"] is not None:
            f.glActiveTexture(GL_TEXTURE0 + UNIT_P)
            f.glBindTexture(GL_TEXTURE_2D_ARRAY, r["texP"].textureId())
        f.glActiveTexture(GL_TEXTURE0)
        return r

    def release(self, widget=None):
        """GL kaynaklarını bırak (bağlam etkinleştirilerek)."""
        if widget is not None:
            try:
                widget.makeCurrent()
            except Exception:
                widget = None
        for r in self.ctx.values():
            for k in ("texA", "texP", "vbo"):
                try:
                    if r[k] is not None:
                        r[k].destroy()
                except Exception:
                    pass
            try:
                if r["fbo"] is not None:
                    r["fbo"].release()
            except Exception:
                pass
        self.ctx.clear()
        if widget is not None:
            try:
                widget.doneCurrent()
            except Exception:
                pass
