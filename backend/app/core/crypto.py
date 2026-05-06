"""AES 加密/解密工具，用于安全存储 API Key 等敏感数据。

使用 Fernet（基于 AES-128-CBC + HMAC-SHA256）提供对称加密，
密钥由 settings.ENCRYPT_KEY（Fernet 兼容的 url-safe base64 编码 32 字节密钥）驱动。
"""

from cryptography.fernet import Fernet

from app.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.ENCRYPT_KEY.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
