# -*- coding: utf-8 -*-
"""FreeCAD komutları (eklenti girişi): Render, Malzemeler, Seçenekler.

InitGui.py bu modülü içe aktarıp `register()` çağırır. Komutlar:
  YBT_Render      ayar panelini açar (render panelin 'Render' düğmesiyle başlar)
  YBT_QuickRender  render'ı doğrudan 3B görünümün üzerinde, kameranın kadrajında yapar (panel açılmaz)
  YBT_GodotRender  sahneyi Godot 4 ile render eder (godot_bridge.py; glb + json yazar, Godot arka planda çalışır)
  YBT_NewScene    ağaçta yeni "YBTray0N" sahnesi yaratır (ışıklar altında durur)
  YBT_DistantLight  sahneye Distant (güneş tipi) ışık nesnesi ekler
  YBT_Camera      sahneye 3B görünümün bakışıyla yeni kamera nesnesi ekler (etkin kamera olur; render bundan alınır)
  YBT_LookThrough 3B görünümü seçili / etkin kameranın bakışına getirir
  YBT_CameraProperties  Kamera Özellikleri paneli (lens, kadraj, alan derinliği)
  YBT_CameraUpdate  seçili / etkin kamerayı şu anki 3B görünüme eşitler
  YBT_PointLight  sahneye Point (konumlu, her yöne yayılan) ışık ekler
  YBT_SpotLight   sahneye Spot (konumlu, koni içine yayılan) ışık ekler
  YBT_LightProperties  Işık Özellikleri panelini açar
  YBT_Materials   Malzemeler (Surface) penceresini açar
  YBT_Options    panelin 'Seçenekler' penceresini açar (gerekirse paneli de açar)

Komutların asıl yeri tezgâh listesindeki "YBT Raytracer" tezgâhıdır (kendi araç çubuğu ve menüsüyle, InitGui.py içinde
tanımlanır; `separate_workbench_enabled`, varsayılan açık). Her tezgâhtan erişilebilmesi için ayrıca FreeCAD 1.0+
`Gui.addWorkbenchManipulator` ile standart "Macro" araç çubuğuna ve "Macro" menüsüne eklenir (manipulator yeni araç
çubuğu yaratamaz, yalnız var olanlara ekler; `ShowInAllWorkbenches`, varsayılan açık).
Kullanıcı JSON dosyaları ve nesne özellikleri (YBTMaterial / YBTInclude) bu katmandan etkilenmez.
"""
import os

COMMANDS = ("YBT_Render", "YBT_QuickRender", "YBT_GodotRender", "YBT_NewScene", "YBT_DistantLight", "YBT_PointLight", "YBT_SpotLight", "YBT_Camera", "YBT_LookThrough", "YBT_CameraUpdate", "YBT_CameraProperties", "YBT_LightProperties", "YBT_Materials", "YBT_Options")
TOOLBAR = "YBT Raytracer"
MENU = "YBT Raytracer"
_PARAM = "User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer"      # i18n.py ile aynı grup
_HERE = os.path.dirname(os.path.abspath(__file__))
_ICONS = os.path.join(_HERE, "icons")

_state = {"registered": False, "manipulator": False}


def icon_path():
    """Tezgâh / ana komut ikonu (InitGui.py sınıf gövdesinden çağrılır)."""
    return os.path.join(_HERE, "icon.png")


def _tr(s):
    from .i18n import T
    return T(s)


def _icon(name):
    p = os.path.join(_ICONS, name)
    return p if os.path.exists(p) else os.path.join(_HERE, "icon.png")


def _doc_ready():
    try:
        import FreeCAD
        import FreeCADGui as Gui
        return FreeCAD.ActiveDocument is not None and Gui.ActiveDocument is not None
    except Exception:
        return False


def _panel():
    """Açık render paneli (kapatılmış / silinmiş Qt nesnesi ise None)."""
    import FreeCAD
    dlg = getattr(FreeCAD, "_raytrace_dlg", None)
    if dlg is None:
        return None
    try:
        if dlg.isVisible():
            return dlg
    except Exception:                                  # silinmiş Qt nesnesi
        pass
    return None


def open_panel():
    """Ayar panelini açar; zaten açıksa öne getirir. Paneli döndürür (açılamadıysa None)."""
    dlg = _panel()
    if dlg is not None:
        dlg.raise_()
        dlg.activateWindow()
        return dlg
    from . import ui
    ui.main()
    return _panel()


def quick_render():
    from . import quickrender
    quickrender.run()


_godot_state = {"job": None}


def _godot_exe():
    """Godot çalıştırılabilir dosyası: kayıtlı yol (parametre 'GodotPath') -> PATH. Bulunamazsa kullanıcıya dosya seçtirilir
    ve seçilen yol parametreye kaydedilir. Döner: yol ya da None (iptal)."""
    import FreeCAD
    from . import godot_bridge as gb
    pr = FreeCAD.ParamGet(_PARAM)
    exe = gb.find_godot(pr.GetString("GodotPath", ""))
    if exe:
        return exe
    from PySide import QtGui
    try:
        import FreeCADGui as Gui
        parent = Gui.getMainWindow()
    except Exception:
        parent = None
    res = QtGui.QFileDialog.getOpenFileName(parent, _tr("Godot 4 çalıştırılabilir dosyasını seçin"), os.path.expanduser("~"))
    path = res[0] if isinstance(res, (tuple, list)) else res
    if not path:
        return None
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        FreeCAD.Console.PrintError(_tr("YBT Godot: seçilen dosya çalıştırılabilir değil: %s\n") % path)
        return None
    pr.SetString("GodotPath", path)
    return path


def godot_render():
    """Etkin belgeyi Godot ile render eder: sahne glb + json olarak yazılır, Godot arka planda çalışır, bitince görüntü açılır.
    Kamera: etkin YBT kamera nesnesi, yoksa 3B görünüm. Işıklar: YBT ışık nesneleri (yoksa panelin klasik ışıkları)."""
    import FreeCAD
    from . import api, cameraobj, freecad_scene, godot_bridge as gb, lightobj, output, settings as S
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return
    job = _godot_state.get("job")
    if job is not None and job.running:
        FreeCAD.Console.PrintWarning(_tr("YBT Godot: önceki render sürüyor.\n"))
        return
    exe = _godot_exe()
    if exe is None:
        return
    cam_obj = cameraobj.active_camera(doc)
    if cam_obj is not None:
        cam, aspect = cameraobj.render_camera(cam_obj)
    else:
        cam, aspect = api.camera_from_view()
    FreeCAD.Console.PrintMessage(_tr("YBT Godot: sahne hazırlanıyor...\n"))
    scene, _n = freecad_scene.collect_scene(doc)
    lightobj.apply_to_settings(doc)
    lights = list(S._OBJ_LIGHTS) if S._OBJ_LIGHTS else None
    import tempfile
    work = tempfile.mkdtemp(prefix="ybt_godot_")
    width = max(int(S.WIDTH), 1280)                      # Godot kalitesi için panelin küçük önizleme genişliği yetmez
    pkg = gb.build_package(scene, work, cam, aspect, lights, width)
    base = output.pick_out_path(doc.FileName)
    out_png = os.path.splitext(base)[0] + "_godot.png"
    _godot_state["job"] = _GodotJob(exe, pkg, out_png, work)
    _godot_state["job"].start()
    FreeCAD.Console.PrintMessage(_tr("YBT Godot: render başladı (%dx%d)...\n") % tuple(pkg["size"]))


class _GodotJob(object):
    """Godot'u ayrı iş parçacığında çalıştırır; bitince sonucu Qt sinyaliyle ana iş parçacığına taşır."""

    def __init__(self, exe, pkg, out_png, work):
        from PySide import QtCore

        class _Sig(QtCore.QObject):
            finished = QtCore.Signal(str, str)

            def __init__(self, owner):
                QtCore.QObject.__init__(self)
                self.owner = owner
                self.finished.connect(self.on_finished)

            @QtCore.Slot(str, str)
            def on_finished(self, png, err):
                self.owner.running = False
                self.owner.done(png, err)

        self.exe, self.pkg, self.out_png, self.work = exe, pkg, out_png, work
        self.running = False
        self._sig = _Sig(self)                              # ana iş parçacığında yaratılır -> slot orada çalışır

    def start(self):
        import threading
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from . import godot_bridge as gb
        png, err = "", ""
        try:
            png = gb.run_godot(self.exe, self.pkg, self.out_png)
        except Exception as ex:
            err = str(ex)
        self._sig.finished.emit(png, err)

    def done(self, png, err):
        import FreeCAD
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)
        if err:
            FreeCAD.Console.PrintError(_tr("YBT Godot hatası:\n") + err + "\n")
            return
        FreeCAD.Console.PrintMessage(_tr("YBT Godot: kaydedildi: %s\n") % png)
        try:
            from PySide import QtCore, QtGui
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(png))
        except Exception:
            pass


def open_materials(select=None, add=False):
    """select: açılışta seçilecek nesne adı (pencere zaten açıksa doğrudan seçer)."""
    import FreeCAD
    sd = getattr(FreeCAD, "_ybt_surface_dlg", None)
    try:
        alive = sd is not None and sd.isVisible()
    except Exception:
        alive = False
    if alive and select:
        sd._ybt_api["pick"](select, add)
        sd.raise_()
        sd.activateWindow()
        return
    api = getattr(_panel(), "_raytrace_api", None)
    if api and api.get("open_mats") and not select:
        api["open_mats"]()                              # panel açıksa onun geri çağrılarıyla (sahne önbelleği sıfırlanır)
        return
    from .i18n import init_lang
    init_lang()
    from .surface_ui import open_surface
    on_change = None
    if api and api.get("reset_scene"):
        on_change = api["reset_scene"]
    open_surface(on_change=on_change, select=select, add=add)


def open_options():
    dlg = open_panel()                                  # Seçenekler penceresi panelin değerlerine dayanır
    api = getattr(dlg, "_raytrace_api", None)
    if api and api.get("open_options"):
        api["open_options"]()


def add_light(kind):
    """Etkin belgeye ışık nesnesi ekler. Belge eski (makro dönemi) aydınlatmadaysa yeni sisteme geçirilir."""
    import FreeCAD
    from . import lightobj, settings as S
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    from . import sceneobj
    doc.openTransaction("Add light")
    try:
        sc = sceneobj.target_scene(doc)                  # seçili / etkin sahne; yoksa YBTray01 yaratılır
        obj = lightobj.create(doc, kind)
        sc.addObject(obj)
    finally:
        doc.commitTransaction()
    if str(S.LIGHT_MODE) != "objects":
        S.LIGHT_MODE = "objects"
        FreeCAD.Console.PrintMessage(_tr("YBT: ışık nesnesi eklendi; bu belge artık ışık nesneleriyle aydınlatılır (eski aydınlatma kapandı).\n"))
        dlg = _panel()
        api = getattr(dlg, "_raytrace_api", None)
        if api and api.get("refresh_light_mode"):
            api["refresh_light_mode"]()
    try:
        from . import light_ui
        light_ui.open_panel(select=obj)                 # yeni ışık hemen düzenlenebilsin
    except Exception:
        pass
    return obj


def new_scene():
    """Ağaçta yeni YBT sahnesi (YBTray01, 02...) yaratır ve etkin yapar."""
    import FreeCAD
    from . import sceneobj
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    doc.openTransaction("New YBT scene")
    try:
        from . import lightobj
        obj = sceneobj.create(doc, make_active=True)
        if not lightobj.lights_in_scene(obj):             # yeni sahne boş başlamasın: bir Distant Light ile gelir
            obj.addObject(lightobj.create(doc, "distant"))
    finally:
        doc.commitTransaction()
    try:
        import FreeCADGui as Gui
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
    except Exception:
        pass
    return obj


def open_light_properties():
    from . import light_ui
    light_ui.open_panel()


def add_distant_light():
    return add_light("distant")


def _selected_camera(doc):
    from . import cameraobj
    try:
        import FreeCADGui as Gui
        for o in Gui.Selection.getSelection():
            if cameraobj.is_camera(o):
                return o
    except Exception:
        pass
    return cameraobj.active_camera(doc) or (cameraobj.scene_cameras(doc) or [None])[0]


def add_camera():
    """Sahneye (YBTray) şu anki 3B görünümün bakışıyla kamera ekler."""
    import FreeCAD
    from . import cameraobj, sceneobj
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    doc.openTransaction("Add camera")
    try:
        sc = sceneobj.target_scene(doc)
        obj = cameraobj.create(doc, from_view=True)
        sc.addObject(obj)
        for o in cameraobj.cameras_in_scene(doc, sc):       # sahnede tek etkin kamera: yeni kamera etkin olur
            if o is not obj and o.Active:
                o.Active = False
        obj.Active = True
    finally:
        doc.commitTransaction()
    try:
        from . import camera_ui
        camera_ui.open_panel(select=obj)                    # yeni kamera hemen düzenlenebilsin
    except Exception:
        pass
    return obj


def open_camera_properties():
    from . import camera_ui
    camera_ui.open_panel()


def look_through_camera():
    import FreeCAD
    from . import cameraobj
    o = _selected_camera(FreeCAD.ActiveDocument)
    if o is None:
        FreeCAD.Console.PrintWarning(_tr("YBT: kamera yok; önce 'YBT Kamera' ile bir kamera ekleyin.\n"))
        return
    cameraobj.look_through(o)


def update_camera_from_view():
    import FreeCAD
    from . import cameraobj
    doc = FreeCAD.ActiveDocument
    o = _selected_camera(doc)
    if o is None:
        FreeCAD.Console.PrintWarning(_tr("YBT: kamera yok; önce 'YBT Kamera' ile bir kamera ekleyin.\n"))
        return
    doc.openTransaction("Update camera from view")
    try:
        cameraobj.update_from_view(o)
    finally:
        doc.commitTransaction()
    doc.recompute()


def add_point_light():
    return add_light("point")


def add_spot_light():
    return add_light("spot")


class _Cmd(object):
    def __init__(self, icon, text, tip, action, needs_doc=True):
        self._icon, self._text, self._tip, self._action, self._needs_doc = icon, text, tip, action, needs_doc

    def GetResources(self):
        return {"Pixmap": _icon(self._icon), "MenuText": _tr(self._text), "ToolTip": _tr(self._tip)}

    def Activated(self):
        import FreeCAD
        try:
            self._action()
        except Exception:
            import traceback
            FreeCAD.Console.PrintError(_tr("RayTrace hatası:\n") + traceback.format_exc())

    def IsActive(self):
        return _doc_ready() if self._needs_doc else True


def _commands():
    return {
        "YBT_Render": _Cmd("render.png", "YBT Raytracer", "Render ayar panelini açar (Render düğmesiyle görüntü alınır)", open_panel),
        "YBT_QuickRender": _Cmd("quick_render.png", "YBT Hızlı Render", "Render'ı doğrudan 3B görünümde, kameranın kadrajında yapar (önce kaba, sonra tam kalite; Esc: kapat)", quick_render),
        "YBT_GodotRender": _Cmd("godot_render.png", "YBT Godot Render", "Sahneyi Godot 4 ile render eder (gerçek zamanlı PBR motoru; Godot'un yolu ilk kullanımda sorulur)", godot_render),
        "YBT_NewScene": _Cmd("scene.png", "YBT Yeni Sahne", "Ağaçta yeni bir YBT sahnesi (YBTray01...) yaratır; ışıklar bunun altına eklenir", new_scene),
        "YBT_DistantLight": _Cmd("distant_light.png", "YBT Distant Light", "Sahneye güneş tipi (paralel) ışık ekler", add_distant_light),
        "YBT_PointLight": _Cmd("point_light.png", "YBT Point Light", "Sahneye konumlu, her yöne yayılan ışık ekler (uzaklıkla söner)", add_point_light),
        "YBT_SpotLight": _Cmd("spot_light.png", "YBT Spot Light", "Sahneye konumlu, koni içine yayılan ışık ekler", add_spot_light),
        "YBT_Camera": _Cmd("camera.png", "YBT Kamera", "Sahneye 3B görünümün bakışıyla kamera ekler; etkin kameradan render alınır", add_camera),
        "YBT_LookThrough": _Cmd("look_through.png", "YBT Kameradan Bak", "3B görünümü seçili (ya da etkin) kameranın bakışına getirir", look_through_camera),
        "YBT_CameraUpdate": _Cmd("view_to_camera.png", "YBT Kamerayı Görünüme Eşitle", "Seçili (ya da etkin) kamerayı şu anki 3B görünümle aynı yapar", update_camera_from_view),
        "YBT_CameraProperties": _Cmd("camera_props.png", "YBT Kamera Özellikleri", "Kamera nesnelerinin özelliklerini düzenler (lens, kadraj, alan derinliği)", open_camera_properties),
        "YBT_LightProperties": _Cmd("light_props.png", "YBT Işık Özellikleri", "Işık nesnelerinin özelliklerini düzenler (renk, şiddet, gölge)", open_light_properties),
        "YBT_Materials": _Cmd("surface_editor.png", "YBT Malzemeler", "Malzemeler (Surface) penceresini açar", open_materials),
        "YBT_Options": _Cmd("options.png", "YBT Seçenekler", "İçerik klasörü ve varsayılan ayarlar", open_options),
    }


class _Manipulator(object):
    """Komutları her tezgâhın Macro araç çubuğuna ve Macro menüsüne ekler (FreeCAD 1.0+)."""

    def modifyToolBars(self):
        return [{"append": c, "toolBar": "Macro"} for c in COMMANDS]

    def modifyMenuBar(self):
        return [{"append": c, "menuItem": "Std_DlgMacroExecute"} for c in COMMANDS]


def separate_workbench_enabled():
    """Ayrı "YBT Raytracer" tezgâhı: kullanıcı parametresi 'SeparateWorkbench' (varsayılan AÇIK; kapatmak için false).
    FreeCAD'de manipulator yoksa (0.21) tezgâh zorunlu açık."""
    try:
        import FreeCAD
        import FreeCADGui as Gui
        if not hasattr(Gui, "addWorkbenchManipulator"):
            return True
        return bool(FreeCAD.ParamGet(_PARAM).GetBool("SeparateWorkbench", True))
    except Exception:
        return True


def global_toolbar_enabled():
    """Macro araç çubuğu / menüsüne ekleme: kullanıcı parametresi 'ShowInAllWorkbenches' (varsayılan açık)."""
    try:
        import FreeCAD
        return bool(FreeCAD.ParamGet(_PARAM).GetBool("ShowInAllWorkbenches", True))
    except Exception:
        return True


def register():
    """Komutları kaydeder, istenirse her tezgâha ekler. Tekrar çağrılırsa bir şey yapmaz."""
    if _state["registered"]:
        return
    import FreeCAD
    import FreeCADGui as Gui
    from .i18n import init_lang
    init_lang()
    for name, cmd in _commands().items():
        Gui.addCommand(name, cmd)
    _state["registered"] = True
    try:
        from . import viewlight
        viewlight.start()                                 # görünümde ışık önizlemesi (YBT sahnesi olan belgede)
    except Exception:
        pass
    if global_toolbar_enabled() and hasattr(Gui, "addWorkbenchManipulator"):
        try:
            Gui.addWorkbenchManipulator(_Manipulator())
            _state["manipulator"] = True
        except Exception as ex:
            FreeCAD.Console.PrintWarning("YBT Raytracer: workbench manipulator: %s\n" % ex)
