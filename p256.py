"""纯标准库 ECDSA P-256 / SHA-256 签名。

只用于 Trae 续期令牌时的“设备证明”。不依赖 cryptography，
所以在 arm32/arm64 机顶盒这种没有预编译包的环境也能直接跑。
"""
import base64
import hashlib
import os

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k, pt):
    r = None
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


# ---------- 最小 DER 解析：取出 PEM 里的 32 字节私钥 ----------
def _tlv(buf, i):
    tag = buf[i]
    i += 1
    ln = buf[i]
    i += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + ln], i + ln


def _children(body):
    out, i = [], 0
    while i < len(body):
        tag, val, i = _tlv(body, i)
        out.append((tag, val))
    return out


def private_scalar(pem):
    """支持 PKCS#8（BEGIN PRIVATE KEY）和 SEC1（BEGIN EC PRIVATE KEY）。"""
    b64 = "".join(l.strip() for l in pem.strip().splitlines() if "-----" not in l)
    der = base64.b64decode(b64)
    _, body, _ = _tlv(der, 0)
    kids = _children(body)
    if len(kids) >= 3 and kids[1][0] == 0x30 and kids[2][0] == 0x04:  # PKCS#8
        _, sec1_body, _ = _tlv(kids[2][1], 0)
        kids = _children(sec1_body)
    if len(kids) >= 2 and kids[1][0] == 0x04:
        return int.from_bytes(kids[1][1], "big")
    raise ValueError("不是有效的 P-256 私钥 PEM")


def _der_int(x):
    b = x.to_bytes(32, "big").lstrip(b"\x00") or b"\x00"
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + bytes([len(b)]) + b


def sign_b64(pem, data):
    """ECDSA(P-256, SHA-256)，返回 base64(DER)，s 取低位（与桌面端一致）。"""
    d = private_scalar(pem)
    z = int.from_bytes(hashlib.sha256(data).digest(), "big")
    while True:
        k = int.from_bytes(os.urandom(32), "big") % (N - 1) + 1
        r = _mul(k, G)[0] % N
        if r == 0:
            continue
        s = pow(k, -1, N) * (z + r * d) % N
        if s == 0:
            continue
        if s > N // 2:
            s = N - s
        body = _der_int(r) + _der_int(s)
        return base64.b64encode(b"\x30" + bytes([len(body)]) + body).decode("ascii")
