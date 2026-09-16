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
    description: Optional[str] = None
    date: str
    end_date: Optional[str] = None
    time: Optional[str]
    event_type: str
    class_id: int
    subject_id: Optional[int]
    created_by: int
    class Config: from_attributes = True

class CalendarEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    date: str
    end_date: Optional[str] = None
    time: Optional[str] = None
    event_type: str = "other"
    subject_id: Optional[int] = None

class HolidayImportRequest(BaseModel):
    state: str
    year: int
    class_id: Optional[int] = None

# Homework & Attachments
class AttachmentItem(BaseModel):
    url: str
    type: str = "file"  # "image" or "file"
    name: Optional[str] = None

class HomeworkOut(BaseModel):
    id: int
    subject_id: int
    class_id: int
    description: str
    due_date: str
    created_by: int
    created_at: Optional[datetime] = None
    checked_by: List[int] = []
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    attachments: List[AttachmentItem] = []
    class Config: from_attributes = True

class HomeworkCreate(BaseModel):
    subject_id: int
    description: str
    due_date: str
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = []

class HomeworkUpdate(BaseModel):
    subject_id: Optional[int] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = None

class SolutionUserOut(BaseModel):
    id: int
    display_name: Optional[str] = None
    email: str
    avatar_url: Optional[str] = None
    class Config: from_attributes = True

class HomeworkSolutionCreate(BaseModel):
    text: Optional[str] = None
    attachments: List[AttachmentItem] = []

class HomeworkSolutionOut(BaseModel):
    id: int
    homework_id: int
    user_id: int
    text: Optional[str] = None
    attachments: List[AttachmentItem] = []
    created_at: datetime
    user: Optional[SolutionUserOut] = None
    class Config: from_attributes = True

# Grade
class GradeOut(BaseModel):
    id: int
    subject_id: int
    value: float
    label: Optional[str]
    note: Optional[str]
    date: Optional[str]
    weight_type: Optional[str] = "exam"
    weight: Optional[float] = 1.0
    class Config: from_attributes = True

class GradeCreate(BaseModel):
    subject_id: int
    value: float
    label: Optional[str] = None
    note: Optional[str] = None
    date: Optional[str] = None
    weight_type: Optional[str] = "exam"
    weight: Optional[float] = 1.0

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

# Drive
class DriveFileOut(BaseModel):
    id: int
    class_id: int
    subject_id: Optional[int]
    subject_name: Optional[str] = None
    topic: Optional[str]
    uploader_id: int
    uploader_name: Optional[str] = None
    original_name: str
    file_size: int
    mime_type: Optional[str]
    is_lecture_notes: bool = False
    created_at: datetime
    class Config: from_attributes = True

class DriveFileUpdate(BaseModel):
    subject_id: Optional[int] = None
    topic: Optional[str] = None

class DriveTextUpdate(BaseModel):
    content: str

class DriveTopicOut(BaseModel):
    id: int
    class_id: int
    subject_id: int
    name: str
    file_count: int = 0
    class Config: from_attributes = True

class DriveTopicCreate(BaseModel):
    subject_id: int
    name: str

class DriveTopicRename(BaseModel):
    name: str

# Google Sync
class GoogleStatusOut(BaseModel):
    configured: bool
    connected: bool
    email: Optional[str] = None
    sync_calendar: bool = False
    sync_homework_tasks: bool = False

class GoogleSettingsUpdate(BaseModel):
    sync_calendar: Optional[bool] = None
    sync_homework_tasks: Optional[bool] = None

# Meal plan
class MealPlanDayOut(BaseModel):
    id: int
    date: str
    meal: str
    edited: bool
    class Config: from_attributes = True

class MealPlanDayUpdate(BaseModel):
    meal: str

class MealPlanOut(BaseModel):
    id: int
    image_url: str
    created_at: datetime
    days: List[MealPlanDayOut]
    class Config: from_attributes = True

# Push
class PushSubscriptionIn(BaseModel):
    subscription: Any
    user_agent: Optional[str] = None

class PushUnsubscribeIn(BaseModel):
    endpoint: Optional[str] = None

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

# Notification Settings
class ReminderItem(BaseModel):
    type: str  # "days" | "hours"
    value: int

class NotificationSettingOut(BaseModel):
    user_id: int
    enabled: bool
    homework_new: bool
    homework_reminders: List[ReminderItem]
    homework_daily_reminder: bool
    homework_daily_time: str
    event_new: bool
    event_reminders: List[ReminderItem]
    meal_reminder_mode: str
    meal_reminder_time: str
    timetable_changes: bool
    timetable_before_first_lesson: bool
    timetable_first_lesson_lead_minutes: int
    timetable_before_lesson_end: bool
    timetable_lesson_end_lead_minutes: int
    timetable_before_break: bool
    timetable_break_lead_minutes: int
    timetable_before_break_end: bool
    timetable_break_end_lead_minutes: int
    timetable_end_of_day_summary: bool
    timetable_end_of_day_delay_minutes: int
    class Config: from_attributes = True

class NotificationSettingUpdate(BaseModel):
    enabled: Optional[bool] = None
    homework_new: Optional[bool] = None
    homework_reminders: Optional[List[ReminderItem]] = None
    homework_daily_reminder: Optional[bool] = None
    homework_daily_time: Optional[str] = None
    event_new: Optional[bool] = None
    event_reminders: Optional[List[ReminderItem]] = None
    meal_reminder_mode: Optional[str] = None
    meal_reminder_time: Optional[str] = None
    timetable_changes: Optional[bool] = None
    timetable_before_first_lesson: Optional[bool] = None
    timetable_first_lesson_lead_minutes: Optional[int] = None
    timetable_before_lesson_end: Optional[bool] = None
    timetable_lesson_end_lead_minutes: Optional[int] = None
    timetable_before_break: Optional[bool] = None
    timetable_break_lead_minutes: Optional[int] = None
    timetable_before_break_end: Optional[bool] = None
    timetable_break_end_lead_minutes: Optional[int] = None
    timetable_end_of_day_summary: Optional[bool] = None
    timetable_end_of_day_delay_minutes: Optional[int] = None

# Subject Weighting
class SubjectWeightingCreate(BaseModel):
    subject_id: int
    exam_weight: float = 50.0
    oral_weight: float = 50.0
    planned_exams: int = 2

class SubjectWeightingOut(BaseModel):
    id: int
    class_id: int
    subject_id: int
    exam_weight: float
    oral_weight: float
    planned_exams: int
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None
    class Config: from_attributes = True

# Audit Log
class AuditLogOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    user_role: Optional[str] = None
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime
    class Config: from_attributes = True

# Storage Statistics
class StorageCategoryStats(BaseModel):
    bytes: int
    files: int

class StorageStatsOut(BaseModel):
    total_bytes: int
    total_files: int
    db_bytes: int
    categories: dict[str, StorageCategoryStats]
    disk_total_bytes: int
    disk_free_bytes: int


