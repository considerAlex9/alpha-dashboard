"""비밀번호로 잠그는 데이터 — 브라우저(웹 암호화 기능)에서 같은 비밀번호로 풀 수 있는 형식.

비밀번호 → PBKDF2(SHA-256) 로 열쇠를 만들고 AES-GCM 으로 암호화한다.
저장소와 사이트에는 암호문만 올라가므로, 비밀번호 없이는 내용을 볼 수 없다.
비밀번호는 환경변수 DASH_PASSWORD (.env 또는 깃허브 비밀 보관함).
"""
import base64
import hashlib
import json
import os

ITERATIONS = 250_000     # 비밀번호를 무작위로 대입해 보는 공격을 느리게 만드는 반복 횟수


def encrypt(obj, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, 32)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    b64 = lambda x: base64.b64encode(x).decode()   # noqa: E731
    return {"v": 1, "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt(box, password):
    """시험용 — 사이트에서는 브라우저가 푼다"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = base64.b64decode
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), d(box["salt"]), box["iter"], 32)
    return json.loads(AESGCM(key).decrypt(d(box["iv"]), d(box["ct"]), None))
