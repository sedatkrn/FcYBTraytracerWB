# -*- coding: utf-8 -*-
"""Görünüm düzlemsel yansıması (planar reflection).

Zemin gibi büyük, düz ve yansıtıcı bir yüzey için sahne, o yüzeyin düzlemine göre aynalanıp her karede ayrı bir
çerçeve arabelleğine (FBO) çizilir. Yansıtıcı yüzeyin gölgelendiricisi, kendi ekran pikselinin konumundan bu dokuyu okur:
nesneler yansımada tam yerinde görünür (yansıma sondasının kutu paralaksı zemine yakın nesneleri kaydırıyordu).

Aynalı geçiş ana çizimin içinde, model kökünün başındaki bir dalda yapılır: dal FBO'yu bağlar, sahneyi yansıma matrisiyle
(SoMatrixTransform, determinant -1) bir kez daha çizer. Kare içinde hiçbir uniform değiştirilmez (Coin parametreleri yalnız
değişiklik bildirilince yükler): gölgelendirici aynalı geçişte olduğunu model-görünüm matrisinin determinant işaretinden
anlar, dünya konumunu / normali / kamerayı sabit düzlem uniform'uyla (ymp) geri çevirir, yüz sarımını düzeltir ve
düzlemin altını atar.
"""

UNIT = 11                       # yansıma dokusunun birimi (malzeme 0..7, aux 12, env 13, gölge 14 / 15)
SCALE = 0.5                     # FBO çözünürlüğü / görünüm çözünürlüğü (varsayılan; viewmat.fx_scale geçersiz kılar)


def _scale():
    try:
        from . import viewmat
        return viewmat.fx_scale()
    except Exception:
        return SCALE
MIN_AREA = 0.02                 # düzlem adayı: yüz alanı >= MIN_AREA * köşegen^2

_GL_FRAMEBUFFER = 0x8D40
_GL_FRAMEBUFFER_BINDING = 0x8CA6
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE0 = 0x84C0

_st = dict(params=None, plane=None, eps=1.0, root_excl=None, busy=False, fbo=None, ctx=None, size=None,
           branch=None, sw=None, mt=None, mgrp=None, kids=None, sg=None, ivc=[], err=False, ok=False, inv_exp=1.0)


def _coin():
    from pivy import coin
    return coin


# ------------------------------------------------------------------ ortak uniform'lar
def params():
    """ymp: düzlem (n, d) | ymv: (1/w, 1/h, ox, oy) | ymc: kırpma düzlemi | ymf: (yüz çevirme, düzlemsel açık, eps, 1/pozlama)"""
    if _st["params"] is None:
        coin = _coin()
        ps = {}
        for nm, v in (("ymp", (0.0, 0.0, 1.0, 0.0)), ("ymv", (0.0, 0.0, 0.0, 0.0)),
                      ("ymf", (1.0, 0.0, 1.0, 1.0))):
            p = coin.SoShaderParameter4f()
            p.name = nm
            p.value = coin.SbVec4f(*v)
            p.ref()
            ps[nm] = p
        s = coin.SoShaderParameter1i()
        s.name = "ymirt"
        s.value = UNIT
        s.ref()
        ps["ymirt"] = s
        _st["params"] = ps
    return list(_st["params"].values())


def _set(nm, v, quiet=True):
    coin = _coin()
    p = _st["params"][nm] if isinstance(nm, str) else nm
    old = p.value.getValue().getValue()
    if max(abs(old[k] - v[k]) for k in range(4)) <= 1e-9:
        return
    if quiet:
        p.value.enableNotify(False)
    p.value.setValue(coin.SbVec4f(*v))
    if quiet:
        p.value.enableNotify(True)


def _apply_plane():
    """Dalın yansıma matrisi ve kırpma düzlemi (dünya koordinatı, aynalamadan önce): aynalanmış geometriden yalnız
    düzlemin altına düşen (gerçekte üstünde olan) kısım kalır."""
    if _st.get("mt") is None or _st["plane"] is None:
        return
    coin = _coin()
    n, d = _st["plane"]
    _st["mt"].matrix.setValue(_mirror_matrix(n, d))
    e = _st["eps"] * 0.25
    _st["cp"].plane.setValue(coin.SbPlane(coin.SbVec3f(-n[0], -n[1], -n[2]), -(d - e)))


def plane_param():
    """Vertex gölgelendiricisi için düzlem uniform'u (ymp)."""
    params()
    return _st["params"]["ymp"]


def busy():
    return _st["busy"]


def active():
    return _st["plane"] is not None


# ------------------------------------------------------------------ düzlem seçimi
def setup(cands, diag, cen):
    """cands: [(nesne, malzeme)] (yansıtıcı, saydam olmayan). En büyük, sahne merkezine bakan düz yüz yansıtıcı düzlem olur."""
    import FreeCAD
    best = None
    for o, m in cands:
        try:
            import Part
            shp = Part.getShape(o, "", needSubElement=False, transform=True)
        except Exception:
            shp = getattr(o, "Shape", None)
        if shp is None:
            continue
        try:
            faces = shp.Faces
        except Exception:
            continue
        for fc in faces:
            try:
                if fc.Surface.TypeId != "Part::GeomPlane":
                    continue
                a = fc.Area
                if a < MIN_AREA * diag * diag or (best is not None and a <= best[0]):
                    continue
                u0, u1, v0, v1 = fc.ParameterRange
                n = fc.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)       # yönelim dahil: dışa bakan normal
                c = fc.CenterOfMass
                to_c = FreeCAD.Vector(*cen) - c
                if n.dot(to_c) <= 0.0:                                 # sahneye bakmayan yüz (döşemenin altı)
                    continue
                n.normalize()
                best = (a, (n.x, n.y, n.z), n.dot(c), o)
            except Exception:
                continue
    if best is None:
        _st["plane"] = None
        _st["root_excl"] = None
        return False
    _a, n, d, o = best
    _st["plane"] = (n, d)
    _st["eps"] = max(0.05, diag * 2e-5)
    try:
        _st["root_excl"] = o.ViewObject.RootNode
    except Exception:
        _st["root_excl"] = None
    if _st["params"] is None:
        params()
    _set("ymp", (n[0], n[1], n[2], d), quiet=False)
    _apply_plane()
    _set("ymf", (1.0, 1.0, _st["eps"] * 4.0, _st["inv_exp"]), quiet=False)
    return True


def set_exposure(e):
    _st["inv_exp"] = 1.0 / max(float(e), 1e-4)
    if _st["params"] is not None and _st["plane"] is not None:
        _set("ymf", (1.0, 1.0, _st["eps"] * 4.0, _st["inv_exp"]), quiet=False)


def clear():
    _st["plane"] = None
    _st["root_excl"] = None
    _st["kids"] = None
    if _st["mgrp"] is not None:
        _st["mgrp"].removeAllChildren()
    if _st["params"] is not None:
        _set("ymf", (1.0, 0.0, 1.0, 1.0), quiet=False)


# ------------------------------------------------------------------ çizim
def _mirror_matrix(n, d):
    coin = _coin()
    v = []
    for i in range(4):
        for j in range(4):
            if i < 3 and j < 3:
                v.append((1.0 if i == j else 0.0) - 2.0 * n[i] * n[j])
            elif i == 3 and j < 3:
                v.append(2.0 * d * n[j])
            else:
                v.append(1.0 if i == j else 0.0)
    return coin.SbMatrix(*v)


def _fbo(ctx, w, h):
    from PySide6.QtOpenGL import QOpenGLFramebufferObject
    if _st["fbo"] is not None and _st["ctx"] is ctx and _st["size"] == (w, h):
        return _st["fbo"]
    _st["fbo"] = None
    fbo = QOpenGLFramebufferObject(w, h, QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
    f = ctx.functions()
    act = int(f.glGetIntegerv(0x84E0))
    f.glActiveTexture(_GL_TEXTURE0 + UNIT)
    f.glBindTexture(_GL_TEXTURE_2D, fbo.texture())
    f.glTexParameteri(_GL_TEXTURE_2D, 0x2801, 0x2601)     # MIN_FILTER = LINEAR
    f.glTexParameteri(_GL_TEXTURE_2D, 0x2800, 0x2601)     # MAG_FILTER = LINEAR
    f.glTexParameteri(_GL_TEXTURE_2D, 0x2802, 0x812F)     # WRAP_S = CLAMP_TO_EDGE
    f.glTexParameteri(_GL_TEXTURE_2D, 0x2803, 0x812F)
    f.glBindTexture(_GL_TEXTURE_2D, 0)
    f.glActiveTexture(act)
    _st["fbo"], _st["ctx"], _st["size"] = fbo, ctx, (w, h)
    return fbo


# ------------------------------------------------------------------ sahne dalı
# Aynalı geçiş ayrı bir render eylemiyle değil, ana çizimin içinde yapılır (iç içe eylem Coin'in gölgelendirici / doku
# durumunu bozuyordu). Model kökünün başına (geri çağırma düğümünden sonra) şu dal eklenir:
#   Separator[ pre_cb, Switch[ Group[ TransparencyType(BLEND), MatrixTransform(M), Group(seçim kökleri), post_cb ] ] ]
# Switch yalnız GL çiziminde (pre_cb'de) açılır: sınır kutusu / seçim eylemleri aynalanmış geometriyi görmez.
# pre_cb FBO'yu bağlar ve uniform'ları aynalı değerlere çeker; post_cb ana çerçeve arabelleğine döner.
_BRANCH = "YBT_VIEWMIRROR"


def _is_gl(action):
    coin = _coin()
    return action.isOfType(coin.SoGLRenderAction.getClassTypeId())


def _node_pass(action):
    """Eylem tüm sahneye mi uygulanıyor (NODE), yoksa kaydedilmiş yollara mı (gecikmiş saydamlık geçişi)?

    Gecikmiş geçişte Coin yalnız yolu gezer: dalın başındaki ön geri çağırma yoldan önceki kardeş olduğu için çalışır,
    ama yoldaki çocuğa (aynalanan sahne) girildikten sonra gezinti durur ve SON geri çağırmaya ulaşılmaz. O geçişte FBO
    bağlanırsa kare sonunda bağlı kalır: ana sahne yarım çözünürlüklü dokuya çizilir ve busy bayrağı takılı kalır."""
    coin = _coin()
    try:
        return action.getWhatAppliedTo() == coin.SoAction.NODE
    except Exception:
        return True


def _quiet_switch(on):
    sw = _st["sw"]
    v = 0 if on else -1
    if sw.whichChild.getValue() != v:
        sw.whichChild.enableNotify(False)
        sw.whichChild.setValue(v)
        sw.whichChild.enableNotify(True)


def _pre_cb(_ud, action):
    if not _is_gl(action):
        return
    _quiet_switch(False)
    if not _node_pass(action):                            # gecikmiş yol geçişi: son geri çağırmaya ulaşılmaz, FBO bağlama
        return
    if _st["plane"] is None or _st["err"]:
        return
    coin = _coin()
    from PySide6.QtGui import QOpenGLContext
    ctx = QOpenGLContext.currentContext()
    if ctx is None:
        return
    state = action.getState()
    n, d = _st["plane"]
    eps = _st["eps"]
    _st["frame"] = _st.get("frame", 0) + 1
    try:
        V = coin.SoViewingMatrixElement.get(state)
        iv = V.inverse()
        side = n[0] * iv[3][0] + n[1] * iv[3][1] + n[2] * iv[3][2] - d
        if side <= eps:                                     # kamera düzlemin arkasında: yansıma yok (gölgelendirici de bakar)
            return
        vpr = coin.SoViewportRegionElement.get(state)
        o_ = vpr.getViewportOriginPixels()
        s_ = vpr.getViewportSizePixels()
        vw, vh = int(s_[0]), int(s_[1])
        if vw < 4 or vh < 4:
            return
        sc = _scale()
        w, h = max(16, int(vw * sc)), max(16, int(vh * sc))
        _sync_kids()
        f = ctx.functions()
        fbo = _fbo(ctx, w, h)
        _st["pass"] = dict(prev=int(f.glGetIntegerv(_GL_FRAMEBUFFER_BINDING)), vp=(int(o_[0]), int(o_[1]), vw, vh))
        act = int(f.glGetIntegerv(0x84E0))                 # GL_ACTIVE_TEXTURE: Coin'in etkin birimi korunmalı
        f.glActiveTexture(_GL_TEXTURE0 + UNIT)
        f.glBindTexture(_GL_TEXTURE_2D, 0)                  # yazılan dokuyu aynı anda okuma
        f.glActiveTexture(act)
        f.glBindFramebuffer(_GL_FRAMEBUFFER, fbo.handle())
        f.glViewport(0, 0, w, h)
        sc = f.glIsEnabled(0x0C11)
        f.glDisable(0x0C11)                                # SCISSOR_TEST
        f.glDepthMask(1)
        f.glClearColor(0.0, 0.0, 0.0, 0.0)
        f.glClear(0x4000 | 0x0100)
        if sc:
            f.glEnable(0x0C11)
        _st["busy"] = True
        _quiet_switch(True)
    except Exception as ex:
        _st.pop("pass", None)
        _fail(ex)


def _post_cb(_ud, action):
    if not _is_gl(action):
        return
    _quiet_switch(False)
    if not _node_pass(action):
        return
    ps = _st.pop("pass", None)
    _st["busy"] = False
    if ps is None:
        return
    try:
        from PySide6.QtGui import QOpenGLContext
        f = QOpenGLContext.currentContext().functions()
        dbg = _st.get("dbg") or {}
        if dbg.get("save"):
            _st["fbo"].toImage().save(dbg.pop("save"))
            dbg["saved"] = _st.get("frame", 0)
        f.glBindFramebuffer(_GL_FRAMEBUFFER, ps["prev"])
        f.glViewport(*ps["vp"])
        x0, y0, vw, vh = ps["vp"]
        # Yalnız viewport gerçekten değişince yazılır. Saklanan değerle karşılaştırmak güvenilmez (parametre düğümü
        # float32 tutar, 1/genişlik float64'ten yuvarlanınca küçük bir fark kalır): her karede yazım, her yazım bir
        # bildirim, her bildirim bir yeniden çizim demek olurdu. Tamsayı viewport kesin sonuç verir.
        if _st.get("vpk") != ps["vp"]:
            _st["vpk"] = ps["vp"]
            _set("ymv", (1.0 / vw, 1.0 / vh, float(x0), float(y0)), quiet=False)
        act = int(f.glGetIntegerv(0x84E0))
        f.glActiveTexture(_GL_TEXTURE0 + UNIT)
        f.glBindTexture(_GL_TEXTURE_2D, _st["fbo"].texture())
        f.glActiveTexture(act)
        _st["ok"] = True
        _st["nerr"] = 0
    except Exception as ex:
        _fail(ex)


def _fail(ex):
    import traceback
    import FreeCAD
    _st["busy"] = False
    _st["tb"] = traceback.format_exc()
    _st["nerr"] = _st.get("nerr", 0) + 1
    if _st["nerr"] >= 20:                                  # sürekli hata: kapat (tek seferlik hatalar geçer)
        _st["err"] = True
    if _st["nerr"] in (1, 20):
        FreeCAD.Console.PrintWarning("YBT düzlemsel yansıma: %s\n%s" % (ex, _st["tb"]))


def _sync_kids():
    """Aynalı geçişte çizilecekler: model kökündeki belge seçim kök(ler)i (SoFCUnifiedSelection)."""
    sg = _st["sg"]
    kids = []
    for i in range(sg.getNumChildren()):
        ch = sg.getChild(i)
        try:
            if ch.getTypeId().getName().getString() == "SoFCUnifiedSelection":
                kids.append(ch)
        except Exception:
            pass
    key = tuple(int(k.this) for k in kids)
    if key != _st["kids"]:
        g = _st["mgrp"]
        g.removeAllChildren()
        for k in kids:
            g.addChild(k)
        _st["kids"] = key


def install(sg, ivc_params):
    """Dalı model köküne (geri çağırma düğümünden hemen sonra) ekler."""
    coin = _coin()
    params()
    _st["ivc"] = list(ivc_params)
    if _st.get("branch") is None:
        sep = coin.SoSeparator()
        sep.setName(_BRANCH)
        sep.renderCaching = coin.SoSeparator.OFF
        sep.boundingBoxCaching = coin.SoSeparator.OFF
        pre = coin.SoCallback()
        pre.setCallback(_pre_cb)
        sw = coin.SoSwitch()
        sw.whichChild = -1
        grp = coin.SoGroup()
        tt = coin.SoTransparencyType()
        tt.value = coin.SoTransparencyType.BLEND
        # Zorlama: nesnelerin kendi saydamlık türü (sıralı / gecikmeli) bu dalda geçersiz. Gecikmeli tür, aynalanan saydam
        # nesneleri ayrı bir yol geçişine erteler; o geçişte dalın son geri çağırması çalışmadığı için FBO bağlı kalırdı.
        tt.setOverride(True)
        cp = coin.SoClipPlane()                            # malzemesiz (sabit işlevli) nesneler için de düzlemin altı kırpılır
        mt = coin.SoMatrixTransform()
        mg = coin.SoGroup()
        post = coin.SoCallback()
        post.setCallback(_post_cb)
        grp.addChild(tt)
        grp.addChild(cp)
        grp.addChild(mt)
        grp.addChild(mg)
        grp.addChild(post)
        sw.addChild(grp)
        sep.addChild(pre)
        sep.addChild(sw)
        sep.ref()
        _st.update(branch=sep, sw=sw, pre=pre, mt=mt, cp=cp, mgrp=mg, kids=None)
        _apply_plane()
    sep = _st["branch"]
    old = _st.get("sg")
    if old is not None and old != sg:
        uninstall()
    if sg.findChild(sep) < 0:
        sg.ref()
        sg.insertChild(sep, 1 if sg.getNumChildren() > 0 else 0)
        _st["sg"] = sg
    return True


def uninstall():
    sg = _st.get("sg")
    sep = _st.get("branch")
    if sg is not None and sep is not None:
        try:
            i = sg.findChild(sep)
            if i >= 0:
                sg.removeChild(i)
        except Exception:
            pass
        try:
            sg.unref()
        except Exception:
            pass
    _st["sg"] = None
    if _st.get("mgrp") is not None:
        _st["mgrp"].removeAllChildren()
    _st["kids"] = None


def release(widget=None):
    """GL kaynaklarını bırak (görünüm kapanırken)."""
    if widget is not None:
        try:
            widget.makeCurrent()
        except Exception:
            widget = None
    _st["fbo"] = None
    _st["ctx"] = None
    _st["size"] = None
    if widget is not None:
        try:
            widget.doneCurrent()
        except Exception:
            pass
    uninstall()
    clear()
    _st["err"] = False
    _st["nerr"] = 0
