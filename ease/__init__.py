from aqt.gui_hooks import reviewer_will_answer_card
from .auto_ease_factor import adjust_factor_when_review

def init_ease_adjust_review_hook():
    reviewer_will_answer_card.append(adjust_factor_when_review)