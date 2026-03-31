import base64
import contextlib
import re
from dataclasses import dataclass

from sanic.log import logger
from torrentool.api import Torrent
from torrentool.bencode import Bencode


@dataclass
class MagnetLink:
    """The magnet link information."""

    link: str
    info_hash: str | None = None
    info_hash_v2: str | None = None


def standardize_magnet(link: str) -> MagnetLink | None:
    """Standardize a magnet link.

    Args:
        link: The magnet link string.

    Returns:
        The standardized magnet link object if successful, None otherwise.
    """
    link = link.strip()
    if not link:
        return None
    # Magnet link
    if link.startswith("magnet:"):
        hash = get_magnet_hash(link)
        hash_v2 = get_magnet_hash_v2(link)
        if not hash and not hash_v2:
            return None
        return MagnetLink(link, hash, hash_v2)
    # BitTorrent info hash v2 (BTMH)
    if is_info_hash_v2(link):
        hash = None
        hash_v2 = link
        return MagnetLink(f"magnet:?xt=urn:btmh:{hash_v2}", hash, hash_v2)
    # BitTorrent info hash (BTIH)
    link = base32_to_sha1(link)
    if is_info_hash(link):
        hash = link
        hash_v2 = None
        return MagnetLink(f"magnet:?xt=urn:btih:{hash}", hash, hash_v2)


def is_info_hash(hash: str) -> bool:
    """Check if a string is a valid info hash.

    Args:
        hash: The string to check.

    Returns:
        True if the string is a valid info hash, False otherwise.
    """
    # support the 32 character base32 encoded info-hash
    # https://bittorrent.org/beps/bep_0009.html
    hash = base32_to_sha1(hash)
    return re.match(r"^[0-9a-fA-F]{40}$", hash) is not None


def is_info_hash_v2(hash: str) -> bool:
    """Check if a string is a valid info hash v2.

    Args:
        hash: The string to check.

    Returns:
        True if the string is a valid info hash v2, False otherwise.
    """
    return re.match(r"^1220[0-9a-fA-F]{64}$", hash) is not None


def get_magnet_hash(link: str) -> str | None:
    """Extract the info hash from a magnet link.

    Args:
        link: The magnet link.

    Returns:
        The info hash if found, None otherwise.
    """
    match = re.search(r"urn:btih:([0-9a-fA-F]{40}|[0-9a-zA-Z]{32})(?=&|$)", link)
    return base32_to_sha1(match.group(1)) if match else None


def get_magnet_hash_v2(link: str) -> str | None:
    """Extract the info hash v2 from a magnet link.

    Args:
        link: The magnet link.

    Returns:
        The info hash v2 if found, None otherwise.
    """
    match = re.search(r"urn:btmh:(1220[0-9a-fA-F]{64})(?=&|$)", link)
    return match.group(1) if match else None


def base32_to_sha1(hash: str) -> str:
    """Converts a Base32 hash (from a magnet link) to a SHA1 hash.

    Args:
        hash: The Base32 encoded hash string.

    Returns:
        The SHA1 hash string, or the original hash if conversion fails.
    """
    if len(hash) == 32:
        with contextlib.suppress(Exception):
            hash = base64.b32decode(hash, casefold=True).hex()
    return hash


def decode_torrent(torrent: bytes) -> Torrent | None:
    """Decode a torrent file.

    Args:
        torrent: The torrent file content.

    Returns:
        The decoded torrent object if successful, None otherwise.
    """
    try:
        decoded = Bencode.read_string(torrent, byte_keys={"pieces"})
        if isinstance(decoded, dict):
            return Torrent(decoded)
    except Exception:
        logger.error("Failed to decode the torrent file!", exc_info=True)
    return None


def get_torrent_hash(torrent: bytes) -> str | None:
    """Extract the info hash from a torrent file.

    Args:
        torrent: The torrent file content.

    Returns:
        The info hash if found, None otherwise.
    """
    decoded = decode_torrent(torrent)
    return decoded.info_hash if decoded else None
