"""Bounded image decoding; uploaded content and metadata never enter logs."""
import io
import warnings
from PIL import Image, ImageOps

# Set the process ceiling once; per-app limits are checked before pixel load.
Image.MAX_IMAGE_PIXELS = 50_000_000
warnings.filterwarnings('error', category=Image.DecompressionBombWarning)
FORMATS = {'image/jpeg':'JPEG','image/png':'PNG','image/webp':'WEBP'}


class ImageRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def decode_image(data, content_type, settings):
    content_type=content_type.split(';',1)[0].strip().lower()
    fmt=FORMATS.get(content_type)
    if fmt is None:
        raise ImageRejected(415,'Unsupported image type')
    if not data:
        raise ImageRejected(422,'Invalid image')
    if len(data) > settings.max_image_bytes:
        raise ImageRejected(413,'Request body too large')
    matches = ((fmt=='JPEG' and data.startswith(b'\xff\xd8\xff')) or
               (fmt=='PNG' and data.startswith(b'\x89PNG\r\n\x1a\n')) or
               (fmt=='WEBP' and data[:4]==b'RIFF' and data[8:12]==b'WEBP'))
    if not matches:
        raise ImageRejected(415,'Unsupported image type')
    try:
        with Image.open(io.BytesIO(data), formats=[fmt]) as img:
            if img.width*img.height > settings.max_image_pixels or getattr(img,'n_frames',1)>1:
                raise ImageRejected(422,'Invalid image')
            img.load()
            oriented=ImageOps.exif_transpose(img)
            try:
                result=oriented.convert('RGB')
                result.info.clear()
                return result
            finally:
                if oriented is not img: oriented.close()
    except (Image.DecompressionBombError,Image.DecompressionBombWarning,OSError,SyntaxError,ValueError):
        raise ImageRejected(422,'Invalid image') from None
