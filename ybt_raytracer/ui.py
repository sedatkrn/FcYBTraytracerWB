# -*- coding: utf-8 -*-
import os
import numpy as np
import time
from . import settings as S
from .i18n import T
from . import textures as _textures
from .parallel import _Cancelled
from .render import render
from .output import pick_out_path, render_meta, write_png
from . import paths as _paths
from .freecad_scene import collect_scene, read_camera, read_freecad_lights
from . import materials as _mats
from . import theme as _theme
from . import lightpresets as _lp
from . import lightobj as _lobj
from . import cameraobj as _cam
from . import userdefaults as _ud
from . import __version__ as _VER


_QUALITY = [("Low", 0.012), ("Hızlı", 0.004), ("Normal", 0.0015), ("İnce", 0.0008)]   # Low: çok büyük montajlar için


def main(lang=None):
    """Ayar paneli olan, kapanmayan (modeless) bir pencere açar. Render, 'Render' düğmesine basınca başlar."""
    import FreeCAD
    import FreeCADGui as Gui
    from PySide import QtGui, QtCore

    from . import i18n
    if lang:
        i18n.set_lang(lang)
    else:
        i18n.init_lang()
    doc = FreeCAD.ActiveDocument
    if doc is None or Gui.ActiveDocument is None:
        FreeCAD.Console.PrintError(T("RayTrace: açık belge yok.\n"))
        return
    old = getattr(FreeCAD, "_raytrace_dlg", None)          # önceki pencere açıksa kapat
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    g = vars(S)                      # ayarlar settings modülünde durur
    from . import docstore as _ds
    if not getattr(FreeCAD, "_ybt_defaults_applied", False):     # kayıtlı varsayılanlar oturumda bir kez yüklenir
        FreeCAD._ybt_defaults_applied = True
        try:
            _ud.apply_saved()
        except Exception as ex:
            FreeCAD.Console.PrintWarning("RayTrace: defaults: %s\n" % ex)

    try:
        _ds.load_settings(doc)                                  # belgeye kaydedilmiş panel ayarları (varsa) kullanıcı varsayılanlarının üstüne biner
    except Exception as ex:
        FreeCAD.Console.PrintWarning("RayTrace: doc settings: %s\n" % ex)
    dlg = QtGui.QDialog(None)                                   # ebeveynsiz üst düzey pencere: görev çubuğunda görünür, küçültülebilir
    dlg.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowMaximizeButtonHint | QtCore.Qt.WindowCloseButtonHint)
    dlg.setWindowTitle("FC YBT Raytracer v%s" % _VER)
    _theme.apply(dlg)                                           # koyu gri palet (theme.py)
    try:
        _ic = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(_ic):
            dlg.setWindowIcon(QtGui.QIcon(_ic))
    except Exception:
        pass
    outer = QtGui.QHBoxLayout(dlg)

    def spin(lo, hi, val, step=1, suffix=""):
        sp = QtGui.QSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setValue(int(round(val)))
        if suffix:
            sp.setSuffix(suffix)
        return sp

    # ================= SOL: Settings =================
    left = QtGui.QWidget()
    lv = QtGui.QVBoxLayout(left)
    lv.setContentsMargins(0, 0, 0, 0)

    # ---- Kalite Ayarları ----
    qbox = QtGui.QGroupBox(T("Kalite Ayarları"))
    qg = QtGui.QGridLayout(qbox)
    sp_w = spin(160, 4000, S.WIDTH, 80, " px")
    cb_q = QtGui.QComboBox()
    for name, _f in _QUALITY:
        cb_q.addItem(T(name))
    cb_q.setCurrentIndex(min(range(len(_QUALITY)), key=lambda i: abs(_QUALITY[i][1] - S.TESS_FACTOR)))
    cb_aa = QtGui.QComboBox()
    for name in (T("Kapalı"), "2x2", "3x3", "4x4"):
        cb_aa.addItem(name)
    cb_aa.setCurrentIndex(0 if int(S.AA) < 2 else min(3, int(S.AA) - 1))
    sp_bn = spin(0, 3, S.BOUNCES)
    cb_th = QtGui.QComboBox()
    _ncpu = os.cpu_count() or 1
    _TH = [0] + sorted({1, 2, 4} | {n for n in (6, 8, 12, 16, 24, 32) if n <= _ncpu} | {_ncpu} | ({int(S.THREADS)} if int(S.THREADS) > 0 else set()))   # işlemci sayısına kadar seçenek
    for _v in _TH:
        cb_th.addItem(T("Otomatik") if _v == 0 else str(_v))
    cb_th.setCurrentIndex(_TH.index(int(S.THREADS)) if int(S.THREADS) in _TH else 0)
    cb_th.setToolTip(T("Render için kullanılacak çekirdek (iş parçacığı) sayısı; Otomatik = işlemci çekirdek sayısı"))
    ck_sh = QtGui.QCheckBox(T("Gölgeler"))
    ck_sh.setChecked(bool(S.SHADOWS))
    ck_gl = QtGui.QCheckBox(T("Saydamlık (cam)"))
    ck_gl.setChecked(bool(S.GLASS))
    ck_rf = QtGui.QCheckBox(T("Yansımalar"))
    ck_rf.setChecked(bool(S.REFLECTIONS))
    ck_rf.setToolTip(T("Malzemelerin yansıma değerlerini uygular (krom, cam, boya...). Kapalıysa yansıma yoktur (en hızlı)."))
    ck_rr = QtGui.QCheckBox(T("Kırılma"))
    ck_rr.setChecked(bool(S.REFRACTION))
    ck_rr.setToolTip(T("Saydam malzemelerde ışığı kırar (Snell yasası, malzemenin IOR değeriyle). Kapalıysa ışık düz geçer (daha hızlı). "
                       "Malzemesi olmayan saydam nesneler cam (IOR 1.5) sayılır."))
    ck_sm = QtGui.QCheckBox(T("Yumuşak yüzeyler"))
    ck_sm.setChecked(bool(S.SMOOTH))
    ck_sm.setToolTip(T("Eğri yüzeyleri (küre, simit, silindir) pürüzsüz gölgeler; keskin kenarlar (küp, plaka) korunur. Kapalıysa üçgenler görünür."))
    ck_cull = QtGui.QCheckBox(T("Kadraj dışındaki nesneleri atla"))
    ck_cull.setChecked(bool(S.CULL_OFFSCREEN))
    ck_cull.setToolTip(T("Kadrajın dışında kalan nesneler sahneye hiç alınmaz: üçgenlenmez, gölge/yansıma vermez, "
                         "sahne boyutuna ve sanal zemin yüksekliğine katılmaz. Büyük modellerde kadraj yakınken hızlandırır. "
                         "Kamera değişince sahne yeniden okunur."))
    ck_sv = QtGui.QCheckBox(T("Bitince otomatik kaydet"))
    ck_sv.setChecked(True)
    sp_w.setToolTip(T("Çıktı genişliği; yükseklik 3B görünümün oranından gelir"))
    cb_q.setToolTip(T("Yüzey pürüzsüzlüğü (üçgenleme inceliği). Değişince sahne yeniden okunur"))
    cb_aa.setToolTip(T("Kenar yumuşatma: yalnız kenar piksellerinde çoklu örnekleme yapar"))
    ck_gl.setToolTip(T("Nesnenin Transparency (saydamlık) özelliğine göre arkası görünür"))
    sp_bn.setToolTip(T("Bir ışının en çok kaç kez yansıyacağı"))
    qrows = [(T("Genişlik"), sp_w, T("Kalite"), cb_q),
             (T("Kenar yumuşatma"), cb_aa, T("Yansıma sayısı"), sp_bn),
             (T("Çekirdek"), cb_th, None, None)]
    for r, (l1, w1, l2, w2) in enumerate(qrows):              # etiket ve alan aynı satırda (dar, kısa panel)
        qg.addWidget(QtGui.QLabel(l1), r, 0)
        qg.addWidget(w1, r, 1)
        if l2 is not None:
            qg.addWidget(QtGui.QLabel(l2), r, 2)
            qg.addWidget(w2, r, 3)
    for r, ck_ in enumerate((ck_sh, ck_gl, ck_rf, ck_rr, ck_sm, ck_sv, ck_cull)):   # onay kutuları tek sütunda, solda
        qg.addWidget(ck_, len(qrows) + r, 0, 1, 4)
    grid_fields = [sp_w, cb_q, cb_aa, sp_bn, cb_th]          # değer kutuları: hepsi aynı genişlik, sütunlar bölümler arasında hizalı
    lv.addWidget(qbox)

    # ---- Işık Ayarları: ortam + en çok 3 ışık ----
    lbox = QtGui.QGroupBox(T("Işık Ayarları"))
    lvb = QtGui.QVBoxLayout(lbox)
    colors = {}

    def color_button(key, rgb, title=T("Işık rengi")):
        colors[key] = tuple(rgb)
        bt = QtGui.QPushButton("")
        bt.setFixedWidth(54)

        def paint():
            r, g_, b_ = [int(round(max(0.0, min(1.0, v)) * 255)) for v in colors[key]]
            bt.setStyleSheet("QPushButton { background-color: rgb(%d,%d,%d); }" % (r, g_, b_))   # seçicisiz yazılırsa açıklama baloncuğu da bu rengi alıyordu

        def pick(*_a):
            r, g_, b_ = [int(round(max(0.0, min(1.0, v)) * 255)) for v in colors[key]]
            col = QtGui.QColorDialog.getColor(QtGui.QColor(r, g_, b_), dlg, title)
            if col.isValid():
                colors[key] = (col.red() / 255.0, col.green() / 255.0, col.blue() / 255.0)
                paint()
        bt.clicked.connect(pick)
        paint()
        bt._paint = paint
        bt._key = key
        bt._after = []
        _orig = pick

        def pick2(*_a):
            _orig()
            for f in bt._after:
                f()
        bt.clicked.disconnect(pick)
        bt.clicked.connect(pick2)
        return bt

    ck_leg = QtGui.QCheckBox(T("Eski aydınlatma (makro dönemi)"))
    ck_leg.setChecked(str(S.LIGHT_MODE) == "legacy")
    ck_leg.setToolTip(T("Kapalı: ışıklar sahneye eklediğiniz ışık nesnelerinden gelir (ışık nesnesi yoksa aşağıdaki varsayılan ışıklar kullanılır). "
                        "Açık: makro dönemindeki gibi paneldeki üç ışık (veya FreeCAD'in ışık ayarları) kullanılır. Eski belgelerde otomatik açılır."))
    lvb.addWidget(ck_leg)
    lb_lobj = QtGui.QLabel("")
    lb_lobj.setWordWrap(True)
    lb_lobj.setStyleSheet("color: #85858a;")
    lvb.addWidget(lb_lobj)
    ck_fc = QtGui.QCheckBox(T("FreeCAD ışık ayarlarını kullan"))
    ck_fc.setChecked(bool(getattr(S, "USE_FC_LIGHTS", False)))
    ck_fc.setToolTip(T("İşaretliyse Tercihler > Display > Light Sources ayarları kullanılır; aşağıdaki ayarlar devre dışı kalır"))
    lvb.addWidget(ck_fc)
    lbl_envwarn = QtGui.QLabel(T("⚠ Ortam / HDRI açık: aşağıdaki ışıklar ve ortam ışığı kullanılmıyor. "
                                 "Birlikte kullanmak için Ortam / HDRI bölümünde 'Sahne ışıkları kalsın' kutusunu işaretleyin."))
    lbl_envwarn.setWordWrap(True)
    lbl_envwarn.setStyleSheet("color: #e0a040;")
    lbl_envwarn.setVisible(False)
    lvb.addWidget(lbl_envwarn)
    cb_pre = QtGui.QComboBox()
    for p in _lp.PRESETS:
        cb_pre.addItem(T(p[1]))
        cb_pre.setItemData(cb_pre.count() - 1, T(p[5]), QtCore.Qt.ToolTipRole)
    cb_pre.addItem(T("Özel"))
    cb_pre.setToolTip(T("Hazır aydınlatma düzenleri (stüdyo ve ürün çekimi). Ayrıntıları değiştirirseniz düzen 'Özel' olur."))
    prow = QtGui.QHBoxLayout()
    prow.addWidget(QtGui.QLabel(T("Aydınlatma")))
    prow.addWidget(cb_pre, 1)
    bt_cust = QtGui.QPushButton(T("Özel ayarlar") + " \u25b8")
    bt_cust.setCheckable(True)
    bt_cust.setToolTip(T("Üç ışığın açı, renk, güç ve gölge ayarlarını göster / gizle"))
    prow.addWidget(bt_cust)
    lvb.addLayout(prow)
    cust = QtGui.QWidget()
    cust_l = QtGui.QVBoxLayout(cust)
    cust_l.setContentsMargins(0, 0, 0, 0)
    lvb.addWidget(cust)
    cust.setVisible(False)
    lrows = []
    names = (T("Ana ışık"), T("Dolgu ışığı"), T("Arka ışık"))
    for i in range(3):
        lt = S.LIGHTS[i] if i < len(S.LIGHTS) else dict(on=False, h=0.0, v=0.0, color=(1, 1, 1), power=0.3, shadow=False)
        frame = QtGui.QFrame()
        frame.setFrameShape(QtGui.QFrame.StyledPanel)
        fg = QtGui.QGridLayout(frame)
        fg.setContentsMargins(6, 4, 6, 4)
        ck = QtGui.QCheckBox(names[i])
        ck.setChecked(bool(lt["on"]))
        cbn = color_button("l%d" % i, lt["color"])
        sp_ = spin(0, 200, float(lt["power"]) * 100, 5, " %")
        sp_.setToolTip(T("Işık gücü"))
        sh = spin(-180, 180, lt["h"], 5, " °")
        sh.setToolTip(T("Yatay açı (kameraya göre; eksi = sol, artı = sağ, 180 = arkadan)"))
        sv = spin(-90, 90, lt["v"], 5, " °")
        sv.setToolTip(T("Dikey açı (artı = yukarıdan)"))
        cs = QtGui.QCheckBox(T("Gölge"))
        cs.setChecked(bool(lt["shadow"]))
        cs.setToolTip(T("Bu ışık gölge düşürsün (her gölgeli ışık süreyi artırır)"))
        fg.addWidget(ck, 0, 0, 1, 2)
        fg.addWidget(cbn, 0, 2)
        fg.addWidget(sp_, 0, 3)
        fg.addWidget(QtGui.QLabel(T("Yatay")), 1, 0)
        fg.addWidget(sh, 1, 1)
        fg.addWidget(QtGui.QLabel(T("Dikey")), 1, 2)
        fg.addWidget(sv, 1, 3)
        fg.addWidget(cs, 2, 0, 1, 4)
        cust_l.addWidget(frame)
        lrows.append((ck, sh, sv, cbn, sp_, cs))
    arow = QtGui.QHBoxLayout()
    arow.addWidget(QtGui.QLabel(T("Ortam ışığı")))
    cb_amb = color_button("amb", S.AMBIENT_COLOR)
    sp_amb = spin(0, 100, S.AMBIENT * 100, 5, " %")
    arow.addWidget(cb_amb)
    arow.addWidget(sp_amb)
    lvb.addLayout(arow)
    # ---- Efektler (Render sekmesi): AO, GI, yumuşak gölge, ton eşleme, Fresnel ----
    xbox = QtGui.QGroupBox(T("Efektler"))
    xg = QtGui.QGridLayout(xbox)
    xrows = []                                        # (sol: etiket metni ya da onay kutusu, sol alan, sağ etiket, sağ alan)
    ck_ao = QtGui.QCheckBox(T("Ortam tıkanıklığı (AO)"))
    _pre_busy = [False]

    def to_custom(*_a):
        if not _pre_busy[0] and cb_pre.currentIndex() != len(_lp.PRESETS):
            cb_pre.blockSignals(True)
            cb_pre.setCurrentIndex(len(_lp.PRESETS))
            cb_pre.blockSignals(False)
            if not bt_cust.isChecked():
                pass

    def on_cust(on):
        cust.setVisible(on)
        bt_cust.setText(T("Özel ayarlar") + (" \u25be" if on else " \u25b8"))
        QtCore.QTimer.singleShot(0, fix_left_height)
    bt_cust.toggled.connect(on_cust)

    def set_color(key, rgb, btn):
        colors[key] = tuple(rgb)
        btn._paint()

    def apply_preset(i):
        if i >= len(_lp.PRESETS):
            bt_cust.setChecked(True)
            return
        _k, _n, lts, amb, ambc, _d = _lp.get(i)
        _pre_busy[0] = True
        try:
            for j, r in enumerate(lrows):
                lt = lts[j]
                r[0].setChecked(bool(lt["on"]))
                r[1].setValue(lt["h"])
                r[2].setValue(lt["v"])
                set_color("l%d" % j, lt["color"], r[3])
                r[4].setValue(int(round(lt["power"] * 100)))
                r[5].setChecked(bool(lt["shadow"]))
            sp_amb.setValue(int(round(amb * 100)))
            set_color("amb", ambc, cb_amb)
        finally:
            _pre_busy[0] = False
    cb_pre.currentIndexChanged.connect(apply_preset)
    for r in lrows:
        r[0].toggled.connect(to_custom)
        for wd in (r[1], r[2], r[4]):
            wd.valueChanged.connect(to_custom)
        r[5].toggled.connect(to_custom)
        r[3]._after.append(to_custom)
    sp_amb.valueChanged.connect(to_custom)
    cb_amb._after.append(to_custom)
    # başlangıç: ayarlar hangi düzene uyuyorsa onu seç, hiçbirine uymuyorsa 'Özel'
    _cur = None
    for _i, _p in enumerate(_lp.PRESETS):
        _same = abs(_p[3] - S.AMBIENT) < 1e-6 and all(
            _p[2][j]["on"] == bool(S.LIGHTS[j]["on"]) and abs(_p[2][j]["power"] - S.LIGHTS[j]["power"]) < 1e-6
            and abs(_p[2][j]["h"] - S.LIGHTS[j]["h"]) < 1e-6 and abs(_p[2][j]["v"] - S.LIGHTS[j]["v"]) < 1e-6
            for j in range(min(3, len(S.LIGHTS))))
        if _same:
            _cur = _i
            break
    cb_pre.blockSignals(True)
    cb_pre.setCurrentIndex(_cur if _cur is not None else len(_lp.PRESETS))
    cb_pre.blockSignals(False)
    ck_ao.setChecked(bool(S.AO))
    ck_ao.setToolTip(T("Köşeler, girintiler ve nesnelerin zemine değdiği yerler ortam ışığını daha az alır (temas gölgesi). "
                       "Gerçekçilik katar; süreyi artırır. Ortam ışığı çok düşükse etkisi az görünür."))
    sp_aos = spin(0, 100, S.AO_STRENGTH * 100, 5, " %")
    sp_aos.setToolTip(T("AO şiddeti: ortam ışığının ne kadarının kısılacağı"))
    sp_aor = spin(1, 50, S.AO_RADIUS * 100, 1, " %")
    sp_aor.setToolTip(T("AO erişimi: sahne boyutunun yüzdesi (büyük = geniş, yumuşak gölgelenme)"))
    sp_aon = spin(2, 64, S.AO_SAMPLES, 2)
    sp_aon.setToolTip(T("Piksel başına AO ışını (çok = daha temiz, daha yavaş)"))
    xrows.append((ck_ao, None, None, sp_aos))
    xrows.append((T("AO erişimi"), sp_aor, T("Işın sayısı"), sp_aon))

    def sync_ao(*_a):
        for wd in (sp_aos, sp_aor, sp_aon):
            wd.setEnabled(ck_ao.isChecked())
    ck_ao.toggled.connect(sync_ao)
    sync_ao()
    ck_gi = QtGui.QCheckBox(T("Dolaylı ışık (GI)"))
    ck_gi.setChecked(bool(S.GI))
    ck_gi.setToolTip(T("Işık yüzeylerden bir kez sekip çevreyi aydınlatır: renk sızması, iç mekânda doğal dolgu. AO ışınlarını kullanır; süreyi belirgin artırır. "
                       "Açıkken ortam ışığını düşürmek gerekebilir."))
    sp_gis = spin(0, 300, S.GI_STRENGTH * 100, 10, " %")
    sp_gis.setToolTip(T("Dolaylı ışık şiddeti"))
    sp_gin = spin(8, 128, S.GI_SAMPLES, 8)
    sp_gin.setToolTip(T("Piksel başına dolaylı ışık ışını (çok = daha temiz, daha yavaş)"))
    sp_gia = spin(0, 100, S.GI_AMBIENT * 100, 10, " %")
    sp_gia.setToolTip(T("GI açıkken sabit ortam ışığının kalan oranı (dolaylı ışık onun yerini aldığı için çift sayım olmasın)"))
    ck_gic = QtGui.QCheckBox(T("Kapalı boşluk karartması"))
    ck_gic.setChecked(bool(getattr(S, "GI_CAVITY", True)))
    ck_gic.setToolTip(T("Yalnız deliklerden açık kapalı hacimlerde (oda içi) ortam ve gölgesiz dolgu ışığı kısılır. Kapalı odalarda duvarlar bulutlu ya da lekeli görünüyorsa kapatın"))
    xrows.append((ck_gi, None, None, sp_gis))
    xrows.append((T("Işın sayısı"), sp_gin, T("Ortam ışığı"), sp_gia))
    xrows.append((ck_gic, None, None, None))

    def sync_gi(*_a):
        for wd in (sp_gis, sp_gin, sp_gia, ck_gic):
            wd.setEnabled(ck_gi.isChecked())
    ck_gi.toggled.connect(sync_gi)
    sync_gi()
    ck_soft = QtGui.QCheckBox(T("Yumuşak gölge"))
    ck_soft.setChecked(float(S.SOFT_SHADOW) > 0.0)
    ck_soft.setToolTip(T("Gölge kenarları yumuşar (alan ışığı). Gölgeli her ışık için birkaç gölge ızgarası kurulur; küçük ve orta sahnelerde ucuzdur, çok büyük modelde atlanır."))
    sp_softr = spin(1, 30, S.SOFT_SHADOW if S.SOFT_SHADOW > 0 else 4, 1, " °")
    sp_softr.setToolTip(T("Işığın görünür boyutu (derece): güneş ~0,3°, softbox 5-15°. Büyük = daha geniş yarı gölge"))
    sp_softk = spin(10, 1000, int(round(float(S.SOFT_SCALE) * 100)), 10, " %")
    sp_softk.setToolTip(T("Yarı gölge genişlik çarpanı: 100 % = ışık açısına göre, 300 % = 3 kat daha geniş (daha büyük ışık kaynağı görünümü)"))
    xrows.append((ck_soft, sp_softr, None, sp_softk))
    ck_sf = QtGui.QCheckBox(T("Gölge süzgeci"))
    ck_sf.setChecked(bool(getattr(S, "SHADOW_FILTER", True)))
    ck_sf.setToolTip(T("Nokta/spot ışığın yumuşak gölgesindeki beneği (kırçıllaşma, koyu noktalar) siler: yalnız aynı yüzeydeki, benzer derinlik ve normaldeki komşu pikseller karıştırılır, kenarlar korunur."))
    sp_sfr = spin(0, 16, int(getattr(S, "SHADOW_FILTER_R", 0)), 1, " px")
    sp_sfr.setSpecialValueText(T("otomatik"))
    sp_sfr.setToolTip(T("Süzgeç yarıçapı (piksel). Otomatik = çözünürlüğe göre 2-5 px. Büyük değer daha pürüzsüz ama gölge kenarı daha yumuşak olur."))
    xrows.append((ck_sf, sp_sfr, None, None))
    ck_sf.toggled.connect(lambda on: sp_sfr.setEnabled(on))
    sp_sfr.setEnabled(ck_sf.isChecked())

    def sync_soft(*_a):
        sp_softr.setEnabled(ck_soft.isChecked())
        sp_softk.setEnabled(ck_soft.isChecked())
    ck_soft.toggled.connect(sync_soft)
    sync_soft()
    cb_tm = QtGui.QComboBox()
    for _t in (T("Yok (kırp)"), T("Yumuşak (Reinhard)"), T("Filmik (ACES)")):
        cb_tm.addItem(_t)
    cb_tm.setCurrentIndex({"none": 0, "soft": 1, "aces": 2}.get(str(S.TONEMAP), 0))
    cb_tm.setToolTip(T("Ton eşleme: çok parlak alanlar patlamak yerine yumuşakça sıkışır. Pozlamayı artırıp ton eşleme açmak daha sinematik görünüm verir."))
    sp_exp = spin(25, 400, S.EXPOSURE * 100, 5, " %")
    sp_exp.setToolTip(T("Pozlama: 100 % = değişiklik yok"))
    xrows.append((T("Ton eşleme"), cb_tm, T("Pozlama"), sp_exp))
    ck_fr = QtGui.QCheckBox(T("Fresnel yansıması"))
    ck_fr.setChecked(bool(S.FRESNEL))
    ck_fr.setToolTip(T("Parlak opak yüzeylerde (boya, plastik, seramik) yansıma, yüzeye eğik bakıldıkça güçlenir"))
    xrows.append((ck_fr, None, None, None))
    for r, (a_, f1, l2, f2) in enumerate(xrows):
        if isinstance(a_, str):
            xg.addWidget(QtGui.QLabel(a_), r, 0)
        else:
            xg.addWidget(a_, r, 0, 1, 1 if f1 is not None else 2)
        if f1 is not None:
            xg.addWidget(f1, r, 1)
            grid_fields.append(f1)
        if l2 is not None:
            xg.addWidget(QtGui.QLabel(l2), r, 2)
        if f2 is not None:
            xg.addWidget(f2, r, 3)
            grid_fields.append(f2)
    for g_ in (qg, xg):                               # iki bölümde aynı sütun genişlikleri: sağdaki kutular tek hizada
        g_.setColumnMinimumWidth(0, 118)
        g_.setColumnMinimumWidth(2, 112)
        g_.setColumnStretch(4, 1)                    # boşluk en sağda; kutular kaydırma çubuğunun altına taşmasın
    # ---- Ortam / HDRI ----
    ebox = QtGui.QGroupBox(T("Ortam / HDRI"))
    evb = QtGui.QVBoxLayout(ebox)
    env_state = {"file": str(S.ENV_FILE or "")}
    cb_env = QtGui.QComboBox()
    cb_env.addItems([T("Ortam: kapalı"), T("Ortam: hazır gökyüzü"), T("Ortam: HDRI dosyası")])
    cb_env.setCurrentIndex({"off": 0, "sky": 1, "hdri": 2}.get(str(S.ENV_MODE), 0))
    cb_env.setToolTip(T("Küresel ortam ışığı: gökyüzü/HDRI haritası arka planı, yansımaları ve sahnenin ışığını verir; haritadaki güneş gölgeli ışık olur. "
                        "Açıkken sahne ışıkları ve ortam ışığı yerine bu kullanılır."))
    btn_envf = QtGui.QPushButton(T("HDRI seç..."))
    lbl_envf = QtGui.QLabel(os.path.basename(env_state["file"]) if env_state["file"] else "")
    sp_ei = spin(10, 500, S.ENV_INTENSITY * 100, 10, " %")
    sp_ei.setToolTip(T("Ortam ışığı ve güneş şiddeti"))
    sp_er = spin(-180, 180, S.ENV_ROT, 5, " °")
    sp_er.setToolTip(T("Haritayı dikey eksen etrafında döndürür (güneşin yönü de döner)"))
    sp_ee = spin(1, 85, S.ENV_SUN_ELEV, 5, " °")
    sp_ee.setToolTip(T("Hazır gökyüzü: güneş yüksekliği"))
    sp_ea = spin(-180, 360, S.ENV_SUN_AZ, 5, " °")
    sp_ea.setToolTip(T("Hazır gökyüzü: güneşin yönü (0° = +X, 90° = +Y)"))
    sp_en = spin(4, 128, S.ENV_SAMPLES, 4)
    sp_en.setToolTip(T("Piksel başına gökyüzü görünürlük ışını (çok = daha temiz, daha yavaş; pencereden giren ışıkta daha çok gerekir)"))
    ck_ebg = QtGui.QCheckBox(T("Arka planda göster"))
    ck_ebg.setChecked(bool(S.ENV_BACKGROUND))
    ck_eli = QtGui.QCheckBox(T("Aydınlat"))
    ck_eli.setChecked(bool(S.ENV_LIGHT))
    ck_ere = QtGui.QCheckBox(T("Yansıt"))
    ck_ere.setChecked(bool(S.ENV_REFLECT))
    ck_esu = QtGui.QCheckBox(T("Güneşi ayır"))
    ck_esu.setChecked(bool(S.ENV_SUN))
    ck_ekl = QtGui.QCheckBox(T("Sahne ışıkları kalsın"))
    ck_ekl.setChecked(bool(S.ENV_KEEP_LIGHTS))
    ck_ekl.setToolTip(T("Sahne ışıkları ve 'Ortam ışığı' dolgu olarak ortamla birlikte çalışır (iç mekânda sıçrayan ışığın yerini tutar)"))
    er1 = QtGui.QHBoxLayout()
    er1.addWidget(cb_env, 1)
    er1.addWidget(btn_envf)
    evb.addLayout(er1)
    evb.addWidget(lbl_envf)
    cb_hd = QtGui.QComboBox()                                   # HDRI klasöründeki dosyalar (Klasörler bölümünden beslenir)
    cb_hd.setToolTip(T("HDRI klasöründeki dosyalar"))
    evb.addWidget(cb_hd)
    er2 = QtGui.QHBoxLayout()
    for t_, w_ in ((T("Şiddet"), sp_ei), (T("Dönüş"), sp_er), (T("Işın"), sp_en)):
        er2.addWidget(QtGui.QLabel(t_))
        er2.addWidget(w_)
    evb.addLayout(er2)
    er3 = QtGui.QHBoxLayout()
    for t_, w_ in ((T("Güneş yüksekliği"), sp_ee), (T("yönü"), sp_ea)):
        er3.addWidget(QtGui.QLabel(t_))
        er3.addWidget(w_)
    evb.addLayout(er3)
    er4 = QtGui.QHBoxLayout()
    for w_ in (ck_ebg, ck_eli, ck_ere):
        er4.addWidget(w_)
    evb.addLayout(er4)
    er5 = QtGui.QHBoxLayout()
    er5.addWidget(ck_esu)
    er5.addWidget(ck_ekl)
    evb.addLayout(er5)

    def sync_env(*_a):
        i_ = cb_env.currentIndex()
        on_ = i_ > 0
        btn_envf.setVisible(i_ == 2)
        lbl_envf.setVisible(i_ == 2)
        cb_hd.setVisible(i_ == 2 and cb_hd.count() > 1)
        for wd in (sp_ei, sp_er, sp_en, ck_ebg, ck_eli, ck_ere, ck_esu, ck_ekl):
            wd.setEnabled(on_)
        sp_ee.setEnabled(i_ == 1)
        sp_ea.setEnabled(i_ == 1)
        lbl_envwarn.setVisible(on_ and ck_eli.isChecked() and not ck_ekl.isChecked())   # ortam sahne ışıklarının yerini alıyor
        QtCore.QTimer.singleShot(0, lambda: fix_left_height())     # fix_left_height aşağıda tanımlı: adı geç çözülsün

    def pick_env():
        res = QtGui.QFileDialog.getOpenFileName(dlg, T("HDRI / ortam haritası seç"),
                                                _paths.hdri_dir() or os.path.dirname(env_state["file"]) or os.path.expanduser("~"),
                                                T("Ortam haritası (*.hdr *.pic *.png *.jpg *.jpeg)"))
        path = res[0] if isinstance(res, (tuple, list)) else res
        if path:
            env_state["file"] = path
            lbl_envf.setText(os.path.basename(path))
    btn_envf.clicked.connect(pick_env)
    cb_env.currentIndexChanged.connect(sync_env)
    ck_eli.toggled.connect(sync_env)
    ck_ekl.toggled.connect(sync_env)
    sync_env()
    lv.addWidget(lbox)
    lv.addWidget(xbox)
    lv.addWidget(ebox)
    legacy_widgets = [wd for row in lrows for wd in row] + [cb_pre, bt_cust]
    light_widgets = legacy_widgets + [cb_amb, sp_amb]

    # ---- Sanal Zemin ----
    fbox = QtGui.QGroupBox(T("Sanal Zemin"))
    fgl = QtGui.QGridLayout(fbox)
    ck_fl = QtGui.QCheckBox(T("Sanal zemin ekle"))
    ck_fl.setChecked(bool(S.FLOOR))
    ck_fl.setToolTip(T("Sahnenin en alt noktasına (+ ofset) yatay, sonsuz bir zemin koyar. FreeCAD'de Z yukarı kabul edilir."))
    cb_fl = color_button("floor", S.FLOOR_COLOR, T("Zemin rengi"))
    sp_fo = spin(0, 100, S.FLOOR_OPACITY * 100, 10, " %")
    sp_fo.setToolTip(T("Zemin opaklığı; 100 = opak, düşünce zemin saydamlaşır"))
    sp_fr = spin(0, 100, S.FLOOR_REFLECT * 100, 10, " %")
    sp_fr.setToolTip(T("Zemin yansıması (eğik bakışta güçlenir); 0 = yansımasız, en hızlı"))
    sp_fz = spin(-1000000, 1000000, S.FLOOR_OFFSET, 10, " mm")
    sp_fz.setToolTip(T("Zemin yüksekliği: sahnenin en alt z değerine göre ofset (eksi = aşağı, artı = yukarı)"))
    fgl.addWidget(ck_fl, 0, 0, 1, 4)
    fgl.addWidget(QtGui.QLabel(T("Renk")), 1, 0)
    fgl.addWidget(cb_fl, 1, 1)
    fgl.addWidget(QtGui.QLabel(T("Opaklık")), 1, 2)
    fgl.addWidget(sp_fo, 1, 3)
    fgl.addWidget(QtGui.QLabel(T("Yansıma")), 2, 0)
    fgl.addWidget(sp_fr, 2, 1)
    fgl.addWidget(QtGui.QLabel(T("Ofset")), 2, 2)
    fgl.addWidget(sp_fz, 2, 3)
    # doku seçimi: düz renk / hazır dokular / kendi görüntü dosyanız
    cb_ft = QtGui.QComboBox()
    cb_ft.addItem(T("Düz renk"), "")
    for k, name, _fn, _mode, _rel in _textures.PRESETS:
        cb_ft.addItem(T(name), k)
    cb_ft.addItem(T("Dosyadan…"), "__file__")
    cb_ft.setToolTip(T("Zemin dokusu. 'Dosyadan…' ile kendi PNG/JPG görüntünüzü seçebilirsiniz."))
    sp_fs = spin(0, 1000000, S.FLOOR_TEX_SCALE, 100, " mm")
    sp_fs.setSpecialValueText(T("Otomatik"))
    sp_fs.setToolTip(T("Dokunun zemindeki genişliği (mm). Otomatik = doku türüne göre sahne büyüklüğüyle orantılı"))
    ft_state = {"key": S.FLOOR_TEXTURE or "", "last": 0}
    for i in range(cb_ft.count()):
        if cb_ft.itemData(i) == ft_state["key"]:
            cb_ft.setCurrentIndex(i)
    fgl.addWidget(QtGui.QLabel(T("Doku")), 3, 0)
    fgl.addWidget(cb_ft, 3, 1)
    fgl.addWidget(QtGui.QLabel(T("Doku boyu")), 3, 2)
    fgl.addWidget(sp_fs, 3, 3)
    # Surface malzemesi: T düğmesi Malzemeler penceresini açar ve "Sanal zemin" satırını seçer
    lbl_fmat = QtGui.QLabel("")
    bt_fmat = QtGui.QPushButton("T")
    bt_fmat.setFixedSize(28, 18)
    bt_fmat.setStyleSheet("QPushButton { padding: 0px; font-size: 9px; font-weight: bold; }")
    bt_fmat.setToolTip(T("Zemine Surface malzemesi ata / düzenle (renk, katmanlı doku, bump, yansıma): Malzemeler penceresini açar"))

    def floor_mat_refresh():
        n_ = str(getattr(S, "FLOOR_MATERIAL", "") or "")
        lbl_fmat.setText((T(n_) if n_ in _mats.BUILTIN else n_) if n_ else T("Yok (renk / doku ayarları)"))
        bt_fmat.setStyleSheet("QPushButton { padding: 0px; font-size: 9px; font-weight: bold; %s}"
                              % ("background-color: #5b7fa6; color: white; " if n_ else ""))
    floor_mat_refresh()
    fmr = QtGui.QHBoxLayout()
    fmr.addWidget(lbl_fmat, 1)
    fmr.addWidget(bt_fmat)
    fgl.addWidget(QtGui.QLabel(T("Yüzey")), 4, 0)
    fgl.addLayout(fmr, 4, 1, 1, 3)
    lv.insertWidget(1, fbox)                                    # Kalite ile Işık arasına
    floor_widgets = [cb_fl, sp_fo, sp_fr, sp_fz, cb_ft, sp_fs, bt_fmat]

    # ---- Arka plan ve sis (Ortam sekmesi) ----
    hbox = QtGui.QGroupBox(T("Arka plan ve sis"))
    hgl = QtGui.QGridLayout(hbox)
    # gökyüzü renk geçişi (LightWave Gradient gibi): solda dikey şerit (altı ufuk, üstü tepe), sağda seçili anahtar
    from . import skygrad as _sg
    sg_busy = [False]

    def sg_sync_fields():
        k = sg.keys[sg.selected]
        sg_busy[0] = True
        try:
            colors["sky_key"] = tuple(k["color"])
            cb_sk._paint()
            sp_sk.setValue(int(round(k["pos"] * 90)))
            lb_sk.setText(T("Anahtar %d / %d") % (sg.selected + 1, len(sg.keys)))
        finally:
            sg_busy[0] = False
    sg = _sg.make_editor(QtGui, QtCore, S.SKY_GRADIENT or _sg.from_two(S.BG_TOP, S.BG_BOTTOM),
                         on_select=lambda: sg_sync_fields(), on_change=lambda: sg_sync_fields())
    sg.smooth = "linear" if str(S.SKY_SMOOTH) == "linear" else "hermite"
    sg.setToolTip(T("Gökyüzü renk geçişi: alt = ufuk, üst = tepe nokta. Tıkla = anahtar ekle, sürükle = taşı, sağ tık = sil."))
    cb_spre = QtGui.QComboBox()
    cb_spre.addItem(T("Hazır düzen..."))
    for _n, _k in _sg.PRESETS:
        cb_spre.addItem(T(_n))

    def on_spre(i):
        if i > 0:
            sg.set_keys(_sg.preset_keys(i - 1))
            cb_spre.blockSignals(True)
            cb_spre.setCurrentIndex(0)
            cb_spre.blockSignals(False)
    cb_spre.currentIndexChanged.connect(on_spre)
    lb_sk = QtGui.QLabel("")
    cb_sk = color_button("sky_key", (0, 0, 0), T("Anahtar rengi"))
    cb_sk._after.append(lambda: None if sg_busy[0] else sg.set_selected_color(colors["sky_key"]))
    sp_sk = spin(0, 90, 0, 1, " °")
    sp_sk.setToolTip(T("Anahtarın yüksekliği: 0° = ufuk, 90° = tepe nokta"))
    sp_sk.valueChanged.connect(lambda v: None if sg_busy[0] else sg.set_selected_pos(v / 90.0))
    cb_ssm = QtGui.QComboBox()
    cb_ssm.addItem(T("Yumuşak (Hermite)"))
    cb_ssm.addItem(T("Doğrusal"))
    cb_ssm.setCurrentIndex(1 if sg.smooth == "linear" else 0)

    def on_ssm(i):
        sg.smooth = "linear" if i == 1 else "hermite"
        sg.update()
    cb_ssm.currentIndexChanged.connect(on_ssm)
    bt_sprev = QtGui.QPushButton(T("Önceki"))
    bt_snext = QtGui.QPushButton(T("Sonraki"))

    def step_key(d):
        sg.selected = (sg.selected + d) % len(sg.keys)
        sg.update()
        sg_sync_fields()
    bt_sprev.clicked.connect(lambda: step_key(-1))
    bt_snext.clicked.connect(lambda: step_key(1))
    sp_hz = spin(0, 300, float(S.HAZE) * 100, 10, " %")
    sp_hz.setToolTip(T("Ufuk sisi: sanal zemin uzakta ufkun biraz üstündeki gökyüzü rengine karışır (sert ufuk çizgisi ve uzak doku titreşimi gizlenir). "
                       "0 = kapalı, 100 = varsayılan, büyük = daha yoğun sis."))
    sp_skl = spin(0, 300, float(getattr(S, "SKY_LIGHT", 0.0)) * 100, 10, " %")
    sp_skl.setToolTip(T("Gökyüzü ışığı: HDRI yokken gökyüzü renk geçişi sahneyi de aydınlatır (yukarı bakan yüzler üst rengi, yanlar ufuk rengini alır; "
                        "nesnelerin altı ve köşeler doğal kararır). Sahne ışıklarına ve ortam ışığına eklenir. 0 = kapalı."))
    sgr = QtGui.QGridLayout()
    sgr.addWidget(cb_spre, 0, 0, 1, 2)
    sgr.addWidget(lb_sk, 1, 0, 1, 2)
    sgr.addWidget(QtGui.QLabel(T("Renk")), 2, 0)
    sgr.addWidget(cb_sk, 2, 1)
    sgr.addWidget(QtGui.QLabel(T("Yükseklik")), 3, 0)
    sgr.addWidget(sp_sk, 3, 1)
    sgr.addWidget(QtGui.QLabel(T("Geçiş")), 4, 0)
    sgr.addWidget(cb_ssm, 4, 1)
    sgr.addWidget(bt_sprev, 5, 0)
    sgr.addWidget(bt_snext, 5, 1)
    sgr.addWidget(QtGui.QLabel(T("Ufuk sisi")), 6, 0)
    sgr.addWidget(sp_hz, 6, 1)
    sgr.addWidget(QtGui.QLabel(T("Gökyüzü ışığı")), 7, 0)
    sgr.addWidget(sp_skl, 7, 1)
    hgl.addWidget(sg, 0, 0)
    hgl.addLayout(sgr, 0, 1)
    hgl.setColumnStretch(1, 1)
    sg_sync_fields()
    lv.insertWidget(2, hbox)

    def sync_floor(*_a):
        on = ck_fl.isChecked()
        for wd in floor_widgets:
            wd.setEnabled(on)
        cb_fl.setEnabled(on and not ft_state["key"])             # doku seçiliyken renk kullanılmaz
        sp_fs.setEnabled(on and bool(ft_state["key"]))

    def on_tex(idx):
        data = cb_ft.itemData(idx)
        if data == "__file__":
            res = QtGui.QFileDialog.getOpenFileName(dlg, T("Zemin dokusu seç"), _paths.texture_dir() or os.path.expanduser("~"),
                                                    T("Görüntü (*.png *.jpg *.jpeg *.bmp)"))
            path = res[0] if isinstance(res, (tuple, list)) else res
            img = QtGui.QImage(path) if path else None
            if not path or img is None or img.isNull():
                cb_ft.blockSignals(True)
                cb_ft.setCurrentIndex(ft_state["last"])           # vazgeçildi: öncekine dön
                cb_ft.blockSignals(False)
                return
            if max(img.width(), img.height()) > 1024:             # bellek/hız: en çok 1024 px
                img = img.scaled(1024, 1024, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            img = img.convertToFormat(QtGui.QImage.Format_RGB888)
            w_, h_ = img.width(), img.height()
            bpl = img.bytesPerLine()
            buf = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)[:bpl * h_].reshape(h_, bpl)[:, :w_ * 3]
            _textures.register_custom(path, buf.reshape(h_, w_, 3), "mirror")
            cb_ft.blockSignals(True)
            cb_ft.setItemText(cb_ft.count() - 1, T("Dosya: ") + os.path.basename(path))
            cb_ft.setItemData(cb_ft.count() - 1, path)
            cb_ft.blockSignals(False)
            ft_state["key"] = path
        else:
            ft_state["key"] = data or ""
        ft_state["last"] = cb_ft.currentIndex()
        sync_floor()
    cb_ft.currentIndexChanged.connect(on_tex)
    ck_fl.toggled.connect(sync_floor)
    sync_floor()

    # ---- içerik klasörü listeleri: HDRI dosyaları ve doku görüntüleri (Seçenekler > Genel'deki klasörlerden) ----
    def fill_hdri_list():
        folder = _paths.hdri_dir()
        cb_hd.blockSignals(True)
        cb_hd.clear()
        cb_hd.addItem(T("HDRI klasöründen seç…"), "")
        for n_ in _paths.list_files(folder, _paths.HDRI_EXT):
            cb_hd.addItem(n_, os.path.join(folder, n_))
        cb_hd.setCurrentIndex(max(0, cb_hd.findData(env_state["file"])))
        cb_hd.blockSignals(False)
        sync_env()

    def on_hd(i_):
        data = cb_hd.itemData(i_)
        if data:
            env_state["file"] = data
            lbl_envf.setText(os.path.basename(data))
    cb_hd.currentIndexChanged.connect(on_hd)

    def fill_floor_tex_list():
        """Doku klasöründeki görüntüleri zemin dokusu listesine (hazır dokulardan sonra) ekler."""
        for i_ in range(cb_ft.count() - 1, -1, -1):
            if cb_ft.itemData(i_, QtCore.Qt.UserRole + 1):
                cb_ft.removeItem(i_)
        folder = _paths.texture_dir()
        pos = 1 + len(_textures.PRESETS)
        cb_ft.blockSignals(True)
        for n_ in _paths.list_files(folder, _paths.IMG_EXT):
            p_ = os.path.join(folder, n_)
            cb_ft.insertItem(pos, T("Klasör: ") + n_, p_)
            cb_ft.setItemData(pos, True, QtCore.Qt.UserRole + 1)
            pos += 1
        k_ = cb_ft.findData(ft_state["key"])
        if k_ >= 0:
            cb_ft.setCurrentIndex(k_)
        cb_ft.blockSignals(False)

    def refresh_content():
        fill_hdri_list()
        fill_floor_tex_list()
    refresh_content()

    def light_count_text():
        try:
            n_ = len(_lobj.active_lights(FreeCAD.ActiveDocument))
        except Exception:
            n_ = 0
        if ck_leg.isChecked():
            return T("Eski aydınlatma açık; ışık nesneleri (%d adet) kullanılmaz.") % n_ if n_ else T("Eski aydınlatma açık.")
        if n_:
            return T("Sahnede %d ışık nesnesi var; aşağıdaki varsayılan ışıklar kullanılmaz.") % n_
        return T("Sahnede ışık nesnesi yok: aşağıdaki varsayılan ışıklar kullanılır. Işık eklemek için araç çubuğundaki ışık düğmelerini kullanın.")

    def sync_lights(*_a):
        leg = ck_leg.isChecked()
        ck_fc.setEnabled(leg)
        for wd in legacy_widgets:
            wd.setEnabled(leg and not ck_fc.isChecked())
        for wd in (cb_amb, sp_amb):                       # ortam ışığı her iki modda da geçerli (yalnız FreeCAD ışıkları ayarı kendi değerini getirir)
            wd.setEnabled(not (leg and ck_fc.isChecked()))
        lb_lobj.setText(light_count_text())

    def refresh_light_mode():
        ck_leg.blockSignals(True)
        ck_leg.setChecked(str(S.LIGHT_MODE) == "legacy")
        ck_leg.blockSignals(False)
        sync_lights()
    ck_fc.toggled.connect(sync_lights)
    ck_leg.toggled.connect(sync_lights)
    sync_lights()

    # ---- düğmeler ----
    btn_r = QtGui.QPushButton("Render")
    btn_r.setStyleSheet("font-weight: bold;")
    btn_s = QtGui.QPushButton(T("Durdur"))
    btn_s.setEnabled(False)
    btn_k = QtGui.QPushButton(T("Farklı kaydet..."))
    btn_k.setEnabled(False)
    btn_y = QtGui.QPushButton(T("Sahneyi yenile"))
    btn_y.setToolTip(T("Model değiştiyse sahneyi bir sonraki Render'da yeniden okur"))
    b1 = QtGui.QHBoxLayout()
    b1.addWidget(btn_r)
    b1.addWidget(btn_s)
    b2 = QtGui.QHBoxLayout()
    b2.addWidget(btn_k)
    b2.addWidget(btn_y)
    btn_m = QtGui.QPushButton(T("Malzemeler (Surface)..."))
    btn_m.setToolTip(T("Nesnelere malzeme ata, malzeme kütüphanesini düzenle"))
    b3 = QtGui.QHBoxLayout()
    b3.addWidget(btn_m)
    def fix_left_height():
        """Sol panel içeriği, kaydırma alanında sıkışmasın: yeterli yüksekliği korur, taşarsa kaydırma çubuğu çıkar."""
        try:
            left.setMinimumHeight(0)
            lv.activate()
            left.setMinimumHeight(lv.sizeHint().height())
        except Exception:
            pass

    def collapsible(box, title, start_open=False):
        """Grup kutusunu, başlığına tıklayınca açılıp kapanan bölüme çevirir (yer kaplamasın)."""
        idx = lv.indexOf(box)
        lv.removeWidget(box)
        box.setTitle("")
        box.setStyleSheet("QGroupBox { border: none; margin-top: 0px; padding-top: 0px; }")
        wrap = QtGui.QWidget()
        wl = QtGui.QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        hb = QtGui.QToolButton()
        hb.setCheckable(True)
        hb.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        hb.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Fixed)
        hb.setStyleSheet("QToolButton { text-align: left; background-color: %s; border: 1px solid %s; border-radius: 3px; padding: 4px 8px; }"
                         "QToolButton:hover { background-color: %s; }" % (_theme.HEADER, _theme.BORDER, _theme.BTN))
        hb.setToolTip(T("Tıklayınca açılır / kapanır"))

        def upd(on):
            hb.setText(("\u25be  " if on else "\u25b8  ") + title)
            box.setVisible(on)
            QtCore.QTimer.singleShot(0, fix_left_height)
        hb.toggled.connect(upd)
        wl.addWidget(hb)
        wl.addWidget(box)
        hb.setChecked(start_open)
        upd(start_open)
        lv.insertWidget(idx, wrap)
        return hb

    # sekmeler (LightWave Render Globals gibi): bölümler alt alta değil yan yana durur, panel kısalır
    tabs_l = QtGui.QTabWidget()
    tabs_l.setDocumentMode(True)
    for _ttl, _boxes in ((T("Render"), ((qbox, T("Kalite Ayarları")), (xbox, T("Efektler")))),
                         (T("Işık"), ((lbox, None),)),
                         (T("Ortam"), ((ebox, T("Ortam / HDRI")), (fbox, T("Sanal Zemin")), (hbox, T("Arka plan ve sis"))))):
        _pg = QtGui.QWidget()
        _pl = QtGui.QVBoxLayout(_pg)
        _pl.setContentsMargins(0, 4, 0, 0)
        _pl.setSpacing(2)
        for _b, _h in _boxes:
            lv.removeWidget(_b)
            _b.setTitle("")
            _b.setStyleSheet("QGroupBox { border: none; margin-top: 0px; padding-top: 0px; }")
            if _h:                                    # aynı sekmedeki bölümler ince bir başlık satırıyla ayrılır
                _hl = QtGui.QLabel(_h)
                _hl.setStyleSheet("font-weight: bold; padding: 3px 6px; background-color: %s; border-radius: 3px;" % _theme.HEADER)
                _pl.addWidget(_hl)
            _pl.addWidget(_b)
        _pl.addStretch(1)
        tabs_l.addTab(_pg, _ttl)
    try:                                              # son açık sekme hatırlansın
        _tp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer")
        tabs_l.setCurrentIndex(max(0, min(2, int(_tp.GetInt("LeftTab", 0)))))
        tabs_l.currentChanged.connect(lambda i_: _tp.SetInt("LeftTab", int(i_)))
    except Exception:
        pass
    tabs_l.currentChanged.connect(lambda _i: QtCore.QTimer.singleShot(0, fix_left_height))
    lv.insertWidget(0, tabs_l)
    for _cb in left.findChildren(QtGui.QComboBox):                  # uzun girdili listeler (doku / HDRI dosya adları) paneli genişletmesin;
        _cb.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToMinimumContentsLengthWithIcon)   # yoksa başlık yazıları görünen alanın dışına kayar
        _cb.setMinimumContentsLength(12)
    lv.addStretch(1)

    # dar ayar paneli (geçmiş paneliyle birlikte sığsın): sayı/liste kutuları gereksiz yere genişlemesin
    _wide = (cb_pre, cb_env, cb_tm, cb_ft)
    for _w in left.findChildren(QtGui.QAbstractSpinBox) + left.findChildren(QtGui.QComboBox):
        _w.setMaximumWidth(200 if _w in _wide else 140)
    for _w in grid_fields:                            # Render sekmesinin değer kutuları: eşit genişlik, tek dikey hizada
        _w.setFixedWidth(98)
    cb_tm.setMinimumContentsLength(4)
    sc_left = QtGui.QScrollArea()                     # ekran küçükse sol panel dikeyde kaydırılır
    sc_left.setWidget(left)
    sc_left.setWidgetResizable(True)
    sc_left.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)  # kutular dar: yatay kaydırma çubuğu çıkmasın
    lcol = QtGui.QVBoxLayout()                        # üstte sabit: başlık + düğmeler, altta kaydırılabilir ayarlar
    head = QtGui.QLabel(T("Settings"))
    head.setStyleSheet("font-weight: bold; font-size: 14px;")
    cb_lang = QtGui.QComboBox()
    cb_lang.addItem("Türkçe", "tr")
    cb_lang.addItem("English", "en")
    cb_lang.setCurrentIndex(1 if i18n.LANG == "en" else 0)
    cb_lang.setToolTip("Dil / Language")

    def on_lang(_i):
        new = cb_lang.itemData(cb_lang.currentIndex())
        if new == i18n.LANG or state["busy"]:
            cb_lang.blockSignals(True)
            cb_lang.setCurrentIndex(1 if i18n.LANG == "en" else 0)
            cb_lang.blockSignals(False)
            return
        apply_all()                                   # ayarlar yeni pencereye taşınsın
        i18n.set_lang(new)
        QtCore.QTimer.singleShot(0, lambda: (dlg.close(), main(new)))
    cb_lang.currentIndexChanged.connect(on_lang)
    def rebuild():
        QtCore.QTimer.singleShot(0, lambda: (dlg.close(), main(i18n.LANG)))

    def open_options():
        """Seçenekler penceresi: Genel (içerik klasörü ve alt klasörleri, çıktı konumu) + Varsayılanlar (kayıtlı ayarlar)."""
        if state["busy"]:
            return
        apply_all()                                   # panelin şu anki değerleri settings'e alınsın
        dd = QtGui.QDialog(dlg)
        dd.setWindowTitle(T("Seçenekler"))
        _theme.apply(dd)
        dd.setMinimumWidth(560)
        tabs = QtGui.QTabWidget()
        outer_ = QtGui.QVBoxLayout(dd)
        outer_.addWidget(tabs)
        pg_gen = QtGui.QWidget()
        pg_def = QtGui.QWidget()
        tabs.addTab(pg_gen, T("Genel"))
        tabs.addTab(pg_def, T("Varsayılanlar"))
        vl = QtGui.QVBoxLayout(pg_def)
        info = QtGui.QLabel(T("Panelin şu anki değerlerini varsayılan olarak kaydedebilir, önceki kayıtlara dönebilirsiniz. "
                              "● işaretli kayıt, FreeCAD her açıldığında yüklenen varsayılandır."))
        info.setWordWrap(True)
        vl.addWidget(info)
        vl.addWidget(QtGui.QLabel(T("Mevcut ayarlar (değer sütununu düzenleyebilirsin):")))
        tbl = QtGui.QTableWidget()
        tbl.setColumnCount(2)
        tbl.setHorizontalHeaderLabels([T("Ayar"), T("Değer")])
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setMinimumHeight(230)
        trows = []                                    # (anahtar, öğe, orijinal metin)
        _keys = [k for k in _ud._keys() if k != "LIGHTS"]
        tbl.setRowCount(len(_keys) + 1)

        def _fmt(v):
            if isinstance(v, tuple):
                return ", ".join("%g" % x for x in v)
            return str(v)

        def fill_table():
            trows.clear()
            tbl.blockSignals(True)
            for r, k in enumerate(_keys):
                v = g[k]
                a = QtGui.QTableWidgetItem(k)
                a.setFlags(QtCore.Qt.ItemIsEnabled)
                tbl.setItem(r, 0, a)
                b = QtGui.QTableWidgetItem()
                if isinstance(v, bool):
                    b.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                    b.setCheckState(QtCore.Qt.Checked if v else QtCore.Qt.Unchecked)
                else:
                    b.setText(_fmt(v))
                tbl.setItem(r, 1, b)
                trows.append((k, b, _fmt(v)))
            r = len(_keys)
            a = QtGui.QTableWidgetItem("LIGHTS")
            a.setFlags(QtCore.Qt.ItemIsEnabled)
            tbl.setItem(r, 0, a)
            b = QtGui.QTableWidgetItem(T("%d ışık (Işık Ayarları bölümünden)") % len(g["LIGHTS"]))
            b.setFlags(QtCore.Qt.ItemIsEnabled)
            tbl.setItem(r, 1, b)
            tbl.blockSignals(False)
            tbl.resizeColumnToContents(0)

        def read_table():
            """Tabloda değiştirilen değerleri döndürür (geçersiz girişler atlanır)."""
            out = {}
            for k, it, orig in trows:
                cur = g[k]
                try:
                    if isinstance(cur, bool):
                        nv = it.checkState() == QtCore.Qt.Checked
                    else:
                        t = it.text().strip()
                        if t == orig:
                            continue
                        if isinstance(cur, int):
                            nv = int(float(t))
                        elif isinstance(cur, float):
                            nv = float(t)
                        elif isinstance(cur, tuple):
                            nv = tuple(float(x) for x in t.replace(";", ",").split(","))
                            if len(nv) != len(cur):
                                continue
                        else:
                            nv = t
                    if nv != cur:
                        out[k] = nv
                except Exception:
                    continue
            return out
        fill_table()
        vl.addWidget(tbl)
        row = QtGui.QHBoxLayout()
        ed = QtGui.QLineEdit()
        ed.setPlaceholderText(T("Kayıt adı (isteğe bağlı)"))
        b_save = QtGui.QPushButton(T("Şu anki ayarları varsayılan olarak kaydet"))
        b_save.setStyleSheet("font-weight: bold;")
        b_app = QtGui.QPushButton(T("Tablodaki değişiklikleri panele uygula"))
        b_app.setToolTip(T("Kaydetmeden, tabloda düzenlenen değerleri panele aktarır"))
        row.addWidget(ed, 1)
        row.addWidget(b_save)
        vl.addLayout(row)
        vl.addWidget(b_app)
        vl.addWidget(QtGui.QLabel(T("Geçmiş (en yeni üstte, son %d kayıt):") % _ud.MAX_HIST))
        lst = QtGui.QListWidget()
        lst.setMinimumHeight(190)
        vl.addWidget(lst)
        b_load = QtGui.QPushButton(T("Seçili kaydı panele yükle"))
        b_def = QtGui.QPushButton(T("Seçili kaydı varsayılan yap"))
        b_del = QtGui.QPushButton(T("Seçili kaydı sil"))
        b_fac = QtGui.QPushButton(T("Fabrika ayarlarını panele yükle"))
        b_off = QtGui.QPushButton(T("Kayıtlı varsayılanı kapat"))
        b_off.setToolTip(T("Kayıtlar silinmez; FreeCAD bir sonraki açılışta fabrika ayarlarıyla başlar"))
        b_cl = QtGui.QPushButton(T("Kapat"))
        g1 = QtGui.QHBoxLayout()
        g1.addWidget(b_load)
        g1.addWidget(b_def)
        g1.addWidget(b_del)
        g2 = QtGui.QHBoxLayout()
        g2.addWidget(b_fac)
        g2.addWidget(b_off)
        g2.addStretch(1)
        g2.addWidget(b_cl)
        vl.addLayout(g1)
        vl.addLayout(g2)
        st = QtGui.QLabel("")
        st.setStyleSheet("color: #85858a;")
        vl.addWidget(st)

        def refill():
            d = _ud.load()
            lst.clear()
            for i in reversed(range(len(d["hist"]))):
                h = d["hist"][i]
                it = QtGui.QListWidgetItem(("● " if i == d.get("current", -1) else "    ") + h.get("ts", "") +
                                           (("   –   " + h["name"]) if h.get("name") else ""))
                it.setData(QtCore.Qt.UserRole, i)
                lst.addItem(it)
            has = lst.count() > 0
            if has:
                lst.setCurrentRow(0)
            else:
                it = QtGui.QListWidgetItem(T("Henüz kayıt yok. Panelde ayarları yapıp yukarıdaki düğmeyle ilk kaydı oluştur."))
                it.setFlags(QtCore.Qt.NoItemFlags)
                lst.addItem(it)
            for b in (b_load, b_def, b_del):
                b.setEnabled(has)
            b_off.setEnabled(d.get("current", -1) >= 0)

        def sel():
            it = lst.currentItem()
            return None if it is None else it.data(QtCore.Qt.UserRole)

        def do_save_def():
            try:
                ch = read_table()
                if ch:
                    _ud.apply(ch)
                _ud.save_current(ed.text())
                ed.clear()
                st.setText(T("Kaydedildi: ") + _ud.path())
            except Exception as ex:
                st.setText(T("Kaydedilemedi: ") + str(ex))
            refill()

        def do_load():
            i = sel()
            if i is None:
                return
            _ud.apply(_ud.load()["hist"][i]["values"])
            dd.accept()
            rebuild()

        def do_make():
            i = sel()
            if i is not None:
                _ud.make_default(i)
                st.setText(T("Seçili kayıt varsayılan yapıldı (açılışta yüklenir)."))
                refill()

        def do_del():
            i = sel()
            if i is not None:
                _ud.delete(i)
                refill()

        def do_fac():
            _ud.apply(_ud.FACTORY)
            dd.accept()
            rebuild()

        def do_off():
            _ud.clear_default()
            st.setText(T("Kayıtlı varsayılan kapatıldı; sonraki açılışta fabrika ayarları kullanılır."))
            refill()
        def do_apply_tbl():
            ch = read_table()
            if not ch:
                st.setText(T("Tabloda değişiklik yok."))
                return
            _ud.apply(ch)
            dd.accept()
            rebuild()
        b_app.clicked.connect(do_apply_tbl)
        b_save.clicked.connect(do_save_def)
        b_load.clicked.connect(do_load)
        b_def.clicked.connect(do_make)
        b_del.clicked.connect(do_del)
        b_fac.clicked.connect(do_fac)
        b_off.clicked.connect(do_off)
        b_cl.clicked.connect(dd.accept)
        lst.itemDoubleClicked.connect(lambda _i: do_load())
        refill()

        # ---------------- Genel sekmesi: içerik klasörü (LightWave "Content Directory" gibi) ----------------
        gv = QtGui.QVBoxLayout(pg_gen)
        gtop = QtGui.QGridLayout()
        b_cd = QtGui.QPushButton(T("İçerik klasörü"))
        b_cd.setToolTip(T("Malzeme kütüphanesi, dokular, HDRI'ler, ayarlar ve render çıktıları bu klasörün alt klasörlerinde durur."))
        le_cd = QtGui.QLineEdit(_paths.content_dir())
        le_cd.setPlaceholderText(T("(ayarsız: eski saklama yerleri kullanılır)"))
        b_cx = QtGui.QPushButton(T("Temizle"))
        b_cx.setToolTip(T("İçerik klasörü ayarını kaldırır (klasördeki dosyalar silinmez)"))
        gtop.addWidget(b_cd, 0, 0)
        gtop.addWidget(le_cd, 0, 1)
        gtop.addWidget(b_cx, 0, 2)
        gv.addLayout(gtop)
        gv.addWidget(QtGui.QLabel(T("Alt klasörler (içerik klasörü seçilince otomatik oluşturulur):")))
        sub_rows = {}
        sgl = QtGui.QGridLayout()
        for r_, (nm_, lab_) in enumerate((("materials", T("Malzemeler")), ("textures", T("Dokular")), ("hdri", "HDRI"),
                                          ("settings", T("Ayarlar")), ("renders", T("Çıktılar")))):
            l1 = QtGui.QLabel(lab_)
            l2 = QtGui.QLabel("")
            l2.setStyleSheet("color: #85858a;")
            l2.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            b_op = QtGui.QPushButton(T("Aç"))
            b_op.setToolTip(T("Klasörü dosya yöneticisinde aç"))
            b_op.setFixedWidth(52)

            def _open(_c=False, n_=nm_):
                d_ = _paths.sub_dir(n_)
                if not d_ or not os.path.isdir(d_):
                    return
                try:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d_))
                except Exception:
                    st_g.setText(d_)
            b_op.clicked.connect(_open)
            sgl.addWidget(l1, r_, 0)
            sgl.addWidget(l2, r_, 1)
            sgl.addWidget(b_op, r_, 2)
            sub_rows[nm_] = (l2, b_op)
        # resim geçmişi içerik klasöründe değil, FreeCAD kullanıcı klasöründe durur (eklenti yönetir); yalnız yeri gösterilir
        from . import history as _hist_g
        try:
            hdir_ = _hist_g.folder()
        except Exception:
            hdir_ = ""
        l1h = QtGui.QLabel(T("Resim geçmişi"))
        l2h = QtGui.QLabel((hdir_ + "  " + T("(FreeCAD kullanıcı klasörü)")) if hdir_ else "")
        l2h.setStyleSheet("color: #85858a;")
        l2h.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        l2h.setToolTip(T("Son %d render burada saklanır; eklenti yönetir, içerik klasöründen bağımsızdır.") % _hist_g.MAX)
        b_oh = QtGui.QPushButton(T("Aç"))
        b_oh.setToolTip(T("Klasörü dosya yöneticisinde aç"))
        b_oh.setFixedWidth(52)
        b_oh.setEnabled(bool(hdir_) and os.path.isdir(hdir_))

        def _open_hist(_c=False):
            try:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(hdir_))
            except Exception:
                st_g.setText(hdir_)
        b_oh.clicked.connect(_open_hist)
        r_h = len(sub_rows)
        sgl.addWidget(l1h, r_h, 0)
        sgl.addWidget(l2h, r_h, 1)
        sgl.addWidget(b_oh, r_h, 2)
        sgl.setColumnStretch(1, 1)
        gv.addLayout(sgl)
        # sahne birimi: gizmo, kamera gövdesi ve yeni kamera simgesi bu boya göre (LightWave ızgara aralığı gibi)
        urow = QtGui.QHBoxLayout()
        urow.addWidget(QtGui.QLabel(T("Sahne birimi")))
        sp_unit = QtGui.QDoubleSpinBox()
        sp_unit.setRange(0.1, 1e6)
        sp_unit.setDecimals(1)
        sp_unit.setSuffix(" mm")
        sp_unit.setKeyboardTracking(False)
        sp_unit.setValue(_paths.scene_unit())
        sp_unit.setToolTip(T("Gizmo okları, kamera gövdesi, kamera bakış prizması ve ışık simgeleri bu boya göre ölçeklenir "
                             "(LightWave'deki ızgara aralığı gibi; kamera/ışığın SymbolSize değeri 0 = otomatik). "
                             "Büyük sahnede 1000 mm, küçük parçada birkaç on mm."))
        b_unit = QtGui.QPushButton(T("Modelden"))
        b_unit.setToolTip(T("Açık belgedeki modelin boyutuna göre öner (sınır kutusu köşegeninin ~1/8'i, yuvarlak sayı)"))

        def _unit_changed(v):
            _paths.set_scene_unit(v)
            try:
                from . import rig_gizmo as _rg
                _rg.refresh_symbols()
            except Exception:
                pass
        sp_unit.valueChanged.connect(_unit_changed)

        def _unit_from_model(_c=False):
            import FreeCAD
            u_ = _paths.unit_from_model(FreeCAD.ActiveDocument) if FreeCAD.ActiveDocument else None
            if u_:
                sp_unit.setValue(u_)
            else:
                st_g.setText(T("Açık belgede model yok."))
        b_unit.clicked.connect(_unit_from_model)
        urow.addWidget(sp_unit, 1)
        urow.addWidget(b_unit)
        gv.addLayout(urow)
        # hızlandırıcı: numba (isteğe bağlı, makineye özgü) - kurulu değilse tek tıkla kurulur
        from . import nbtrace as _nbt
        arow = QtGui.QHBoxLayout()
        arow.addWidget(QtGui.QLabel(T("Hızlandırıcı")))
        lb_acc = QtGui.QLabel("")
        lb_acc.setWordWrap(True)
        b_nb = QtGui.QPushButton(T("Numba'yı kur"))
        b_nb.setToolTip(T("numba'yı FreeCAD'in Python'una dokunmadan YBTRayTracer/pylibs klasörüne kurar (internet gerekir, ~45 MB). "
                          "Gölge, AO, GI ve yansıma ışınlarının araması derlenir; render genelde 2-3 kat hızlanır, görüntü aynı kalır. "
                          "Kurulmazsa her şey numpy ile çalışmaya devam eder."))

        def _acc_status():
            on_ = _nbt.available()
            lb_acc.setText(T("numba %s: hızlandırma açık") % _nbt._ST["ver"] if on_ else
                           T("numba kurulu değil: render numpy ile (yavaş). Kurarsanız genelde 2-3 kat hızlanır."))
            lb_acc.setStyleSheet("color: %s;" % ("#85858a" if on_ else "#e0b050"))
            b_nb.setVisible(not on_)

        def _nb_install(_c=False):
            import threading
            res_ = {}
            b_nb.setEnabled(False)
            lb_acc.setText(T("Kuruluyor... (ilk kez ~1 dk sürebilir)"))
            th_ = threading.Thread(target=lambda: res_.update(r=_nbt.install()), daemon=True)
            th_.start()
            tm_ = QtCore.QTimer(dd)

            def _poll():
                if th_.is_alive():
                    return
                tm_.stop()
                b_nb.setEnabled(True)
                ok_, out_ = res_.get("r", (False, ""))
                _acc_status()
                if not ok_ or not _nbt.available():
                    if ok_ and _nbt._ST.get("err"):               # pip başarılı ama import edilemedi: gerçek hatayı göster
                        st_g.setText(T("numba kuruldu ama yüklenemedi (FreeCAD'in Python/numpy sürümüyle uyumsuz olabilir):") + "\n" + _nbt._ST["err"][-400:])
                    else:
                        st_g.setText(T("numba kurulamadı:") + "\n" + (out_ or _nbt._ST["err"])[-400:])
            tm_.timeout.connect(_poll)
            tm_.start(300)
        b_nb.clicked.connect(_nb_install)
        arow.addWidget(lb_acc, 1)
        arow.addWidget(b_nb)
        gv.addLayout(arow)
        # GPU (isteğe bağlı OpenCL, makineye özgü, belgeye kaydedilmez): numba satırının altında
        from . import clgpu as _clg
        grow_ = QtGui.QHBoxLayout()
        grow_.addWidget(QtGui.QLabel(T("GPU")))
        cb_acc = QtGui.QComboBox()
        cb_acc.addItem(T("Kapalı (numba / numpy)"), "auto")
        cb_acc.addItem(T("GPU (OpenCL, 32 bit, hızlı)"), "gpu")
        cb_acc.addItem(T("GPU (OpenCL, 64 bit, CPU ile birebir aynı)"), "gpu64")
        cb_acc.setCurrentIndex(max(0, cb_acc.findData(_clg.choice())))
        cb_acc.setToolTip(T("Gölge, AO, GI ve yansıma ışınlarının sahne araması. GPU ayrı ekran kartında belirgin hızlıdır; "
                            "zayıf tümleşik grafikte numba'dan yavaş olabilir. GPU bulunamaz ya da hata verirse otomatik moda döner. "
                            "32 bit gölge kenarında tek tük piksel farkı verebilir; 64 bit birebir aynıdır ama oyun kartlarında yavaştır."))
        b_acc = QtGui.QPushButton(T("GPU desteğini kur"))
        b_acc.setToolTip(T("pyopencl'i FreeCAD'in Python'una dokunmadan YBTRayTracer/pylibs klasörüne kurar (internet gerekir). "
                           "Ekran kartı sürücüsünün OpenCL desteği ayrıca gerekir."))
        lb_gpu = QtGui.QLabel("")
        lb_gpu.setWordWrap(True)
        lb_gpu.setStyleSheet("color: #85858a;")

        def _gpu_status():
            try:
                lb_gpu.setText(_clg.status())
            except Exception as e:
                lb_gpu.setText("OpenCL: %s" % e)

        def _acc_changed(_i):
            _clg.set_choice(cb_acc.currentData())
            _gpu_status()
        cb_acc.currentIndexChanged.connect(_acc_changed)

        def _acc_install(_c=False):
            lb_gpu.setText(T("Kuruluyor..."))
            QtGui.QApplication.processEvents()
            ok_, out_ = _clg.install()
            _gpu_status()
            if not ok_:
                st_g.setText(out_[-400:])
        b_acc.clicked.connect(_acc_install)
        grow_.addWidget(cb_acc, 1)
        grow_.addWidget(b_acc)
        gv.addLayout(grow_)
        gv.addWidget(lb_gpu)
        _gpu_status()
        _acc_status()
        orow = QtGui.QHBoxLayout()
        orow.addWidget(QtGui.QLabel(T("Render çıktıları")))
        cb_out = QtGui.QComboBox()
        cb_out.addItem(T("İçerik klasörü / renders"), "renders")
        cb_out.addItem(T("Belgenin yanı"), "document")
        cb_out.setCurrentIndex(0 if str(S.OUT_TO) == "renders" else 1)
        cb_out.setToolTip(T("Otomatik kaydedilen render görüntülerinin yeri. İçerik klasörü ayarsızsa her zaman belgenin yanı."))
        orow.addWidget(cb_out, 1)
        gv.addLayout(orow)
        gv.addStretch(1)
        st_g = QtGui.QLabel("")
        st_g.setWordWrap(True)
        st_g.setStyleSheet("color: #85858a;")
        gv.addWidget(st_g)
        gclose = QtGui.QPushButton(T("Kapat"))
        gclose.clicked.connect(dd.accept)
        grow = QtGui.QHBoxLayout()
        grow.addStretch(1)
        grow.addWidget(gclose)
        gv.addLayout(grow)
        changed_ = {"content": False}

        def refresh_sub_rows():
            on_ = bool(_paths.content_dir())
            for nm_, (l2, b_op) in sub_rows.items():
                d_ = _paths.sub_dir(nm_)
                l2.setText((("\u2713 " if os.path.isdir(d_) else "\u2013 ") + d_) if on_ else T("(içerik klasörü ayarsız)"))
                b_op.setEnabled(on_ and os.path.isdir(d_))
            cb_out.setEnabled(on_)
            b_cx.setEnabled(on_)

        def set_content(path_):
            path_ = path_.strip()
            try:
                _paths.set_content_dir(path_)
            except OSError as ex:
                st_g.setText(T("Klasör oluşturulamadı: ") + str(ex))
                le_cd.setText(_paths.content_dir())
                return
            msg_ = ""
            if path_:
                copied_ = _paths.migrate_to_content()
                msg_ = T("İçerik klasörü ayarlandı.")
                if copied_:
                    msg_ += " " + T("Eski yerden kopyalandı (eskiler silinmedi): ") + ", ".join(copied_)
            else:
                msg_ = T("İçerik klasörü ayarı kaldırıldı; eski saklama yerleri kullanılıyor.")
            le_cd.setText(_paths.content_dir())
            st_g.setText(msg_)
            changed_["content"] = True
            refresh_sub_rows()
            refill()                                   # Varsayılanlar sekmesinin geçmişi yeni yerden okunur

        def browse_cd():
            cur_ = _paths.content_dir()
            p_ = QtGui.QFileDialog.getExistingDirectory(dd, T("İçerik klasörü"), cur_ if os.path.isdir(cur_) else os.path.expanduser("~"))
            if p_:
                set_content(p_)
        b_cd.clicked.connect(browse_cd)
        le_cd.editingFinished.connect(lambda: set_content(le_cd.text()) if le_cd.text().strip() != _paths.content_dir() else None)
        b_cx.clicked.connect(lambda: set_content(""))

        def on_out(_i):
            g["OUT_TO"] = cb_out.currentData()
        cb_out.currentIndexChanged.connect(on_out)
        refresh_sub_rows()
        dd.finished.connect(lambda *_a: refresh_content() if changed_["content"] else None)   # panelin HDRI / doku listeleri yenilenir

        _theme.fix_combos(dd)
        (dd.exec_ if hasattr(dd, "exec_") else dd.exec)()

    btn_def = QtGui.QPushButton(T("⚙ Seçenekler…"))
    btn_def.setToolTip(T("İçerik klasörü ve alt klasörleri; varsayılan ayarları kaydet, geçmiş kayıtlara dön, fabrika ayarlarına dön"))
    btn_def.clicked.connect(open_options)
    btn_tb = QtGui.QPushButton()
    btn_tb.setToolTip(T("Render panelini açan düğmeyi her tezgâhta görünen ayrı bir araç çubuğuna ekler"))

    def refresh_tb_btn(added):
        btn_tb.setText(T("Araç çubuğundan kaldır") if added else T("Araç çubuğuna ekle"))

    def toggle_tb(*_a):
        from . import commands as _cmds
        refresh_tb_btn(_cmds.toggle_own_toolbar())
    btn_tb.clicked.connect(toggle_tb)
    from . import commands as _cmds0
    refresh_tb_btn(_cmds0.own_toolbar_added())
    hrow = QtGui.QHBoxLayout()
    hrow.addWidget(head)
    hrow.addStretch(1)
    hrow.addWidget(btn_def)
    hrow.addWidget(btn_tb)
    hrow.addWidget(QtGui.QLabel("Dil / Language:"))
    hrow.addWidget(cb_lang)
    lcol.addLayout(hrow)
    lcol.addLayout(b1)
    lcol.addLayout(b2)
    lcol.addLayout(b3)
    lcol.addWidget(sc_left, 1)
    lw = QtGui.QWidget()
    lw.setLayout(lcol)
    lw.setMinimumWidth(360)
    outer.setContentsMargins(6, 6, 6, 6)
    split = QtGui.QSplitter(QtCore.Qt.Horizontal)       # ayar paneli ile ön izleme arasındaki çubuk sürüklenebilir
    split.setChildrenCollapsible(False)
    split.addWidget(lw)

    # ================= SAĞ: Ön izleme ekranı (ortalanmış) =================
    right = QtGui.QVBoxLayout()
    title = QtGui.QLabel(T("Önizleme ve Seçim Ekranı"))
    title.setAlignment(QtCore.Qt.AlignCenter)
    title.setStyleSheet("font-weight: bold; font-size: 15px; color: #e8e8ea;")
    right.addWidget(title)
    vrow = QtGui.QHBoxLayout()
    btn_fit = QtGui.QPushButton(T("Otomatik ortala"))
    btn_fit.setToolTip(T("Görüntüyü pencereye sığdırıp ortalar (çift tıklama da aynı işi yapar)"))
    btn_11 = QtGui.QPushButton("1:1")
    btn_11.setToolTip(T("Gerçek boyut: görüntünün bir pikseli ekranda bir piksel"))
    lbl_zoom = QtGui.QLabel("")
    lbl_zoom.setMinimumWidth(52)
    lbl_hint = QtGui.QLabel(T("Tekerlek: yakınlaştır / uzaklaştır  ·  Sürükle: kaydır  ·  Tıkla: parçayı seç"))
    lbl_hint.setStyleSheet("color: #85858a;")
    vrow.addWidget(btn_fit)
    vrow.addWidget(btn_11)
    vrow.addWidget(lbl_zoom)
    vrow.addStretch(1)
    vrow.addWidget(lbl_hint)
    right.addLayout(vrow)

    class _Preview(QtGui.QWidget):
        """Önizleme/seçim ekranı: çerçeveli, fare tekerleğiyle yakınlaştırılır, sürüklenerek kaydırılır, tıklamayla nesne seçer.
        Görüntü yakınlaştırılınca (>= 2 kat) pikseller yumuşatılmadan gösterilir."""

        def __init__(self):
            QtGui.QWidget.__init__(self)
            self.pm = None
            self.on_click = None                      # f(u, v, ctrl): u,v = görüntü içinde 0..1 konum
            self.on_focus = None                      # f(u, v): Shift+tıklama = alan derinliği odak noktası
            self.on_view = None                       # f(ölçek): görünüm değişince
            self.scale = 1.0                          # ekran pikseli / görüntü pikseli
            self.ox = self.oy = 0.0                   # görüntünün sol-üst köşesi (widget koordinatı)
            self.fitted = True
            self._press = None
            self.setMinimumSize(320, 240)
            self.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Ignored)
            self.setMouseTracking(False)

        # --- geometri
        def _fit_scale(self):
            if self.pm is None:
                return 1.0
            return max(1e-3, min((self.width() - 8) / float(self.pm.width()), (self.height() - 8) / float(self.pm.height())))

        def fit(self):
            self.fitted = True
            self._apply_fit()
            self.update()

        def _apply_fit(self):
            if self.pm is None:
                return
            self.scale = self._fit_scale()
            self.ox = (self.width() - self.pm.width() * self.scale) / 2.0
            self.oy = (self.height() - self.pm.height() * self.scale) / 2.0
            self._notify()

        def actual_size(self):
            self._zoom_to(1.0, self.width() / 2.0, self.height() / 2.0)

        def _clamp(self):
            if self.pm is None:
                return
            iw, ih = self.pm.width() * self.scale, self.pm.height() * self.scale
            for attr, size, win in (("ox", iw, self.width()), ("oy", ih, self.height())):
                v = getattr(self, attr)
                if size <= win:
                    v = (win - size) / 2.0                      # küçükse ortala
                else:
                    v = min(0.0, max(win - size, v))            # büyükse kenar boşluk bırakma
                setattr(self, attr, v)

        def _notify(self):
            if self.on_view is not None:
                self.on_view(self.scale)

        def _zoom_to(self, new_scale, ax, ay):
            if self.pm is None:
                return
            fs = self._fit_scale()
            new_scale = min(max(new_scale, fs), 64.0)
            ix, iy = (ax - self.ox) / self.scale, (ay - self.oy) / self.scale     # imlecin altındaki görüntü noktası
            self.scale = new_scale
            self.ox, self.oy = ax - ix * new_scale, ay - iy * new_scale
            self._clamp()
            self.fitted = abs(new_scale - fs) < 1e-6
            self._notify()
            self.update()

        # --- içerik
        def set_pixmap_full(self, pm):
            first = self.pm is None or self.pm.size() != pm.size()
            self.pm = pm
            if first or self.fitted:
                self.fitted = True
                self._apply_fit()
            self.update()

        def pixmap(self):
            return self.pm

        def image_rect(self):
            return (self.ox, self.oy, self.pm.width() * self.scale, self.pm.height() * self.scale) if self.pm is not None else None

        def resizeEvent(self, ev):
            QtGui.QWidget.resizeEvent(self, ev)
            if self.fitted:
                self._apply_fit()
            else:
                self._clamp()

        def paintEvent(self, ev):
            p = QtGui.QPainter(self)
            p.fillRect(self.rect(), QtGui.QColor(_theme.PREVIEW_BG))
            if self.pm is None:
                p.setPen(QtGui.QColor(_theme.TEXT_DIS))
                p.drawText(self.rect(), QtCore.Qt.AlignCenter, T("Ön izleme ekranı"))
            else:
                p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, self.scale < 2.0)
                p.drawPixmap(QtCore.QRectF(self.ox, self.oy, self.pm.width() * self.scale, self.pm.height() * self.scale),
                             self.pm, QtCore.QRectF(self.pm.rect()))
            p.setPen(QtGui.QColor(_theme.PREVIEW_BORDER))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
            p.end()

        # --- fare
        def wheelEvent(self, ev):
            if self.pm is None:
                return
            try:
                pt = ev.position()
                ax, ay = pt.x(), pt.y()
            except AttributeError:
                ax, ay = ev.x(), ev.y()
            d = ev.angleDelta().y()
            if d:
                self._zoom_to(self.scale * (1.2 ** (d / 120.0)), ax, ay)
            ev.accept()

        def _pos(self, ev):
            try:
                pt = ev.position()
                return pt.x(), pt.y()
            except AttributeError:
                return float(ev.x()), float(ev.y())

        def mousePressEvent(self, ev):
            if ev.button() in (QtCore.Qt.LeftButton, QtCore.Qt.MiddleButton) and self.pm is not None:
                x, y = self._pos(ev)
                self._press = (ev.button(), x, y, self.ox, self.oy, False)
            QtGui.QWidget.mousePressEvent(self, ev)

        def mouseMoveEvent(self, ev):
            if self._press is None:
                return
            btn, x0, y0, ox0, oy0, moved = self._press
            x, y = self._pos(ev)
            if not moved and (abs(x - x0) + abs(y - y0)) < 5:
                return
            if not moved:
                self.setCursor(QtCore.Qt.ClosedHandCursor)
            self._press = (btn, x0, y0, ox0, oy0, True)
            self.ox, self.oy = ox0 + (x - x0), oy0 + (y - y0)
            self._clamp()
            self.fitted = False if self.scale > self._fit_scale() + 1e-6 else self.fitted
            self.update()

        def mouseReleaseEvent(self, ev):
            pr, self._press = self._press, None
            self.unsetCursor()
            QtGui.QWidget.mouseReleaseEvent(self, ev)
            if pr is None or pr[5] or pr[0] != QtCore.Qt.LeftButton or self.on_click is None or self.pm is None:
                return                                              # kaydırma ya da orta tuş: seçme yok
            x, y = self._pos(ev)
            u = (x - self.ox) / (self.pm.width() * self.scale)
            v = (y - self.oy) / (self.pm.height() * self.scale)
            if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
                if (ev.modifiers() & QtCore.Qt.ShiftModifier) and self.on_focus is not None:
                    self.on_focus(u, v)
                    return
                self.on_click(u, v, bool(ev.modifiers() & QtCore.Qt.ControlModifier))

        def mouseDoubleClickEvent(self, ev):
            if self.pm is not None:
                self.fit()

    lbl = _Preview()
    lbl.on_view = lambda sc_: lbl_zoom.setText("%d%%" % int(round(sc_ * 100)))
    btn_fit.clicked.connect(lambda *_a: lbl.fit())
    btn_11.clicked.connect(lambda *_a: lbl.actual_size())
    right.addWidget(lbl, 1)
    # ---- işlem çubuğu: ön izlemeyle aynı genişlikte; içinde yapılan işin adı, sağda yüzde ----
    pframe = QtGui.QFrame()
    pframe.setFrameShape(QtGui.QFrame.StyledPanel)
    ph = QtGui.QHBoxLayout(pframe)
    ph.setContentsMargins(2, 2, 6, 2)
    bar = QtGui.QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(True)
    bar.setAlignment(QtCore.Qt.AlignCenter)
    bar.setMinimumHeight(26)
    pct = QtGui.QLabel("")
    pct.setMinimumWidth(48)
    pct.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    ph.addWidget(bar, 1)
    ph.addWidget(pct)
    right.addWidget(pframe)

    class _St(object):
        """Eski 'etiket' arayüzü (setText/text) + çubuk ilerlemesi (frac: 0..1 ya da None)."""

        def __init__(self):
            self.t = ""

        def set(self, text, frac=None):
            self.t = text
            bar.setFormat(text.replace("%p", "").replace("%v", "").replace("%m", ""))
            bar.setToolTip(text)
            if frac is None:
                bar.setValue(0)
                pct.setText("")
            else:
                v = int(round(max(0.0, min(1.0, frac)) * 100))
                bar.setValue(v)
                pct.setText("%%%d" % v)

        def setText(self, text):                   # bitiş / hata / iptal mesajı: çubuk değerine dokunma
            self.t = text
            bar.setFormat(text.replace("%p", "").replace("%v", "").replace("%m", ""))
            bar.setToolTip(text)

        def text(self):
            return self.t

    lbl_st = _St()
    lbl_st.setText(T("Hazır. Ayarları seç, 3B görünümü çevir ve Render'a bas."))
    rw = QtGui.QWidget()
    rw.setLayout(right)
    split.addWidget(rw)

    # ================= EN SAĞ: Resim geçmişi (son 10 render, en yeni üstte) =================
    from . import history as _hist
    hw = QtGui.QWidget()
    hvl = QtGui.QVBoxLayout(hw)
    hvl.setContentsMargins(4, 0, 0, 0)
    htitle = QtGui.QLabel(T("Resim Geçmişi"))
    htitle.setAlignment(QtCore.Qt.AlignCenter)
    htitle.setStyleSheet("font-weight: bold; font-size: 15px; color: #e8e8ea;")
    hvl.addWidget(htitle)
    hlist = QtGui.QListWidget()
    hlist.setViewMode(QtGui.QListView.IconMode)               # küçük resim, adı altında
    hlist.setFlow(QtGui.QListView.TopToBottom)                 # alt alta
    hlist.setWrapping(False)
    hlist.setMovement(QtGui.QListView.Static)
    hlist.setResizeMode(QtGui.QListView.Adjust)
    hlist.setIconSize(QtCore.QSize(200, 120))
    hlist.setSpacing(6)
    hlist.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    hlist.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    hlist.setToolTip(T("Tıkla: önizlemede göster  ·  Sağ tık: farklı kaydet / resmi aç"))
    hvl.addWidget(hlist, 1)
    hw.setMinimumWidth(230)
    hw.setMaximumWidth(320)

    def hist_refill():
        hlist.blockSignals(True)
        try:
            hlist.clear()
            for it_ in _hist.load()["items"]:
                p_ = _hist.path_of(it_)
                pm_ = QtGui.QPixmap(p_)
                if pm_.isNull():
                    continue
                li = QtGui.QListWidgetItem(QtGui.QIcon(pm_.scaled(200, 120, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)),
                                           it_["name"])
                li.setData(QtCore.Qt.UserRole, p_)
                li.setToolTip("%s\n%s  |  %dx%d%s" % (it_["name"], it_.get("ts", ""), it_.get("w", 0), it_.get("h", 0),
                                                       ("\n" + it_["info"]) if it_.get("info") else ""))
                hlist.addItem(li)
            if hlist.count() and state.get("last_hist") == hlist.item(0).data(QtCore.Qt.UserRole):
                hlist.setCurrentRow(0)
        finally:
            hlist.blockSignals(False)

    def hist_show(li):
        """Geçmişteki resmi önizlemede göster (karşılaştırma için). Tıkla-seç yalnız son render'da çalışır."""
        if state["busy"] or li is None:
            return
        p_ = li.data(QtCore.Qt.UserRole)
        pm_ = QtGui.QPixmap(p_)
        if pm_.isNull():
            return
        lbl.set_pixmap_full(pm_)
        latest = p_ == state.get("last_hist")
        state["hist_view"] = not latest
        if latest:
            state["pick"] = state.get("pick_last")
        lbl_st.setText((T("Son render: %s") if latest else T("Geçmiş: %s")) % li.text())

    def hist_menu(pos):
        li = hlist.itemAt(pos)
        if li is None:
            return
        p_ = li.data(QtCore.Qt.UserRole)
        mnu = QtGui.QMenu(hlist)
        a_sv = mnu.addAction(T("Farklı kaydet..."))
        a_op = mnu.addAction(T("Resmi aç"))
        act = mnu.exec_(hlist.viewport().mapToGlobal(pos)) if hasattr(mnu, "exec_") else mnu.exec(hlist.viewport().mapToGlobal(pos))
        if act is a_sv:
            start = os.path.join(os.path.dirname(state["path"]) if state["path"] else os.path.expanduser("~"), li.text() + ".png")
            res = QtGui.QFileDialog.getSaveFileName(dlg, T("Render'ı kaydet"), start, "PNG (*.png)")
            dst = res[0] if isinstance(res, (tuple, list)) else res
            if dst:
                if not dst.lower().endswith(".png"):
                    dst += ".png"
                try:
                    import shutil
                    shutil.copyfile(p_, dst)
                    lbl_st.setText(T("kaydedildi: %s") % dst)
                except Exception as ex:
                    lbl_st.setText(T("KAYDEDİLEMEDİ (%s)") % ex)
        elif act is a_op:
            try:                                          # FreeCAD içinde yeni sekmede (Image tezgâhı)
                import ImageGui
                ImageGui.open(p_)
            except Exception:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p_))

    hlist.itemClicked.connect(hist_show)
    hlist.customContextMenuRequested.connect(hist_menu)
    split.addWidget(hw)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setStretchFactor(2, 0)
    outer.addWidget(split, 1)
    # pencere ~1480x820 açılır (1080p ekranı tam kaplamaz), ekran küçükse sığacak kadar küçülür; ekranın ortasına konur
    pw = max(400, left.sizeHint().width() + sc_left.verticalScrollBar().sizeHint().width() + 24)
    pw = min(pw, 470)
    hwid = 250
    try:
        try:
            avail = Gui.getMainWindow().screen().availableGeometry()
        except Exception:
            avail = QtGui.QApplication.desktop().availableGeometry(Gui.getMainWindow())
    except Exception:
        avail = None
    W, H = 1480, 820
    if avail is not None:
        W, H = min(W, avail.width() - 40), min(H, avail.height() - 40)
    dlg.resize(W, H)
    split.setSizes([pw, max(400, W - pw - hwid - 30), hwid])
    if avail is not None:
        dlg.move(avail.x() + (avail.width() - W) // 2, avail.y() + (avail.height() - H) // 2)

    def sync_enabled(*_a):
        gl = ck_gl.isChecked()
        ck_rr.setEnabled(gl)
        sp_bn.setEnabled(gl or ck_rf.isChecked())
    ck_gl.toggled.connect(sync_enabled)
    ck_rf.toggled.connect(sync_enabled)
    sync_enabled()

    state = {"scene": None, "key": None, "busy": False, "cancel": False, "last": 0.0,
             "out": None, "path": None, "pick": None, "pick_last": None, "last_hist": None, "hist_view": False, "meta": None}

    def alive():
        return dlg.isVisible() and not state["cancel"]

    def status(msg, force=False, frac=None):
        now = time.time()
        if force or now - state["last"] > 0.15:
            state["last"] = now
            lbl_st.set(msg, frac)
            QtCore.QCoreApplication.processEvents()
        if not alive():
            raise _Cancelled()

    def tick():
        QtCore.QCoreApplication.processEvents()
        return alive()

    def put(arr, w, h):
        a = np.ascontiguousarray(arr)
        qi = QtGui.QImage(a.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
        lbl.set_pixmap_full(QtGui.QPixmap.fromImage(qi))

    def show(img, rows, frac=None, label=T("ışınlar izleniyor")):
        h, w = img.shape[:2]
        put(np.clip(img * 255.0, 0, 255).astype(np.uint8), w, h)
        lbl_st.set(label, (rows / float(h)) if frac is None else frac)
        QtCore.QCoreApplication.processEvents()
        return alive()

    def busy(on):
        state["busy"] = on
        for wd in light_widgets + floor_widgets + [ck_fl, ck_fc, ck_leg, btn_r, btn_y, sp_w, cb_q, cb_aa, ck_sh, ck_gl, ck_sm, ck_rf, ck_rr, ck_cull, sp_bn, cb_th, ck_sv, ck_ao, ck_gi, cb_pre, bt_cust, ck_soft, sp_softr, sp_softk, ck_sf, sp_sfr, cb_tm, sp_exp, ck_fr, sg, cb_spre, cb_sk, sp_sk, cb_ssm, bt_sprev, bt_snext, sp_hz, sp_skl]:
            wd.setEnabled(not on)
        btn_s.setEnabled(on)
        if not on:
            sync_enabled()
            sync_lights()
            sync_floor()
            sync_soft()                                 # yumuşak gölge kapalıysa yarıçap/çarpan kutuları pasif kalsın
            sync_ao()
            btn_k.setEnabled(state["out"] is not None)

    def apply_basic():
        g["WIDTH"] = sp_w.value()
        g["TESS_FACTOR"] = _QUALITY[cb_q.currentIndex()][1]
        g["SHADOWS"] = ck_sh.isChecked()
        g["GLASS"] = ck_gl.isChecked()
        g["SMOOTH"] = ck_sm.isChecked()
        g["CULL_OFFSCREEN"] = ck_cull.isChecked()
        g["SOFT_SHADOW"] = float(sp_softr.value()) if ck_soft.isChecked() else 0.0
        g["SOFT_SCALE"] = sp_softk.value() / 100.0
        g["SHADOW_FILTER"] = ck_sf.isChecked()
        g["SHADOW_FILTER_R"] = int(sp_sfr.value())
        g["TONEMAP"] = ("none", "soft", "aces")[cb_tm.currentIndex()]
        g["EXPOSURE"] = sp_exp.value() / 100.0
        g["FRESNEL"] = ck_fr.isChecked()
        g["AO"] = ck_ao.isChecked()
        g["AO_STRENGTH"] = sp_aos.value() / 100.0
        g["AO_RADIUS"] = sp_aor.value() / 100.0
        g["AO_SAMPLES"] = sp_aon.value()
        g["GI"] = ck_gi.isChecked()
        g["GI_STRENGTH"] = sp_gis.value() / 100.0
        g["GI_SAMPLES"] = sp_gin.value()
        g["GI_AMBIENT"] = sp_gia.value() / 100.0
        g["GI_CAVITY"] = ck_gic.isChecked()
        g["ENV_MODE"] = ("off", "sky", "hdri")[cb_env.currentIndex()]
        g["ENV_FILE"] = env_state["file"]
        g["ENV_INTENSITY"] = sp_ei.value() / 100.0
        g["ENV_ROT"] = float(sp_er.value())
        g["ENV_SUN_ELEV"] = float(sp_ee.value())
        g["ENV_SUN_AZ"] = float(sp_ea.value())
        g["ENV_SAMPLES"] = sp_en.value()
        g["ENV_BACKGROUND"] = ck_ebg.isChecked()
        g["ENV_LIGHT"] = ck_eli.isChecked()
        g["ENV_REFLECT"] = ck_ere.isChecked()
        g["ENV_SUN"] = ck_esu.isChecked()
        g["ENV_KEEP_LIGHTS"] = ck_ekl.isChecked()
        g["REFLECTIONS"] = ck_rf.isChecked()
        g["REFRACTION"] = ck_rr.isChecked()
        g["BOUNCES"] = sp_bn.value()
        g["THREADS"] = _TH[cb_th.currentIndex()]
        g["FLOOR"] = ck_fl.isChecked()
        g["FLOOR_COLOR"] = colors["floor"]
        g["FLOOR_OPACITY"] = sp_fo.value() / 100.0
        g["FLOOR_REFLECT"] = sp_fr.value() / 100.0
        g["FLOOR_OFFSET"] = float(sp_fz.value())
        g["FLOOR_TEXTURE"] = ft_state["key"]
        g["FLOOR_TEX_SCALE"] = float(sp_fs.value())
        g["SKY_GRADIENT"] = [dict(k_) for k_ in sg.keys]
        g["SKY_SMOOTH"] = sg.smooth
        g["BG_BOTTOM"] = tuple(sg.keys[0]["color"])          # eski iki renkli alanlar (Godot köprüsü vb.) uç anahtarlardan
        g["BG_TOP"] = tuple(sg.keys[-1]["color"])
        g["HAZE"] = sp_hz.value() / 100.0
        g["SKY_LIGHT"] = sp_skl.value() / 100.0

    def apply_manual_lights():
        g["AMBIENT"] = sp_amb.value() / 100.0
        g["AMBIENT_COLOR"] = colors["amb"]
        g["LIGHTS"] = [dict(on=r[0].isChecked(), h=float(r[1].value()), v=float(r[2].value()),
                            color=colors["l%d" % i], power=r[4].value() / 100.0, shadow=r[5].isChecked())
                       for i, r in enumerate(lrows)]

    def apply_all():
        apply_basic()
        apply_manual_lights()
        g["AA"] = 0 if cb_aa.currentIndex() == 0 else cb_aa.currentIndex() + 1
        g["USE_FC_LIGHTS"] = ck_fc.isChecked()
        g["LIGHT_MODE"] = "legacy" if ck_leg.isChecked() else "objects"

    def do_render(*_a):
        if state["busy"]:
            return
        state["cancel"] = False
        busy(True)
        lbl.fit()
        t0 = time.time()
        try:
            if Gui.ActiveDocument is None:
                lbl_st.setText(T("Açık belge yok."))
                return
            d = Gui.ActiveDocument.Document
            view = Gui.ActiveDocument.ActiveView
            cam, aspect = read_camera(view)
            _co = _cam.active_camera(d)                         # sahnede etkin kamera nesnesi varsa render ondan alınır
            if _co is not None:
                cam, aspect = _cam.render_camera(_co)
                FreeCAD.Console.PrintMessage(T("RayTrace: kamera nesnesi kullanılıyor: %s\n") % _co.Label)
                if bool(_co.DepthOfField) and not cam["persp"]:
                    FreeCAD.Console.PrintWarning(T("RayTrace: alan derinliği yalnız perspektif kamerada çalışır (Projection = Perspective yapın).\n"))
            apply_basic()
            try:
                apply_all()
                _ds.save(d)                                     # ayarlar + malzeme tanımları belgeyle birlikte saklanır
            except Exception as ex:
                FreeCAD.Console.PrintWarning("RayTrace: doc save: %s\n" % ex)
            fc_info = ""
            fc = None
            if not ck_leg.isChecked():                          # yeni sistem: ışıklar ışık nesnelerinden (yoksa panelin varsayılanları)
                _m, _n, _u = _lobj.apply_to_settings(d)
                FreeCAD.Console.PrintMessage(T("RayTrace: %d ışık nesnesi kullanılıyor.\n") % _u if _u else T("RayTrace: ışık nesnesi yok; varsayılan ışıklar kullanılıyor.\n"))
            else:
                S._OBJ_LIGHTS = None
            if ck_leg.isChecked() and ck_fc.isChecked():
                try:
                    fc = read_freecad_lights(lambda m: FreeCAD.Console.PrintMessage("RayTrace: " + m + "\n"))
                except Exception as ex:
                    FreeCAD.Console.PrintWarning(T("RayTrace: FreeCAD ışık ayarları okunamadı (%s); kendi ayarlar kullanılıyor.\n") % ex)
            if fc is not None:
                g["LIGHTS"], g["AMBIENT"], g["AMBIENT_COLOR"], fc_info = fc
            else:
                apply_manual_lights()
            g["AA"] = 0 if cb_aa.currentIndex() == 0 else cb_aa.currentIndex() + 1
            g["USE_FC_LIGHTS"] = ck_fc.isChecked()
            w = int(S.WIDTH)
            h = max(1, int(round(w / aspect)))
            key = (d.Name, len(d.Objects), S.TESS_FACTOR, bool(S.CULL_OFFSCREEN),
                   tuple(sorted(o.Name for o in d.Objects if not _mats.included(o))))   # hariç tutulanlar (elle değişse de yakalanır)
            if S.CULL_OFFSCREEN:                          # kadraj dışı atma kameraya bağlıdır: kamera değişince sahne yenilenir
                key += (tuple(np.round(cam["pos"], 3)), tuple(np.round(cam["fwd"], 4)), tuple(np.round(cam["up"], 4)),
                        bool(cam["persp"]), round(float(cam["size"]), 5), round(float(aspect), 4))
            if state["scene"] is None or state["key"] != key:
                state["scene"], nobj = collect_scene(d, status, camera=cam, aspect=aspect)
                state["key"] = key
                FreeCAD.Console.PrintMessage(T("RayTrace: %d nesne, %d üçgen (hazırlık %.1f sn)\n")
                                             % (nobj, state["scene"].count, time.time() - t0))
            t1 = time.time()
            out = render(state["scene"], cam, w, h, shadows=S.SHADOWS, progress=show, tile=S.TILE,
                         tick=tick, stage=lambda s: status(s, True, None))
            if out is None:
                raise _Cancelled()
            state["out"] = out
            state["pick"] = (state["scene"], cam, w, h)     # tıkla-seç: gösterilen görüntüye ait sahne/kamera (sahne sonradan sıfırlansa da)
            state["pick_last"], state["hist_view"] = state["pick"], False
            put(out, w, h)
            doc_nm = os.path.splitext(os.path.basename(d.FileName))[0] if d.FileName else d.Label
            rt = time.time() - t1
            try:                                            # PNG'ye gömülen render ayarları (Description + tüm ayarlar JSON)
                state["meta"] = render_meta(doc_nm, {"Size": "%dx%d" % (w, h), "Triangles": state["scene"].count,
                                                     "Render time": "%.1f s" % rt,
                                                     "Lighting preset": "FreeCAD" if ck_fc.isChecked() else cb_pre.currentText()})
            except Exception as ex:
                state["meta"] = None
                FreeCAD.Console.PrintWarning("RayTrace: meta: %s\n" % ex)
            try:                                            # resim geçmişi (son 10, en yeni üstte)
                hi_ = _hist.add(out, doc_nm, T("%d üçgen | render %.1f sn") % (state["scene"].count, rt), state["meta"])
                state["last_hist"] = _hist.path_of(hi_)
                hist_refill()
            except Exception as ex:
                FreeCAD.Console.PrintWarning("RayTrace: history: %s\n" % ex)
            msg = ""
            if ck_sv.isChecked():
                path = pick_out_path(d.FileName)
                try:
                    write_png(path, out, state["meta"])
                    state["path"] = path
                    msg = T(" | kaydedildi: %s") % path
                except Exception as ex:
                    msg = T(" | KAYDEDİLEMEDİ (%s)") % ex
            dt = time.time() - t0
            lbl_st.set(T("bitti: %dx%d | %d üçgen | render %.1f sn | toplam %.1f sn")
                       % (w, h, state["scene"].count, time.time() - t1, dt), 1.0)
            bar.setToolTip((T("kaydedildi: %s") % state["path"]) if ck_sv.isChecked() and state["path"] else msg.strip(" |"))
            FreeCAD.Console.PrintMessage(T("RayTrace: render %.1f sn, toplam %.1f sn%s\n")
                                         % (time.time() - t1, dt, msg))
        except _Cancelled:
            lbl_st.setText(T("iptal edildi."))
            FreeCAD.Console.PrintMessage(T("RayTrace: iptal edildi.\n"))
        except Exception:
            import traceback
            FreeCAD.Console.PrintError(T("RayTrace hatası:\n") + traceback.format_exc())
            lbl_st.setText(T("HATA: Report View'a bak."))
        finally:
            busy(False)

    def do_stop(*_a):
        state["cancel"] = True

    def do_save(*_a):
        if state["out"] is None:
            return
        try:
            fn_ = FreeCAD.ActiveDocument.FileName if FreeCAD.ActiveDocument else ""
            start = pick_out_path(fn_) if _paths.out_dir(fn_) else (state["path"] or pick_out_path(fn_))
        except Exception:
            start = "render.png"
        res = QtGui.QFileDialog.getSaveFileName(dlg, T("Render'ı kaydet"), start, "PNG (*.png)")
        path = res[0] if isinstance(res, (tuple, list)) else res
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            write_png(path, state["out"], state.get("meta"))
            state["path"] = path
            lbl_st.setText(T("kaydedildi: %s") % path)
        except Exception as ex:
            lbl_st.setText(T("KAYDEDİLEMEDİ (%s)") % ex)

    def do_refresh(*_a):
        state["scene"] = None
        lbl_st.setText(T("Sahne bir sonraki Render'da yeniden okunacak."))

    btn_r.clicked.connect(do_render)
    btn_s.clicked.connect(do_stop)
    btn_k.clicked.connect(do_save)
    btn_y.clicked.connect(do_refresh)

    def open_mats(*_a):
        """Malzemeler penceresi zaten açıksa yalnızca öne getirir (kapatıp yeniden taramaz); değilse açar."""
        sd = getattr(FreeCAD, "_ybt_surface_dlg", None)
        try:
            if sd is not None and sd.isVisible():
                sd.raise_()
                sd.activateWindow()
                return
        except Exception:                                 # kapanmış (silinmiş) Qt nesnesi
            pass
        from .surface_ui import open_surface
        txt = btn_m.text()
        btn_m.setText(T("Açılıyor..."))
        btn_m.setEnabled(False)
        QtGui.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtGui.QApplication.processEvents()                # basılı/pasif görünüm hemen çizilsin
        try:
            open_surface(on_change=lambda: state.__setitem__("scene", None), is_busy=lambda: state["busy"])
        finally:
            QtGui.QApplication.restoreOverrideCursor()
            btn_m.setText(txt)
            btn_m.setEnabled(True)
    btn_m.clicked.connect(open_mats)

    def open_floor_mat(*_a):
        """Zeminin T düğmesi: Malzemeler penceresini aç, 'Sanal zemin' satırını seç."""
        if state["busy"]:
            return
        open_mats()
        sd = getattr(FreeCAD, "_ybt_surface_dlg", None)
        try:
            if sd is not None and "floor" in sd._ybt_api:
                sd._ybt_api["floor"]()
        except Exception:
            pass
    bt_fmat.clicked.connect(open_floor_mat)
    dlg._ybt_floor_refresh = floor_mat_refresh

    def on_preview_click(u, v, ctrl):
        """Önizlemede tıklanan nesneyi bul; Malzemeler penceresinde seç (boya kovası açıksa malzemeyi ata)."""
        if state["busy"]:
            return
        if state.get("hist_view"):
            lbl_st.setText(T("Geçmişteki bir resim gösteriliyor; parça seçmek için son render'a tıklayın ya da yeniden render alın."))
            return
        pk = state["pick"]
        if pk is None:
            lbl_st.setText(T("Önce bir render alın; sonra önizlemede parçaya tıklayabilirsiniz."))
            return
        sc, cam_, w_, h_ = pk
        try:
            from .pick import pick as _pick
            hit = _pick(sc, cam_, w_, h_, u * w_, v * h_)
        except Exception:
            import traceback
            FreeCAD.Console.PrintError(T("RayTrace hatası:\n") + traceback.format_exc())
            return
        if hit is None:
            lbl_st.setText(T("Tıklanan yerde nesne yok (arka plan ya da zemin)."))
            return
        sd = getattr(FreeCAD, "_ybt_surface_dlg", None)
        try:
            alive_ = sd is not None and sd.isVisible()
        except Exception:                             # pencere kapanmış (silinmiş Qt nesnesi)
            alive_ = False
        if not alive_:
            open_mats()
            sd = getattr(FreeCAD, "_ybt_surface_dlg", None)
        if sd is None:
            return
        sd._ybt_api["pick"](hit[0], ctrl)
        o = FreeCAD.ActiveDocument.getObject(hit[0]) if FreeCAD.ActiveDocument else None
        lbl_st.setText(T("Tıklanan: %s") % (o.Label if o is not None else hit[0]))
    lbl.on_click = on_preview_click

    def on_preview_focus(u, v):
        """Shift+tıklama: etkin kameranın odak mesafesini tıklanan noktaya ayarlar (alan derinliği)."""
        if state["busy"]:
            return
        pk = state["pick"]
        d_ = FreeCAD.ActiveDocument
        co = _cam.active_camera(d_) if d_ is not None else None
        if pk is None or co is None:
            lbl_st.setText(T("Odak seçmek için önce etkin bir kamera nesnesiyle render alın; sonra önizlemede Shift+tıklayın."))
            return
        from .pick import depth_at as _depth
        z = _depth(pk[0], pk[1], pk[2], pk[3], u * pk[2], v * pk[3])
        if z is None or z <= 0:
            lbl_st.setText(T("Tıklanan yerde yüzey yok (arka plan)."))
            return
        d_.openTransaction("Set focus")
        try:
            co.FocusObject = None
            co.FocusDistance = float(z)
            if not bool(co.DepthOfField):
                co.DepthOfField = True
        finally:
            d_.commitTransaction()
        lbl_st.setText(T("Odak mesafesi: %.0f mm (yeniden render alın)") % z)
    lbl.on_focus = on_preview_focus
    lbl.setToolTip(T("Render aldıktan sonra bir parçaya tıklayın: Malzemeler penceresinde seçilir (Ctrl: seçime ekle). Shift+tıklama: kameranın odak noktasını oraya ayarlar (alan derinliği)"))

    def _save_to_doc(*_a):
        try:
            apply_all()
            _ds.save(FreeCAD.ActiveDocument)
        except Exception:
            pass
    dlg.finished.connect(_save_to_doc)                          # pencere kapanırken de belgeye yaz
    FreeCAD._raytrace_dlg = dlg
    dlg._raytrace_handlers = (do_render, do_stop, do_save, do_refresh, status, tick, show)  # referansları canlı tut
    _theme.fix_combos(dlg)
    try:
        hist_refill()
    except Exception as ex:
        FreeCAD.Console.PrintWarning("RayTrace: history: %s\n" % ex)
    dlg.show()
    # test/otomasyon için dışarıdan tetikleme
    dlg._raytrace_api = dict(render=do_render, widgets=dict(width=sp_w, quality=cb_q, ao=ck_ao, soft=ck_soft, soft_radius=sp_softr, soft_scale=sp_softk, tonemap=cb_tm, exposure=sp_exp, fresnel=ck_fr, ao_strength=sp_aos, ao_radius=sp_aor, ao_samples=sp_aon, shadows=ck_sh, glass=ck_gl, smooth=ck_sm, cull=ck_cull, reflections=ck_rf,
                                                            refraction=ck_rr, bounces=sp_bn, autosave=ck_sv, aa=cb_aa, threads=cb_th, floor=ck_fl, floor_color=cb_fl, floor_opacity=sp_fo, floor_reflect=sp_fr, floor_offset=sp_fz, floor_texture=cb_ft, floor_tex_scale=sp_fs, lights=lrows, ambient=sp_amb, fclights=ck_fc, hdri_list=cb_hd, floor_tex=cb_ft),
                             status=lbl_st, bar=bar, pct=pct, state=state,
                             open_mats=open_mats, open_options=open_options, refresh_light_mode=refresh_light_mode, apply_all=apply_all,
                             reset_scene=lambda: state.__setitem__("scene", None))       # eklenti komutları (commands.py) bunlarla açar
