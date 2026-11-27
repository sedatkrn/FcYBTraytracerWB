# -*- coding: utf-8 -*-
"""3B görünümde malzeme önizlemesi (gerçek zamanlı): malzemesi atanmış nesneler FreeCAD görünümünde YBT malzemesiyle, render ile
aynı hesapla çizilir. Gölgelendirici viewmat_glsl.py'de malzemeden üretilir (tüm kanallar, prosedürel desenler, bump, stokastik
döşeme, ortam ışığı / yansıması, vernik, cam); bu modül sahneyi tarar ve Coin düğümlerini yönetir.

  * Her nesnenin ViewObject.RootNode'unun başına bir SoGroup eklenir: doku birimleri (SoTextureUnit + SoTexture2) ve GLSL
    SoShaderProgram. Malzeme değişince yalnız kaynak metni değişir, Coin yeniden derler.
  * Dünya koordinatı gölgelendiricide kameranın ters görüş matrisiyle hesaplanır (model kökünün başındaki SoCallback her çizimde
    günceller); Link / Assembly kopyaları da kendi yerlerinde dokulanır.
  * Kenar ve nokta çizgilerine düz renk çizen küçük bir program eklenir (yüz gölgelendiricisi çizgileri boyamasın).
  * Belgeye HİÇBİR ŞEY yazılmaz; önizleme kapanınca tüm düğümler kaldırılır. Eklenen ve dokunulan düğümler ref'lenir:
    nesne / belge silinse de kaldırma güvenlidir.

FreeCAD'in ekran dışı görüntü kaydı (Save picture) gölgelendiriciyi çalıştırmaz: önizleme açıkken resim kaydetmeden önce kapatın.
"""
import math

from . import materials as M

_NAME = "YBT_VIEWMAT"
_SHARED_NAME = "YBT_ViewShared"
_PLAIN_NAME = "YBT_VIEWMAT_PLAIN"
_CB_NAME = "YBT_VIEWMAT_CB"
MAX_LAYERS = 4
MAX_SHADOW_LIGHTS = 8          # gerçek zamanlı gölge veren en çok ışık (Seçenekler > Genel)
DEFAULT_SHADOW_LIGHTS = 3
_PARAM = "User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer"


def shadow_lights():
    """Görünümde gölge veren ışık sayısı (0 = gölge kapalı). Makineye özgü kullanıcı parametresi."""
    try:
        import FreeCAD
        v = int(FreeCAD.ParamGet(_PARAM).GetInt("ViewShadowLights", DEFAULT_SHADOW_LIGHTS))
    except Exception:
        v = DEFAULT_SHADOW_LIGHTS
    return max(0, min(MAX_SHADOW_LIGHTS, v))


def set_shadow_lights(n):
    try:
        import FreeCAD
        FreeCAD.ParamGet(_PARAM).SetInt("ViewShadowLights", max(0, min(MAX_SHADOW_LIGHTS, int(n))))
    except Exception:
        pass
    if _st.get("on"):
        _schedule(50)


def probe_enabled():
    """Nesne / zemin yansıması (yansıma sondası) açık mı. Makineye özgü kullanıcı parametresi, varsayılan açık."""
    try:
        import FreeCAD
        return bool(FreeCAD.ParamGet(_PARAM).GetBool("ViewProbe", True))
    except Exception:
        return True


def set_probe_enabled(on):
    try:
        import FreeCAD
        FreeCAD.ParamGet(_PARAM).SetBool("ViewProbe", bool(on))
    except Exception:
        pass
    if _st.get("on"):
        _schedule(50)


MAX_TEX = 1024                 # görünüme yüklenen doku en çok bu kenar boyunda (mip düzeyinden seçilir)

_st = {"on": False, "items": {}, "plain": [], "cbs": [], "iv": None, "tex": {}, "override": {}, "obs": None,
       "timer": None, "diag": 1000.0, "doc": None, "env": None}


_PLAIN_VS = "void main(){ gl_FrontColor = gl_Color; gl_BackColor = gl_Color; gl_Position = ftransform(); }"
_PLAIN_FS = "void main(){ gl_FragColor = gl_Color; }"


# ------------------------------------------------------------------ Coin düğümleri
def _coin():
    from pivy import coin
    return coin


def _tex_node(tx):
    """Texture -> paylaşılan SoTexture2 (sRGB 8 bit; mip düzeyi MAX_TEX'e sığan)."""
    import numpy as np
    coin = _coin()
    key = id(tx)
    node = _st["tex"].get(key)
    if node is not None:
        return node
    lv = 0
    while lv + 1 < len(tx.levels) and max(tx.levels[lv].shape[:2]) > MAX_TEX:
        lv += 1
    a = np.clip(np.asarray(tx.levels[lv], dtype=np.float64), 0.0, 1.0) ** (1.0 / 2.2)
    a8 = np.ascontiguousarray((a * 255.0 + 0.5).astype(np.uint8)[:, :, :3])
    h, w = a8.shape[:2]
    node = coin.SoTexture2()
    node.image.setValue(coin.SbVec2s(w, h), 3, a8.tobytes())
    node.model = coin.SoTexture2.MODULATE
    wrap = coin.SoTexture2.CLAMP if getattr(tx, "clamp", False) else coin.SoTexture2.REPEAT
    node.wrapS = wrap
    node.wrapT = wrap
    node.ref()
    _st["tex"][key] = (node, tx)                       # tx: id() geçerli kalsın (önbellek nesnesi silinmesin)
    return _st["tex"][key]


def _iv_params():
    """Ters görüş matrisi sütunları: tüm programlarda paylaşılan 3 parametre düğümü (SoCallback her çizimde doldurur)."""
    if _st["iv"] is None:
        coin = _coin()
        ps = []
        for k in range(3):
            p = coin.SoShaderParameter4f()
            p.name = "ivc%d" % k
            p.value = coin.SbVec4f(*[1.0 if j == k else 0.0 for j in range(4)])
            p.ref()
            ps.append(p)
        _st["iv"] = ps
    return _st["iv"]


def _cam_cb(_ud, action):
    """Çizim sırasında (kameradan SONRA): kameranın ters görüş matrisini parametrelere yaz. Yalnız değiştiyse yazılır: aynı
    karede aşağıdaki gölgelendiriciler yeni değeri alır; bildirim tek bir ek çizim ister, değer aynı kalınca döngü durur."""
    try:
        coin = _coin()
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        vm = coin.SoViewingMatrixElement.get(action.getState()).inverse()
        for j, p in enumerate(_iv_params()):
            v = (vm[0][j], vm[1][j], vm[2][j], vm[3][j])
            old = p.value.getValue().getValue()
            if max(abs(v[k] - old[k]) for k in range(4)) > 1e-9:
                p.value.setValue(coin.SbVec4f(*v))
    except Exception:
        pass
    mgr = _st.get("shadow")
    if mgr is not None and mgr.specs:
        try:
            mgr.update_camera(coin.SoViewingMatrixElement.get(action.getState()).inverse())
            r = mgr.render()
            if r is not None and r.pop("restore_vp", False):     # gölge haritası çizildi: görünümün viewport'unu geri koy
                from PySide6.QtGui import QOpenGLContext
                vpr = coin.SoViewportRegionElement.get(action.getState())     # pivy eylemi genel SoAction olarak verir
                o_ = vpr.getViewportOriginPixels()
                s_ = vpr.getViewportSizePixels()
                QOpenGLContext.currentContext().functions().glViewport(int(o_[0]), int(o_[1]), int(s_[0]), int(s_[1]))
                from . import viewshadow as _vsh_
                _vsh_._dbg("viewport restore %s %s" % (tuple(o_), tuple(s_)))
        except Exception as ex:
            import traceback
            from . import viewshadow as _vsh_
            _vsh_._dbg("callback EXC " + traceback.format_exc())
            if not _st.get("shadow_err"):
                _st["shadow_err"] = True
                import FreeCAD
                FreeCAD.Console.PrintWarning("YBT görünüm gölgesi devre dışı: %s\n" % ex)
                _schedule(10)                           # gölgesiz gölgelendiricilerle yeniden kur (örnekleyiciler bağsız kalmasın)
            _st["shadow"] = None


def _install_callbacks(gdoc):
    """Her 3B görünümün model kökünün (kamera ve ışıklardan sonra gelen düğüm) başına SoCallback ekler."""
    coin = _coin()
    for v in gdoc.mdiViewsOfType("Gui::View3DInventor"):
        try:
            sg = v.getSceneGraph()                         # FreeCAD'in model kökü: kamera bunun üstünde, görüş matrisi hazır
        except Exception:
            continue
        if any(s is sg or s == sg for s, _cb in _st["cbs"]):
            continue
        try:
            if any(sg.getChild(i).getName().getString() == _CB_NAME for i in range(sg.getNumChildren())):
                continue
        except Exception:
            pass
        cb = coin.SoCallback()
        cb.setName(_CB_NAME)
        cb.setCallback(_cam_cb)
        sg.ref()                                          # belge / görünüm kapansa da düğüm silinmesin (kaldırırken güvenli)
        cb.ref()
        sg.insertChild(cb, 0)
        _st["cbs"].append((sg, cb))


def _install_shared():
    """Ortak doku birimleri (sky + sonda = aux, HDRI = env) her 3B görünümün model kökünde bir kez bağlanır; Coin'de doku
    düğümü sayısı kareyi doğrudan yavaşlattığından nesne başına değil."""
    coin = _coin()
    from . import viewmat_glsl as G
    env = _st.get("env")
    for sg, grp in _st.get("shared", []):
        try:
            i = sg.findChild(grp)
            if i >= 0:
                sg.removeChild(i)
        except Exception:
            pass
        _unref(grp)
    _st["shared"] = []
    if not env:
        return
    for sg, _cb in _st["cbs"]:
        grp = coin.SoGroup()
        grp.ref()
        grp.setName(_SHARED_NAME)
        for spec, key in env["units"]:
            u = coin.SoTextureUnit()
            u.unit = G.AUX_UNIT if key[1] == "aux" else G.ENV_UNIT
            grp.addChild(u)
            grp.addChild(_unit_node(key, spec))
        u = coin.SoTextureUnit()
        u.unit = 0
        grp.addChild(u)
        sg.insertChild(grp, min(1, sg.getNumChildren()))
        _st["shared"].append((sg, grp))


def _remove_callbacks():
    for sg, grp in _st.get("shared", []):
        try:
            i = sg.findChild(grp)
            if i >= 0:
                sg.removeChild(i)
        except Exception:
            pass
        _unref(grp)
    _st["shared"] = []
    for sg, cb in _st["cbs"]:
        try:
            i = sg.findChild(cb)
            if i >= 0:
                sg.removeChild(i)
        except Exception:
            pass
        _unref(cb)
        _unref(sg)
    _st["cbs"] = []


def _unref(node):
    try:
        node.unref()
    except Exception:
        pass


def _program(vs_src, fs_src, ntex, shadow=False):
    coin = _coin()
    prog = coin.SoShaderProgram()
    vs = coin.SoVertexShader()
    vs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    vs.sourceProgram = vs_src
    fs = coin.SoFragmentShader()
    fs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    fs.sourceProgram = fs_src
    ivs = _iv_params()
    vs.parameter.setValues(0, len(ivs), ivs)
    extra = list(ivs)
    if shadow and _st.get("shadow") is not None:
        extra += _st["shadow"].params()
    if shadow:
        from . import viewshadow as _vsh
        for nm, u in (("ySA", _vsh.UNIT_A), ("ySP", _vsh.UNIT_P)):
            p = coin.SoShaderParameter1i()
            p.name = nm
            p.value = u
            extra.append(p)
    from . import viewmat_glsl as _G
    for nm, u in (("yaux", _G.AUX_UNIT), ("yenv", _G.ENV_UNIT)):
        if ("uniform sampler2D %s;" % nm) in fs_src:
            p = coin.SoShaderParameter1i()
            p.name = nm
            p.value = u
            extra.append(p)
    _set_samplers(fs, ntex, extra)
    prog.shaderObject.setValues(0, 2, [vs, fs])
    return prog, vs, fs


def _set_samplers(fs, ntex, extra=()):
    coin = _coin()
    ps = list(extra)
    for i in range(ntex):
        p = coin.SoShaderParameter1i()
        p.name = "ytex%d" % i
        p.value = i
        ps.append(p)
    fs.parameter.deleteValues(0)
    if ps:
        fs.parameter.setValues(0, len(ps), ps)


def _plain_program():
    coin = _coin()
    prog = coin.SoShaderProgram()
    prog.setName(_PLAIN_NAME)
    vs = coin.SoVertexShader()
    vs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    vs.sourceProgram = _PLAIN_VS
    fs = coin.SoFragmentShader()
    fs.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    fs.sourceProgram = _PLAIN_FS
    prog.shaderObject.setValues(0, 2, [vs, fs])
    return prog


def _protect_lines(root):
    """root altındaki kenar / nokta çizimlerine düz program ekler (yüz gölgelendiricisi çizgileri boyamasın)."""
    coin = _coin()
    sa = coin.SoSearchAction()
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.setSearchingAll(True)
    found = []
    for tname in ("SoBrepEdgeSet", "SoBrepPointSet", "SoLineSet", "SoIndexedLineSet", "SoPointSet", "SoMarkerSet"):
        try:
            t = coin.SoType.fromName(tname)
        except Exception:
            continue
        if t.isBad():
            continue
        sa.reset()
        sa.setType(t)
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.setSearchingAll(True)
        sa.apply(root)
        pl = sa.getPaths()
        for k in range(pl.getLength()):
            p = pl[k]
            if p.getLength() >= 2:
                found.append(p.getNodeFromTail(1))
    for par in found:
        try:
            if not par.isOfType(coin.SoGroup.getClassTypeId()):
                continue
            if par.getNumChildren() and par.getChild(0).getName().getString() == _PLAIN_NAME:
                continue
            prog = _plain_program()
            par.ref()
            prog.ref()
            par.insertChild(prog, 0)
            _st["plain"].append((par, prog))
        except Exception:
            pass


def _remove_plain():
    for par, prog in _st["plain"]:
        try:
            i = par.findChild(prog)
            if i >= 0:
                par.removeChild(i)
        except Exception:
            pass
        _unref(prog)
        _unref(par)
    _st["plain"] = []


def _maybe_transparent(m, env):
    """Malzeme görünümde saydam (ya da cam yansımalı) çizilebilir mi: Coin karışımı açılmalı."""
    if not env["flags"].get("glass", True):
        lays = M.layers_of(m, "transp")
        return any(L.get("on", True) for L in lays)
    return float(m.get("transp", 0.0)) > 0.0 or any(L.get("on", True) for L in M.layers_of(m, "transp"))


def _unit_node(key, spec):
    """Doku birimi tanımı -> paylaşılan (önbellekli) SoTexture2."""
    import numpy as np
    coin = _coin()
    if spec[0] == "img":
        return _tex_node(spec[1])[0]
    hit = _st["tex"].get(key)
    if hit is not None:
        return hit[0]
    node = coin.SoTexture2()
    if spec[0] == "rgbm":
        # 3 kanal (alfasız!): alfa kanallı doku Coin'e nesneyi "saydam" saydırır; saydam nesneler derinlik yazmadan, sıralanıp
        # sonra çizilir ve birbirini yanlış örter. HDR aralığı: (doğrusal / 8) ^ (1/2.2) olarak saklanır (0..8, gama hassasiyetli).
        img = np.maximum(np.asarray(spec[2], dtype=np.float64), 0.0)
        a = np.clip(img / 8.0, 0.0, 1.0) ** (1.0 / 2.2)
        a8 = np.ascontiguousarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8))
        wrap = coin.SoTexture2.CLAMP if spec[3] == "clamp" else coin.SoTexture2.REPEAT
    else:                                                 # "tab": stokastik döşeme ötelemeleri (texelFetch ile okunur)
        a8 = np.ascontiguousarray(spec[2])
        wrap = coin.SoTexture2.REPEAT
    h, w = a8.shape[:2]
    node.image.setValue(coin.SbVec2s(w, h), a8.shape[2], a8.tobytes())
    node.model = coin.SoTexture2.REPLACE
    node.wrapS = wrap
    node.wrapT = wrap
    node.ref()
    _st["tex"][key] = (node, spec)
    return node


class _Item(object):
    """Bir nesnenin RootNode'una eklenen düğümler."""

    def __init__(self, obj, vo, lc, lh, dcols=None, inst=None):
        coin = _coin()
        self.name = obj.Name
        self.root = vo.RootNode                           # ViewObject değil kök düğüm tutulur (ref'li): nesne / belge
        self.root.ref()                                   # silinse de düğüm yaşar, kaldırma güvenli olur
        self.lc, self.lh = lc, lh
        self.dcols = dcols
        self.inst = inst
        self.grp = coin.SoGroup()
        self.grp.ref()
        self.grp.setName(_NAME)
        self.mat_node = None
        self.prog = None
        self.sig = None

    def apply(self, mat, diag):
        if self.root is None:
            return
        coin = _coin()
        from . import viewmat_glsl as G
        env = _st.get("env")
        if env is None:
            return
        vs_src, fs_src, units, dropped = G.build(mat if mat is not None else M.DEFAULT, self.lc, self.lh, diag, env,
                                                 self.inst, self.dcols)
        sig = (vs_src, fs_src, tuple(k for k, _s in units))
        if sig == self.sig:
            return
        self.sig = sig
        g = self.grp
        g.removeAllChildren()
        cx = coin.SoComplexity()                             # trilineer mip süzme (yansımada bulanıklık düzeyi de buna dayanır)
        cx.textureQuality = 1.0
        cx.value.setIgnored(True)
        cx.type.setIgnored(True)
        g.addChild(cx)
        for i, (key, spec) in enumerate(units):
            u = coin.SoTextureUnit()
            u.unit = i
            g.addChild(u)
            g.addChild(_unit_node(key, spec))
        if units:
            u = coin.SoTextureUnit()
            u.unit = 0
            g.addChild(u)
        m = M.normalize(mat) if mat is not None else dict(M.DEFAULT)
        if _maybe_transparent(m, env):                       # saydam olabilir: Coin karışımı açsın (renk gölgelendiricide)
            sm = coin.SoMaterial()
            sm.transparency = 0.01
            for f_ in (sm.ambientColor, sm.diffuseColor, sm.specularColor, sm.emissiveColor, sm.shininess):
                f_.setIgnored(True)                      # yalnız saydamlık geçersiz kılınsın (seçim rengi gelmeye devam etsin)
            sm.setOverride(True)
            g.addChild(sm)
        self.prog, _vs, _fs = _program(vs_src, fs_src, len(units), shadow=bool(env.get("shadow")))
        g.addChild(self.prog)
        root = self.root
        if root.findChild(g) < 0:
            root.insertChild(g, 0)
        if dropped:
            import FreeCAD
            FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: %s: doku birimi sınırı (%d) aşıldı, %d katman görünümde yok\n"
                                         % (self.name, G.MAX_UNITS, dropped))

    def remove(self):
        try:
            root = self.root
            i = root.findChild(self.grp)
            if i >= 0:
                root.removeChild(i)
        except Exception:
            pass
        self.grp.removeAllChildren()
        _unref(self.root)
        self.root = None


# ------------------------------------------------------------------ sahne
def _local_bounds(shp):
    """Şeklin kendi (yerleşimsiz) koordinatlarında sınır kutusu: (merkez, yarı boyut)."""
    import FreeCAD
    s = shp.copy()
    s.Placement = FreeCAD.Placement()
    b = s.BoundBox
    return ((b.XMin + b.XMax) / 2.0, (b.YMin + b.YMax) / 2.0, (b.ZMin + b.ZMax) / 2.0), \
        (max(b.XLength, 1e-6) / 2.0, max(b.YLength, 1e-6) / 2.0, max(b.ZLength, 1e-6) / 2.0)


def _disp_colors(o, tgt, shp):
    """Nesnenin ekran renkleri (yüz başına olanlar dahil, tekrarsız), vurgu algılamada karşılaştırmak için."""
    try:
        from .freecad_scene import _display_colors
        base, dc, _how = _display_colors(o, tgt, len(shp.Faces))
        cols = [tuple(base)] + [tuple(c) for c in (dc or ())]
        out = []
        for c in cols:
            c = tuple(round(float(x), 3) for x in c[:3])
            if c not in out:
                out.append(c)
        return out
    except Exception:
        return None


def _targets(doc):
    """Önizlenecek nesneler: render'ın gerçekten çizeceği görünür yapraklar (Link / Assembly / Body dahil).
    Döner [(nesne, hedef, yerel merkez, yerel yarı boyut)] ve sahne köşegeni."""
    import FreeCAD
    from .freecad_scene import _gather
    items = _gather(doc, lambda *_a: None)
    out, seen = [], set()
    inst = {}
    for o, _tgt, shp, _sub in items:                        # nesnenin tüm kopyalarının dünya sınır kutuları (stokastik tablo)
        try:
            b = shp.BoundBox
            inst.setdefault(o.Name, []).append(((b.XMin, b.YMin, b.ZMin), (b.XMax, b.YMax, b.ZMax)))
        except Exception:
            pass
    bb = FreeCAD.BoundBox()
    for o, tgt, shp, _sub in items:
        try:
            bb.add(shp.BoundBox)
        except Exception:
            pass
        if o.Name in seen or getattr(shp, "is_mesh", False):
            continue
        if not M.included(o):
            continue
        seen.add(o.Name)
        try:
            lc, lh = _local_bounds(shp)
        except Exception:
            continue
        out.append((o, tgt, lc, lh, _disp_colors(o, tgt, shp), inst.get(o.Name)))
    diag = bb.DiagonalLength if bb.isValid() else 1000.0
    cen = (bb.Center.x, bb.Center.y, bb.Center.z) if bb.isValid() else (0.0, 0.0, 0.0)
    return out, max(diag, 1e-6), cen


def _get_sc(doc):
    """Bu yenilemede bir kez toplanan render sahnesi (gölge ve yansıma sondası paylaşır)."""
    if _st.get("sc") is None:
        from .freecad_scene import collect_scene
        _st["sc"] = collect_scene(doc)[0]
    return _st["sc"]


def _probe_update(doc):
    """Yansıma sondası: gerekirse arka planda pişirmeyi başlatır; hazır olan sondayı döndürür (yoksa None)."""
    from . import viewprobe as vp
    from . import settings as S
    if not probe_enabled() or not bool(S.REFLECTIONS):
        vp.reset()
        return None
    try:
        sc = _get_sc(doc)
        lights = None
        if str(getattr(S, "LIGHT_MODE", "objects")) == "objects":
            from . import lightobj
            lights = lightobj.collect(doc) or None
        vp.request(sc, lights, S)
    except Exception as ex:
        import FreeCAD
        FreeCAD.Console.PrintWarning("YBT yansıma sondası: %s\n" % ex)
        return vp.current()
    if vp.busy() or vp.pending_peek():
        _probe_poll()
    return vp.current()


def _probe_poll(ms=400):
    from PySide import QtCore
    t = _st.get("ptimer")
    if t is None:
        t = QtCore.QTimer()
        t.setSingleShot(True)
        t.timeout.connect(_probe_tick)
        _st["ptimer"] = t
    t.start(ms)


def _probe_tick():
    from . import viewprobe as vp
    if not _st["on"]:
        return
    if vp.busy():
        _probe_poll()
        return
    fresh = vp.take_result()
    p = vp.pending_request()
    if p is not None:
        vp.request(*p)
        _probe_poll()
    if fresh:
        refresh()


def _shadow_scene(doc):
    """Gölge haritaları için render üçgenleri (opak / cam). Gölge kapalıysa None."""
    from . import settings as S
    n = shadow_lights()
    if n <= 0 or not bool(S.SHADOWS) or _st.get("shadow_err"):
        _st["shadow_tri"] = None
        return None
    import numpy as np
    try:
        sc = _get_sc(doc)
    except Exception:
        _st["shadow_tri"] = None
        return None
    tri = np.asarray(sc.tri, dtype=np.float64) + sc.center
    alpha = np.asarray(sc.alpha) if bool(S.GLASS) else np.ones(len(tri))
    gl = alpha < 0.999
    flat = tri.reshape(-1, 3)
    _st["shadow_tri"] = (tri[~gl], tri[gl])
    return dict(lo=flat.min(0), hi=flat.max(0), has_glass=bool(gl.any()), nmax=n)


def _apply_shadow(sh_info):
    env = _st.get("env")
    if not env or not env.get("shadow") or _st.get("shadow_tri") is None:
        mgr = _st.get("shadow")
        if mgr is not None:
            mgr.specs = None
        return
    from . import viewshadow as _vsh
    mgr = _st.get("shadow")
    if mgr is None:
        mgr = _vsh.Manager()
        _st["shadow"] = mgr
    specs, layout = env["shadow"]
    mgr.set_scene(_st["shadow_tri"][0], _st["shadow_tri"][1], specs, layout)


def _material_for(o, tgt):
    ov = _st["override"]
    for x in (o, tgt):
        if x is not None and x.Name in ov:
            return ov[x.Name]
    name = M.assigned(o) or M.assigned(tgt)
    return M.get(name) if name else None


def _gl_widget():
    """Etkin 3B görünümün OpenGL bileşeni (GL kaynaklarını bırakırken bağlamı etkinleştirmek için)."""
    try:
        import FreeCADGui as Gui
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
        except Exception:
            from PySide2.QtWidgets import QOpenGLWidget
        ws = [w for w in Gui.getMainWindow().findChildren(QOpenGLWidget) if w.isVisible()]
        return max(ws, key=lambda w: w.width() * w.height()) if ws else None
    except Exception:
        return None


def is_on():
    return bool(_st["on"])


def refresh(doc=None):
    """Nesne listesini ve malzemeleri yeniden okuyup düğümleri günceller (açık değilse bir şey yapmaz)."""
    if not _st["on"]:
        return
    import FreeCAD
    import FreeCADGui as Gui
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        return
    try:
        gdoc = Gui.getDocument(doc.Name)
    except Exception:
        return
    if _st["doc"] is not None and _st["doc"] != doc.Name:
        _clear_nodes()
    _st["doc"] = doc.Name
    _install_callbacks(gdoc)
    try:
        targets, diag, cen = _targets(doc)
    except Exception as ex:
        FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: sahne taranamadı (%s)\n" % ex)
        return
    _st["diag"] = diag
    _st["sc"] = None
    sh_info = _shadow_scene(doc)
    probe = _probe_update(doc)
    _st["sc"] = None
    try:
        from . import viewmat_glsl as G
        _st["env"] = G.env_setup(doc, cen, shadow=sh_info, probe=probe)
        for k_ in [k for k in _st["tex"] if isinstance(k, tuple) and len(k) > 2 and k[0] == "rgbm" and k[1] == "aux"
                   and k != _st["env"]["aux_key"]]:
            _unref(_st["tex"].pop(k_)[0])
        _install_shared()
        _apply_shadow(sh_info)
    except Exception as ex:
        import traceback
        FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: ortam / ışıklar okunamadı (%s)\n%s" % (ex, traceback.format_exc()))
        _st["env"] = None
    keep = set()
    for o, tgt, lc, lh, dcols, inst in targets:
        mat = _material_for(o, tgt)
        if mat is None:
            continue                                  # malzemesi olmayan nesne FreeCAD'in kendi görünümüyle kalır
        vo = getattr(o, "ViewObject", None)
        if vo is None:
            continue
        it = _st["items"].get(o.Name)
        if it is None or it.lc != lc or it.lh != lh or it.dcols != dcols or it.inst != inst:
            if it is not None:
                it.remove()
            it = _Item(o, vo, lc, lh, dcols, inst)
            _st["items"][o.Name] = it
        try:
            it.apply(mat, diag)
            keep.add(o.Name)
            _protect_lines(vo.RootNode)
        except Exception as ex:
            FreeCAD.Console.PrintWarning("YBT görünüm malzemesi: %s: %s\n" % (o.Label, ex))
    for nm in list(_st["items"]):
        if nm not in keep:
            _st["items"].pop(nm).remove()
    _redraw(doc)


def update_material(names, mat):
    """Canlı düzenleme: adlı nesnelerde (kaydedilmemiş) mat malzemesini göster. names boş / mat None: geçersiz kıl."""
    ov = _st["override"]
    ov.clear()
    if mat is not None:
        for n in names or ():
            ov[n] = mat
    if not _st["on"]:
        return
    if mat is None:
        refresh()
        return
    hit = False
    for n in names or ():
        it = _st["items"].get(n)
        if it is not None:
            try:
                it.apply(mat, _st["diag"])
                hit = True
            except Exception:
                pass
    if not hit and names:
        refresh()                                     # henüz malzemesi olmayan nesneye ilk atama: listeye girsin
    else:
        _redraw()


def clear_override():
    if _st["override"]:
        _st["override"].clear()
        refresh()


def _redraw(doc=None):
    try:
        import FreeCADGui as Gui
        gdoc = Gui.getDocument(doc.Name) if doc is not None else Gui.ActiveDocument
        for v in gdoc.mdiViewsOfType("Gui::View3DInventor"):
            v.redraw()
    except Exception:
        pass


def _clear_nodes():
    for it in list(_st["items"].values()):
        it.remove()
    _st["items"].clear()
    _remove_plain()
    _remove_callbacks()


# ------------------------------------------------------------------ belge gözlemcisi
_WATCH = ("YBTMaterial", "YBTInclude", "Shape", "Visibility", "LinkedObject", "Group", "Mesh", "ElementCount",
          "ShapeAppearance", "Placement")


def _is_light(obj):
    try:
        from . import lightobj
        return bool(lightobj._is_light(obj))
    except Exception:
        return False


class _Obs(object):
    def slotChangedObject(self, obj, prop):
        if prop in _WATCH or _is_light(obj):
            _schedule()

    def slotCreatedObject(self, obj):
        _schedule()

    def slotDeletedObject(self, obj):
        _schedule()

    def slotActivateDocument(self, doc):
        _schedule()

    def slotDeletedDocument(self, doc):
        if getattr(doc, "Name", None) == _st["doc"]:
            _clear_nodes()                                # düğümler ref'li: kaldırma, belge silinirken de güvenli
            _st["doc"] = None


def _schedule(ms=250):
    from PySide import QtCore
    t = _st["timer"]
    if t is None:
        t = QtCore.QTimer()
        t.setSingleShot(True)
        t.timeout.connect(lambda: refresh())
        _st["timer"] = t
    t.start(ms)


def start():
    import FreeCAD
    if _st["on"]:
        refresh()
        return
    _st["on"] = True
    if _st["obs"] is None:
        _st["obs"] = _Obs()
        FreeCAD.addDocumentObserver(_st["obs"])
    refresh()


def stop():
    import FreeCAD
    _st["on"] = False
    if _st["obs"] is not None:
        try:
            FreeCAD.removeDocumentObserver(_st["obs"])
        except Exception:
            pass
        _st["obs"] = None
    if _st["timer"] is not None:
        _st["timer"].stop()
    if _st.get("ptimer") is not None:
        _st["ptimer"].stop()
    try:
        from . import viewprobe as _vp
        _vp.reset()
    except Exception:
        pass
    _clear_nodes()
    _st["override"].clear()
    _st["doc"] = None
    mgr = _st.pop("shadow", None)
    if mgr is not None:
        try:
            mgr.release(_gl_widget())
        except Exception:
            pass
    _st["shadow_err"] = False
    _redraw()


def toggle():
    if _st["on"]:
        stop()
    else:
        start()
    return _st["on"]
