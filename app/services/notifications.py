from app.models.notification import Notification


def create_notification(db, user_id: str, title: str, message: str, href: str) -> Notification:
    notification = Notification(user_id=user_id, title=title, message=message, href=href)
    db.add(notification)
    return notification
