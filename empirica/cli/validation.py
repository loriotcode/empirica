"""
CLI Input Validation Models

Pydantic models for validating JSON inputs to CLI commands.
Addresses CWE-20: Improper Input Validation.

Usage:
    from empirica.cli.validation import PreflightInput, validate_json_input

    validated = validate_json_input(raw_json, PreflightInput)
    # validated is now a PreflightInput instance or raises ValidationError
"""

import json
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar('T', bound=BaseModel)


# =============================================================================
# Epistemic Transaction Workflow Input Models
# =============================================================================

class VectorValues(BaseModel):
    """Epistemic vector values (0.0-1.0 scale)."""
    know: float = Field(ge=0.0, le=1.0, description="Knowledge level")
    uncertainty: float = Field(ge=0.0, le=1.0, description="Uncertainty level")
    context: float | None = Field(default=None, ge=0.0, le=1.0, description="Context understanding")
    engagement: float | None = Field(default=None, ge=0.0, le=1.0, description="Engagement level")
    clarity: float | None = Field(default=None, ge=0.0, le=1.0, description="Clarity of understanding")
    coherence: float | None = Field(default=None, ge=0.0, le=1.0, description="Coherence of knowledge")
    signal: float | None = Field(default=None, ge=0.0, le=1.0, description="Signal strength")
    density: float | None = Field(default=None, ge=0.0, le=1.0, description="Information density")
    state: float | None = Field(default=None, ge=0.0, le=1.0, description="Current state")
    change: float | None = Field(default=None, ge=0.0, le=1.0, description="Rate of change")
    completion: float | None = Field(default=None, ge=0.0, le=1.0, description="Phase-aware completion: NOETIC='Have I learned enough?' PRAXIC='Have I implemented enough?'")
    impact: float | None = Field(default=None, ge=0.0, le=1.0, description="Expected impact")
    do: float | None = Field(default=None, ge=0.0, le=1.0, description="Execution capability")


class PreflightInput(BaseModel):
    """Input model for preflight-submit command."""
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] = Field(description="Epistemic vector values")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for assessment")
    task_context: str | None = Field(default="", max_length=2000, description="Context for pattern retrieval")
    work_context: str | None = Field(
        default=None, description="Work context for maturity-aware calibration normalization",
        pattern="^(greenfield|iteration|investigation|refactor)$",
    )
    work_type: str | None = Field(
        default=None,
        description="Type of work being done — determines which evidence sources are relevant for grounded calibration",
        pattern="^(code|infra|research|release|debug|config|docs|data|comms|design|audit)$",
    )

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id format."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float]) -> dict[str, float]:
        """Validate vector values are in valid range."""
        if not v:
            raise ValueError('vectors cannot be empty')

        valid_keys = {'know', 'uncertainty', 'context', 'engagement', 'clarity',
                      'coherence', 'signal', 'density', 'state', 'change',
                      'completion', 'impact', 'do'}

        for key, value in v.items():
            if key not in valid_keys:
                raise ValueError(f'Unknown vector key: {key}')
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number, got {type(value).__name__}')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0, got {value}')

        # Require at least know and uncertainty
        if 'know' not in v or 'uncertainty' not in v:
            raise ValueError('vectors must include at least "know" and "uncertainty"')

        return v


class CheckInput(BaseModel):
    """Input model for check-submit command."""
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] | None = Field(default=None, description="Updated vector values")
    approach: str | None = Field(default="", max_length=2000, description="Planned approach")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for check")

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id is non-empty."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        """Validate optional vector values are in valid 0.0-1.0 range."""
        if v is None:
            return v
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0')
        return v


class PostflightInput(BaseModel):
    """Input model for postflight-submit command."""
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] = Field(description="Final epistemic vector values")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for assessment")
    learnings: str | None = Field(default="", max_length=5000, description="Key learnings from session")
    goal_id: str | None = Field(default=None, max_length=100, description="Associated goal ID")

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id is non-empty."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float]) -> dict[str, float]:
        """Validate required vector values are in valid 0.0-1.0 range."""
        if not v:
            raise ValueError('vectors cannot be empty')
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0')
        return v


# =============================================================================
# Finding/Unknown Input Models
# =============================================================================

class FindingInput(BaseModel):
    """Input model for finding-log command."""
    session_id: str = Field(min_length=1, max_length=100)
    finding: str = Field(min_length=1, max_length=5000)
    impact: float = Field(ge=0.0, le=1.0, default=0.5)
    domain: str | None = Field(default=None, max_length=100)
    goal_id: str | None = Field(default=None, max_length=100)


class UnknownInput(BaseModel):
    """Input model for unknown-log command."""
    session_id: str = Field(min_length=1, max_length=100)
    unknown: str = Field(min_length=1, max_length=5000)
    impact: float = Field(ge=0.0, le=1.0, default=0.5)
    goal_id: str | None = Field(default=None, max_length=100)


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_json_input(raw_json: str, model: type[T]) -> T:
    """
    Parse and validate JSON input against a Pydantic model.

    Args:
        raw_json: Raw JSON string
        model: Pydantic model class to validate against

    Returns:
        Validated model instance

    Raises:
        ValueError: If JSON is invalid or validation fails
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    return model.model_validate(data)


def validate_dict_input(data: dict[str, Any], model: type[T]) -> T:
    """
    Validate a dictionary against a Pydantic model.

    Args:
        data: Dictionary to validate
        model: Pydantic model class to validate against

    Returns:
        Validated model instance

    Raises:
        ValueError: If validation fails
    """
    return model.model_validate(data)


def safe_validate(data: dict[str, Any], model: type[T]) -> tuple[T | None, str | None]:
    """
    Safely validate data, returning (validated, None) or (None, error_message).

    Args:
        data: Dictionary to validate
        model: Pydantic model class

    Returns:
        Tuple of (validated_model, error_message)
    """
    try:
        validated = model.model_validate(data)
        return validated, None
    except Exception as e:
        return None, str(e)
