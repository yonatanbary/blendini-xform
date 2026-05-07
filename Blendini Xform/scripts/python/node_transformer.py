


import hou
import math, time
import os
import nodegraphsnap as snap
import nodegraphutils as graphutils
import nodegraphprefs as prefs


if hou.applicationVersion()[0] >= 21:
    from PySide6 import QtWidgets, QtGui, QtCore
    from PySide6.QtCore import QTimer
else:
    from PySide2 import QtWidgets, QtGui, QtCore
    from PySide2.QtCore import QTimer




class NodeTransformer(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self._active = False
        self._mode = None
        self._axis = None
        self._slow = None
        self._last_cursor = None
        self._start_pos = None
        self._node_start_positions = {}
        self._center = None
        self._editor = None
        self._start_distance = None
        self._accumulated_offset = None
        self._accumulated_scale = None
        self._accumulated_angle = None
        self._undo_group = None
        self._ui_scale = float(os.environ.get('BX_UI_SCALE', 1.0)) 

        self._drawing = False
        self._draw_last_pos = None
        self._draw_shapes = []
    

    def _should_block2(self):
        app = QtWidgets.QApplication.instance()
        tab_menu_open = any(
            w.objectName() == "RE_Window" and w.windowTitle() in ("", "completer")
            for w in app.topLevelWidgets()
            if w.isVisible()
        )
        focused = app.focusWidget()
        text_input_active = focused and focused.testAttribute(QtCore.Qt.WA_InputMethodEnabled)
        return tab_menu_open or text_input_active
    

    def _should_block(self):
        app = QtWidgets.QApplication.instance()

        # Fast path first (O(1))
        focused = app.focusWidget()
        if focused and focused.testAttribute(QtCore.Qt.WA_InputMethodEnabled):
            return True

        for w in app.topLevelWidgets():
            if not w.isVisible():
                continue

            name = w.objectName()
            title = w.windowTitle()

            # Tab menu / completer
            if name == "RE_Window" and title in ("", "completer"):
                return True

            # Any dialog/popup that isn't the main Houdini window
            # Main window has RE_Window name + a non-empty title (the .hip filename)
            # Background QWidgets have no name and no title
            if name != "RE_Window" and name != "":
                return True  # e.g. NodeRecipeDialog, FindWindow, etc.

            # Named dialogs with titles (but not main window)
            if name == "" and title != "":
                return True  # e.g. Save Decoration

        return False

    def _should_block3(self):
        app = QtWidgets.QApplication.instance()
        
        for w in app.topLevelWidgets():
            if not w.isVisible():
                continue
            if w.objectName() == "RE_Window" and w.windowTitle() in ("", "completer"):
                return True
            if type(w).__name__ == "FindWindow":
                return True
        
        focused = app.focusWidget()
        if focused and focused.testAttribute(QtCore.Qt.WA_InputMethodEnabled):
            return True
        
        return False
    
    def _debug_widgets(self):
        app = QtWidgets.QApplication.instance()
        print("=== Top Level Widgets ===")
        for w in app.topLevelWidgets():
            if w.isVisible():
                print(f"  Name: '{w.objectName()}' | Title: '{w.windowTitle()}' | Type: {type(w).__name__}")

        print("\n=== Focus Widget ===")
        focused = app.focusWidget()
        if focused:
            print(f"  Type: {type(focused).__name__}")
            print(f"  Name: '{focused.objectName()}'")
            print(f"  InputMethodEnabled: {focused.testAttribute(QtCore.Qt.WA_InputMethodEnabled)}")
            # Walk up parent chain
            parent = focused.parent()
            while parent:
                print(f"  Parent: {type(parent).__name__} | Name: '{parent.objectName()}'")
                parent = parent.parent()
        else:
            print("  None")
    

    _HANDLED_EVENTS = frozenset((
        QtCore.QEvent.KeyPress,
        QtCore.QEvent.KeyRelease,
        QtCore.QEvent.MouseButtonPress,
        QtCore.QEvent.MouseButtonRelease,
        QtCore.QEvent.MouseMove,
    ))


    def eventFilter(self, obj, event):

        etype = event.type()

        # Fast reject: only care about input events
        if etype not in self._HANDLED_EVENTS:
            return False

        # Mouse events are only relevant while transforming
        if not self._active and etype != QtCore.QEvent.KeyPress:
            return False

        pane = hou.ui.paneTabUnderCursor()
        if not pane or pane.type() != hou.paneTabType.NetworkEditor:
            return False

        if not self._active and self._should_block():
            return False
        
        # if etype == QtCore.QEvent.Wheel:
        #     print("Wheel event:", event)
        
        if etype == QtCore.QEvent.KeyPress:
            key = event.key()

            # KEY G
            if key == QtCore.Qt.Key_G:
                if event.modifiers() == QtCore.Qt.NoModifier:
                    if not event.isAutoRepeat():
                        if not self._active:
                            self._start_transform(pane, 'grab')
                        elif self._mode != 'grab':
                            self._switch_mode('grab')
                        return True
            
            # KEY R
            elif key == QtCore.Qt.Key_R:
                if hou.getPreference('custom.bx.rotate') != '1':
                    return False
                if event.modifiers() == QtCore.Qt.NoModifier:
                    if not event.isAutoRepeat() and len(hou.selectedItems()) > 0:
                        if not self._active:
                            self._start_transform(pane, 'rotate')
                        elif self._mode != 'rotate':
                            self._switch_mode('rotate')
                        return True
            
            # KEY S
            elif key == QtCore.Qt.Key_S:
                if event.modifiers() == QtCore.Qt.NoModifier:
                    if not event.isAutoRepeat() and len(hou.selectedItems()) > 0:
                        if not self._active:
                            self._start_transform(pane, 'scale')
                        elif self._mode != 'scale':
                            self._switch_mode('scale')
                        return True
            
            # KEY X
            elif key == QtCore.Qt.Key_X:
                if self._active:
                    if self._mode in ('grab', 'scale'):
                        self._axis = 'x' if self._axis != 'x' else None
                        self._do_transform(self._editor.cursorPosition())
                        return True
                # elif event.modifiers() == QtCore.Qt.NoModifier:
                #     self._delete_nodes(pane)
                #     return True
            
            # KEY Y
            elif key == QtCore.Qt.Key_Y:
                if self._active and self._mode in ('grab', 'scale'):
                    self._axis = 'y' if self._axis != 'y' else None
                    self._do_transform(self._editor.cursorPosition())
                    return True
            
            # KEY ESC
            elif key == QtCore.Qt.Key_Escape:
                if self._active:
                    self._revert()
                    return True
            
            # KEY 0
            elif key == QtCore.Qt.Key_0:
                if self._active and self._mode == 'scale':
                    self._start_distance = 1  # Avoid division issues
                    self._do_scale(self._center)  # cursor at center = scale 0
                    self._apply()
                    return True
            
            # KEY SHIFT+D (duplicate and grab)
            elif key == QtCore.Qt.Key_D:
                if not self._active and not event.isAutoRepeat():
                    mods = event.modifiers()
                    if mods == QtCore.Qt.ShiftModifier and hou.selectedItems():
                        self._start_transform(pane, 'grab', duplicate=True, reference=False)
                        return True
                    elif mods == QtCore.Qt.AltModifier and hou.selectedItems():
                        self._start_transform(pane, 'grab', duplicate=True, reference=True)
                        return True

            # Consume all other keys while transform is active
            if self._active:
                return True

            return False

        elif etype == QtCore.QEvent.MouseMove:
            if self._active:
                self._slow = event.modifiers() & QtCore.Qt.ShiftModifier
                self._do_transform(self._editor.cursorPosition())
                return True
        
        elif etype == QtCore.QEvent.MouseButtonPress:
            if self._active:
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._apply()
                elif event.button() == QtCore.Qt.MouseButton.RightButton:
                    self._revert()
                return True  # Consume original


        ### DRAWING MODE ###

        # #print(f"event: {event.type()}, time: {time.time()}")
        # pane.setShapes(list(self._draw_shapes))
        # #QtCore.QTimer.singleShot(0, lambda: pane.setOverlayShapes(list(self._draw_shapes)))
        # if etype in (QtCore.QEvent.UpdateRequest, QtCore.QEvent.Expose, QtCore.QEvent.Paint):
        #     print(f"Redraw event: {etype}")

        # # KEY D
        # if etype == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_D:
        #     if not event.isAutoRepeat() and not self._active:
        #         print("D Key Pressed")
        #         self._drawing = True
        #     return True
            

        # # KEY D RELEASE
        # if etype == QtCore.QEvent.KeyRelease and event.key() == QtCore.Qt.Key_D:
        #     if not event.isAutoRepeat() and not self._active:
        #         print("D Key Released")
        #         self._drawing = False
        #     return True
        
        # # Mouse move while drawing + LMB held
        # if etype == QtCore.QEvent.MouseMove and self._drawing:
        #     if event.buttons() & QtCore.Qt.LeftButton:
        #         cursor = pane.cursorPosition()
        #         if self._draw_last_pos:
        #             self._draw_shapes.append(hou.NetworkShapeLine(
        #                 self._draw_last_pos, cursor,
        #                 color=hou.Color(0.38, 0.612, 0.78), alpha=1.0, width=10.0,
        #                 screen_space=False, smooth=False, dashed=False
        #             ))
        #             pane.setOverlayShapes(list(self._draw_shapes))
        #         self._draw_last_pos = cursor
        #     else:
        #         self._draw_last_pos = None  # Reset when LMB not held
        #     return True
        

        return False
    
    def _start_transform(self, pane, mode, duplicate=False, reference=False):
        
        self._ui_scale = float(os.environ.get('BX_UI_SCALE', 1.0)) 

        nodes = hou.selectedItems()
        if not nodes:
            return
        
        
        # Start undo group
        self._undo_group = hou.undos.group(f"{mode.capitalize()} Nodes")
        self._undo_group.__enter__()

        self._duplicate_mode = duplicate
        self._original_nodes = list(nodes) if duplicate else None
        

        if duplicate:
            parent = nodes[0].parent()

            # Store original positions
            original_positions = {n: n.position() for n in nodes}
            new_nodes = parent.copyItems(nodes, channel_reference_originals=reference)
            # Restore positions to match originals
            for old_node, new_node in zip(nodes, new_nodes):
                new_node.setPosition(original_positions[old_node])

            for n in nodes:
                n.setSelected(False)
            for n in new_nodes:
                n.setSelected(True)
            nodes = new_nodes  # Work with new nodes from here


        
        self._active = True
        self._mode = mode
        self._axis = None
        self._editor = pane
        self._start_pos = pane.cursorPosition()
        self._last_cursor = self._start_pos

        self._accumulated_offset = hou.Vector2(0, 0)
        self._accumulated_scale = 1.0
        self._accumulated_angle = 0.0

        self._node_start_positions = {n: n.position() for n in nodes}
        self._node_start_rects = {n: self._editor.itemRect(n, False) for n in nodes}

        self._center = self._get_bounds_center(nodes)
        
        self._start_distance = math.sqrt(
            (self._start_pos[0] - self._center[0]) ** 2 +
            (self._start_pos[1] - self._center[1]) ** 2
        )

        cursor = hou.qt.getCursor("invisible")
        QtWidgets.QApplication.setOverrideCursor(cursor)
        
        # Run transform immediately to draw initial overlay
        self._do_transform(self._start_pos)
        
        self._update_prompt()



    def _switch_mode(self, mode):

        cursor = self._editor.cursorPosition()
        self._mode = mode
        self._last_cursor = cursor

        if mode == 'rotate':
            self._axis = None
        
        if mode == 'grab':
            self._accumulated_offset = cursor - self._start_pos
        elif mode == 'rotate':
            start_angle = math.atan2(
                self._start_pos[1] - self._center[1],
                self._start_pos[0] - self._center[0]
            )
            curr_angle = math.atan2(
                cursor[1] - self._center[1],
                cursor[0] - self._center[0]
            )
            self._accumulated_angle = curr_angle - start_angle
        elif mode == 'scale':
            curr_distance = (cursor - self._center).length()
            self._accumulated_scale = curr_distance / self._start_distance if self._start_distance > 0.001 else 1.0
        
        self._do_transform(cursor)
        self._update_prompt()


    
    def _do_transform(self, cursor):
        try:
            if self._mode == 'grab':
                self._do_grab(cursor)
            elif self._mode == 'rotate':
                self._do_rotate(cursor)
            elif self._mode == 'scale':
                self._do_scale(cursor)
        except hou.ObjectWasDeleted:
            # print("Node was deleted, cancelling transform")
            self._reset()
    

    def _do_grab(self, cursor):
        delta = cursor - self._last_cursor
        self._last_cursor = cursor
        
        if self._slow:
            delta = delta * 0.1
        
        self._accumulated_offset = self._accumulated_offset + delta
        
        offset = self._accumulated_offset
        if self._axis == 'x':
            offset = hou.Vector2(offset[0], 0)
        elif self._axis == 'y':
            offset = hou.Vector2(0, offset[1])
        
        for node, start_pos in self._node_start_positions.items():
            node.setPosition(start_pos + offset)
        
        moveitems = list(self._node_start_positions.keys())
        alignrects = self._editor.allVisibleRects(moveitems)
        
        # Collect snap results from all nodes
        all_snaps = []
        for node in moveitems:
            draggedrect = self._editor.itemRect(node, False)
            snapresult = snap.snap(self._editor, node, draggedrect, alignrects)
            if snapresult.isValid():
                all_snaps.append(snapresult)
        
        # Apply snap from first valid result (they should all have same delta if aligned)
        if all_snaps:
            snap_delta = all_snaps[0].delta()
            for n in moveitems:
                n.setPosition(n.position() + snap_delta)
        
        # Build overlay shapes from ALL snap results
        shapes = []
        for snapresult in all_snaps:
            shapes.extend(snapresult.shapes(self._editor))

        # Centerline snapping - check all nodes
        # all_centerline_snaps = []
        # for node in moveitems:
        #     dragged_rect = self._editor.itemRect(node, False)
        #     centerline_delta, centerline_shapes = self._find_centerline_snaps(dragged_rect, alignrects)
        #     if centerline_delta.length() > 0:
        #         all_centerline_snaps.append((centerline_delta, centerline_shapes))
        
        # if all_centerline_snaps:
        #     centerline_delta, centerline_shapes = all_centerline_snaps[0]
        #     for n in moveitems:
        #         n.setPosition(n.position() + centerline_delta)
        #     shapes.extend(centerline_shapes)
        
        shapes.extend(self._create_rotation_indicator(cursor, radius=10 * self._ui_scale, size=10 * self._ui_scale, width=5 * self._ui_scale))
        
        # Connection drop preview
        self._pending_connection = None
        if len(moveitems) == 1:
            conn = self._find_nearby_connection(moveitems[0])
            self._pending_connection = conn
            if conn:
                shapes.extend(self._draw_connection_preview(moveitems[0], conn))
        
        self._editor.setOverlayShapes(shapes)
        
    def _do_rotate(self, cursor):
        last_angle = math.atan2(
            self._last_cursor[1] - self._center[1],
            self._last_cursor[0] - self._center[0]
        )
        curr_angle = math.atan2(
            cursor[1] - self._center[1],
            cursor[0] - self._center[0]
        )
        self._last_cursor = cursor
        
        delta_angle = curr_angle - last_angle
        
        # Normalize to -π to π
        while delta_angle > math.pi:
            delta_angle -= 2 * math.pi
        while delta_angle < -math.pi:
            delta_angle += 2 * math.pi
        
        if self._slow:
            delta_angle *= 0.1
        self._accumulated_angle += delta_angle
        
        for node, start_pos in self._node_start_positions.items():
            start_rect = self._node_start_rects[node]
            start_center = start_rect.center()
            offset_to_center = start_center - start_pos
            
            rel = start_center - self._center
            cos_a = math.cos(self._accumulated_angle)
            sin_a = math.sin(self._accumulated_angle)
            new_rel = hou.Vector2(
                rel[0] * cos_a - rel[1] * sin_a,
                rel[0] * sin_a + rel[1] * cos_a
            )
            new_center = self._center + new_rel
            node.setPosition(new_center - offset_to_center)
        
        self._draw_overlay(cursor, self._make_rotate_arrows)
    

    def _do_scale(self, cursor):
        last_distance = (self._last_cursor - self._center).length()
        curr_distance = (cursor - self._center).length()
        self._last_cursor = cursor
        
        if last_distance > 0.001:
            delta_scale = curr_distance / last_distance
            if self._slow:
                delta_scale = 1 + (delta_scale - 1) * 0.1
            self._accumulated_scale *= delta_scale
        
        scale = self._accumulated_scale
        for node, start_pos in self._node_start_positions.items():
            start_rect = self._node_start_rects[node]
            start_center = start_rect.center()
            offset_to_center = start_center - start_pos
            
            rel = start_center - self._center
            
            if self._axis == 'x':
                new_rel = hou.Vector2(rel[0] * scale, rel[1])
            elif self._axis == 'y':
                new_rel = hou.Vector2(rel[0], rel[1] * scale)
            else:
                new_rel = rel * scale
            
            new_center = self._center + new_rel
            node.setPosition(new_center - offset_to_center)
        
        self._draw_overlay(cursor, self._make_scale_arrows)
    
    def _draw_overlay(self, cursor, arrow_func):
        lines = self._create_dashed_line(self._center, cursor, dash_length=7 * self._ui_scale, gap_length=9 * self._ui_scale, width=3.0 * self._ui_scale)
        arrows = arrow_func(self._center, cursor, width=6.0 * self._ui_scale)
        self._editor.setOverlayShapes(lines + arrows)
    
    def _apply(self):
        

        if self._mode == 'grab' and self._pending_connection:
            conn = self._pending_connection
            node = next(iter(self._node_start_positions.keys()))
            moveitems = list(self._node_start_positions.keys())

            graphutils.insertItemsIntoWire(
                conn,
                chain_items=[node],
                all_selected_items=moveitems,
                remove_existing_connections=True,
                editor=self._editor
            )

            # Push connected nodes out of the way
            graphutils.moveNodesToAvoidOverlap(self._editor, moveitems, update_graph=True)
            
            # input_conns = node.inputConnections()
            # parent = input_conns[0].inputNode() if input_conns else None
            
            # # Reconnect children to parent (or disconnect)
            # for out_conn in node.outputConnections():
            #     out_conn.outputNode().setInput(out_conn.outputIndex(), parent)
            
            # # Insert into new connection
            # conn.outputNode().setInput(conn.outputIndex(), node)
            # node.setInput(0, conn.inputNode())


        # Close undo group - changes are kept and undoable
        if self._undo_group:
            self._undo_group.__exit__(None, None, None)
            self._undo_group = None

        self._reset()
    
    def _revert(self):
        # Close undo group then undo it immediately
        if self._undo_group:
            self._undo_group.__exit__(None, None, None)
            self._undo_group = None

            # Only undo if something actually changed
            has_changes = any(
                node.position() != start_pos
                for node, start_pos in self._node_start_positions.items()
                if node is not None
            )
            if has_changes or self._duplicate_mode:
                hou.undos.performUndo()

        self._reset()
    
    def _reset(self):
        def restore_all_cursors():
            while QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()
        
        QTimer.singleShot(150, restore_all_cursors)

        if self._editor:
            self._editor.setOverlayShapes([])
            
            # self._editor.qtWindow().update()
            # self._editor.qtWindow().repaint()

            # widget = self._editor.qtWindow()
            
            # # Send a fake Shift press/release (harmless key)
            # press = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Q, QtCore.Qt.NoModifier)
            # release = QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Q, QtCore.Qt.NoModifier)
            # QtWidgets.QApplication.postEvent(widget, press)
            # QtWidgets.QApplication.postEvent(widget, release)

            # self._editor.redraw()

        #QtWidgets.QApplication.changeOverrideCursor(self._saved_cursor)
            
        self._active = False
        self._mode = None
        self._axis = None
        self._slow = None
        self._node_start_positions = {}
        self._center = None
        #self._editor = None
        self._start_distance = None
        self._undo_group = None

        self._duplicate_mode = False
        self._original_nodes = None

        self._update_prompt()

    
    def _delete_nodes(self, pane):
        items = hou.selectedItems()
        if not items:
            return
        
        with hou.undos.group("Delete Nodes"):
            for item in items:
                try:
                    item.destroy()
                except hou.ObjectWasDeleted:
                    pass
        
    def _get_bounds_center(self, nodes):
        rect = hou.BoundingRect()
        for node in nodes:
            rect.enlargeToContain(self._editor.itemRect(node, False))
        return rect.center()
    

    def _update_prompt(self):
        if not self._active or not self._editor:
            if self._editor:
                self._editor.setPrompt("")
            return
        
        # Respect Show Help Messages preference
        import nodegraphprefs as prefs
        if not prefs.showPromptText(self._editor):
            return
        
        if self._mode == 'grab':
            action = "move"
        elif self._mode == 'rotate':
            action = "rotate"
        elif self._mode == 'scale':
            action = "scale"
        else:
            action = ""
        
        axis_text = f" on {self._axis.upper()} axis" if self._axis else ""
        
        prompt = f"Drag mouse to {action}{axis_text}. Left click to apply, right click to cancel."
        
        if self._mode in ('grab', 'scale'):
            prompt += "\nPress X or Y to constrain axis."
        
        self._editor.setPrompt(prompt)
    

    def _create_dashed_line(self, start, end, dash_length=20.0, gap_length=10.0, width=2.0, color=hou.Color(1, 1, 1), alpha=1):
        lines = []
        
        delta = end - start
        total_distance = delta.length()
        
        if total_distance < 0.001:
            return lines
        
        direction = delta.normalized()

        # Convert screen-pixel sizes to network units
        scaled_dash = self._editor.lengthFromScreen(dash_length)
        scaled_gap = self._editor.lengthFromScreen(gap_length)
        
        current_dist = 0.0
        while current_dist < total_distance:
            # Dash start point
            dash_start = start + direction * current_dist
            
            # Dash end point (clamped to not exceed cursor)
            dash_end_dist = min(current_dist + scaled_dash, total_distance)
            dash_end = start + direction * dash_end_dist
            
            lines.append(hou.NetworkShapeLine(
                dash_start,
                dash_end,
                color,
                alpha=alpha,
                width=width,
                screen_space=False,
                smooth=True,
                dashed=False,
            ))
            
            # Move past this dash and the gap
            current_dist = dash_end_dist + scaled_gap
    
        return lines

    def _make_arrow(self, position, direction_angle, length=0.3, tip_size=0.1, color=hou.Color(1, 1, 1), alpha=0.8, width=2.0):
        """Create an arrow: stem + two lines at 45° forming arrowhead."""
        stem_end = hou.Vector2(
            position[0] + math.cos(direction_angle) * length,
            position[1] + math.sin(direction_angle) * length
        )
        
        left_angle = direction_angle + math.pi * 0.75
        right_angle = direction_angle - math.pi * 0.75
        
        left = hou.Vector2(
            stem_end[0] + math.cos(left_angle) * tip_size,
            stem_end[1] + math.sin(left_angle) * tip_size
        )
        right = hou.Vector2(
            stem_end[0] + math.cos(right_angle) * tip_size,
            stem_end[1] + math.sin(right_angle) * tip_size
        )
        
        return [
            hou.NetworkShapeLine(position, stem_end, color, alpha, width, screen_space=False, dashed=False),
            hou.NetworkShapeLine(stem_end, left, color, alpha, width, screen_space=False, dashed=False),
            hou.NetworkShapeLine(stem_end, right, color, alpha, width, screen_space=False, dashed=False),
        ]

    def _make_rotate_arrows(self, center, cursor, offset=1.25, length=2, tip_size=1.3, color=hou.Color(1, 1, 1), alpha=1, width=6.0):
        """Create two arrows at cursor, pointing in tangent directions."""
        dx = cursor[0] - center[0]
        dy = cursor[1] - center[1]
        dist = math.sqrt(dx * dx + dy * dy)
        
        if dist < 0.001:
            return []
        
        scale = self._editor.lengthFromScreen(100) / 10.0 * self._ui_scale
        scaled_offset = offset * scale
        scaled_length = length * scale
        scaled_tip = tip_size * scale
        
        tangent_angle = math.atan2(dx, -dy)
        tangent_x = math.cos(tangent_angle)
        tangent_y = math.sin(tangent_angle)
        
        pos1 = hou.Vector2(cursor[0] + tangent_x * scaled_offset, cursor[1] + tangent_y * scaled_offset)
        pos2 = hou.Vector2(cursor[0] - tangent_x * scaled_offset, cursor[1] - tangent_y * scaled_offset)
        
        arrows1 = self._make_arrow(pos1, tangent_angle, scaled_length, scaled_tip, color, alpha, width)
        arrows2 = self._make_arrow(pos2, tangent_angle + math.pi, scaled_length, scaled_tip, color, alpha, width)
        
        return arrows1 + arrows2

    def _make_scale_arrows(self, center, cursor, offset=1.25, length=2, tip_size=1.3, color=hou.Color(1, 1, 1), alpha=1, width=6.0):
        """Create two arrows at cursor, pointing toward and away from center."""
        dx = cursor[0] - center[0]
        dy = cursor[1] - center[1]
        dist = math.sqrt(dx * dx + dy * dy)
        
        if dist < 0.001:
            return []
        
        scale = self._editor.lengthFromScreen(100) / 10.0 * self._ui_scale
        scaled_offset = offset * scale
        scaled_length = length * scale
        scaled_tip = tip_size * scale
        
        normal_angle = math.atan2(dy, dx)
        normal_x = math.cos(normal_angle)
        normal_y = math.sin(normal_angle)
        
        pos1 = hou.Vector2(cursor[0] + normal_x * scaled_offset, cursor[1] + normal_y * scaled_offset)
        pos2 = hou.Vector2(cursor[0] - normal_x * scaled_offset, cursor[1] - normal_y * scaled_offset)
        
        arrows1 = self._make_arrow(pos1, normal_angle, scaled_length, scaled_tip, color, alpha, width)
        arrows2 = self._make_arrow(pos2, normal_angle + math.pi, scaled_length, scaled_tip, color, alpha, width)
        
        return arrows1 + arrows2
    
    def _make_grab_arrows(self, cursor, offset=0.15, size=0.25, color=hou.Color(1, 1, 1), alpha=1, width=4.0):
        """Create four arrows pointing in cardinal directions."""
        arrows = []
        for angle in [0, math.pi/2, math.pi, -math.pi/2]:  # right, up, left, down
            pos = hou.Vector2(
                cursor[0] + math.cos(angle) * offset,
                cursor[1] + math.sin(angle) * offset
            )
            arrows.extend(self._make_arrow(pos, angle, size, color, alpha, width))
        return arrows

    def _create_rotation_indicator(self, center, radius, size=15.0, color=hou.Color(1, 1, 1), alpha=1, width=2.0):
        lines = []
        
        radius_net = self._editor.lengthFromScreen(radius)
        size_net = self._editor.lengthFromScreen(size)
        
        arm = size_net * 0.5
        
        for i in range(4):
            angle = math.radians(i * 90)
            
            # Chevron position on circle
            cx = center.x() + radius_net * math.cos(angle)
            cy = center.y() + radius_net * math.sin(angle)
            
            # Tip points outward (radial direction)
            tip = hou.Vector2(cx + arm * math.cos(angle),
                            cy + arm * math.sin(angle))
            
            # Two arms perpendicular to radial (along tangent)
            perp = angle + math.pi / 2
            
            end1 = hou.Vector2(cx + arm * math.cos(perp),
                            cy + arm * math.sin(perp))
            end2 = hou.Vector2(cx - arm * math.cos(perp),
                            cy - arm * math.sin(perp))
            
            lines.append(hou.NetworkShapeLine(end1, tip, color, alpha=alpha, width=width,
                                            screen_space=False, smooth=True, dashed=False))
            lines.append(hou.NetworkShapeLine(end2, tip, color, alpha=alpha, width=width,
                                            screen_space=False, smooth=True, dashed=False))
        
        return lines
    

    def _find_nearby_connection(self, node, radius=0.25):
        """Find closest wire near node center. radius=network units."""
        # pos = node.position()
        # size = node.size()
        # center = pos + hou.Vector2(size[0] / 2, size[1] / 2)
        center = self._editor.itemRect(node, False).center()
        
        screen_center = self._editor.posToScreen(center)
        screen_radius = self._editor.lengthToScreen(radius)
        
        pos1 = hou.Vector2(screen_center[0] - screen_radius, screen_center[1] - screen_radius)
        pos2 = hou.Vector2(screen_center[0] + screen_radius, screen_center[1] + screen_radius)
        
        items = self._editor.networkItemsInBox(pos1, pos2, for_drop=True)
        
        for item, part, index in items:
            if isinstance(item, hou.NodeConnection):
                if item.inputItem() == node or item.outputItem() == node:
                    continue
                return item
        
        return None
    

    def _find_centerline_snaps(self, dragged_rect, alignrects):
        """Find snaps to midpoints between pairs of visible nodes."""

        if not prefs.doSnapping(self._editor):
            return hou.Vector2(0, 0), []
        
        threshold = graphutils.getSnapRadius(self._editor)
        min_pair_distance = threshold * 10  # Only snap to centerlines of nodes this far apart
        dragged_center = dragged_rect.center()
        
        # Collect centers from alignrects (list of (item, rect) tuples)
        centers = [rect.center() for item, rect in alignrects]
        
        if len(centers) < 2:
            return hou.Vector2(0, 0), []
        
        best_x = None  # (distance, snap_x, center1, center2)
        best_y = None
        
        for i, c1 in enumerate(centers):
            for c2 in centers[i+1:]:

                # Skip pairs that are too close together
                pair_distance = (c1 - c2).length()
                if pair_distance < min_pair_distance:
                    continue

                mid_x = (c1.x() + c2.x()) * 0.5
                mid_y = (c1.y() + c2.y()) * 0.5
                
                dx = abs(dragged_center.x() - mid_x)
                dy = abs(dragged_center.y() - mid_y)
                
                if dx < threshold and (best_x is None or dx < best_x[0]):
                    best_x = (dx, mid_x, c1, c2)
                if dy < threshold and (best_y is None or dy < best_y[0]):
                    best_y = (dy, mid_y, c1, c2)
        
        # Calculate delta and build shapes
        delta = hou.Vector2(0, 0)
        shapes = []
        #color = hou.ui.colorFromName('GraphSnapLine')
        color = hou.Color(0.5, 0.35, 0.3)  # Light red
        zero = hou.Vector2(0,0)
        
        if best_x:
            delta = hou.Vector2(best_x[1] - dragged_center.x(), delta.y())
            c1, c2 = best_x[2], best_x[3]
            snapped_center = hou.Vector2(best_x[1], dragged_center.y())
            min_y = min(c1.y(), c2.y(), snapped_center.y())
            max_y = max(c1.y(), c2.y(), snapped_center.y())
            shapes.append(hou.NetworkShapeConnection(
                hou.Vector2(best_x[1], min_y), zero,
                hou.Vector2(best_x[1], max_y), zero,
                color, fade_factor=1.0, smooth=False, dashed=True
            ))

        if best_y:
            delta = hou.Vector2(delta.x(), best_y[1] - dragged_center.y())
            c1, c2 = best_y[2], best_y[3]
            snapped_center = hou.Vector2(dragged_center.x() + delta.x(), best_y[1])
            min_x = min(c1.x(), c2.x(), snapped_center.x())
            max_x = max(c1.x(), c2.x(), snapped_center.x())
            shapes.append(hou.NetworkShapeConnection(
                hou.Vector2(min_x, best_y[1]), zero,
                hou.Vector2(max_x, best_y[1]), zero,
                color, fade_factor=1.0, smooth=False, dashed=True
            ))
        
        return delta, shapes
    

    def _draw_connection_preview(self, node, conn):
        """Draw preview wires: conn.inputNode -> node.input, node.output -> conn.outputNode"""
        
        shapes = []
        
        # Wire from connection's source node to grabbed node's input
        if graphutils.getMaxNumInputs(node) > 0:
            shapes.append(hou.NetworkShapeConnection(
                self._editor.itemOutputPos(conn.inputItem(), conn.inputItemOutputIndex()),
                self._editor.itemOutputDir(conn.inputItem(), conn.inputItemOutputIndex()),
                self._editor.itemInputPos(node, 0),
                self._editor.itemInputDir(node, 0),
                color=hou.Color(1, 1, 1),
                alpha=1,
                dashed=True
            ))
        
        # Wire from grabbed node's output to connection's destination node
        if graphutils.getMaxNumOutputs(node) > 0:
            shapes.append(hou.NetworkShapeConnection(
                self._editor.itemOutputPos(node, 0),
                self._editor.itemOutputDir(node, 0),
                self._editor.itemInputPos(conn.outputItem(), conn.inputIndex()),
                self._editor.itemInputDir(conn.outputItem(), conn.inputIndex()),
                color=hou.Color(1, 1, 1),
                alpha=1,
                dashed=True
            ))
        
        return shapes
    
