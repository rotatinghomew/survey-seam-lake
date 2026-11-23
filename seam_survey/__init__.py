"""Seam survey ledger.

`seam_survey` turns registered survey batches into defensible, deliverable
zone coverage.  It deliberately keeps three things separate:

* raw measurements in whatever local network and unit a team used;
* the registered control network that supplies the transformation basis;
* the common-coordinate coverage that a designer actually references.

Nothing here silently repairs geometry.  A batch is only usable when its
control association is registered, its residual is within tolerance, and the
resulting coverage really meets the registered responsibility area.
"""

from .ledger import SurveyLedger
from .models import (
    BatchDiagnostics,
    ControlNetwork,
    DesignReference,
    FeatureObservation,
    HandoverReport,
    Issue,
    PairCheck,
    Partition,
    PartitionFeature,
    PartitionReadiness,
    Project,
    ReviewRecord,
    SeamAssessment,
    SeamGroup,
    SurveyBatch,
    TransformRecord,
    Zone,
)
from .errors import (
    ConflictError,
    CoverageError,
    GeometryError,
    LedgerError,
    RegistrationError,
    SeamError,
    ToleranceError,
)
from .facts import load_facts
from .lineage import LinkPath, LinkStep
from .transform import ControlLink, LinkBuilder, Similarity

__all__ = [
    "SurveyLedger",
    "load_facts",
    "BatchDiagnostics",
    "ControlNetwork",
    "ControlLink",
    "DesignReference",
    "FeatureObservation",
    "HandoverReport",
    "Issue",
    "LinkBuilder",
    "LinkPath",
    "LinkStep",
    "PairCheck",
    "Partition",
    "PartitionFeature",
    "PartitionReadiness",
    "Project",
    "ReviewRecord",
    "SeamAssessment",
    "SeamGroup",
    "Similarity",
    "SurveyBatch",
    "TransformRecord",
    "Zone",
    "ConflictError",
    "CoverageError",
    "GeometryError",
    "LedgerError",
    "RegistrationError",
    "SeamError",
    "ToleranceError",
]
