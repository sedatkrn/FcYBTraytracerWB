# -*- coding: utf-8 -*-
"""
İsteğe bağlı OpenCL (GPU) hızlandırıcı: küme ağacında (accel._Clusters) en yakın isabet araması.

Ayar `ACCEL`: "gpu" = 32 bit (hızlı; numba/numpy yoluyla üçgenlerin ~%99,99'u aynı, gölge kenarında tek tük piksel
farkı olabilir), "gpu64" = 64 bit (cl_khr_fp64; numba/numpy yoluyla birebir aynı, oyun kartlarında 64 bit yavaştır).
Varsayılan "auto" GPU KULLANMAZ: zayıf tümleşik grafikte (ör. Ryzen 5625U, 7 CU) çok çekirdekli numba GPU'dan hızlı
ölçüldü; ayrı ekran kartında GPU belirgin hızlıdır. Kullanıcı Seçenekler penceresinden açar.

nbkernel.closest_batch'in birebir aktarımı: her ışın bir iş öğesi, aynı eşitlik kuralı (TIE_TOL / TIE_LAST).
Sözleşme nbtrace.closest / accel._closest_general ile aynı: closest(sc, cl, O, D, tk, tmax) -> (tb, ib).
pyopencl <FreeCAD kullanıcı verisi>/YBTRayTracer/pylibs içinden yüklenir (nbtrace.libs_dir); install() oraya kurar.
GPU tek aygıttır: çağrılar bir kilitle sıraya girer; render GPU açıkken süreç (fork) yerine iş parçacıklarıyla çalışır
(OpenCL bağlamı fork ile güvenli değil). Bir hata olursa GPU bu oturumda kapanır, arama numba/numpy yoluna döner.
"""
import sys
import threading

import numpy as np

from . import settings as S

_SRC = r"""
#ifdef YBT_FP64
#pragma OPENCL EXTENSION cl_khr_fp64 : enable
typedef double real;
#define R(x) x
#else
typedef float real;
#define R(x) x##f
#endif
#define STK 128

inline void box(real lx, real ly, real lz, real hx, real hy, real hz,
                real ox, real oy, real oz, real ix, real iy, real iz, real *tn, real *tf)
{
    real t1 = (lx - ox) * ix, t2 = (hx - ox) * ix;
    real n = fmin(t1, t2), f = fmax(t1, t2);
    t1 = (ly - oy) * iy; t2 = (hy - oy) * iy;
    n = fmax(n, fmin(t1, t2)); f = fmin(f, fmax(t1, t2));
    t1 = (lz - oz) * iz; t2 = (hz - oz) * iz;
    n = fmax(n, fmin(t1, t2)); f = fmin(f, fmax(t1, t2));
    *tn = n; *tf = f;
}

__kernel void closest(__global const real *O, __global const real *D, __global real *tb, __global long *ib,
                      __global const real *N, __global const real *A, __global const real *B,
                      __global const real *v0N, __global const real *v0A, __global const real *v0B,
                      __global const long *order, __global const real *lo, __global const real *hi,
                      __global const real *tlo, __global const real *thi, __global const long *offs,
                      __global const long *tcnt, const int nlev, const int F, const int G, const int SZ, const long K,
                      const real tol, const real slack, const int tie_last, const int Rn)
{
    int r = get_global_id(0);
    if (r >= Rn) return;
    const long BIG = ((long)1) << 60;
    const real ZERO = R(0.0), ONE = R(1.0), EPS = R(1e-9), TINY = R(1e-12);
    int stl[STK]; long stn[STK]; real stt[STK];
    real ctn[16]; long cid[16];
    real ox = O[3*r], oy = O[3*r+1], oz = O[3*r+2];
    real dx = D[3*r], dy = D[3*r+1], dz = D[3*r+2];
    real ix = ONE / (fabs(dx) >= TINY ? dx : TINY);
    real iy = ONE / (fabs(dy) >= TINY ? dy : TINY);
    real iz = ONE / (fabs(dz) >= TINY ? dz : TINY);
    real best = tb[r];
    long bi = -1, brank = BIG;
    int sp = 1;
    stl[0] = nlev - 1; stn[0] = 0; stt[0] = -INFINITY;
    while (sp > 0) {
        sp--;
        if (stt[sp] > best + slack) continue;
        int L = stl[sp]; long n = stn[sp]; int cnt = 0;
        real tn, tf;
        if (L > 0) {
            long base = offs[L - 1];
            for (int f = 0; f < F; f++) {
                long c = n * F + f;
                if (c >= tcnt[L - 1]) break;
                long g = base + c;
                box(tlo[3*g], tlo[3*g+1], tlo[3*g+2], thi[3*g], thi[3*g+1], thi[3*g+2], ox, oy, oz, ix, iy, iz, &tn, &tf);
                if (tf >= ZERO && tn <= tf && tn <= best + slack) { ctn[cnt] = fmax(tn, ZERO); cid[cnt] = c; cnt++; }
            }
            for (int a = 1; a < cnt; a++) {                    /* azalan: en yakın çocuk en son girer, ilk işlenir */
                real kt = ctn[a]; long kc = cid[a]; int b = a - 1;
                while (b >= 0 && ctn[b] < kt) { ctn[b+1] = ctn[b]; cid[b+1] = cid[b]; b--; }
                ctn[b+1] = kt; cid[b+1] = kc;
            }
            for (int j = 0; j < cnt && sp < STK; j++) { stl[sp] = L - 1; stn[sp] = cid[j]; stt[sp] = ctn[j]; sp++; }
        } else {
            for (int gg = 0; gg < G; gg++) {
                long c = n * G + gg;
                if (c >= K) break;
                box(lo[3*c], lo[3*c+1], lo[3*c+2], hi[3*c], hi[3*c+1], hi[3*c+2], ox, oy, oz, ix, iy, iz, &tn, &tf);
                if (tf >= ZERO && tn <= tf && tn <= best + slack) { ctn[cnt] = fmax(tn, ZERO); cid[cnt] = c; cnt++; }
            }
            for (int a = 1; a < cnt; a++) {                    /* artan: yakın küme önce */
                real kt = ctn[a]; long kc = cid[a]; int b = a - 1;
                while (b >= 0 && ctn[b] > kt) { ctn[b+1] = ctn[b]; cid[b+1] = cid[b]; b--; }
                ctn[b+1] = kt; cid[b+1] = kc;
            }
            for (int j = 0; j < cnt; j++) {
                if (ctn[j] > best + slack) break;
                long c = cid[j];
                for (int s = 0; s < SZ; s++) {
                    long tid = order[c * SZ + s];
                    if (tid < 0) continue;
                    real nx = N[3*tid], ny = N[3*tid+1], nz = N[3*tid+2];
                    real dn = dx * nx + dy * ny + dz * nz;
                    if (dn == ZERO) continue;
                    real t = (v0N[tid] - (ox * nx + oy * ny + oz * nz)) / dn;
                    if (!(t > ZERO) || isinf(t)) continue;
                    real u = (ox * A[3*tid] + oy * A[3*tid+1] + oz * A[3*tid+2]) - v0A[tid]
                           + t * (dx * A[3*tid] + dy * A[3*tid+1] + dz * A[3*tid+2]);
                    if (!(u >= -EPS)) continue;
                    real v = (ox * B[3*tid] + oy * B[3*tid+1] + oz * B[3*tid+2]) - v0B[tid]
                           + t * (dx * B[3*tid] + dy * B[3*tid+1] + dz * B[3*tid+2]);
                    if (!(v >= -EPS) || !(u + v <= ONE + EPS)) continue;
                    long rk = tie_last ? -tid : tid;
                    if (t < best - tol || (fabs(t - best) <= tol && rk < brank)) { best = t; bi = tid; brank = rk; }
                }
            }
        }
    }
    tb[r] = best;
    ib[r] = bi;
}
"""

_ST = {"dev": None, "err": None, "prg": {}, "failed": None}
_LOCK = threading.Lock()
PKGS = ("pyopencl", "pytools", "platformdirs", "typing_extensions", "siphash24")


PARAM = "User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer"


def choice():
    """Seçenekler penceresindeki makineye özgü seçim (FreeCAD parametresi Accel): "auto" / "gpu" / "gpu64"."""
    try:
        import FreeCAD
        return FreeCAD.ParamGet(PARAM).GetString("Accel", "auto") or "auto"
    except Exception:
        return "auto"


def set_choice(v):
    try:
        import FreeCAD
        FreeCAD.ParamGet(PARAM).SetString("Accel", str(v))
    except Exception:
        pass
    _ST["failed"] = None                               # yeniden denensin


def mode():
    """İstenen GPU kipi: None (kapalı), 32 ya da 64. Ayar ACCEL (API'den) "gpu"/"gpu64" ise o, "numpy" ise kapalı,
    değilse makinenin Seçenekler'deki seçimi."""
    a = str(getattr(S, "ACCEL", "auto")).lower()
    if a == "numpy":
        return None
    if a not in ("gpu", "gpu64"):
        a = choice()
    return 64 if a == "gpu64" else 32 if a == "gpu" else None


def _cl():
    from . import nbtrace
    d = nbtrace.libs_dir()
    if d not in sys.path:
        sys.path.append(d)
    import pyopencl as cl
    return cl


def _device():
    """(cl, ctx, queue, aygıt) ya da None. Önce ayrı ekran kartı (en çok işlem birimi), sonra tümleşik."""
    if _ST["dev"] is not None or _ST["err"] is not None:
        return _ST["dev"]
    try:
        cl = _cl()
        devs = [d for p in cl.get_platforms() for d in p.get_devices(device_type=cl.device_type.GPU)]
        if not devs:
            raise RuntimeError("OpenCL GPU aygıtı yok (sürücü / ICD kurulu mu?)")
        dev = max(devs, key=lambda d: (not d.host_unified_memory, d.max_compute_units))
        ctx = cl.Context([dev])
        _ST["dev"] = (cl, ctx, cl.CommandQueue(ctx), dev)
    except Exception as e:
        _ST["err"] = "%s: %s" % (type(e).__name__, e)
    return _ST["dev"]


def _program(bits):
    if bits in _ST["prg"]:
        return _ST["prg"][bits]
    cl, ctx, q, dev = _device()
    if bits == 64 and "cl_khr_fp64" not in dev.extensions:
        raise RuntimeError("aygıt 64 bit (cl_khr_fp64) desteklemiyor")
    p = cl.Program(ctx, _SRC).build(options=["-DYBT_FP64"] if bits == 64 else [])
    _ST["prg"][bits] = p
    return p


def available():
    """İstenen GPU kipi açık ve kullanılabilir mi (derleme dahil)."""
    bits = mode()
    if bits is None or _ST["failed"] is not None or _device() is None:
        return False
    try:
        _program(bits)
        return True
    except Exception as e:
        _ST["failed"] = "%s: %s" % (type(e).__name__, e)
        return False


def status():
    """Durum metni (Seçenekler penceresi)."""
    d = _device()
    if d is None:
        return "OpenCL yok: %s" % _ST["err"]
    dev = d[3]
    s = "%s (%d CU, %s, 64 bit %s)" % (dev.name.strip(), dev.max_compute_units,
                                       "tümleşik" if dev.host_unified_memory else "ayrı kart",
                                       "var" if "cl_khr_fp64" in dev.extensions else "yok")
    if _ST["failed"]:
        s += " | kapatıldı: " + _ST["failed"]
    return s


def _prep(sc, cl_, bits):
    """Sahne ve küme ağacı GPU belleğine bir kez yüklenir (kümede, kesinliğe göre önbellek)."""
    key = "_clprep%d" % bits
    p = getattr(cl_, key, None)
    if p is not None and p[0] is sc:
        return p[1]
    cl, ctx, q, dev = _device()
    from . import nbtrace
    (N, A, B, v0N, v0A, v0B, order, lo, hi, tlo, thi, offs, tcnt, nlev, F, G, SZ, K) = nbtrace._prep(sc, cl_)
    ft = np.float64 if bits == 64 else np.float32
    mf = cl.mem_flags
    buf = lambda a: cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=np.ascontiguousarray(a))
    fl = lambda a: buf(np.asarray(a, dtype=ft))
    arr = ([fl(N), fl(A), fl(B), fl(v0N), fl(v0A), fl(v0B), buf(order), fl(lo), fl(hi), fl(tlo), fl(thi),
            buf(offs), buf(tcnt)],
           (np.int32(nlev), np.int32(F), np.int32(G), np.int32(SZ), np.int64(K)))
    setattr(cl_, key, (sc, arr))
    return arr


def closest(sc, cl_, O, D, tk=None, tmax=None, step=1 << 20):
    """accel._closest_general / nbtrace.closest ile aynı sözleşme: (tb, ib)."""
    bits = mode() or 32
    cl, ctx, q, dev = _device()
    prg = _program(bits)
    ft = np.float64 if bits == 64 else np.float32
    R = len(O)
    if tmax is not None and np.ndim(tmax) > 0:
        tb = np.array(tmax, dtype=np.float64)
    else:
        tb = np.full(R, np.inf if tmax is None else float(tmax))
    ib = np.full(R, -1, dtype=np.int64)
    if R == 0:
        return tb, ib
    O = np.ascontiguousarray(O, dtype=ft)
    D = np.ascontiguousarray(D, dtype=ft)
    tol = float(S.TIE_TOL) * sc.diag
    slack = tol + 1e-9 * sc.diag
    tl = np.int32(1 if bool(S.TIE_LAST) else 0)
    mf = cl.mem_flags
    with _LOCK:                                        # tek aygıt: render iş parçacıkları sırayla kullanır
        bufs, ints = _prep(sc, cl_, bits)
        for s in range(0, R, step):
            if tk is not None:
                tk()
            e = min(R, s + step)
            n = e - s
            tbs = np.ascontiguousarray(tb[s:e], dtype=ft)
            bO = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=O[s:e])
            bD = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=D[s:e])
            btb = cl.Buffer(ctx, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=tbs)
            bib = cl.Buffer(ctx, mf.WRITE_ONLY, size=n * 8)
            prg.closest(q, (((n + 63) // 64) * 64,), (64,), bO, bD, btb, bib, *bufs, *ints,
                        ft(tol), ft(slack), tl, np.int32(n))
            out_i = np.empty(n, np.int64)
            cl.enqueue_copy(q, tbs, btb)
            cl.enqueue_copy(q, out_i, bib).wait()
            tb[s:e] = tbs
            ib[s:e] = out_i
    return tb, ib


def disable(err):
    """Çağrı sırasında hata: bu oturumda GPU kapanır (arama numba/numpy yoluna döner)."""
    _ST["failed"] = err
    try:
        import FreeCAD
        FreeCAD.Console.PrintWarning("YBT Raytracer: GPU hızlandırıcı kapatıldı (%s); numba / numpy ile devam.\n" % err)
    except Exception:
        pass


def install(log=None):
    """pyopencl'i FreeCAD'in Python'una dokunmadan pylibs klasörüne pip ile kurar. Döner: (başarılı, çıktı).
    OpenCL sürücüsü (ICD) ayrıca gerekir: Windows'ta ekran kartı sürücüsüyle gelir; Linux'ta ör. ROCm / Mesa rusticl."""
    import subprocess
    from . import nbtrace
    py = nbtrace._find_python()
    d = nbtrace.libs_dir()
    r = subprocess.run([py, "-m", "pip", "install", "--no-deps", "--upgrade", "--target", d,
                        "--disable-pip-version-check"] + list(PKGS), capture_output=True, text=True, timeout=900)
    _ST.update(dev=None, err=None, prg={}, failed=None)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
