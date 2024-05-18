from typing import List

from anki.utils import ids2str
from aqt import mw
from aqt.gui_hooks import sync_will_start, sync_did_finish

from .configuration import Config
from .ease.auto_ease_factor import adjust_ease
from .schedule.disperse_siblings import disperse_siblings
from .schedule.reschedule import reschedule


def create_comparelog(local_rids: List[int], texts: List[str]) -> None:
    texts.clear()
    local_rids.clear()
    local_rids.extend([id for id in mw.col.db.list("SELECT id FROM revlog")])


def review_cid_remote(local_rids: List[int]):
    local_rid_string = ids2str(local_rids)
    # exclude entries where ivl == lastIvl: they indicate a dynamic deck without rescheduling
    remote_reviewed_cids = [
        cid
        for cid in mw.col.db.list(
            f"""SELECT DISTINCT cid
            FROM revlog
            WHERE id NOT IN {local_rid_string}
            AND type < 3
            """
        )  # type: 0=Learning, 1=Review, 2=relearn, 3=Relearning, 4=Manual
    ]
    return remote_reviewed_cids


def auto_reschedule(local_rids: List[int], texts: List[str]):
    if len(local_rids) == 0:
        return
    config = Config()
    config.load()
    if not config.auto_reschedule_after_sync:
        return

    remote_reviewed_cids = review_cid_remote(local_rids)

    fut = reschedule(
        None,
        recent=False,
        filter_flag=True,
        filtered_cids=set(remote_reviewed_cids),
    )

    if fut:
        # wait for reschedule to finish
        (reschedule_result_msg, _) = fut.result()
        texts.append(reschedule_result_msg)


def auto_disperse(local_rids: List[int], texts: List[str]):
    if len(local_rids) == 0:
        return
    config = Config()
    config.load()
    if not config.auto_disperse_after_sync:
        return

    remote_reviewed_cids = review_cid_remote(local_rids)
    remote_reviewed_cid_string = ids2str(remote_reviewed_cids)
    remote_reviewed_nids = [
        nid
        for nid in mw.col.db.list(
            f"""SELECT DISTINCT nid 
            FROM cards 
            WHERE id IN {remote_reviewed_cid_string}
        """
        )
    ]
    remote_reviewed_nid_string = ids2str(remote_reviewed_nids)

    fut = disperse_siblings(
        None,
        filter_flag=True,
        filtered_nid_string=remote_reviewed_nid_string,
        text_from_reschedule="<br>".join(texts),
    )

    if fut:
        # wait for disperse to finish
        return fut.result()


def auto_adjust_ease(local_rids: List[int], texts: List[str]):
    if len(local_rids) == 0:
        return

    remote_reviewed_cids = review_cid_remote(local_rids)

    fut = adjust_ease(
        None,
        recent=False,
        filter_flag=True,
        filtered_cids=set(remote_reviewed_cids),
    )

    if fut:
        # wait for adjustment to finish
        texts.append(fut.result())


def init_sync_hook():
    local_rids = []
    texts = []

    sync_will_start.append(lambda: create_comparelog(local_rids, texts))
    sync_did_finish.append(lambda: auto_adjust_ease(local_rids, texts))
    sync_did_finish.append(lambda: auto_reschedule(local_rids, texts))
    sync_did_finish.append(lambda: auto_disperse(local_rids, texts))
