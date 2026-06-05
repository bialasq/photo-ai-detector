"""Pydantic v2 request schemas with strict field constraints (task 1.4.2)."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

PERSON_NAME_PATTERN = re.compile(r"^[\w\s\-\.]+$", re.UNICODE)
MAX_FOLDER_PATH_LENGTH = 4096
MAX_PERSON_NAME_LENGTH = 200


class ScanFolderRequest(BaseModel):
    """Body for POST /api/scan-folder and POST /api/v1/scan-folder."""

    folder_path: str = Field(
        ...,
        min_length=1,
        max_length=MAX_FOLDER_PATH_LENGTH,
        description="Absolute or relative path to a local directory to scan recursively.",
        examples=[r"C:\Users\Photos\Vacation2024"],
    )

    @field_validator("folder_path")
    @classmethod
    def strip_folder_path(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("folder_path must not be empty or whitespace")
        return stripped


class IdentifyClusterRequest(BaseModel):
    """Body for POST /api/clusters/identify (cluster batch or single noise face)."""

    cluster_id: Optional[int] = Field(
        default=None,
        ge=0,
        description="DBSCAN cluster label (>= 0) for batch naming.",
    )
    face_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Single noise face id (cluster_id IS NULL) for Noise Inspector.",
    )
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=MAX_PERSON_NAME_LENGTH,
        description="Display name for a new profile (cluster or noise face).",
    )
    person_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Existing people.id when assigning a noise face to a named profile.",
    )

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be empty or whitespace")
        if not PERSON_NAME_PATTERN.fullmatch(stripped):
            raise ValueError(
                "name may only contain letters, numbers, spaces, hyphens, and periods"
            )
        return stripped

    @model_validator(mode="after")
    def validate_identify_target(self) -> IdentifyClusterRequest:
        has_cluster = self.cluster_id is not None
        has_face = self.face_id is not None

        if has_cluster == has_face:
            raise ValueError("Provide exactly one of cluster_id or face_id")

        if has_face:
            if self.person_id is not None:
                if self.name is not None:
                    raise ValueError(
                        "Provide person_id or name for face_id, not both"
                    )
                return self
            if self.name is None:
                raise ValueError("name is required when person_id is omitted for face_id")
            return self

        if self.person_id is not None:
            raise ValueError("person_id is only valid with face_id")
        if self.name is None:
            raise ValueError("name is required when cluster_id is provided")
        return self


class MergePeopleRequest(BaseModel):
    """Body for POST /api/people/merge."""

    target_person_id: int = Field(
        ...,
        ge=1,
        description="Person record that survives the merge.",
    )
    source_person_id: int = Field(
        ...,
        ge=1,
        description="Person record removed after faces are reassigned.",
    )

    @model_validator(mode="after")
    def validate_distinct_person_ids(self) -> MergePeopleRequest:
        if self.target_person_id == self.source_person_id:
            raise ValueError("source_person_id must differ from target_person_id")
        return self


class DevSimulateScanRequest(BaseModel):
    """Optional body for POST /api/dev/simulate-scan."""

    folder_path: Optional[str] = Field(
        default=None,
        max_length=MAX_FOLDER_PATH_LENGTH,
        description="Override dev scan folder (default: PHOTO_ORGANIZER_DEV_SCAN_FOLDER).",
    )
    reset_first: bool = Field(
        default=True,
        description="When true, wipe photos/faces/people before starting the scan.",
    )

    @field_validator("folder_path")
    @classmethod
    def strip_optional_folder_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
