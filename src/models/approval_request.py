import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, DateTime, Enum, ForeignKey, Integer, Text, JSON
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from ..core.database import Base
from ..schemas.comman import ApprovalStatus

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_name = Column(String(100), nullable=False, index=True)  # User-friendly name for the request, e.g. "Role Mapping Update for State X"
    
    # Who submitted
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Context
    org_type = Column(
        String(20),
        nullable=True,
        index=True,
        comment="Organization type: ministry or state"
    )
    state_center_id = Column(String(255), nullable=False, index=True)
    department_id = Column(String(255), nullable=True, index=True)
    state_center_name = Column(String(255), nullable=False)
    department_name = Column(String(255), nullable=True)
    # Approver (MDO Admin/Leader)
    mdo_id = Column(String(255), nullable=False, index=True)
    
    # Counts
    designation_count = Column(Integer, nullable=False, default=0)
    
    # Status
    status = Column(
        Enum(ApprovalStatus, name="approval_status_enum", create_type=True),
        nullable=False,
        default=ApprovalStatus.PENDING
    )
    
    # Timestamps
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Reviewer comments (set by MDO)
    reviewer_comments = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], lazy="selectin")
    items = relationship("ApprovalRequestItem", back_populates="approval_request", cascade="all, delete-orphan", lazy="selectin")

class ApprovalRequestItem(Base):
    __tablename__ = "approval_request_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    approval_request_id = Column(UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Snapshot of role mapping at time of submission
    source_role_mapping_id = Column(UUID(as_uuid=True), nullable=False)  # Original role_mapping.id reference
    
    designation_name = Column(
        String(255), 
        nullable=False,
        index=True
    )
    wing_division_section = Column(
        String(255), 
        nullable=True
    )
    role_responsibilities = Column(
        JSONB,
        default=list,
        nullable=True
    )
    activities = Column(
        JSONB,
        default=list,
        nullable=True
    )
    competencies = Column(
        JSONB,
        default=list,
        nullable=True
    )

    sort_order = Column(
        Integer,
        nullable=True,
        index=True,
        comment="Sort order for hierarchical arrangement of designations (1=highest, higher numbers=lower hierarchy)"
    )

    # iGOT portal matched designation fields
    igot_designation_name = Column(
        String(255),
        nullable=True,
        comment="Designation name as it exists in the iGOT portal (populated after validation)"
    )
    igot_designation_id = Column(
        String(255),
        nullable=True,
        comment="Designation ID from the iGOT portal (populated after validation)"
    )
    
    # CBP Plan snapshot
    cbp_plan_data = Column(JSON, nullable=True)
    status = Column(
        Enum(ApprovalStatus, name="approval_request_item_status_enum", create_type=True),
        nullable=False,
        default=ApprovalStatus.PENDING
    )
     # Reviewer comments (set by MDO)
    reviewer_comments = Column(Text, nullable=True)

    rejected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationship back to request
    approval_request = relationship("ApprovalRequest", back_populates="items")