"""Bounded authenticated owner image uploads; public metadata never contains paths."""
import io
from .artifacts import ArtifactError, MAX_IMAGE_PIXELS, _image_header

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 4

def upload(store, data):
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ArtifactError("图片为空或超过 8 MB")
    mime, width, height = _image_header(data[:128 * 1024])
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp") or not width or not height or width * height > MAX_IMAGE_PIXELS:
        raise ArtifactError("请选择有效的 PNG、JPEG、GIF 或 WebP 图片（至多 1600 万像素）")
    # Decode before registering, including decompression-bomb rejection. Metadata alone
    # cannot prove that bytes are an image. Dependency absence fails explicitly.
    try:
        from PIL import Image
    except ImportError:
        raise ArtifactError("Runtime 需要安装 Pillow 才能接收图片") from None
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError()
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception:
        raise ArtifactError("图片内容损坏或尺寸过大") from None
    suffix = {"image/png":"png", "image/jpeg":"jpg", "image/gif":"gif", "image/webp":"webp"}[mime]
    return store._snapshot("owner-image", io.BytesIO(data), "图片." + suffix, "owner")

def resolve(store, ids):
    if ids is None:
        return []
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_IMAGES or len(set(x for x in ids if isinstance(x, str))) != len(ids):
        raise ArtifactError("每条消息可附 1 到 4 张不同的图片")
    result = []
    for identity in ids:
        stream, meta, mime, size = store.open_content(identity)
        stream.close()
        if meta["source"] != "owner" or meta["kind"] != "image":
            raise ArtifactError("只能发送已上传的主人图片")
        result.append(meta)
    return result
