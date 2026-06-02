"""
Centralized SDN-style routing for StarryNet emulations.

Control plane: snapshot-based shortest-path computation from delay matrices.
Data plane: kernel static routes installed via docker exec (no Open vSwitch).
"""

from starrynet.sdn_routing.config import SdnConfig
from starrynet.sdn_routing.controller import SdnController

__all__ = ["SdnConfig", "SdnController"]
