"""Unit tests for crypto utility functions."""

import pytest

from app.utils.crypto import xor_decrypt, xor_encrypt


def test_encrypt_decrypt_basic_string():
    """Test encryption and decryption of a basic ASCII string."""
    plain_text = "Hello, World!"
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_empty_string():
    """Test encryption and decryption of an empty string."""
    plain_text = ""
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_unicode_characters():
    """Test encryption and decryption with Unicode characters."""
    plain_text = "你好世界 🌍 Hello!"
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_special_characters():
    """Test encryption and decryption with special characters."""
    plain_text = "!@#$%^&*()_+-=[]{}|;:',.<>?/~`"
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_numbers():
    """Test encryption and decryption with numeric strings."""
    plain_text = "1234567890"
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_multiline_text():
    """Test encryption and decryption with multiline text."""
    plain_text = "Line 1\nLine 2\nLine 3"
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypt_decrypt_long_text():
    """Test encryption and decryption with long text."""
    plain_text = "A" * 1000
    encrypted = xor_encrypt(plain_text)
    decrypted = xor_decrypt(encrypted)
    assert decrypted == plain_text


def test_encrypted_output_is_base64():
    """Test that encrypted output is valid Base64."""
    plain_text = "Test string"
    encrypted = xor_encrypt(plain_text)
    # Base64 should only contain alphanumeric characters, +, /, and =
    assert all(c.isalnum() or c in "+/=" for c in encrypted)


def test_same_input_produces_same_output():
    """Test that encrypting the same input twice produces the same output."""
    plain_text = "Consistent output"
    encrypted1 = xor_encrypt(plain_text)
    encrypted2 = xor_encrypt(plain_text)
    assert encrypted1 == encrypted2


def test_different_inputs_produce_different_outputs():
    """Test that different inputs produce different encrypted outputs."""
    plain_text1 = "Text 1"
    plain_text2 = "Text 2"
    encrypted1 = xor_encrypt(plain_text1)
    encrypted2 = xor_encrypt(plain_text2)
    assert encrypted1 != encrypted2


def test_decrypt_invalid_base64_raises_error():
    """Test that decrypting invalid Base64 raises an error."""
    invalid_encrypted = "This is not valid Base64!!!"
    with pytest.raises(ValueError):
        xor_decrypt(invalid_encrypted)
