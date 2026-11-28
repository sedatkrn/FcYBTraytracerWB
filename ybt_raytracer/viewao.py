# -*- coding: utf-8 -*-
"""Görünümde ortam tıkanıklığı (AO): ekran uzayında, derinlik ön geçişiyle.

Render'ın AO'su yüzeyin yarım küresine ışın atıp ilk engele uzaklıkla söner (``1 - t/R``, R = AO_RADIUS x köşegen) ve
yalnız ORTAM ışığını kısar (``amb * (1 - AO_STRENGTH * ao)``). Burada aynı kural ekran uzayında kurulur:

  1. Derinlik ön geçişi: sahnenin render üçgenleri kameradan, yarım çözünürlüklü bir derinlik dokusuna çizilir. Saklanan
     değer, nokta ışık gölge haritalarındaki gibi DOĞRUSAL uzaklıktır (``|P - göz| / far``), böylece izdüşüm
     doğrusalsızlığıyla uğraşmadan dünya konumu geri kurulabilir.
  2. AO geçişi: tam ekran dörtgen. Her piksel için dünya konumu derinlikten geri kurulur, normal konumun türevinden
     alınır; yarım kürede 12 yön x 2 adım örneklenir. Bir örnek noktası derinlik tamponuna göre yüzeyin arkasında
     kalıyorsa engel sayılır ve render'ın düşümü (``1 - t/R``, t = engel ile yüzey arası uzaklık) uygulanır.
  3. Bulanıklaştırma: derinliğe duyarlı 5x5 süzgeç (kenarlarda sızdırmaz). SSAO'nun beneklenmesi bu adımda silinir.

Sonuç dokusu UNIT birimine bağlanır; malzeme gölgelendiricisi tek okumayla ortam ışığını kısar.

Neden Coin'in sahnesine değil de kendi üçgenlerimize çiziyoruz: gölge atlasıyla aynı gerekçe. Coin'in ekran dışı
düğümleri Wayland'de ayrı bir GL bağlamı açmaya çalışıyor; burada her şey FreeCAD'in KENDİ bağlamında, çizim sırasında
bir SoCallback içinden Qt OpenGL sınıflarıyla yapılır. Üçgenler zaten gölge için toplanıyor (freecad_scene.collect_scene),
yani gölgeyi atan ve AO'yu veren geometri render'dakiyle aynı.

Ekran uzayının sınırı: ekran dışında kalan ya da başka bir nesnenin arkasındaki engeller görünmez. Kamera ters çevrilince
AO o bölgede kaybolur. Pişirilmiş AO'nun aksine kare maliyeti vardır ama sahne değişince beklemek gerekmez.
"""
import math

import numpy as np

UNIT = 10                   # AO dokusunun doku birimi (malzeme 0..7, yansıma 11, aux 12, env 13, gölge 14 / 15)
SCALE = 0.5                 # ön geçiş / AO çözünürlüğünün görünüme oranı (varsayılan; viewmat.fx_scale geçersiz kılar)
NDIR = 12                   # yarım küre yön sayısı (her yönde 2 adım)
BLUR = 2                    # bulanıklaştırma yarıçapı (piksel; 5x5)

_GL_FRAMEBUFFER = 0x8D40
_GL_FRAMEBUFFER_BINDING = 0x8CA6
_GL_COLOR_ATTACHMENT0 = 0x8CE0
_GL_DEPTH_ATTACHMENT = 0x8D00
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE0 = 0x84C0
_GL_DEPTH_TEST = 0x0B71
_GL_CULL_FACE = 0x0B44
_GL_BLEND = 0x0BE2
_GL_SCISSOR_TEST = 0x0C11
_GL_LESS = 0x0201
_GL_FLOAT = 0x1406
_GL_TRIANGLES = 0x0004
_GL_TRIANGLE_STRIP = 0x0005
_GL_DEPTH_BUFFER_BIT = 0x0100
_GL_COLOR_BUFFER_BIT = 0x4000


def _f(x):
    x = float(x)
    if not math.isfinite(x):
        x = 0.0
    s = repr(x)
    return s if ("." in s or "e" in s) else s + ".0"


def hemisphere(n):
    """Yarım kürede (+Z) kosinüs ağırlıklı, altın açı sarmalıyla düzgün dağılmış n yön."""
    out = []
    ga = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n):
        # kosinüs ağırlıklı: z = sqrt(1 - u), u düzgün
        u = (i + 0.5) / float(n)
        z = math.sqrt(max(1.0 - u, 1e-6))
        r = math.sqrt(max(1.0 - z * z, 0.0))
        a = ga * i
        out.append((r * math.cos(a), r * math.sin(a), z))
    return out


# ------------------------------------------------------------------ GL programları
_DEPTH_VS = """#version 130
in vec3 pos; uniform mat4 VP; out vec3 wp;
void main(){ wp = pos; gl_Position = VP * vec4(pos, 1.0); }"""

_DEPTH_FS = """#version 130
in vec3 wp; uniform vec4 cam;          // xyz: göz, w: far
void main(){ gl_FragDepth = clamp(length(wp - cam.xyz) / cam.w, 0.0, 1.0); }"""

_QUAD_VS = """#version 130
in vec2 p;
void main(){ gl_Position = vec4(p, 0.0, 1.0); }"""

_AO_FS = """#version 130
uniform sampler2D dep;                 // doğrusal uzaklık / far
uniform mat4 VP;                       // dünya -> kırpma
uniform mat4 iVP;                      // kırpma -> dünya
uniform vec4 cam;                      // xyz: göz, w: far
uniform vec4 par;                      // x: yarıçap (dünya), y: eğim payı, zw: 1/genişlik, 1/yükseklik
const vec3 HS[%(N)d] = vec3[%(N)d](%(DIRS)s);

vec3 raydir(vec2 uv){
  vec4 h = iVP * vec4(uv * 2.0 - 1.0, 0.0, 1.0);
  return normalize(h.xyz / h.w - cam.xyz);
}

void main(){
  vec2 uv = gl_FragCoord.xy * par.zw;
  float d0 = texture2D(dep, uv).r;
  if (d0 >= 0.9995) { gl_FragColor = vec4(0.0); return; }      // arka plan: tıkanıklık yok
  vec3 P = cam.xyz + raydir(uv) * (d0 * cam.w);
  vec3 N = cross(dFdx(P), dFdy(P));
  float nl = length(N);
  if (nl < 1e-12) { gl_FragColor = vec4(0.0); return; }
  N /= nl;
  if (dot(N, cam.xyz - P) < 0.0) N = -N;
  vec3 up = abs(N.z) < 0.9 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0);
  vec3 T = normalize(cross(up, N)); vec3 B = cross(N, T);
  float a = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) * 6.2831853;
  mat2 rot = mat2(cos(a), sin(a), -sin(a), cos(a));
  float occ = 0.0;
  for (int i = 0; i < %(N)d; i++) {
    vec3 h = HS[i];
    vec2 xy = rot * h.xy;
    vec3 dir = T * xy.x + B * xy.y + N * h.z;
    float best = 0.0;
    for (int k = 0; k < 2; k++) {
      vec3 S = P + dir * (par.x * (k == 0 ? 0.4 : 0.9));
      vec4 c = VP * vec4(S, 1.0);
      if (c.w <= 0.0) continue;
      vec2 su = c.xy / c.w * 0.5 + 0.5;
      if (su.x < 0.0 || su.x > 1.0 || su.y < 0.0 || su.y > 1.0) continue;
      float ds = texture2D(dep, su).r * cam.w;
      float dS = length(S - cam.xyz);
      if (ds < dS - par.y * dS) {                              // örnek, görünen yüzeyin arkasında: engel
        vec3 O = cam.xyz + normalize(S - cam.xyz) * ds;
        best = max(best, 1.0 - min(length(O - P) / par.x, 1.0));   // render'ın düşümü
      }
    }
    occ += best;
  }
  gl_FragColor = vec4(occ / %(NF)s);
}""" % dict(N=NDIR, NF=_f(NDIR),
            DIRS=", ".join("vec3(%s, %s, %s)" % tuple(_f(v) for v in d) for d in hemisphere(NDIR)))

_BLUR_FS = """#version 130
uniform sampler2D ao; uniform sampler2D dep;
uniform vec4 par;                      // x: derinlik toleransı (0..1 ölçeğinde), zw: 1/genişlik, 1/yükseklik
void main(){
  vec2 uv = gl_FragCoord.xy * par.zw;
  float d0 = texture2D(dep, uv).r;
  float s = 0.0, w = 0.0;
  for (int y = -%(R)d; y <= %(R)d; y++) {
    for (int x = -%(R)d; x <= %(R)d; x++) {
      vec2 u2 = uv + vec2(float(x), float(y)) * par.zw;
      float ww = abs(texture2D(dep, u2).r - d0) < par.x ? 1.0 : 0.0;
      s += texture2D(ao, u2).r * ww;
      w += ww;
    }
  }
  gl_FragColor = vec4(w > 0.0 ? s / w : 0.0);
}""" % dict(R=BLUR)


# ------------------------------------------------------------------ malzeme gölgelendiricisi tarafı
def glsl():
    """Malzeme gölgelendiricisine eklenecek okuma işlevi: aovis() -> 0..1 tıkanıklık."""
    return ("uniform sampler2D yaot; uniform vec4 yaov;\n"
            "float aovis(){ return texture2D(yaot, (gl_FragCoord.xy - yaov.zw) * yaov.xy).r; }")


class Manager(object):
    """AO dokusunun GL kaynakları ve geçişleri (bağlam başına). SoCallback içinden render() çağrılır."""

    def __init__(self):
        self.tri = None
        self.radius = 0.0
        self.ctx = {}
        self.err = False
        self.nerr = 0
        self._par = None
        self._ok = False

    # -- paylaşılan uniform (gl_FragCoord -> doku koordinatı)
    def params(self):
        from pivy import coin
        if self._par is None:
            p = coin.SoShaderParameter4f()
            p.name = "yaov"
            p.value = coin.SbVec4f(0.0, 0.0, 0.0, 0.0)
            p.ref()
            s = coin.SoShaderParameter1i()
            s.name = "yaot"
            s.value = UNIT
            s.ref()
            self._par = [p, s]
        return list(self._par)

    def _set_view(self, w, h, x0, y0):
        from pivy import coin
        p = self._par[0]
        v = (1.0 / max(w, 1), 1.0 / max(h, 1), float(x0), float(y0))
        old = p.value.getValue().getValue()
        if max(abs(old[k] - v[k]) for k in range(4)) > 1e-12:
            p.value.setValue(coin.SbVec4f(*v))

    def set_scene(self, tri, radius):
        """tri: (n, 3, 3) dünya üçgenleri (opak + cam), radius: AO erişimi (dünya birimi)."""
        a = np.ascontiguousarray(np.asarray(tri).reshape(-1, 9), dtype=np.float32)
        self.tri = a if len(a) else None
        self.radius = float(radius)

    def active(self):
        return self.tri is not None and not self.err

    # -- kaynaklar
    def _res(self, ctx):
        from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLBuffer
        key = id(ctx)
        r = self.ctx.get(key)
        if r is None:
            def mk(vs_src, fs_src, attr):
                pr = QOpenGLShaderProgram()
                pr.addShaderFromSourceCode(QOpenGLShader.Vertex, vs_src)
                pr.addShaderFromSourceCode(QOpenGLShader.Fragment, fs_src)
                pr.bindAttributeLocation(attr, 0)
                if not pr.link():
                    raise RuntimeError("AO programı (%s): %s" % (attr, pr.log()))
                return pr
            quad = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            quad.create()
            quad.bind()
            d = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype=np.float32).tobytes()
            quad.allocate(d, len(d))
            quad.release()
            r = dict(dep=mk(_DEPTH_VS, _DEPTH_FS, "pos"), ao=mk(_QUAD_VS, _AO_FS, "p"),
                     blur=mk(_QUAD_VS, _BLUR_FS, "p"), quad=quad, vbo=None, geo=-1,
                     texD=None, texA=None, texB=None, fbo=None, size=None, ctx=ctx)
            self.ctx[key] = r
        return r

    def _alloc(self, r, w, h):
        from PySide6.QtOpenGL import QOpenGLTexture, QOpenGLFramebufferObject, QOpenGLBuffer
        if r["size"] != (w, h):
            for k in ("texD", "texA", "texB"):
                if r[k] is not None:
                    r[k].destroy()
                    r[k] = None

            def mk(depth):
                t = QOpenGLTexture(QOpenGLTexture.Target2D)
                t.setFormat(QOpenGLTexture.D24 if depth else QOpenGLTexture.R8_UNorm)
                t.setSize(w, h)
                t.setMinificationFilter(QOpenGLTexture.Nearest if depth else QOpenGLTexture.Linear)
                t.setMagnificationFilter(QOpenGLTexture.Nearest if depth else QOpenGLTexture.Linear)
                t.setWrapMode(QOpenGLTexture.ClampToEdge)
                if depth:
                    t.setComparisonMode(QOpenGLTexture.CompareNone)
                    t.allocateStorage(QOpenGLTexture.Depth, QOpenGLTexture.UInt32)
                else:
                    t.allocateStorage(QOpenGLTexture.Red, QOpenGLTexture.UInt8)
                return t
            r["texD"], r["texA"], r["texB"] = mk(True), mk(False), mk(False)
            r["size"] = (w, h)
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

    def render(self, W, eye, far, vp):
        """W: dünya -> kırpma (4x4, satır vektörü düzeni: clip = dünya * W). vp: (x0, y0, genişlik, yükseklik) piksel.
        Döner: viewport geri konmalı mı."""
        if not self.active():
            return False
        from PySide6.QtGui import QOpenGLContext, QMatrix4x4
        ctx = QOpenGLContext.currentContext()
        if ctx is None:
            return False
        x0, y0, vw, vh = vp
        if vw < 8 or vh < 8:
            return False
        try:
            from . import viewmat
            sc = viewmat.fx_scale()
        except Exception:
            sc = SCALE
        w, h = max(16, int(vw * sc)), max(16, int(vh * sc))
        try:
            f = ctx.functions()
            r = self._res(ctx)
            self._alloc(r, w, h)
            Wn = np.asarray(W, dtype=np.float64)
            gl_VP = QMatrix4x4(*[float(v) for v in Wn.T.reshape(-1)])        # GL sütun vektörü: clip = M * v
            gl_iVP = QMatrix4x4(*[float(v) for v in np.linalg.inv(Wn).T.reshape(-1)])
            prev = int(f.glGetIntegerv(_GL_FRAMEBUFFER_BINDING))
            act = int(f.glGetIntegerv(0x84E0))
            blend_on, cull_on, sc_on = f.glIsEnabled(_GL_BLEND), f.glIsEnabled(_GL_CULL_FACE), f.glIsEnabled(_GL_SCISSOR_TEST)
            depth_on = f.glIsEnabled(_GL_DEPTH_TEST)
            prev_df = int(f.glGetIntegerv(0x0B74))
            f.glActiveTexture(_GL_TEXTURE0 + UNIT)
            f.glBindTexture(_GL_TEXTURE_2D, 0)                               # yazarken aynı dokuyu okuma
            f.glActiveTexture(act)
            f.glDisable(_GL_BLEND)
            f.glDisable(_GL_CULL_FACE)
            f.glDisable(_GL_SCISSOR_TEST)
            r["fbo"].bind()
            f.glFramebufferTexture2D(_GL_FRAMEBUFFER, _GL_COLOR_ATTACHMENT0, _GL_TEXTURE_2D, 0, 0)
            f.glViewport(0, 0, w, h)

            # 1) derinlik ön geçişi
            f.glFramebufferTexture2D(_GL_FRAMEBUFFER, _GL_DEPTH_ATTACHMENT, _GL_TEXTURE_2D, r["texD"].textureId(), 0)
            f.glEnable(_GL_DEPTH_TEST)
            f.glDepthFunc(_GL_LESS)
            f.glDepthMask(True)
            f.glClearDepthf(1.0)
            f.glDepthRangef(0.0, 1.0)
            f.glColorMask(False, False, False, False)
            f.glClear(_GL_DEPTH_BUFFER_BIT)
            pr = r["dep"]
            pr.bind()
            r["vbo"].bind()
            pr.enableAttributeArray(0)
            pr.setAttributeBuffer(0, _GL_FLOAT, 0, 3)
            pr.setUniformValue("VP", gl_VP)
            pr.setUniformValue("cam", float(eye[0]), float(eye[1]), float(eye[2]), float(far))
            f.glDrawArrays(_GL_TRIANGLES, 0, len(self.tri) * 3)
            pr.disableAttributeArray(0)
            r["vbo"].release()
            pr.release()
            f.glColorMask(True, True, True, True)

            # derinlik dokusu artık OKUNACAK: ekten çıkar (geri besleme olmasın)
            f.glFramebufferTexture2D(_GL_FRAMEBUFFER, _GL_DEPTH_ATTACHMENT, _GL_TEXTURE_2D, 0, 0)
            f.glDisable(_GL_DEPTH_TEST)
            f.glDepthMask(False)
            f.glActiveTexture(_GL_TEXTURE0)
            f.glBindTexture(_GL_TEXTURE_2D, r["texD"].textureId())
            f.glActiveTexture(_GL_TEXTURE0 + 1)

            # 2) AO geçişi -> texA
            f.glFramebufferTexture2D(_GL_FRAMEBUFFER, _GL_COLOR_ATTACHMENT0, _GL_TEXTURE_2D, r["texA"].textureId(), 0)
            f.glClearColor(0.0, 0.0, 0.0, 0.0)
            f.glClear(_GL_COLOR_BUFFER_BIT)
            pr = r["ao"]
            pr.bind()
            r["quad"].bind()
            pr.enableAttributeArray(0)
            pr.setAttributeBuffer(0, _GL_FLOAT, 0, 2)
            pr.setUniformValue("dep", 0)
            pr.setUniformValue("VP", gl_VP)
            pr.setUniformValue("iVP", gl_iVP)
            pr.setUniformValue("cam", float(eye[0]), float(eye[1]), float(eye[2]), float(far))
            pr.setUniformValue("par", float(self.radius), 0.004, 1.0 / w, 1.0 / h)
            f.glDrawArrays(_GL_TRIANGLE_STRIP, 0, 4)

            # 3) derinliğe duyarlı bulanıklaştırma -> texB
            f.glFramebufferTexture2D(_GL_FRAMEBUFFER, _GL_COLOR_ATTACHMENT0, _GL_TEXTURE_2D, r["texB"].textureId(), 0)
            f.glClear(_GL_COLOR_BUFFER_BIT)
            pr.disableAttributeArray(0)
            pr.release()
            f.glBindTexture(_GL_TEXTURE_2D, r["texA"].textureId())           # birim 1
            pr = r["blur"]
            pr.bind()
            pr.enableAttributeArray(0)
            pr.setAttributeBuffer(0, _GL_FLOAT, 0, 2)
            pr.setUniformValue("ao", 1)
            pr.setUniformValue("dep", 0)
            pr.setUniformValue("par", float(max(self.radius * 0.5 / max(far, 1e-6), 1e-5)), 0.0, 1.0 / w, 1.0 / h)
            f.glDrawArrays(_GL_TRIANGLE_STRIP, 0, 4)
            pr.disableAttributeArray(0)
            r["quad"].release()
            pr.release()

            # geri dön
            r["fbo"].release()
            f.glBindFramebuffer(_GL_FRAMEBUFFER, prev)
            f.glBindTexture(_GL_TEXTURE_2D, 0)                               # birim 1
            f.glActiveTexture(_GL_TEXTURE0)
            f.glBindTexture(_GL_TEXTURE_2D, 0)
            f.glActiveTexture(_GL_TEXTURE0 + UNIT)
            f.glBindTexture(_GL_TEXTURE_2D, r["texB"].textureId())
            f.glActiveTexture(act)
            f.glDepthFunc(prev_df)
            f.glDepthMask(True)
            if depth_on:
                f.glEnable(_GL_DEPTH_TEST)
            if blend_on:
                f.glEnable(_GL_BLEND)
            if cull_on:
                f.glEnable(_GL_CULL_FACE)
            if sc_on:
                f.glEnable(_GL_SCISSOR_TEST)
            self.params()
            self._set_view(vw, vh, x0, y0)
            self._ok = True
            self.nerr = 0
            return True
        except Exception as ex:
            import traceback
            import FreeCAD
            self.nerr += 1
            if self.nerr >= 10:
                self.err = True
            if self.nerr in (1, 10):
                FreeCAD.Console.PrintWarning("YBT görünüm AO: %s\n%s\n" % (ex, traceback.format_exc()))
            return False

    def bind(self):
        """Çizim sırasında dokunun birimde bağlı kalmasını güvenceye alır (Coin birimi değiştirmiş olabilir)."""
        if not self._ok:
            return
        from PySide6.QtGui import QOpenGLContext
        ctx = QOpenGLContext.currentContext()
        r = self.ctx.get(id(ctx)) if ctx is not None else None
        if r is None or r.get("texB") is None:
            return
        f = ctx.functions()
        act = int(f.glGetIntegerv(0x84E0))
        f.glActiveTexture(_GL_TEXTURE0 + UNIT)
        f.glBindTexture(_GL_TEXTURE_2D, r["texB"].textureId())
        f.glActiveTexture(act)

    def release(self, widget=None):
        if widget is not None:
            try:
                widget.makeCurrent()
            except Exception:
                widget = None
        for r in self.ctx.values():
            for k in ("texD", "texA", "texB", "vbo", "quad"):
                try:
                    if r.get(k) is not None:
                        r[k].destroy()
                except Exception:
                    pass
            try:
                if r.get("fbo") is not None:
                    r["fbo"].release()
            except Exception:
                pass
        self.ctx = {}
        self._ok = False
        if widget is not None:
            try:
                widget.doneCurrent()
            except Exception:
                pass
