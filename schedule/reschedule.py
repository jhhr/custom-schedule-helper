from ..utils import *
from ..configuration import Config
from anki.cards import Card
from anki.decks import DeckManager
from anki.utils import ids2str, int_version

DAYS_UPPER = 225
LOG = False
class Scheduler:
    max_ivl: int
    enable_load_balance: bool
    free_days: List[int]
    due_cnt_perday_from_first_day: Dict[int, int]
    learned_cnt_perday_from_today: Dict[int, int]
    card: Card
    elapsed_days: int

    def __init__(self) -> None:
        self.max_ivl = 36500
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
                WHERE type = 2  
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
        revs = mw.col.db.all("select ivl, ease, factor from revlog where cid = ?", card.id)
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
                hardMult = rev_conf["deck_hard_fct"]
                hardGoodRatio = min(hardMult / mult, 1)
                # Mult approaches 1 the closer normal hardMult is to goodMult
                mult = min(hardMult * (1 - hardGoodRatio) + 1 * hardGoodRatio)
            else:
                return self.apply_fuzz(card.ivl)
        # Good
        elif prev_rev_ease == 3:
            mult = mult
        # Easy
        elif prev_rev_ease == 4:
            mult = mult * rev_conf["deck_easy_fct"]

        min_mod_factor = math.sqrt(mult)
        adj_days_upper = DAYS_UPPER * mult

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


def reschedule(did, recent=False, filter_flag=False, filtered_cids={}):
    start_time = time.time()

    def on_done(future):
        mw.progress.finish()
        tooltip(f"{future.result()} in {time.time() - start_time:.2f} seconds")
        mw.reset()

    fut = mw.taskman.run_in_background(
        lambda: reschedule_background(did, recent, filter_flag, filtered_cids),
        on_done,
    )

    return fut


def reschedule_background(did, recent=False, filter_flag=False, filtered_cids={}):
    config = Config()
    config.load()

    undo_entry = mw.col.add_custom_undo_entry("Reschedule")
    mw.taskman.run_on_main(
        lambda: mw.progress.start(label="Rescheduling", immediate=False)
    )

    cnt = 0
    scheduler = Scheduler()
    if config.load_balance:
        scheduler.set_load_balance()
        scheduler.free_days = config.free_days
    cancelled = False
    DM = DeckManager(mw.col)
    if did is not None:
        did_list = ids2str(DM.deck_and_child_ids(did))
        did_query = f"AND did IN {did_list}"

    if recent:
        today_cutoff = mw.col.sched.day_cutoff
        day_before_cutoff = today_cutoff - (config.days_to_reschedule + 1) * 86400
        recent_query = (
            f"AND id IN (SELECT cid FROM revlog WHERE id >= {day_before_cutoff * 1000})"
        )

    if filter_flag:
        filter_query = f"AND id IN {ids2str(filtered_cids)}"

    cards = mw.col.db.all(
        f"""
        SELECT 
            id,
            CASE WHEN odid==0
            THEN did
            ELSE odid
            END
        FROM cards
        WHERE queue IN ({QUEUE_TYPE_LRN}, {QUEUE_TYPE_REV}, {QUEUE_TYPE_DAY_LEARN_RELEARN})
        {did_query if did is not None else ""}
        {recent_query if recent else ""}
        {filter_query if filter_flag else ""}
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
        card = reschedule_card(cid, scheduler, filter_flag)
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

    return f"{cnt} cards rescheduled"


def reschedule_card(cid, scheduler: Scheduler, recompute=False):
    card = mw.col.get_card(cid)

    new_custom_data = {"v": "reschedule"}
    card.custom_data = json.dumps(new_custom_data)

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
