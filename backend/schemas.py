from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime

# User
class UserOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    role: str
    class_id: Optional[int]
    muted_user_ids: Optional[str] = "[]"
    muted_room_ids: Optional[str] = "[]"
    avatar_url: Optional[str] = None
    class Config: from_attributes = True

class UserUpdate(BaseModel):
    display_name: Optional[str] = None

class UserAdminUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    class_id: Optional[int] = None

class UserCreate(BaseModel):
    email: str
    display_name: Optional[str] = None
    role: str = "student"
    class_id: Optional[int] = None

# ClassGroup
class ClassGroupOut(BaseModel):
    id: int
    name: str
    class Config: from_attributes = True

class ClassGroupCreate(BaseModel):
    name: str

# Subject
class SubjectOut(BaseModel):
    id: int
    name: str
    short_name: Optional[str]
    color: Optional[str]
    class_id: Optional[int]
    is_global: bool
    class Config: from_attributes = True

class SubjectCreate(BaseModel):
    name: str
    short_name: Optional[str] = None
    color: Optional[str] = "#6750a4"
    is_global: bool = False

# Calendar
class CalendarEventOut(BaseModel):
    id: int
    title: str
    date: str
    time: Optional[str]
    event_type: str
    class_id: int
    subject_id: Optional[int]
    created_by: int
    class Config: from_attributes = True

class CalendarEventCreate(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    event_type: str = "other"
    subject_id: Optional[int] = None

# Homework
class HomeworkOut(BaseModel):
    id: int
    subject_id: int
    class_id: int
    description: str
    due_date: str
    created_by: int
    checked_by: List[int]
    class Config: from_attributes = True

class HomeworkCreate(BaseModel):
    subject_id: int
    description: str
    due_date: str

# Grade
class GradeOut(BaseModel):
    id: int
    subject_id: int
    value: float
    label: Optional[str]
    note: Optional[str]
    date: Optional[str]
    class Config: from_attributes = True

class GradeCreate(BaseModel):
    subject_id: int
    value: float
    label: Optional[str] = None
    note: Optional[str] = None
    date: Optional[str] = None

# Chat
class ChatRoomOut(BaseModel):
    id: int
    name: Optional[str]
    is_group: bool
    member_ids: List[int]
    is_muted: bool = False
    class Config: from_attributes = True

class MessageOut(BaseModel):
    id: int
    room_id: int
    sender_id: int
    content: Optional[str]
    file_url: Optional[str]
    file_type: Optional[str]
    created_at: datetime
    read_by: List[int]
    reply_to_id: Optional[int] = None
    reply_preview: Optional[dict] = None
    edited: bool = False
    deleted: bool = False
    waveform: Optional[List[int]] = None
    poll_data: Optional[dict] = None
    reactions: Optional[dict] = None
    class Config: from_attributes = True

# Shared Files
class SharedFileOut(BaseModel):
    id: int
    uploader_id: int
    original_name: str
    file_size: int
    mime_type: Optional[str]
    visibility: str
    visible_to: List[int]
    class_id: Optional[int]
    expires_at: datetime
    created_at: datetime
    class Config: from_attributes = True

# Push
class PushSubscriptionIn(BaseModel):
    subscription: Any

class PushNotificationIn(BaseModel):
    title: str
    body: str
    target: str = "class"  # class | all | user:{id}

class NotificationOut(BaseModel):
    id: int
    title: str
    body: Optional[str]
    is_read: bool
    created_at: datetime
    class Config: from_attributes = True
