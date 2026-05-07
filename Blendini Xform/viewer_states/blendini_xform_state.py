"""
Blendini Xform State
Main logic for the Blender-style G/R/S transforms.
"""


import math
import hou
import time
import ast
import operator as op



if hou.applicationVersion()[0] >= 21:
    from PySide6 import QtWidgets, QtGui, QtCore
    from PySide6.QtCore import QTimer
else:
    from PySide2 import QtWidgets, QtGui, QtCore
    from PySide2.QtCore import QTimer



# supported operators for input expressions
operators = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
             ast.Div: op.truediv, ast.Pow: op.pow, ast.BitXor: op.xor,
             ast.USub: op.neg}


class State(object):

    TRANSLATE = 'Translate'
    ROTATE = 'Rotate'
    SCALE = 'Scale'

    MODE_INDEX = {
        TRANSLATE: 0,
        ROTATE: 1,
        SCALE: 2
    }

    LOCAL = 'Local'
    WORLD = 'Global'
    VIEW = 'View Plane'

    PIVOT_OBJECT = 'Object'
    PIVOT_CURSOR = '3D Cursor'

    X_AXIS = 'X'
    Y_AXIS = 'Y'
    Z_AXIS = 'Z'

    X_VECTOR = hou.Vector3(1, 0, 0)
    Y_VECTOR = hou.Vector3(0, 1, 0)
    Z_VECTOR = hou.Vector3(0, 0, 1)

    AXIS_TO_VECTOR = {
        X_AXIS: X_VECTOR,
        Y_AXIS: Y_VECTOR,
        Z_AXIS: Z_VECTOR
    }

    FACTOR = 1.0
    SLOW_FACTOR = 0.1

    TRANSLATE_SNAPPING = 1
    ROTATE_SNAPPING = 5
    SCALE_SNAPPING = 0.1


    HUD_TEMPLATE = {
        "title": "Blendini Xform",# "desc": "by Yonatan",
        "icon": "SOP_xform",
        "rows": [
            {"type": "divider"},
            {"id": "mode", "label": "Mode                             ", "key": "G/R/S"},
            {"id": "mode_g", "type": "choicegraph", "count": 3},
            {"type": "divider"},
            {"id": "axis", "label": "Axis", "key": "X/Y/Z"},
            {"type": "divider"},
            {"id": "pivot", "label": "Pivot", "key": "C"},
            {"type": "divider"},
            {"label": "Apply Transform", "key": "LMB"},
            {"label": "Cancel Transform", "key": "RMB"},
            {"type": "divider"},
            {"id": "precision", "label": "Precision Mode", "key": "Shift"},
            {"id": "snapping", "label": "Snapping Mode", "key": "Ctrl"},
            #{"label": "Clear transform", "key": "LMB LMB"},
            {"type": "divider"},
            {"label": "Trackball", "key": "R R"},
            {"type": "divider"},
            {"id": "pivot_menu", "label": "Pivot Menu", "key": "Shift+c"},
            {"id": "pivot_place", "label": "Set 3D Cursor", "key": "Shift+RMB"},
        ]
    }

    def __init__(self, state_name, scene_viewer):
        self.state_name = state_name
        self.scene_viewer = scene_viewer

        hou.session.blendini_xform_state = self

        # Important parm references
        self.node = None
        self.position_parm = None
        self.rotation_parm = None
        self.scale_parm = None
        self.pivottranslate = None
        self.pivotrotate = None
        self.scale_components = 3

        # Default transform modes
        self.mode = State.TRANSLATE
        self.space = State.VIEW
        self.pivot_mode = State.PIVOT_OBJECT
        self.trackball = False
        self.factor = State.FACTOR
        self.snapping = False
        self.is_transforming = False
        self.rotate_enabled = False
        self.scale_enabled = False
        self.uses_direction = False

        # 3D curosr and pivots
        self.geo_center = hou.Vector3(0, 0, 0)
        self.geo_center_screen = hou.Vector3(0, 0, 0)
        self.cursor_pos = hou.Vector3(0,0,0)
        self.cursor_pos_screen = hou.Vector3(0,0,0)
        self.pivot_pos = hou.Vector3(0,0,0)
        self.pivot_pos_screen = hou.Vector3(0,0,0)

        # AXIS(s)
        self.axis = []
        self.axis_vector = hou.Vector3(0, 0, 0)
        self.view_dir = hou.Vector3(0, 0, 0)
        self.intersection_normal = hou.Vector3(0, 0, 0)
        self.intersection_center = hou.Vector3(0, 0, 0)

        # Store transform matrix
        self.transform = hou.Matrix4()
        self.transform_no_scale = hou.Matrix4() # For use when scale has 0
        self.view_transform = hou.Matrix4()
        self.snapped_transform = hou.Matrix4()
        
        # Starting position on pressed
        self.first_pos = hou.Vector3(0, 0, 0)
        self.prev_pos = hou.Vector3(0, 0, 0)
        self.mouse = hou.Vector3(0, 0, 0)
        self.wrap_offset = hou.Vector3(0, 0, 0)
        self.screen_size = (0, 0, 0, 0)
        self.screen_pos = hou.Vector3(0, 0, 0)

        # Store last key pressed for MODES and AXIS
        self.last_key_axis = 0
        self.last_key_mode = 0
        self.last_key_mode_time = 0
        self.is_shift_last = False
        self.is_pressed = False
        self.last_pressed = 0

        # Keys override hack
        self.keys_override = [
            {'symbol': 'h.pane.gview.state.new_move', 'key': 't', 'override': False},
            {'symbol': 'h.pane.gview.state.volatile_select', 'key': 's', 'override': False},
            {'symbol': 'h.pane.gview.state.new_rotate', 'key': 'r', 'override': False},
            {'symbol': 'h.pane.gview.delete', 'key': 'del', 'override': False},
            {'symbol': 'h.pane.gview.state.volatile_floor', 'key': '/', 'override': False},
            {'symbol': 'h.pane.gview.increase_subd', 'key': 'shift+=', 'override': False},
            {'symbol': 'h.pane.gview.increase_handle_size', 'key': 'shift+8', 'override': False},
            {'symbol': 'h.pane.gview.enable_snapping', 'key': 'x', 'override': False},
            {'symbol': 'h.pane.gview.decrease_handle_size', 'key': 'PadSlash', 'override': False},
            
        ]

        # Drawables
        self.line_axis = []
        self.custom_cursor = None
        self.custom_line = None
        self.object_dot = None
        self.cursor_dot = None
        self.helper_grid = None
        self.show_grid = False
        self.text_drawable = None
        self.is_dark = True
        self.guid_drawable = None

        # Info display
        self.deg = 0
        self.info = ""
        self.info_sub = ""
        self.amount_input = ""
        self.input_eval_amount = 0
        self.is_input_mode = False
        self.is_input_invalid = False

        # Extra variables for rotation/scale modes
        self.angle_sum = self.angle_sum_snapped = 0
        self.trackball_axis = hou.Vector2(0, 0)
        self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
        self.scale_sum = self.scale_sum_snapped = 1

        # Extra variables for rotation winding tracking
        self.original_rotation = hou.Vector3(0, 0, 0)
        self.baseline_decomposed = hou.Vector3(0, 0, 0)

        # For add node gadgets
        self.selected_point = 0
        self.point_gadget = None




    def _log(self, *args):
        pass
        # print("[BT State]", *args)


    def _setup_state(self, node):
        """Common setup logic for both onEnter and onGenerate"""
        self.node = node
        self._log("Setting up state for node:", node.path() if node else "None")

        for key in self.keys_override:
            symbol = key['symbol']
            hotkey = key['key']
            
            # Get current assignments for this symbol
            current = hou.hotkeys.assignments(symbol)
            if current:
                # Try clearing
                hou.hotkeys.clearAssignments(symbol)
                key['override'] = True
                after = hou.hotkeys.assignments(symbol)

        # Setup HUD info
        self.scene_viewer.hudInfo(template=State.HUD_TEMPLATE)

        self.view_transform = self.scene_viewer.curViewport().viewTransform()
        self.active_viewport = self.scene_viewer.curViewport()
        self.screen_size = self.active_viewport.size()
        screen_pos = self.active_viewport.geometry()
        self.screen_pos = hou.Vector3(screen_pos[0], screen_pos[1], 0)
        

        self.text_drawable = hou.TextDrawable(self.scene_viewer, 'text_drawable_name')


        # Position Parm
        self.position_parm = self.node.parmTuple("t") or self.node.parmTuple('origin') or self.node.parmTuple('goal') or self.node.parmTuple('offsetv')


        # Rotation Parm
        self.rotation_parm = self.node.parmTuple("r")
        self.rotate_enabled = True

        if not self.rotation_parm or len(self.rotation_parm) < 3:
            if self.node.parmTuple('dir'):
                self.rotation_parm = self.node.parmTuple('dir')
                self.uses_direction = True
            else:
                self.rotate_enabled = False
            


        # Scale Parm!

        parm = self.node.parmTuple('s') or self.node.parmTuple("size") or self.node.parmTuple('rad')
        if parm and len(parm) == 3:
            self.scale_parm = parm
            self.scale_components = 3
            self.scale_enabled = True

        elif (self.node.parm("sx") and self.node.parm("sy")): # Font and trace sops
            self.scale_parm = (self.node.parm("sx"), self.node.parm("sy"), None)
            self.scale_components = 2
            self.scale_enabled = True

        elif (self.node.parm("sizex") and self.node.parm("sizey")): # Grid sop
            orient = self.node.parm("orient") or self.node.parm("plane")
            orient_val = orient.eval() if orient else 2

            if orient_val == 0:
                self.scale_parm = (self.node.parm("sizex"), self.node.parm("sizey"), None)
            elif orient_val == 1:
                self.scale_parm = (None, self.node.parm("sizey"), self.node.parm("sizex"))
            else:
                self.scale_parm = (self.node.parm("sizex"), None, self.node.parm("sizey"))

            self.scale_components = 2
            self.scale_enabled = True

        elif (self.node.parm("radx") and self.node.parm("rady")): # Circle sop
            orient = self.node.parm("orient")
            orient_val = orient.eval() if orient else 2

            if orient_val == 0:
                self.scale_parm = (self.node.parm("radx"), self.node.parm("rady"), None)
            elif orient_val == 1:
                self.scale_parm = (None, self.node.parm("rady"), self.node.parm("radx"))
            else:
                self.scale_parm = (self.node.parm("radx"), None, self.node.parm("rady"))

            self.scale_components = 2
            self.scale_enabled = True

        elif self.node.parm("scale"):
            self.scale_parm = self.node.parm("scale")
            self.scale_components = 1
            self.scale_enabled = True

        elif self.node.parm("uniformscale"):
            self.scale_parm = self.node.parm("uniformscale")
            self.scale_components = 1
            self.scale_enabled = True

        elif self.node.parm("dist"):
            self.scale_parm = self.node.parm("dist")
            self.scale_components = 1
            self.scale_enabled = True

        elif self.node.parm("radius"):
            self.scale_parm = self.node.parm("radius")
            self.scale_components = 1
            self.scale_enabled = True

        elif self.node.parm("elementsize"):
            self.scale_parm = self.node.parm("elementsize")
            self.scale_components = 1
            self.scale_enabled = True

        if self.node.type().name() == 'tube':
            radscale = self.node.parm("radscale")
            height = self.node.parm("height")

            orient = self.node.parm("orient")
            orient_val = orient.eval() if orient else 2

            if orient_val == 0:
                self.scale_parm = (height, radscale, radscale)
            elif orient_val == 1:
                self.scale_parm = (radscale, height, radscale)
            else:
                self.scale_parm = (radscale, radscale, height)

            self.scale_components = 2
            self.scale_enabled = True

        if self.node.type().name() == 'popaxisforce':
            radius = self.node.parm('r')
            height = self.node.parm('height')

            self.scale_parm = (radius, height, radius)

            self.scale_components = 2
            self.scale_enabled = True


        if self.node.type().name() in ['mirror','clip::2.0']:
            dirtype = self.node.parm('dirtype').eval()
            if dirtype == 0:
                self.position_parm = self.node.parmTuple("origin")
                self.rotation_parm = self.node.parmTuple("dir")
                self.uses_direction = True
            elif dirtype == 1:
                self.position_parm = self.node.parmTuple("t")
                self.rotation_parm = self.node.parmTuple("r")
                self.uses_direction = False
            
            self.scale_parm = None
            self.scale_components = 0
            self.scale_enabled = False


        if self.node.type().name() == 'polyextrude::2.0':
            self.position_parm = self.node.parmTuple("translate")
            self.rotation_parm = self.node.parmTuple("rotate")
            self.scale_parm = self.node.parmTuple("scale")
            self.scale_components = 3
            self.scale_enabled = True



        # Pivot Parm
        self.pivottranslate = self.node.parmTuple('p')
        #self.pivotrotate = self.node.parmTuple('pr')
        self.pivotrotate = None
        

        # Special paramters to make the curve node type work
        if self.node.type().name() == 'curve::2.0':
            self.position_parm = self.node.parmTuple("translate")
            self.rotation_parm = self.node.parmTuple("rotate")
            self.scale_parm = self.node.parmTuple("scale")

            self.pivottranslate = self.node.parmTuple("pivottranslate")
            self.pivotrotate = self.node.parmTuple("pivotrotate")
            self.activepoints = self.node.parm("activepoints")

            self.rotate_enabled = True
            self.scale_enabled = True


        elif self.node.type().name() == 'add':
            pts = self.node.parm('points').eval()
            self.selected_point = 0
            if pts > 0:
                self.position_parm = self.node.parmTuple('pt0')

            # Create gadget drawable

            self.point_gadget = self.state_gadgets["point_gadget"]  # auto-created by Houdini
            self._update_gadget_geo()
            self.point_gadget.setParams({
                "draw_color": [0.5, 0.5, 0.5, 1],
                "locate_color": [1, 0.5, 0, 1],
                "pick_color": [1, 1, 0, 1],
                "radius": 5.0
            })
            self.point_gadget.show(True)


        if not self.position_parm:
            # No t parm, so exit!
            if hou.session.blendini_xform_from_handle:
                self.scene_viewer.enterCurrentNodeState()
            else:
                self.scene_viewer.enterViewState()
            return True



        # Store the current position
        self.updateTransform()


        # Check if mode was set by handle
        pending_mode = getattr(hou.session, "blendini_xform_mode", None)
        if pending_mode:
            if pending_mode == 'Enter':
                self.is_transforming = False
            else:
                if pending_mode == "g":
                    self.mode = State.TRANSLATE
                elif pending_mode == "r":

                    if not self.rotate_enabled:
                        self.scene_viewer.flashMessage("NETVIEW_warning_badge", "Rotate Not Available In This Node", 2.0)
                    else:
                        self.last_key_mode = 114 # r
                        self.last_key_mode_time = time.time()
                        self.mode = State.ROTATE

                elif pending_mode == "s":

                    if not self.scale_enabled:
                        self.scene_viewer.flashMessage("NETVIEW_warning_badge", "Scale Not Available In This Node", 2.0)
                    else:
                        self.mode = State.SCALE
                hou.session.blendini_xform_mode = None
                self.is_transforming = True
                self._init_rotation_tracking()
                self._hide_cursor()
                self.show()
                self.updateInfo()
                self.scene_viewer.beginStateUndo(f"Blendini Xform {self.mode}")

        else:
            self.mode = State.TRANSLATE

        
        #self.z_up = hou.getenv("BX_USE_Z_UP", default_value="0") == "1"

        self.space = State.VIEW
        #self.pivot_mode = getattr(hou.session, 'blendini_pivot_mode', State.PIVOT_OBJECT)
        self.trackball = False
        self.snapping = False
        self.initAxis([])

        self.mouse = getattr(hou.session, "blendini_xform_mouse", self.geo_center_screen)
        self.first_pos = self.prev_pos = self.mouse - self.screen_pos
        self.last_click_time = 0

        view_settings = self.scene_viewer.curViewport().settings()
        #self.is_dark = view_settings.colorScheme() in (hou.viewportColorScheme.Dark, hou.viewportColorScheme.DarkGrey)


        self.custom_line = self.createCursorLine()
        self.object_dot = self.createDot()
        self.object_dot.show(True)
        self.cursor_dot = self.createDot()
        self.cursor_dot.show(True)
        try:
            geo = self.node.geometry()
            self.guid_drawable = hou.SimpleDrawable(
                self.scene_viewer, geo,
                self.state_name + "_guide"
            )
            self.guid_drawable.setTransform(self.node.creator().worldTransform())
            self.guid_drawable.setXray(True)  # Shows through other geometry
            self.guid_drawable.setDisplayMode(hou.drawableDisplayMode.CurrentViewportMode)
            self.guid_drawable.enable(False)
            #self.guid_drawable.show(True)
        except (AttributeError, hou.OperationFailed):
            pass
        

        if self.mode == State.TRANSLATE:
            self.custom_cursor = self._create_grab_indicator_geo()
        else:
            self.custom_cursor = self.createCursor()

        if not self.is_transforming:
            self.custom_cursor.show(False)
            
        #self.updateInfo()

    
    def _update_gadget_geo(self):
        if not self.node or self.node.type().name() != 'add':
            return
            
        geo = hou.Geometry()
        num_points = self.node.parm('points').eval()
        
        self.index_map = {}  # gadget point index -> parm index
        gadget_idx = 0
        
        for i in range(num_points):
            if i == self.selected_point:
                continue
            pos = hou.Vector3(self.node.parmTuple(f"pt{i}").eval())
            geo.createPoint().setPosition(pos)
            self.index_map[gadget_idx] = i
            gadget_idx += 1
        
        self.point_gadget.setGeometry(geo)


    def updateHudInfo(self):
        if self.trackball:
            axis_label = "Trackball"
        elif len(self.axis) == 0:
            axis_label = "View Plane"
        else:
            if len(self.axis) == 1:
                axis_label = f"{self.axis[0]}"
            else:
                axis_label = f"{self.axis[0]} and {self.axis[1]}"
            
            if self.space == State.LOCAL:
                axis_label = f"{axis_label}  (Local)"

        updates = {
            "mode": self.mode,
            "mode_g": State.MODE_INDEX[self.mode],
            "axis": axis_label,
            "pivot": self.pivot_mode,
            "precision": "On" if self.factor == State.SLOW_FACTOR and self.is_transforming else "",
            "snapping": "On" if self.snapping and self.is_transforming else "",
        }
        self.scene_viewer.hudInfo(values=updates)

    def updateInfo(self):
        self.info = f"X: {self.mouse[0]:.2f}, Y: {self.mouse[1]:.2f}"

        # Main info
        if self.mode == State.ROTATE:
            self.info = f"Rotation: {self.angle_sum_snapped:.2f}°"
        elif self.mode == State.SCALE:
            self.info = f"Scale: {self.scale_sum_snapped:.2f}"
        elif self.mode == State.TRANSLATE:
            amount = self.position_sum_snapped
            amounts = []
            for index in range(3):
                if self.axis_vector[index] == 1:
                    amounts.append(amount[index])

            if len(self.axis) == 1:
                self.info = f"D: {amounts[0]:.2f}" if amounts else "D: 0.00"
            elif len(self.axis) == 2:
                self.info = f"D{self.axis[0].lower()}: {amounts[0]:.2f}, D{self.axis[1].lower()}: {amounts[1]:.2f}" if len(amounts) >= 2 else ""
            else:
                if self.space == State.VIEW:
                    amount *= self.view_transform.extractRotationMatrix3().inverted()
                self.info = f"Dx: {amount[0]:.2f}, Dy: {amount[1]:.2f}, Dz: {amount[2]:.2f}"

        # Input mode
        if self.is_input_mode:
            if self.mode == State.ROTATE:
                self.info = f"Rotation: [{self.amount_input}]"
            elif self.mode == State.SCALE:
                self.info = f"Scale: [{self.amount_input}]"
            elif self.mode == State.TRANSLATE:
                self.info = f"D: [{self.amount_input}]"

            self.info += " = "
            if self.is_input_invalid:
                self.info += "Invalid"
            else:
                self.info += f"{self.input_eval_amount}"
                if self.mode == State.ROTATE:
                    self.info += "°"

        # Sub info
        if len(self.axis) == 1:
            self.info_sub = f"along {self.space.lower()} {self.axis[0]}"
        elif len(self.axis) == 2:
            self.info_sub = f"along {self.space.lower()} {self.axis[0]}{self.axis[1]} plane"
        else:
            self.info_sub = ""

        if self.trackball:
            self.info_sub = f"along [{self.trackball_axis.x():.2f}, {self.trackball_axis.y():.2f}]"

        snap_mode = self.scene_viewer.snappingMode()
        if snap_mode != hou.snappingMode.Off and self.mode == State.TRANSLATE and self.space == State.VIEW:
            snapped_pos = getattr(self, 'snapped_pos', (0,0,0))
            snapped_pos = [round(v, 5) if abs(v) > 1e-6 else 0 for v in snapped_pos]
            self.info = f"{str(snap_mode).split('.')[1]} Snapping to {snapped_pos}"
            self.info_sub = ""

        self.scene_viewer.curViewport().draw()

    def show(self):
        for line in self.line_axis:
            line.show(True)
        if self.custom_cursor:
            self.custom_cursor.show(True)
        if self.custom_line and self.is_pressed:
            self.custom_line.show(True)
        if self.text_drawable:
            self.text_drawable.show(True)
        if self.helper_grid and self.show_grid:
            self.helper_grid.show(True)
        if self.object_dot:
            self.object_dot.show(True)
        if self.cursor_dot:
            self.cursor_dot.show(True)
        if self.guid_drawable:
            self.guid_drawable.show(True)

    def hide(self):
        for line in self.line_axis:
            line.show(False)
        if self.custom_cursor:
            self.custom_cursor.show(False)
        if self.custom_line:
            self.custom_line.show(False)
        if self.text_drawable:
            self.text_drawable.show(False)
        if self.helper_grid:
            self.helper_grid.show(False)
        if self.object_dot:
            self.object_dot.show(True)
        if self.cursor_dot:
            self.cursor_dot.show(True)
        if self.guid_drawable:
            self.guid_drawable.show(True)

    def snap(self, value, step):
        return round(value / step / self.factor) * step * self.factor

    def updateTransform(self, t=None, r=None, s=None):

        if t is None:
            t = self.position_parm.evalAsFloats()
        if r is None:
            #r = self.rotation_parm.evalAsFloats() if self.rotation_parm else (0,0,0)
            r = self._get_rotation() if self.rotation_parm else (0,0,0)
        if s is None:
            #s = self.scale_parm.evalAsFloats() if self.scale_enabled else (1, 1, 1)
            s = self._get_scale() if self.scale_enabled else (1, 1, 1)

        if self.pivottranslate:
            t = hou.Vector3(t) + hou.Vector3(self.pivottranslate.evalAsFloats())

        # if self.pivotrotate:
        #     r = hou.Vector3(r) + hou.Vector3(self.pivotrotate.evalAsFloats())


        values = {
            'translate': t,
            'rotate': r,
            'scale': s
        }
        self.transform = hou.hmath.buildTransform(values)
        self.snapped_transform = self.transform # For snapping mode
        self.transform_no_scale = hou.hmath.buildTransform({
            'translate': t,
            'rotate': r,
            'scale': (1,1,1)
        }) # For when scale is 0, this will keep the rotation data

    def revert_transform(self):

        t = self.transform.extractTranslates()

        if self.pivottranslate:
            t = t - hou.Vector3(self.pivottranslate.evalAsFloats())

        self.position_parm.set(t)

        if self.rotation_parm:
            self._set_rotation(self.original_rotation)

            # if self.pivotrotate:
            #     rotates = hou.Vector3(rotates) - hou.Vector3(self.pivotrotate.evalAsFloats())

        if self.scale_parm:
            self._set_scale(self.transform.extractScales(transform_order='srt'))

        self.view_transform = self.scene_viewer.curViewport().viewTransform()
        self.updateTransform()

        self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
        self.angle_sum = self.angle_sum_snapped = 0
        self.scale_sum = self.scale_sum_snapped = 1
        self.trackball_axis = hou.Vector2(0, 0)

        self.space = State.VIEW
        self.trackball = False
        self.factor = State.FACTOR
        self.snapping = False
        
        self.is_transforming = False
        self.is_input_mode = False
        self.amount_input = ""
        self.wrap_offset = hou.Vector3(0,0,0)

    
        

        self.initAxis([])
        self.hide()
        self.updateInfo()
        try:
            self.scene_viewer.endStateUndo()
            hou.undos.performUndo()
        except:
            pass

        self._restore_cursor()


        if self.node.type().name() == 'curve::2.0':
            hou.session.blendini_update_curve = True
            hou.session.blendini_t = self.position_parm.eval()
            hou.session.blendini_r = self._get_rotation()
            hou.session.blendini_s = self._get_scale()

            hou.session.blendini_pivott = self.pivottranslate.eval()
            hou.session.blendini_pivotr = self.pivotrotate.eval()

            hou.session.blendini_activepoints = self.activepoints.eval()

            self.scene_viewer.enterCurrentNodeState()

    def createLine(self, direction, color, center=None):

        geo = hou.Geometry()
        verb = hou.sopNodeTypeCategory().nodeVerb("line")
        
        if center is None:
            center = self.intersection_center
        
        viewport = self.scene_viewer.curViewport().viewTransform().inverted().extractTranslates()
        if viewport:
            cam_pos = self.view_transform.extractTranslates()
            dist = (hou.Vector3(center) - cam_pos).length()
            length = dist * 100
        else:
            length = 1000

        
        if self.space == State.LOCAL:
            rot_matrix = self.transform_no_scale.extractRotationMatrix3()
            if self.pivotrotate:
                pivot_rot = hou.hmath.buildRotate(self.pivotrotate.evalAsFloats()).extractRotationMatrix3()
                rot_matrix = rot_matrix * pivot_rot
            direction *= rot_matrix
        
        verb.setParms({
            "origin": center + (direction * -length / 2),
            "dir": direction,
            "dist": length
        })
        verb.execute(geo, [])
        
        color_attrib = geo.addAttrib(hou.attribType.Point, "Cd", (1.0, 1.0, 1.0))
        alpha_attrib = geo.addAttrib(hou.attribType.Point, "Alpha", 1.0)
        for point in geo.points():
            point.setAttribValue(color_attrib, color[:3])
            point.setAttribValue(alpha_attrib, 0.7)
        
        return geo

    def createLine2(self, direction, color, center=None):
        LARGE_SIZE = 1000

        geo = hou.Geometry()
        verb = hou.sopNodeTypeCategory().nodeVerb("line")

        if center is None:
            center = self.pivot_pos
            #center = hou.Vector3(self.position_parm.evalAsFloats())

        if self.space == State.LOCAL:
            rot_matrix = self.transform_no_scale.extractRotationMatrix3()

            if self.pivotrotate:
                pivot_rot = hou.hmath.buildRotate(self.pivotrotate.evalAsFloats()).extractRotationMatrix3()
                rot_matrix = rot_matrix * pivot_rot
                
            direction *= rot_matrix

        vector = center + (direction * -LARGE_SIZE / 2)

        verb.setParms({
            "origin": vector,
            "dir": direction,
            "dist": LARGE_SIZE
        })
        verb.execute(geo, [])

        color_attrib = geo.addAttrib(hou.attribType.Point, "Cd", (1.0, 1.0, 1.0))
        alpha_attrib = geo.addAttrib(hou.attribType.Point, "Alpha", 1.0)

        for point in geo.points():
            point.setAttribValue(color_attrib, (color[0], color[1], color[2]))
            point.setAttribValue(alpha_attrib, 0.7)

        return geo

    def initAxis(self, axis):
        # Clear out old line drawables
        self.line_axis = []
        self.axis = axis
        self.updateHudInfo()

        # Calculate the vector pointing from the camera
        cam_pos = self.view_transform.extractTranslates()
        object_pos = self.transform.extractTranslates()


        # Calculate geo_center, 3d cursor and final pivot pos

        parent_transform = hou.Matrix4(1)
        if self.node.parent() and hasattr(self.node.parent(), 'worldTransform'):
            parent_transform = self.node.parent().worldTransform()

        transform = self.transform

        snap_mode = self.scene_viewer.snappingMode()
        if snap_mode != hou.snappingMode.Off and self.space == State.VIEW:
            transform = self.snapped_transform


        self.geo_center = transform.extractTranslates()
        self.geo_center_screen = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(self.geo_center * parent_transform), 0)


        if not hasattr(hou.session, 'blendini_xform_cursor'):
            hou.session.blendini_xform_cursor = hou.Vector3(0,0,0)


        self.cursor_pos = hou.session.blendini_xform_cursor
        self.cursor_pos_screen = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(self.cursor_pos * parent_transform), 0)

        if self.pivot_mode == State.PIVOT_OBJECT:
            self.pivot_pos = self.geo_center
            self.pivot_pos_screen = self.geo_center_screen
        else:
            self.pivot_pos = self.cursor_pos
            self.pivot_pos_screen = self.cursor_pos_screen


            
        self.view_dir = (cam_pos - self.pivot_pos).normalized()


        self.intersection_center = self.pivot_pos


        if self.scene_viewer.curViewport().settings().camera() and self.node.type().name() in ['cam', 'camera']:
            self.view_dir = hou.Vector3(0, 0, -1) * self.view_transform.extractRotationMatrix3()
            self.view_dir = self.view_dir.normalized()
            self.pivot_pos_screen = hou.Vector3(self.screen_size[2]/2, self.screen_size[3]/2, 0)

            self.intersection_center = self.view_transform.extractTranslates() + self.view_dir*20
        

        


        if self.is_input_mode:
            self.do_transform()

        if len(self.axis) == 0:

            if self.mode == State.ROTATE:

                viewport_type = self.scene_viewer.curViewport().type()
                if viewport_type == hou.geometryViewportType.Top:
                    self.axis_vector = State.Y_VECTOR
                elif viewport_type == hou.geometryViewportType.Bottom:
                    self.axis_vector = -State.Y_VECTOR
                elif viewport_type == hou.geometryViewportType.Front:
                    self.axis_vector = State.Z_VECTOR
                elif viewport_type == hou.geometryViewportType.Back:
                    self.axis_vector = -State.Z_VECTOR
                elif viewport_type == hou.geometryViewportType.Right:
                    self.axis_vector = State.X_VECTOR
                elif viewport_type == hou.geometryViewportType.Left:
                    self.axis_vector = -State.X_VECTOR
                else:
                    # if self.scene_viewer.curViewport().settings().isOrtho():
                    #     self.axis_vector = hou.Vector3(0, 0, -1) * self.view_transform.extractRotationMatrix3()
                    #     self.axis_vector = self.axis_vector.normalized()
                    self.axis_vector = -self.view_dir
                    




            else:
                self.axis_vector = hou.Vector3(1, 1, 1)
                self.view_dir = hou.Vector3(0, 0, 1) * self.view_transform.inverted().transposed()
                self.intersection_normal = self.view_dir
        else:
            self.axis_vector = hou.Vector3(0, 0, 0)
            current_vector = hou.Vector3(0, 0, 0)
            for i in self.axis:
                if i == State.X_AXIS:
                    current_vector = State.X_VECTOR
                    color = (1.0, 0.0, 0.0, 1.0)
                elif i == State.Y_AXIS:
                    current_vector = State.Y_VECTOR
                    color = (0.0, 1.0, 0.0, 1.0)
                elif i == State.Z_AXIS:
                    current_vector = State.Z_VECTOR
                    color = (0.0, 0.0, 1.0, 1.0)

                self.axis_vector += current_vector

                line_geo = self.createLine(current_vector, color)
                new_line = hou.GeometryDrawable(
                    self.scene_viewer,
                    hou.drawableGeometryType.Line,
                    "line",
                    params={
                        "style": hou.drawableGeometryLineStyle.Dot1,
                        "line_width": 2.0,
                        "fade_factor": 0.3,
                    }
                )
                new_line.setGeometry(line_geo)
                new_line.show(True)
                self.line_axis.append(new_line)

            # ADD HERE - before intersection calculations
            if self.space == State.WORLD and self.pivotrotate:
                pivot_rot = hou.hmath.buildRotate(self.pivotrotate.evalAsFloats()).extractRotationMatrix3()
                self.axis_vector *= pivot_rot


            # Two axis mode
            if len(self.axis) == 2:
                self.intersection_normal = hou.Vector3(1, 1, 1) - self.axis_vector
                self.info_sub = f"along {self.space.lower()}"

            # Single axis mode
            elif len(self.axis) == 1:
                view_dir = self.view_dir
                if self.space == State.LOCAL:
                    view_dir = view_dir * self.transform_no_scale.extractRotationMatrix3().inverted()

                cross = view_dir.cross(self.axis_vector)
                self.intersection_normal = self.axis_vector.cross(cross).normalized()


            # Rotate intersection normal to Local space
            if self.space == State.LOCAL:
                self.intersection_normal *= self.transform_no_scale.extractRotationMatrix3()
            elif self.space == State.WORLD and self.pivotrotate:
                self.intersection_normal *= pivot_rot

            

            # Fix for camera view local z axis (to move backward and forward)
            if self.scene_viewer.curViewport().settings().camera() and self.node.type().name() in ['cam', 'camera']:
                if len(self.axis) == 1 and self.axis[0] == State.Z_AXIS and self.space == State.LOCAL:
                    view_forward = hou.Vector3(0, 0, 1) * self.view_transform.extractRotationMatrix3()
                    self.intersection_normal = (self.intersection_normal + view_forward * 0.8).normalized()
                    self.intersection_normal = hou.Vector3(0,1,0)



        self.helper_grid = self.createGrid()
        self.show()

    def createCursorLine(self):
        poly_geo = hou.Geometry()
        poly_line = poly_geo.createPolygon()

        point1 = poly_geo.createPoint()
        point2 = poly_geo.createPoint()

        if not self.trackball:
            #posInScreen = self.scene_viewer.curViewport().mapToScreen(hou.Vector3(self.position_parm.evalAsFloats()))
            #posInScreen = self.scene_viewer.curViewport().mapToScreen(self.cursor_pos)
            posInScreen = self.pivot_pos_screen
        else:
            posInScreen = self.first_pos


        mouse_pos = self.mouse + self.wrap_offset - self.screen_pos

        point1.setPosition((posInScreen[0], posInScreen[1], 1))
        point2.setPosition((mouse_pos[0], mouse_pos[1], 1))

        poly_line.addVertex(point1)
        poly_line.addVertex(point2)

        new_line = hou.GeometryDrawable(
            self.scene_viewer,
            hou.drawableGeometryType.Line,
            "line"
        )
        new_line.setGeometry(poly_geo)
        new_line.show(True)
        return new_line

    def createCursor(self):
        geo = hou.Geometry()
        arrow_main = geo.createPolygon()

        point1 = geo.createPoint()
        point2 = geo.createPoint()
        point3 = geo.createPoint()
        point4 = geo.createPoint()

        point1.setPosition(hou.Vector3(10, 0, 0))
        point2.setPosition(hou.Vector3(25, 0, 0))
        point3.setPosition(hou.Vector3(18, 7, 0))
        point4.setPosition(hou.Vector3(18, -7, 0))

        arrow_main.addVertex(point1)
        arrow_main.addVertex(point2)
        arrow_main.addVertex(point3)
        arrow_main.addVertex(point2)
        arrow_main.addVertex(point4)
        arrow_main.setIsClosed(False)

        arrow_main2 = geo.createPolygon()

        point1 = geo.createPoint()
        point2 = geo.createPoint()
        point3 = geo.createPoint()
        point4 = geo.createPoint()

        point1.setPosition(hou.Vector3(-10, 0, 0))
        point2.setPosition(hou.Vector3(-25, 0, 0))
        point3.setPosition(hou.Vector3(-18, 7, 0))
        point4.setPosition(hou.Vector3(-18, -7, 0))

        arrow_main2.addVertex(point1)
        arrow_main2.addVertex(point2)
        arrow_main2.addVertex(point3)
        arrow_main2.addVertex(point2)
        arrow_main2.addVertex(point4)
        arrow_main2.setIsClosed(False)

        arrow = hou.GeometryDrawable(
            self.scene_viewer,
            hou.drawableGeometryType.Line,
            "arrow"
        )
        arrow.setGeometry(geo)
        arrow.show(True)
        return arrow
    
    def _create_grab_indicator_geo(self, radius=10, size=5.0):
        """Create 4 chevron arrows pointing outward from a circle."""
        geo = hou.Geometry()
        
        radius_net = radius
        arm = size
        
        for i in range(4):
            angle = math.radians(i * 90)
            
            # Chevron base position on circle
            cx = radius_net * math.cos(angle)
            cy = radius_net * math.sin(angle)
            
            # Tip points outward (radial direction)
            tip_x = cx + arm * math.cos(angle)
            tip_y = cy + arm * math.sin(angle)
            
            # Arms perpendicular to radial
            perp = angle + math.pi / 2
            end1_x = cx + arm * math.cos(perp)
            end1_y = cy + arm * math.sin(perp)
            end2_x = cx - arm * math.cos(perp)
            end2_y = cy - arm * math.sin(perp)
            
            # Create chevron polygon (two lines: end1->tip, end2->tip)
            chevron = geo.createPolygon()
            
            p_end1 = geo.createPoint()
            p_tip = geo.createPoint()
            p_end2 = geo.createPoint()
            
            p_end1.setPosition(hou.Vector3(end1_x, end1_y, 0))
            p_tip.setPosition(hou.Vector3(tip_x, tip_y, 0))
            p_end2.setPosition(hou.Vector3(end2_x, end2_y, 0))
            
            chevron.addVertex(p_end1)
            chevron.addVertex(p_tip)
            chevron.addVertex(p_end2)
            chevron.setIsClosed(False)
        
        drawable = hou.GeometryDrawable(
            self.scene_viewer,
            hou.drawableGeometryType.Line,
            "grab_indicator"
        )
        drawable.setGeometry(geo)
        drawable.show(True)
        return drawable

    def createDot(self):
        geo = hou.Geometry()
        pt = geo.createPoint()
        pt.setPosition(hou.Vector3(0, 0, 0))

        # Create point drawable
        pivot_dot = hou.GeometryDrawable(
            self.scene_viewer,
            hou.drawableGeometryType.Point,
            "pivot_dot"
        )

        pivot_dot.setGeometry(geo)

        return pivot_dot

    def createGrid(self):
        geo2 = hou.Geometry()
        verb = hou.sopNodeTypeCategory().nodeVerb("grid")
        verb.setParms({"size": (15, 30)})
        verb.execute(geo2, [])

        geo2.addAttrib(hou.attribType.Global, "xform", hou.Matrix4(1).asTuple())

        if len(self.axis) == 1:
            view_dir = self.view_dir
            if self.space == State.LOCAL:
                view_dir *= self.transform_no_scale.extractRotationMatrix3().inverted()

            up = State.Y_VECTOR
            if self.axis_vector == State.Y_VECTOR:
                up = State.Z_VECTOR

            out = up.cross(self.axis_vector)
            view_dir *= (hou.hmath.buildScale(hou.Vector3(1, 1, 1) - self.axis_vector))
            above = hou.hmath.sign(-view_dir.dot(up))
            dot = view_dir.normalized().dot(out) * above
            deg = math.degrees(math.asin(max(-1, min(1, dot))))

            matrix = hou.hmath.buildRotateZToAxis(self.axis_vector)
            matrix *= hou.hmath.buildRotateAboutAxis(self.axis_vector, deg)

            if self.space == State.LOCAL:
                try:
                    rotates = self.transform.extractRotates()
                except:
                    rotates = self.transform_no_scale.extractRotates()
                matrix = matrix * hou.hmath.buildRotate(rotates)

        elif len(self.axis) == 2:
            intersection_normal = self.intersection_normal
            if self.space == State.LOCAL:
                intersection_normal *= self.transform_no_scale.extractRotationMatrix3().inverted()

            matrix = hou.hmath.buildRotateAboutAxis(State.X_VECTOR, 90) * hou.hmath.buildRotateZToAxis(intersection_normal)
            if self.space == State.LOCAL:
                try:
                    rotates = self.transform.extractRotates()
                except:
                    rotates = self.transform_no_scale.extractRotates()
                matrix = matrix * hou.hmath.buildRotate(rotates)
        else:
            matrix = hou.hmath.buildRotateAboutAxis(State.X_VECTOR, 90)
            matrix *= hou.hmath.buildRotateAboutAxis(State.Z_VECTOR, 90)
            matrix *= hou.hmath.buildRotate(self.view_transform.extractRotates())

        #translation = hou.hmath.buildTranslate(self.transform.extractTranslates())
        translation = hou.hmath.buildTranslate(self.intersection_center)
        matrix *= translation

        geo2.setGlobalAttribValue("xform", matrix.asTuple())

        verb = hou.sopNodeTypeCategory().nodeVerb("xformbyattrib")
        verb.execute(geo2, [geo2])

        drawable = hou.GeometryDrawable(
            self.scene_viewer,
            hou.drawableGeometryType.Line,
            "grid",
            geometry=geo2
        )
        params = {"line_width": 0.5}
        drawable.setParams(params)

        return drawable

    def onGenerate(self, kwargs):
        """Called when state is activated via handle or shelf tool"""
        self._log("onGenerate")
        node = self.scene_viewer.currentNode()
        
        if node:
            self._setup_state(node)
        else:
            self._log("Warning: No node found in onGenerate")

    def onEnter(self, kwargs):
        """Called on node bound states when it starts"""
        self._log("onEnter")
        node = kwargs.get("node")
        if node:
            self._setup_state(node)

    def onInterrupt(self, kwargs):
        self._log("onInterrupt")


        
        if self.is_transforming and not (self.scene_viewer.snappingMode() != hou.snappingMode.Off and self.mode == State.TRANSLATE and self.space == State.VIEW):

            widget = self.scene_viewer.qtWindow()
            pixel_ratio = widget.devicePixelRatio()
            
            # Get current viewport bounds
            vp_x, vp_y = self.screen_pos[0], self.screen_pos[1]
            vp_w, vp_h = self.screen_size[2], self.screen_size[3]
            
            cursor_global = QtGui.QCursor.pos()
            local_pos = widget.mapFromGlobal(cursor_global)
            x = local_pos.x() * pixel_ratio
            y = local_pos.y() * pixel_ratio
            
            # Flip y for viewport coords (viewport is bottom-left origin)
            widget_height = widget.height() * pixel_ratio
            y_vp = widget_height - y
            
            safe_margin = 15 * pixel_ratio
            offset_x = 0
            offset_y = 0
            wrap = False
            
            if x <= vp_x:
                x = vp_x + vp_w - safe_margin
                offset_x = -(vp_w - safe_margin)
                wrap = True
            elif x >= vp_x + vp_w:
                x = vp_x + safe_margin
                offset_x = vp_w - safe_margin
                wrap = True
            
            if y_vp <= vp_y:
                y_vp = vp_y + vp_h - safe_margin
                offset_y = -(vp_h - safe_margin)
                wrap = True
            elif y_vp >= vp_y + vp_h:
                y_vp = vp_y + safe_margin
                offset_y = vp_h - safe_margin
                wrap = True
            
            if wrap:
                self.wrap_offset += hou.Vector3(offset_x, offset_y, 0)
                # Convert back to widget coords
                y = widget_height - y_vp
                new_local = QtCore.QPoint(int(x / pixel_ratio), int(y / pixel_ratio))
                QtGui.QCursor.setPos(widget.mapToGlobal(new_local))
                self.scene_viewer.curViewport().draw()
                self._log("Wraped!")

            else:
                self._restore_cursor()

        
        self.scene_viewer.curViewport().draw()

    def onResume(self, kwargs):
        self._log("onResume")

        self.node = self.scene_viewer.currentNode()

        if not self.is_transforming:
            self.active_viewport = self.scene_viewer.curViewport()
            self.screen_size = self.active_viewport.size()
            screen_pos = self.active_viewport.geometry()
            self.screen_pos = hou.Vector3(screen_pos[0], screen_pos[1], 0)
        else:
            self._hide_cursor()
            

        self.view_transform = self.scene_viewer.curViewport().viewTransform()
        self.initAxis(self.axis)
        #self.custom_line = self.createCursorLine()
        #self.updateTransform()

        #view_settings = self.scene_viewer.curViewport().settings()
        #self.is_dark = view_settings.colorScheme() in (hou.viewportColorScheme.Dark, hou.viewportColorScheme.DarkGrey)

        if not self.is_transforming:
            self.hide()
        #self.updateInfo()


    def onExit(self, kwargs):
        """Called when the state terminates"""
        self._log("onExit")
        self.hide()

        for key in self.keys_override:
            if key['override']:
                hou.hotkeys.addAssignment(key['symbol'], key['key'])
                key['override'] = False


    def onDraw(self, kwargs):
        """Called for rendering a state"""
        draw_handle = kwargs["draw_handle"]


        # Only Draw the active viewport!
        if self.scene_viewer.curViewport().name() != self.active_viewport.name():
            return


        # Draw axis lines
        for line in self.line_axis:
            line.draw(draw_handle)

        if self.helper_grid:
            self.helper_grid.draw(draw_handle)

        if self.pivot_mode == State.PIVOT_CURSOR:
            cursor_color = hou.Vector4(0.949, 0.639, 0.282, 1)
        else:
            cursor_color = hou.Vector4(0.5, 0.5, 0.5, 1)

        # Draw point for geo center
        if self.object_dot and self.position_parm:
            self.geo_center = hou.Vector3(self.position_parm.evalAsFloats())

            if self.pivottranslate:
                self.geo_center += hou.Vector3(self.pivottranslate.evalAsFloats())

            if self.pivotrotate:
                pivot_rot = hou.hmath.buildRotate(self.pivotrotate.evalAsFloats()).extractRotationMatrix3()
                self.geo_center *= pivot_rot

            if self.node.type().name() == 'add':
                radius = 5
            else:
                radius = 3

            #self.geo_center = self.transform.extractTranslates()
            self.geo_center_screen = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(self.geo_center), 0)
            # Black stroke (larger)
            params_stroke = {
                "color1": hou.Vector4(0, 0, 0, 1),
                "style": hou.drawableGeometryPointStyle.SmoothCircle,
                'translate': self.geo_center,
                "fade_factor": 0.95,
                "radius": radius + 0.5
            }
            self.object_dot.draw(draw_handle, params_stroke)
            
            # Orange fill (smaller)
            params_fill = {
                "color1": hou.Vector4(0.949, 0.639, 0.282, 1),
                "style": hou.drawableGeometryPointStyle.SmoothCircle,
                'translate': self.geo_center,
                "fade_factor": 0.95,
                "radius": radius
            }
            self.object_dot.draw(draw_handle, params_fill)

        # Draw point for cursor center
        if self.cursor_dot and self.pivot_mode == State.PIVOT_CURSOR:
            params = {
                "color1": cursor_color,
                "style": hou.drawableGeometryPointStyle.Locate,
                'translate': self.cursor_pos,
                "fade_factor": 0.95,
                "radius": 6
            }
            self.cursor_dot.draw(draw_handle, params)

            params = {
                "color1": hou.Vector4(0, 0, 0, 1),
                "style": hou.drawableGeometryPointStyle.SmoothCircle,
                'translate': self.cursor_pos,
                "fade_factor": 0.95,
                "radius": 3.5
            }
            self.cursor_dot.draw(draw_handle, params)

            params = {
                "color1": cursor_color,
                "style": hou.drawableGeometryPointStyle.SmoothCircle,
                'translate': self.cursor_pos,
                "fade_factor": 0.95,
                "radius": 3
            }
            self.cursor_dot.draw(draw_handle, params)

            

        # Draw dotted line connecting cursor to pivot
        if self.custom_line and self.mode != State.TRANSLATE and not (self.trackball and not self.is_pressed):
            params = {
                "style": hou.drawableGeometryLineStyle.Dash2,
                "line_width": 1.5,
                "color1": (0, 0, 0, 0.75) if not self.is_dark else (1, 1, 1, 0.75),
                "screen_space": [0, 0, 0, 0, 0, 0]
            }
            self.custom_line.draw(draw_handle, params)

        # Draw cursor
        if self.custom_cursor:# and self.mode != State.TRANSLATE:
            rel_pos = self.mouse + self.wrap_offset - self.screen_pos - self.pivot_pos_screen
            angle = math.atan2(rel_pos[0], rel_pos[1])
            angle = math.degrees(-angle)
            if self.mode == State.SCALE:
                angle += 90
            elif self.trackball or self.mode == State.TRANSLATE:
                angle = 0

            params = {
                "style": hou.drawableGeometryLineStyle.Plain,
                "line_width": 1.5,
                "color1": (0, 0, 0, 1) if not self.is_dark else (1, 1, 1, 1),
                "fade_factor": 0.5,
                "rotate": (0, 0, angle),
                "screen_space": [-self.mouse[0] +self.screen_pos[0], -self.mouse[1] +self.screen_pos[1], 0, 0, 0, 0],
            }
            self.custom_cursor.draw(draw_handle, params)

            # Duplicate cursor for trackball mode
            if self.trackball:
                params2 = {
                    "style": hou.drawableGeometryLineStyle.Plain,
                    "line_width": 1.5,
                    "color1": (0, 0, 0, 1) if not self.is_dark else (1, 1, 1, 1),
                    "fade_factor": 0.5,
                    "rotate": (0, 0, angle + 90),
                    "screen_space": [-self.mouse[0] +self.screen_pos[0], -self.mouse[1] +self.screen_pos[1], 0, 0, 0, 0],
                }
                self.custom_cursor.draw(draw_handle, params2)

        if self.text_drawable:
            params = {
                'text': f'<font size=5>{self.info} </font><font size=4>{self.info_sub}</font>',
                'multi_line': True,
                'color1': hou.Color(1.0, 1.0, 1.0),
                'color2': hou.Color(0.5, 0.5, 0.5),
                'translate': hou.Vector3(0, 0, 0),
                'glow_width': 1.5,
                'highlight_mode': hou.drawableHighlightMode.MatteOverGlow,
                'origin': hou.drawableTextOrigin.UpperCenter,
                'translate': hou.Vector3(self.screen_size[2] / 2, self.screen_size[3], 0),
                'margins': hou.Vector2(0, -20)
            }
            self.text_drawable.draw(draw_handle, params)



        if self.node.type().name() == 'add':
            self._update_gadget_geo()
            self.point_gadget.draw(draw_handle)
                

    def _hide_cursor(self):
        cursor = hou.qt.getCursor("invisible")
        QtWidgets.QApplication.setOverrideCursor(cursor)

    def _restore_cursor(self):
        def delayed_restore():
            QTimer.singleShot(50, QtWidgets.QApplication.restoreOverrideCursor)

        while QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()
        #hou.ui.postEventCallback(delayed_restore)
        
    def onMouseEvent(self, kwargs):
        """Process mouse events"""
        ui_event = kwargs["ui_event"]
        device = ui_event.device()
        reason = ui_event.reason()


        self.mouse = hou.Vector3(device.mouseX(), device.mouseY(), 0)
        

        # Handle precision mode: Shift key
        if device.isShiftKey() and self.factor == State.FACTOR and not self.is_input_mode:
            self.factor = State.SLOW_FACTOR
            self.updateHudInfo()
        elif not device.isShiftKey() and self.factor == State.SLOW_FACTOR:
            self.factor = State.FACTOR
            self.updateHudInfo()

        # Handle snapping mode: Ctrl key
        if device.isCtrlKey() and not self.snapping and not self.is_input_mode:
            self.snapping = True
            self.updateHudInfo()
        elif not device.isCtrlKey() and self.snapping:
            self.snapping = False
            self.updateHudInfo()

        # Handle moving cursor
        if reason in (hou.uiEventReason.Picked, hou.uiEventReason.Start, hou.uiEventReason.Active):
            if not self.is_transforming: 
                if device.isRightButton() and device.isShiftKey():
                    self.ui_event = ui_event
                    self._update_cursor()
                    return True

        # Click to finalize or right click to revert
        if reason in (hou.uiEventReason.Picked, hou.uiEventReason.Changed):

            # Gadegt for add sop
            if self.state_context.gadget() == "point_gadget":
                c1 = self.state_context.component1()
                if c1 >= 0 and c1 in self.index_map:
                    actual_index = self.index_map[c1]
                    self.selected_point = actual_index
                    self.position_parm = self.node.parmTuple(f'pt{self.selected_point}')
                    self.updateTransform()
                return True

            # Double click to reset
            # 0.5 second threshold and only if the last click is before the last key. and no shift
            if time.time() - self.last_click_time < 0.5 and self.last_click_time > self.last_key_mode_time and not device.isShiftKey():
                self.reset_transform()

            self.last_click_time = time.time()

            if self.is_transforming:

                # Right click to revert
                if device.isRightButton():
                    self.revert_transform()
                    return True
                

                if self.is_input_mode:
                    self.amount_input = ""
                    self.is_input_invalid = False
                    self.is_input_mode = False

                self._finalize_transform()
                
            return True
        

        # Hover to transform
        if reason == hou.uiEventReason.Located and self.is_transforming:
            self.custom_line = self.createCursorLine()

            if self.is_input_mode:
                return True
            
            self.ui_event = ui_event
                        
            if self.mode == State.TRANSLATE:
                self._do_translate()
            elif self.mode == State.ROTATE:
                self._do_rotate()
            elif self.mode == State.SCALE:
                self._do_scale()

            
            self.updateInfo()

        return False


    def _update_cursor(self, position=None):

        if position:
            intersection = hou.Vector3(position)
        else:
            screen_position = self.mouse + self.wrap_offset - self.screen_pos

            ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(screen_position[0], screen_position[1])
            # inv_xform = self.node.creator().worldTransform().inverted()
            # local_origin = origin * inv_xform
            # ray_direction = (origin + ray_direction) * inv_xform - local_origin
            # origin = local_origin
            
            hit_node = self.scene_viewer.curViewport().queryNodeAtPixel(int(screen_position[0]), int(screen_position[1]))
            did_hit = -1
            if hit_node:
                # Intersect with geometry under cursor
                geo = hit_node.geometry()
                intersection = hou.Vector3()
                normal = hou.Vector3()
                uvw = hou.Vector3()
                did_hit = geo.intersect(origin, ray_direction, intersection, normal, uvw)

            if did_hit == -1:

                # Intersect with grid plane
                cplane = self.scene_viewer.constructionPlane()
                if cplane.isVisible():
                    center = hou.Vector3(0,0,0) * cplane.transform()
                    normal = hou.Vector3(0,0,1) * cplane.transform().inverted().transposed()
                else:
                    center = hou.Vector3(0,0,0)
                    normal = hou.Vector3(0,1,0)


                intersection = hou.hmath.intersectPlane(
                        center, normal, origin, ray_direction)
                

            

            snap_mode = self.scene_viewer.snappingMode()
            if snap_mode != hou.snappingMode.Off and self.space == State.VIEW:
                self.scene_viewer.setSnapToCurrentGeometry(True)
                self.scene_viewer.setSnapToOtherObjects(True)


                snap_dict = self.ui_event.snappingRay()


                if snap_dict["snapped"]:
                    intersection = hou.Vector3(snap_dict["snap_pos"])
            


        self.cursor_pos = intersection
        hou.session.blendini_xform_cursor = self.cursor_pos
        self.pivot_mode = State.PIVOT_CURSOR
        hou.session.blendini_pivot_mode = self.pivot_mode

        self.initAxis(self.axis)
        self.hide()
        pass


    def _finalize_transform(self):
        """Finalize current transform operation"""
        
        self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
        self.angle_sum = self.angle_sum_snapped = 0
        self.scale_sum = self.scale_sum_snapped = 1
        self.trackball_axis = hou.Vector2(0, 0)

        self.space = State.VIEW
        self.trackball = False
        self.factor = State.FACTOR
        self.snapping = False
        self.is_transforming = False
        self.wrap_offset = hou.Vector3(0,0,0)

                
        self.view_transform = self.scene_viewer.curViewport().viewTransform()
        self.updateTransform()
        self.initAxis([])
        self.hide()
        self.updateInfo()
        try:
            self.scene_viewer.endStateUndo()
        except:
            pass

        self._restore_cursor()


        if self.node.type().name() == 'curve::2.0':

            hou.session.blendini_update_curve = True
            hou.session.blendini_t = self.position_parm.eval()
            hou.session.blendini_r = self._get_rotation()
            hou.session.blendini_s = self._get_scale()

            hou.session.blendini_pivott = self.pivottranslate.eval()
            hou.session.blendini_pivotr = self.pivotrotate.eval()

            hou.session.blendini_activepoints = self.activepoints.eval()

            hou.session.blendini_is_cursor = self.pivot_mode == State.PIVOT_CURSOR
            hou.session.blendini_cursor_pos = self.pivot_pos

            self.scene_viewer.enterCurrentNodeState()

            

    def _do_translate2(self):
        """Handle translation transform"""

        position = self.mouse + self.wrap_offset - self.screen_pos
        pre_pos = self.prev_pos 

        try:

            ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(position[0], position[1])
            current_intersection = hou.hmath.intersectPlane(
                self.intersection_center, self.intersection_normal, origin, ray_direction)
            

            ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(pre_pos[0], pre_pos[1])
            prev_intersection = hou.hmath.intersectPlane(
                self.intersection_center, self.intersection_normal, origin, ray_direction)
            
            
        except TypeError as e:
            if "Input line is parallel to the plane" in str(e):
                return
            raise

        delta = current_intersection - prev_intersection

        if self.space == State.LOCAL:
            delta *= self.transform_no_scale.inverted().extractRotationMatrix3()
        elif self.space == State.VIEW:
            delta *= self.view_transform.inverted().extractRotationMatrix3()


        delta *= hou.hmath.buildScale(self.axis_vector)
        delta *= self.factor


        self.position_sum += delta
        self.position_sum_snapped = self.position_sum

        if self.snapping:
            self.position_sum_snapped = hou.Vector3(
                self.snap(self.position_sum[0], State.TRANSLATE_SNAPPING),
                self.snap(self.position_sum[1], State.TRANSLATE_SNAPPING),
                self.snap(self.position_sum[2], State.TRANSLATE_SNAPPING)
            )

        position_sum_snapped = self.position_sum_snapped
        if self.space == State.LOCAL:
            position_sum_snapped *= self.transform_no_scale.extractRotationMatrix3()
        elif self.space == State.VIEW:
            position_sum_snapped *= self.view_transform.extractRotationMatrix3()




        #snapped_transform = hou.Matrix4(1)
        snapped = False
        snap_mode = self.scene_viewer.snappingMode()
        if snap_mode != hou.snappingMode.Off and self.space == State.VIEW:

            self.scene_viewer.setSnapToCurrentGeometry(False)
            self.scene_viewer.setSnapToOtherObjects(True)


            snap_dict = self.ui_event.snappingRay()

            if snap_dict["snapped"]:
                snapped = True

                intersection = hou.Vector3(snap_dict["snap_pos"])
                self.snapped_pos = intersection
                new_t = intersection

                self.prev_pos = self.first_pos = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(intersection), 0)

                self.position_sum = self.position_sum_snapped = hou.Vector3(0,0,0)

                t = (self.position_parm.evalAsFloats())
                if self.pivottranslate:
                    t = hou.Vector3(t) + hou.Vector3(self.pivottranslate.evalAsFloats())

                values = {
                    'translate': t,
                    'rotate': (0,0,0),
                    'scale': (1,1,1)
                }

                self.snapped_transform = hou.hmath.buildTransform(values)

                
                # Will reset the intersection plane to the new snapped position
                # Will allow for continuation of dragging when released of a snapped position
                self.initAxis(self.axis) 

                
                
                
            elif snap_mode == hou.snappingMode.Grid:
                ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(position[0], position[1])
                intersection = hou.hmath.intersectPlane(
                    hou.Vector3(0,0,0), hou.Vector3(0,1,0), origin, ray_direction)
                new_t = intersection
                
            else:
                # Point, Prim or Multi snapping mode, but not snapped
                matrix = self.snapped_transform * hou.hmath.buildTranslate(position_sum_snapped)
                new_t = matrix.extractTranslates()

                
                pass
        
        else:
            matrix = self.transform * hou.hmath.buildTranslate(position_sum_snapped)
            new_t = matrix.extractTranslates()
            
            
            
        
        if self.pivottranslate:
            new_t = new_t - hou.Vector3(self.pivottranslate.evalAsFloats())
        
        self.position_parm.set(new_t)



        if not snapped:
            self.prev_pos = self.mouse + self.wrap_offset - self.screen_pos


    def _do_translate(self):
        """Handle translation transform"""
        import time
        t0 = time.perf_counter()

        position = self.mouse + self.wrap_offset - self.screen_pos
        pre_pos = self.prev_pos 

        t1 = time.perf_counter()

        try:
            ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(position[0], position[1])
            current_intersection = hou.hmath.intersectPlane(
                self.intersection_center, self.intersection_normal, origin, ray_direction)
            
            ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(pre_pos[0], pre_pos[1])
            prev_intersection = hou.hmath.intersectPlane(
                self.intersection_center, self.intersection_normal, origin, ray_direction)
            
        except TypeError as e:
            if "Input line is parallel to the plane" in str(e):
                return
            raise

        t2 = time.perf_counter()

        delta = current_intersection - prev_intersection

        if self.space == State.LOCAL:
            delta *= self.transform_no_scale.inverted().extractRotationMatrix3()
        elif self.space == State.VIEW:
            delta *= self.view_transform.inverted().extractRotationMatrix3()

        delta *= hou.hmath.buildScale(self.axis_vector)
        delta *= self.factor

        self.position_sum += delta
        self.position_sum_snapped = self.position_sum

        if self.snapping:
            self.position_sum_snapped = hou.Vector3(
                self.snap(self.position_sum[0], State.TRANSLATE_SNAPPING),
                self.snap(self.position_sum[1], State.TRANSLATE_SNAPPING),
                self.snap(self.position_sum[2], State.TRANSLATE_SNAPPING)
            )

        position_sum_snapped = self.position_sum_snapped
        if self.space == State.LOCAL:
            position_sum_snapped *= self.transform_no_scale.extractRotationMatrix3()
        elif self.space == State.VIEW:
            position_sum_snapped *= self.view_transform.extractRotationMatrix3()

        t3 = time.perf_counter()

        snapped = False
        snap_mode = self.scene_viewer.snappingMode()
        if snap_mode != hou.snappingMode.Off and self.space == State.VIEW:

            self.scene_viewer.setSnapToCurrentGeometry(False)
            self.scene_viewer.setSnapToOtherObjects(True)

            snap_dict = self.ui_event.snappingRay()

            if snap_dict["snapped"]:
                snapped = True

                intersection = hou.Vector3(snap_dict["snap_pos"])
                self.snapped_pos = intersection
                new_t = intersection

                self.prev_pos = self.first_pos = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(intersection), 0)

                self.position_sum = self.position_sum_snapped = hou.Vector3(0,0,0)

                t = (self.position_parm.evalAsFloats())
                if self.pivottranslate:
                    t = hou.Vector3(t) + hou.Vector3(self.pivottranslate.evalAsFloats())

                values = {
                    'translate': t,
                    'rotate': (0,0,0),
                    'scale': (1,1,1)
                }

                self.snapped_transform = hou.hmath.buildTransform(values)
                self.initAxis(self.axis) 

            elif snap_mode == hou.snappingMode.Grid:
                ray_direction, origin = self.scene_viewer.curViewport().mapToWorld(position[0], position[1])
                intersection = hou.hmath.intersectPlane(
                    hou.Vector3(0,0,0), hou.Vector3(0,1,0), origin, ray_direction)
                new_t = intersection
                
            else:
                matrix = self.snapped_transform * hou.hmath.buildTranslate(position_sum_snapped)
                new_t = matrix.extractTranslates()
        
        else:
            matrix = self.transform * hou.hmath.buildTranslate(position_sum_snapped)
            new_t = matrix.extractTranslates()

        t4 = time.perf_counter()
        
        if self.pivottranslate:
            new_t = new_t - hou.Vector3(self.pivottranslate.evalAsFloats())
        
        self.position_parm.set(new_t)

        # # Throttle parm updates to ~60fps
        # now = time.perf_counter()
        # if not hasattr(self, '_last_parm_update') or (now - self._last_parm_update) > 0.016:
        #     self.position_parm.set(new_t)
        #     self._last_parm_update = now
        
        # self._pending_t = new_t  # Always store for final apply

        if not snapped:
            self.prev_pos = self.mouse + self.wrap_offset - self.screen_pos

        t5 = time.perf_counter()



    def _do_rotate(self):
        """Handle rotation transform"""

        position = self.mouse + self.wrap_offset - self.screen_pos

        if not self.trackball:
            prev_direction = (self.prev_pos - self.pivot_pos_screen).normalized()
            direction = (position - self.pivot_pos_screen).normalized()

            dot = direction.dot(prev_direction)
            dot = max(-1.0, min(1.0, dot))
            cross_z = direction.cross(prev_direction).normalized().z()

            angle = math.degrees(math.acos(dot)) * cross_z

            rotated_axis_vector = self.axis_vector
            if self.space == State.LOCAL:
                rotated_axis_vector *= self.transform_no_scale.extractRotationMatrix3()

            if self.view_dir.dot(rotated_axis_vector) > 0:
                angle *= -1

            angle *= self.factor

            self.angle_sum += angle
            self.angle_sum_snapped = self.angle_sum

            if self.snapping:
                self.angle_sum_snapped = self.snap(self.angle_sum, State.ROTATE_SNAPPING)

            turn_axis = hou.hmath.buildRotateAboutAxis(rotated_axis_vector, self.angle_sum_snapped)
            current_rot_axis = rotated_axis_vector
            

        else:
            # TRACKBALL MODE
            direction = position - self.first_pos
            last_vector = self.prev_pos - self.first_pos

            amount = direction.length() - last_vector.length()
            amount *= self.factor / 2

            self.angle_sum += amount
            self.angle_sum_snapped = self.angle_sum

            direction = direction.normalized()
            if self.snapping:
                angle = math.degrees(math.atan2(direction.y(), direction.x()))
                angle = self.snap(angle, State.ROTATE_SNAPPING)
                direction = hou.Vector3(math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0)
                self.angle_sum_snapped = self.snap(self.angle_sum, State.ROTATE_SNAPPING)

            cross = hou.Vector3(-direction[1], direction[0], 0)
            self.trackball_axis = hou.Vector2(direction[0], direction[1])
            origin_2d = self.first_pos
            end_2d = origin_2d + cross

            origin_3d = self.scene_viewer.curViewport().mapToWorld(origin_2d[0], origin_2d[1])[1]
            end_3d = self.scene_viewer.curViewport().mapToWorld(end_2d[0], end_2d[1])[1]

            rotation_axis = (end_3d - origin_3d).normalized()
            turn_axis = hou.hmath.buildRotateAboutAxis(rotation_axis, self.angle_sum_snapped)
            current_rot_axis = rotation_axis



        rotates = self._compute_unwound_rotation(current_rot_axis, self.angle_sum_snapped)

        
        if self.pivotrotate:
            rotates = [rotates[i] - self.pivotrotate.evalAsFloats()[i] for i in range(3)]

        self._set_rotation(rotates)


        # Also translate for roatating around world origin
        if(self.pivot_mode == State.PIVOT_CURSOR):

            orig_pos = hou.Vector3(self.transform.extractTranslates())
            rotated_pos = (orig_pos - self.pivot_pos) * turn_axis + self.pivot_pos

            if self.pivottranslate:
                rotated_pos = rotated_pos - hou.Vector3(self.pivottranslate.evalAsFloats())

            self.position_parm.set(rotated_pos)


        self.prev_pos = self.mouse + self.wrap_offset - self.screen_pos

        #self.position_parm.set(translates)
    def _do_scale(self):
        """Handle scale transform"""
        if not self.scale_enabled:
            return
        
        position = self.mouse + self.wrap_offset - self.screen_pos

        first_dist = self.pivot_pos_screen.distanceTo(self.first_pos)
        if first_dist == 0:
            return

        last_dist = self.pivot_pos_screen.distanceTo(self.prev_pos)
        current_dist = self.pivot_pos_screen.distanceTo(position)

        delta_dist = current_dist - last_dist
        amount = delta_dist / first_dist
        amount *= self.factor

        self.scale_sum += amount
        self.scale_sum_snapped = self.scale_sum
        if self.snapping:
            self.scale_sum_snapped = self.snap(self.scale_sum, State.SCALE_SNAPPING)

        vector = (hou.Vector3(1, 1, 1) - self.axis_vector) + (
            hou.Vector3(1, 1, 1) * self.scale_sum_snapped * hou.hmath.buildScale(self.axis_vector))
        
        
        scaling_matrix = hou.hmath.buildScale(vector)
        #matrix = self.transform * scaling_matrix
        if self.space != State.LOCAL:
            matrix = self.transform * scaling_matrix
        else:
            matrix = scaling_matrix * self.transform

        scale = matrix.extractScales(transform_order='srt')
        #self.scale_parm.set(scale)
        self._set_scale(scale)




        # Also translate for scaling from world origin
        if(self.pivot_mode == State.PIVOT_CURSOR):
            original_pos = self.transform.extractTranslates()
            offset = original_pos - self.pivot_pos
            
            scale_vec = (hou.Vector3(1, 1, 1) - self.axis_vector) + (self.axis_vector * self.scale_sum_snapped)
            
            if self.space == State.LOCAL:
                # Rotate offset into local space
                rot_matrix = self.transform_no_scale.extractRotationMatrix3()
                inv_rot = rot_matrix.inverted()
                local_offset = offset * inv_rot
                
                # Scale in local space
                scaled_local = hou.Vector3(
                    local_offset[0] * scale_vec[0],
                    local_offset[1] * scale_vec[1],
                    local_offset[2] * scale_vec[2]
                )
                
                # Rotate back to world
                scaled_offset = scaled_local * rot_matrix
            else:
                scaled_offset = hou.Vector3(
                    offset[0] * scale_vec[0],
                    offset[1] * scale_vec[1],
                    offset[2] * scale_vec[2]
                )
            
            new_pos = self.pivot_pos + scaled_offset

            if self.pivottranslate:
                new_pos = new_pos - hou.Vector3(self.pivottranslate.evalAsFloats())

            self.position_parm.set(new_pos)


        self.prev_pos = self.mouse + self.wrap_offset - self.screen_pos




    def _set_scale(self, scale):
        if self.scale_components == 3:
            self.scale_parm.set(scale)
        elif self.scale_components == 2:
            for i in range(3):
                if self.scale_parm[i]:
                    self.scale_parm[i].set(scale[i])
        elif self.scale_components == 1:
            avg = (scale[0] + scale[1] + scale[2]) / 3
            self.scale_parm.set(avg)


    def _get_scale(self):
        if self.scale_components == 3:
            return hou.Vector3(self.scale_parm.eval())
        elif self.scale_components == 2:
            result = [1, 1, 1]
            for i in range(3):
                if self.scale_parm[i]:
                    result[i] = self.scale_parm[i].eval()
            return hou.Vector3(result)
        elif self.scale_components == 1:
            v = self.scale_parm.eval()
            return hou.Vector3(v, v, v)
        return hou.Vector3(1, 1, 1)
    

    def _compatible_euler(self, decomposed, reference):
        """Pick the euler representation closest to reference.
        
        For XYZ rotation order, any rotation matrix has two valid euler
        decompositions: (x, y, z) and (x+180, 180-y, z+180).
        Unwrap both to minimize distance from reference, pick closest.
        This is the same logic as Blender's mat3_normalized_to_compatible_eul.
        """
        x1, y1, z1 = decomposed[0], decomposed[1], decomposed[2]

        sol1 = [x1, y1, z1]
        sol2 = [x1 + 180.0, 180.0 - y1, z1 + 180.0]

        for i in range(3):
            while sol1[i] - reference[i] > 180.0: sol1[i] -= 360.0
            while sol1[i] - reference[i] < -180.0: sol1[i] += 360.0
            while sol2[i] - reference[i] > 180.0: sol2[i] -= 360.0
            while sol2[i] - reference[i] < -180.0: sol2[i] += 360.0

        dist1 = sum((sol1[i] - reference[i]) ** 2 for i in range(3))
        dist2 = sum((sol2[i] - reference[i]) ** 2 for i in range(3))

        return sol1 if dist1 <= dist2 else sol2

    def _compute_unwound_rotation(self, axis_vector, total_angle):
        """Compute euler rotation that preserves winding beyond +/-180.
        
        Steps from 0 to total_angle in small increments, using 
        _compatible_euler at each step to track continuity.
        Returns final euler values with winding preserved.
        """
        STEP = 90.0
        steps = max(1, int(abs(total_angle) / STEP) + 1)

        reference = list(self.baseline_decomposed)

        for s in range(1, steps + 1):
            frac = total_angle * s / steps
            turn = hou.hmath.buildRotateAboutAxis(axis_vector, frac)
            try:
                matrix = self.transform * turn
                decomposed = matrix.extractRotates()
            except:
                matrix = self.transform_no_scale * turn
                decomposed = matrix.extractRotates()

            reference = self._compatible_euler(decomposed, reference)

        # Delta from baseline in decomposed space
        delta = [reference[i] - self.baseline_decomposed[i] for i in range(3)]

        # Apply delta to the original (possibly wound) euler values
        rotates = [self.original_rotation[i] + delta[i] for i in range(3)]
        return rotates

    def _init_rotation_tracking(self):
        """Store baseline values when a rotation transform begins."""
        self.original_rotation = hou.Vector3(self._get_rotation()) if self.rotation_parm else hou.Vector3(0, 0, 0)
        try:
            self.baseline_decomposed = hou.Vector3(self.transform.extractRotates())
        except:
            self.baseline_decomposed = hou.Vector3(self.transform_no_scale.extractRotates())

    def _set_rotation(self, rotation):
        if self.uses_direction:
            rot_matrix = hou.hmath.buildRotate(rotation)
            dir_vec = hou.Vector3(0, 1, 0) * rot_matrix
            self.rotation_parm.set(dir_vec)
        else:
            self.rotation_parm.set(rotation)


    def _get_rotation(self):
        if self.uses_direction:
            dir_vec = hou.Vector3(self.rotation_parm.eval()).normalized()
            rot_matrix = hou.Vector3(0, 1, 0).matrixToRotateTo(dir_vec)
            return rot_matrix.extractRotates()
        else:
            return hou.Vector3(self.rotation_parm.eval())


    def onKeyEvent(self, kwargs):
        """Called for processing a keyboard event"""
        ui_event = kwargs["ui_event"]
        device = ui_event.device()


        key_string = device.keyString().lower()
        key_value = device.keyValue()
        is_shift = 'shift' in key_string

        self._log('key string', device.keyString())
        self._log('key value', device.keyValue())

        if 'ctrl' in key_string or 'cmd' in key_string:
            return False

        # CHANGING TRANSFORM MODES
        modes = (103, 114, 115)  # (g, r, s)

        
        if key_value == 116: # Allow t key to also change Translate mode
            key_value = 103


        if key_value in modes:
            if key_value == modes[0]:
                self.mode = State.TRANSLATE
                self.custom_cursor = self._create_grab_indicator_geo()
            elif key_value == modes[1]:

                if not self.rotate_enabled:
                    self.scene_viewer.flashMessage("NETVIEW_warning_badge", "Rotate Not Available In This Node", 2.0)
                    return True
                
                self.mode = State.ROTATE
                self.custom_cursor = self.createCursor()
            elif key_value == modes[2]:

                if not self.scale_enabled:
                    self.scene_viewer.flashMessage("NETVIEW_warning_badge", "Scale Not Available In This Node", 2.0)
                    return True
                
                self.mode = State.SCALE
                self.custom_cursor = self.createCursor()

            self.trackball = False

            # # Double G to toggle grid
            # if self.mode == State.TRANSLATE and self.last_key_mode == modes[0] and time.time() - self.last_key_mode_time < 0.5:
            #     print("Toggle grid")
            #     self.show_grid = not self.show_grid
            #     self.show()

            # Double R for trackball mode
            if self.mode == State.ROTATE and self.last_key_mode == modes[1] and time.time() - self.last_key_mode_time < 0.5:
                self.trackball = True
                self.axis = []

            self.initAxis(self.axis)
            self.custom_line = self.createCursorLine()

            self.last_key_mode = key_value
            self.last_key_mode_time = time.time()

            self.updateInfo()

            #self.updateTransform()
            if not self.is_transforming:
                # Initialize transforming
                self.is_transforming = True

                self._hide_cursor()


                self.view_transform = self.scene_viewer.curViewport().viewTransform()
                self.updateTransform()  # Grab current parm state
                self._init_rotation_tracking()

                # if self.scene_viewer.snappingMode() == hou.snappingMode.Grid and self.ui_event:
                #     snap_dict = self.ui_event.snappingRay()
                #     snap_pos = hou.Vector3(snap_dict["snap_pos"])
                #     self.first_pos = self.prev_pos = hou.Vector3(*self.scene_viewer.curViewport().mapToScreen(snap_pos), 0) - self.screen_pos
                # else:
                #     self.first_pos = self.prev_pos = self.mouse - self.screen_pos
                self.first_pos = self.prev_pos = self.mouse - self.screen_pos
                try:
                    self.scene_viewer.beginStateUndo(f"Blendini Xform {self.mode}")
                except hou.OperationFailed:
                    # Already in an undo block (e.g. from a previous error), end it and restart
                    self.scene_viewer.endStateUndo()
                    self.scene_viewer.beginStateUndo(f"Blendini Xform {self.mode}")

            else:

                t = self.transform.extractTranslates()

                if self.pivottranslate:
                    t = t - hou.Vector3(self.pivottranslate.evalAsFloats())

                self.position_parm.set(t)

                if self.rotation_parm:
                    self._set_rotation(self.original_rotation)
                if self.scale_parm:
                    #self.scale_parm.set(self.transform.extractScales(transform_order='srt'))
                    self._set_scale(self.transform.extractScales(transform_order='srt'))

                self.prev_pos = self.first_pos

                self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
                self.angle_sum = self.angle_sum_snapped = 0
                self.scale_sum = self.scale_sum_snapped = 1


                if self.mode == State.TRANSLATE:
                    self._do_translate()
                    
                elif self.mode == State.ROTATE:
                    self._do_rotate()
                    
                elif self.mode == State.SCALE:
                    self._do_scale()
                    
                self.prev_pos = self.mouse - self.screen_pos

                

                self.updateInfo()


            return True

        # CHANGING AXIS
        axes = (120, 121, 122)  # (x, y, z)
        if key_value in axes:
            if self.trackball:
                return True
            
            if not self.is_transforming:
                self.is_transforming = True
                self._hide_cursor()
                self.updateTransform()  # Grab current parm state
                self._init_rotation_tracking()
                self.view_transform = self.scene_viewer.curViewport().viewTransform()

                self.first_pos = self.prev_pos = self.mouse - self.screen_pos
                
                try:
                    self.scene_viewer.beginStateUndo(f"Blendini Xform {self.mode}")
                except hou.OperationFailed:
                    # Already in an undo block (e.g. from a previous error), end it and restart
                    self.scene_viewer.endStateUndo()
                    self.scene_viewer.beginStateUndo(f"Blendini Xform {self.mode}")

                

            # Double pressed
            if key_value == self.last_key_axis and is_shift == self.is_shift_last:
                if self.space == State.LOCAL:
                    self.space = State.VIEW
                    self.initAxis([])
                else:
                    if self.space == State.VIEW:
                        self.space = State.WORLD
                    elif self.space == State.WORLD:
                        self.space = State.LOCAL
                    self.createAxis(key_value, is_shift, axes)
            else:
                self.space = State.WORLD
                self.createAxis(key_value, is_shift, axes)

            self.last_key_axis = key_value
            self.is_shift_last = is_shift

            if self.is_transforming:

                new_t = self.transform.extractTranslates()
                if self.pivottranslate:
                    new_t = new_t - hou.Vector3(self.pivottranslate.evalAsFloats())
                self.position_parm.set(new_t)

                # Reset parms to original
                #self.position_parm.set(self.transform.extractTranslates())
                if self.rotation_parm:
                    self._set_rotation(self.original_rotation)
                if self.scale_parm:
                    #self.scale_parm.set(self.transform.extractScales(transform_order='srt'))
                    self._set_scale(self.transform.extractScales(transform_order='srt'))

                # Reinit winding tracking for new axis
                self._init_rotation_tracking()

                if self.mode == State.TRANSLATE:
                    # Recalculate - different plane
                    self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
                    self.prev_pos = self.first_pos
                    self._do_translate()
                    self.prev_pos = self.mouse - self.screen_pos
                elif self.mode == State.ROTATE:
                    # Reapply same angle, new axis
                    self._do_rotate()
                elif self.mode == State.SCALE:
                    # Reapply same scale, new axis
                    self._do_scale()


            self.scene_viewer.curViewport().draw()
            self.updateInfo()
            return True



        if key_value == 99: # c - change pivot
            
            if is_shift:
                if self.is_transforming:
                    return True
                hou.session.radial_state = self
    
                menu = hou.ui.createRadialMenu("3d_cursor_menu", "3D Cursor Menu")
                root = menu.root()
                
                root.createScriptItem(hou.radialItemLocation.Top, label="Object to Cursor",
                                    script="hou.session.radial_state._handle_option(0)")
                root.createScriptItem(hou.radialItemLocation.Right, label="Cursor to Object",
                                    script="hou.session.radial_state._handle_option(1)")
                root.createScriptItem(hou.radialItemLocation.Bottom, label="Cursor to World Origin",
                                    script="hou.session.radial_state._handle_option(2)")
                
                self.scene_viewer.displayRadialMenu("3d_cursor_menu")
                
                return True

            # Toggle pivot
            if self.pivot_mode == State.PIVOT_OBJECT:
                self.pivot_mode = State.PIVOT_CURSOR
            else:
                self.pivot_mode = State.PIVOT_OBJECT
            hou.session.blendini_pivot_mode = self.pivot_mode

            self.initAxis(self.axis)

            if self.is_transforming:
                # Reset parms to original
                t = self.transform.extractTranslates()
                if self.pivottranslate:
                    t = t - hou.Vector3(self.pivottranslate.evalAsFloats())
                self.position_parm.set(t)

                if self.rotation_parm:
                    self._set_rotation(self.original_rotation)
                if self.scale_parm:
                    #self.scale_parm.set(self.transform.extractScales(transform_order='srt'))
                    self._set_scale(self.transform.extractScales(transform_order='srt'))

                # Reinit winding tracking for new pivot
                self._init_rotation_tracking()

                if self.mode == State.TRANSLATE:
                    # Recalculate - different plane
                    self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
                    self.prev_pos = self.first_pos
                    self._do_translate()
                    self.prev_pos = self.mouse - self.screen_pos
                elif self.mode == State.ROTATE:
                    # Reapply same angle, new axis
                    #self.prev_pos = self.first_pos
                    self._do_rotate()
                    #self.prev_pos = self.mouse
                elif self.mode == State.SCALE:
                    # Reapply same scale, new axis
                    self._do_scale()

                
                self.custom_line = self.createCursorLine()
                self.show()
                

            else:
                self.hide()

            self.updateHudInfo()
            return True

        # INPUT MODE

        if self.is_transforming:

            # Numbers 0-9
            if (48 <= key_value <= 57) and not is_shift:
                self.is_input_mode = True
                self.amount_input += key_string
                self.do_transform()
                return True
            
            if key_string in ['pad0', 'pad1', 'pad2', 'pad3', 'pad4', 'pad5', 'pad6', 'pad7', 'pad8', 'pad9'] and not is_shift:
                self.is_input_mode = True
                self.amount_input += key_string.replace('pad', '')
                self.do_transform()
                return True

            if key_value == 46 and self.is_input_mode:  # `.` character
                self.amount_input += "."
                self.do_transform()
                return True
            
            if key_string == 'paddot' and self.is_input_mode: # '.' numpad character
                self.amount_input += "."
                self.do_transform()
                return True

            if key_value == 45 or (key_string == 'padminus'):  # `-` character
                self.is_input_mode = True
                self.amount_input += "-"
                self.do_transform()
                return True

            if (key_value == 61 and is_shift) or (key_string == 'padplus'):  # `+` character
                if self.is_input_mode:
                    self.amount_input += "+"
                    self.do_transform()
                return True

            if key_value == 56 and is_shift and self.is_input_mode:  # '*' character
                self.amount_input += "*"
                self.do_transform()
                return True
            
            if key_string == 'padstart'  and self.is_input_mode:  # '*' numpad character
                self.amount_input += "*"
                self.do_transform()
                return True

            if key_value == 47 and self.is_input_mode:  # '/' character
                self.amount_input += "/"
                self.do_transform()
                return True
            
            if key_string == 'padslash' and self.is_input_mode:# '/' numpad character
                self.amount_input += "/"
                self.do_transform()
                return True

            if key_value == 8:  # Del for deleting
                self.amount_input = self.amount_input[:-1]
                if len(self.amount_input) == 0:
                    self.is_input_mode = False
                self.do_transform()
                return True

        if key_value == 13:  # enter key
            if self.is_transforming:
                if self.is_input_mode:
                    self.amount_input = ""
                    self.is_input_invalid = False
                    self.is_input_mode = False
                self._finalize_transform()
                return True
            else:
                if self.node.type().name() == 'curve::2.0':
                    hou.session.blendini_update_curve = True
                    hou.session.blendini_activepoints = self.activepoints.eval()
                    hou.session.blendini_t = ""
                self.scene_viewer.enterCurrentNodeState()
                return True
            return False
        
        if key_value == 27:  # Escape
            if self.is_transforming:
                self.revert_transform()
                return True
            else:

                if hou.session.blendini_xform_from_handle:
                    self.scene_viewer.enterCurrentNodeState()
                else:
                    self.scene_viewer.enterViewState()
                return True
            

        return False

    def createAxis(self, key_value, is_shift, axes):
        if is_shift:
            if key_value == axes[0]:
                self.initAxis([State.Y_AXIS, State.Z_AXIS])
            elif key_value == axes[1]:
                self.initAxis([State.X_AXIS, State.Z_AXIS])
            elif key_value == axes[2]:
                self.initAxis([State.X_AXIS, State.Y_AXIS])
        else:
            if key_value == axes[0]:
                self.initAxis([State.X_AXIS])
            elif key_value == axes[1]:
                self.initAxis([State.Y_AXIS])
            elif key_value == axes[2]:
                self.initAxis([State.Z_AXIS])



    def reset_transform(self):
        self._log("Reset transform")
        if self.mode == State.TRANSLATE:
            self.position_parm.set((0, 0, 0))
        elif self.mode == State.ROTATE:
            self._set_rotation((0, 0, 0))
        elif self.mode == State.SCALE:
            #self.scale_parm.set((1, 1, 1))
            self._set_scale((1,1,1))

        self.space = State.VIEW
        self.trackball = False
        self.factor = State.FACTOR
        self.snapping = False
        self.is_transforming = False
        self.is_input_mode = False
        self.amount_input = ""
        self.wrap_offset = hou.Vector3(0,0,0)

        self.position_sum = self.position_sum_snapped = hou.Vector3(0, 0, 0)
        self.angle_sum = self.angle_sum_snapped = 0
        self.scale_sum = self.scale_sum_snapped = 1
        self.trackball_axis = hou.Vector2(0, 0)

            

        #self.initAxis(self.axis)
        self.updateTransform()
        self.initAxis([])
        self.hide()
        self.updateInfo()

        try:
            self.scene_viewer.endStateUndo()
        except:
            pass

        self._restore_cursor()


    def do_transform(self):
        try:
            amount = self.input_eval_amount = self.eval_(ast.parse(self.amount_input, mode='eval').body)
            self.is_input_invalid = False
            self.updateInfo()
        except:
            self.is_input_invalid = True
            if self.mode == State.SCALE:
                amount = 1
            else:
                amount = 0
            self.updateInfo()

        if self.mode == State.TRANSLATE:
            if len(self.axis) == 1:
                amount_vector = self.axis_vector * amount
            elif len(self.axis) == 2:
                amount_vector = State.AXIS_TO_VECTOR[self.axis[0]] * amount
            else:
                amount_vector = State.X_VECTOR * amount

            if self.space == State.LOCAL:
                amount_vector *= self.transform_no_scale.extractRotationMatrix3()
            elif self.space == State.VIEW:
                amount_vector *= self.view_transform.extractRotationMatrix3()

            matrix = self.transform * hou.hmath.buildTranslate(amount_vector)
            new_t = matrix.extractTranslates()
    
            if self.pivottranslate:
                new_t = new_t - hou.Vector3(self.pivottranslate.evalAsFloats())
            
            self.position_parm.set(new_t)

        elif self.mode == State.ROTATE:
            rotated_axis_vector = self.axis_vector
            if self.space == State.LOCAL:
                rotated_axis_vector *= self.transform_no_scale.extractRotationMatrix3()

            turn_axis = hou.hmath.buildRotateAboutAxis(rotated_axis_vector, amount)
            rotates = self._compute_unwound_rotation(rotated_axis_vector, amount)

            if self.pivotrotate:
                rotates = [rotates[i] - self.pivotrotate.evalAsFloats()[i] for i in range(3)]

            self._set_rotation(rotates)

            # Also translate for roatating around world origin
            if(self.pivot_mode == State.PIVOT_CURSOR):

                orig_pos = hou.Vector3(self.transform.extractTranslates())
                rotated_pos = (orig_pos - self.pivot_pos) * turn_axis + self.pivot_pos

                if self.pivottranslate:
                    rotated_pos = rotated_pos - hou.Vector3(self.pivottranslate.evalAsFloats())

                self.position_parm.set(rotated_pos)

        elif self.mode == State.SCALE:
            vector = (hou.Vector3(1, 1, 1) - self.axis_vector) + (hou.Vector3(1, 1, 1) * amount * hou.hmath.buildScale(self.axis_vector))
            scaling_matrix = hou.hmath.buildScale(vector)

            if self.space != State.LOCAL:
                matrix = self.transform * scaling_matrix
            else:
                matrix = scaling_matrix * self.transform

            scale = matrix.extractScales(transform_order='srt')
            self._set_scale(scale)



            # Also translate for scaling from world origin
            if(self.pivot_mode == State.PIVOT_CURSOR):
                original_pos = self.transform.extractTranslates()
                offset = original_pos - self.pivot_pos
                
                #scale_vec = (hou.Vector3(1, 1, 1) - self.axis_vector) + (self.axis_vector * self.scale_sum_snapped)
                
                if self.space == State.LOCAL:
                    # Rotate offset into local space
                    rot_matrix = self.transform_no_scale.extractRotationMatrix3()
                    inv_rot = rot_matrix.inverted()
                    local_offset = offset * inv_rot
                    
                    # Scale in local space
                    scaled_local = hou.Vector3(
                        local_offset[0] * vector[0],
                        local_offset[1] * vector[1],
                        local_offset[2] * vector[2]
                    )
                    
                    # Rotate back to world
                    scaled_offset = scaled_local * rot_matrix
                else:
                    scaled_offset = hou.Vector3(
                        offset[0] * vector[0],
                        offset[1] * vector[1],
                        offset[2] * vector[2]
                    )
                
                new_pos = self.pivot_pos + scaled_offset

                if self.pivottranslate:
                    new_pos = new_pos - hou.Vector3(self.pivottranslate.evalAsFloats())

                self.position_parm.set(new_pos)


    def eval_(self, node):
        match node:
            case ast.Constant(value) if isinstance(value, (int, float)):
                return value
            case ast.BinOp(left, op, right):
                return operators[type(op)](self.eval_(left), self.eval_(right))
            case ast.UnaryOp(op, operand):
                return operators[type(op)](self.eval_(operand))
            case _:
                raise TypeError(node)
            


    def _handle_option(self, option):
        if option == 0:
            self.position_parm.set(self.cursor_pos)
        elif option == 1:
            position = self.node.geometry().boundingBox().center()
            self._update_cursor(position=position)
        elif option == 2:
            self._update_cursor(position=(0,0,0))
        pass


def createViewerStateTemplate():
    """Mandatory entry point to create and return the viewer state template to register."""

    state_name = "blendini_xform"
    state_label = "Blendini Xform"
    state_cat = hou.sopNodeTypeCategory()
    extra_contexts = [hou.objNodeTypeCategory(), hou.dopNodeTypeCategory()]


    template = hou.ViewerStateTemplate(state_name, state_label, state_cat, extra_contexts)
    template.bindFactory(State)
    template.bindIcon("SOP_xform")

    template.bindGadget(hou.drawableGeometryType.Point, "point_gadget", label="Points")

    return template