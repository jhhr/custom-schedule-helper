import math
import random
import time
from builtins import int
from datetime import datetime, timedelta
from typing import List, Dict

from anki.cards import Card
from anki.consts import (
    CARD_TYPE_REV,
    QUEUE_TYPE_LRN,
    QUEUE_TYPE_REV,
    QUEUE_TYPE_DAY_LEARN_RELEARN,
)
from anki.decks import DeckManager
from anki.utils import ids2str, int_version
from aqt import mw
from aqt.utils import tooltip, showWarning

from ..configuration import Config
from ..utils import (
    get_rev_conf,
    get_fuzz_range,
    update_card_due_ivl,
    rotate_number_by_k,
    write_custom_data,
    check_custom_scheduler,
    CustomSchedulerNotFoundError,
    get_deck_parameters,
    DeckParamsMissingError,
    get_skip_decks,
    GLOBAL_DECK_CONFIG_NAME,
    SCHEDULER_NAME,
)

LOG = False


class Scheduler:
    max_ivl: int
    days_upper: bool
    enable_load_balance: bool
    free_days: List[int]
    due_cnt_perday_from_first_day: Dict[int, int]
    learned_cnt_perday_from_today: Dict[int, int]
    card: Card
    elapsed_days: int

    def __init__(self) -> None:
        self.max_ivl = 36500
        self.days_upper = 200
        self.enable_load_balance = False
        self.free_days = []
        self.elapsed_days = 0

    def set_load_balance(self):
        self.enable_load_balance = True
        true_due = "CASE WHEN odid==0 THEN due ELSE odue END"
        self.due_cnt_perday_from_first_day = {
            day: cnt
            for day, cnt in mw.col.db.all(
                f"""SELECT {true_due}, count() 
                FROM cards 
                WHERE type = {CARD_TYPE_REV}
                AND queue != -1
                GROUP BY {true_due}"""
            )
        }
        for day in list(self.due_cnt_perday_from_first_day.keys()):
            if day < mw.col.sched.today:
                self.due_cnt_perday_from_first_day[mw.col.sched.today] = (
                        self.due_cnt_perday_from_first_day.get(mw.col.sched.today, 0)
                        + self.due_cnt_perday_from_first_day[day]
                )
                self.due_cnt_perday_from_first_day.pop(day)
        self.learned_cnt_perday_from_today = {
            day: cnt
            for day, cnt in mw.col.db.all(
                f"""SELECT (id/1000-{mw.col.sched.day_cutoff})/86400, count(distinct cid)
                FROM revlog
                WHERE ease > 0
                GROUP BY (id/1000-{mw.col.sched.day_cutoff})/86400"""
            )
        }

    def set_fuzz_factor(self, cid: int, reps: int):
        random.seed(rotate_number_by_k(cid, 8) + reps)
        self.fuzz_factor = random.random()

    def apply_fuzz(self, ivl):
        if ivl < 7:
            return ivl
        ivl = int(round(ivl))
        min_ivl, max_ivl = get_fuzz_range(ivl, self.elapsed_days)
        self.elapsed_days = 0
        if not self.enable_load_balance:
            if int_version() >= 231001:
                return ivl + mw.col.fuzz_delta(self.card.id, ivl)
            else:
                return int(self.fuzz_factor * (max_ivl - min_ivl + 1) + min_ivl)
        else:
            min_num_cards = 18446744073709551616
            best_ivl = ivl
            step = (max_ivl - min_ivl) // 100 + 1
            due = self.card.due if self.card.odid == 0 else self.card.odue
            for check_ivl in reversed(range(min_ivl, max_ivl + step, step)):
                check_due = due + check_ivl - self.card.ivl
                day_offset = check_due - mw.col.sched.today
                due_date = datetime.now() + timedelta(days=day_offset)
                due_cards = self.due_cnt_perday_from_first_day.get(
                    max(check_due, mw.col.sched.today), 0
                )
                rated_cards = (
                    self.learned_cnt_perday_from_today.get(0, 0)
                    if day_offset <= 0
                    else 0
                )
                num_cards = due_cards + rated_cards
                if (
                        num_cards < min_num_cards
                        and due_date.weekday() not in self.free_days
                ):
                    best_ivl = check_ivl
                    min_num_cards = num_cards
            return best_ivl

    def next_interval(self, max_ivl):
        card = self.card

        # Get all revs, including manual reschedules
        revs = mw.col.db.all("SELECT ivl, ease, factor FROM revlog WHERE cid = ?", card.id)
        if len(revs) > 1:
            prev_rev = revs[len(revs) - 1]
        else:
            return self.apply_fuzz(card.ivl)

        prev_ivl = prev_rev[0]
        prev_rev_ease = prev_rev[1]
        prev_factor = prev_rev[2]

        rev_conf = get_rev_conf(card)
        # Default factor from rev
        # NOTE: factor is stored as an Integer of parts per 1000, convert to the actual multiplier
        # This is the normal good mult
        mult = prev_factor / 1000

        # Again or manual reschedule
        # Again never increases ivl so we do nothing in that case
        if prev_rev_ease == 1 or prev_rev_ease == 0:
            return self.apply_fuzz(card.ivl)
        # Hard
        elif prev_rev_ease == 2:
            # Here too, only adjust ivl, if it's increasing
            if rev_conf["deck_hard_fct"] > 1:
                hard_mult = rev_conf["deck_hard_fct"]
                hard_good_ratio = min(hard_mult / mult, 1)
                # Mult approaches 1 the closer normal hard_mult is to goodMult
                mult = hard_mult * (1 - hard_good_ratio) + 1 * hard_good_ratio
            else:
                return self.apply_fuzz(card.ivl)
        # Good
        elif prev_rev_ease == 3:
            mult = mult
        # Easy
        elif prev_rev_ease == 4:
            mult = mult * rev_conf["deck_easy_fct"]

        min_mod_factor = math.sqrt(mult)
        adj_days_upper = self.days_upper * mult

        ratio = min(prev_ivl / adj_days_upper, 1)
        mod_factor = min(mult, mult * (1 - ratio) + min_mod_factor * ratio)
        mod_ivl = min(card.ivl, prev_ivl * mod_factor)
        new_interval = self.apply_fuzz(mod_ivl)
        if LOG:
            print("")
            print("card.id", card.id)
            print("adj_days_upper", adj_days_upper)
            print("ratio", ratio)
            print("min_mod_factor", min_mod_factor)
            print("mod_factor", mod_factor)
            print("cur_ivl", card.ivl)
            print("prev_rev_ease", prev_rev_ease)
            print("prev_ivl", prev_ivl)
            print("prev_factor", prev_factor)
            print("mod_ivl", mod_ivl)
            print("new_interval", new_interval)
        return min(max(int(round(new_interval)), 1), max_ivl)

    def set_card(self, card: Card):
        self.card = card


def reschedule(did, recent=False, filter_flag=False, filtered_cids=[]):
    start_time = time.time()

    def on_done(future):
        mw.progress.finish()
        (result_msg, err_msgs) = future.result()
        tooltip(f"{result_msg} in {time.time() - start_time:.2f} seconds")
        if (len(err_msgs) > 0):
            showWarning("\n".join(err_msgs))
        mw.reset()

    fut = mw.taskman.run_in_background(
        lambda: reschedule_background(did, recent, filter_flag, filtered_cids),
        on_done,
    )

    return fut


RESCHEDULE_STOP_MSG = "Reschedule stopped due to error"


def reschedule_background(did, recent=False, filter_flag=False, filtered_cids=[]):
    config = Config()
    config.load()
    try:
        custom_scheduler = check_custom_scheduler(mw.col.all_config())
    except CustomSchedulerNotFoundError as err:
        return (RESCHEDULE_STOP_MSG, [err.message])
    try:
        deck_parameters = get_deck_parameters(custom_scheduler)
    except DeckParamsMissingError as err:
        return (RESCHEDULE_STOP_MSG, [err.message])

    skip_decks = get_skip_decks(custom_scheduler)

    undo_entry = mw.col.add_custom_undo_entry("Reschedule")
    mw.taskman.run_on_main(
        lambda: mw.progress.start(label="Rescheduling", immediate=False)
    )

    cnt = 0
    err_msgs = []
    decks = sorted(mw.col.decks.all(), key=lambda item: item['name'], reverse=True)

    scheduler = Scheduler()

    if config.load_balance:
        scheduler.set_load_balance()
        scheduler.free_days = config.free_days

    cancelled = False
    DM = DeckManager(mw.col)

    for deck in decks:
        # Is this a single deck reschedule from deck menu?
        if did is not None:
            deck_name = mw.col.decks.get(did)['name']
            # If so, skip all other decks
            if not deck['name'].startswith(deck_name): continue

        dids = DM.deck_and_child_ids(deck['id'])
        # get dids for skip decks
        skip_dids = [mw.col.decks.by_name(skip_deck_name)['id'] for skip_deck_name in skip_decks]
        # filter out skip decks
        dids = [did for did in dids if did not in skip_dids]
        if len(dids) == 0:
            continue
        dids_str = ids2str(dids)
        did_query = f"AND did IN {dids_str}"

        try:
            cur_deck_param = get_current_deck_parameter(deck['name'], deck_parameters)
        except GlobalConfigNotFoundError as err:
            err_msgs.append(err.message)
            break

        if cur_deck_param is None:
            err_msgs.append(
                f"{SCHEDULER_NAME} ERROR: Deck parameter was not found for deck '{deck['name']}'"
            )
            break

        # Set deck specific parameters
        scheduler.days_upper = cur_deck_param["days_upper"]

        recent_query = None
        if recent:
            today_cutoff = mw.col.sched.day_cutoff
            day_before_cutoff = today_cutoff - (config.days_to_reschedule + 1) * 86400
            recent_query = (
                f"AND id IN (SELECT cid FROM revlog WHERE id >= {day_before_cutoff * 1000})"
            )

        filter_query = None
        if filter_flag and len(filtered_cids) > 0:
            filter_query = f"AND id IN {ids2str(filtered_cids)}"

        not_already_rescheduled_query = None
        # When doing auto reschedule, we don't want to reschedule cards that were already rescheduled
        # or dispersed by another Anki instance running this addon
        # But when running reschedule from the deck menu or main menu, we will reschedule again
        if filter_flag:
            not_already_rescheduled_query = f"AND json_extract(data, '$.cd.v') NOT IN ('reschedule', 'disperse')"

        cards = mw.col.db.all(
            f"""
            SELECT 
                id,
                CASE WHEN odid==0
                THEN did
                ELSE odid
                END
            FROM cards
            WHERE data != ''
            AND queue IN ({QUEUE_TYPE_LRN}, {QUEUE_TYPE_REV}, {QUEUE_TYPE_DAY_LEARN_RELEARN})
            {not_already_rescheduled_query if not_already_rescheduled_query is not None else ""}
            {did_query if did_query is not None else ""}
            {recent_query if recent_query is not None else ""}
            {filter_query if filter_query is not None else ""}
        """
        )
        # x[0]: cid
        # x[1]: did
        # x[2]: max interval
        cards = map(
            lambda x: (
                    x
                    + [
                        DM.config_dict_for_deck_id(x[1])["rev"]["maxIvl"],
                    ]
            ),
            cards,
        )

        for cid, _, max_interval in cards:
            if cancelled:
                break
            scheduler.max_ivl = max_interval
            card = reschedule_card(cid, scheduler)
            if card is None:
                continue
            mw.col.update_card(card)
            mw.col.merge_undo_entries(undo_entry)
            cnt += 1
            if cnt % 500 == 0:
                mw.taskman.run_on_main(
                    lambda: mw.progress.update(value=cnt, label=f"{cnt} cards rescheduled")
                )
                if mw.progress.want_cancel():
                    cancelled = True

    return (f"{cnt} cards rescheduled", err_msgs)


class GlobalConfigNotFoundError(BaseException):
    def __init__(self):
        self.message = f"{SCHEDULER_NAME} ERROR: '{GLOBAL_DECK_CONFIG_NAME}' is not found in the deckParams"


def get_current_deck_parameter(deckname, deck_parameters):
    try:
        deck_parameter = deck_parameters[GLOBAL_DECK_CONFIG_NAME]
    except KeyError:
        raise GlobalConfigNotFoundError()
    for name, params in deck_parameters.items():
        if deckname.startswith(name):
            deck_parameter = params
            break
    return deck_parameter


def reschedule_card(cid, scheduler: Scheduler):
    card = mw.col.get_card(cid)

    write_custom_data(card, "v", "reschedule")

    if card.type == CARD_TYPE_REV:
        scheduler.set_card(card)
        scheduler.set_fuzz_factor(cid, card.reps)
        new_ivl = scheduler.next_interval(scheduler.max_ivl)
        due_before = max(card.odue if card.odid else card.due, mw.col.sched.today)
        card = update_card_due_ivl(card, new_ivl)
        due_after = max(card.odue if card.odid else card.due, mw.col.sched.today)
        if scheduler.enable_load_balance:
            scheduler.due_cnt_perday_from_first_day[due_before] -= 1
            scheduler.due_cnt_perday_from_first_day[due_after] = (
                    scheduler.due_cnt_perday_from_first_day.get(due_after, 0) + 1
            )
    return card
