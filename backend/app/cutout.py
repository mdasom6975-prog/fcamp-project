"""불투명 제품 사진의 배경을 지워 누끼(투명 PNG)로 만든다 (로컬 모델 rembg/u2net, 호출 비용 없음).
제품 픽셀은 하나도 다시 그리지 않고 투명도(알파)만 만들므로, 합성 방식(compose.paste_product)에 그대로 쓸 수 있다.
rembg 는 선택 의존성이다: 설치돼 있지 않거나(예: Vercel) 결과가 미덥지 않으면 None 을 돌려주고, 호출한 쪽은 기존 참조 방식으로 처리한다."""
import logging
import threading

from PIL import Image

log = logging.getLogger("cutout")

MODEL = "u2net"
WORK_SIDE = 1280                  # 모델에 넣는 긴 변(사진은 업로드 때 이미 1280px 이하)
MIN_COVER, MAX_COVER = 0.03, 0.92     # 제품이 차지하는 면적 비율의 상식 범위. 밖이면 마스크 실패로 본다
_lock = threading.Lock()
_session = None


def available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


def cut(im: Image.Image) -> Image.Image | None:
    """배경을 지운 RGBA 이미지. 불가능하거나 마스크가 미덥지 않으면 None."""
    global _session
    if not available():
        return None
    try:
        from rembg import new_session, remove
        with _lock:                           # onnx 세션은 한 번만 만들고 추론은 한 번에 하나씩
            if _session is None:
                _session = new_session(MODEL)
            src = im.convert("RGB")
            src.thumbnail((WORK_SIDE, WORK_SIDE), Image.LANCZOS)
            out = remove(src, session=_session).convert("RGBA")
    except Exception as e:                    # 모델 내려받기 실패·메모리 부족 등은 처리 실패일 뿐 업로드를 막지 않는다
        log.warning("누끼 처리 실패: %s", e)
        return None
    a = out.getchannel("A").point(lambda v: 0 if v < 24 else v)      # 옅은 잔상 제거
    out.putalpha(a)
    cover = sum(a.histogram()[128:]) / max(1, a.width * a.height)
    if not (MIN_COVER <= cover <= MAX_COVER):
        log.info("누끼 마스크가 비정상(면적 %.2f) → 사용 안 함", cover)
        return None
    return out
