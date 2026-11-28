# -*- coding: utf-8 -*-
"""Görünüm (viewport) ayarları penceresi: "Görünümde Malzemeler" önizlemesinin makineye özgü anahtarları.

Buradaki ayarların hepsi KULLANICI PARAMETRESİDİR (Mod/FCYBTRayTracer): belgeye yazılmaz, render'ı değiştirmez,
bilgisayardan bilgisayara taşınmaz. Amaç, güçlü makinede her şeyi açık tutup zayıf makinede tek tek kısabilmek.

Render'ın kendi ayarları (AO açık mı, AO yarıçapı / şiddeti, yansıma, cam, pozlama...) Render panelinde kalır;
görünüm onları izler. Buradaki anahtarlar yalnız "görünümde de hesaplansın mı" sorusunu yanıtlar.
"""
from .i18n import T


def _dlg_parent():
    try:
        import FreeCADGui as Gui
        return Gui.getMainWindow()
    except Exception:
        return None


def open_dialog():
    """Ayar penceresini açar (modeless, tek kopya)."""
    import FreeCAD
    from PySide import QtGui, QtCore
    from . import viewmat as _vm
    from . import theme as _theme

    old = getattr(FreeCAD, "_ybt_viewsettings_dlg", None)
    if old is not None:
        try:
            old.show()
            old.raise_()
            old.activateWindow()
            return old
        except Exception:
            pass

    dlg = QtGui.QDialog(_dlg_parent())
    dlg.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
    dlg.setWindowTitle(T("YBT Görünüm Ayarları"))
    dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
    lay = QtGui.QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(8)

    head = QtGui.QLabel(T("3B görünümdeki önizleme için. Bu ayarlar bu bilgisayara özgüdür; belgeye ve render'a "
                          "yazılmaz."))
    head.setWordWrap(True)
    lay.addWidget(head)

    form = QtGui.QFormLayout()
    form.setSpacing(6)
    form.setLabelAlignment(QtCore.Qt.AlignLeft)

    # --- gölge
    sp_sh = QtGui.QSpinBox()
    sp_sh.setRange(0, _vm.MAX_SHADOW_LIGHTS)
    sp_sh.setValue(_vm.shadow_lights())
    sp_sh.setSuffix(T(" ışık"))
    sp_sh.setSpecialValueText(T("kapalı"))
    sp_sh.setToolTip(T("Görünümde gerçek zamanlı gölge veren en çok ışık (en güçlü ışıklar seçilir, kalanlar gölgesiz "
                       "aydınlatır). Her ışık bir gölge haritası demektir: zayıf ekran kartında azaltın. "
                       "Varsayılan %d, en çok %d.") % (_vm.DEFAULT_SHADOW_LIGHTS, _vm.MAX_SHADOW_LIGHTS))
    form.addRow(T("Gölge veren ışık"), sp_sh)

    # --- nesne yansıması (küp sonda)
    cb_probe = QtGui.QCheckBox(T("Nesneler birbirini yansısın (küp sonda)"))
    cb_probe.setChecked(_vm.probe_enabled())
    cb_probe.setToolTip(T("Parlak yüzeyler ve cam yalnız ortamı (HDRI / gökyüzü) değil, sahnedeki nesneleri de "
                          "yansıtır. Sahne arka planda 6 yöne pişirilir; sahne ya da ışık değişince yenilenir."))
    form.addRow("", cb_probe)

    # --- düzlemsel yansıma
    cb_mir = QtGui.QCheckBox(T("Düz zeminde keskin yansıma (düzlemsel)"))
    cb_mir.setChecked(_vm.mirror_enabled())
    cb_mir.setToolTip(T("Sahnedeki en büyük düz ve yansıtıcı yüzey (genelde zemin) için sahne her karede aynalanıp "
                        "ayrı çizilir: nesneler yansımada tam altlarında görünür. Sahne karede iki kez çizilir, "
                        "büyük montajlarda yavaşlatabilir."))
    form.addRow("", cb_mir)

    # --- AO
    cb_ao = QtGui.QCheckBox(T("Ortam tıkanıklığı (AO)"))
    cb_ao.setChecked(_vm.ao_enabled())
    cb_ao.setToolTip(T("Köşelerde ve nesne–zemin temasında yumuşak kararma (ekran uzayında, derinlik ön geçişiyle). "
                       "Render'daki AO kuralının aynısıdır: yalnız ortam ışığını kısar. AO'nun kendisi Render "
                       "panelinden açılır; buradaki anahtar yalnız görünümde hesaplanmasını kapatır."))
    form.addRow("", cb_ao)

    # --- efekt çözünürlüğü
    cb_fx = QtGui.QComboBox()
    cb_fx.addItem(T("Yarım (hızlı)"), 0.5)
    cb_fx.addItem(T("Tam (net, yavaş)"), 1.0)
    cb_fx.setCurrentIndex(1 if _vm.fx_scale() > 0.75 else 0)
    cb_fx.setToolTip(T("Düzlemsel yansımanın ve AO'nun hesaplandığı çözünürlük (görünümün oranı olarak). Yarım "
                       "çözünürlük dört kat az piksel demektir; zayıf ekran kartında burada kalın."))
    form.addRow(T("Yansıma / AO çözünürlüğü"), cb_fx)

    lay.addLayout(form)

    note = QtGui.QLabel(T("Not: AO, cam, yansıma ve gölgenin açık olup olmadığı Render panelinden gelir. Burada "
                          "kapatılan şey görünümde hesaplanmaz, ama render'da yine çıkar."))
    note.setWordWrap(True)
    try:
        note.setStyleSheet("color: %s;" % _theme.TEXT_DIS)
    except Exception:
        pass
    lay.addWidget(note)

    row = QtGui.QHBoxLayout()
    b_def = QtGui.QPushButton(T("Varsayılanlar"))
    b_close = QtGui.QPushButton(T("Kapat"))
    b_close.setDefault(True)
    row.addWidget(b_def)
    row.addStretch(1)
    row.addWidget(b_close)
    lay.addLayout(row)

    # --- bağlantılar: her değişiklik hemen uygulanır (önizleme kapalıyken de parametre yazılır)
    busy = {"v": False}

    def _apply():
        if busy["v"]:
            return
        _vm.set_shadow_lights(sp_sh.value())
        _vm.set_probe_enabled(cb_probe.isChecked())
        _vm.set_mirror_enabled(cb_mir.isChecked())
        _vm.set_ao_enabled(cb_ao.isChecked())
        _vm.set_fx_scale(float(cb_fx.itemData(cb_fx.currentIndex())))

    sp_sh.valueChanged.connect(lambda _v: _apply())
    for c in (cb_probe, cb_mir, cb_ao):
        c.toggled.connect(lambda _v: _apply())
    cb_fx.currentIndexChanged.connect(lambda _v: _apply())

    def _defaults():
        busy["v"] = True
        sp_sh.setValue(_vm.DEFAULT_SHADOW_LIGHTS)
        cb_probe.setChecked(True)
        cb_mir.setChecked(True)
        cb_ao.setChecked(True)
        cb_fx.setCurrentIndex(0)
        busy["v"] = False
        _apply()
    b_def.clicked.connect(_defaults)
    b_close.clicked.connect(dlg.close)

    try:
        _theme.apply(dlg)
        _theme.fix_combos(dlg)
    except Exception:
        pass
    dlg.resize(420, dlg.sizeHint().height())
    FreeCAD._ybt_viewsettings_dlg = dlg
    dlg.show()
    return dlg
