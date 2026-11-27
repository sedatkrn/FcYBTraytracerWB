# -*- coding: utf-8 -*-
"""Görünümde malzeme önizlemesi için GLSL üreteci (viewmat.py kullanır).

Amaç: Surface editöründeki her ayarın 3B görünümde render (render.py) ile AYNI hesaplanması. Bu modül render.py'deki yüzey
gölgelendirmesinin GLSL karşılığını üretir; değerler kaynak metne sabit olarak gömülür (malzeme değişince yeniden derlenir).

Birebir taşınanlar
  * Doku yerleşimi: _tex_space / _tex_uv / _tex_sample (cubic, planar, silindirik, küresel; nesneye sığdır; dünya; dönme, ölçek,
    konum; tek kez / kenar; aynalı tekrar)
  * Katman yığını: _stack / _blend (9 karışım modu, opaklık, ters çevirme, tek kez kapsama maskesi), tüm kanallar
    (renk, yayılı, parlama, parlaklık, yansıma, saydamlık, kendi ışığı, bump)
  * Prosedürel desenler: procedural.evaluate (10 tür; aynı tamsayı karması ve gradyan gürültüsü, ayak izi sönümlemesi)
  * Stokastik döşeme (hex tiling): render'ın rastgele ötelemeleri Python'da hesaplanıp tablo dokusu olarak verilir (aynı desen)
  * Bump: _bump_normal (dört noktalı tetrahedron türevi, aynı adım ve yükseklik formülü, seviyeler)
  * Işıklar, ortam ışığı (sabit / gökyüzü tablosu / HDRI SH9), HDRI güneşi, parlama, metal, vernik, kendi ışığı,
    yansıma (Fresnel, vernik, cam), cam emilimi, pozlama, ton eşleme
Görünümde olmayanlar (yalnız render): gölgeler, AO, GI, nesneler arası yansıma, kırılma, sanal zemin, anizotropik süzme.
Yansıma ortamı (HDRI ya da gökyüzü) görünür; diğer nesneler yansımaz.
"""
import math

import numpy as np

from . import materials as M

MAX_UNITS = 14                 # gölgelendirici başına doku birimi üst sınırı (katman + ortam + tablo)
HEX_TAB_MAX = 2048             # stokastik döşeme tablosu en çok bu kenar boyunda (aşarsa yaklaşık karma)
BUMP_REL = 0.012               # render.BUMP_REL
BUMP_STEP = 1.75               # render.BUMP_STEP
_PLANE = ((1, 2), (0, 2), (0, 1))
_AX = "xyz"
_BLEND_ID = {"normal": 0, "add": 1, "sub": 2, "mul": 3, "div": 4, "diff": 5, "screen": 6, "max": 7, "min": 8}
_TETRA = ((1.0, 1.0, 1.0), (1.0, -1.0, -1.0), (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0))
_GRAD = ((1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1), (-1, 0, -1),
         (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1), (1, 1, 0), (-1, 1, 0), (0, -1, 1), (0, -1, -1))


def _f(x):
    x = float(x)
    if not math.isfinite(x):
        x = 0.0
    s = repr(x)
    return s if ("." in s or "e" in s or "n" in s) else s + ".0"


def _v3(c):
    return "vec3(%s, %s, %s)" % (_f(c[0]), _f(c[1]), _f(c[2]))


def _lin(c):
    return [max(0.0, float(v)) ** 2.2 for v in c[:3]]


# ======================================================================== ortak GLSL
_VS = """#version 130
uniform vec4 ivc0; uniform vec4 ivc1; uniform vec4 ivc2;
varying vec3 wpos; varying vec3 wnrm; varying vec3 wcen; varying vec3 whe; varying vec4 dcol; varying vec3 wcam;
vec3 toW(vec4 e){ return vec3(dot(ivc0, e), dot(ivc1, e), dot(ivc2, e)); }
void main(){
  vec4 e = gl_ModelViewMatrix * gl_Vertex;
  wpos = toW(e);
  wnrm = toW(vec4(gl_NormalMatrix * gl_Normal, 0.0));
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

_FS_COMMON = """#version 130
uniform vec4 ivc0; uniform vec4 ivc1; uniform vec4 ivc2;
varying vec3 wpos; varying vec3 wnrm; varying vec3 wcen; varying vec3 whe; varying vec4 dcol; varying vec3 wcam;
%(SAMPLERS)s
const float PI = 3.14159265358979;
const float PI2 = 6.28318530718;
vec3 lin(vec3 c){ return pow(max(c, 0.0), vec3(2.2)); }
float fsign(int ax, vec3 nl){ bool p = (ax == 0 ? nl.x : (ax == 1 ? nl.y : nl.z)) > 0.0; return (p == (ax != 1)) ? 1.0 : -1.0; }
float gray(vec3 c){ return pow(clamp(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b, 0.0, 1.0), 1.0 / 2.2); }
vec3 blend3(vec3 r, vec3 v, int m, float op){
  vec3 n = v;
  if (m == 1) n = r + v; else if (m == 2) n = r - v; else if (m == 3) n = r * v;
  else if (m == 4) n = r / max(v, vec3(1e-3)); else if (m == 5) n = abs(r - v);
  else if (m == 6) n = 1.0 - (1.0 - r) * (1.0 - v); else if (m == 7) n = max(r, v); else if (m == 8) n = min(r, v);
  return op < 1.0 ? mix(r, n, op) : n;
}
float blend1(float r, float v, int m, float op){
  float n = v;
  if (m == 1) n = r + v; else if (m == 2) n = r - v; else if (m == 3) n = r * v;
  else if (m == 4) n = r / max(v, 1e-3); else if (m == 5) n = abs(r - v);
  else if (m == 6) n = 1.0 - (1.0 - r) * (1.0 - v); else if (m == 7) n = max(r, v); else if (m == 8) n = min(r, v);
  return op < 1.0 ? mix(r, n, op) : n;
}
"""

_FS_PROC = """
uint ihash(int ix, int iy, int iz, int seed){
  uint h = uint(ix) * 374761393u + uint(iy) * 668265263u + uint(iz) * 1274126177u + uint(seed) * 144665u + 1013904223u;
  h = (h ^ (h >> 13u)) * 1274126177u;
  return h ^ (h >> 16u);
}
const vec3 GRAD[16] = vec3[16](%(GRAD)s);
float gnoise(vec3 p, int seed){
  vec3 i = floor(p); vec3 f = p - i; ivec3 ii = ivec3(i);
  vec3 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float c0 = dot(GRAD[int(ihash(ii.x,     ii.y,     ii.z,     seed) & 15u)], f - vec3(0.0, 0.0, 0.0));
  float c1 = dot(GRAD[int(ihash(ii.x + 1, ii.y,     ii.z,     seed) & 15u)], f - vec3(1.0, 0.0, 0.0));
  float c2 = dot(GRAD[int(ihash(ii.x,     ii.y + 1, ii.z,     seed) & 15u)], f - vec3(0.0, 1.0, 0.0));
  float c3 = dot(GRAD[int(ihash(ii.x + 1, ii.y + 1, ii.z,     seed) & 15u)], f - vec3(1.0, 1.0, 0.0));
  float c4 = dot(GRAD[int(ihash(ii.x,     ii.y,     ii.z + 1, seed) & 15u)], f - vec3(0.0, 0.0, 1.0));
  float c5 = dot(GRAD[int(ihash(ii.x + 1, ii.y,     ii.z + 1, seed) & 15u)], f - vec3(1.0, 0.0, 1.0));
  float c6 = dot(GRAD[int(ihash(ii.x,     ii.y + 1, ii.z + 1, seed) & 15u)], f - vec3(0.0, 1.0, 1.0));
  float c7 = dot(GRAD[int(ihash(ii.x + 1, ii.y + 1, ii.z + 1, seed) & 15u)], f - vec3(1.0, 1.0, 1.0));
  float x00 = c0 + (c1 - c0) * u.x, x10 = c2 + (c3 - c2) * u.x, x01 = c4 + (c5 - c4) * u.x, x11 = c6 + (c7 - c6) * u.x;
  float y0 = x00 + (x10 - x00) * u.y, y1 = x01 + (x11 - x01) * u.y;
  return clamp(y0 + (y1 - y0) * u.z, -1.0, 1.0);
}
float fbm(vec3 p, int oct, float gain, int seed, float foot, int mode){
  float acc = 0.0, nrm = 0.0, amp = 1.0, fr = 1.0;
  for (int k = 0; k < oct; k++){
    float fade = clamp(1.0 - fr * foot * 2.0, 0.0, 1.0);
    if (k > 0 && fade <= 0.0) break;
    float v = gnoise(p * fr + float(k) * 17.31, seed + k);
    if (mode == 0) v = abs(v) * 1.7; else if (mode == 2) v = (1.0 - abs(v)) * (1.0 - abs(v)); else v = v * 0.5 + 0.5;
    acc += amp * fade * v; nrm += amp * fade; amp *= gain; fr *= 2.0;
  }
  return acc / max(nrm, 1e-9);
}
""" % dict(GRAD=", ".join("vec3(%d.0, %d.0, %d.0)" % g for g in _GRAD))


# ======================================================================== katmanlar
class Gen(object):
    """Bir malzeme için GLSL üretimi; doku birimleri (sırayla) ve yardımcı fonksiyonlar toplanır."""

    def __init__(self, diag, inst):
        self.diag = float(diag)
        self.inst = inst or []                          # [(min xyz, max xyz)] nesnenin dünya sınır kutuları (Link kopyaları)
        self.units = []                                 # ("img", Texture) | ("rgbm", key, arr, wrap) | ("tab", key, arr)
        self.fns = []
        self.need_proc = False
        self.nfn = 0
        self.dropped = 0

    def unit(self, spec, key):
        for i, (k, _s) in enumerate(self.units):
            if k == key:
                return i
        if len(self.units) >= MAX_UNITS:
            return None
        self.units.append((key, spec))
        return len(self.units) - 1

    # -- yerleşim
    @staticmethod
    def tex_params(L):
        rx, ry, rz = [math.radians(float(L.get(k, 0.0))) for k in ("tex_rx", "tex_ry", "tex_rz")]
        R = None
        if rx or ry or rz:
            cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
            Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
            Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            R = Rz @ Ry @ Rx
        s = np.array([float(L.get(k, 1.0)) for k in ("tex_sx", "tex_sy", "tex_sz")])
        s = np.where(np.abs(s) < 1e-9, 1e-9, s)
        return dict(proj=str(L.get("tex_proj", "cubic")), axis=int(L.get("tex_axis", 2)), s=s,
                    pos=np.array([float(L.get(k, 0.0)) for k in ("tex_px", "tex_py", "tex_pz")]),
                    R=R, world=bool(L.get("tex_world", False)), fit=bool(L.get("tex_fit", False)))

    def space_code(self, tp):
        """Q (doku uzayı), nl (dönmüş normal), he (nesne yarı boyutu) — render._tex_space."""
        o = ["    vec3 Q = P - %s - %s;" % ("vec3(0.0)" if tp["world"] else "wcen", _v3(tp["pos"])), "    vec3 nl = N;"]
        if tp["R"] is not None:
            R = tp["R"]
            cols = [_v3(R[:, j]) for j in range(3)]
            o.append("    Q = vec3(dot(Q, %s), dot(Q, %s), dot(Q, %s));" % tuple(cols))
            o.append("    nl = vec3(dot(nl, %s), dot(nl, %s), dot(nl, %s));" % tuple(cols))
        o.append("    Q /= %s;" % _v3(tp["s"]))
        o.append("    vec3 he = %s;" % ("vec3(%s)" % _f(self.diag / 4.0) if tp["world"] else "whe"))
        return o

    # -- stokastik döşeme tablosu (render: Texture._hash2, aynı karma)
    def _hex_table(self, uv_boxes):
        """uv kutuları [(umin, umax, vmin, vmax)] -> hex köşe indisi aralığı için öteleme tablosu. Döner (key, x0, y0, arr) ya da None."""
        xs, ys = [], []
        for (u0, u1, v0, v1) in uv_boxes:
            for u in (u0, u1):
                for v in (v0, v1):
                    x, y = u * 3.4641016, v * 3.4641016
                    xs.append(x)
                    ys.append(-0.57735027 * x + 1.15470054 * y)
        if not xs:
            return None
        x0, x1 = int(math.floor(min(xs))) - 2, int(math.floor(max(xs))) + 3
        y0, y1 = int(math.floor(min(ys))) - 2, int(math.floor(max(ys))) + 3
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if w > HEX_TAB_MAX or h > HEX_TAB_MAX or w * h > HEX_TAB_MAX * HEX_TAB_MAX // 4:
            return None
        W2, H2 = 1 << max(0, (w - 1).bit_length()), 1 << max(0, (h - 1).bit_length())
        ix = np.arange(x0, x0 + W2, dtype=np.float64)[None, :]
        iy = np.arange(y0, y0 + H2, dtype=np.float64)[:, None]
        a = np.sin(ix * 127.1 + iy * 311.7) * 43758.5453
        b = np.sin(ix * 269.5 + iy * 183.3) * 43758.5453
        ox, oy = a - np.floor(a), b - np.floor(b)
        qx = np.clip(np.round(ox * 4095.0), 0, 4095).astype(np.uint32)       # 12 + 12 bit, 3 kanal (alfasız: bkz. rgbm)
        qy = np.clip(np.round(oy * 4095.0), 0, 4095).astype(np.uint32)
        arr = np.stack([qx >> 4, ((qx & 15) << 4) | (qy >> 8), qy & 255], 2).astype(np.uint8)
        return ("hex", x0, y0, W2, H2), x0, y0, np.ascontiguousarray(arr)

    def _uv_boxes(self, tp, per, asp, sav, sp):
        """Nesnenin (kopyalarının) Q aralığından görüntü katmanının (u, v) aralıkları (stokastik tablo için)."""
        boxes = []
        for (lo, hi) in self.inst:
            lo, hi = np.array(lo, dtype=np.float64), np.array(hi, dtype=np.float64)
            cen, half = (lo + hi) / 2.0, (hi - lo) / 2.0
            C = np.zeros(3) if tp["world"] else cen
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - C - tp["pos"]
            if tp["R"] is not None:
                corners = corners @ tp["R"]
            Q = corners / tp["s"]
            qlo, qhi = Q.min(0), Q.max(0)
            hext = np.full(3, self.diag / 4.0) if tp["world"] else half
            proj, ax = tp["proj"], tp["axis"]
            if proj == "cubic":
                for (a_, b_) in _PLANE:
                    boxes.append((qlo[a_] / per, qhi[a_] / per, qlo[b_] / (per * asp), qhi[b_] / (per * asp)))
            elif proj == "planar":
                a_, b_ = _PLANE[ax]
                boxes.append((qlo[a_] / per, qhi[a_] / per, qlo[b_] / (per * asp), qhi[b_] / (per * asp)))
            else:
                a_, b_ = _PLANE[ax]
                if proj == "cylindrical":
                    R0 = max(hext[a_], hext[b_])
                else:
                    R0 = hext.max()
                rep = max(1.0, round(2.0 * math.pi * R0 / (sav * per)))
                if proj == "cylindrical":
                    boxes.append((-rep / 2.0, rep / 2.0, qlo[ax] / (per * asp), qhi[ax] / (per * asp)))
                else:
                    boxes.append((-rep / 2.0, rep / 2.0, 0.0, rep / 2.0 / asp))
        return boxes

    # -- tek görüntü örneği (düz ya da stokastik)
    def _sample_expr(self, u_tex, tx, uv, hexinfo):
        if hexinfo is None:
            return "texture2D(ytex%d, %s).rgb" % (u_tex, uv)
        u_tab, x0, y0 = hexinfo
        return "hexsamp(ytex%d, ytex%d, %s, ivec2(%d, %d), %s)" % (u_tex, u_tab, uv, x0, y0, _v3(tx.mean ** (1.0 / 2.2)))

    def image_layer(self, L, tx, gray):
        """render._tex_sample: görüntü katmanı -> GLSL fonksiyon adı. Döner (ad, kapsama_var_mı)."""
        tp = self.tex_params(L)
        u_tex = self.unit(("img", tx), ("img", id(tx)))
        if u_tex is None:
            self.dropped += 1
            return None
        w_ = float(L.get("tex_scale", 0.0))
        tw = w_ if w_ > 0 else tx.rel * self.diag
        tw = max(tw, 1e-6 * self.diag)
        per = tw * tx.span
        sp = float(tx.span)
        asp = float(tx.aspect)
        sav = float(np.mean(tp["s"]))
        fit = tp["fit"]
        once = bool(getattr(tx, "clamp", False))
        cov_on = bool(getattr(tx, "once", False))
        shift = 0.5 if (once and not fit) else 0.0
        hexinfo = None
        if getattr(tx, "stoch", False) and not once and not fit:     # render: stoch ve (sığdır değil) ve kenar/tek kez değil
            ht = self._hex_table(self._uv_boxes(tp, per, asp, sav, sp))
            if ht is not None:
                key, x0, y0, arr = ht
                u_tab = self.unit(("tab", key, arr), ("tab",) + key)
                if u_tab is not None:
                    hexinfo = (u_tab, x0, y0)
        name = "lay%d" % self.nfn
        self.nfn += 1
        ret = "float" if gray else "vec3"
        o = ["%s %s(vec3 P, vec3 N, out float cv){" % (ret, name)]
        o += self.space_code(tp)
        o.append("  vec3 tc = vec3(0.0); cv = 0.0;")

        def samp(u, v, w):
            lines = ["      vec2 uv = vec2(%s, %s) + vec2(%s);" % (u, v, _f(shift))]
            if once:
                lines.append("      float inb = (uv.x >= 0.0 && uv.x < 1.0 && uv.y >= 0.0 && uv.y < 1.0) ? 1.0 : 0.0;")
                lines.append("      uv = clamp(uv, 0.0, 1.0 - 1e-7);")
            else:
                lines.append("      float inb = 1.0;")
            lines.append("      tc += %s * %s; cv += %s * inb;" % (w, "lin(%s)" % self._sample_expr(u_tex, tx, "uv", hexinfo), w))
            return lines

        if tp["proj"] == "cubic":
            o.append("    vec3 w3 = pow(abs(nl), vec3(4.0)); w3 /= max(w3.x + w3.y + w3.z, 1e-30);")
            o.append("    float ws = 0.0;")
            for axx, (a_, b_) in enumerate(_PLANE):
                A, B = _AX[a_], _AX[b_]
                o.append("    if (w3.%s > 0.02) {" % _AX[axx])
                if fit:
                    o.append("      float su = fsign(%d, nl);" % axx)
                    u = "(su * Q.%s / max(2.0 * he.%s, 1e-9) + 0.5) / %s" % (A, A, _f(sp))
                    v = "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (B, B, _f(sp))
                else:
                    u, v = "Q.%s / %s" % (A, _f(per)), "Q.%s / %s" % (B, _f(per * asp))
                o += samp(u, v, "w3.%s" % _AX[axx])
                o.append("      ws += w3.%s;" % _AX[axx])
                o.append("    }")
            o.append("    tc /= max(ws, 1e-9); cv /= max(ws, 1e-9);")
        else:
            ax = tp["axis"]
            a_, b_ = _PLANE[ax]
            A, B, X = _AX[a_], _AX[b_], _AX[ax]
            o.append("    {")
            proj = tp["proj"]
            if fit and proj in ("cylindrical", "spherical"):
                o.append("      float ang = atan(Q.%s, Q.%s) / PI2 + 0.5;" % (B, A))
                if proj == "cylindrical":
                    u, v = "ang / %s" % _f(sp), "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (X, X, _f(sp))
                else:
                    o.append("      float rr = max(length(Q), 1e-12);")
                    o.append("      float lat = asin(clamp(Q.%s / rr, -1.0, 1.0)) / PI + 0.5;" % X)
                    u, v = "ang / %s" % _f(sp), "(1.0 - lat) / %s" % _f(sp)
            elif fit and proj == "planar":
                o.append("      float su = fsign(%d, nl);" % ax)
                u = "(su * Q.%s / max(2.0 * he.%s, 1e-9) + 0.5) / %s" % (A, A, _f(sp))
                v = "(0.5 - Q.%s / max(2.0 * he.%s, 1e-9)) / %s" % (B, B, _f(sp))
            elif proj in ("cylindrical", "spherical"):
                o.append("      float ang = atan(Q.%s, Q.%s) / PI2;" % (B, A))
                if tp["world"]:
                    o.append("      float R0 = %s;" % _f(self.diag / 4.0))
                elif proj == "cylindrical":
                    o.append("      float R0 = max(he.%s, he.%s);" % (A, B))
                else:
                    o.append("      float R0 = max(he.x, max(he.y, he.z));")
                o.append("      float rep = max(1.0, floor(PI2 * R0 / %s + 0.5));" % _f(sav * per))
                if proj == "cylindrical":
                    u, v = "ang * rep", "Q.%s / %s" % (X, _f(per * asp))
                else:
                    o.append("      float rr = max(length(Q), 1e-12);")
                    o.append("      float lat = asin(clamp(Q.%s / rr, -1.0, 1.0)) / PI + 0.5;" % X)
                    u, v = "ang * rep", "lat * rep / 2.0 / %s" % _f(asp)
            else:
                u, v = "Q.%s / %s" % (A, _f(per)), "Q.%s / %s" % (B, _f(per * asp))
            o += samp(u, v, "1.0")
            o.append("    }")
        if gray:
            o.append("  float r = pow(clamp(0.2126 * tc.r + 0.7152 * tc.g + 0.0722 * tc.b, 0.0, 1.0), 1.0 / 2.2);")
        else:
            o.append("  vec3 r = tc;")
        if bool(L.get("invert", False)):
            o.append("  r = 1.0 - r;")
        o.append("  return r;")
        o.append("}")
        self.fns.append("\n".join(o))
        # bump adımı için boyut bilgisi (render._bump_normal)
        if fit:
            szx = "2.0 * (whe.x + whe.y + whe.z - min(whe.x, min(whe.y, whe.z))) / 2.0 * %s" % _f(sav)
        else:
            szx = _f(tw * sav)
        epsx = "(%s) * %s / %s" % (szx, _f(sp), _f(float(tx.w)))
        return dict(fn=name, cov=cov_on, sz=szx, eps=epsx)

    def proc_layer(self, L, gray):
        """render._layer_val (prosedürel) + procedural.evaluate -> GLSL fonksiyon adı."""
        self.need_proc = True
        tp = self.tex_params(L)
        q = dict(L, axis=int(L.get("tex_axis", 2)))
        name = "lay%d" % self.nfn
        self.nfn += 1
        size = float(L.get("tex_scale", 0.0))
        sav = float(np.mean(tp["s"]))
        if size > 0.0:
            szx = _f(max(size, 1e-6 * self.diag))
        elif tp["world"]:
            szx = _f(self.diag * 0.25)
        else:
            szx = "max(max(he.x, max(he.y, he.z)) * 0.6, %s)" % _f(1e-6 * self.diag)
        o = ["%s %s(vec3 P, vec3 N, out float cv){" % ("float" if gray else "vec3", name)]
        o += self.space_code(tp)
        o.append("  cv = 1.0;")
        o.append("  float sz = %s;" % szx)
        o.append("  vec3 p = Q / sz;")
        o.append("  float ft = FOOT / (sz * %s);" % _f(sav))
        o.append(self._proc_eval(str(q.get("ptype", "turbulence")), q))
        if gray:
            o.append("  float r = g;")
        else:
            o.append("  vec3 r = %s + (%s - %s) * g;" % (_v3(_lin(q.get("pcolor1", (0, 0, 0)))), _v3(_lin(q.get("pcolor2", (1, 1, 1)))),
                                                       _v3(_lin(q.get("pcolor1", (0, 0, 0))))))
        if bool(L.get("invert", False)):
            o.append("  r = 1.0 - r;")
        o.append("  return r;")
        o.append("}")
        self.fns.append("\n".join(o))
        sz_bump = "(%s) * %s" % (szx.replace("he.", "whe.") if not (size > 0.0 or tp["world"]) else szx, _f(sav))
        return dict(fn=name, cov=False, sz=sz_bump, eps="(%s) / 40.0" % sz_bump)

    @staticmethod
    def _proc_eval(pt, q):
        """procedural.evaluate gövdesi -> 'float g' (0..1, kontrast ve değer uygulanmış)."""
        octv = max(1, int(q.get("pfreq", 3)))
        gain = min(max(float(q.get("ppower", 0.5)), 0.05), 0.95)
        seed = int(q.get("pseed", 0))
        stripes = max(float(q.get("pstripes", 6.0)), 0.1)
        ax = int(q.get("axis", 2))
        a_, b_ = _PLANE[ax]
        A, B, X = _AX[a_], _AX[b_], _AX[ax]
        S = _f(stripes)
        o = []
        if pt == "turbulence":
            o.append("  float v = fbm(p * 4.0, %d, %s, %d, ft * 4.0, 0);" % (octv, _f(gain), seed))
        elif pt == "fractal":
            o.append("  float v = fbm(p * 4.0, %d, %s, %d, ft * 4.0, 1);" % (octv, _f(gain), seed))
        elif pt == "veins":
            o.append("  float v = fbm(p * 4.0, %d, %s, %d, ft * 4.0, 2);" % (octv, _f(gain), seed))
        elif pt == "marble":
            o.append("  float t = fbm(p * 2.0, %d, %s, %d, ft * 2.0, 0);" % (octv, _f(gain), seed))
            o.append("  float v = 0.5 + 0.5 * sin(PI2 * (p.%s * %s + 0.9 * t));" % (X, S))
        elif pt == "wood":
            o.append("  float rr = sqrt(p.%s * p.%s + p.%s * p.%s);" % (A, A, B, B))
            o.append("  float t = fbm(p * 2.0, %d, %s, %d, ft * 2.0, 1);" % (octv, _f(gain), seed))
            o.append("  float v = rr * %s + 0.6 * (t - 0.5); v = v - floor(v); v = 1.0 - v * v;" % S)
        elif pt == "checker":
            o.append("  ivec3 s = ivec3(floor(p * %s));" % S)
            o.append("  float v = float((s.x + s.y + s.z) & 1);")
        elif pt == "grid":
            o.append("  vec3 fq = p * %s; fq = abs((fq - floor(fq)) - 0.5);" % S)
            o.append("  float v = (fq.%s > 0.44 || fq.%s > 0.44) ? 1.0 : 0.0;" % (A, B))
        elif pt == "dots":
            o.append("  vec3 cq = p * %s; cq = (cq - floor(cq)) - 0.5;" % S)
            o.append("  float v = (sqrt(cq.%s * cq.%s + cq.%s * cq.%s) < 0.32) ? 1.0 : 0.0;" % (A, A, B, B))
        elif pt == "bricks":
            o.append("  float bu = p.%s * %s * 0.5; float bw = p.%s * %s;" % (A, S, B, S))
            o.append("  float row = floor(bw); bu = bu + 0.5 * float(int(row) & 1);")
            o.append("  float fu = bu - floor(bu), fv = bw - floor(bw);")
            o.append("  bool mortar = (fu < 0.06) || (fv < 0.12);")
            o.append("  uint cell = ihash(int(floor(bu)), int(row), 0, %d);" % seed)
            o.append("  float v = mortar ? 0.18 : 0.62 + 0.38 * (float(cell & 255u) / 255.0);")
        elif pt == "ripples":
            o.append("  float rr = length(p);")
            o.append("  float t = fbm(p * 2.0, %d, %s, %d, ft * 2.0, 1);" % (octv, _f(gain), seed))
            o.append("  float v = 0.5 + 0.5 * sin(PI2 * (rr * %s + 0.4 * (t - 0.5)));" % S)
        else:
            o.append("  float v = 0.5;")
        c = float(q.get("pcontrast", 0.0))
        if c:
            k = (1.0 + c / 100.0 * 3.0) if c > 0 else (1.0 + c / 100.0)
            o.append("  v = (v - 0.5) * %s + 0.5;" % _f(k))
        o.append("  float g = clamp(v, 0.0, 1.0) * %s;" % _f(min(max(float(q.get("pvalue", 1.0)), 0.0), 1.0)))
        return "\n".join(o)

    def stack(self, layers, base, gray):
        """render._stack -> GLSL ifadeleri (değişken 'res' üzerinde). layers: [(katman, Texture|None)]."""
        o = []
        infos = []
        for L, tx in layers:
            info = self.proc_layer(L, gray) if L.get("type") == "proc" else self.image_layer(L, tx, gray)
            if info is None:
                continue
            infos.append(info)
            op = max(0.0, min(1.0, float(L.get("opacity", 1.0))))
            bm = _BLEND_ID.get(str(L.get("blend", "normal")), 0)
            fn = "blend1" if gray else "blend3"
            o.append("  { float cv; %s val = %s(P, N, cv); %s nw = %s(res, val, %d, %s);" %
                     ("float" if gray else "vec3", info["fn"], "float" if gray else "vec3", fn, bm, _f(op)))
            if info["cov"]:
                o.append("    nw = res + (nw - res) * cv;")
            o.append("    res = nw; }")
        return "\n".join(o), infos


def _layers(m, chan):
    """Kanalın etkin katmanları (render._prep_layers ile aynı süzme). Döner [(katman, Texture|None)]."""
    from .render import load_texture
    out = []
    for L in M.layers_of(m, chan):
        if not (L.get("on", True) and (L.get("type") == "proc" or L.get("tex"))):
            continue
        if L.get("type") == "proc":
            out.append((L, None))
        else:
            t_ = load_texture(L["tex"], L.get("tile"))
            if t_ is not None:
                out.append((L, t_))
    return out


_HEX_FN = """
vec3 hexsamp(sampler2D t, sampler2D tab, vec2 uv, ivec2 o0, vec3 meanS){
  vec3 mean = lin(meanS);
  float x = uv.x * 3.4641016, y = uv.y * 3.4641016;
  float s0 = x, s1 = -0.57735027 * x + 1.15470054 * y;
  float bx = floor(s0), by = floor(s1);
  float fx = s0 - bx, fy = s1 - by, fz = 1.0 - fx - fy;
  bool up = fz > 0.0;
  float w1 = up ? fz : -fz, w2 = up ? fy : 1.0 - fy, w3 = up ? fx : 1.0 - fx;
  vec2 v1 = up ? vec2(bx, by) : vec2(bx + 1.0, by + 1.0);
  vec2 v2 = up ? vec2(bx, by + 1.0) : vec2(bx + 1.0, by);
  vec2 v3 = up ? vec2(bx + 1.0, by) : vec2(bx, by + 1.0);
  vec3 ws = pow(max(vec3(w1, w2, w3), vec3(1e-6)), vec3(3.0)); ws /= (ws.x + ws.y + ws.z);
  vec2 g = vec2(dFdx(uv)), h = vec2(dFdy(uv));
  vec3 acc = vec3(0.0);
  for (int k = 0; k < 3; k++){
    vec2 vv = k == 0 ? v1 : (k == 1 ? v2 : v3);
    float wk = k == 0 ? ws.x : (k == 1 ? ws.y : ws.z);
    ivec3 q = ivec3(texelFetch(tab, ivec2(vv) - o0, 0).rgb * 255.0 + 0.5);
    vec2 off = vec2(float(q.x * 16 + q.y / 16), float((q.y - (q.y / 16) * 16) * 256 + q.z)) / 4095.0;
    acc += wk * (lin(textureGrad(t, uv + off, g, h).rgb) - mean);
  }
  float nrm = sqrt(ws.x * ws.x + ws.y * ws.y + ws.z * ws.z);
  return pow(max(mean + acc / nrm, 0.0), vec3(1.0 / 2.2));
}
"""


# ======================================================================== ortam ve ışıklar (sahne başına bir kez)
def env_setup(doc, center, S=None, shadow=None, probe=None):
    """Render'ın ortam / ışık kurallarını (render.render başı) GLSL parçalarına çevirir.
    Döner dict: code (FS fonksiyonları: ambient(N), envrefl(D, level), lights), units [(spec, key)], level_w (HDRI genişliği).
    shadow: dict(lo, hi, has_glass, nmax) -> gölge veren ışıklar seçilir (viewshadow), env["shadow"] = (specs, layout)."""
    if S is None:
        from . import settings as S
    from . import envmap
    env = None
    try:
        env = envmap.get_env(S)
    except Exception:
        env = None
    sky_on = env is None and float(getattr(S, "SKY_LIGHT", 0.0)) > 0.0
    env_i = (float(S.SKY_LIGHT) if sky_on else float(S.ENV_INTENSITY)) if (env is not None or sky_on) else 0.0
    env_rot = float(S.ENV_ROT)
    env_light = (env is not None or sky_on) and (sky_on or bool(S.ENV_LIGHT))
    env_refl = env is not None and bool(S.ENV_REFLECT)
    keep_lights = sky_on or bool(S.ENV_KEEP_LIGHTS)
    units = []
    # --- gökyüzü (render.sky_lin) tablosu: z -> doğrusal renk
    from . import skygrad as _skygrad
    sky_w = max(1e-3, float(S.SKY_HORIZON))
    keys = _skygrad.norm_keys(getattr(S, "SKY_GRADIENT", None))
    smooth = "linear" if str(getattr(S, "SKY_SMOOTH", "hermite")) == "linear" else "hermite"
    top, bot = np.array(S.BG_TOP, dtype=np.float64), np.array(S.BG_BOTTOM, dtype=np.float64)
    zs = np.linspace(-1.0, 1.0, 512)

    def sky_lin(z):
        z = np.asarray(z, dtype=np.float64)
        if keys:
            return _skygrad.sky_srgb(keys, z, smooth) ** 2.2
        wb = np.exp(-np.clip(z, 0.0, None) / sky_w)[:, None]
        return (top * (1.0 - wb) + bot * wb) ** 2.2
    sky_tab = sky_lin(zs)
    # tek ek doku ("aux" 512x512): sky tablosu (498-502. satırlar) + yansıma sondası (renk 0.., alfa 256..). Doku düğümü sayısı
    # kareyi doğrudan yavaşlatır (nesne başına ~0,25 ms / doku), bu yüzden hepsi tek birimde.
    aux = np.zeros((512, 512, 3))
    aux[498:503, :, :] = sky_tab[None, :, :]
    aux_ver = (hash(sky_tab.tobytes()), 0)
    if probe is not None:
        from . import viewprobe as _vp
        aux[:probe["rgb"].shape[0], :probe["rgb"].shape[1]] = probe["rgb"]
        aux[256:256 + probe["al"].shape[0], :probe["al"].shape[1]] = (probe["al"] * 8.0)[:, :, None]
        aux_ver = (aux_ver[0], probe["ver"])
    aux_key = ("rgbm", "aux", aux_ver)
    units.append((("rgbm", aux_key, aux, "clamp"), aux_key))
    code = []
    code.append("vec3 skylin(float z){ return rgbm(texture2D(ENVU_AUX, vec2(clamp(z, -1.0, 1.0) * %s + 0.5, %s))); }"
                % (_f(0.5 * 511.0 / 512.0), _f(500.5 / 512.0)))
    # --- yansıma ortamı
    level_w = 64
    if env_refl:
        lv0 = 0
        while lv0 + 1 < len(env.levels) and env.levels[lv0].shape[1] > 1024:
            lv0 += 1
        img = np.asarray(env.levels[lv0], dtype=np.float64)
        level_w = int(env.levels[0].shape[1])
        key = ("rgbm", "env", id(env), lv0)
        units.append((("rgbm", key, img, "repeat"), key))
        code.append("""vec3 envlook(vec3 d, float level){
  float lon = atan(d.y, d.x) / PI2 + 0.5 - %(ROT)s;
  float lat = asin(clamp(d.z, -1.0, 1.0));
  vec2 uv = vec2(lon - floor(lon), clamp(0.5 - lat / PI, 0.0, 1.0));
  vec3 rad = rgbm(texture2D(ENVU_ENV, uv, max(level - %(LV0)s, 0.0)));
  float lm = 0.2126 * rad.r + 0.7152 * rad.g + 0.0722 * rad.b;
  rad *= min(%(CAP)s / max(lm, 1e-6), 1.0);
  return rad * %(EI)s;
}""" % dict(ROT=_f(env_rot / 360.0), LV0=_f(lv0), CAP=_f(envmap.REFL_CAP), EI=_f(env_i)))
    else:
        code.append("vec3 envlook(vec3 d, float level){ return skylin(d.z); }")
    code.append("float glosslevel(float g){ float lv = 8.0 * pow(1.0 - clamp(g, 0.0, 1.0), 1.5) - %s; return floor(max(lv, 0.0) * 4.0 + 0.5) / 4.0; }"
                % _f(math.log2(2048.0 / max(level_w, 1))))
    if probe is not None:
        code.append(_vp.glsl(probe))
    has_env_obj = env is not None or sky_on
    # --- ortam ışığı (sabit + gökyüzü)
    amb = np.array([float(S.AMBIENT) * max(0.0, float(c)) ** 2.2 for c in S.AMBIENT_COLOR])
    if env_light and not keep_lights:
        amb = amb * 0.0
    amb_code = ["vec3 ambient(vec3 N){", "  vec3 a = %s;" % _v3(amb)]
    if env_light:
        c_, s_ = math.cos(math.radians(-env_rot)), math.sin(math.radians(-env_rot))
        amb_code.append("  vec3 n = vec3(%s * N.x - %s * N.y, %s * N.x + %s * N.y, N.z);" % (_f(c_), _f(s_), _f(s_), _f(c_)))
        if sky_on:                                              # _SkyEnv.sh_light: kosinüs ağırlıklı yarım küre tablosu
            k = np.arange(256) + 0.5
            r_, ph_ = np.sqrt(k / 256.0), k * 2.399963
            loc = np.stack([r_ * np.cos(ph_), r_ * np.sin(ph_), np.sqrt(1.0 - k / 256.0)], 1)
            ztab = np.linspace(-1.0, 1.0, 33)
            tab = []
            for z in ztab:
                s2 = math.sqrt(max(0.0, 1.0 - z * z))
                dz = loc[:, 2] * z - loc[:, 0] * s2
                tab.append(sky_lin(dz).mean(0))
            tab = np.array(tab) * env_i
            amb_code.append("  const vec3 ST[33] = vec3[33](%s);" % ", ".join(_v3(t) for t in tab))
            amb_code.append("  float fz = (clamp(n.z, -1.0, 1.0) + 1.0) * 16.0; int iz = int(min(floor(fz), 31.0));")
            amb_code.append("  a += mix(ST[iz], ST[iz + 1], fz - float(iz));")
        else:                                                   # Env.sh_light: SH9 ışınım x k
            wgt = [1.0, 2.0 / 3.0, 2.0 / 3.0, 2.0 / 3.0, 0.25, 0.25, 0.25, 0.25, 0.25]
            co = np.asarray(env.sh, dtype=np.float64) * np.array(wgt)[:, None] * float(env.k) * env_i
            amb_code.append("  float x = n.x, y = n.y, z = n.z;")
            basis = ["0.282095", "0.488603 * y", "0.488603 * z", "0.488603 * x", "1.092548 * x * y", "1.092548 * y * z",
                     "0.315392 * (3.0 * z * z - 1.0)", "1.092548 * x * z", "0.546274 * (x * x - y * y)"]
            terms = " + ".join("%s * (%s)" % (_v3(co[i]), basis[i]) for i in range(9))
            amb_code.append("  a += max(%s, vec3(0.0));" % terms)
    amb_code.append("  return a;")
    amb_code.append("}")
    code.append("\n".join(amb_code))
    # --- doğrudan ışıklar (render: _OBJ_LIGHTS ya da panel; HDRI güneşi)
    obj_lights = []
    if str(getattr(S, "LIGHT_MODE", "objects")) == "objects" and doc is not None:
        try:
            from . import lightobj
            obj_lights = lightobj.collect(doc)
        except Exception:
            obj_lights = []
    if obj_lights:
        src = obj_lights[:16]
    elif env_light and not keep_lights:
        src = []
    else:
        src = list(S.LIGHTS)[:3]
    L = []
    for lt in src:
        if not lt.get("on", True) or float(lt["power"]) <= 0.0:
            continue
        col = _lin(lt["color"])
        d = {}
        if lt.get("kind") in ("point", "spot") and "wpos" in lt:
            lp = [float(x) for x in lt["wpos"]]
            ref = float(lt.get("ref", 0.0))
            if ref <= 0.0:
                ref = max(1e-6, math.sqrt(sum((lp[k] - center[k]) ** 2 for k in range(3))))
            d = dict(pos=lp, ref=ref, fall=str(lt.get("falloff", "inverse_square")), radius=max(0.0, float(lt.get("radius", 0.0))))
            if lt.get("kind") == "spot":
                half = math.radians(max(0.5, min(float(lt.get("cone", 45.0)), 178.0)) / 2.0)
                inn = max(0.0, half - math.radians(max(0.0, float(lt.get("edge", 10.0)))))
                ax = np.array(lt.get("axis", (0.0, 0.0, -1.0)), dtype=np.float64)
                ax = ax / (np.linalg.norm(ax) or 1.0)
                d.update(spot=True, axis=ax, co=math.cos(half), ci=math.cos(inn))
        elif "wdir" in lt:
            w = np.array(lt["wdir"], dtype=np.float64)
            d = dict(wdir=w / (np.linalg.norm(w) or 1.0))
        elif "dir" in lt:
            dx, dy, dz = lt["dir"]
            d = dict(edir=(-dx, -dy, -dz))                   # kamera uzayı: -dx*sağ - dy*yukarı + dz*ileri (ileri = -z)
        else:
            hh, vv = math.radians(float(lt["h"])), math.radians(float(lt["v"]))
            d = dict(edir=(math.cos(vv) * math.sin(hh), math.sin(vv), math.cos(vv) * math.cos(hh)))
        d.update(col=col, power=float(lt["power"]), ad=1.0 if lt.get("diffuse", True) else 0.0,
                 asp=1.0 if lt.get("spec", True) else 0.0, cast=bool(lt.get("shadow", False)) and bool(S.SHADOWS),
                 soft=float(lt.get("soft", S.SOFT_SHADOW)))
        L.append(d)
    if env_light and bool(getattr(S, "ENV_SUN", True)) and env is not None and getattr(env, "sun", None) is not None:
        sd = np.array(env.sun["dir"], dtype=np.float64)
        a_ = math.radians(env_rot)
        c_, s_ = math.cos(a_), math.sin(a_)
        sd = np.array([c_ * sd[0] - s_ * sd[1], s_ * sd[0] + c_ * sd[1], sd[2]])
        L.append(dict(wdir=sd / (np.linalg.norm(sd) or 1.0), col=list(np.maximum(env.sun["power"], 0.0) * env_i), power=1.0,
                      ad=1.0, asp=1.0, cast=bool(S.SHADOWS), soft=float(S.SOFT_SHADOW)))
    # --- gölgeler (viewshadow): en güçlü nmax ışık
    sh_specs, sh_layout, sh_code, sh_names = None, None, "", {}
    if shadow is not None and int(shadow.get("nmax", 0)) > 0:
        from . import viewshadow as _vsh
        sh_specs, sh_layout = _vsh.plan(L, shadow["lo"], shadow["hi"], int(shadow["nmax"]), bool(shadow.get("has_glass")),
                                        float(S.SOFT_SHADOW), float(getattr(S, "SOFT_SCALE", 1.0)))
        if sh_specs:
            sh_code, sh_names = _vsh.glsl(sh_specs, sh_layout)
    if sh_code:
        code.append(sh_code)
    lc = []
    for li, d in enumerate(L):
        o = ["  {"]
        if "pos" in d:
            o.append("    vec3 dv = %s - P; float dd = max(length(dv), 1e-9); vec3 Ln = dv / dd;" % _v3(d["pos"]))
            if d["fall"] == "inverse_square":
                o.append("    float att = min(pow(%s / dd, 2.0), 16.0);" % _f(d["ref"]))
            elif d["fall"] == "linear":
                o.append("    float att = min(%s / dd, 16.0);" % _f(d["ref"]))
            else:
                o.append("    float att = 1.0;")
            if d.get("spot"):
                o.append("    float cs = -dot(Ln, %s);" % _v3(d["axis"]))
                o.append("    float xs = clamp((cs - %s) / %s, 0.0, 1.0); att *= xs * xs * (3.0 - 2.0 * xs);"
                         % (_f(d["co"]), _f(max(d["ci"] - d["co"], 1e-6))))
            o.append("    float pw = %s * att;" % _f(d["power"]))
        elif "wdir" in d:
            o.append("    vec3 Ln = %s; float pw = %s;" % (_v3(d["wdir"]), _f(d["power"])))
        else:
            o.append("    vec3 Ln = normalize(vec3(dot(ivc0.xyz, %s), dot(ivc1.xyz, %s), dot(ivc2.xyz, %s))); float pw = %s;"
                     % (_v3(d["edir"]), _v3(d["edir"]), _v3(d["edir"]), _f(d["power"])))
        o.append("    float ndl = max(dot(N, Ln), 0.0);")
        o.append("    vec3 H = normalize(Ln + V); float nh = max(dot(N, H), 0.0); float lp = ndl > 0.0 ? 1.0 : 0.0;")
        if li in sh_names:                                     # render: ndl ve parlama gölge görünürlüğüyle çarpılır
            o.append("    if (lp > 0.0 && dot(Ng, Ln) > 0.0) { float vis = %s(P, Ng); ndl *= vis; lp *= vis; }" % sh_names[li])
        o.append("    lit += %s * pw * ndl * %s;" % (_f(d["ad"]), _v3(d["col"])))
        o.append("    spc += %s * min(pw / 0.7, 1.5) * pow(nh, shin) * lp * %s;" % (_f(d["asp"]), _v3(d["col"])))
        o.append("    if (coat > 0.0) cts += %s * coat * min(pw / 0.7, 1.5) * pow(nh, 900.0) * lp * %s;" % (_f(d["asp"]), _v3(d["col"])))
        o.append("  }")
        lc.append("\n".join(o))
    code.append("void lights(vec3 P, vec3 N, vec3 Ng, vec3 V, float shin, float coat, inout vec3 lit, inout vec3 spc, inout vec3 cts){\n%s\n}"
                % "\n".join(lc))
    tm = str(getattr(S, "TONEMAP", "none"))
    if tm == "soft":
        tms = "c = c * (1.0 + c * 0.25) / (1.0 + c);"
    elif tm == "aces":
        tms = "c = (c * (2.51 * c + 0.03)) / (c * (2.43 * c + 0.59) + 0.14);"
    else:
        tms = ""
    code.append("vec3 tonemap(vec3 c){ c *= %s; %s return c; }" % (_f(float(getattr(S, "EXPOSURE", 1.0))), tms))
    flags = dict(refl_on=bool(S.REFLECTIONS), glass=bool(S.GLASS), fresnel=bool(S.FRESNEL), env=has_env_obj,
                 bump_soft=max(0.25, float(getattr(S, "BUMP_SOFT", 1.0))))
    return dict(code=code, units=units, flags=flags, probe=probe is not None, aux_key=aux_key,
                shadow=(sh_specs, sh_layout) if sh_code else None)


# ======================================================================== tam gölgelendirici
_RGBM = "vec3 rgbm(vec4 t){ return pow(t.rgb, vec3(2.2)) * 8.0; }"      # HDR, 3 kanal (viewmat._unit_node)


def build(mat, lc, lh, diag, env, inst=None, dcols=None):
    """Malzeme + sahne ortamı -> (vs, fs, units). units: [(spec, key)] sırayla doku birimleri 0..n-1."""
    m = M.normalize(mat)
    g = Gen(diag, inst)
    fl = env["flags"]
    chans = {c: _layers(m, c) for c in M.CHANNELS}
    body = []
    # taban değerleri (render: tablolar)
    base_col = "lin(%s)" % _v3(m["color"]) if m.get("color") is not None else "lin(dcol.rgb)"
    body.append("  vec3 Nn = normalize(wnrm); if (!gl_FrontFacing) Nn = -Nn;")
    body.append("  vec3 P = wpos; vec3 N = Nn;")
    body.append("  float FOOT = max(length(dFdx(wpos)), length(dFdy(wpos)));")
    st_code = {}
    for ch, kind, base in (("color", "c", base_col), ("diffuse", "g", _f(m["diffuse"])), ("spec", "g", _f(m["spec"])),
                           ("gloss", "g", _f(m["gloss"])), ("refl", "g", _f(m["refl"] if fl["refl_on"] else 0.0)),
                           ("lum", "g", _f(m["lum"])),
                           ("transp", "g", "(1.0 - %s)" % _f(1.0 - float(m["transp"]) if fl["glass"] else 1.0))):
        lys = chans.get(ch) or []
        if ch == "refl" and not fl["refl_on"]:
            lys = []
        code, _inf = g.stack(lys, None, kind == "g")
        t = "vec3" if kind == "c" else "float"
        st_code[ch] = (t, base, code)
    # bump
    bump_lys = chans.get("bump") or []
    bh = float(m.get("bump_height", 1.0))
    bump_fn = None
    if bump_lys and bh > 0.0:
        code, infos = g.stack(bump_lys, None, True)
        if infos:
            lo, hi = float(m.get("bump_lo", 0.0)), float(m.get("bump_hi", 1.0))
            lvl = ("  res = clamp((res - %s) / %s, 0.0, 1.0);" % (_f(lo), _f(max(hi - lo, 1e-3)))) if (lo > 0.0 or hi < 1.0) else ""
            # yükseklik: alt katmanlar FOOT'u kullanır (fonksiyon parametresi olarak)
            g.fns.append("float bumph(vec3 P, vec3 N, float FOOT){\n  float res = 0.0;\n%s\n%s\n  return res;\n}"
                         % (code.replace("(P, N, cv)", "(P, N, cv, FOOT)"), lvl))
            eps = "min(" * (len(infos) - 1) + ", ".join(["(%s)" % infos[0]["eps"]] + ["(%s))" % i["eps"] for i in infos[1:]]) \
                if len(infos) > 1 else "(%s)" % infos[0]["eps"]
            sref = "(%s) / %s" % (" + ".join("(%s)" % i["sz"] for i in infos), _f(len(infos)))
            bump_fn = dict(eps=eps, sref=sref, h=bh)
    # katman fonksiyonları FOOT'u global gibi kullanır: imzaya ekle
    fns = [f.replace("(vec3 P, vec3 N, out float cv){", "(vec3 P, vec3 N, out float cv, float FOOT){") for f in g.fns]
    fix = lambda s: s.replace("(P, N, cv)", "(P, N, cv, FOOT)")
    for ch in st_code:
        t, base, code = st_code[ch]
        body.append("  %s %s_v; { %s res = %s;\n%s\n  %s_v = res; }" % (t, ch, t, base, fix(code), ch))
    body.append("  vec3 albedo = max(color_v, 0.0);")
    body.append("  float dif = clamp(diffuse_v, 0.0, 1.5);")
    body.append("  float spk = clamp(spec_v, 0.0, 1.5);")
    body.append("  float glv = clamp(gloss_v, 0.0, 1.0); float shin = 2.0 + 600.0 * glv * glv;")
    body.append("  float rfl = clamp(refl_v, 0.0, 1.0);")
    body.append("  float lum = clamp(lum_v, 0.0, 4.0);")
    body.append("  float a = 1.0 - clamp(transp_v, 0.0, 1.0);")
    ab = float(m.get("absorb", 0.0))
    if ab > 0.0 and fl["glass"]:
        body.append("  if (a < 0.999) a = a + (1.0 - a) * %s * 0.7;" % _f(min(max(ab, 0.0), 1.0)))
    if bump_fn is not None:
        body.append("  {")
        body.append("    float bfoot = FOOT * %s;" % _f(fl["bump_soft"]))
        body.append("    float eps = max((%s) * %s, 0.5 * bfoot);" % (bump_fn["eps"].replace("whe.", "whe."), _f(BUMP_STEP)))
        body.append("    vec3 gr = vec3(0.0);")
        for sv in _TETRA:
            body.append("    gr += %s * bumph(P + eps * %s, Nn, bfoot);" % (_v3(sv), _v3(sv)))
        body.append("    gr /= 4.0 * eps;")
        body.append("    float hrel = %s * %s * (%s);" % (_f(bump_fn["h"]), _f(BUMP_REL), bump_fn["sref"]))
        body.append("    vec3 nb = Nn - hrel * (gr - dot(gr, Nn) * Nn);")
        body.append("    N = normalize(nb);")
        body.append("  }")
    coat = float(m.get("coat", 0.0))
    body.append("  vec3 V = normalize(wcam - P);")
    body.append("  vec3 lit = ambient(N); vec3 spc = vec3(0.0); vec3 cts = vec3(0.0);")
    body.append("  vec3 Ng = normalize(cross(dFdx(wpos), dFdy(wpos))); if (dot(Ng, Nn) < 0.0) Ng = -Ng;")
    body.append("  lights(P, N, Ng, V, shin, %s, lit, spc, cts);" % _f(coat))
    body.append("  vec3 albraw = albedo;")
    body.append("  spc *= spk * ((1.0 - %s) + %s * min(albraw * 1.4, vec3(1.0)));" % (_f(m["metal"]), _f(m["metal"])))
    body.append("  vec3 surf = albedo * dif * lit + spc + cts + lum * albraw * 1.5;")
    # yansıma (render: R, wl, wr, wt)
    body.append("  float cosv = clamp(dot(N, V), 0.0, 1.0); float fr5 = pow(1.0 - cosv, 5.0);")
    body.append("  bool glass = a < 0.999;")
    body.append("  float rop = rfl;")
    if fl["fresnel"]:
        body.append("  if (rop > 0.0) rop = clamp(rop + (1.0 - rop) * fr5, 0.0, 0.95);")
    if coat > 0.0 and fl["refl_on"]:
        body.append("  rop = clamp(1.0 - (1.0 - rop) * (1.0 - %s * (0.04 + 0.96 * fr5)), 0.0, 0.95);" % _f(coat))
    body.append("  float R = glass ? clamp(rfl * (0.15 + 0.85 * fr5), 0.0, 0.95) : rop;")
    body.append("  vec3 rc = vec3(0.0);")
    body.append("  if (R > 0.0) { vec3 D = reflect(-V, N); float lv = %s; rc = envlook(normalize(D), lv); }"
                % ("glass ? 0.0 : glosslevel(glv)" if fl["env"] else "0.0"))
    if env.get("probe"):                                  # nesne / zemin yansıması: sonda (parlak yüzeyde keskin, pürüzlüde silinir)
        body.append("  float pwt = glass ? 1.0 : smoothstep(0.55, 0.9, glv);")
        body.append("  if (R > 0.01 && pwt > 0.0) { vec3 D = normalize(reflect(-V, N)); vec4 pr = probelook(P, D);"
                    " rc = mix(rc, pr.rgb + (1.0 - pr.a) * envlook(D, 0.0), pwt); }")
    body.append("  float al = 1.0 - (1.0 - a) * (1.0 - R);")
    body.append("  vec3 c = (surf * a * (1.0 - R) + rc * R) / max(al, 1e-4);")
    body.append("  c = tonemap(c);")
    body.append("  vec3 outc = pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2));")
    if dcols and len(dcols) <= 24:
        conds = " && ".join("distance(dcol.rgb, %s) > 0.03" % _v3(c) for c in dcols)
        body.append("  if (%s) { outc = mix(outc, dcol.rgb, 0.75); al = max(al, 0.6); }" % conds)
    body.append("  gl_FragColor = vec4(outc, al);")
    # birimler: katmanlar + ortam
    units = list(g.units)
    env_units = env["units"]
    nmat = len(units)
    samplers = ["uniform sampler2D ytex%d;" % i for i in range(nmat)]
    env_code = "\n".join(env["code"])
    for j, (spec, key) in enumerate(env_units):
        samplers.append("uniform sampler2D ytex%d;" % (nmat + j))
        units.append((key, spec))
    idx = {}
    for j, (_spec, key) in enumerate(env_units):
        idx[key[1]] = nmat + j
    env_code = env_code.replace("ENVU_AUX", "ytex%d" % idx["aux"])
    env_code = env_code.replace("ENVU_ENV", "ytex%d" % idx.get("env", idx["aux"]))
    fs = _FS_COMMON % dict(SAMPLERS="\n".join(samplers))
    fs += _RGBM + "\n"
    if g.need_proc:
        fs += _FS_PROC
    if any(k[0] == "tab" for k, _s in g.units):
        fs += _HEX_FN
    fs += "\n".join(fns) + "\n"
    fs += env_code + "\n"
    fs += "void main(){\n%s\n}\n" % "\n".join(body)
    vs = _VS % dict(LC=_v3(lc), LH=_v3(lh))
    return vs, fs, units, g.dropped
