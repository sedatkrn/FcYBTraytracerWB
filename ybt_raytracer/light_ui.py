# -*- coding: utf-8 -*-
"""Işık Özellikleri paneli (LightWave "Light Properties" tarzı): sahnedeki ışık nesnelerini tek pencerede düzenler.

Açılış: araç çubuğundaki "YBT Işık Özellikleri", ışık nesnesine çift tıklama ya da yeni ışık eklenince.
Panel kalıcıdır (modal değil): FreeCAD'de ışık seçilince panel o ışığa geçer, panelden yapılan değişiklik nesnenin özelliğine yazılır
(Özellik düzenleyicisiyle aynı değerler; ikisi birbirini yansıtır).
"""
import FreeCAD

from .i18n import T
from . import lightobj as L
from . import theme as _theme
from . import settings as S

_KEY = "_ybt_light_dlg"


def _qt():
    from PySide import QtCore, QtGui
    return QtCore, QtGui


def _doc():
    return FreeCAD.ActiveDocument


def open_panel(select=None):
    """Paneli açar (açıksa öne getirir); `select` verilirse o ışık nesnesi seçilir. Paneli döndürür."""
    QtCore, QtGui = _qt()
    dlg = getattr(FreeCAD, _KEY, None)
    try:
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            if select is not None:
                dlg._api["select"](select)
            return dlg
    except Exception:
        pass
    dlg = _build(QtCore, QtGui)
    setattr(FreeCAD, _KEY, dlg)
    dlg.show()
    if select is not None:
        dlg._api["select"](select)
    return dlg


def _build(QtCore, QtGui):
    dlg = QtGui.QDialog(None)
    dlg.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowCloseButtonHint)
    dlg.setWindowTitle(T("Işık Özellikleri"))
    _theme.apply(dlg)
    dlg.setMinimumWidth(380)
    st = {"obj": None, "busy": False, "snap": None, "names": None}

    root = QtGui.QVBoxLayout(dlg)
    top = QtGui.QHBoxLayout()
    b_clear = QtGui.QPushButton(T("Tüm ışıkları sil"))
    b_clear.setToolTip(T("Belgedeki tüm ışık nesnelerini siler (Geri Al ile geri gelir)"))
    lb_count = QtGui.QLabel("")
    lb_count.setStyleSheet("padding: 4px 8px; background-color: #3b3b3f; border-radius: 3px;")
    top.addWidget(b_clear)
    top.addWidget(lb_count, 1)
    root.addLayout(top)

    form = QtGui.QGridLayout()
    form.setColumnStretch(1, 1)

    def lab(t):
        q = QtGui.QLabel(T(t))
        q.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return q

    cb_cur = QtGui.QComboBox()
    ck_on = QtGui.QCheckBox(T("Açık"))
    ck_on.setToolTip(T("Işığı aç / kapat"))
    form.addWidget(lab("Geçerli ışık"), 0, 0)
    form.addWidget(cb_cur, 0, 1)
    form.addWidget(ck_on, 0, 2)
    cb_type = QtGui.QComboBox()
    for k, v in L.TYPES.items():
        cb_type.addItem(T(v["label"]), k)
    cb_type.setToolTip(T("Işık türü; değiştirince türe özgü ayarlar eklenir / kalkar"))
    form.addWidget(lab("Işık türü"), 1, 0)
    form.addWidget(cb_type, 1, 1, 1, 2)
    # renk: düğme + R G B
    bt_col = QtGui.QPushButton("")
    bt_col.setFixedWidth(44)
    bt_col.setToolTip(T("Işık rengi"))
    sp_rgb = []
    crow = QtGui.QHBoxLayout()
    crow.addWidget(bt_col)
    for nm in "RGB":
        s = QtGui.QSpinBox()
        s.setRange(0, 255)
        s.setPrefix(nm + " ")
        sp_rgb.append(s)
        crow.addWidget(s, 1)
    form.addWidget(lab("Işık rengi"), 2, 0)
    form.addLayout(crow, 2, 1, 1, 2)
    sp_int = QtGui.QDoubleSpinBox()
    sp_int.setRange(0.0, 10000.0)
    sp_int.setDecimals(1)
    sp_int.setSingleStep(5.0)
    sp_int.setSuffix(" %")
    sp_int.setToolTip(T("Işık şiddeti. %100 = ışığa dik beyaz yüzeyi tam aydınlatır"))
    form.addWidget(lab("Işık şiddeti"), 3, 0)
    form.addWidget(sp_int, 3, 1, 1, 2)
    root.addLayout(form)

    tabs = QtGui.QTabWidget()
    pg_basic, pg_shadow = QtGui.QWidget(), QtGui.QWidget()
    tabs.addTab(pg_basic, T("Temel"))
    tabs.addTab(pg_shadow, T("Gölgeler"))
    bl = QtGui.QVBoxLayout(pg_basic)
    ck_dif = QtGui.QCheckBox(T("Yüzey rengini aydınlat (Diffuse)"))
    ck_spc = QtGui.QCheckBox(T("Parlama versin (Specular)"))
    ck_dif.setToolTip(T("Kapalıyken ışık yüzeyleri aydınlatmaz, yalnız parlama bırakır"))
    ck_spc.setToolTip(T("Kapalıyken ışık parlama/vurgu bırakmaz"))
    bl.addWidget(ck_dif)
    bl.addWidget(ck_spc)
    bl.addStretch(1)
    sl = QtGui.QGridLayout(pg_shadow)
    ck_sh = QtGui.QCheckBox(T("Gölge düşür"))
    sp_soft = QtGui.QDoubleSpinBox()
    sp_soft.setRange(0.0, 30.0)
    sp_soft.setDecimals(2)
    sp_soft.setSingleStep(0.25)
    sp_soft.setSuffix(" °")
    sp_soft.setToolTip(T("Gölge yumuşaklığı: ışığın görünür açısı (güneş ~0,5°; 0 = sert gölge)"))
    sl.addWidget(ck_sh, 0, 0, 1, 2)
    lb_soft = lab("Yumuşaklık (açı)")
    sl.addWidget(lb_soft, 1, 0)
    sl.addWidget(sp_soft, 1, 1)
    sp_rad = QtGui.QDoubleSpinBox()
    sp_rad.setRange(0.0, 100000.0)
    sp_rad.setDecimals(1)
    sp_rad.setSingleStep(5.0)
    sp_rad.setSuffix(" mm")
    sp_rad.setToolTip(T("Işık kaynağının boyu: büyüdükçe gölge kenarı yumuşar (0 = sert gölge)"))
    lb_rad = lab("Işık boyu (yarıçap)")
    sl.addWidget(lb_rad, 2, 0)
    sl.addWidget(sp_rad, 2, 1)
    sp_smp = QtGui.QSpinBox()
    sp_smp.setRange(4, 128)
    sp_smp.setToolTip(T("Yumuşak gölge kalitesi: yarı gölge için örnek sayısı. Yüksek = pürüzsüz ama yavaş; 24-48 temiz yarı gölge verir"))
    lb_smp = lab("Gölge kalitesi (örnek)")
    sl.addWidget(lb_smp, 3, 0)
    sl.addWidget(sp_smp, 3, 1)
    sl.setRowStretch(4, 1)
    # Falloff sekmesi (Point / Spot)
    pg_fall = QtGui.QWidget()
    fl = QtGui.QGridLayout(pg_fall)
    cb_fall = QtGui.QComboBox()
    for key, txt in (("InverseSquare", "Ters kare (gerçekçi)"), ("Linear", "Doğrusal"), ("None", "Yok (sönmez)")):
        cb_fall.addItem(T(txt), key)
    sp_ref = QtGui.QDoubleSpinBox()
    sp_ref.setRange(0.0, 1000000.0)
    sp_ref.setSpecialValueText(T("Otomatik"))
    sp_ref.setDecimals(1)
    sp_ref.setSingleStep(50.0)
    sp_ref.setSuffix(" mm")
    sp_ref.setToolTip(T("Işığın şiddetinin tam değerinde olduğu uzaklık. Daha yakında parlar, daha uzakta söner. Otomatik (0) = ışığın sahne merkezine uzaklığı"))
    sp_cone = QtGui.QDoubleSpinBox()
    sp_cone.setRange(1.0, 178.0)
    sp_cone.setDecimals(1)
    sp_cone.setSuffix(" °")
    sp_cone.setToolTip(T("Spot koninin tam açısı"))
    sp_edge = QtGui.QDoubleSpinBox()
    sp_edge.setRange(0.0, 90.0)
    sp_edge.setDecimals(1)
    sp_edge.setSuffix(" °")
    sp_edge.setToolTip(T("Koninin kenarındaki yumuşak geçiş (0 = keskin kenar)"))
    lb_cone, lb_edge = lab("Koni açısı"), lab("Kenar yumuşaklığı")
    fl.addWidget(lab("Sönme"), 0, 0)
    fl.addWidget(cb_fall, 0, 1)
    fl.addWidget(lab("Referans uzaklık"), 1, 0)
    fl.addWidget(sp_ref, 1, 1)
    fl.addWidget(lb_cone, 2, 0)
    fl.addWidget(sp_cone, 2, 1)
    fl.addWidget(lb_edge, 3, 0)
    fl.addWidget(sp_edge, 3, 1)
    fl.setRowStretch(4, 1)
    tabs.addTab(pg_fall, T("Sönme / Koni"))
    # Lens Flare sekmesi (render sonrası 2B efekt; postfx)
    from . import postfx as _pf
    pg_flare = QtGui.QWidget()
    gl = QtGui.QGridLayout(pg_flare)
    ck_lf = QtGui.QCheckBox(T("Lens flare"))
    ck_lf.setToolTip(T("Bu ışığa lens flare eklenir (render sonrası). Render paneli > Efektler > Lens flare açık olmalı"))
    cb_fst = QtGui.QComboBox()
    for k_ in _pf.STYLES:
        cb_fst.addItem(T(_pf.STYLE_LABELS[k_]), k_)
    cb_fst.setToolTip(T("Hazır stil: seçince parça ayarları doldurulur. Bir parça elle değişince stil 'Özel' olur"))

    def dspin(lo, hi, step, suffix, dec=1):
        q = QtGui.QDoubleSpinBox()
        q.setRange(lo, hi)
        q.setDecimals(dec)
        q.setSingleStep(step)
        q.setSuffix(suffix)
        return q

    def ispin(lo, hi):
        q = QtGui.QSpinBox()
        q.setRange(lo, hi)
        return q
    sp_fi = dspin(0.0, 1000.0, 10.0, " %")
    sp_fi.setToolTip(T("Flare şiddeti"))
    sp_fsz = dspin(0.5, 100.0, 1.0, " %")
    sp_fsz.setToolTip(T("Merkez parlamasının boyu: görüntü yüksekliğinin yüzdesi"))
    ck_fgl = QtGui.QCheckBox(T("Merkez parlaması"))
    ck_fgl.setToolTip(T("Işığın etrafındaki yumuşak hale"))
    ck_frg = QtGui.QCheckBox(T("Halka"))
    ck_frg.setToolTip(T("Merkezin çevresinde ince ışık halkası"))
    ck_fsr = QtGui.QCheckBox(T("Yıldız çizgileri"))
    sp_fsn = ispin(2, 32)
    sp_fsn.setToolTip(T("Yıldız çizgisi (kol) sayısı"))
    sp_fsa = dspin(-360.0, 360.0, 5.0, " °")
    sp_fsa.setToolTip(T("Yıldız çizgilerinin dönüş açısı"))
    sp_fsl = dspin(1.0, 200.0, 5.0, " %")
    sp_fsl.setToolTip(T("Yıldız çizgisi uzunluğu: görüntü yüksekliğinin yüzdesi"))
    ck_fan = QtGui.QCheckBox(T("Anamorfik çizgi"))
    ck_fan.setToolTip(T("Sinema lensi tarzı yatay, mavimsi çizgi"))
    sp_fal = dspin(1.0, 300.0, 5.0, " %")
    sp_fal.setToolTip(T("Yatay çizginin uzunluğu: görüntü genişliğinin yüzdesi"))
    ck_fgh = QtGui.QCheckBox(T("Hayaletler"))
    ck_fgh.setToolTip(T("Işıktan ekran merkezine giden doğru üzerinde renkli lens yansımaları"))
    sp_fgn = ispin(0, 24)
    sp_fgn.setToolTip(T("Hayalet sayısı"))
    ck_foc = QtGui.QCheckBox(T("Nesne arkasında sön"))
    ck_foc.setToolTip(T("Işık bir nesnenin arkasındaysa flare söner (kısmen kapalıysa zayıflar, cam arkasında saydamlık oranında)"))
    gl.addWidget(ck_lf, 0, 0)
    gl.addWidget(cb_fst, 0, 1, 1, 3)
    gl.addWidget(lab("Şiddet"), 1, 0)
    gl.addWidget(sp_fi, 1, 1)
    gl.addWidget(lab("Boyut"), 1, 2)
    gl.addWidget(sp_fsz, 1, 3)
    gl.addWidget(ck_fgl, 2, 0, 1, 2)
    gl.addWidget(ck_frg, 2, 2, 1, 2)
    gl.addWidget(ck_fsr, 3, 0)
    gl.addWidget(lab("Kol sayısı"), 3, 1)
    gl.addWidget(sp_fsn, 3, 2)
    gl.addWidget(lab("Açı"), 4, 1)
    gl.addWidget(sp_fsa, 4, 2)
    gl.addWidget(lab("Uzunluk"), 5, 1)
    gl.addWidget(sp_fsl, 5, 2)
    gl.addWidget(ck_fan, 6, 0)
    gl.addWidget(lab("Uzunluk"), 6, 1)
    gl.addWidget(sp_fal, 6, 2)
    gl.addWidget(ck_fgh, 7, 0)
    gl.addWidget(lab("Sayı"), 7, 1)
    gl.addWidget(sp_fgn, 7, 2)
    gl.addWidget(ck_foc, 8, 0, 1, 4)
    gl.setRowStretch(9, 1)
    tabs.addTab(pg_flare, T("Lens Flare"))
    flare_w = [ck_lf, cb_fst, sp_fi, sp_fsz, ck_fgl, ck_frg, ck_fsr, sp_fsn, sp_fsa, sp_fsl, ck_fan, sp_fal, ck_fgh, sp_fgn, ck_foc]
    root.addWidget(tabs)
    ck_view = QtGui.QCheckBox(T("3B görünümü bu ışıklarla aydınlat"))
    ck_view.setToolTip(T("Açıkken YBT sahnesi olan belgelerde FreeCAD'in kendi görünüm ışıkları kapanır ve ışık nesneleri 3B görünümde önizlenir "
                         "(gölge ve yumuşaklık yalnız render'da). Kapanınca FreeCAD'in ayarları geri gelir."))
    ck_view.setChecked(bool(getattr(S, "VIEW_LIGHTS", True)))
    ck_view.toggled.connect(lambda v: setattr(S, "VIEW_LIGHTS", bool(v)))
    root.addWidget(ck_view)
    hint = QtGui.QLabel(T("Konum ve yön: ışık nesnesinin Yerleşim (Placement) özelliği. Işık yerel -Z yönüne yayılır (Point ışıkta yön önemsizdir)."))
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #85858a;")
    root.addWidget(hint)

    inputs = [cb_type, ck_on, bt_col, sp_int, ck_dif, ck_spc, ck_sh, sp_soft, sp_rad, sp_smp, cb_fall, sp_ref, sp_cone, sp_edge] + sp_rgb + flare_w

    def paint_color(rgb):
        bt_col.setStyleSheet("background-color: rgb(%d,%d,%d); border: 1px solid #26262a;" % tuple(rgb))

    def alive():
        o = st["obj"]
        try:
            return o is not None and o.Document is not None and o in o.Document.Objects
        except Exception:
            return False

    def kind_of(o):
        return getattr(o, L.PROP_TYPE, "distant")

    def val(o, name, default=0.0):
        try:
            v = getattr(o, name)
            return float(getattr(v, "Value", v))
        except Exception:
            return default

    def snapshot(o):
        try:
            return (bool(o.Enabled), tuple(o.Color)[:3], float(o.Intensity), bool(o.Shadows), val(o, "Softness"),
                    bool(o.AffectDiffuse), bool(o.AffectSpecular), val(o, "ShadowRadius"), val(o, "ShadowSamples", 24), str(getattr(o, "Falloff", "")),
                    val(o, "ReferenceDistance"), val(o, "ConeAngle"), val(o, "EdgeAngle"), kind_of(o),
                    tuple(sorted(L.flare_params(o).items())), str(getattr(o, "FlareStyle", "")))
        except Exception:
            return None

    def show_for(kind):
        """Işık türüne göre alanları göster / gizle."""
        pos = L.is_positional(kind)
        for w in (lb_soft, sp_soft):
            w.setVisible(not pos)
        for w in (lb_rad, sp_rad, lb_smp, sp_smp):
            w.setVisible(pos)
        tabs.setTabEnabled(tabs.indexOf(pg_fall), pos)
        for w in (lb_cone, sp_cone, lb_edge, sp_edge):
            w.setVisible(kind == "spot")
        if not pos and tabs.currentWidget() is pg_fall:
            tabs.setCurrentIndex(0)

    def load():
        """Nesnenin özelliklerini araç gereçlere yazar."""
        o = st["obj"]
        has = alive()
        for w in inputs:
            w.setEnabled(has)
        if not has:
            return
        st["busy"] = True
        try:
            ck_on.setChecked(bool(o.Enabled))
            rgb = [int(round(max(0.0, min(1.0, c)) * 255)) for c in tuple(o.Color)[:3]]
            for s, v in zip(sp_rgb, rgb):
                s.setValue(v)
            paint_color(rgb)
            sp_int.setValue(float(o.Intensity))
            ck_sh.setChecked(bool(o.Shadows))
            kd = kind_of(o)
            show_for(kd)
            sp_soft.setValue(val(o, "Softness"))
            sp_rad.setValue(val(o, "ShadowRadius"))
            sp_smp.setValue(int(val(o, "ShadowSamples", 24)))
            k2 = cb_fall.findData(str(getattr(o, "Falloff", "InverseSquare")))
            if k2 >= 0:
                cb_fall.setCurrentIndex(k2)
            sp_ref.setValue(val(o, "ReferenceDistance", 1000.0))
            sp_cone.setValue(val(o, "ConeAngle", 45.0))
            sp_edge.setValue(val(o, "EdgeAngle", 10.0))
            ck_dif.setChecked(bool(o.AffectDiffuse))
            ck_spc.setChecked(bool(o.AffectSpecular))
            load_flare(o)
            k = cb_type.findData(getattr(o, L.PROP_TYPE, "distant"))
            if k >= 0:
                cb_type.setCurrentIndex(k)
            st["snap"] = snapshot(o)
        finally:
            st["busy"] = False

    def sync_flare():
        on = ck_lf.isChecked() and ck_lf.isEnabled()
        for w_ in flare_w[1:]:
            w_.setEnabled(on)
        if on:
            sp_fsn.setEnabled(ck_fsr.isChecked())
            sp_fsa.setEnabled(ck_fsr.isChecked())
            sp_fsl.setEnabled(ck_fsr.isChecked())
            sp_fal.setEnabled(ck_fan.isChecked())
            sp_fgn.setEnabled(ck_fgh.isChecked())

    def load_flare(o):
        f = L.flare_params(o)
        ck_lf.setChecked(bool(f["on"]))
        k_ = cb_fst.findData(str(getattr(o, "FlareStyle", "Custom")))
        cb_fst.setCurrentIndex(max(0, k_))
        sp_fi.setValue(f["intensity"])
        sp_fsz.setValue(f["size"])
        ck_fgl.setChecked(f["glow"])
        ck_frg.setChecked(f["ring"])
        ck_fsr.setChecked(f["streaks"])
        sp_fsn.setValue(f["streak_n"])
        sp_fsa.setValue(f["streak_angle"])
        sp_fsl.setValue(f["streak_len"])
        ck_fan.setChecked(f["anam"])
        sp_fal.setValue(f["anam_len"])
        ck_fgh.setChecked(f["ghosts"])
        sp_fgn.setValue(f["ghost_n"])
        ck_foc.setChecked(f["occlude"])
        sync_flare()

    def refill(keep=None):
        """Işık listesini belgeden yeniler."""
        d = _doc()
        objs = L.active_lights(d) if d is not None else []
        names = [(o.Name, o.Label) for o in objs]
        keep = keep or (st["obj"].Name if alive() else None)
        st["busy"] = True
        try:
            cb_cur.clear()
            for o in objs:
                cb_cur.addItem(o.Label, o.Name)
            k = cb_cur.findData(keep) if keep else -1
            if k < 0 and objs:
                k = 0
            if k >= 0:
                cb_cur.setCurrentIndex(k)
                st["obj"] = d.getObject(cb_cur.itemData(k))
            else:
                st["obj"] = None
        finally:
            st["busy"] = False
        st["names"] = names
        lb_count.setText(T("Sahnedeki ışıklar: %d") % len(objs))
        load()

    def on_cur(i):
        if st["busy"]:
            return
        d = _doc()
        name = cb_cur.itemData(i)
        st["obj"] = d.getObject(name) if (d is not None and name) else None
        load()
        try:
            import FreeCADGui as Gui
            if st["obj"] is not None:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(st["obj"])
        except Exception:
            pass
    cb_cur.currentIndexChanged.connect(on_cur)

    def put(name, value):
        if st["busy"] or not alive():
            return
        o = st["obj"]
        try:
            if name not in o.PropertiesList:                      # eski ışık nesnesi: eksik özellikleri tamamla
                L._Light.add_props(o, kind_of(o))
            setattr(o, name, value)
            st["snap"] = snapshot(o)
        except Exception as ex:
            FreeCAD.Console.PrintWarning("YBT light: %s\n" % ex)

    def refresh_render():
        """Render penceresinde son render'ın flare'ı, yeniden render almadan güncellensin."""
        try:
            rd = getattr(FreeCAD, "_raytrace_dlg", None)
            fn = getattr(rd, "_raytrace_api", {}).get("refresh_post") if rd is not None else None
            if fn is not None:
                fn()
        except Exception as ex:
            FreeCAD.Console.PrintWarning("YBT flare: %s\n" % ex)

    def put_f(name, value):
        """Flare özelliği: yazılır, stil kutusu (elle değişince 'Özel') ve render önizlemesi güncellenir."""
        if st["busy"] or not alive():
            return
        o = st["obj"]
        if "LensFlare" not in o.PropertiesList:
            L._Light.add_flare_props(o)
        put(name, value)
        st["busy"] = True
        try:
            if name == "FlareStyle":
                load_flare(o)
            else:
                k_ = cb_fst.findData(str(getattr(o, "FlareStyle", "Custom")))
                cb_fst.setCurrentIndex(max(0, k_))
        finally:
            st["busy"] = False
        st["snap"] = snapshot(o)
        sync_flare()
        refresh_render()

    ck_lf.toggled.connect(lambda v: put_f("LensFlare", bool(v)))
    cb_fst.currentIndexChanged.connect(lambda i: put_f("FlareStyle", str(cb_fst.itemData(i))))
    sp_fi.valueChanged.connect(lambda v: put_f("FlareIntensity", float(v)))
    sp_fsz.valueChanged.connect(lambda v: put_f("FlareSize", float(v)))
    ck_fgl.toggled.connect(lambda v: put_f("FlareGlow", bool(v)))
    ck_frg.toggled.connect(lambda v: put_f("FlareRing", bool(v)))
    ck_fsr.toggled.connect(lambda v: put_f("FlareStreaks", bool(v)))
    sp_fsn.valueChanged.connect(lambda v: put_f("FlareStreakCount", int(v)))
    sp_fsa.valueChanged.connect(lambda v: put_f("FlareStreakAngle", float(v)))
    sp_fsl.valueChanged.connect(lambda v: put_f("FlareStreakLength", float(v)))
    ck_fan.toggled.connect(lambda v: put_f("FlareAnamorphic", bool(v)))
    sp_fal.valueChanged.connect(lambda v: put_f("FlareAnamorphicLength", float(v)))
    ck_fgh.toggled.connect(lambda v: put_f("FlareGhosts", bool(v)))
    sp_fgn.valueChanged.connect(lambda v: put_f("FlareGhostCount", int(v)))
    ck_foc.toggled.connect(lambda v: put_f("FlareOcclusion", bool(v)))

    ck_on.toggled.connect(lambda v: put("Enabled", bool(v)))
    ck_sh.toggled.connect(lambda v: put("Shadows", bool(v)))
    ck_dif.toggled.connect(lambda v: put("AffectDiffuse", bool(v)))
    ck_spc.toggled.connect(lambda v: put("AffectSpecular", bool(v)))
    sp_int.valueChanged.connect(lambda v: put("Intensity", float(v)))
    sp_soft.valueChanged.connect(lambda v: put("Softness", float(v)))
    sp_rad.valueChanged.connect(lambda v: put("ShadowRadius", float(v)))
    sp_smp.valueChanged.connect(lambda v: put("ShadowSamples", int(v)))
    sp_ref.valueChanged.connect(lambda v: put("ReferenceDistance", float(v)))
    sp_cone.valueChanged.connect(lambda v: put("ConeAngle", float(v)))
    sp_edge.valueChanged.connect(lambda v: put("EdgeAngle", float(v)))
    cb_fall.currentIndexChanged.connect(lambda i: put("Falloff", str(cb_fall.itemData(i))))

    def type_changed(i):
        """Işık türünü değiştirir: yeni türün özellikleri eklenir, Placement ve ortak ayarlar korunur."""
        if st["busy"] or not alive():
            return
        o = st["obj"]
        k = cb_type.itemData(i)
        if k == kind_of(o) or k not in L.TYPES:
            return
        d = o.Document
        d.openTransaction("Change light type")
        try:
            L.change_type(o, k)
        finally:
            d.commitTransaction()
        d.recompute()
        load()
    cb_type.currentIndexChanged.connect(type_changed)

    def rgb_changed(*_a):
        if st["busy"]:
            return
        rgb = [s.value() for s in sp_rgb]
        paint_color(rgb)
        put("Color", tuple(c / 255.0 for c in rgb) + (1.0,))
    for s in sp_rgb:
        s.valueChanged.connect(rgb_changed)

    def pick_color():
        if not alive():
            return
        c0 = QtGui.QColor(*[s.value() for s in sp_rgb])
        c = QtGui.QColorDialog.getColor(c0, dlg, T("Işık rengi"))
        if c.isValid():
            for s, v in zip(sp_rgb, (c.red(), c.green(), c.blue())):
                s.setValue(v)
    bt_col.clicked.connect(pick_color)

    def clear_all():
        d = _doc()
        objs = L.active_lights(d) if d is not None else []
        if not objs:
            return
        if QtGui.QMessageBox.question(dlg, T("Tüm ışıkları sil"), T("%d ışık nesnesi silinsin mi?") % len(objs),
                                      QtGui.QMessageBox.Yes | QtGui.QMessageBox.No) != QtGui.QMessageBox.Yes:
            return
        d.openTransaction("Clear lights")
        try:
            for o in objs:
                d.removeObject(o.Name)
        finally:
            d.commitTransaction()
        d.recompute()
        refill()
    b_clear.clicked.connect(clear_all)

    # -- belge / seçim eşitleme: ışık seçilince panel ona geçer; özellik düzenleyicide değişen değer panele yansır
    def tick():
        d = _doc()
        objs = L.active_lights(d) if d is not None else []
        names = [(o.Name, o.Label) for o in objs]
        if names != st["names"]:
            refill()
        elif alive():
            sn = snapshot(st["obj"])
            if sn != st["snap"] and not any(w.hasFocus() for w in inputs):
                old = st["snap"]
                load()
                if sn is not None and (old is None or old[-2:] != sn[-2:]):     # flare özellik düzenleyicide değişti
                    refresh_render()
        try:
            import FreeCADGui as Gui
            sel = [o for o in Gui.Selection.getSelection() if L._is_light(o)]
            if len(sel) == 1 and sel[0] is not st["obj"] and sel[0].Document is d and not dlg.isActiveWindow():
                refill(sel[0].Name)
        except Exception:
            pass
    timer = QtCore.QTimer(dlg)
    timer.setInterval(400)
    timer.timeout.connect(tick)
    timer.start()

    def select(obj):
        refill(obj.Name)

    def on_close(*_a):
        timer.stop()
        if getattr(FreeCAD, _KEY, None) is dlg:
            setattr(FreeCAD, _KEY, None)
    dlg.finished.connect(on_close)
    dlg.destroyed.connect(on_close)
    dlg._api = dict(select=select, refill=refill, widgets=dict(current=cb_cur, enabled=ck_on, type=cb_type, rgb=sp_rgb, intensity=sp_int,
                                                               diffuse=ck_dif, specular=ck_spc, shadows=ck_sh, softness=sp_soft,
                                                               radius=sp_rad, falloff=cb_fall, refdist=sp_ref, cone=sp_cone, edge=sp_edge, tabs=tabs,
                                                               clear=b_clear, count=lb_count))
    _theme.fix_combos(dlg)
    refill()
    return dlg
