from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from ..core.database import Base
from datetime import datetime

class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True, index=True)
    subject = Column(String)
    sender = Column(String)
    date = Column(DateTime)
    category = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    account = Column(String)  # The email account this belongs to
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "date": self.date.isoformat() if self.date else datetime.now().isoformat(),
            "category": self.category or "Uncategorized",
            "content": self.content,
            "is_read": self.is_read,
            "account": self.account,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        } 