"""Local image super-resolution service using PIL Lanczos3/Bicubic resampling."""
import io
import base64
from typing import Optional, Tuple
from PIL import Image
from ..core.logger import debug_logger


class ImageUpscaler:
    """Provides high-quality image upscaling using Lanczos3 or Bicubic filters."""

    @staticmethod
    def upscale_bytes(
        image_bytes: bytes,
        scale: float = 2.0,
        target_size: Optional[Tuple[int, int]] = None,
        engine: str = "lanczos3"
    ) -> bytes:
        """
        Upscale image bytes by a scale factor or to a specific target size.
        engine: 'lanczos3' (Pillow Resampling.LANCZOS) or 'bicubic' (Pillow Resampling.BICUBIC)
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                orig_w, orig_h = img.size
                if target_size:
                    new_w, new_h = target_size
                else:
                    new_w = max(1, int(round(orig_w * scale)))
                    new_h = max(1, int(round(orig_h * scale)))

                resample_filter = (
                    Image.Resampling.LANCZOS
                    if str(engine).lower() != "bicubic"
                    else Image.Resampling.BICUBIC
                )
                upscaled = img.resize((new_w, new_h), resample=resample_filter)

                output = io.BytesIO()
                fmt = img.format or "JPEG"
                if str(fmt).upper() in ("JPEG", "JPG"):
                    upscaled.save(output, format="JPEG", quality=95, optimize=True)
                else:
                    upscaled.save(output, format=fmt)

                debug_logger.log_info(
                    f"[UPSCALER] Upscaled image ({orig_w}x{orig_h} -> {new_w}x{new_h}) using {engine}"
                )
                return output.getvalue()
        except Exception as e:
            debug_logger.log_error(f"[UPSCALER] Local super-resolution failed: {e}")
            raise

    @staticmethod
    def upscale_base64(
        base64_str: str,
        scale: float = 2.0,
        target_size: Optional[Tuple[int, int]] = None,
        engine: str = "lanczos3"
    ) -> str:
        """Upscale a base64 encoded image and return base64 string."""
        raw_bytes = base64.b64decode(base64_str)
        upscaled_bytes = ImageUpscaler.upscale_bytes(
            raw_bytes, scale=scale, target_size=target_size, engine=engine
        )
        return base64.b64encode(upscaled_bytes).decode("ascii")
