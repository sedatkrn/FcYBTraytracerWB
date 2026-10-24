# -*- coding: utf-8 -*-
"""Gökyüzü renk geçişi (LightWave Gradient gibi): anahtar noktalar (konum 0 = ufuk, 1 = tepe nokta; bakış açısının
yüksekliğine göre) ve aralarında doğrusal ya da yumuşak (Hermite) geçiş.

  settings.SKY_GRADIENT = [{"pos": 0.0, "color": (r, g, b)}, ...]   (renkler sRGB 0..1; boşsa BG_TOP/BG_BOTTOM kullanılır)
  settings.SKY_SMOOTH   = "hermite" | "linear"
"""
import numpy as np

PRESETS = [                                           # (ad, [(konum 0 = ufuk .. 1 = tepe, sRGB renk)])
    # gündüz
    ("Gündüz", [(0.0, (0.93, 0.94, 0.96)), (0.12, (0.80, 0.85, 0.92)), (0.45, (0.62, 0.72, 0.86)), (1.0, (0.45, 0.58, 0.80))]),
    ("Açık öğle", [(0.0, (0.85, 0.90, 0.97)), (0.25, (0.55, 0.72, 0.95)), (1.0, (0.25, 0.45, 0.85))]),
    ("Şafak", [(0.0, (1.00, 0.80, 0.55)), (0.06, (0.98, 0.70, 0.62)), (0.2, (0.70, 0.68, 0.85)), (0.6, (0.45, 0.55, 0.80)), (1.0, (0.30, 0.42, 0.70))]),
    ("Altın saat", [(0.0, (1.00, 0.78, 0.40)), (0.1, (0.95, 0.76, 0.55)), (0.35, (0.60, 0.68, 0.85)), (1.0, (0.40, 0.52, 0.78))]),
    # akşam / gece
    ("Gün batımı", [(0.0, (1.00, 0.75, 0.20)), (0.03, (1.00, 0.50, 0.10)), (0.08, (0.85, 0.25, 0.10)), (0.18, (0.40, 0.10, 0.25)),
                    (0.45, (0.10, 0.04, 0.12)), (1.0, (0.02, 0.01, 0.03))]),
    ("Mavi saat", [(0.0, (0.35, 0.75, 0.80)), (0.06, (0.20, 0.45, 0.60)), (0.2, (0.12, 0.12, 0.30)), (0.5, (0.05, 0.03, 0.12)), (1.0, (0.01, 0.01, 0.03))]),
    ("Gece", [(0.0, (0.10, 0.11, 0.17)), (0.15, (0.04, 0.05, 0.09)), (1.0, (0.01, 0.01, 0.02))]),
    ("Ay ışıklı gece", [(0.0, (0.20, 0.24, 0.33)), (0.2, (0.10, 0.13, 0.22)), (1.0, (0.03, 0.04, 0.09))]),
    ("Şehir gecesi", [(0.0, (0.35, 0.25, 0.18)), (0.12, (0.15, 0.12, 0.12)), (0.5, (0.05, 0.05, 0.07)), (1.0, (0.02, 0.02, 0.03))]),
    ("Zifiri karanlık", [(0.0, (0.03, 0.03, 0.04)), (1.0, (0.0, 0.0, 0.0))]),
    # hava durumu
    ("Bulutlu", [(0.0, (0.78, 0.79, 0.80)), (0.3, (0.66, 0.68, 0.70)), (1.0, (0.55, 0.57, 0.60))]),
    ("Puslu", [(0.0, (0.88, 0.88, 0.87)), (0.4, (0.80, 0.81, 0.82)), (1.0, (0.72, 0.74, 0.77))]),
    ("Fırtına", [(0.0, (0.45, 0.47, 0.48)), (0.2, (0.30, 0.32, 0.35)), (1.0, (0.18, 0.19, 0.22))]),
    # stüdyo (ürün görselleri)
    ("Stüdyo beyaz", [(0.0, (0.97, 0.97, 0.97)), (1.0, (0.90, 0.90, 0.90))]),
    ("Stüdyo gri", [(0.0, (0.55, 0.55, 0.55)), (1.0, (0.30, 0.30, 0.30))]),
]


def norm_keys(keys):
    """Anahtarları sıralı, sınırlı (0..1) {pos, color} sözlükleri olarak döndürür; en az bir anahtar."""
    out = []
    for k in keys or []:
        try:
            p = float(k["pos"] if isinstance(k, dict) else k[0])
            c = k["color"] if isinstance(k, dict) else k[1]
            out.append({"pos": min(1.0, max(0.0, p)), "color": tuple(float(min(1.0, max(0.0, v))) for v in c[:3])})
        except Exception:
            continue
    out.sort(key=lambda k: k["pos"])
    return out


def from_two(top, bottom):
    """Eski iki renkli arka plandan geçiş (ufukta alt renk, ~25°'de üst renk)."""
    return [{"pos": 0.0, "color": tuple(bottom)}, {"pos": 0.28, "color": tuple(top)}, {"pos": 1.0, "color": tuple(top)}]


def preset_keys(i):
    return norm_keys([{"pos": p, "color": c} for p, c in PRESETS[i][1]])


def evaluate(keys, t, smooth="hermite"):
    """t (n,) 0..1 için sRGB renkler (n,3)."""
    keys = norm_keys(keys)
    t = np.clip(np.asarray(t, dtype=np.float64), 0.0, 1.0)
    if not keys:
        return np.zeros(t.shape + (3,))
    P = np.array([k["pos"] for k in keys])
    C = np.array([k["color"] for k in keys])
    if len(keys) == 1:
        return np.broadcast_to(C[0], t.shape + (3,)).copy()
    j = np.clip(np.searchsorted(P, t, side="right") - 1, 0, len(P) - 2)
    p0, p1 = P[j], P[j + 1]
    u = np.where(p1 > p0, (t - p0) / np.maximum(p1 - p0, 1e-12), 0.0)
    u = np.clip(u, 0.0, 1.0)
    if smooth == "hermite":
        u = u * u * (3.0 - 2.0 * u)
    out = C[j] * (1.0 - u)[..., None] + C[j + 1] * u[..., None]
    out = np.where((t < P[0])[..., None], C[0], out)              # ilk anahtarın altı ve son anahtarın üstü sabit
    return np.where((t > P[-1])[..., None], C[-1], out)


def sky_srgb(keys, z, smooth="hermite"):
    """Bakış yönünün z bileşeninden (yükseklik sinüsü) gökyüzü rengi (sRGB). Ufuk altı = ufuk rengi."""
    el = np.arcsin(np.clip(np.asarray(z, dtype=np.float64), -1.0, 1.0)) / (np.pi / 2.0)
    return evaluate(keys, el, smooth)


def make_editor(QtGui, QtCore, keys, on_change=None, on_select=None):
    """Dikey geçiş şeridi: altı ufuk, üstü tepe. Tıkla = anahtar ekle (o noktadaki renkle), sürükle = taşı,
    sağ tık = sil (en az iki anahtar kalır). Seçili anahtar dışarıdan .selected ile okunur."""

    class GradientStrip(QtGui.QWidget):
        def __init__(self):
            super(GradientStrip, self).__init__()
            self.keys = norm_keys(keys) or from_two((0.62, 0.72, 0.86), (0.93, 0.94, 0.96))
            self.smooth = "hermite"
            self.selected = 0
            self._drag = None
            self.setMinimumSize(70, 150)
            self.setSizePolicy(QtGui.QSizePolicy.Fixed, QtGui.QSizePolicy.Expanding)
            self.setFixedWidth(70)
            self.setToolTip("")

        # şerit dikdörtgeni: solda anahtar işaretleri için boşluk
        def _rect(self):
            return QtCore.QRect(16, 6, self.width() - 22, self.height() - 12)

        def _pos_to_y(self, p):
            r = self._rect()
            return int(round(r.bottom() - p * r.height()))

        def _y_to_pos(self, y):
            r = self._rect()
            return min(1.0, max(0.0, (r.bottom() - y) / float(max(1, r.height()))))

        def paintEvent(self, _e):
            qp = QtGui.QPainter(self)
            r = self._rect()
            n = max(2, r.height())
            cols = evaluate(self.keys, np.linspace(1.0, 0.0, n), self.smooth)
            for i in range(n):
                c = cols[i]
                qp.setPen(QtGui.QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
                qp.drawLine(r.left(), r.top() + i, r.right(), r.top() + i)
            qp.setPen(QtGui.QColor(90, 90, 96))
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.drawRect(r)
            for i, k in enumerate(self.keys):
                y = self._pos_to_y(k["pos"])
                c = k["color"]
                sel = i == self.selected
                qp.setPen(QtGui.QColor(255, 255, 255) if sel else QtGui.QColor(30, 30, 30))
                qp.drawLine(r.left(), y, r.right(), y)
                qp.setBrush(QtGui.QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
                qp.setPen(QtGui.QColor(255, 255, 255) if sel else QtGui.QColor(140, 140, 140))
                qp.drawRect(2, y - 5, 10, 10)
            qp.end()

        def _hit(self, y):
            best, bd = None, 7
            for i, k in enumerate(self.keys):
                d = abs(self._pos_to_y(k["pos"]) - y)
                if d <= bd:
                    best, bd = i, d
            return best

        def mousePressEvent(self, e):
            y = int(e.position().y()) if hasattr(e, "position") else e.y()
            i = self._hit(y)
            if e.button() == QtCore.Qt.RightButton:
                if i is not None and len(self.keys) > 2:
                    del self.keys[i]
                    self.selected = min(self.selected, len(self.keys) - 1)
                    self._changed()
                return
            if i is None:                                        # yeni anahtar: o noktanın rengiyle
                p = self._y_to_pos(y)
                c = tuple(float(v) for v in evaluate(self.keys, np.array([p]), self.smooth)[0])
                self.keys.append({"pos": p, "color": c})
                self.keys = norm_keys(self.keys)
                i = [k["pos"] for k in self.keys].index(p)
                self._changed()
            self.selected = i
            self._drag = i
            self._selected()
            self.update()

        def mouseMoveEvent(self, e):
            if self._drag is None:
                return
            y = int(e.position().y()) if hasattr(e, "position") else e.y()
            self.keys[self._drag]["pos"] = self._y_to_pos(y)
            k = self.keys[self._drag]
            self.keys = norm_keys(self.keys)
            self._drag = self.selected = next(j for j, q in enumerate(self.keys) if q["pos"] == k["pos"] and q["color"] == k["color"])
            self._changed()
            self._selected()

        def mouseReleaseEvent(self, _e):
            self._drag = None

        def set_keys(self, ks):
            self.keys = norm_keys(ks)
            self.selected = 0
            self._changed()
            self._selected()

        def set_selected_color(self, c):
            self.keys[self.selected]["color"] = tuple(c)
            self._changed()

        def set_selected_pos(self, p):
            k = self.keys[self.selected]
            k["pos"] = min(1.0, max(0.0, p))
            self.keys = norm_keys(self.keys)
            self.selected = self.keys.index(k)
            self._changed()

        def _changed(self):
            self.update()
            if on_change:
                on_change()

        def _selected(self):
            if on_select:
                on_select()

    return GradientStrip()
