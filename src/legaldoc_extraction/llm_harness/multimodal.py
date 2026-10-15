"""Multimodal input utilities for OpenAI Chat Completions API.

Creates content blocks compatible with OpenAI's Chat Completions API format used by Vercel AI Gateway and similar providers.
"""

import base64
from typing import Any, cast

from langchain_core.messages import HumanMessage

DEFAULT_IMAGE_MIME_TYPE = "image/jpeg"
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
}


class MediaMessage(HumanMessage):
    """Build a HumanMessage from JPEG or PNG bytes in Chat Completions format.

    Args:
        images: Image bytes or a list of image bytes.
        description: Text appended after the images.
        label_pages: Add a numbered page label before each image.
        mime_type: Image MIME type, either image/jpeg or image/png.

    Example:
        >>> message = MediaMessage(b"image", description="Describe this page")
    """

    def __init__(
        self,
        images: bytes | list[bytes],
        description: str = "",
        label_pages: bool = False,
        mime_type: str = DEFAULT_IMAGE_MIME_TYPE,
    ):
        """Build ordered image blocks with optional page labels and trailing text, rejecting unsupported MIME types."""
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            supported = ", ".join(sorted(SUPPORTED_IMAGE_MIME_TYPES))
            raise ValueError(
                f"Unsupported image mime type: {mime_type}. Supported: {supported}"
            )

        items = [images] if isinstance(images, bytes) else list(images)

        content_blocks: list[str | dict[str, Any]] = []
        for idx, item in enumerate(items, 1):
            if label_pages:
                content_blocks.append({"type": "text", "text": f"Page {idx}:"})
            data_url = (
                f"data:{mime_type};base64,{base64.b64encode(item).decode('utf-8')}"
            )
            content_blocks.append({"type": "image_url", "image_url": {"url": data_url}})

        if description:
            content_blocks.append({"type": "text", "text": description})

        super().__init__(content=cast(str | list[str | dict[str, Any]], content_blocks))
