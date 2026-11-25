"""Recall practice and spacing for mastered knowledge points."""

from datetime import timedelta

from sqlalchemy import and_, func, select, update

from app.db.models import CognitiveItem, KnowledgeNode, now, public


INTERVAL_DAYS = (1, 3, 7, 15, 15)


def eligible(doc_id):
    mastered_nodes = select(KnowledgeNode.id).where(
        KnowledgeNode.doc_id == doc_id, KnowledgeNode.mastery_score >= 80,
    ).correlate(None)
    return and_(
        CognitiveItem.doc_id == doc_id, CognitiveItem.status == "mastered",
        CognitiveItem.node_id.in_(mastered_nodes),
    )


def due_expression():
    return func.coalesce(CognitiveItem.next_review_at,
                         func.datetime(CognitiveItem.updated_at, f"+{INTERVAL_DAYS[0]} days"))


def lists(db, doc_id):
    when = now()
    cards = []
    rows = db.execute(select(CognitiveItem, KnowledgeNode.mastery_score)
                      .join(KnowledgeNode, CognitiveItem.node_id == KnowledgeNode.id)
                      .where(eligible(doc_id)).execution_options(populate_existing=True))
    for item, mastery in rows:
        card = public(item)
        card["mastery_score"] = mastery
        card["next_review_at"] = item.next_review_at or item.updated_at + timedelta(days=INTERVAL_DAYS[0])
        cards.append(card)
    cards.sort(key=lambda card: (card["next_review_at"], card["item_order"]))
    return {
        "due_items": [card for card in cards if card["next_review_at"] <= when],
        "all_reviewable_items": cards,
    }


def next_interval(stage, outcome):
    if outcome == "again":
        return 0, timedelta(minutes=10)
    stage = min(len(INTERVAL_DAYS), stage + 1)
    return stage, timedelta(days=INTERVAL_DAYS[stage - 1])


def record(db, item, outcome, observed_count):
    stage, interval = next_interval(item.review_stage, outcome)
    when = now()
    # A retried answer carries the same count and cannot advance the schedule twice.
    db.execute(update(CognitiveItem).where(
        CognitiveItem.id == item.id, eligible(item.doc_id), CognitiveItem.review_count == observed_count,
    ).values(review_stage=stage, review_count=CognitiveItem.review_count + 1,
             last_reviewed_at=when, next_review_at=when + interval, updated_at=CognitiveItem.updated_at)
      .execution_options(synchronize_session=False))
    db.commit()


def skip(db, item):
    when = now()
    db.execute(update(CognitiveItem).where(
        CognitiveItem.id == item.id, eligible(item.doc_id), due_expression() <= when,
    ).values(skip_count=CognitiveItem.skip_count + 1, next_review_at=when + timedelta(days=1),
             updated_at=CognitiveItem.updated_at).execution_options(synchronize_session=False))
    db.commit()


def advance(db, doc_id, days):
    db.execute(update(CognitiveItem).where(eligible(doc_id)).values(
        next_review_at=func.strftime("%Y-%m-%d %H:%M:%f", due_expression(), f"-{days} days"),
        updated_at=CognitiveItem.updated_at,
    ).execution_options(synchronize_session=False))
    db.commit()
