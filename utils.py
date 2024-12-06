import json
import math
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import List, Union

from anki.cards import Card
from anki.stats import (
    REVLOG_LRN,
    REVLOG_REV,
    REVLOG_RELRN,
    REVLOG_CRAM,
)
from anki.stats_pb2 import CardStatsResponse
from aqt import mw
from aqt.utils import showWarning

SCHEDULER_NAME = "Custom Scheduler"
CUR_SCHEDULER_VERSION = (1, 0, 0)
CUR_SCHEDULER_VERSION_STR = ".".join(map(str, CUR_SCHEDULER_VERSION))
GLOBAL_DECK_CONFIG_NAME = "global config for Custom Scheduler"
DECK_NAME_PARAM = "deckName"
DAYS_UPPER_PARAM = "daysUpper"

ALL_PARAMS = [DAYS_UPPER_PARAM]


def get_version(custom_scheduler):
    str_matches = re.findall(rf'// {SCHEDULER_NAME} v(\d+)\.(\d+)\.(\d+)', custom_scheduler)
    try:
        version = tuple(map(int, str_matches[0]))
    except IndexError:
        mw.taskman.run_on_main(lambda: showWarning(
            f"Please check whether the version of {SCHEDULER_NAME} matches {CUR_SCHEDULER_VERSION_STR}"))
        return
    if version != CUR_SCHEDULER_VERSION:
        mw.taskman.run_on_main(lambda: showWarning(
            f"Please check whether the version of {SCHEDULER_NAME} matches {CUR_SCHEDULER_VERSION_STR}"))
        return
    return version


class CustomSchedulerNotFoundError(BaseException):
    def __init__(self):
        self.message = "Paste the custom_scheduler.js into custom scheduling in the deck config."


def check_custom_scheduler(all_config):
    if "cardStateCustomizer" not in all_config:
        raise CustomSchedulerNotFoundError()
    custom_scheduler = all_config['cardStateCustomizer']
    if f"// {SCHEDULER_NAME}" not in custom_scheduler:
        raise CustomSchedulerNotFoundError()
    get_version(custom_scheduler)
    return custom_scheduler


def _remove_comment_line(custom_scheduler):
    not_comment_line = '\n'.join([re.sub('^ *//..*$', '', _) for _ in custom_scheduler.split('\n')])
    return not_comment_line

# Add base exception for all deck param errors
class DeckParamError(BaseException):
    def __init__(self):
        super().__init__(self.message)
        
class MalFormedDeckParamsError(DeckParamError):
    def __init__(self):
        self.message = f"""{SCHEDULER_NAME} ERROR: The deckParams are not properly formatted.
        Please check your deckParams in custom scheduler.
        """

class GlobalDeckParamsMissingError(DeckParamError):
    def __init__(self):
        self.message = f"""{SCHEDULER_NAME} ERROR: The global deckParams are not defined.
        Please check your deckParams in custom scheduler contain params for deckName="{GLOBAL_DECK_CONFIG_NAME}".
        """

class GlobalDeckSomeParamsMissingError(DeckParamError):
    def __init__(self):
        self.message = f"""{SCHEDULER_NAME} ERROR: The global deckParams or {DAYS_UPPER_PARAM} are not defined.
        Please check your deckParams in custom scheduler.
        """
        
class DeckNameParamMissingError(DeckParamError):
    def __init__(self):
        self.message = f"""{SCHEDULER_NAME} ERROR: The deckName parameter is missing.
        Please check that each deckParam in custom scheduler contains a deckName="..." parameter.
        """


def get_deck_parameters(custom_scheduler):
    custom_scheduler = _remove_comment_line(custom_scheduler)

    params_array_pat = r'const deckParams = (\[[\s\S]*?\]);'
    hanging_comma_pat = r',(\s*?(?:\}|\]))'
    deck_params_str = re.search(params_array_pat, custom_scheduler).group(1)
    deck_params_str = re.sub(hanging_comma_pat, r'\1', deck_params_str)
    try:
        deck_parameters = json.loads(deck_params_str)
    except json.JSONDecodeError:
        raise MalFormedDeckParamsError()

    global_config = None
    for deck_param in deck_parameters:
        if deck_param[DECK_NAME_PARAM] == GLOBAL_DECK_CONFIG_NAME:
            global_config = deck_param
            if not all(param in global_config for param in ALL_PARAMS):
                raise GlobalDeckSomeParamsMissingError()
        else:
            if DECK_NAME_PARAM not in deck_param:
                raise DeckNameParamMissingError()
    if global_config is None:
        raise GlobalDeckParamsMissingError()
    
    # Fill in missing parameters with global config
    for deck_param in deck_parameters:
        for param in ALL_PARAMS:
            if param not in deck_param:
                deck_param[param] = global_config[param]
    
    # Sort the deck parameters by deck name, so that parent decks are sorted before sub-decks
    deck_parameters = sorted(deck_parameters, key=lambda x: x[DECK_NAME_PARAM])
    deck_parameters = OrderedDict(
        {deck_param[DECK_NAME_PARAM]: deck_param for deck_param in deck_parameters}
    )
            
    return deck_parameters


def get_current_deck_parameter(deckname, deck_parameters):
    """
    Get the deck parameters for the current deck.
    Will default to global deck parameters and then override with deck specific parameters.
    Additionally parent deck params will be applied first and then overridden by sub-deck params.
    """
    deck_params = deck_parameters[GLOBAL_DECK_CONFIG_NAME].copy()
    for name, params in deck_parameters.items():
        if deckname.startswith(name):
            deck_params.update(params)
            # continue looping to override with more specific deck params
    print(deckname, json.dumps(deck_params, indent=2))
    return deck_params


def get_skip_decks(custom_scheduler):
    pattern = r'[const ]?skipDecks ?= ?(.*);'
    str_matches = re.findall(pattern, custom_scheduler)
    try:
        names = str_matches[0].split(', ')
    except IndexError:
        mw.taskman.run_on_main(lambda: showWarning(
            "Skip decks are not found in the custom scheduler. Please always include it, even if empty"))
        return []
    deck_names = list(map(lambda x: x.strip(']["'), names))
    non_empty_deck_names = list(filter(lambda x: x != '', deck_names))

    decks = []
    missing_decks = []
    for skip_deck_name in non_empty_deck_names:
        deck = mw.col.decks.by_name(skip_deck_name)
        if deck is not None:
            decks.append(deck)
        else:
            missing_decks.append(skip_deck_name)
    if len(missing_decks) > 0:
        mw.taskman.run_on_main(lambda: showWarning(
            f"Decks {missing_decks} are not found in the collection. Check the deck names."))
    return decks

def RepresentsInt(s):
    try:
        return int(s)
    except ValueError:
        return None


def reset_ivl_and_due(cid: int, revlogs: List[CardStatsResponse.StatsRevlogEntry]):
    card = mw.col.get_card(cid)
    card.ivl = int(revlogs[0].interval / 86400)
    due = (
            math.ceil(
                (revlogs[0].time + revlogs[0].interval - mw.col.sched.day_cutoff) / 86400
            )
            + mw.col.sched.today
    )
    if card.odid:
        card.odue = max(due, 1)
    else:
        card.due = due
    mw.col.update_card(card)


def filter_revlogs(
        revlogs: List[CardStatsResponse.StatsRevlogEntry],
) -> List[CardStatsResponse.StatsRevlogEntry]:
    return list(filter(lambda x: x.review_kind != REVLOG_CRAM or x.ease != 0, revlogs))


def get_last_review_date(card: Card):
    revlogs = mw.col.card_stats_data(card.id).revlog
    try:
        last_revlog = list(filter(lambda x: x.button_chosen >= 1, revlogs))[0]
        last_review_date = (
                math.ceil((last_revlog.time - mw.col.sched.day_cutoff) / 86400)
                + mw.col.sched.today
        )
    except IndexError:
        due = card.odue if card.odid else card.due
        last_review_date = due - card.ivl
    return last_review_date


def update_card_due_ivl(card: Card, new_ivl: int):
    # Don't change ivl, it leads to ever-increasing ivl when reschedule is applied repeatedly
    # card.ivl = new_ivl
    last_review_date = get_last_review_date(card)
    if card.odid:
        card.odue = max(last_review_date + new_ivl, 1)
    else:
        card.due = last_review_date + new_ivl
    return card


def has_again(revlogs: List[CardStatsResponse.StatsRevlogEntry]):
    for r in revlogs:
        if r.button_chosen == 1:
            return True
    return False


def has_manual_reset(revlogs: List[CardStatsResponse.StatsRevlogEntry]):
    last_kind = None
    for r in revlogs:
        if r.button_chosen == 0:
            return True
        if (
                last_kind is not None
                and last_kind in (REVLOG_REV, REVLOG_RELRN)
                and r.review_kind == REVLOG_LRN
        ):
            return True
        last_kind = r.review_kind
    return False


def get_fuzz_range(interval, elapsed_days):
    min_ivl = max(2, int(round(interval * 0.95 - 1)))
    max_ivl = int(round(interval * 1.05 + 1))
    if interval > elapsed_days:
        min_ivl = max(min_ivl, elapsed_days + 1)
    return min_ivl, max_ivl


def due_to_date(due: int) -> str:
    offset = due - mw.col.sched.today
    today_date = datetime.today()
    return (today_date + timedelta(days=offset)).strftime("%Y-%m-%d")


def power_forgetting_curve(elapsed_days, stability):
    return (1 + elapsed_days / (9 * stability)) ** -1

def add_dict_key_value( dict: dict,key: str, value: Union[str, None]):
    if value is not None:
        dict[key] = value
    elif key in dict:
        dict.pop(key, None)

def write_custom_data(
    card: Card,
    key: str = None,
    value: str = None,
    key_values: list[tuple[str, str]] = None,
):
    if card.custom_data != "":
        custom_data = json.loads(card.custom_data)
    else:
        custom_data = {}
    if key_values is not None:
        for k, v in key_values:
            add_dict_key_value(custom_data,k, v)
    else:
        add_dict_key_value(custom_data, key, value)
    card.custom_data = json.dumps(custom_data)


def rotate_number_by_k(N, K):
    num = str(N)
    length = len(num)
    K = K % length
    rotated = num[K:] + num[:K]
    return int(rotated)


def get_rev_conf(card: Card):
    deck_id = card.did
    if card.odid:
        deck_id = card.odid
    try:
        deck_easy_fct = mw.col.decks.config_dict_for_deck_id(
            deck_id)['rev']['ease4']
    except KeyError:
        deck_easy_fct = 1.3
    try:
        deck_hard_fct = mw.col.decks.config_dict_for_deck_id(
            deck_id)['rev']['hardFactor']
    except KeyError:
        deck_hard_fct = 1.2
    try:
        deck_max_ivl = mw.col.decks.config_dict_for_deck_id(
            deck_id)['rev']['maxIvl']
    except KeyError:
        deck_max_ivl = 3650
    try:
        deck_again_fct = mw.col.decks.config_dict_for_deck_id(
            deck_id)['lapse']['mult']
    except KeyError:
        deck_max_ivl = 0
    return {
        'deck_easy_fct': deck_easy_fct,
        'deck_hard_fct': deck_hard_fct,
        'deck_max_ivl': deck_max_ivl,
        'deck_again_fct': deck_again_fct,
    }
