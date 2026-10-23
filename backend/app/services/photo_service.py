"""旅行照片存储展示副本，不读取定位元数据、不发送模型、不据此标记到访。"""

from io import BytesIO

from PIL import Image

from app.core.errors import InvalidRequest
from app.domain.itinerary import Itinerary, TravelRecap
from app.services.receipt_service import normalise_receipt
from app.storage.session_store import SessionStore


def normalise_photo(data: bytes) -> tuple[bytes, int, int, str]:
    preview, _, _, _, digest = normalise_receipt(data)
    with Image.open(BytesIO(preview)) as image:
        image.thumbnail((1200, 1200))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue(), image.width, image.height, digest


async def validate_photos(
    store: SessionStore, session_id: str, recap: TravelRecap, itinerary: Itinerary
) -> None:
    previous = {p.id: p for p in itinerary.recap.photos} if itinerary.recap else {}
    for photo in recap.photos:
        if await store.get_photo(session_id, photo.id) is None:
            raise InvalidRequest("照片不存在或不属于本次旅行，请重新上传旅行照片。")
        old = previous.get(photo.id)
        if (
            photo.day
            and not any(day.day == photo.day for day in itinerary.days)
            and (old is None or old.day != photo.day)
        ):
            raise InvalidRequest("照片日期不在本次行程中。")
        if (
            photo.activity_id
            and not any(
                a.id == photo.activity_id and not a.is_context_anchor
                for day in itinerary.days
                for a in day.activities
            )
            and (old is None or old.activity_id != photo.activity_id)
        ):
            raise InvalidRequest("照片关联的地点不存在，请选择具体活动。")


async def photo_export_html(
    content: str, itinerary: Itinerary, store: SessionStore, session_id: str
) -> str:
    import base64
    import html

    recap = itinerary.recap
    if recap is None:
        return content
    figures = []
    cover = ""
    total = 0
    for photo in recap.photos:
        if not photo.included:
            continue
        asset = await store.get_photo(session_id, photo.id)
        if asset is None:
            raise InvalidRequest("所选照片缺失，请移除该照片后重新导出。")
        data = asset["image_data"]
        total += len(data)
        if total > 16 * 1024 * 1024:
            raise InvalidRequest("所选照片合计超过16MB，请减少选入的照片后导出图文回顾。")
        caption = photo.caption
        labels = []
        if photo.day:
            labels.append(f"第{photo.day}天")
        if photo.activity_id:
            activity = next(
                (a for d in itinerary.days for a in d.activities if a.id == photo.activity_id), None
            )
            labels.append(activity.title if activity else "原关联地点已不在当前计划")
        if labels:
            caption += "（用户标注：" + " · ".join(labels) + "；不据此确认到访）"
        encoded = base64.b64encode(data).decode()
        figure = (
            f'<figure><img src="data:image/jpeg;base64,{encoded}" '
            f'alt="{html.escape(photo.caption or "旅行照片", quote=True)}">'
            f"<figcaption>{html.escape(caption)}</figcaption></figure>"
        )
        if photo.id == recap.cover_photo_id:
            cover = '<section aria-label="回顾封面">' + figure + "</section>"
        else:
            figures.append(figure)
    content = content.replace("<main>", "<main>" + cover, 1)
    if figures:
        content = content.replace(
            "</main>",
            '<section aria-label="精选照片"><h2>精选照片</h2>'
            '<div class="photo-grid">' + "".join(figures) + "</div></section></main>",
            1,
        )
    return content
