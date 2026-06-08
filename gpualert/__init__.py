"""
gpualert — GPU job notifications with log delivery guarantee.

Every execution writes log files to ~/.gpualert/logs/.
Logs are always attached to notifications, success or failure.

Usage:
    gpualert run -- python train.py
    gpualert slurm 12345
    gpualert config --init
"""

__version__ = "0.1.2"
__author__ = "GPUAlert Contributors"
__license__ = "MIT"

from gpualert.config import GPUAlertConfig, load_config
from gpualert.types import ArtifactFile, JobResult, NotificationResult, SlurmJobInfo

__all__ = [
    "__version__",
    "JobResult",
    "ArtifactFile",
    "NotificationResult",
    "SlurmJobInfo",
    "GPUAlertConfig",
    "load_config",
]
