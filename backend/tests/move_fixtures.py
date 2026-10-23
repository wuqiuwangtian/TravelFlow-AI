"""同城跨天搬移样本，含已付款但尚未开始的景点。"""

from linked_fixtures import linked_itinerary

from app.domain.itinerary import Place


def move_itinerary(*, ambiguous=False, booked=False, with_receipt=False):
    plan = linked_itinerary(with_receipt=with_receipt)
    park = plan.days[1].activities[0]
    park.place = Place(
        id="confirmed-park", name=park.place_name, city="北京", longitude=116.4, latitude=39.9
    )
    park.place_status = "matched"
    park.candidates = [park.place.model_copy(deep=True)]
    if not booked:
        plan.days[1].activities[1].booking = None
    if ambiguous:
        another = plan.days[1].activities[0].model_copy(deep=True)
        another.id = "park-other"
        another.place_name = "另一座公园"
        plan.days[1].activities.insert(1, another)
    return plan
