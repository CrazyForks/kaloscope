import base64
import hashlib

from app.core.constants import APP_NAME, ENCODING

_KEY_BYTES = APP_NAME.encode(ENCODING)
_KEY_BYTES_LENGTH = len(_KEY_BYTES)


def encrypt(password: str) -> str:
    """Encrypt the password using PBKDF2-HMAC-SHA256.

    Args:
        password: The password to encrypt.

    Returns:
        The encrypted password.
    """
    salt = _KEY_BYTES
    hash = hashlib.pbkdf2_hmac("sha256", password.encode(ENCODING), salt, 100000)
    return hash.hex()


def xor(input_bytes: bytes) -> bytes:
    """XOR the input bytes with the key bytes.

    Args:
        input_bytes: The input bytes.

    Returns:
        The XOR-ed bytes.
    """
    output_bytes = bytearray()
    for i in range(len(input_bytes)):
        output_bytes.append(input_bytes[i] ^ _KEY_BYTES[i % _KEY_BYTES_LENGTH])
    return bytes(output_bytes)


def xor_encrypt(plain_text: str) -> str:
    """Encrypt the plain text using XOR operation with Base64 encoding.

    Args:
        plain_text: The plain text to encrypt.

    Returns:
        The encrypted string in Base64 format.
    """
    encrypted_bytes = xor(plain_text.encode(ENCODING))
    return base64.b64encode(encrypted_bytes).decode(ENCODING)


def xor_decrypt(encrypted_text: str) -> str:
    """Decrypt the encrypted text using XOR operation from Base64 format.

    Args:
        encrypted_text: The encrypted text in Base64 format.

    Returns:
        The decrypted plain text.
    """
    encrypted_bytes = base64.b64decode(encrypted_text)
    return xor(encrypted_bytes).decode(ENCODING)
