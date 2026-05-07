"""
Blendini Xform Viewer Handle by Yonatan Bary
This sets the custom python state on key press (g, r, s)
"""

import hou
import viewerhandle.utils as hu
import os
import re

import importlib
import node_transformer
importlib.reload(node_transformer)
from node_transformer import NodeTransformer


if hou.applicationVersion()[0] >= 21:
    from PySide6 import QtWidgets, QtCore, QtGui
else:
    from PySide2 import QtWidgets, QtCore, QtGui



import os


_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUPPORTED_NODES_PATH = os.path.join(_PACKAGE_ROOT, "Nodes.txt")


def _load_supported_nodes(filepath):
    """Load supported nodes from config file. Returns dict of {category: [node_names]}."""
    nodes = {}
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '/' not in line:
                    continue
                cat, name = line.split('/', 1)
                nodes.setdefault(cat, []).append(name)
    except Exception as e:
        print(f"[Blendini Xform] Failed to load {filepath}: {e}")
    return nodes


SUPPORTED_NODES = _load_supported_nodes(_SUPPORTED_NODES_PATH)

_CAT_MAP = {'Sop': 'sop', 'Object': 'obj', 'Dop': 'dop', 'Lop': 'lop'}

_SUPPORTED_SET = frozenset(
    (cat, name) for cat, nodes in SUPPORTED_NODES.items() for name in nodes
)


def _bind_blendini_xform():
    for cat, nodes in SUPPORTED_NODES.items():
        for node_name in nodes:
            hou.hscript(
                f'ombind -t {cat} "Blendini Xform" '
                f'{node_name}:tx blendini_xform:tx'
            )


def _is_supported_node(node):
    if not node:
        return False
    cat = _CAT_MAP.get(node.type().category().name())
    return (cat, node.type().name()) in _SUPPORTED_SET if cat else False


class SceneViewerFilter(QtCore.QObject):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.space_held = False

    _HANDLED_EVENTS = frozenset((
        QtCore.QEvent.KeyPress,
        QtCore.QEvent.KeyRelease,
        QtCore.QEvent.MouseButtonPress,
        QtCore.QEvent.MouseButtonRelease,
        QtCore.QEvent.MouseMove,
    ))

    def _should_block(self):
        app = QtWidgets.QApplication.instance()
        focused = app.focusWidget()
        if focused and focused.testAttribute(QtCore.Qt.WA_InputMethodEnabled):
            return True
        return any(
            w.windowTitle() in ("", "completer")
            for w in app.topLevelWidgets()
            if w.isVisible() and w.objectName() == "RE_Window"
        )

    def eventFilter(self, obj, event):

        if event.type() not in self._HANDLED_EVENTS:
            return False
        
        if not isinstance(obj, QtGui.QWindow):
            return False
        if obj.objectName() not in ("RE_WindowWindow", "RE_GLDrawableWindow"):
            return False
        
        sv = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
        if not sv or sv != hou.ui.paneTabUnderCursor():
            return False
        
        node = sv.currentNode()
        current_state = sv.currentState()
        is_bx_state = current_state in ("blendini_xform", "blendini_xform_lop")
        state = getattr(hou.session, 'blendini_xform_state', None)

        if event.type() == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.RightButton:
                if state and state.is_transforming and is_bx_state:
                    return True  # Block context menu
        
        # Right-click to cancel transform
        if event.type() == QtCore.QEvent.MouseButtonRelease:
            if event.button() == QtCore.Qt.RightButton:
                
                if state and state.is_transforming and is_bx_state:
                    state.revert_transform()
                    return True
                
        
        if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Space:
            self.space_held = True
        if event.type() == QtCore.QEvent.KeyRelease and event.key() == QtCore.Qt.Key_Space:
            self.space_held = False
        
        # G/R/S to activate state
        if event.type() == QtCore.QEvent.KeyPress and not event.isAutoRepeat():
            if event.modifiers() != QtCore.Qt.NoModifier:
                return False
            key = event.key()
            if key in (QtCore.Qt.Key_G, QtCore.Qt.Key_R, QtCore.Qt.Key_S):

                if self.space_held:
                    return False

                if self._should_block():
                    return False

                # Skip if already in our state
                if current_state in ("blendini_xform", "blendini_xform_lop"):
                    return False
                
                # Skip if handle is active (node state) - let handle's onKeyEvent process it
                if node and current_state == node.type().name():
                    return False
                
                if not _is_supported_node(node):
                    return False
                
                key_map = {
                    QtCore.Qt.Key_G: 'g',
                    QtCore.Qt.Key_R: 'r',
                    QtCore.Qt.Key_S: 's'
                }
                hou.session.blendini_xform_mode = key_map[key]
                
                cursor_pos = QtGui.QCursor.pos()
                widget = sv.qtWindow()
                local_pos = widget.mapFromGlobal(cursor_pos)
                pixel_ratio = widget.devicePixelRatio()
                widget_height = widget.height() * pixel_ratio
                
                mouse_x = local_pos.x() * pixel_ratio
                mouse_y = widget_height - (local_pos.y() * pixel_ratio)
                
                hou.session.blendini_xform_mouse = hou.Vector3(mouse_x, mouse_y, 0)
                hou.session.blendini_xform_from_handle = False
                
                if node.type().category().name() == 'Lop':
                    sv.setCurrentState("blendini_xform_lop")
                else:
                    sv.setCurrentState("blendini_xform")
                return True
            

        # Block unhandled keys while transforming (but let handled keys through)
        if event.type() == QtCore.QEvent.KeyPress:
            if state and state.is_transforming:
                key = event.key()
                mods = event.modifiers()
                
                # Let Ctrl/Cmd through (for snapping)
                if mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier):
                    return False
                
                # Keys the state needs to receive
                allowed_keys = {
                    QtCore.Qt.Key_G, QtCore.Qt.Key_R, QtCore.Qt.Key_S, QtCore.Qt.Key_T,
                    QtCore.Qt.Key_X, QtCore.Qt.Key_Y, QtCore.Qt.Key_Z,
                    QtCore.Qt.Key_C,
                    QtCore.Qt.Key_W,
                    QtCore.Qt.Key_Space,
                    QtCore.Qt.Key_0, QtCore.Qt.Key_1, QtCore.Qt.Key_2, QtCore.Qt.Key_3,
                    QtCore.Qt.Key_4, QtCore.Qt.Key_5, QtCore.Qt.Key_6, QtCore.Qt.Key_7,
                    QtCore.Qt.Key_8, QtCore.Qt.Key_9,
                    QtCore.Qt.Key_Period, QtCore.Qt.Key_Minus, QtCore.Qt.Key_Plus,
                    QtCore.Qt.Key_Asterisk, QtCore.Qt.Key_Slash, QtCore.Qt.Key_Equal,
                    QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete,
                    QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Escape,
                }
                
                if key in allowed_keys:
                    return False
                
                # Numpad keys
                if mods & QtCore.Qt.KeypadModifier:
                    return False
                
                return True  # Block everything else
        
        return False



def createViewerHandleTemplate():
    template = hou.ViewerHandleTemplate(
        "blendini_xform", 
        "Blendini Xform", 
        [hou.sopNodeTypeCategory(),
         hou.objNodeTypeCategory(),
         hou.dopNodeTypeCategory(),
         hou.lopNodeTypeCategory()]
    )
    template.bindFactory(Handle)
    
    template.bindParameter(hou.parmTemplateType.Float, name="tx", label="Translate X", default_value=0.0)
    template.bindParameter(hou.parmTemplateType.Float, name="ty", label="Translate Y", default_value=0.0)
    template.bindParameter(hou.parmTemplateType.Float, name="tz", label="Translate Z", default_value=0.0)
    template.bindParameter(hou.parmTemplateType.Int, name="mode", label="Curve Mode", default_value=1)
    template.exportParameters(["tx", "ty", "tz", "mode"])


    # BIND HANDLE TO SUPPORTED NODES
    hou.ui.postEventCallback(_bind_blendini_xform)


    # INSTALL NODE TRANSFORMER FILTER
    if hasattr(hou.session, 'bx_network_filter'):
        QtWidgets.QApplication.instance().removeEventFilter(hou.session.bx_network_filter)
    hou.session.bx_network_filter = NodeTransformer()
    if hou.getPreference('custom.bx.enabled') != '0':
        QtWidgets.QApplication.instance().installEventFilter(hou.session.bx_network_filter)


    # INSTALL SCENE VIEWER FILTER
    if hasattr(hou.session, 'bx_sceneviewer_filter'):
        QtWidgets.QApplication.instance().removeEventFilter(hou.session.bx_sceneviewer_filter)
    hou.session.bx_sceneviewer_filter = SceneViewerFilter()
    QtWidgets.QApplication.instance().installEventFilter(hou.session.bx_sceneviewer_filter)

    
    return template


class Handle(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.xform_aid = hu.TransformAid(self, {"translate": ["tx", "ty", "tz"]})

        self.keys_override = [
            {'symbol': 'h.pane.gview.state.volatile_select', 'key': 's', 'override': False},
            {'symbol': 'h.pane.gview.state.new_rotate', 'key': 'r', 'override': False},
            {'symbol': 'h.pane.gview.state.sop.sidefx_curve.enter_draw_mode', 'key': 'g', 'override': False},
            #{'symbol': 'h.pane.gview.accept', 'key': 'Enter', 'override': False},
        ]

    
    def onActivate(self, kwargs):
        """Triggered when handle becomes active (Enter on node)"""

        # Using event call back since self.scene_viewer.currentNode() will not return the correct node 
        # if the state is already active when placing a curve node
        hou.ui.postEventCallback(lambda: self._afterActivate())


    def _afterActivate(self):
        node = self.scene_viewer.currentNode()

        # Clear R and S hotkey conflicts
        for key in self.keys_override:

            if key['key'] == 'Enter':
                if node and node.type().name() == 'curve::2.0':
                    continue  # Don't clear Enter, let onparmchange state handle it

            current = hou.hotkeys.assignments(key['symbol'])
            if current:
                hou.hotkeys.clearAssignments(key['symbol'])
                key['override'] = True

        
        
        if node and node.type().name() == 'curve::2.0' and getattr(hou.session, 'blendini_update_curve', False):
            hou.ui.postEventCallback(lambda: self._update_curve())
        elif node and node.type().name() == 'curve::2.0' and getattr(hou.session, 'blendini_activepoints', None):
            #hou.ui.postEventCallback(lambda: self._update_activepoints()) # This is for visual clarity, so we don't have the previous line faded
            pass
    
    def _update_curve(self):

        with hou.undos.disabler():

            node = self.scene_viewer.currentNode()
            node.parm('activepoints').set(getattr(hou.session, 'blendini_activepoints', ""))

            t = getattr(hou.session, 'blendini_t', "")

            if t != "":
                pivot = hou.Vector3(getattr(hou.session, 'blendini_pivott', ""))

                pivot_adjusted = pivot + hou.Vector3(t)

                node.parmTuple('pivottranslate').set(pivot_adjusted)
                node.parmTuple('pivotrotate').set(getattr(hou.session, 'blendini_pivotr', ""))


                if getattr(hou.session, 'blendini_is_cursor', False):
                    translate = hou.Vector3(node.parmTuple('translate').eval())
                    scale = hou.Vector3(node.parmTuple('scale').eval())
                    translate = translate * hou.hmath.buildScale(scale)

                    rotate = hou.Vector3(node.parmTuple('rotate').eval())
                    translate = translate * hou.hmath.buildRotate(rotate)

                    node.parmTuple('translate').set(translate)

                try:
                    del hou.session.blendini_update_curve
                    #del hou.session.blendini_activepoints
                    del hou.session.blendini_pivott
                    del hou.session.blendini_pivotr
                except:
                    pass


    def _update_activepoints(self):

        with hou.undos.disabler():
            node = self.scene_viewer.currentNode()
            node.parm('activepoints').set(getattr(hou.session, 'blendini_activepoints', ""))

            del hou.session.blendini_activepoints
        

    def onKeyEvent(self, kwargs):
        """Handle keyboard events"""
        device = kwargs["ui_event"].device()
        key = device.keyString()

        scene_viewer = self.scene_viewer  # Capture reference
        node = self.scene_viewer.currentNode()

        if key in ("g", "r", "s"): #, "Enter"):

            if node and node.type().name() == 'curve::2.0':
                if node.parm('activepoints') and node.parm('activepoints').eval() == "":
                    return True

            hou.session.blendini_xform_mode = key
            hou.session.blendini_xform_mouse = hou.Vector3(device.mouseX(), device.mouseY(), 0)
            hou.session.blendini_xform_from_handle = True
            
            if node.type().category().name() == 'Lop':
                hou.ui.postEventCallback(lambda: scene_viewer.setCurrentState("blendini_xform_lop"))
            else:
                hou.ui.postEventCallback(lambda: scene_viewer.setCurrentState("blendini_xform"))


            return True  # Consume the event
        
        elif key in ['Cmd+z', 'Ctrl+z']:
            
            if node and node.type().name() == 'curve::2.0' and node.parm('activepoints'):
                hou.session.blendini_activepoints = node.parm('activepoints').eval()
                hou.ui.postEventCallback(lambda: self._update_activepoints())
    
        return False  # Let other handlers process it
    

    
    def onDeactivate(self, kwargs):
        """Triggered when handle is deactivated"""

        # Restore hotkeys
        for key in self.keys_override:
            if key['override']:
                hou.hotkeys.addAssignment(key['symbol'], key['key'])
                key['override'] = False


    def onParmChangeEvent(self, kwargs):

        parm_name = kwargs['parm_name']
        parm_value = kwargs['parm_value']

        # node = self.scene_viewer.currentNode()
        
        # if node and node.type().name() == 'curve::2.0':
        #     if parm_name == 'mode':
        #         key = self.keys_override[3] # Enter Key
        #         if parm_value == 0:
        #             current = hou.hotkeys.assignments(key['symbol'])
        #             if current:
        #                 hou.hotkeys.clearAssignments(key['symbol'])
        #                 key['override'] = True
        #         else:
        #             current = hou.hotkeys.assignments(key['symbol'])
        #             if not current:
        #                 hou.hotkeys.addAssignment(key['symbol'], key['key'])
        #                 key['override'] = False

    
    def onMouseEvent(self, kwargs):
        return False
    
    def onDraw(self, kwargs):
        pass
    
    def onDrawSetup(self, kwargs):
        pass