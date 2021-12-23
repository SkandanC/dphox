from copy import deepcopy as copy
from dataclasses import dataclass

import numpy as np
from shapely.geometry import MultiPolygon

from .device import Device
from .foundry import CommonLayer, OXIDE, SILICON
from .parametric import bezier_dc, dc, grating_arc, straight
from .pattern import Box, Pattern, Port
from .typing import Float2, Int2, Optional, Union
from .utils import fix_dataclass_init_docs

try:
    import plotly.graph_objects as go
except ImportError:
    pass


@fix_dataclass_init_docs
@dataclass
class DC(Pattern):
    """Directional coupler

    A directional coupler is a `symmetric` component (across x and y dimensions) that contains two waveguides that
    interact and couple light by adiabatically bending the waveguides towards each other, interacting over some
    interaction length :code:`interaction_l`, and then adiabatically bending out to the original interport distance.
    An MMI can actually be created if the gap_w is set to be negative.

    Attributes:
        waveguide_w: Waveguide width at the inputs and outputs.
        bend_radius: The bend radius of the directional coupler
        gap_w: Gap between the waveguides in the interaction region.
        interaction_l: Interaction length for the interaction region.
        coupler_waveguide_w: Coupling waveguide width
        end_bend_extent: If specified, places an additional end bend
        use_radius: use radius to define bends
    """

    waveguide_w: float
    bend_radius: float
    interport_distance: float
    gap_w: float
    interaction_l: float
    euler: float = 0.2
    end_l: float = 0
    coupler_waveguide_w: Optional[float] = None
    use_radius: bool = False

    def __post_init__(self):
        self.coupler_waveguide_w = self.waveguide_w if self.coupler_waveguide_w is None else self.coupler_waveguide_w
        radius, dy, w = (self.bend_radius, (self.interport_distance - self.gap_w - self.coupler_waveguide_w) / 2,
                     self.coupler_waveguide_w)
        lower_path = dc(radius, dy, self.interaction_l, self.euler).path(self.waveguide_w)
        upper_path = dc(radius, -dy, self.interaction_l, self.euler).path(self.waveguide_w)
        upper_path.translate(dx=0, dy=self.interport_distance)
        super(DC, self).__init__(lower_path, upper_path)
        self.lower_path, self.upper_path = lower_path, upper_path
        self.port['a0'] = Port(0, 0, -180, w=self.waveguide_w)
        self.port['a1'] = Port(0, self.interport_distance, -180, w=self.waveguide_w)
        self.port['b0'] = Port(self.size[0], 0, w=self.waveguide_w)
        self.port['b1'] = Port(self.size[0], self.interport_distance, w=self.waveguide_w)
        self.lower_path.port = {'a0': self.port['a0'].copy, 'b0': self.port['b0'].copy}
        self.upper_path.port = {'a0': self.port['a1'].copy, 'b0': self.port['b1'].copy}
        self.refs.extend([self.lower_path, upper_path])

    @property
    def interaction_points(self) -> np.ndarray:
        bl = np.asarray(self.center) - np.asarray((self.interaction_l, self.waveguide_w + self.gap_w)) / 2
        tl = bl + np.asarray((0, self.waveguide_w + self.gap_w))
        br = bl + np.asarray((self.interaction_l, 0))
        tr = tl + np.asarray((self.interaction_l, 0))
        return np.vstack((bl, tl, br, tr))

    @property
    def path_array(self):
        return np.array([self.polygons[:3], self.polygons[3:]])


@fix_dataclass_init_docs
@dataclass
class Cross(Pattern):
    """Cross

    Attributes:
        waveguide: waveguide to form the crossing (used to implement tapering)
    """

    waveguide: Pattern

    def __post_init__(self):
        horizontal = self.waveguide.align()
        vertical = self.waveguide.align().copy.rotate(90)
        super().__init__(horizontal, vertical)
        self.port['a0'] = horizontal.port['a0']
        self.port['a1'] = vertical.port['a0']
        self.port['b0'] = horizontal.port['b0']
        self.port['b1'] = vertical.port['b0']


@fix_dataclass_init_docs
@dataclass
class Array(Pattern):
    """Array of boxes or ellipses for 2D photonic crystals.

    This class can generate large circle arrays which may be used for photonic crystal designs or for slow
    light applications.

    Attributes:
        unit: The pattern to repeat in the array
        grid_shape: Number of rows and columns
        pitch: The distance between the circles in the Hole array
        n_points: The number of points in the circle (it can save time to use fewer points).

    """
    unit: Pattern
    grid_shape: Int2
    # orientation: Union[float, np.ndarray]
    pitch: Optional[Union[float, Float2]] = None

    def __post_init__(self):
        self.pitch = (self.pitch, self.pitch) if isinstance(self.pitch, float) else self.pitch
        super().__init__(MultiPolygon([self.unit.copy.translate(i * self.pitch, j * self.pitch)
                                       for i in range(self.grid_shape[0])
                                       for j in range(self.grid_shape[1])
                                       ]))


@fix_dataclass_init_docs
@dataclass
class WaveguideDevice(Device):
    """Waveguide cross section allowing specification of ridge and slab waveguides.

    Attributes:
        ridge_waveguide: The ridge waveguide (the thick section of the rib), represented as a :code:`Waveguide` object
            to allow features such as tapering and coupling. Generally this should be smaller than
            :code:`slab_waveguide`. The port of this device is defined using the port of the ridge waveguide.
        slab_waveguide: The slab waveguide (the thin section of the rib), represented as a :code:`Waveguide` object
            to allow features such as tapering and coupling. Generally this should be larger than
            :code:`ridge_waveguide`. If not specified, this merely implements a waveguide pattern.
        ridge: Ridge layer.
        slab: Slab layer.
        name: The device name.
    """
    ridge_waveguide: Pattern
    slab_waveguide: Optional[Pattern] = None
    ridge: str = CommonLayer.RIDGE_SI
    slab: str = CommonLayer.RIB_SI
    name: str = "rib_wg"

    def __post_init__(self):
        pattern_to_layer = [(self.ridge_waveguide, self.ridge)]
        pattern_to_layer += [(self.slab_waveguide, self.slab)] if self.slab_waveguide is not None else []
        super().__init__(self.name, pattern_to_layer)
        self.port = {'a0': self.ridge_waveguide.port['a0'].copy, 'b0': self.ridge_waveguide.port['b0'].copy}


@fix_dataclass_init_docs
@dataclass
class StraightGrating(Device):
    """Straight (non-focusing) grating with partial etch.

    Attributes:
        extent: Dimension of the extent of the grating.
        waveguide: The waveguide to connect to the grating structure (can be tapered if desired)
        pitch: The pitch between the grating teeth.
        duty_cycle: The fill factor for the grating.
        rib_grow: Offset the rib / slab layer in size (usually positive).
        num_periods: The number of periods (uses maximum given extent and pitch if not specified).
        name: Name of the device.
        ridge: The ridge layer for the partial etch.
        slab: The slab layer for the partial etch.

    """
    extent: Float2
    waveguide: Pattern
    pitch: float
    duty_cycle: float = 0.5
    rib_grow: float = 0
    num_periods: Optional[int] = None
    name: str = 'straight_grating'
    ridge: CommonLayer = CommonLayer.RIDGE_SI
    slab: CommonLayer = CommonLayer.RIB_SI

    def __post_init__(self):
        self.stripe_w = self.pitch * (1 - self.duty_cycle)
        slab = (Box(self.extent).hstack(self.waveguide).buffer(self.rib_grow), self.slab)
        grating = (Box(self.extent).hstack(self.waveguide).striped(self.stripe_w, (self.pitch, 0)), self.ridge)
        super().__init__(self.name, [slab, grating, (self.waveguide, self.ridge)])
        self.port['a0'] = self.waveguide.port['a0'].copy


@fix_dataclass_init_docs
@dataclass
class FocusingGrating(Device):
    """Focusing grating with partial etch.

    Attributes:
        angle: The opening angle for the focusing grating.
        waveguide: The waveguide for the focusing grating.
        wavelength: wavelength accepted by the grating.
        duty_cycle: duty cycle for the grating
        n_clad: clad material index of refraction (assume oxide by default).
        n_core: core material index of refraction (assume silicon by default).
        fiber_angle: angle of the fiber in degrees.
        num_periods: number of grating periods
        num_evaluations: Number of evaluations for the curve.
        grating_frac: The fraction of the distance radiating from the center occupied by the grating (otherwise ridge).
        rib_grow: Offset the rib / slab layer in size (usually positive).
        name: Name of the device.
        ridge: The ridge layer for the partial etch.
        slab: The slab layer for the partial etch.

    """
    angle: float
    waveguide_w: float
    waveguide_l: float
    n_clad: int = OXIDE.n
    n_core: int = SILICON.n
    min_period: int = 10
    num_periods: int = 20
    wavelength: float = 1.55
    fiber_angle: float = 8
    duty_cycle: float = 0.5
    grating_frac: float = 1
    num_evaluations: int = 16
    rib_grow: float = 1
    name: str = 'focusing_grating'
    ridge: CommonLayer = CommonLayer.RIDGE_SI
    slab: CommonLayer = CommonLayer.RIB_SI

    def __post_init__(self):
        grating_arcs = [grating_arc(self.angle, self.duty_cycle, self.n_core, self.n_clad,
                                    self.fiber_angle, self.wavelength, m, num_evaluations=self.num_evaluations)
                        for m in range(self.min_period, self.min_period + self.num_periods)]
        sector = Pattern(np.hstack((np.zeros((2, 1)), grating_arcs[0].curve.geoms[0])))
        grating = Pattern(grating_arcs, sector)
        self.waveguide = WaveguideDevice(straight(self.waveguide_l).path(self.waveguide_w),
                                         slab=self.slab, ridge=self.ridge)
        self.waveguide.halign(np.abs(self.waveguide.port['b0'].w / np.tan(np.radians(self.angle) / 2)), left=False)
        super().__init__(self.name,
                         [(grating.buffer(self.rib_grow), self.slab),
                          (grating, self.ridge), self.waveguide])
        self.port['a0'] = self.waveguide.port['a0'].copy
        self.halign(0)


@fix_dataclass_init_docs
@dataclass
class TapDC(Pattern):
    """Tap directional coupler

    Attributes:
        dc: the directional coupler that acts as a tap coupler
        grating_pad: the grating pad for the tap
    """
    dc: DC
    grating: Union[StraightGrating, FocusingGrating]

    def __post_init__(self):
        in_grating = self.grating_pad.copy.to(self.dc.port['b1'])
        out_grating = self.grating_pad.copy.to(self.dc.port['a1'])
        super().__init__(self.dc, in_grating, out_grating)
        self.port['a0'] = self.dc.port['a0']
        self.port['b0'] = self.dc.port['b0']
        self.refs.append(self.dc)
        self.wg_path = self.dc.lower_path
