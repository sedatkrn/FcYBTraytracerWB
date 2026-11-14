# -*- coding: utf-8 -*-
"""Render sonrası lens efektleri: ışık başına lens flare ve parlak piksellerin taşması (bloom).

Flare gerçek kamerada lensin içindeki yansımalardan doğar; burada render bittikten sonra 2B olarak eklenir.
Parçalar (her biri ışık nesnesinde ayrı açılır):
  glow       merkez parlaması: ışığın etrafındaki yumuşak hale (ortası beyaza kaçan sıcak çekirdek)
  ring       merkezin çevresinde ince, kenarı hafif gökkuşağı renkli halka
  streaks    yıldız çizgileri (ışın sayısı, açı, uzunluk)
  anamorphic sinema lensi gibi yatay, mavimsi çizgi
  ghosts     ışıktan ekran merkezine giden doğru üzerinde dizilen renkli lens yansımaları
Gizlenme: kameradan ışığa birkaç ışın atılır; ışık nesne arkasındaysa flare çizilmez, kısmen kapalıysa söner,
cam arkasındaysa saydamlık oranında zayıflar. Spot ışıkta kamera koninin dışındaysa flare söner.

Efektler ton eşlemeden çıkmış (sRGB) görüntüye doğrusal uzayda eklenir. Son render'ın efektsiz görüntüsü ve flare
kaynakları LAST'te tutulur: efekt ayarı değişince `reapply()` yeniden render almadan sonucu günceller.
"""
import hashlib

import numpy as np

from . import settings as S

# -- hazır stiller (ışık nesnesinin FlareStyle özelliği; "Custom" = elle ayar)
STYLES = ("Custom", "Simple", "Sun", "StreetLamp", "Cinema")
STYLE_LABELS = {"Custom": "Özel", "Simple": "Sade parlama", "Sun": "Güneş", "StreetLamp": "Sokak lambası", "Cinema": "Sinema (anamorfik)"}

# özellik adı -> flare sözlüğündeki anahtar
PROPS = (("FlareIntensity", "intensity"), ("FlareSize", "size"), ("FlareGlow", "glow"), ("FlareRing", "ring"),
         ("FlareStreaks", "streaks"), ("FlareStreakCount", "streak_n"), ("FlareStreakAngle", "streak_angle"),
         ("FlareStreakLength", "streak_len"), ("FlareAnamorphic", "anam"), ("FlareAnamorphicLength", "anam_len"),
         ("FlareGhosts", "ghosts"), ("FlareGhostCount", "ghost_n"), ("FlareOcclusion", "occlude"))

DEFAULT = dict(on=False, intensity=100.0, size=8.0, glow=True, ring=False, streaks=False, streak_n=6, streak_angle=15.0,
               streak_len=30.0, anam=False, anam_len=60.0, ghosts=False, ghost_n=6, occlude=True)

PRESETS = {
    "Simple": dict(intensity=100.0, size=8.0, glow=True, ring=False, streaks=False, anam=False, ghosts=False),
    "Sun": dict(intensity=100.0, size=12.0, glow=True, ring=True, streaks=True, streak_n=6, streak_angle=15.0,
                streak_len=35.0, anam=False, ghosts=True, ghost_n=7),
    "StreetLamp": dict(intensity=90.0, size=6.0, glow=True, ring=False, streaks=True, streak_n=4, streak_angle=0.0,
                       streak_len=22.0, anam=False, ghosts=False),
    "Cinema": dict(intensity=100.0, size=7.0, glow=True, ring=False, streaks=False, anam=True, anam_len=70.0,
                   ghosts=True, ghost_n=5),
}


def preset(style):
    """Stilin tam parametre sözlüğü (DEFAULT + stilin değerleri)."""
    d = dict(DEFAULT)
    d.update(PRESETS.get(style, {}))
    return d


SUN_NAME = "__sun__"


def sun_params():
    """Ortam (gökyüzü / HDRI) güneşinin flare'ı: panelin FLARE_SUN / FLARE_SUN_STYLE / FLARE_SUN_INTENSITY ayarlarından."""
    st = str(getattr(S, "FLARE_SUN_STYLE", "Sun"))
    d = preset(st if st in PRESETS else "Sun")
    d["intensity"] = float(getattr(S, "FLARE_SUN_INTENSITY", 1.0)) * 100.0
    d["on"] = bool(getattr(S, "FLARE_SUN", False))
    return d


LAST = None          # son render: dict(base=(h,w,3) sRGB float, src=[kaynak], w, h)


# ---------------------------------------------------------------- yardımcılar
def _seed(name):
    return int(hashlib.md5(str(name).encode("utf-8")).hexdigest()[:8], 16)


def _win(cx, cy, R, w, h):
    """(cx, cy) merkezli R yarıçaplı karenin görüntü içindeki kısmı: (y dilimi, x dilimi, dx, dy) ya da None."""
    x0, x1 = max(0, int(np.floor(cx - R))), min(w, int(np.ceil(cx + R)) + 1)
    y0, y1 = max(0, int(np.floor(cy - R))), min(h, int(np.ceil(cy + R)) + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    xs = np.arange(x0, x1, dtype=np.float32) + 0.5 - np.float32(cx)
    ys = np.arange(y0, y1, dtype=np.float32) + 0.5 - np.float32(cy)
    return slice(y0, y1), slice(x0, x1), xs[None, :], ys[:, None]


def _tail(r, R):
    """Pencere sınırında dikiş kalmasın: r -> R'ye giderken 0'a inen yumuşak çarpan."""
    q = np.clip(1.0 - r / np.float32(R), 0.0, 1.0)
    return q * q


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _add(buf, ys, xs, val, col):
    buf[ys, xs] += val[..., None] * np.asarray(col, dtype=np.float32)[None, None, :]


# ---------------------------------------------------------------- flare parçaları
def _glow(buf, x, y, S_, col, I):
    w, h = buf.shape[1], buf.shape[0]
    R = 3.2 * S_
    wn = _win(x, y, R, w, h)
    if wn is None:
        return
    ys, xs, dx, dy = wn
    r = np.sqrt(dx * dx + dy * dy)
    core = np.exp(-(r / np.float32(max(0.09 * S_, 0.8))) ** 2) * 1.8
    halo = (np.exp(-(r / np.float32(0.65 * S_)) ** 2) * 0.30 + 0.14 / (1.0 + (r / np.float32(0.25 * S_)) ** 2)) * _tail(r, R)
    white = 0.55 * np.ones(3, np.float32) + 0.45 * col
    _add(buf, ys, xs, core * I, white)
    _add(buf, ys, xs, halo * I, col)


def _ring(buf, x, y, S_, col, I):
    w, h = buf.shape[1], buf.shape[0]
    rr = 1.7 * S_
    wd = max(0.07 * S_, 1.0)
    wn = _win(x, y, rr * 1.06 + 3 * wd, w, h)
    if wn is None:
        return
    ys, xs, dx, dy = wn
    r = np.sqrt(dx * dx + dy * dy)
    out = np.zeros(r.shape + (3,), np.float32)
    for c_, k_ in enumerate((1.025, 1.0, 0.975)):                 # kırmızı dışta, mavi içte: hafif renk ayrışması
        out[..., c_] = np.exp(-((r - rr * k_) / np.float32(wd)) ** 2)
    buf[ys, xs] += out * (0.08 * I) * (0.5 + 0.5 * col)[None, None, :]


def _streaks(buf, x, y, S_, col, I, n, ang_deg, L):
    w, h = buf.shape[1], buf.shape[0]
    n = max(2, int(n))
    L = max(L, 2.0)
    wn = _win(x, y, L, w, h)
    if wn is None:
        return
    ys, xs, dx, dy = wn
    r = np.sqrt(dx * dx + dy * dy)
    phi = np.arctan2(-dy, dx)                                    # ekranda y aşağı: açı saat yönünün tersine
    sec = np.float32(2.0 * np.pi / n)
    d = np.mod(phi - np.float32(np.radians(ang_deg)), sec)
    d = np.minimum(d, sec - d)
    perp = r * np.sin(d)
    w0 = np.float32(max(0.9, 0.010 * S_)) * (1.0 + 1.2 * r / np.float32(L))
    amp = np.clip(1.0 - r / np.float32(L), 0.0, 1.0) ** 2.2
    val = (np.exp(-(perp / w0) ** 2) * 0.85 + np.exp(-(perp / (5.0 * w0)) ** 2) * 0.10) * amp
    _add(buf, ys, xs, val * I, 0.45 + 0.55 * col)


def _anamorphic(buf, x, y, S_, col, I, L):
    w, h = buf.shape[1], buf.shape[0]
    hw = max(0.9, 0.0045 * h)
    y0, y1 = max(0, int(y - 14 * hw)), min(h, int(y + 14 * hw) + 1)
    if y0 >= y1:
        return
    dx = (np.arange(w, dtype=np.float32) + 0.5 - np.float32(x))[None, :]
    dy = (np.arange(y0, y1, dtype=np.float32) + 0.5 - np.float32(y))[:, None]
    Lx = np.float32(max(L, 4.0) / 3.0)
    along = np.exp(-np.abs(dx) / Lx)
    val = (np.exp(-(dy / np.float32(hw)) ** 2) * 0.55 + np.exp(-(dy / np.float32(4.5 * hw)) ** 2) * 0.07) * along
    tint = np.array([0.30, 0.55, 1.0], np.float32) * 0.75 + col * 0.25
    buf[y0:y1] += val[..., None] * (I * tint)[None, None, :]


_GHOST_TINTS = np.array([[1.0, 0.55, 0.25], [0.45, 1.0, 0.55], [0.35, 0.6, 1.0], [1.0, 0.4, 0.65],
                         [0.95, 0.9, 0.45], [0.55, 0.85, 1.0]], np.float32)


def _ghosts(buf, x, y, S_, col, I, n, seed):
    w, h = buf.shape[1], buf.shape[0]
    rng = np.random.RandomState(seed % (2 ** 31))
    cx, cy = w / 2.0, h / 2.0
    vx, vy = x - cx, y - cy
    for i in range(max(0, int(n))):
        k = rng.uniform(-1.25, 0.65)
        if abs(k - 1.0) < 0.25:
            k -= 0.4
        gx, gy = cx + vx * k, cy + vy * k
        rg = S_ * rng.uniform(0.18, 1.0)
        amp = rng.uniform(0.035, 0.10) * (0.6 + 0.4 * min(1.0, 0.3 * S_ / max(rg, 1e-3)))
        tint = _GHOST_TINTS[rng.randint(len(_GHOST_TINTS))] * 0.7 + col * 0.3
        ring = rng.uniform() < 0.3
        wn = _win(gx, gy, rg * 1.15, w, h)
        if wn is None:
            continue
        ys, xs, dx, dy = wn
        r = np.sqrt(dx * dx + dy * dy)
        if ring:
            val = np.exp(-((r - rg * 0.9) / np.float32(max(0.07 * rg, 1.0))) ** 2)
        else:
            val = 1.0 - _smooth(rg * 0.82, rg, r)
            val = val * 0.75 + 0.45 * np.exp(-((r - rg * 0.9) / np.float32(max(0.06 * rg, 1.0))) ** 2)
        _add(buf, ys, xs, val * (amp * I), tint)


def draw_flare(buf, src):
    """Tek ışığın flare'ı: buf (h, w, 3) doğrusal, üzerine eklenir. src: dict(x, y, col, gain, params, seed)."""
    h = buf.shape[0]
    p = src["params"]
    I = float(p.get("intensity", 100.0)) / 100.0 * float(src["gain"])
    if I <= 1e-4:
        return
    x, y = float(src["x"]), float(src["y"])
    col = np.asarray(src["col"], np.float32)
    S_ = max(1.0, float(p.get("size", 8.0)) / 100.0 * h)
    if p.get("glow", True):
        _glow(buf, x, y, S_, col, I)
    if p.get("ring", False):
        _ring(buf, x, y, S_, col, I)
    if p.get("streaks", False):
        _streaks(buf, x, y, S_, col, I, p.get("streak_n", 6), float(p.get("streak_angle", 0.0)),
                 float(p.get("streak_len", 30.0)) / 100.0 * h)
    if p.get("anam", False):
        _anamorphic(buf, x, y, S_, col, I, float(p.get("anam_len", 60.0)) / 100.0 * buf.shape[1])
    if p.get("ghosts", False):
        _ghosts(buf, x, y, S_, col, I, p.get("ghost_n", 6), int(src.get("seed", 0)))


# ---------------------------------------------------------------- bloom
def _box_h(a, r):
    if r < 1:
        return a
    n = a.shape[1]
    c = np.cumsum(np.pad(a, ((0, 0), (r + 1, r), (0, 0)), mode="edge"), axis=1)
    return (c[:, 2 * r + 1:2 * r + 1 + n] - c[:, :n]) / np.float32(2 * r + 1)


def _blur(a, r):
    """Yaklaşık Gauss: yatay + dikey kutu süzgeci, 3 kez."""
    r = int(max(0, round(r / 1.7)))
    for _ in range(3):
        a = _box_h(a, r)
        a = np.swapaxes(_box_h(np.swapaxes(a, 0, 1), r), 0, 1)
    return a


def _resize(a, h, w):
    """Çift doğrusal büyütme (numpy)."""
    sh, sw = a.shape[:2]
    yy = (np.arange(h, dtype=np.float32) + 0.5) * (sh / float(h)) - 0.5
    xx = (np.arange(w, dtype=np.float32) + 0.5) * (sw / float(w)) - 0.5
    y0 = np.clip(np.floor(yy).astype(np.int64), 0, sh - 1)
    x0 = np.clip(np.floor(xx).astype(np.int64), 0, sw - 1)
    y1, x1 = np.minimum(y0 + 1, sh - 1), np.minimum(x0 + 1, sw - 1)
    fy = np.clip(yy - y0, 0.0, 1.0)[:, None, None]
    fx = np.clip(xx - x0, 0.0, 1.0)[None, :, None]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy) + bot * fy


def bloom(lin, strength, threshold, radius_pct):
    """Parlak piksellerin çevreye taşması. lin: (h, w, 3) doğrusal. Döner: eklenecek ışık (aynı boyut)."""
    h, w = lin.shape[:2]
    thr = float(np.clip(threshold, 0.0, 0.99)) ** 2.2                 # eşik sRGB olarak verilir
    L = lin.max(2)
    knee = max(1e-3, 1.0 - thr)
    b = lin * np.clip((L - thr) / knee, 0.0, 1.0)[..., None] ** 2
    if not b.any():
        return None
    rad = max(1.0, float(radius_pct) / 100.0 * h)
    f = int(max(1, min(16, rad // 6)))                                # küçültülmüş görüntüde bulanıklaştır (hız)
    hh, ww = max(1, h // f), max(1, w // f)
    sm = b[:hh * f, :ww * f].reshape(hh, f, ww, f, 3).mean((1, 3)).astype(np.float32)
    rs = rad / f
    acc = _blur(sm, rs * 0.5) * 0.5 + _blur(sm, rs) * 0.3 + _blur(sm, rs * 2.0) * 0.2
    return _resize(acc, h, w) * np.float32(strength)


# ---------------------------------------------------------------- kaynaklar (izdüşüm + gizlenme)
def _transmission(scene, clusters, O, D, tmax):
    """Işınlar boyunca geçirgenlik (0 = tamamen kapalı, 1 = açık). Cam (saydamlık) zayıflatır. O (n,3)|(3,), D (n,3) birim, tmax (n,) | None."""
    from .accel import _closest_general
    n = len(D)
    Tr = np.ones(n)
    if scene is None or clusters is None or scene.count == 0:
        return Tr
    O = np.broadcast_to(np.asarray(O, np.float64), (n, 3)).copy()
    rem = np.full(n, np.inf) if tmax is None else np.asarray(tmax, np.float64).copy()
    alpha = scene.alpha if bool(getattr(S, "GLASS", True)) else np.ones(scene.count)
    eps = scene.diag * 1e-5
    act = np.arange(n)
    for _ in range(8):
        if not len(act):
            break
        tm = float(rem[act].max())
        t_, i_ = _closest_general(scene, clusters, O[act], D[act], None, None if not np.isfinite(tm) else tm)
        t_, i_ = np.asarray(t_), np.asarray(i_)
        hit = (i_ >= 0) & (t_ < rem[act])
        if not hit.any():
            break
        a_ = act[hit]
        Tr[a_] *= 1.0 - np.clip(alpha[i_[hit]], 0.0, 1.0)
        step = t_[hit] + eps
        O[a_] += D[a_] * step[:, None]
        rem[a_] -= step
        act = a_[Tr[a_] > 0.01]
    return Tr


def sources(scene, clusters, view, flares):
    """Flare'ı açık ışıkların ekran konumu ve görünürlüğü.
    view: dict(pos, right, up, fwd, persp, half_x, half_y, w, h, o_ortho) (sahne-içi koordinatlar).
    flares: [dict(name, P (konumlu ışık, sahne içi) ya da dir (ışığa doğru yön), col (doğrusal), power, params,
                  spot=(eksen, cos dış, cos iç) | None, radius, soft (derece))]."""
    pos, right, up, fwd = view["pos"], view["right"], view["up"], view["fwd"]
    persp, hx, hy, w, h = view["persp"], view["half_x"], view["half_y"], view["w"], view["h"]
    out = []
    for fl in flares:
        p = fl["params"]
        P = fl.get("P")
        if P is not None:
            v = P - (pos if persp else view["o_ortho"])
            z = float(v @ fwd)
            if z <= 1e-9 * scene.diag:
                continue                                              # kameranın arkasında
            X, Y = (float(v @ right) / z, float(v @ up) / z) if persp else (float(v @ right), float(v @ up))
        else:
            if not persp:
                continue                                              # ortografik görünümde sonsuzdaki ışığın ekranda yeri yok
            dv = np.asarray(fl["dir"], np.float64)
            z = float(dv @ fwd)
            if z <= 1e-6:
                continue
            X, Y = float(dv @ right) / z, float(dv @ up) / z
        sx = (X / hx + 1.0) * 0.5 * w
        sy = (1.0 - Y / hy) * 0.5 * h
        d_out = max(0.0, -sx, sx - w, -sy, sy - h)
        fade = max(0.0, 1.0 - d_out / (0.35 * h))
        if fade <= 0.0:
            continue
        g = fade                                                      # flare'ın gücü kendi Şiddet ayarından (ışık şiddetine bağlı değil:
                                                                      # iç mekânda düşük şiddetli lambalar flare'ı da söndürüyordu)
        if P is not None and fl.get("spot") is not None:              # spot: kamera koninin dışındaysa ampul görünmez
            ax, co, ci = fl["spot"]
            dc = (pos if persp else P - fwd * 1.0) - P
            dc = dc / max(np.linalg.norm(dc), 1e-12)
            g *= float(_smooth(co, ci, float(np.dot(ax, dc))))
        if g <= 1e-4:
            continue
        vis = 1.0                                                     # her zaman ölçülür: gizlenme sonradan açılırsa yeniden render gerekmesin
        if scene is not None and scene.count:
            vis = _visibility(scene, clusters, view, fl, P, z)
        col = np.maximum(np.asarray(fl["col"], np.float64), 0.0)
        col = col / max(col.max(), 1e-9)
        out.append(dict(name=fl.get("name", ""), x=sx, y=sy, col=col, gain=g, vis=vis, params=dict(p),
                        seed=_seed(fl.get("name", ""))))
    return out


def _visibility(scene, clusters, view, fl, P, z):
    pos, right, up, fwd = view["pos"], view["right"], view["up"], view["fwd"]
    K = 16
    k = np.arange(K, dtype=np.float64)
    rr = np.sqrt((k + 0.5) / K)
    th = k * 2.399963
    if P is not None:
        pix = z * (2.0 * view["half_y"] / view["h"]) if view["persp"] else 2.0 * view["half_y"] / view["h"]
        R = max(float(fl.get("radius", 0.0)), 2.5 * pix)              # en az birkaç piksellik disk: kenardan kayarken yumuşak sönsün
        tg = P + (right[None, :] * (rr * np.cos(th))[:, None] + up[None, :] * (rr * np.sin(th))[:, None]) * R
        if view["persp"]:
            D = tg - pos
            dist = np.linalg.norm(D, axis=1)
            D = D / dist[:, None]
            return float(_transmission(scene, clusters, pos, D, dist * (1.0 - 1e-6) - scene.diag * 1e-5).mean())
        O = tg - fwd[None, :] * (z + 0.05 * scene.diag)
        D = np.tile(fwd, (K, 1))
        return float(_transmission(scene, clusters, O, D, np.full(K, z + 0.05 * scene.diag - scene.diag * 1e-5)).mean())
    dv = np.asarray(fl["dir"], np.float64)
    u = np.cross(dv, up)
    u = u / max(np.linalg.norm(u), 1e-12)
    vv = np.cross(dv, u)
    tn = np.tan(np.radians(max(0.25, min(float(fl.get("soft", 0.5)), 10.0))))
    D = dv[None, :] + (u[None, :] * (rr * np.cos(th))[:, None] + vv[None, :] * (rr * np.sin(th))[:, None]) * tn
    D = D / np.linalg.norm(D, axis=1)[:, None]
    return float(_transmission(scene, clusters, pos, D, None).mean())


# ---------------------------------------------------------------- uygulama
def enabled():
    return bool(getattr(S, "FLARE", True)) or bool(getattr(S, "BLOOM", False))


def compose(base, src):
    """base: (h, w, 3) sRGB [0,1] float; src: flare kaynakları. Döner: sRGB float."""
    flare_on = bool(getattr(S, "FLARE", True)) and any(s_["params"].get("on", True) for s_ in src)
    bloom_on = bool(getattr(S, "BLOOM", False)) and float(getattr(S, "BLOOM_STRENGTH", 0.0)) > 0.0
    if not (flare_on or bloom_on):
        return base
    lin = np.clip(base, 0.0, 1.0).astype(np.float32) ** np.float32(2.2)
    add = np.zeros_like(lin)
    if bloom_on:
        b = bloom(lin, float(S.BLOOM_STRENGTH), float(getattr(S, "BLOOM_THRESHOLD", 0.8)), float(getattr(S, "BLOOM_RADIUS", 4.0)))
        if b is not None:
            add += b
    if flare_on:
        k = float(getattr(S, "FLARE_INTENSITY", 1.0))
        for s_ in src:
            p_ = s_["params"]
            if not p_.get("on", True):
                continue
            s2 = dict(s_)
            s2["gain"] = float(s_["gain"]) * k * (float(s_["vis"]) if p_.get("occlude", True) else 1.0)
            draw_flare(add, s2)
    out = lin + add * (1.0 - 0.6 * np.clip(lin, 0.0, 1.0))           # yarı "ekran" karışımı: karanlıkta tam toplanır, parlak
                                                                      # yüzeyde biraz kısılır (gökyüzü patlamaz, açık duvarda flare kaybolmaz)
    return np.clip(out, 0.0, 1.0) ** np.float32(1.0 / 2.2)


def finish(img, scene=None, clusters=None, view=None, flares=None):
    """render() sonu: efektleri uygular, efektsiz görüntüyü yeniden uygulama için saklar. img: sRGB float (h, w, 3)."""
    global LAST
    src = []
    if flares and view is not None:
        try:
            src = sources(scene, clusters, view, flares)
        except Exception as ex:                                      # flare render'ı asla bozmasın
            print("YBT lens flare: %s: %s" % (type(ex).__name__, ex))
            src = []
    base = np.array(img, dtype=np.float32)
    LAST = dict(base=base, src=src, w=base.shape[1], h=base.shape[0])
    try:
        return compose(base, src)
    except Exception as ex:
        print("YBT post effects: %s: %s" % (type(ex).__name__, ex))
        return img


def reapply(light_params=None, last=None):
    """Son render'a efektleri yeniden uygular (ayar değişince, render almadan). light_params: {ışık adı: flare sözlüğü}
    verilirse kaynakların parametreleri güncellenir (konum ve görünürlük son render'dakidir). last: finish()'in kaydı
    (verilmezse en son render). Döner: uint8 ya da None."""
    last = LAST if last is None else last
    if last is None:
        return None
    src = last["src"]
    src2 = []
    for s_ in src:                                                   # güneşin ayarı panelden; ışıklarınki verildiyse nesnelerden
        if s_["name"] == SUN_NAME:
            p = sun_params()
        elif light_params is not None:
            p = light_params.get(s_["name"], s_["params"])
        else:
            p = s_["params"]
        s2 = dict(s_)
        s2["params"] = dict(p)
        src2.append(s2)
    src = src2
    last["src"] = src
    out = compose(last["base"], src)
    return (np.clip(out, 0, 1) * 255.0 + 0.5).astype(np.uint8)
