"""纯标准库的二维码生成器（字节模式、纠错等级 M、版本 1–40 自动选择）。

只用来在网页里画登录二维码，所以功能很克制。写法参考 QR Code 规范与 Nayuki 的公开实现思路。
encode(text) 返回不含静区的 0/1 矩阵（list[list[bool]]）。
"""

# 纠错等级 M：每块的纠错码字数、块数（下标 0 对应版本 1）
_ECC_PER_BLOCK = [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28,
                  28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28]
_NUM_BLOCKS = [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28,
               29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49]
_FORMAT_ECL_M = 0  # M 的格式位


def _raw_modules(ver):
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        a = ver // 7 + 2
        n -= (25 * a - 10) * a - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver):
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver - 1] * _NUM_BLOCKS[ver - 1]


# ---------- Reed-Solomon（GF(256)，多项式 0x11D） ----------
def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data, divisor):
    res = [0] * len(divisor)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(divisor):
            res[i] ^= _gf_mul(c, factor)
    return res


def _bit(x, i):
    return (x >> i) & 1 != 0


# ---------- 画矩阵 ----------
class _Matrix:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        n = self.size
        self.m = [[False] * n for _ in range(n)]
        self.fn = [[False] * n for _ in range(n)]
        self._draw_function_patterns()

    def _set(self, x, y, dark):
        self.m[y][x] = dark
        self.fn[y][x] = True

    def _align_positions(self):
        if self.ver == 1:
            return []
        n = self.ver // 7 + 2
        step = 26 if self.ver == 32 else (self.ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
        res = [self.size - 7 - i * step for i in range(n - 1)] + [6]
        return res[::-1]

    def _draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for (x, y) in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < n and 0 <= yy < n:
                        self._set(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self._align_positions()
        last = len(pos) - 1
        for i in range(len(pos)):
            for j in range(len(pos)):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)  # 先占位，后面按真实掩码重画
        self._draw_version()

    def _draw_format(self, mask):
        n = self.size
        data = _FORMAT_ECL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        for i in range(6):
            self._set(8, i, _bit(bits, i))
        self._set(8, 7, _bit(bits, 6))
        self._set(8, 8, _bit(bits, 7))
        self._set(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self._set(14 - i, 8, _bit(bits, i))
        for i in range(8):
            self._set(n - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, _bit(bits, i))
        self._set(8, n - 8, True)

    def _draw_version(self):
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            a, b = self.size - 11 + i % 3, i // 3
            self._set(a, b, _bit(bits, i))
            self._set(b, a, _bit(bits, i))

    def draw_codewords(self, data):
        n, i, right = self.size, 0, self.size - 1
        while right >= 1:  # 从右往左两列一组，蛇形填充；跳过第 6 列（时序线）
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = _bit(data[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        f = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]

    def penalty(self):
        n, m, score = self.size, self.m, 0
        lines = [m[y] for y in range(n)] + [[m[y][x] for y in range(n)] for x in range(n)]
        for line in lines:  # N1 连续同色 + N3 类定位图案
            run = 1
            for k in range(1, n):
                if line[k] == line[k - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
            s = "".join("1" if v else "0" for v in line)
            for pat in ("10111010000", "00001011101"):
                start = 0
                while True:
                    k = s.find(pat, start)
                    if k < 0:
                        break
                    score += 40
                    start = k + 1
        for y in range(n - 1):  # N2 2x2 同色块
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(v for row in m for v in row)  # N4 黑白比例
        k = (abs(dark * 20 - n * n * 10) + n * n - 1) // (n * n) - 1
        return score + max(k, 0) * 10


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]


def _build_codewords(payload, ver):
    cap = _data_codewords(ver)
    bits = [0, 1, 0, 0]  # 字节模式
    cc = 8 if ver <= 9 else 16
    bits += [(len(payload) >> i) & 1 for i in range(cc - 1, -1, -1)]
    for b in payload:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < cap:
        data.append(pad)
        pad ^= 0xEC ^ 0x11

    nb, ecc_len = _NUM_BLOCKS[ver - 1], _ECC_PER_BLOCK[ver - 1]
    raw = _raw_modules(ver) // 8
    short_blocks, short_len = nb - raw % nb, raw // nb
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(nb):
        dat = data[k:k + short_len - ecc_len + (0 if i < short_blocks else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < short_blocks:
            dat = dat + [0]
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                out.append(blk[i])
    return out


def encode(text, mask=None):
    """把文本编成二维码矩阵（不含静区）。mask 只用于测试，默认自动选罚分最低的。"""
    payload = text.encode("utf-8")
    for ver in range(1, 41):
        cc = 8 if ver <= 9 else 16
        if 4 + cc + 8 * len(payload) <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("内容太长，二维码放不下")
    codewords = _build_codewords(payload, ver)
    best = None
    for msk in ([mask] if mask is not None else range(8)):
        q = _Matrix(ver)
        q.draw_codewords(codewords)
        q.apply_mask(msk)
        q._draw_format(msk)
        if best is None or (mask is None and q.penalty() < best[0]):
            best = (q.penalty() if mask is None else 0, q)
    return best[1].m
