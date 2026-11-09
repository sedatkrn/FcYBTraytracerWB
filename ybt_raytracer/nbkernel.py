# -*- coding: utf-8 -*-
"""
numba çekirdeği: küme ağacında (accel._Clusters) en yakın isabet araması. Bu modülü yalnız `nbtrace` içe aktarır;
numba yoksa ImportError verir ve render numpy yoluna döner (accel._closest_general).

Çekirdek tek iş parçacıklı ve `nogil`: Linux'ta render zaten ayrı süreçlere (fork) bölünüyor, Windows'ta iş parçacıklarına;
her ikisinde de ek bir numba iş parçacığı havuzuna (fork ile uyumsuz) gerek yok. Sonuç numpy yoluyla aynı kuralları izler:
eşit uzaklıkta (TIE_TOL) en küçük (TIE_LAST ise en büyük) üçgen indeksi kazanır.
"""
import numpy as np
from numba import njit


@njit(nogil=True, cache=True, inline="always")
def _box(lx, ly, lz, hx, hy, hz, ox, oy, oz, ix, iy, iz):
    t1 = (lx - ox) * ix
    t2 = (hx - ox) * ix
    tn = min(t1, t2)
    tf = max(t1, t2)
    t1 = (ly - oy) * iy
    t2 = (hy - oy) * iy
    tn = max(tn, min(t1, t2))
    tf = min(tf, max(t1, t2))
    t1 = (lz - oz) * iz
    t2 = (hz - oz) * iz
    tn = max(tn, min(t1, t2))
    tf = min(tf, max(t1, t2))
    return tn, tf


@njit(nogil=True, cache=True)
def closest_batch(O, D, tb, ib, N, A, B, v0N, v0A, v0B, order, lo, hi, tlo, thi, offs, tcnt,
                  nlev, F, G, SZ, K, tol, slack, tie_last):
    """O, D: (R,3) ışınlar; tb: (R,) üst sınır (girişte tmax, çıkışta isabet mesafesi); ib: (R,) isabet üçgeni (-1 = yok)."""
    BIG = np.int64(1) << np.int64(60)
    stl = np.empty(128, np.int64)
    stn = np.empty(128, np.int64)
    stt = np.empty(128, np.float64)
    ctn = np.empty(16, np.float64)
    cid = np.empty(16, np.int64)
    for r in range(O.shape[0]):
        ox = O[r, 0]
        oy = O[r, 1]
        oz = O[r, 2]
        dx = D[r, 0]
        dy = D[r, 1]
        dz = D[r, 2]
        ix = 1.0 / (dx if abs(dx) >= 1e-12 else 1e-12)
        iy = 1.0 / (dy if abs(dy) >= 1e-12 else 1e-12)
        iz = 1.0 / (dz if abs(dz) >= 1e-12 else 1e-12)
        best = tb[r]
        bi = np.int64(-1)
        brank = BIG
        sp = 1
        stl[0] = nlev - 1
        stn[0] = 0
        stt[0] = -1e300
        while sp > 0:
            sp -= 1
            if stt[sp] > best + slack:
                continue
            L = stl[sp]
            n = stn[sp]
            cnt = 0
            if L > 0:
                base = offs[L - 1]
                for f in range(F):
                    c = n * F + f
                    if c >= tcnt[L - 1]:
                        break
                    g = base + c
                    tn, tf = _box(tlo[g, 0], tlo[g, 1], tlo[g, 2], thi[g, 0], thi[g, 1], thi[g, 2],
                                  ox, oy, oz, ix, iy, iz)
                    if tf >= 0.0 and tn <= tf and tn <= best + slack:
                        ctn[cnt] = max(tn, 0.0)
                        cid[cnt] = c
                        cnt += 1
                # azalan sıra: en yakın çocuk yığına en son girer, ilk işlenir
                for a in range(1, cnt):
                    kt = ctn[a]
                    kc = cid[a]
                    b = a - 1
                    while b >= 0 and ctn[b] < kt:
                        ctn[b + 1] = ctn[b]
                        cid[b + 1] = cid[b]
                        b -= 1
                    ctn[b + 1] = kt
                    cid[b + 1] = kc
                for j in range(cnt):
                    stl[sp] = L - 1
                    stn[sp] = cid[j]
                    stt[sp] = ctn[j]
                    sp += 1
            else:
                for gg in range(G):
                    c = n * G + gg
                    if c >= K:
                        break
                    tn, tf = _box(lo[c, 0], lo[c, 1], lo[c, 2], hi[c, 0], hi[c, 1], hi[c, 2],
                                  ox, oy, oz, ix, iy, iz)
                    if tf >= 0.0 and tn <= tf and tn <= best + slack:
                        ctn[cnt] = max(tn, 0.0)
                        cid[cnt] = c
                        cnt += 1
                # artan sıra: yakın küme önce, bulunan isabetten uzakta kalanlar atlanır
                for a in range(1, cnt):
                    kt = ctn[a]
                    kc = cid[a]
                    b = a - 1
                    while b >= 0 and ctn[b] > kt:
                        ctn[b + 1] = ctn[b]
                        cid[b + 1] = cid[b]
                        b -= 1
                    ctn[b + 1] = kt
                    cid[b + 1] = kc
                for j in range(cnt):
                    if ctn[j] > best + slack:
                        break
                    c = cid[j]
                    for s in range(SZ):
                        tid = order[c, s]
                        if tid < 0:
                            continue
                        dn = dx * N[tid, 0] + dy * N[tid, 1] + dz * N[tid, 2]
                        if dn == 0.0:
                            continue
                        t = (v0N[tid] - (ox * N[tid, 0] + oy * N[tid, 1] + oz * N[tid, 2])) / dn
                        if not (t > 0.0) or t == np.inf:
                            continue
                        u = (ox * A[tid, 0] + oy * A[tid, 1] + oz * A[tid, 2]) - v0A[tid] \
                            + t * (dx * A[tid, 0] + dy * A[tid, 1] + dz * A[tid, 2])
                        if not (u >= -1e-9):
                            continue
                        v = (ox * B[tid, 0] + oy * B[tid, 1] + oz * B[tid, 2]) - v0B[tid] \
                            + t * (dx * B[tid, 0] + dy * B[tid, 1] + dz * B[tid, 2])
                        if not (v >= -1e-9) or not (u + v <= 1.0 + 1e-9):
                            continue
                        rk = -tid if tie_last else tid
                        if t < best - tol or (abs(t - best) <= tol and rk < brank):
                            best = t
                            bi = tid
                            brank = rk
        tb[r] = best
        ib[r] = bi


@njit(nogil=True, cache=True)
def ao_blur_rows(ao, have, obj, nrm, tt, hit, R, gx, gy, r0, r1, acc, wsum):
    """render._ao_blur'un çekirdeği (satır bandı r0..r1): numpy yoluyla aynı ağırlık kuralı. ao: (h,w,C) float32,
    have: (h,w) float32 (ışın atılmış mı), obj: (h,w) int32, nrm: (h,w,3) float32, tt: (h,w) float32, hit: (h,w) bool."""
    h, w, C = ao.shape
    sig2 = np.float32(2.0 * (R * 0.55) ** 2)
    for y in range(r0, r1):
        for x in range(w):
            if not hit[y, x]:
                continue
            o = obj[y, x]
            t0 = tt[y, x]
            gxc, gyc = gx[y, x], gy[y, x]
            tol = max(t0 * np.float32(0.02), np.float32(1e-9))
            nx, ny, nz = nrm[y, x, 0], nrm[y, x, 1], nrm[y, x, 2]
            ws = np.float32(0.0)
            for dy in range(-R, R + 1):
                yy = y + dy
                if yy < 0 or yy >= h:
                    continue
                for dx in range(-R, R + 1):
                    xx = x + dx
                    if xx < 0 or xx >= w:
                        continue
                    if have[yy, xx] <= 0.0 or obj[yy, xx] != o:
                        continue
                    if abs(tt[yy, xx] - (t0 + gxc * dx + gyc * dy)) >= tol * (np.float32(1.0) + np.float32(0.5) * np.float32(np.sqrt(np.float32(dx * dx + dy * dy)))):
                        continue
                    nd = nrm[yy, xx, 0] * nx + nrm[yy, xx, 1] * ny + nrm[yy, xx, 2] * nz
                    k = (nd - np.float32(0.7)) / np.float32(0.3)
                    if k <= 0.0:
                        continue
                    if k > 1.0:
                        k = np.float32(1.0)
                    wg = k * np.float32(np.exp(-np.float32(dx * dx + dy * dy) / sig2)) * have[yy, xx]
                    ws += wg
                    for c in range(C):
                        acc[y, x, c] += wg * ao[yy, xx, c]
            wsum[y, x] = ws
