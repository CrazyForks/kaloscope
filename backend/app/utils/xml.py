from decimal import Decimal

from lxml import etree
from sanic.log import logger


def get_text(element: etree._Element | None, tag_name: str) -> str | None:
    """Get the text content of the first matching sub-element.

    Args:
        element: The parent XML element.
        tag_name: The tag name of the sub-element.

    Returns:
        The text content of the sub-element, or None if not found.
    """
    if element is None:
        return None
    tag = element.find(tag_name)
    # https://lxml.de/tutorial.html#elements-are-lists
    if tag is not None and tag.text:
        return tag.text.strip()
    return None


def get_integer(element: etree._Element | None, tag_name: str) -> int | None:
    """Get the integer content of the first matching sub-element.

    Args:
        element: The parent XML element.
        tag_name: The tag name of the sub-element.

    Returns:
        The integer content of the sub-element, or None if not found or invalid.
    """
    text = get_text(element, tag_name)
    if text and text.isdigit():
        return int(text)
    return None


def get_decimal(element: etree._Element | None, tag_name: str) -> Decimal | None:
    """Get the decimal content of the first matching sub-element.

    Args:
        element: The parent XML element.
        tag_name: The tag name of the sub-element.

    Returns:
        The decimal content of the sub-element, or None if not found or invalid.
    """
    text = get_text(element, tag_name)
    if text:
        try:
            return Decimal(text)
        except Exception:
            logger.warning("Invalid decimal value for tag '%s': %s", tag_name, text)
    return None
