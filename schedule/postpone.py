import math
import random
import time

from anki.decks import DeckManager
from anki.stats import (
    QUEUE_TYPE_REV,
)
from anki.utils import ids2str
from aqt import mw
from aqt.qt import (
    QDialog,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QDialogButtonBox
)
from aqt.utils import tooltip, getText, showWarning

from ..utils import (
    write_custom_data,
    RepresentsInt,
    update_card_due_ivl,
    get_last_review_date,
)

WARNING_TEXT = "You can postpone more cards if you wish, but it is not recommended.\nKeep in mind that whenever you use Postpone or Advance, you depart from the optimal scheduling.\n"
INFO_TEXT = (
    "This feature only affects the cards that have been scheduled by Custom Schedule.\n"
)
INQUIRE_COUNT_TEXT = "Enter the number of cards to be postponed.\n"
INQUIRE_IVL_TEXT = "Or enter the interval above which the cards will be postponed.\n"


class PostPoneDialog(QDialog):
    def __init__(self, safe_cnt, did, cards, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Postpone cards")
        self.cards = cards
        self.main_layout = QVBoxLayout(self)
        self.form_layout = QGridLayout()
        self.main_layout.addLayout(self.form_layout)
        notification_text = f"{'For this deck' if did else 'For this collection'}, it is relatively safe to postpone up to {safe_cnt} cards.\n"

        cnt_label = QLabel(notification_text + WARNING_TEXT + INFO_TEXT)
        self.form_layout.addWidget(cnt_label, 0, 0, 1, 2)
        self.count_line_edit = QLineEdit()
        self.form_layout.addWidget(QLabel(INQUIRE_COUNT_TEXT), 1, 0)
        self.form_layout.addWidget(self.count_line_edit, 1, 1)

        self.interval_line_edit = QLineEdit()
        self.form_layout.addWidget(QLabel(INQUIRE_IVL_TEXT), 2, 0)
        self.form_layout.addWidget(self.interval_line_edit, 2, 1)
        # Add a view showing the number of cards with interval greater than the entered interval.
        self.interval_line_edit.textChanged.connect(self.update_interval_view)
        self.interval_view = QLabel()
        self.form_layout.addWidget(self.interval_view, 3, 0, 1, 2)

        self.bottom_layout = QVBoxLayout()
        self.main_layout.addLayout(self.bottom_layout)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.bottom_layout.addWidget(self.button_box)

    def get_inputs(self):
        return (self.count_line_edit.text(), self.interval_line_edit.text())

    def update_interval_view(self):
        interval = self.interval_line_edit.text()
        if RepresentsInt(interval):
            interval = int(interval)
            self.interval_view.setText(
                f"{len(list(filter(lambda x: x[3] >= interval, self.cards)))} cards with interval greater than {interval}.")
        else:
            self.interval_view.setText("")


def get_desired_postpone_def_with_response(safe_cnt, did, cards):
    dialog = PostPoneDialog(safe_cnt, did, cards)
    if dialog.exec():
        res = dialog.get_inputs()
        if res is not None:
            return RepresentsInt(res[0]), RepresentsInt(res[1])
        return None
    return None


def get_desired_postpone_cnt_with_response(safe_cnt, did):
    notification_text = f"{'For this deck' if did else 'For this collection'}, it is relatively safe to postpone up to {safe_cnt} cards.\n"
    (s, r) = getText(
        INQUIRE_COUNT_TEXT + notification_text + WARNING_TEXT + INFO_TEXT, default="10"
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
        AND json_extract(json_extract(data, '$.cd'), '$.v') != 'postpone'
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
    # sort by interval (ascending)
    cards = sorted(cards, key=lambda x: x[3])
    safe_cnt = len(
        list(filter(lambda x: x[4] / x[3] - 1 < 0.25, cards))
    )

    res = get_desired_postpone_def_with_response(safe_cnt, did, cards)
    print(res)
    if res is None:
        showWarning("Please enter the number of cards or interval by which you want to postpone.")
        return
    else:
        (desired_postpone_cnt, desired_postpone_interval) = res
        print(desired_postpone_cnt, desired_postpone_interval)
        if desired_postpone_cnt is not None and desired_postpone_interval is not None:
            showWarning("Please enter only either the number of cards or the interval.")
            return
        if ((desired_postpone_cnt is not None and desired_postpone_cnt <= 0)
                or (desired_postpone_interval is not None and desired_postpone_interval <= 0)):
            showWarning("Please enter a positive integer.")
            return

    (desired_postpone_cnt, desired_postpone_interval) = res

    if desired_postpone_interval is not None:
        # filter cards after desired_postpone_interval
        cards = list(filter(lambda x: x[3] >= desired_postpone_interval, cards))
    else:
        # filter cards to desired_postpone_cnt, cutting off from the beginning
        if desired_postpone_cnt < len(cards):
            cards = cards[len(cards) - desired_postpone_cnt:len(cards)]

    undo_entry = mw.col.add_custom_undo_entry("Postpone")

    mw.progress.start()
    start_time = time.time()

    cnt = 0
    ivl_incr = 0

    for cid, _, fct, ivl, elapsed_days, max_ivl in cards:
        card = mw.col.get_card(cid)
        random.seed(cid + ivl)
        last_review = get_last_review_date(card)
        elapsed_days = mw.col.sched.today - last_review
        # For cards with ivl < 30, postpone by a percentage of the interval
        delay = elapsed_days - ivl
        msg = f"fct={round(fct / 1000, 2)} Elapsed days: {elapsed_days}, IVL: {ivl}"
        if elapsed_days < 30:
            # Postpone the card between 5% to ~35% depending on the factor
            # the fct is a number between 1300 and 5000, so we divide by 20000 to get a number between 0.065 and 0.25
            mult = max(1.05, 1.00 + fct / 20000 + 0.5 * random.random() - min(0.2, ivl / (30 * 5)))
            new_ivl = min(
                max(1, math.ceil(elapsed_days * mult)), max_ivl
            )
            # Set the increment to the maximum of the new interval and the elapsed days we've postponed
            # so far, thus when start postponing in 1 day increments, we'll start from the next day after
            # the last postponed card.
            ivl_incr = max(new_ivl - elapsed_days, ivl_incr)
            msg += f" Percentage increase, Delay: {delay}, IVL mult: {round(mult, 2)}, New IVL: {new_ivl}, IVL incr: {ivl_incr}"
        else:
            ivl_incr += 1
            # This card is postponed by 1 more day, the next by 2 more days, and so on.
            new_ivl = min(
                elapsed_days + ivl_incr, max_ivl
            )
            msg += f" Fixed increment, New IVL: {new_ivl}, IVL incr: {ivl_incr}"
        print(msg)
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
