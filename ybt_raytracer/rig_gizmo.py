# -*- coding: utf-8 -*-
"""LightWave tarzı rig gizmosu: kamera ve ışıklar için Taşı / Döndür / Hedef tutamakları + nesneyi izleyen düğmeler.

  Taşı   -> yalnız Placement.Base: dünya X/Y/Z okları, XY/YZ/XZ düzlem kareleri, merkezde ekran düzleminde serbest
  Döndür -> yalnız Placement.Rotation (nesnenin yerel eksenleri etrafında halkalar)
  Hedef  -> baktığı noktada bir tutamak: ekran düzleminde sürüklenir, Ctrl basılıyken imlecin altındaki yüzeye
            yapışır (elmas yeşil) ve bırakınca kalıcı kilit kurulur (TargetObject / TargetPoint); nesne yerinde kalır.
Türlere göre: kamera / spot / distant = Taşı + Döndür + Hedef, point = Taşı. Kilitliyken Döndür kapalı.
Direnç: taşıma / döndürme fare hareketinin DRAG_GAIN oranında, Shift = FINE_GAIN. Sol alt köşede değer paneli:
Taşı'da X/Y/Z (mm), Döndür'de LightWave H/P/B (derece); yazıp Enter = uygula.
Başlatma: InitGui tezgâh etkinleşince start(), çıkınca stop(); kilit gözlemcisi (install_locks) hep açık.

Coin'in hazır dragger'ları kullanılmaz: FreeCAD'in seçim sistemiyle birlikte sürüklenince çöküyorlar (Access violation).
Tutamaklar yalnız çizimdir; fare olaylarını görünümün olay geri çağrısı yakalar (Draft araçları gibi). İmleç bir
tutamağa yakınsa olay "işlendi" sayılır ve seçime gitmez. Sürükleme ekran ışınından hesaplanır; nesneyi değiştiren
her şey olay bittikten sonra Qt zamanlayıcısıyla uygulanır.
"""
import math

import FreeCAD
import FreeCADGui as Gui
from pivy import coin

from .paths import scene_unit   # mm; sahne birimi (Seçenekler > Genel), LightWave ızgara aralığı gibi

GIZMO_LEN = 0.45         # ok boyu / halka yarıçapı, sahne birimi cinsinden (1 m birimde 45 cm)
MIN_PX = 40.0            # uzaklaşınca gizmo ekranda bundan küçülmez
PICK_PX = 8.0            # tutamağı yakalama uzaklığı (px)
DRAG_GAIN = 0.5          # direnç: taşıma / döndürme fare hareketinin bu oranında (1 = imleci birebir izler)
FINE_GAIN = 0.15         # Shift basılıyken ince ayar oranı
BTN = 24                 # düğme çapı (px)
PANEL_W = 170            # değer panelinin sabit genişliği (px)
RGB = ((0.94, 0.27, 0.27), (0.13, 0.77, 0.37), (0.23, 0.51, 0.96))
HOT = (1.0, 1.0, 0.6)
TARGET_RGB = (0.02, 0.71, 0.83)
SNAP_RGB = (0.2, 1.0, 0.3)       # Ctrl ile bir yüzey yakalandığında hedef elması
AXES = (FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(0, 0, 1))
RING_N = 64

MODES = {"camera": ("move", "rotate", "target"), "point": ("move",),
         "spot": ("move", "rotate", "target"), "distant": ("move", "rotate", "target")}


def kind_of(o):
    if o is None:
        return None
    k = getattr(o, "YBTLightType", None)
    if k in MODES:
        return k
    try:
        from ybt_raytracer import cameraobj
        if cameraobj.is_camera(o):
            return "camera"
    except Exception:
        pass
    return None


def _view():
    return Gui.ActiveDocument.ActiveView


def _vv(v):
    W, H = v.getSize()
    return v.getCameraNode().getViewVolume(float(W) / H), W, H


def _gain():
    from PySide6 import QtWidgets, QtCore
    fine = QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier
    return FINE_GAIN if fine else DRAG_GAIN


def _later(fn):
    from PySide6 import QtCore
    QtCore.QTimer.singleShot(0, fn)


# ------------------------------------------------------------------ tutamak çizimi
class _Handle(object):
    """Tek bir tutamak: renk/kalınlık düğümleri (vurgu için) + çizim."""

    def __init__(self, rgb, width):
        self.rgb = rgb
        self.width = width
        self.sep = coin.SoSeparator()
        self.col = coin.SoBaseColor()
        self.col.rgb = rgb
        self.style = coin.SoDrawStyle()
        self.style.lineWidth = width
        self.sep.addChild(self.col)
        self.sep.addChild(self.style)

    def hot(self, on):
        self.col.rgb = HOT if on else self.rgb
        self.style.lineWidth = self.width + (1.5 if on else 0.0)

    def lines(self, pts, idx):
        co = coin.SoCoordinate3()
        co.point.setValues(0, len(pts), pts)
        ls = coin.SoIndexedLineSet()
        ls.coordIndex.setValues(0, len(idx), idx)
        self.sep.addChild(co)
        self.sep.addChild(ls)
        return co


class _FillHandle(_Handle):
    """Dolgulu tutamak (düzlem karesi, merkez küp): çizgi + yarı saydam dolgu; soluk = tutulamaz."""

    def __init__(self, rgb, width, alpha):
        _Handle.__init__(self, rgb, width)
        self.alpha = alpha
        self.mat = coin.SoMaterial()
        self.mat.diffuseColor = rgb
        self.mat.emissiveColor = rgb
        self.mat.transparency = alpha
        self.fill = coin.SoSeparator()
        self.fill.addChild(self.mat)
        self.sep.addChild(self.fill)
        self._dim = False

    def hot(self, on):
        _Handle.hot(self, on and not self._dim)
        c = HOT if (on and not self._dim) else self.rgb
        self.mat.diffuseColor = c
        self.mat.emissiveColor = c

    def dim(self, off):
        """Düzlem ekrana yandan bakıyor: soluklaştır (kare tek çizgiye iner, sürüklemek nesneyi fırlatır)."""
        if off == self._dim:
            return
        self._dim = off
        self.mat.transparency = 0.93 if off else self.alpha
        self.col.rgb = tuple(0.35 + 0.3 * c for c in self.rgb) if off else self.rgb


PLANE_A, PLANE_B = 0.22, 0.42          # düzlem karesinin iki eksendeki başlangıç / bitişi (ok boyunun oranı)
FREE_R = 0.07                          # merkez küpün yarı boyu


def _plane_corners(i):
    """i eksenine dik düzlemdeki karenin köşeleri (birim gizmo ölçüsünde)."""
    a, b = AXES[(i + 1) % 3], AXES[(i + 2) % 3]
    return [a * u + b * w for u, w in ((PLANE_A, PLANE_A), (PLANE_B, PLANE_A), (PLANE_B, PLANE_B), (PLANE_A, PLANE_B))]


def _plane_square(i):
    h = _FillHandle(RGB[i], 1.5, 0.6)
    pts = [(p.x, p.y, p.z) for p in _plane_corners(i)]
    h.lines(pts, [0, 1, 2, 3, 0, -1])
    co = coin.SoCoordinate3()
    co.point.setValues(0, 4, pts)
    fs = coin.SoFaceSet()
    fs.numVertices.setValue(4)
    sh = coin.SoShapeHints()                               # iki yüzü de görünsün
    sh.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
    for n in (sh, co, fs):
        h.fill.addChild(n)
    return h.sep, h


def _free_cube():
    h = _FillHandle((0.92, 0.92, 0.95), 1.5, 0.15)
    r = FREE_R
    c = [(x, y, z) for x in (-r, r) for y in (-r, r) for z in (-r, r)]
    h.lines(c, [0, 1, 3, 2, 0, -1, 4, 5, 7, 6, 4, -1, 0, 4, -1, 1, 5, -1, 2, 6, -1, 3, 7, -1])
    cu = coin.SoCube()
    cu.width = cu.height = cu.depth = 2 * r
    h.fill.addChild(cu)
    return h.sep, h


def _arrow(i):
    """Birim ok (+X yönünde, boy 1); i: eksen (döndürme ile hizalanır)."""
    h = _Handle(RGB[i], 3.0)
    s = coin.SoSeparator()
    r = coin.SoRotation()
    r.rotation.setValue(coin.SbRotation(coin.SbVec3f(1, 0, 0), coin.SbVec3f(*AXES[i])))
    s.addChild(r)
    s.addChild(h.sep)
    h.lines([(0, 0, 0), (1, 0, 0)], [0, 1, -1])
    t = coin.SoTransform()
    t.translation = (1.05, 0, 0)
    t.rotation.setValue(coin.SbVec3f(0, 0, 1), -math.pi / 2)
    cn = coin.SoCone()
    cn.bottomRadius = 0.06
    cn.height = 0.18
    h.sep.addChild(t)
    h.sep.addChild(cn)
    return s, h


def _ring_pts(i):
    """Yerel i ekseni etrafındaki birim çemberin noktaları (eksene dik düzlemde)."""
    a = AXES[(i + 1) % 3]
    b = AXES[(i + 2) % 3]
    return [a * math.cos(2 * math.pi * k / RING_N) + b * math.sin(2 * math.pi * k / RING_N) for k in range(RING_N)]


def _ring(i):
    h = _Handle(RGB[i], 3.5)
    h.lines([(p.x, p.y, p.z) for p in _ring_pts(i)], list(range(RING_N)) + [0, -1])
    return h.sep, h


def _target_mark():
    """Hedef tutamağı: ekrana bakan küçük elmas + artı (yerel XY = ekran düzlemi)."""
    h = _Handle(TARGET_RGB, 2.5)
    r = 1.0
    pts = [(r, 0, 0), (0, r, 0), (-r, 0, 0), (0, -r, 0), (1.8 * r, 0, 0), (-1.8 * r, 0, 0), (0, 1.8 * r, 0), (0, -1.8 * r, 0)]
    h.lines(pts, [0, 1, 2, 3, 0, -1, 4, 5, -1, 6, 7, -1])
    return h.sep, h


# ------------------------------------------------------------------ ekran ışını yardımcıları
def _ray(v, pos):
    """İmleç pikselinden (sol alt kökenli, aygıt pikseli) dünya ışını: (başlangıç, birim yön)."""
    p = v.getPoint((pos[0], pos[1]))
    cn = v.getCameraNode()
    if cn.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
        o = FreeCAD.Vector(*cn.position.getValue().getValue())
        d = p - o
    else:
        d = FreeCAD.Vector(*v.getViewDirection())
        o = p - d * 1e7
    d.normalize()
    return o, d


def _to_screen(vv, W, H, p):
    sx, sy, _ = vv.projectToScreen(coin.SbVec3f(p.x, p.y, p.z)).getValue()
    return sx * W, sy * H


def _seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


PLANE_EDGE = 0.2         # düzlem normali ile bakış arasındaki |cos| bundan küçükse kare yandan görünüyor sayılır


def _view_dir(v, p):
    """p noktasına bakış yönü (birim): perspektifte gözden noktaya, ortografikte görünüm yönü."""
    cn = v.getCameraNode()
    if cn.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
        d = p - FreeCAD.Vector(*cn.position.getValue().getValue())
    else:
        d = FreeCAD.Vector(*v.getViewDirection())
    if d.Length > 1e-12:
        d.normalize()
    return d


def _in_poly(x, y, poly):
    """Ekran noktası çokgenin içinde mi (ışın sayma)."""
    inside = False
    n = len(poly)
    for k in range(n):
        (x1, y1), (x2, y2) = poly[k], poly[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _line_param(o, d, p0, a):
    """Işın (o, d) ile doğru (p0, a) arasındaki en yakın noktanın doğru üzerindeki parametresi."""
    w = o - p0
    b = d.dot(a)
    den = 1.0 - b * b
    if abs(den) < 1e-6:
        return None
    return (w.dot(a) - b * w.dot(d)) / den


def _plane_hit(o, d, c, n):
    den = d.dot(n)
    if abs(den) < 1e-6:
        return None
    t = (c - o).dot(n) / den
    return o + d * t


# ------------------------------------------------------------------ gizmo
class RigGizmo(object):
    def __init__(self, obj):
        self.obj = obj
        self.kind = kind_of(obj)
        self.mode = None
        self.size = 1.0
        self._pl0 = None          # sürükleme başındaki yerleşim (sürükleme yoksa None)
        self._drag = None         # (mod, eksen no)
        self._hover = None
        self._tdist = None
        self._near = None         # görünümün gözü gizmonun dibinde mi (kameradan bakış)
        self.handles = {}        # (mod, i) -> _Handle
        self.root = coin.SoAnnotation()                    # tüm tutamaklar en son, derinlik testi olmadan: nesnenin arkasında / içinde kalsa da görünür
        self.switch = coin.SoSwitch()
        self.root.addChild(self.switch)
        self._build()
        obj.ViewObject.RootNode.addChild(self.root)
        v = _view()
        self._cb_btn = self._on_button                    # aynı nesneyle kaldırılabilsin
        self._cb_mov = self._on_motion
        self._cb_key = self._on_key
        v.addEventCallbackPivy(coin.SoKeyboardEvent.getClassTypeId(), self._cb_key)
        v.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self._cb_btn)
        v.addEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), self._cb_mov)
        self.set_mode(None)

    def _build(self):
        self.align = coin.SoRotation()                     # Taşı: dünya eksenlerine hizala
        self.scale = coin.SoScale()
        g = coin.SoSeparator()
        g.addChild(self.align)
        g.addChild(self.scale)
        for i in range(3):
            s, h = _arrow(i)
            g.addChild(s)
            self.handles[("move", i)] = h
        for i in range(3):                                 # düzlem kareleri: i eksenine dik düzlemde taşı
            s, h = _plane_square(i)
            g.addChild(s)
            self.handles[("plane", i)] = h
        s, h = _free_cube()                                # merkez: ekran düzleminde serbest taşı
        g.addChild(s)
        self.handles[("free", 0)] = h
        self.switch.addChild(g)

        g = coin.SoSeparator()
        g.addChild(self.scale)
        for i in range(3):
            s, h = _ring(i)
            g.addChild(s)
            self.handles[("rotate", i)] = h
        self.switch.addChild(g)

        g = coin.SoSeparator()                             # hedef: kesikli bakış çizgisi + ekrana dönük tutamak
        self.tline = coin.SoCoordinate3()
        ds = coin.SoDrawStyle()
        ds.lineWidth = 1.5
        ds.linePattern = 0xF0F0
        c = coin.SoBaseColor()
        c.rgb = TARGET_RGB
        for n in (c, ds, self.tline, coin.SoLineSet()):
            g.addChild(n)
        self.tpos = coin.SoTranslation()
        self.tface = coin.SoRotation()
        self.tscale = coin.SoScale()
        h = coin.SoAnnotation()                            # en son ve derinlik testi olmadan: nesnenin içinde kalsa da görünür
        for n in (self.tpos, self.tface, self.tscale):
            h.addChild(n)
        s, hd = _target_mark()
        h.addChild(s)
        g.addChild(h)
        self.handles[("target", 0)] = hd
        self.switch.addChild(g)

    # .......................................... durum
    def target_distance(self):
        if self._tdist:
            return self._tdist
        P = lock_point(self.obj)
        if P is not None:                                  # kilitliyken tutamak hedefin üstünde
            return max(1.0, (P - self.obj.Placement.Base).Length)
        if self.kind == "camera":
            from . import cameraobj
            L = cameraobj.symbol_size(self.obj)            # 0 = otomatik (sahne birimi)
        else:
            from . import lightobj
            L = lightobj.symbol_size(self.obj)             # ışıkta da 0 = otomatik (sahne birimi)
        return L * 2.0 if self.kind == "spot" else L     # spot: hedef koninin iki katı uzakta

    def set_mode(self, mode):
        if mode not in MODES.get(self.kind, ()) or (mode == "rotate" and is_locked(self.obj)):
            mode = None                                    # kilitliyken yön hedeften gelir: Döndür kapalı
        self.mode = mode
        self._near = None                                  # refresh görünürlüğü yeniden kursun
        self.switch.whichChild = {"move": 0, "rotate": 1, "target": 2}.get(mode, coin.SO_SWITCH_NONE)
        self.refresh()

    def refresh(self):
        """Her karede (sürükleme dışında): boyut (dünya ölçüsü, en az MIN_PX), hizalama, hedef tutamağı ekrana dönük."""
        if self._pl0 is not None:
            return
        v = _view()
        pl = self.obj.Placement
        vv, W, H = _vv(v)
        p = pl.Base
        smin = vv.getWorldToScreenScale(coin.SbVec3f(p.x, p.y, p.z), MIN_PX / float(H))
        self.size = max(GIZMO_LEN * scene_unit() / 1.18, smin)
        self.scale.scaleFactor.setValue(self.size, self.size, self.size)
        self.align.rotation.setValue(*pl.Rotation.inverted().Q)
        # görünümün gözü gizmonun dibindeyse (kameradan bakış) gizle: merkez küp / kareler ekranı kaplıyordu
        cn = v.getCameraNode()
        near = False
        if cn.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            near = (p - FreeCAD.Vector(*cn.position.getValue().getValue())).Length < self.size * 3.0
        if near != self._near:
            self._near = near
            self.switch.whichChild = coin.SO_SWITCH_NONE if near else \
                {"move": 0, "rotate": 1, "target": 2}.get(self.mode, coin.SO_SWITCH_NONE)
        vd = _view_dir(v, p)
        for i in range(3):                                 # yandan görünen düzlem karesi soluk ve tutulamaz
            self.handles[("plane", i)].dim(abs(vd.dot(AXES[i])) < PLANE_EDGE)
        D = self.target_distance()
        self.tline.point.setValues(0, 2, [(0, 0, 0), (0, 0, -D)])
        self.tpos.translation = (0, 0, -D)
        q = v.getCameraNode().orientation.getValue().getValue()
        self.tface.rotation.setValue(*pl.Rotation.inverted().multiply(FreeCAD.Rotation(*q)).Q)
        tp = pl.multVec(FreeCAD.Vector(0, 0, -D))
        ts = vv.getWorldToScreenScale(coin.SbVec3f(tp.x, tp.y, tp.z), 9.0 / float(H))
        self.tscale.scaleFactor.setValue(ts, ts, ts)

    # .......................................... tutamak bulma (ekran pikseli)
    def _pick(self, pos):
        """İmlecin PICK_PX yakınındaki tutamak: (mod, i) ya da None."""
        if self.mode is None or self._near:
            return None
        v = _view()
        vv, W, H = _vv(v)
        pl = self.obj.Placement
        x, y = pos
        best, bd = None, PICK_PX
        if self.mode == "move":
            c = _to_screen(vv, W, H, pl.Base)
            if math.hypot(x - c[0], y - c[1]) < 10.0:      # öncelik: merkez küp, sonra kareler, sonra oklar
                return ("free", 0)
            for i in range(3):
                if self.handles[("plane", i)]._dim:
                    continue
                poly = [_to_screen(vv, W, H, pl.Base + q * self.size) for q in _plane_corners(i)]
                if _in_poly(x, y, poly):
                    return ("plane", i)
            for i in range(3):
                e = _to_screen(vv, W, H, pl.Base + AXES[i] * (self.size * 1.2))
                dd = _seg_dist(x, y, c, e)
                if dd < bd:
                    best, bd = ("move", i), dd
        elif self.mode == "rotate":
            for i in range(3):
                pts = [_to_screen(vv, W, H, pl.Base + pl.Rotation.multVec(q) * self.size) for q in _ring_pts(i)[::2]]
                for k in range(len(pts)):
                    dd = _seg_dist(x, y, pts[k], pts[(k + 1) % len(pts)])
                    if dd < bd:
                        best, bd = ("rotate", i), dd
        else:
            t = _to_screen(vv, W, H, pl.multVec(FreeCAD.Vector(0, 0, -self.target_distance())))
            dd = math.hypot(x - t[0], y - t[1])
            if dd < max(bd, 12.0):
                best = ("target", 0)
        return best

    def _set_hover(self, key):
        if key == self._hover:
            return
        old, self._hover = self._hover, key

        def apply():
            if old in self.handles:
                self.handles[old].hot(False)
            if key in self.handles:
                self.handles[key].hot(True)
        _later(apply)

    # .......................................... fare olayları (Coin olayı içinde: sahneye dokunma, yalnız not al)
    def _on_button(self, cb):
        ev = cb.getEvent()
        if ev.getButton() != coin.SoMouseButtonEvent.BUTTON1:
            return
        pos = ev.getPosition().getValue()
        if ev.getState() == coin.SoButtonEvent.DOWN:
            key = self._pick(pos)
            if key is None:
                return
            cb.setHandled()                                # seçime ve gezinmeye gitmesin
            self._begin(key, pos)
        elif self._drag is not None:
            cb.setHandled()
            self._last_pos = pos
            _later(self._end)

    def _on_key(self, cb):
        """Hedef sürüklenirken Ctrl'ye basılınca imleç kıpırdamadan yakalamayı dene."""
        if self._drag is None or self._drag[0] != "target":
            return
        ev = cb.getEvent()
        if ev.getState() == coin.SoButtonEvent.DOWN and ev.getKey() in (coin.SoKeyboardEvent.LEFT_CONTROL,
                                                                        coin.SoKeyboardEvent.RIGHT_CONTROL):
            self._applied_pos = None
            if not getattr(self, "_apply_pending", False):
                self._apply_pending = True
                _later(self._apply)

    def _on_motion(self, cb):
        pos = cb.getEvent().getPosition().getValue()
        if self._drag is not None:
            from PySide6 import QtWidgets, QtCore
            if not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
                _later(self._end)                          # sol tuş görünüm dışında bırakıldı: sürükleme biter
                return
            cb.setHandled()
            self._last_pos = pos
            if not getattr(self, "_apply_pending", False):
                self._apply_pending = True
                _later(self._apply)
            return
        self._set_hover(self._pick(pos))

    # .......................................... sürükleme
    def _begin(self, key, pos):
        self._drag = key
        self._pl0 = FreeCAD.Placement(self.obj.Placement)
        self._d0 = self.target_distance()
        if key[0] == "target":
            _SUSPENDED.add(self.obj.Name)
        self._last_pos = pos
        self._acc = 0.0                                    # toplanan taşıma (mm) / dönüş (radyan)
        self._vec = FreeCAD.Vector()                       # toplanan düzlem / serbest taşıma
        self._free_n = _view_dir(_view(), self._pl0.Base)  # serbest taşıma düzleminin normali (sürükleme boyunca sabit)
        self._ref = self._measure(pos)                   # başlangıç ölçüsü (parametre / açı / nokta)

    def _measure(self, pos):
        """Sürüklenen tutamak için imlecin ölçüsü: ok -> eksen parametresi, halka -> düzlemdeki nokta, hedef -> nokta."""
        v = _view()
        o, d = _ray(v, pos)
        pl0 = self._pl0
        mode, i = self._drag
        if mode == "move":
            return _line_param(o, d, pl0.Base, AXES[i])
        if mode == "plane":
            return _plane_hit(o, d, pl0.Base, AXES[i])
        if mode == "free":
            return _plane_hit(o, d, pl0.Base, self._free_n)
        if mode == "rotate":
            n = pl0.Rotation.multVec(AXES[i])
            hit = _plane_hit(o, d, pl0.Base, n)
            if hit is None or abs(d.dot(n)) < 0.08:        # halka yandan görünüyor: ekranda merkez etrafındaki açı
                vv, W, H = _vv(v)
                c = _to_screen(vv, W, H, pl0.Base)
                return ("screen", math.atan2(pos[1] - c[1], pos[0] - c[0]), d.dot(n))
            return ("plane", hit - pl0.Base)
        from PySide6 import QtWidgets, QtCore
        self._snapped = None
        if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier:
            # imlecin altındaki tüm isabetler; kamera / ışık simgeleri (prizma, koni, gizmo çizgileri) atlanır, ilk gerçek yüzey
            hits = v.getObjectsInfo((int(pos[0]), int(pos[1]))) or []   # yalnız demet kabul ediyor (Coin listesi değil)
            doc = self.obj.Document
            for info in hits:
                hit_obj = doc.getObject(info.get("Object", ""))
                if hit_obj is not None and kind_of(hit_obj) is None:
                    pt = FreeCAD.Vector(info["x"], info["y"], info["z"])
                    self._snapped = (hit_obj, pt)          # bırakınca kilit buna kurulur
                    return pt
        t0 =pl0.multVec(FreeCAD.Vector(0, 0, -self._d0))
        hit = _plane_hit(o, d, t0, d)                      # hedeften geçen, bakışa dik düzlem
        return hit if hit is not None else t0

    def _apply(self):
        self._apply_pending = False
        if self._drag is None or self._pl0 is None:
            return
        pl0 = self._pl0
        mode, i = self._drag
        self._applied_pos = list(self._last_pos)
        cur = self._measure(self._last_pos)
        # Taşı / Döndür: her adımın farkı o anki oranla (direnç; Shift = ince) çarpılıp toplanır. Böylece Shift sürükleme
        # ortasında basılınca zıplama olmaz ve dönüş ±180° ile sınırlı kalmaz.
        if mode == "move":
            if cur is None or self._ref is None:
                return
            self._acc += (cur - self._ref) * _gain()
            self._ref = cur
            pl = FreeCAD.Placement(pl0.Base + AXES[i] * self._acc, pl0.Rotation)
        elif mode in ("plane", "free"):
            if cur is None or self._ref is None:
                return
            g = 1.0 if mode == "free" else _gain()         # serbest taşıma imleci birebir izler
            self._vec += (cur - self._ref) * g
            self._ref = cur
            pl = FreeCAD.Placement(pl0.Base + self._vec, pl0.Rotation)
        elif mode == "rotate":
            n = pl0.Rotation.multVec(AXES[i])
            r = self._ref
            if r[0] != cur[0]:                             # ölçü türü değişti (halka yandan görünür oldu): yeni referans
                self._ref = cur
                return
            if r[0] == "plane":
                a, b = r[1], cur[1]
                ang = math.atan2(n.dot(a.cross(b)), a.dot(b))
            else:
                ang = (cur[1] - r[1] + math.pi) % (2 * math.pi) - math.pi
                if r[2] > 0:
                    ang = -ang
            self._acc += ang * _gain()
            self._ref = cur
            pl = FreeCAD.Placement(pl0.Base, FreeCAD.Rotation(n, math.degrees(self._acc)).multiply(pl0.Rotation))
        else:
            tgt = cur
            dirv = tgt - pl0.Base
            if dirv.Length < 1e-6:
                return
            old = pl0.Rotation.multVec(FreeCAD.Vector(0, 0, -1))
            rot = FreeCAD.Rotation(old, dirv).multiply(pl0.Rotation)      # en kısa dönüş: gereksiz yuvarlanma olmasın
            self._tdist = dirv.Length
            pl = FreeCAD.Placement(pl0.Base, rot)
            D = self._tdist
            self.tline.point.setValues(0, 2, [(0, 0, 0), (0, 0, -D)])
            self.tpos.translation = (0, 0, -D)
            h = self.handles[("target", 0)]                # yüzey yakalandı: elmas yeşil, yakalanmadı: sürükleme rengi
            h.hot(True)
            if self._snapped:
                h.col.rgb = SNAP_RGB
        if not self._txn:
            self.obj.Document.openTransaction("Rig gizmo")
            self._txn = True
        self.obj.Placement = pl
        if mode in ("move", "plane", "free"):              # kilitliyken nesne taşınırken döner: oklar dünya eksenlerinde kalsın
            self.align.rotation.setValue(*self.obj.Placement.Rotation.inverted().Q)

    _txn = False

    def _end(self):
        # İmleç son uygulamadan beri oynamadıysa yeniden ölçme: Ctrl fare tuşundan önce bırakılınca yakalama kaybolmasın.
        if list(self._last_pos) != getattr(self, "_applied_pos", None):
            self._apply()
        _SUSPENDED.discard(self.obj.Name)
        if self._drag is not None and self._drag[0] == "target" and self._txn:
            snap = getattr(self, "_snapped", None)         # yeşilken bırakıldı: kalıcı kilit; değilse kilit kalkar
            if snap:
                set_lock(self.obj, snap[0], snap[1])
            else:
                clear_lock(self.obj)
            self._tdist = None
        if self._txn:
            self.obj.Document.commitTransaction()
            self._txn = False
        if self._drag is not None:
            self.handles[self._drag].hot(self._drag == self._hover)
        self._drag = None
        self._pl0 = None
        self.refresh()

    def extent_points(self):
        """Açık tutamakların dünya noktaları (düğmeler bunlarla çakışmasın diye örneklenir)."""
        pl = self.obj.Placement
        s = self.size
        pts = []
        if self.mode == "move":
            for i in range(3):
                pts += [pl.Base + AXES[i] * (s * 1.25 * k / 6.0) for k in range(1, 7)]
                pts += [pl.Base + q * s for q in _plane_corners(i)]
        elif self.mode == "rotate":
            for i in range(3):
                pts += [pl.Base + pl.Rotation.multVec(q) * s for q in _ring_pts(i)[::4]]
        elif self.mode == "target":
            pts.append(pl.multVec(FreeCAD.Vector(0, 0, -self.target_distance())))
        return pts

    def remove(self):
        v = _view()
        try:
            v.removeEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self._cb_btn)
            v.removeEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), self._cb_mov)
            v.removeEventCallbackPivy(coin.SoKeyboardEvent.getClassTypeId(), self._cb_key)
        except Exception:
            pass
        try:
            self.obj.ViewObject.RootNode.removeChild(self.root)
        except Exception:
            pass


# ------------------------------------------------------------------ düğmeler
_SVG = {
    "move": '<path d="M13 6h3l-4-4-4 4h3v5H6v-3l-4 4 4 4v-3h5v5H8l4 4 4-4h-3v-5h5v3l4-4-4-4v3h-5V6z"/>',
    "rotate": '<path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.430 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/>',
    "target": '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm8.94 3A8.99 8.99 0 0 0 13 3.06V1h-2v2.06A8.99 8.99 0 0 0 3.06 11H1v2h2.06A8.99 8.99 0 0 0 11 20.94V23h2v-2.06A8.99 8.99 0 0 0 20.94 13H23v-2h-2.06zM12 19a7 7 0 1 1 0-14 7 7 0 0 1 0 14z"/>',
    "lock": '<path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/>',
    None: '<path d="M18.3 5.71 12 12.01 5.7 5.7 4.29 7.11 10.59 13.4 4.29 19.7 5.7 21.11 12 14.81 18.3 21.11 19.71 19.7 13.41 13.4 19.71 7.11z" transform="translate(0,-1.4)"/>',
}
_STYLE = {"move": ("#ef4444", "#ffffff"), "rotate": ("#eab308", "#0c0d12"),
          "target": ("#06b6d4", "#0c0d12"), "lock": ("#0e7490", "#ffffff"), None: ("#475569", "#ffffff")}


def _icon(key, col):
    from PySide6 import QtCore, QtGui, QtSvg
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%s">%s</svg>' % (col, _SVG[key])
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    pm = QtGui.QPixmap(48, 48)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    r.render(p)
    p.end()
    return QtGui.QIcon(pm)


# LightWave H/P/B (Heading, Pitch, Bank) <-> FreeCAD dönüşü.
# Kamera / ışık yerel -Z'ye bakar, yerel +Y yukarıdır. Sıfır duruş: dünya +Y'ye bakar, yukarısı dünya +Z (FreeCAD "ön").
# LightWave sırası: önce H (dünya Z etrafında, pozitif = sağa), sonra P (sağ eksen etrafında, pozitif = burun aşağı),
# sonra B (bakış ekseni etrafında, pozitif = sağ taraf aşağı).  R = Rz(-H) * Rx(-P) * Ry(B) * B0
_B0 = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)       # yerel -Z -> dünya +Y, yerel +Y -> dünya +Z


def hpb_to_rotation(h, p, b):
    return (FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), -h)
            .multiply(FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -p))
            .multiply(FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), b))
            .multiply(_B0))


def rotation_to_hpb(rot):
    m = rot.multiply(_B0.inverted())
    f = m.multVec(FreeCAD.Vector(0, 1, 0))                  # bakış yönü
    h = math.degrees(math.atan2(f.x, f.y))
    p = -math.degrees(math.asin(max(-1.0, min(1.0, f.z))))
    m0 = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), -h).multiply(FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -p))
    r = m0.inverted().multiply(m).multVec(FreeCAD.Vector(1, 0, 0))
    b = math.degrees(math.atan2(-r.z, r.x))
    return h, p, b


# ------------------------------------------------------------------ kalıcı hedef kilidi
# Kilitli nesnede iki özellik: TargetObject (bağ) ve TargetPoint (yakalanan nokta, hedef nesnenin yerel koordinatında).
# Hedef ya da kilitli nesne hareket edince belge gözlemcisi nesneyi hedefe yeniden çevirir (H ve P hedeften, B korunur:
# kamerada ufuk düz kalır). Placement hep güncel olduğundan render tarafı değişmez.
_SUSPENDED = set()       # hedefi elle sürüklenen nesneler: gözlemci onları eski hedefe geri çevirmesin


def _target(o):
    """Kilidin hedef nesnesi; yalnız bizim kamera / ışıklarımızda (başka tezgâhların nesnelerinde de "TargetObject"
    adlı özellik olabilir: onlar kilit sayılmaz)."""
    try:
        if "TargetPoint" not in o.PropertiesList or kind_of(o) is None:
            return None
    except Exception:
        return None
    return getattr(o, "TargetObject", None)


def is_locked(o):
    return _target(o) is not None


def lock_point(o):
    """Kilitli hedefin dünya konumu (kilit yoksa None)."""
    t = _target(o)
    if t is None:
        return None
    try:
        g = t.getGlobalPlacement()
    except Exception:
        g = t.Placement
    return g.multVec(o.TargetPoint)


def aim(o):
    """Kilitli nesneyi hedefine çevirir; değişmediyse dokunmaz (gözlemci döngüsü olmasın)."""
    P = lock_point(o)
    if P is None:
        return False
    d = P - o.Placement.Base
    if d.Length > 1e-6:
        d.normalize()
        h = math.degrees(math.atan2(d.x, d.y))
        p = -math.degrees(math.asin(max(-1.0, min(1.0, d.z))))
        b = rotation_to_hpb(o.Placement.Rotation)[2]
        rot = hpb_to_rotation(h, p, b)
        if not rot.isSame(o.Placement.Rotation, 1e-9):
            o.Placement = FreeCAD.Placement(o.Placement.Base, rot)
    _lock_line(o, P)
    return True


def set_lock(o, target, world_pt):
    if "TargetObject" not in o.PropertiesList:
        o.addProperty("App::PropertyLinkGlobal", "TargetObject", "Target", "Kilitli hedef: nesne hareket edince buna bakmaya devam eder")
    if "TargetPoint" not in o.PropertiesList:
        o.addProperty("App::PropertyVector", "TargetPoint", "Target", "Hedef noktası, hedef nesnenin yerel koordinatında")
    try:
        g = target.getGlobalPlacement()
    except Exception:
        g = target.Placement
    o.TargetObject = target
    o.TargetPoint = g.inverse().multVec(world_pt)
    aim(o)


def clear_lock(o):
    if is_locked(o):
        o.TargetObject = None
    _lock_line(o, None)


def _lock_line(o, P):
    """Kilitliyken (gizmo kapalı olsa da) nesneden hedefe kesikli çizgi + hedefte küçük işaret; P None = kaldır."""
    vp = getattr(getattr(o, "ViewObject", None), "Proxy", None)
    if vp is None:
        return
    node = getattr(vp, "_rig_lock", None)
    if P is None:
        if node is not None:
            try:
                o.ViewObject.RootNode.removeChild(node[0])
            except Exception:
                pass
            vp._rig_lock = None
        return
    if node is None:
        sep = coin.SoAnnotation()                          # kilit çizgisi de simgeler gibi hep görünür
        c = coin.SoBaseColor()
        c.rgb = TARGET_RGB
        ds = coin.SoDrawStyle()
        ds.lineWidth = 1.5
        ds.linePattern = 0xF0F0
        ds.pointSize = 7
        co = coin.SoCoordinate3()
        for n in (c, ds, co, coin.SoLineSet()):
            sep.addChild(n)
        mk = coin.SoAnnotation()                           # hedef işareti hep üstte
        mco = coin.SoCoordinate3()
        mset = coin.SoMarkerSet()
        mset.markerIndex = coin.SoMarkerSet.DIAMOND_FILLED_9_9
        mk.addChild(mco)
        mk.addChild(mset)
        sep.addChild(mk)
        o.ViewObject.RootNode.addChild(sep)
        node = vp._rig_lock = (sep, co, mco)
    q = o.Placement.inverse().multVec(P)                   # RootNode nesnenin yerel çerçevesinde
    node[1].point.setValues(0, 2, [(0, 0, 0), (q.x, q.y, q.z)])
    node[2].point.setValue(q.x, q.y, q.z)


class LockObserver(object):
    """Belge gözlemcisi: hedef ya da kilitli nesne (veya kapsayıcısı) değişince yeniden çevir; hedef silinince kilidi kaldır."""

    def __init__(self):
        self._busy = False

    def slotChangedObject(self, obj, prop):
        if self._busy or prop not in ("Placement", "TargetObject", "TargetPoint"):
            return
        self._busy = True
        try:
            for o in obj.Document.Objects:
                t = _target(o)                             # yalnız bizim kamera / ışıklarımız
                if t is None:
                    if o is obj and prop == "TargetObject" and kind_of(o) is not None:
                        _lock_line(o, None)
                    continue
                try:
                    related = o is obj or t is obj or obj in t.InListRecursive
                except Exception:
                    related = o is obj or t is obj
                if related and o.Name not in _SUSPENDED:
                    aim(o)
        except Exception as e:
            FreeCAD.Console.PrintError("rig lock: %s\n" % e)
        finally:
            self._busy = False

    def slotFinishRestoreDocument(self, doc):
        """Kayıtlı belge açılınca kilit çizgileri (görünüm vekilleri hazır olunca)."""
        try:
            from PySide6 import QtCore
            QtCore.QTimer.singleShot(0, lambda d=doc: refresh_locks(d))
        except Exception:
            pass

    def slotDeletedObject(self, obj):
        try:
            for o in obj.Document.Objects:
                if o is not obj and _target(o) is obj:   # başka tezgâhın "TargetObject"ine dokunma
                    clear_lock(o)
        except Exception:
            pass


def refresh_locks(doc):
    for o in doc.Objects:
        if is_locked(o):
            aim(o)


_AXIS_CSS = ("#ef4444", "#22c55e", "#3b82f6")
_PANEL_CSS = """
QFrame#rigPanel { background: rgba(18,18,26,225); border: 2px solid #f59e0b; border-radius: 6px; }
QLabel { background: transparent; border: none; }
QLabel#rigTitle { color: #fbbf24; font-weight: bold; }
QDoubleSpinBox { background: transparent; color: #facc15; border: none; border-bottom: 1px solid rgba(250,204,21,90);
                 padding: 0 2px; min-width: 0; selection-background-color: #f59e0b; selection-color: #0c0d12; }
QDoubleSpinBox:focus { border-bottom: 1px solid #facc15; background: rgba(250,204,21,25); }
"""


def _make_overlay():
    from PySide6 import QtWidgets, QtCore

    class ValuePanel(QtWidgets.QFrame):
        """Sol alt köşedeki değer paneli: Taşı modunda X/Y/Z (mm), Döndür modunda LightWave H/P/B (derece).
        Sürüklerken canlı güncellenir; bir kutuya yazıp Enter = uygula (tek geri alma adımı)."""

        def __init__(self, parent):
            super().__init__(parent)
            self.setObjectName("rigPanel")
            self.setStyleSheet(_PANEL_CSS)
            self.gz = None
            self.mode = None
            lay = QtWidgets.QGridLayout(self)
            lay.setContentsMargins(10, 6, 10, 8)
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(3)
            self.title = QtWidgets.QLabel()
            self.title.setObjectName("rigTitle")
            lay.addWidget(self.title, 0, 0, 1, 2)
            self.names, self.boxes = [], []
            for i in range(3):
                n = QtWidgets.QLabel()
                n.setStyleSheet("color:%s; font-weight:bold;" % _AXIS_CSS[i])
                n.setFixedWidth(14)
                b = QtWidgets.QDoubleSpinBox()
                b.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                b.setKeyboardTracking(False)
                b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                b.setAlignment(QtCore.Qt.AlignRight)
                b.editingFinished.connect(lambda i=i: self._commit(i))
                lay.addWidget(n, i + 1, 0)
                lay.addWidget(b, i + 1, 1)
                self.names.append(n)
                self.boxes.append(b)
            self.setFixedWidth(PANEL_W)                        # Taşı ve Döndür'de aynı genişlik
            self.adjustSize()
            self.setFixedHeight(self.sizeHint().height())
            self.hide()

        def _setup(self, mode):
            self.mode = mode
            rot = mode == "rotate"
            for i, b in enumerate(self.boxes):
                self.names[i].setText(("H", "P", "B")[i] if rot else ("X", "Y", "Z")[i])
                b.blockSignals(True)
                b.setDecimals(2)
                b.setRange(-180.0, 180.0) if rot else b.setRange(-1e9, 1e9)
                b.setSuffix(" °" if rot else " mm")
                b.setWrapping(rot)
                b.blockSignals(False)

        def _values(self):
            pl = self.gz.obj.Placement
            if self.mode == "rotate":
                return rotation_to_hpb(pl.Rotation)
            return (pl.Base.x, pl.Base.y, pl.Base.z)

        def sync(self, gz, viewport):
            """Zamanlayıcıdan: görünürlük, konum (sol alt köşe) ve değerler (düzenlenen kutuya dokunmadan)."""
            mode = gz.mode if gz is not None else None
            if gz is None and self.isVisible():                # ✕: Enter'dan sonra odak kutuda kalsa bile kapat
                for b in self.boxes:
                    b.clearFocus()
                self.hide()
                return
            if mode not in ("move", "rotate"):
                if self.isVisible() and not self._editing():
                    self.hide()
                return
            if gz is not self.gz or mode != self.mode:
                self.gz = gz
                self._setup(mode)
            fm = self.title.fontMetrics()                      # uzun adlar sabit genişliği bozmasın
            self.title.setText(fm.elidedText(gz.obj.Label, QtCore.Qt.ElideRight, PANEL_W - 24))
            for b, val in zip(self.boxes, self._values()):
                if not b.hasFocus():
                    b.blockSignals(True)
                    b.setValue(val)
                    b.blockSignals(False)
            self.move(12, viewport.height() - self.height() - 12)
            if not self.isVisible():
                self.show()
                self.raise_()

        def _editing(self):
            return any(b.hasFocus() for b in self.boxes)

        def _commit(self, i):
            if self.gz is None or self.gz._pl0 is not None:
                return
            o = self.gz.obj
            pl = o.Placement
            vals = [b.value() for b in self.boxes]
            if self.mode == "rotate":
                old = rotation_to_hpb(pl.Rotation)
                if all(abs(a - b) < 1e-6 for a, b in zip(old, vals)):
                    return
                new = FreeCAD.Placement(pl.Base, hpb_to_rotation(*vals))
            else:
                v = FreeCAD.Vector(*vals)
                if (v - pl.Base).Length < 1e-6:
                    return
                new = FreeCAD.Placement(v, pl.Rotation)
            o.Document.openTransaction("Rig value")
            o.Placement = new
            o.Document.commitTransaction()

    class Overlay(QtWidgets.QWidget):
        """3B görünümün üstünde, seçili rig nesnesini izleyen düğme dizisi (hep ekrana bakar)."""

        def __init__(self, gv):
            super().__init__(gv.viewport())
            self.gv = gv
            self.gz = None
            self.on_close = None
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.setStyleSheet("background:transparent;")
            self.lay = QtWidgets.QHBoxLayout(self)
            self.lay.setContentsMargins(3, 3, 3, 3)
            self.lay.setSpacing(5)
            self.buttons = {}
            for key in ("move", "rotate", "target", "lock", None):
                b = QtWidgets.QPushButton()
                b.setFixedSize(BTN, BTN)
                b.setIconSize(QtCore.QSize(14, 14))
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                b.clicked.connect(lambda _c=False, k=key: self.pick(k))
                self.lay.addWidget(b)
                self.buttons[key] = b
            self.hide()
            self.panel = ValuePanel(gv.viewport())
            self.t = QtCore.QTimer(self)
            self.t.timeout.connect(self.follow)
            self.t.start(30)

        def bind(self, gz):
            self.gz = gz
            if gz is None:
                self.hide()
                return
            self._locked = is_locked(gz.obj)
            keys = list(MODES[gz.kind]) + (["lock"] if self._locked else []) + [None]
            for k, b in self.buttons.items():
                b.setVisible(k in keys)
            self.setFixedSize(len(keys) * BTN + (len(keys) - 1) * 5 + 6, BTN + 6)
            self.restyle()
            self.show()

        def restyle(self):
            locked = self.gz is not None and is_locked(self.gz.obj)
            for k, b in self.buttons.items():
                fill, ic = _STYLE[k]
                on = k is not None and self.gz is not None and self.gz.mode == k
                off = k == "rotate" and locked                 # kilitliyken Döndür pasif (gri)
                if off:
                    fill, ic = "#3f3f46", "#71717a"
                b.setEnabled(not off)
                b.setStyleSheet("QPushButton{min-width:0;max-width:%dpx;min-height:0;padding:0;margin:0;background:%s;"
                                "border:2px solid %s;border-radius:%dpx;}QPushButton:hover{border-color:%s;}"
                                % (BTN, fill, "#ffffff" if on else fill, BTN // 2, fill if off else "#ffffff"))
                b.setIcon(_icon(k, ic))

        def pick(self, key):
            if key is None and self.on_close is not None:
                self.on_close()
                return
            if key == "lock" and self.gz is not None:          # kilidi kaldır (tek geri alma adımı)
                o = self.gz.obj
                o.Document.openTransaction("Unlock target")
                clear_lock(o)
                o.Document.commitTransaction()
                self.bind(self.gz)
                return
            if self.gz is not None:
                self.gz.set_mode(key)
                self.restyle()

        def anchor(self, v, vv, H):
            """Düğmelerin oturacağı dünya noktası ve ekstra piksel boşluğu."""
            o = self.gz.obj
            pl = o.Placement
            if self.gz.kind == "camera":
                u = scene_unit() * float(getattr(o, "BodyLength", 0.3)) / 1.28
                return pl.multVec(FreeCAD.Vector(0, 0.66 * u, 0.8 * u)), 4.0
            from . import lightobj
            L = lightobj.symbol_size(o)
            r = L * (0.12 if self.gz.kind == "distant" else 0.18)          # simgenin yarıçapı (lightobj.symbol_geometry)
            p = pl.Base
            px = r / max(1e-9, vv.getWorldToScreenScale(coin.SbVec3f(p.x, p.y, p.z), 1.0 / float(H)))
            return p, max(px, 10.0) + 4.0

        def follow(self):
            try:
                self.panel.sync(self.gz, self.gv.viewport())
            except Exception as e:
                FreeCAD.Console.PrintError("rig panel: %s\n" % e)
            if self.gz is None:
                return
            if is_locked(self.gz.obj) != getattr(self, "_locked", False) and self.gz._pl0 is None:
                if self.gz.mode == "rotate":                   # kilit kuruldu / kalktı (sürükleme, geri alma ...)
                    self.gz.set_mode(None)
                self.bind(self.gz)
            try:
                self.gz.refresh()
                v = _view()
                vv, W, H = _vv(v)
                p, gap = self.anchor(v, vv, H)
                cn = v.getCameraNode()
                if cn.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
                    eye = FreeCAD.Vector(*cn.position.getValue().getValue())
                    if (p - eye).dot(FreeCAD.Vector(*vv.getProjectionDirection().getValue())) <= 0:
                        self.hide()
                        return
                sx, sy, _ = vv.projectToScreen(coin.SbVec3f(p.x, p.y, p.z)).getValue()
                w, h = self.gv.viewport().width(), self.gv.viewport().height()
                dpr = H / float(h)
                x = sx * w
                y = (1.0 - sy) * h - gap / dpr - self.height() / 2.0
                # gizmonun düğme dizisinin altına düşen kısmı varsa düğmeleri onun da üstüne al (bazı açılarda
                # oklar / halkalar düğmelerin altında kalıyordu)
                half = self.width() / 2.0 + 6.0
                for q in self.gz.extent_points():
                    qx, qy, _ = vv.projectToScreen(coin.SbVec3f(q.x, q.y, q.z)).getValue()
                    if abs(qx * w - x) < half:
                        y = min(y, (1.0 - qy) * h - self.height() / 2.0 - 8.0)
                self.setVisible(-self.width() < x < w + self.width() and -self.height() < y < h + self.height())
                self.move(int(x - self.width() / 2), int(y - self.height() / 2))
            except Exception as e:
                FreeCAD.Console.PrintError("rig overlay: %s\n" % e)
                self.bind(None)

    return Overlay


# ------------------------------------------------------------------ simgeler (cameraobj / lightobj görünüm vekilleri kullanır)
BODY_LEN = 0.3           # kamera gövdesi, sahne birimi cinsinden (30 cm)
_BODY_U = 1.28           # gövde çiziminin birim boyu (mercek ucundan arkaya)


def camera_body(body_len=BODY_LEN):
    """LightWave tarzı ince kamera gövdesi (çizgi): dar uzun kutu, öne açılan mercek kapağı, üstte iki makara.
    Mercek ucu kamera noktasında (yerel 0), gövde arkada (+Z); body_len sahne biriminin katı."""
    u = scene_unit() * float(body_len) / _BODY_U
    P, I = [], []

    def poly(pts, closed=True):
        b = len(P)
        P.extend(pts)
        I.extend(list(range(b, b + len(pts))) + ([b] if closed else []) + [-1])

    def box(hx0, hy0, z0, hx1, hy1, z1):
        a = [(-hx0, -hy0, z0), (hx0, -hy0, z0), (hx0, hy0, z0), (-hx0, hy0, z0)]
        b = [(-hx1, -hy1, z1), (hx1, -hy1, z1), (hx1, hy1, z1), (-hx1, hy1, z1)]
        poly(a)
        poly(b)
        for p, q in zip(a, b):
            poly([p, q], False)
    box(0.17, 0.22, 0.32, 0.17, 0.22, 1.30)                # gövde
    box(0.20, 0.17, 0.02, 0.11, 0.11, 0.32)                # öne açılan mercek kapağı
    for zc in (0.58, 1.02):                                # makaralar
        for xc in (-0.04, 0.04):
            poly([(xc, 0.43 + 0.21 * math.cos(2 * math.pi * i / 12), zc + 0.21 * math.sin(2 * math.pi * i / 12)) for i in range(12)])
        poly([(0, 0.22, zc - 0.06), (0, 0.43, zc), (0, 0.22, zc + 0.06)], False)
    return [(x * u, y * u, z * u) for x, y, z in P], I


def pivot_mark():
    """Işık merkezi: ekranda sabit 9 px dolu daire (tıklanınca ışık seçilir)."""
    s = coin.SoSeparator()
    co = coin.SoCoordinate3()
    co.point.setValue(0, 0, 0)
    mk = coin.SoMarkerSet()
    mk.markerIndex = coin.SoMarkerSet.CIRCLE_FILLED_9_9
    s.addChild(co)
    s.addChild(mk)
    return s


# ------------------------------------------------------------------ seçim -> gizmo
class Manager(object):
    """YBT tezgâhı etkinken: seçilen kamera / ışık için gizmoyu ve düğmeleri kurar (LightWave'deki "geçerli öğe")."""

    def __init__(self):
        self.gz = None
        self.ov = None
        Gui.Selection.addObserver(self)
        self._later()

    def _overlay(self):
        """Etkin 3B görünümün düğme katmanı; görünüm değiştiyse (başka belge) yenisi kurulur."""
        v = _view()
        try:
            if self.ov is not None and self.ov.gv is v.graphicsView():
                return self.ov
        except RuntimeError:                               # görünüm kapanmış, Qt nesnesi silinmiş
            self.ov = None
        self._close_overlay()
        self.ov = _make_overlay()(v.graphicsView())
        self.ov.on_close = self.dismiss
        return self.ov

    def _close_overlay(self):
        if self.ov is None:
            return
        try:
            self.ov.t.stop()
            self.ov.panel.deleteLater()
            self.ov.deleteLater()
        except RuntimeError:
            pass
        self.ov = None

    def _later(self):
        """Seçim olayı tıklamanın ortasında gelir; sahne grafiğini o an değiştirmek seçimi yarıda keser. Olay bitince uygula."""
        from PySide6 import QtCore
        if not getattr(self, "_pending", False):
            self._pending = True
            QtCore.QTimer.singleShot(0, self._sync)

    def _sync(self):
        """Seçim kalkınca gizmo kalır, yalnız başka bir kamera / ışık seçilince ona geçer. Tutamağa basınca FreeCAD
        seçimi temizler; o an gizmoyu sökmek sürüklenen tutamağı yok eder."""
        self._pending = False
        if self.gz is not None and self.gz._pl0 is not None:
            from PySide6 import QtCore                     # sürükleme sürüyor: bitince yeniden bak
            QtCore.QTimer.singleShot(100, self._later)
            return
        if Gui.ActiveDocument is None:
            return
        sel = Gui.Selection.getSelection()
        o = sel[0] if len(sel) == 1 else None
        if o is None or kind_of(o) is None:
            return
        if self.gz is not None and self.gz.obj is o:
            return
        self._drop()
        try:
            self.gz = RigGizmo(o)
            self._overlay().bind(self.gz)
        except Exception as e:
            FreeCAD.Console.PrintError("rig gizmo: %s\n" % e)
            self._drop()

    def _drop(self):
        if self.gz is not None:
            self.gz.remove()
            self.gz = None
        if self.ov is not None:
            try:
                self.ov.bind(None)
            except RuntimeError:
                self.ov = None

    def dismiss(self):
        """✕: gizmoyu ve düğmeleri kapatır (düğme tıklaması Coin olayı dışında, sökmek güvenli)."""
        if self.gz is not None and self.gz._pl0 is None:
            self._drop()

    def addSelection(self, *_a):
        self._later()

    def removeSelection(self, *_a):
        self._later()

    def setSelection(self, *_a):
        self._later()

    def clearSelection(self, *_a):
        self._later()

    def close(self):
        Gui.Selection.removeObserver(self)
        if self.gz is not None and self.gz._pl0 is None:
            self.gz.remove()
        self.gz = None
        self._close_overlay()


# ------------------------------------------------------------------ başlatma (InitGui)
_manager = None
_locks = None


def refresh_symbols():
    """Sahne birimi değişince: açık belgelerdeki kamera ve ışık simgeleri yeniden çizilir (gizmo boyu her karede okunur)."""
    try:
        from . import cameraobj, lightobj
    except Exception:
        return
    for d in FreeCAD.listDocuments().values():
        for o in d.Objects:
            try:
                if cameraobj.is_camera(o) or lightobj._is_light(o):
                    o.ViewObject.Proxy._rebuild()
            except Exception:
                pass


def install_locks():
    """Hedef kilidi gözlemcisi: eklenti yüklenince bir kez kurulur ve hep açık kalır (kilitler her tezgâhta çalışsın)."""
    global _locks
    if _locks is None:
        _locks = LockObserver()
        FreeCAD.addDocumentObserver(_locks)
        for d in FreeCAD.listDocuments().values():
            refresh_locks(d)


def start():
    """Tezgâh etkinleşince: seçimi izleyen gizmo yöneticisi."""
    global _manager
    install_locks()
    if _manager is None:
        _manager = Manager()


def stop():
    """Tezgâhtan çıkınca gizmo ve düğmeler kalkar (kilitler çalışmaya devam eder)."""
    global _manager
    if _manager is not None:
        _manager.close()
        _manager = None
