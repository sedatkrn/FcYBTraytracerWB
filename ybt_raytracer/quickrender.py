# -*- coding: utf-8 -*-
"""Hızlı Render: render'ı panel açmadan, doğrudan 3B görünümün üzerinde, kameranın kadrajında yapar.

Akış: etkin kamera nesnesi varsa 3B görünüm o kameranın bakışına getirilir (kadraj görünümün içine sığar); render bu kadraj dikdörtgeninin
üzerine çizilir. Etkin kamera yoksa şu anki 3B görünümün kamerası ve tüm görünüm alanı kullanılır.

Canlı ve kademeli (LightWave VPR gibi): görüntü önce iri pikselli gelir, her kademede çözünürlük iki katına çıkar
(1/8 -> 1/4 -> 1/2 -> tam); son kademe görünümün kendi çözünürlüğünde ve paneldeki tam kalitededir (AA, AO, GI). İlk kademeler
hızlı ayarlarla (AA yok, AO yok, az GI/gölge örneği) çizilir. Görünüm çevrilince / yakınlaştırılınca, kamera ya da ışık
değişince ya da model değişince render iptal edilir ve dokunma bitince en iri kademeden yeniden başlar. Fare olayları
görünüme geçer (render açıkken de gezinilebilir); çift tıklama tıklanan parçanın malzemesini açar.
Ayarlar (kalite, ışık, AO, HDRI...) render panelindekilerle aynıdır: panel açıksa paneldeki değerler, kapalıysa belgede saklı
ayarlar kullanılır; panelde ayar değişince ↻ ile yeniden başlatılır. Esc ya da ✕: kapat; Kaydet: son biten kademeyi PNG kaydeder.
Komut yeniden çalıştırılırsa açık canlı render kapanır (aç / kapa).
"""
import time

import numpy as np

from . import settings as S
from .i18n import T

_KEY = "_ybt_quick_overlay"
_running = {"on": False}


def _qt():
    from PySide import QtCore, QtGui
    return QtCore, QtGui


def _view_widget():
    """Etkin 3B görünümün Qt bileşeni (üzerine yer paylaşımı eklenecek)."""
    import FreeCADGui as Gui
    QtCore, QtGui = _qt()
    import FreeCAD
    mw = Gui.getMainWindow()
    mdi = mw.findChild(QtGui.QMdiArea)
    if mdi is None:
        return None
    sub = mdi.currentSubWindow() or mdi.activeSubWindow()
    if sub is None:                                          # odak başka yerdeyse (panel vb.): belgenin görünür 3B görünümü
        name = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else ""
        cands = [s for s in mdi.subWindowList() if s.widget() is not None and s.isVisible()]
        by_name = [s for s in cands if name and name in s.windowTitle()]
        sub = (by_name or cands or [None])[0]
    return sub.widget() if sub is not None else None


def close_overlay():
    import FreeCAD
    live = getattr(FreeCAD, "_ybt_quick_live", None)
    if live is not None:
        live.close()
    ov = getattr(FreeCAD, _KEY, None)
    if ov is not None:
        try:
            ov.close_all()
        except Exception:
            pass
        setattr(FreeCAD, _KEY, None)


def _fit_rect(W, H, aspect):
    """W x H alanına `aspect` oranlı en büyük dikdörtgen, ortalı: (x, y, w, h)."""
    if W / float(H) > aspect:
        h = H
        w = int(round(H * aspect))
    else:
        w = W
        h = int(round(W / aspect))
    return (W - w) // 2, (H - h) // 2, max(w, 16), max(h, 16)


class _Overlay(object):
    """3B görünümün üzerinde: görüntü, durum çubuğu, Kaydet ve ✕ düğmeleri."""

    def __init__(self, host, rect, on_close, on_save):
        QtCore, QtGui = _qt()
        self.QtCore, self.QtGui = QtCore, QtGui
        x, y, w, h = rect
        self.host = host
        self.alive = True
        self.on_close = on_close
        self.on_save = on_save
        self.on_double = None                                    # çift tıklama: (u, v, ctrl) -> tıklanan yüzeyin malzemesini seç
        self.img = QtGui.QLabel(host)
        self.img.setGeometry(x, y, w, h)
        self.img.setScaledContents(True)
        self.img.setStyleSheet("background-color: #1e1e22; border: 2px solid #ff9a2e;")
        self.img.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.bar = QtGui.QLabel(host)
        self.bar.setStyleSheet("background-color: rgba(20,20,24,200); color: #f0f0f0; padding: 3px 8px; border-radius: 3px;")
        self.btn_save = QtGui.QPushButton(T("Kaydet"), host)
        self.btn_close = QtGui.QPushButton("✕", host)
        self.btn_redo = QtGui.QPushButton("↻", host)
        self.on_redo = None
        self.btn_redo.setToolTip(T("Panel ayarlarını yeniden al ve render'ı baştan başlat"))
        self.btn_redo.clicked.connect(lambda *_a: self.on_redo() if self.on_redo else None)
        for b in (self.btn_save, self.btn_close, self.btn_redo):
            b.setStyleSheet("QPushButton { background-color: rgba(20,20,24,220); color: #f0f0f0; border: 1px solid #55555a; border-radius: 3px; "
                            "padding: 3px 10px; min-width: 0px; } QPushButton:hover { background-color: #ff9a2e; color: #111; }")
        self.btn_save.setToolTip(T("Görüntüyü PNG olarak kaydeder"))
        self.btn_close.setToolTip(T("Kapat (Esc). Render sürüyorsa iptal eder"))
        self.btn_save.setEnabled(False)
        self.btn_close.clicked.connect(self._close_clicked)
        self.btn_save.clicked.connect(lambda *_a: self.on_save())
        self.img.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)   # fare görünüme geçer: render açıkken gezinilir
        self._place()
        for wd in self.widgets():
            wd.show()
            wd.raise_()
        # Esc ve çift tıklama: uygulama düzeyinde süzgeç (görüntü fareyi geçirdiği için olaylar alttaki görünüme gider)
        self._filter = _EscFilter(self, QtCore)
        QtGui.QApplication.instance().installEventFilter(self._filter)

    def widgets(self):
        return (self.img, self.bar, self.btn_save, self.btn_redo, self.btn_close)

    def set_rect(self, rect):
        x, y, w, h = rect
        self.img.setGeometry(x, y, w, h)
        self._place()

    def show_image(self, on):
        self.img.setVisible(bool(on))

    def _place(self):
        g = self.img.geometry()
        self.btn_close.adjustSize()
        self.btn_save.adjustSize()
        self.btn_redo.adjustSize()
        self.btn_close.move(g.right() - self.btn_close.width() - 8, g.top() + 8)
        self.btn_redo.move(self.btn_close.x() - self.btn_redo.width() - 6, g.top() + 8)
        self.btn_save.move(self.btn_redo.x() - self.btn_save.width() - 6, g.top() + 8)
        self.bar.adjustSize()
        self.bar.move(g.left() + 8, g.top() + 8)

    def set_status(self, text):
        self.bar.setText(text)
        self.bar.adjustSize()
        self.bar.raise_()

    def set_array(self, arr, w, h):
        QtCore, QtGui = self.QtCore, self.QtGui
        a = np.ascontiguousarray(arr)
        qi = QtGui.QImage(a.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
        self.img.setPixmap(QtGui.QPixmap.fromImage(qi))
        for wd in self.widgets()[1:]:
            wd.raise_()

    def _close_clicked(self, *_a):
        self.on_close()

    def close_all(self):
        self.alive = False
        try:
            self.QtGui.QApplication.instance().removeEventFilter(self._filter)
        except Exception:
            pass
        for wd in self.widgets():
            try:
                wd.hide()
                wd.deleteLater()
            except Exception:
                pass


class _EscFilter(object):
    def __new__(cls, ov, QtCore):
        class F(QtCore.QObject):
            def eventFilter(self, obj, ev):
                try:
                    t = ev.type()
                    if t == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_Escape and ov.host.isVisible() \
                            and ov.host.window().isActiveWindow():
                        ov.on_close()
                        return True
                    if t == QtCore.QEvent.MouseButtonDblClick and ev.button() == QtCore.Qt.LeftButton and ov.on_double is not None \
                            and isinstance(obj, QtCore.QObject) and hasattr(obj, "mapTo") and (obj is ov.host or ov.host.isAncestorOf(obj)):
                        p = ev.position() if hasattr(ev, "position") else ev.pos()
                        pt = obj.mapTo(ov.host, p.toPoint() if hasattr(p, "toPoint") else p)
                        g = ov.img.geometry()
                        if g.contains(pt) and ov.img.isVisible():
                            ctrl = bool(ev.modifiers() & QtCore.Qt.ControlModifier)
                            ov.on_double((pt.x() - g.left()) / float(max(1, g.width())), (pt.y() - g.top()) / float(max(1, g.height())), ctrl)
                            return True
                except Exception:
                    pass
                return False
        return F()




def _apply_settings(doc):
    """Render ayarlarını S'ye al: panel açıksa panelden, değilse belgede saklı ayarlardan."""
    from . import commands, docstore, lightobj
    dlg = commands._panel()
    api = getattr(dlg, "_raytrace_api", None) if dlg is not None else None
    if api and api.get("apply_all"):
        api["apply_all"]()
    else:
        docstore.load_settings(doc)
    if str(S.LIGHT_MODE) != "legacy":
        lightobj.apply_to_settings(doc)
    else:
        S._OBJ_LIGHTS = None


# kademeler: (genişlik payı, kaba mı). Kaba kademeler hızlı ayarlarla çizilir; son kademe paneldeki tam kalitededir.
LEVELS = ((1 / 8.0, True), (1 / 4.0, True), (1 / 2.0, True), (1.0, False))
_ROUGH_KEYS = ("WIDTH", "AA", "SOFT_SAMPLES", "AO", "GI_SAMPLES", "QUICK_PASS")
_SETTLE = 0.3          # sn: görünüm bu kadar süre durgun kalınca render yeniden başlar


def _round_cam(cam, aspect):
    return (tuple(np.round(np.asarray(cam["pos"], float), 4)), tuple(np.round(np.asarray(cam["fwd"], float), 5)),
            tuple(np.round(np.asarray(cam["up"], float), 5)), bool(cam["persp"]), round(float(cam["size"]), 6),
            round(float(aspect), 4))


class _DocWatch(object):
    """Belge gözlemcisi: ışık / kamera değişince yeniden render, diğer nesneler değişince sahne yeniden okunur."""

    _SKIP = {"Label", "Label2", "ExpressionEngine"}

    def __init__(self, live):
        self.live = live

    def slotChangedObject(self, obj, prop):
        try:
            if obj.Document is not self.live.doc or prop in self._SKIP:
                return
            from . import cameraobj, lightobj
            if lightobj._is_light(obj) or cameraobj.is_camera(obj):
                self.live.mark("render")
            else:
                self.live.mark("scene")
        except Exception:
            pass

    def slotCreatedObject(self, obj):
        self.slotChangedObject(obj, "Shape")

    def slotDeletedObject(self, obj):
        self.slotChangedObject(obj, "Shape")


class _Live(object):
    """Canlı, kademeli Hızlı Render: görünüm durgunken iri pikselden tam çözünürlüğe kadar inceltir, dokunulunca baştan başlar."""

    def __init__(self, doc, host):
        import FreeCAD
        self.FreeCAD = FreeCAD
        self.QtCore, self.QtGui = _qt()
        self.doc, self.host = doc, host
        self.ov = None
        self.scene = None
        self.scene_dirty = True
        self.dirty = True                     # yeniden render gerekiyor
        self.busy = False                     # render döngüsü çalışıyor
        self.changed_at = 0.0
        self.sig = None                       # son render'ın kamera imzası
        self.out = None
        self.size = None
        self.cam = None
        self.aspect = 1.0
        self.rect = None
        self.watch = _DocWatch(self)
        self.timer = None

    # -- kamera / kadraj
    def _camera(self):
        import FreeCADGui as Gui
        from . import cameraobj
        from .freecad_scene import read_camera
        co = cameraobj.active_camera(self.doc)
        if co is not None:
            cam, aspect = cameraobj.render_camera(co)
            rect = _fit_rect(self.host.width(), self.host.height(), aspect)
        else:
            cam, aspect = read_camera(Gui.ActiveDocument.ActiveView)
            rect = (0, 0, self.host.width(), self.host.height())
            aspect = rect[2] / float(max(1, rect[3]))
        return cam, aspect, rect

    def mark(self, what):
        if what == "scene":
            self.scene_dirty = True
        self.dirty = True
        self.changed_at = time.time()

    # -- başlat / kapat
    def start(self):
        from . import cameraobj
        co = cameraobj.active_camera(self.doc)
        if co is not None:
            cameraobj.look_through(co)                       # kadraj görünümün içine oturur
            for _ in range(3):
                self.QtCore.QCoreApplication.processEvents()
        self.cam, self.aspect, self.rect = self._camera()
        self.ov = _Overlay(self.host, self.rect, self.close, self.save)
        self.ov.on_double = self.pick
        self.ov.on_redo = lambda: (self.mark("scene"), setattr(self, "changed_at", 0.0))
        self.FreeCAD._ybt_quick_overlay = self.ov
        self.FreeCAD.addDocumentObserver(self.watch)
        self.timer = self.QtCore.QTimer()
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.changed_at = 0.0
        self._poll()

    def close(self):
        _running["on"] = False
        self.dirty = False
        try:
            if self.timer is not None:
                self.timer.stop()
        except RuntimeError:
            pass
        try:
            self.FreeCAD.removeDocumentObserver(self.watch)
        except Exception:
            pass
        if self.ov is not None:
            self.ov.close_all()
        if getattr(self.FreeCAD, _KEY, None) is self.ov:
            setattr(self.FreeCAD, _KEY, None)
        if getattr(self.FreeCAD, "_ybt_quick_live", None) is self:
            self.FreeCAD._ybt_quick_live = None

    def alive(self):
        return self.ov is not None and self.ov.alive and _running["on"]

    # -- izleme: görünüm / kamera değişti mi, durgun mu
    def _poll(self):
        if not self.alive():
            return
        try:
            if self.FreeCAD.ActiveDocument is not self.doc:
                return                                         # başka belgeye geçildi: bekle
            cam, aspect, rect = self._camera()
        except Exception:
            return
        sig = _round_cam(cam, aspect) + (tuple(rect),)
        if sig != self.sig:
            if self.sig is not None:
                self.ov.show_image(False)                      # gezinirken canlı OpenGL görünümü görünsün
            self.sig = sig
            self.cam, self.aspect = cam, aspect
            if rect != self.rect:
                self.rect = rect
                self.ov.set_rect(rect)
            self.mark("render")
            return
        if self.dirty and not self.busy and time.time() - self.changed_at >= _SETTLE:
            self.QtCore.QTimer.singleShot(0, self._loop)

    def _tick(self):
        self.QtCore.QCoreApplication.processEvents()
        return self.alive() and not self.dirty

    # -- render döngüsü
    def _loop(self):
        from .freecad_scene import collect_scene
        from .parallel import _Cancelled
        from .render import render
        if self.busy or not self.alive() or not self.dirty:
            return
        self.busy = True
        self.dirty = False
        saved = None
        t0 = time.time()
        try:
            _apply_settings(self.doc)
            saved = {k: getattr(S, k, None) for k in _ROUGH_KEYS}
            cam, aspect = self.cam, self.aspect
            if self.scene is None or self.scene_dirty or bool(S.CULL_OFFSCREEN):
                self.scene_dirty = False
                self.ov.set_status(T("sahne hazırlanıyor..."))

                def status(msg, force=False, frac=None):
                    if not self._tick():
                        raise _Cancelled()
                self.scene, _n = collect_scene(self.doc, status, camera=cam, aspect=aspect)
            full_w = max(64, int(self.rect[2]))
            prev = None
            for i, (frac, rough) in enumerate(LEVELS):
                w = max(16, int(round(full_w * frac)))
                if w < 48 and i < len(LEVELS) - 1:
                    continue
                h = max(1, int(round(w / aspect)))
                S.WIDTH = w
                S.AA = 0 if rough else saved["AA"]
                S.QUICK_PASS = rough
                S.SOFT_SAMPLES = min(int(saved["SOFT_SAMPLES"]), 8) if rough else saved["SOFT_SAMPLES"]
                S.AO = False if (rough and frac < 0.5) else saved["AO"]          # yarım çözünürlükte AO da gelsin
                S.GI_SAMPLES = min(int(saved["GI_SAMPLES"]), 12 if frac < 0.5 else 32) if rough else saved["GI_SAMPLES"]
                label = T("kademe %d/%d") % (i + 1, len(LEVELS))
                bg = self._upscaled(prev, w, h)

                def show(img, rows, fr=None, stage_lbl=None, label=label, w=w, h=h, bg=bg):
                    if not self.alive():
                        return False
                    a = np.clip(img * 255.0, 0, 255).astype(np.uint8)
                    if bg is not None and fr is None:                    # henüz çizilmemiş satırlarda bir önceki kademe görünsün
                        a = a.copy()
                        a[rows:] = bg[rows:]
                    self.ov.set_array(a, w, h)
                    self.ov.show_image(True)
                    self.ov.set_status("%s  %dx%d  %s%d%%" % (label, w, h, (stage_lbl + "  ") if stage_lbl else "",
                                                             int(100 * (rows / float(h) if fr is None else fr))))
                    return self._tick()

                out = render(self.scene, cam, w, h, shadows=S.SHADOWS, progress=show, tile=S.TILE, tick=self._tick,
                             stage=lambda s_, label=label: self.ov.set_status("%s  %s" % (label, s_)))
                if out is None:
                    raise _Cancelled()
                prev = out
                self.out, self.size = out, (w, h)
                self.ov.set_array(out, w, h)
                self.ov.show_image(True)
                self.ov.btn_save.setEnabled(True)
                for k, v in saved.items():                                  # kademe arasında ayarlar asıl haline dönsün
                    setattr(S, k, v)
            self.ov.set_status(T("bitti: %dx%d | %.1f sn | görünümü çevirince yeniden başlar | Esc: kapat")
                               % (self.size[0], self.size[1], time.time() - t0))
            self.FreeCAD.Console.PrintMessage(T("Hızlı Render: %dx%d, %.1f sn\n") % (self.size[0], self.size[1], time.time() - t0))
        except _Cancelled:
            if self.alive():
                self.ov.set_status(T("görünüm değişti, yeniden başlıyor..."))
        except Exception:
            import traceback
            self.FreeCAD.Console.PrintError(T("RayTrace hatası:\n") + traceback.format_exc())
            if self.alive():
                self.ov.set_status(T("hata: Rapor görünümüne bakın"))
        finally:
            if saved is not None:
                for k, v in saved.items():
                    setattr(S, k, v)
            S.QUICK_PASS = False
            self.busy = False

    @staticmethod
    def _upscaled(prev, w, h):
        if prev is None:
            return None
        ph, pw = prev.shape[:2]
        yi = np.minimum((np.arange(h) * ph) // max(h, 1), ph - 1)
        xi = np.minimum((np.arange(w) * pw) // max(w, 1), pw - 1)
        return prev[yi][:, xi]

    # -- düğmeler
    def save(self):
        from .output import pick_out_path, write_png
        if self.out is None:
            return
        try:
            path = pick_out_path(self.doc.FileName)
            write_png(path, self.out)
            self.ov.set_status(T("kaydedildi: %s") % path)
            self.FreeCAD.Console.PrintMessage(T("Hızlı Render: kaydedildi: %s\n") % path)
        except Exception as ex:
            self.ov.set_status(T("KAYDEDİLEMEDİ (%s)") % ex)

    def pick(self, u, v, ctrl):
        """Çift tıklanan yüzeyi bul; Malzeme penceresini açıp o nesneyi seçer (boya kovası açıksa malzemeyi atar)."""
        if self.scene is None or self.size is None:
            return
        from .pick import pick as _pick
        from . import commands
        try:
            w_, h_ = self.size
            hit = _pick(self.scene, self.cam, w_, h_, u * w_, v * h_)
        except Exception:
            import traceback
            self.FreeCAD.Console.PrintError(T("RayTrace hatası:\n") + traceback.format_exc())
            return
        if hit is None:
            self.ov.set_status(T("Tıklanan yerde nesne yok (arka plan ya da zemin)."))
            return
        commands.open_materials(select=hit[0], add=ctrl)
        o = self.doc.getObject(hit[0])
        self.ov.set_status(T("Seçildi: %s") % (o.Label if o is not None else hit[0]))


def run():
    """Hızlı Render (araç çubuğu komutu): canlı, kademeli render'ı açar; açıksa kapatır."""
    import FreeCAD
    import FreeCADGui as Gui
    live = getattr(FreeCAD, "_ybt_quick_live", None)
    if live is not None and live.alive():
        live.close()
        return
    doc = FreeCAD.ActiveDocument
    if doc is None or Gui.ActiveDocument is None or getattr(Gui.ActiveDocument, "ActiveView", None) is None:
        return
    host = _view_widget()
    if host is None:
        FreeCAD.Console.PrintWarning(T("Hızlı Render: 3B görünüm bulunamadı.\n"))
        return
    close_overlay()
    _running["on"] = True
    live = _Live(doc, host)
    FreeCAD._ybt_quick_live = live
    try:
        live.start()
    except Exception:
        import traceback
        FreeCAD.Console.PrintError(T("RayTrace hatası:\n") + traceback.format_exc())
        live.close()
