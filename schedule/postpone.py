import math
import random
import time

from anki.decks import DeckManager
from anki.stats import (
    QUEUE_TYPE_REV,
)
from anki.utils import ids2str
from aqt import mw
from aqt.utils import tooltip, getText, showWarning

from ..utils import (
    write_custom_data,
    RepresentsInt,
    update_card_due_ivl,
    get_last_review_date,
)


def get_desired_postpone_cnt_with_response(safe_cnt, did):
    inquire_text = "Enter the number of cards to be postponed.\n"
    notification_text = f"{'For this deck' if did else 'For this collection'}, it is relatively safe to postpone up to {safe_cnt} cards.\n"
    warning_text = "You can postpone more cards if you wish, but it is not recommended.\nKeep in mind that whenever you use Postpone or Advance, you depart from the optimal scheduling.\n"
    info_text = (
        "This feature only affects the cards that have been scheduled by Custom Schedule."
    )
    (s, r) = getText(
        inquire_text + notification_text + warning_text + info_text, default="10"
    )
    if r:
        return (RepresentsInt(s), r)
    return (None, r)


def postpone(did):
    DM = DeckManager(mw.col)
    if did is not None:
        did_list = ids2str(DM.deck_and_child_ids(did))

    # json_extract(data, '$.dr')
    # WHERE data != ''
    cards = mw.col.db.all(
        f"""
        SELECT 
            id, 
            CASE WHEN odid==0
            THEN did
            ELSE odid
            END,
            factor,
            ivl,
            CASE WHEN odid==0
            THEN {mw.col.sched.today} - (due - ivl)
            ELSE {mw.col.sched.today} - (odue - ivl)
            END
        FROM cards
        WHERE due <= {mw.col.sched.today}
        AND queue = {QUEUE_TYPE_REV}
        {"AND did IN %s" % did_list if did is not None else ""}
    """
    )
    # x[0]: cid
    # x[1]: did
    # x[2]: factor
    # x[3]: interval
    # x[4]: elapsed days
    # x[5]: max interval
    cards = map(
        lambda x: (
                x
                + [
                    DM.config_dict_for_deck_id(x[1])["rev"]["maxIvl"],
                ]
        ),
        cards,
    )
    # sort by (elapsed_days / interval - 1), -interval (ascending)
    cards = sorted(cards, key=lambda x: (x[4] / x[3] - 1, -x[3]))
    safe_cnt = len(
        list(filter(lambda x: x[4] / x[3] - 1 - 1 < 0.15, cards))
    )

    (desired_postpone_cnt, resp) = get_desired_postpone_cnt_with_response(safe_cnt, did)
    if desired_postpone_cnt is None:
        if resp:
            showWarning("Please enter the number of cards you want to postpone.")
        return
    else:
        if desired_postpone_cnt <= 0:
            showWarning("Please enter a positive integer.")
            return

    undo_entry = mw.col.add_custom_undo_entry("Postpone")

    mw.progress.start()
    start_time = time.time()

    cnt = 0

    for cid, _, _, ivl, elapsed_days, max_ivl in cards:
        if cnt >= desired_postpone_cnt:
            break

        card = mw.col.get_card(cid)
        random.seed(cid + ivl)
        last_review = get_last_review_date(card)
        elapsed_days = mw.col.sched.today - last_review
        delay = elapsed_days - ivl
        new_ivl = min(
            max(1, math.ceil(ivl * (1.05 + 0.05 * random.random())) + delay), max_ivl
        )
        card = update_card_due_ivl(card, new_ivl)
        write_custom_data(card, "v", "postpone")
        mw.col.update_card(card)
        mw.col.merge_undo_entries(undo_entry)
        cnt += 1

    tooltip(
        f"""{cnt} cards postponed in {time.time() - start_time:.2f} seconds."""
    )
    mw.progress.finish()
    mw.reset()
