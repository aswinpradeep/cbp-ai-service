import uuid
import re
from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field, field_validator

from .comman import ApprovalStatus, Competency


# ─── Nested Schemas ───

class UserInfo(BaseModel):
    user_id: uuid.UUID
    username: str
    email: str

    class Config:
        from_attributes = True


# ─── Request Schemas ───

class SendForApprovalRequest(BaseModel):
    state_center_id: str = Field(..., min_length=1)
    department_id: Optional[str] = None
    role_mapping_ids: List[uuid.UUID] = Field(..., min_length=1, description="At least one role mapping must be selected")
    mdo_id: str = Field(..., min_length=1, description="MDO Admin/Leader ID who will receive the request")
    request_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("request_name")
    @classmethod
    def validate_request_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Request name cannot be empty")
        if len(v) > 100:
            raise ValueError("Request name cannot exceed 100 characters")
        # Only allow alphanumeric, spaces, underscores, hyphens
        if not re.match(r'^[a-zA-Z0-9\s_\-]+$', v):
            raise ValueError("Request name can only contain letters, numbers, spaces, underscores (_) and hyphens (-)")
        return v

    @field_validator("role_mapping_ids")
    @classmethod
    def validate_role_mapping_ids(cls, v: List[uuid.UUID]) -> List[uuid.UUID]:
        if not v:
            raise ValueError("At least one role mapping must be selected")
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for rid in v:
            if rid not in seen:
                seen.add(rid)
                unique.append(rid)
        return unique


class RevokeApprovalRequest(BaseModel):
    request_id: uuid.UUID = Field(..., description="The approval request UUID")


class ApprovalRequestListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)
    search: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = Field(default=None)


# ─── Response Schemas ───

class ApprovalRequestItemResponse(BaseModel):
    id: uuid.UUID
    source_role_mapping_id: uuid.UUID
    
    designation_name: str = Field(..., min_length=1, max_length=255, description="Name of the designation")
    wing_division_section: str = Field(..., max_length=255, description="Wing/Division/Section name")
    role_responsibilities: List[str] = Field(default=[], description="List of role responsibilities")
    activities: List[str] = Field(default=[], description="List of activities")
    competencies: List[Competency] = Field(default=[], description="List of competencies")
    sort_order: Optional[int] = Field(None, description="Sort order for hierarchical arrangement")
    igot_designation_name: Optional[str] = Field(None, description="Designation name as it exists in the iGOT portal")
    igot_designation_id: Optional[str] = Field(None, description="Designation ID from the iGOT portal")

    status: ApprovalStatus
    rejected_at: Optional[datetime] = None
    reviewer_comments: Optional[str] = None
    created_at: datetime
    cbp_plan_data: Optional[Any] = None

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }


class ApprovalRequestResponse(BaseModel):
    id: uuid.UUID
    request_name: str
    user_id: uuid.UUID
    user: Optional[UserInfo] = None
    org_type: Optional[str] = None
    state_center_id: str = Field(..., description="ID of the associated state/center")
    state_center_name: str = Field(..., description="Name of the associated state/center")
    department_id: Optional[str] = Field(None, description="ID of the associated department")
    department_name: Optional[str] = Field(None, description="Name of the associated department")
    mdo_id: str
    designation_count: int
    status: ApprovalStatus
    rejected_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    reviewer_comments: Optional[str] = None
    items: List[ApprovalRequestItemResponse] = []

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }


class ApprovalRequestListItem(BaseModel):
    id: uuid.UUID
    request_name: str
    user: Optional[UserInfo] = None
    designation_count: int
    status: ApprovalStatus
    created_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }


class ApprovalRequestListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[ApprovalRequestListItem]


class SendForApprovalResponse(BaseModel):
    id: uuid.UUID
    status: ApprovalStatus
    designation_count: int
    created_at: datetime
    message: str

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }



class RevokeApprovalResponse(BaseModel):
    id: uuid.UUID
    status: ApprovalStatus
    revoked_at: datetime
    message: str

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }


class MDOAdmin(BaseModel):
    """Schema for MDO admin/leader user"""
    id: str = Field(..., description="User ID")
    first_name: str = Field(..., description="First name of the user")
    last_name: str = Field(..., description="Last name of the user")
    role_type: str = Field(..., description="Role type: MDO_ADMIN or MDO_LEADER")
    department_name: str = Field(..., description="Department/Organization name")


class MDOAdminListResponse(BaseModel):
    """Schema for MDO admin list response"""
    admins: List[MDOAdmin] = Field(default=[], description="List of MDO admins and leaders")
    count: int = Field(..., description="Total count of admins")