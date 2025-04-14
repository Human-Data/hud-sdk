from __future__ import annotations

import enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class CustomGym(BaseModel):
    """
    Public environment specification with a dockerfile and controller.
    
    If the location is remote, the env will be created on the server.
    If the location is dev, the env will be created locally via docker.
    """
    type: Literal["public"] = "public"
    dockerfile: str
    location: Literal["local", "remote"]
    # If path, then it is a development environment on the local computer
    # If none, then the controller must be installed in the environment through the dockerfile
    controller_source_dir: Path | None = None

# Strings are identifiers for gyms on the HUD server
Gym = CustomGym | str

class EnvironmentStatus(str, enum.Enum):
    """
    Status of the environment.

    Attributes:
        INITIALIZING: The environment is initializing
        RUNNING: The environment is running
        COMPLETED: The environment is completed
        ERROR: The environment is in an error state
    """

    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"

