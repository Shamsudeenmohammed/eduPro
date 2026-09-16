"""All hostel forms.

    from hostel import forms
    forms.HostelApplyForm
"""

from .accommodation import AmenityForm, HostelBlockForm, HostelFloorForm, HostelForm, HostelRoomForm  # noqa: F401
from .allocation import AllocationForm, CheckInForm, CheckoutForm, TransferReviewForm  # noqa: F401
from .incident import HostelIncidentForm  # noqa: F401
from .maintenance import BedMaintenanceForm  # noqa: F401
from .policy import HostelFeeConfigForm, HostelPolicyForm  # noqa: F401
from .student import HostelApplyForm, HostelTransferRequestForm  # noqa: F401