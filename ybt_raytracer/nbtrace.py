# -*- coding: utf-8 -*-
"""
İsteğe bağlı hızlandırıcı: numba varsa küme ağacı araması (accel._closest_general) derlenmiş çekirdekle (nbkernel.py) yapılır.
numba yoksa ya da ayar "numpy" ise hiçbir şey değişmez; render numpy yoluyla çalışır ve sonuç aynıdır.

numba arama sırası: FreeCAD'in kendi Python'u -> `<FreeCAD kullanıcı verisi>/YBTRayTracer/pylibs` (ya da YBT_PYLIBS ortam
değişkeni). Kurulum için `install()`: numba'yı FreeCAD'in Python'una dokunmadan o klasöre pip ile kurar.
Derleme önbelleği `<FreeCAD kullanıcı verisi>/YBTRayTracer/numba_cache` içindedir (ilk derleme ~3 sn, sonra yükleme).
"""
import os
import shutil
import subprocess
import sys

import numpy as np

from . import settings as S

_ST = {"tried": False, "kern": None, "err": "", "ver": ""}


def _user_dir():
    try:
        import FreeCAD
        base = FreeCAD.getUserAppDataDir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".ybt_raytracer")
    return os.path.join(base, "YBTRayTracer")


def libs_dir():
    return os.environ.get("YBT_PYLIBS") or os.path.join(_user_dir(), "pylibs")


def _load():
    """numba ve çekirdeği bir kez yüklemeyi dener. Döner: nbkernel modülü ya da None."""
    if _ST["tried"]:
        return _ST["kern"]
    _ST["tried"] = True
    try:
        d = libs_dir()
        if os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)                      # sona: FreeCAD'in kendi numpy'ı öncelikli kalır
        os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(_user_dir(), "numba_cache"))
        from . import nbkernel
        import numba
        _ST["kern"], _ST["ver"] = nbkernel, numba.__version__
    except Exception as e:                          # numba yok / uyumsuz: numpy yolu
        _ST["kern"], _ST["err"] = None, "%s: %s" % (type(e).__name__, e)
    return _ST["kern"]


def available():
    """Ayar izin veriyorsa ve numba yüklenebiliyorsa True."""
    if str(getattr(S, "ACCEL", "auto")).lower() == "numpy":
        return False
    return _load() is not None


def status():
    """Durum metni (arayüz / hata ayıklama için)."""
    if str(getattr(S, "ACCEL", "auto")).lower() == "numpy":
        return "numpy (ayar: ACCEL = numpy)"
    k = _load()
    return ("numba %s" % _ST["ver"]) if k is not None else ("numpy (numba yok: %s)" % _ST["err"])


def _prep(sc, cl):
    """Küme ağacını ve sahne dizilerini çekirdeğin istediği düz dizilere çevirir (kümede önbelleğe alınır)."""
    p = getattr(cl, "_nbprep", None)
    if p is not None and p[0] is sc:
        return p[1]
    nlev = len(cl.tlo)
    offs = np.zeros(nlev, dtype=np.int64)
    a = 0
    for l in range(nlev):
        offs[l] = a
        a += len(cl.tlo[l])
    f64 = lambda x: np.ascontiguousarray(x, dtype=np.float64)
    arr = (f64(sc.N), f64(sc.A), f64(sc.B), f64(sc.v0N), f64(sc.v0A), f64(sc.v0B),
           np.ascontiguousarray(cl.order, dtype=np.int64), f64(cl.lo), f64(cl.hi),
           f64(np.concatenate(cl.tlo)), f64(np.concatenate(cl.thi)), offs,
           np.asarray(cl.tcnt, dtype=np.int64), int(nlev), int(cl.F), int(cl.G), int(cl.SZ), int(cl.K))
    cl._nbprep = (sc, arr)
    return arr


def closest(sc, cl, O, D, tk=None, tmax=None, step=65536):
    """accel._closest_general ile aynı sözleşme: (tb, ib). tmax: None / sayı / ışın başına dizi."""
    kern = _load()
    R = len(O)
    if tmax is not None and np.ndim(tmax) > 0:
        tb = np.array(tmax, dtype=np.float64)
    else:
        tb = np.full(R, np.inf if tmax is None else float(tmax))
    ib = np.full(R, -1, dtype=np.int64)
    if R == 0:
        return tb, ib
    O = np.ascontiguousarray(O, dtype=np.float64)
    D = np.ascontiguousarray(D, dtype=np.float64)
    prep = _prep(sc, cl)
    tol = float(S.TIE_TOL) * sc.diag
    slack = tol + 1e-9 * sc.diag
    tl = bool(S.TIE_LAST)
    for s in range(0, R, step):
        if tk is not None:
            tk()
        kern.closest_batch(O[s:s + step], D[s:s + step], tb[s:s + step], ib[s:s + step], *prep, tol, slack, tl)
    return tb, ib


def warm(sc, cl):
    """Çekirdeği render'dan ÖNCE (fork öncesi, ana süreçte) derler/yükler; çocuk süreçler hazır kodu miras alır."""
    if not available():
        return
    try:
        c = np.asarray(sc.tri[0]).mean(0)
        closest(sc, cl, np.tile(c, (8, 1)), np.tile((0.0, 0.0, 1.0), (8, 1)), None, 1.0)
    except Exception as e:
        _ST["kern"], _ST["err"] = None, "%s: %s" % (type(e).__name__, e)


def _find_python():
    """FreeCAD'in Python yorumlayıcısı (AppImage / Windows paketi / sistem)."""
    names = ("python.exe", "python3.exe", "python", "python3") if os.name == "nt" else ("python", "python3")
    dirs = [os.path.dirname(sys.executable), os.path.join(sys.exec_prefix, "bin"), sys.exec_prefix,
            os.path.join(sys.exec_prefix, "Scripts")]
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return shutil.which("python3") or shutil.which("python")


def install(log=None):
    """numba'yı FreeCAD'in Python'una dokunmadan libs_dir() içine pip ile kurar. Döner: (başarılı, çıktı)."""
    py = _find_python()
    if not py:
        return False, "Python yorumlayıcısı bulunamadı"
    d = libs_dir()
    os.makedirs(d, exist_ok=True)
    r = subprocess.run([py, "-m", "pip", "install", "--no-deps", "--upgrade", "--target", d,
                        "--disable-pip-version-check", "numba", "llvmlite"], capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    if log:
        log(out)
    _ST.update(tried=False, kern=None, err="", ver="")   # yeniden başlatmadan algılansın (bir sonraki _load yeniden dener)
    return r.returncode == 0, out[-2000:]
