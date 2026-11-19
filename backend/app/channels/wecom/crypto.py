"""企业微信回调消息加解密（官方 WXBizMsgCrypt 算法的 Python 实现）。

签名：sha1(sort([token, timestamp, nonce, encrypt]))
密文：AES-256-CBC，key = base64(EncodingAESKey + "=")，iv = key[:16]，PKCS#7 补位到 32 字节
明文：16 字节随机串 + 4 字节网络序长度 + 消息 + receiveid（corpid）
"""

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK_SIZE = 32


class WeComCryptoError(Exception):
    pass


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        if len(encoding_aes_key) != 43:
            raise WeComCryptoError("EncodingAESKey 长度应为 43")
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        parts = sorted([self.token, timestamp, nonce, encrypted])
        return hashlib.sha1("".join(parts).encode()).hexdigest()

    def verify(self, msg_signature: str, timestamp: str, nonce: str, encrypted: str) -> None:
        if not hmac.compare_digest(self.signature(timestamp, nonce, encrypted), msg_signature or ""):
            raise WeComCryptoError("签名校验失败")

    def decrypt(self, encrypted: str) -> str:
        try:
            cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
            decryptor = cipher.decryptor()
            plain = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
            pad = plain[-1]
            if pad < 1 or pad > BLOCK_SIZE:
                raise WeComCryptoError("补位错误")
            plain = plain[16:-pad]
            (length,) = struct.unpack(">I", plain[:4])
            msg, receive_id = plain[4 : 4 + length].decode(), plain[4 + length :].decode()
        except WeComCryptoError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WeComCryptoError(f"解密失败：{exc}") from exc
        if receive_id != self.receive_id:
            raise WeComCryptoError("receiveid 不匹配")
        return msg

    def encrypt(self, msg: str) -> str:
        body = os.urandom(16) + struct.pack(">I", len(msg.encode())) + msg.encode() + self.receive_id.encode()
        pad = BLOCK_SIZE - len(body) % BLOCK_SIZE
        body += bytes([pad]) * pad
        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).encryptor()
        return base64.b64encode(encryptor.update(body) + encryptor.finalize()).decode()
