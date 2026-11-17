# SPDX-License-Identifier: GPL-3.0-or-later
import time
import bpy
from . import inputs, session, hud, rig, phone, perf, optimize, collide, physcam, look, tune, i18n, updater, ai
from . import smooth, refly, model, pathviz
from .i18n import T, CTX


def EnumProperty(**kw):
    kw.setdefault("translation_context", CTX)
    return bpy.props.EnumProperty(**kw)


class _Op:
    bl_translation_context = CTX


def _view3d(context):
    if context.area and context.area.type == 'VIEW_3D':
        return context.area
    for a in context.window.screen.areas:
        if a.type == 'VIEW_3D':
            return a
    return None


def _window_region(area):
    if area is None:
        return None
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


_SPEED_KEYS = {'WHEELUPMOUSE': 1, 'WHEELDOWNMOUSE': -1, 'NUMPAD_PLUS': 1, 'NUMPAD_MINUS': -1, 'PAGE_UP': 1,
               'PAGE_DOWN': -1, 'PLUS': 1, 'MINUS': -1, 'EQUAL': 1}


def _redraw_ui(context):
    """Redraw only the sidebars (cheap) - never the heavy 3D viewport itself."""
    for w in context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                for r in a.regions:
                    if r.type == 'UI':
                        r.tag_redraw()


# ===================================================================================== fly
class FPV_OT_fly(_Op, bpy.types.Operator):
    """Fly the drone. ESC / right click: stop.  R: back to the start point.  Mouse wheel: speed"""
    bl_idname = "fpv.fly"
    bl_label = "Fly FPV"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None and not context.window_manager.fpv_state.flying

    def invoke(self, context, event):
        s = context.scene.fpv
        i18n.refresh()
        # the timeline must not play on its own: the flight drives the frame (two drivers = fights, and crashes
        # in some add-ons' frame handlers)
        try:
            if context.screen and context.screen.is_animation_playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
        except Exception:
            pass
        # other add-ons' frame / depsgraph handlers are paused first: the session start already changes the frame
        self.handlers = perf.HandlerGuard(getattr(s, "perf_pause_handlers", True))
        try:
            backend = inputs.open_backend(s.backend, s.device)
            self.sess = session.FlightSession(context, backend)
        except Exception as e:
            self.handlers.restore()
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        obj = s.drone
        obj["fpv_spawn_loc"] = list(self.sess.spawn_loc)
        obj["fpv_spawn_rot"] = list(self.sess.spawn_rot)
        self.area = _view3d(context)
        self.region = _window_region(self.area)
        if self.sess.floor_note:
            self.report({'WARNING'}, "FPV: " + T(self.sess.floor_note))
        if s.view_through and self.area:
            cam = self.sess.cam or (obj if obj.type == 'CAMERA' else None)
            if cam:
                context.scene.camera = cam
                r3d = self.area.spaces.active.region_3d
                r3d.view_perspective = 'CAMERA'
        self.is_kbd = isinstance(backend, inputs.KeyboardBackend)
        cam = self.sess.cam or (obj if obj.type == 'CAMERA' else None)
        self.guard = perf.PerfGuard(context, self.area, s, camera=cam)
        if self.sess.culler is not None:
            self.sess.culler.area = self.area
        hud.enable(self.sess)
        wm = context.window_manager
        wm.fpv_state.flying = True
        wm.fpv_state.monitoring = False
        wm.fpv_state.stop_request = False
        wm.fpv_state.reset_request = False
        # frame-locked pacing at the screen refresh rate (like playback: evenly spaced images)
        rate = perf.display_refresh_hz() if s.update_rate == 'DISPLAY' else float(s.update_rate)
        self.period = 1.0 / rate
        self.sess.period = self.period
        perf.precise_timers(True)
        self._timer = wm.event_timer_add(self.period, window=context.window)
        self.pacer = perf.Pacer(self.period, time.perf_counter())
        self.stats = perf.FrameStats()
        self.sess.stats = self.stats
        self.sess.area_ptr = self.area.as_pointer() if self.area else 0
        wm.modal_handler_add(self)
        self.t_last = time.perf_counter()
        self.fps_t, self.fps_n = self.t_last, 0
        context.workspace.status_text_set(
            T("FPV  |  ESC / Right-click: stop   R: reset to start   Wheel or + -: speed") +
            T("   Home / End: R2 power") +
            (T("   |   W/S throttle  A/D yaw  arrows pitch/roll  Shift full  Ctrl fine") if self.is_kbd else "") +
            (T("   |   F / click: fire") if self.sess.proj is not None else ""))
        if self.handlers.paused:
            self.report({'INFO'}, T("FPV: other add-ons paused while flying: %s") % ", ".join(self.handlers.owners()))
        return {'RUNNING_MODAL'}

    def _redraw(self):
        # only the 3D view's main region: tagging the whole area also redrew the sidebar, header and toolbar
        try:
            if self.region is not None:
                self.region.tag_redraw()
            elif self.area:
                self.area.tag_redraw()
        except ReferenceError:
            self.region = None

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._finish(context)
        if event.type == 'R' and event.value == 'PRESS':
            self.sess.event('reset')
            return {'RUNNING_MODAL'}
        if event.type in {'F', 'LEFTMOUSE'} and event.value == 'PRESS':
            if self.sess.proj is not None:
                self.sess.event('fire')
            return {'RUNNING_MODAL'}
        d = _SPEED_KEYS.get(event.type)
        if d is not None and event.value == 'PRESS':
            self.sess.change_boost(d)
            return {'RUNNING_MODAL'}
        if event.type in {'HOME', 'END'} and event.value == 'PRESS':
            self.sess.change_power(1 if event.type == 'HOME' else -1)
            return {'RUNNING_MODAL'}
        if self.is_kbd and self.sess.backend.event(event):
            return {'RUNNING_MODAL'}
        st = context.window_manager.fpv_state
        if st.stop_request:
            st.stop_request = False
            return self._finish(context)
        if st.reset_request:
            st.reset_request = False
            self.sess.event('reset')
        if event.type == 'TIMER':
            now = time.perf_counter()
            self.t_last = now
            dt = self.pacer.step(now)
            if dt <= 0.0:
                return {'RUNNING_MODAL'}
            if len(self.stats.ticks) < 200000:
                self.stats.ticks.append(now)
            try:
                msg = self.sess.tick(dt)
                self.sess.feedback()
            except Exception as e:
                msg = "FPV error: %s" % e
            if self.sess.want_stop:                       # Options button on the PS controller
                return self._finish(context)
            if len(self.stats.tick_cost) < 200000:
                self.stats.tick_cost.append((time.perf_counter() - now) * 1000.0)
            if msg:
                self.report({'WARNING'}, msg)
                return self._finish(context)
            if now - self.fps_t >= 0.5:                      # images really drawn per second
                nd = len(self.stats.draws)
                self.sess.ui_fps = (nd - self.fps_n) / (now - self.fps_t) if nd else 1.0 / self.period
                self.fps_t, self.fps_n = now, nd
                try:                                          # the timeline must not start playing on its own
                    if context.screen and context.screen.is_animation_playing:
                        bpy.ops.screen.animation_cancel(restore_frame=False)
                except Exception:
                    pass
            self._redraw()
            return {'RUNNING_MODAL'}
        if event.type.startswith('TIMER') or event.type in {'WINDOW_DEACTIVATE', 'NONE'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}          # swallow every other key / mouse event: no Blender shortcut while flying

    def _finish(self, context):
        wm = context.window_manager
        try:
            wm.event_timer_remove(self._timer)
        except Exception:
            pass
        perf.precise_timers(False)
        try:
            self.sess.finish()
        finally:
            try:
                self.guard.restore(context)
            except Exception:
                pass
            self.handlers.restore()                         # always: the other add-ons get their handlers back
        hud.disable()
        wm.fpv_state.flying = False
        context.workspace.status_text_set(None)
        try:
            r = self.stats.report(self.period)
            r.update(target_hz=1.0 / self.period, catchups=self.pacer.catchups, resyncs=self.pacer.resyncs,
                     double_ticks=self.pacer.skipped)
            txt = bpy.data.texts.get("FPV_Perf_Log") or bpy.data.texts.new("FPV_Perf_Log")
            txt.write("%s  %s\n" % (time.strftime("%H:%M:%S"), ", ".join("%s=%.2f" % kv for kv in sorted(r.items()))))
            if "draw_fps" in r:
                self.report({'INFO'}, T("FPV live: %.0f img/s (target %.0f), image interval %.1f ms median / %.1f ms worst, %d hitches")
                            % (r["draw_fps"], r["target_hz"], r["draw_med_ms"], r["draw_max_ms"], r["draw_hitches"]))
        except Exception:
            pass
        if self.sess.take_error:
            self.report({'ERROR'}, T("FPV: the take could not be written: %s") % self.sess.take_error)
        elif self.sess.record and self.sess.frames_recorded:
            self.report({'INFO'}, T("FPV take recorded: %d frames (%d -> %d), top speed %.0f km/h") % (
                self.sess.frames_recorded, self.sess.frame0, self.sess.frame - 1, self.sess.max_speed * 3.6))
            if getattr(context.scene.fpv, "auto_smooth", False):
                try:
                    bpy.ops.fpv.smooth_take()
                except Exception as e:
                    self.report({'WARNING'}, "FPV: %s" % e)
            kw = getattr(self.sess, "keys_written", None)
            if kw:
                self.report({'INFO'}, T("Editable curve: %d position keys, %d rotation keys, %d camera keys") % kw)
        try:
            if self.area:
                self.area.tag_redraw()
        except ReferenceError:
            pass
        return {'FINISHED'}

    def cancel(self, context):
        try:
            self._finish(context)
        except Exception:
            pass


# ===================================================================================== rig / utilities
class FPV_OT_create_rig(_Op, bpy.types.Operator):
    """Create an FPV quad rig (body empty + uptilted camera + proxy) at the 3D cursor"""
    bl_idname = "fpv.create_rig"
    bl_label = "Create FPV Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        body, cam = rig.create_rig(context, context.scene.fpv)
        for o in context.selected_objects:
            o.select_set(False)
        body.select_set(True)
        context.view_layer.objects.active = body
        self.report({'INFO'}, T("FPV rig created: %s / %s") % (body.name, cam.name))
        return {'FINISHED'}


class FPV_OT_reset(_Op, bpy.types.Operator):
    """Put the drone back where the last flight started"""
    bl_idname = "fpv.reset"
    bl_label = "Back to Start Point"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        d = context.scene.fpv.drone
        return d is not None and "fpv_spawn_loc" in d

    def execute(self, context):
        d = context.scene.fpv.drone
        d.location = d["fpv_spawn_loc"]
        d.rotation_mode = 'QUATERNION'
        d.rotation_quaternion = d["fpv_spawn_rot"]
        return {'FINISHED'}


class FPV_OT_clear_take(_Op, bpy.types.Operator):
    """Delete the drone keyframes from the recording start frame onward"""
    bl_idname = "fpv.clear_take"
    bl_label = "Clear Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None

    def execute(self, context):
        s = context.scene.fpv
        n = session.delete_keys_from(s.drone, s.record_start, {"location", "rotation_quaternion", "rotation_euler"})
        cam = rig.find_rig_camera(s.drone)
        if cam:
            n += session.delete_keys_from(cam, s.record_start, {"rotation_euler"})
        self.report({'INFO'}, T("%d keys removed") % n)
        return {'FINISHED'}


class FPV_OT_start_here(_Op, bpy.types.Operator):
    """Use the current frame as recording start"""
    bl_idname = "fpv.start_here"
    bl_label = "Current Frame"

    def execute(self, context):
        context.scene.fpv.record_start = context.scene.frame_current
        return {'FINISHED'}


PRESETS = {
    'RADIO': dict(backend='WINMM', axis_roll=0, axis_pitch=1, axis_throttle=2, axis_yaw=3, inv_roll=False, inv_pitch=False,
                  inv_throttle=False, inv_yaw=False, throttle_range='FULL', flight_mode='ACRO',
                  throttle_mode='DIRECT', use_triggers=False),
    'XBOX': dict(backend='XINPUT', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, inv_roll=False, inv_pitch=False,
                 inv_throttle=False, inv_yaw=False, throttle_range='UPPER', flight_mode='ANGLE',
                 throttle_mode='CLIMB', use_triggers=True, axis_forward=5, axis_backward=4),
    'PS': dict(backend='DS4', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, inv_roll=False, inv_pitch=False,
               inv_throttle=False, inv_yaw=False, throttle_range='UPPER', flight_mode='ANGLE',
               throttle_mode='CLIMB', use_triggers=True, axis_forward=5, axis_backward=4),
    'KEYBOARD': dict(backend='KEYBOARD', axis_roll=0, axis_pitch=1, axis_throttle=2, axis_yaw=3, inv_roll=False, inv_pitch=False,
                     inv_throttle=False, inv_yaw=False, throttle_range='FULL', flight_mode='ANGLE'),
}


class FPV_OT_preset(_Op, bpy.types.Operator):
    """Apply a controller mapping preset (Mode 2)"""
    bl_idname = "fpv.preset"
    bl_label = "Controller Preset"
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=[('RADIO', "RC Radio", "EdgeTX / OpenTX radio in USB joystick mode"),
                              ('XBOX', "Xbox Gamepad", ""), ('PS', "PS4 / PS5 Controller", ""),
                              ('KEYBOARD', "Keyboard", "")])

    def execute(self, context):
        s = context.scene.fpv
        for k, v in PRESETS[self.kind].items():
            setattr(s, k, v)
        return {'FINISHED'}


# ===================================================================================== live input monitor / learn
class FPV_OT_monitor(_Op, bpy.types.Operator):
    """Show the live controller axes in the panel (click again or ESC to stop)"""
    bl_idname = "fpv.monitor"
    bl_label = "Test Controller"

    def invoke(self, context, event):
        st = context.window_manager.fpv_state
        if st.monitoring:
            st.monitoring = False
            return {'FINISHED'}
        s = context.scene.fpv
        try:
            self.b = inputs.open_backend(s.backend, s.device)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if isinstance(self.b, inputs.KeyboardBackend):
            self.report({'INFO'}, T("Keyboard input: nothing to test, keys are W/S, A/D and the arrows"))
            return {'CANCELLED'}
        st.monitoring = True
        st.message = getattr(self.b, "label", "")
        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / 30.0, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        st = context.window_manager.fpv_state
        if not st.monitoring or (event.type == 'ESC' and event.value == 'PRESS') or st.flying:
            return self._stop(context)
        if event.type == 'TIMER':
            axes, _ = self.b.read()
            if axes is None:
                self.report({'WARNING'}, T("Controller disconnected"))
                return self._stop(context)
            st.live = axes[:8]
            _redraw_ui(context)
        return {'PASS_THROUGH'}

    def _stop(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.window_manager.fpv_state.monitoring = False
        self.b.close()
        _redraw_ui(context)
        return {'FINISHED'}


class FPV_OT_learn_axis(_Op, bpy.types.Operator):
    """Detect which controller axis drives this channel: click, then move only that stick"""
    bl_idname = "fpv.learn_axis"
    bl_label = "Learn Axis"
    channel: EnumProperty(items=[('throttle', "Throttle", ""), ('yaw', "Yaw", ""), ('pitch', "Pitch", ""), ('roll', "Roll", "")])

    HINT = {'throttle': "push THROTTLE fully UP", 'yaw': "push YAW fully RIGHT",
            'pitch': "push PITCH fully FORWARD (up)", 'roll': "push ROLL fully RIGHT"}

    def invoke(self, context, event):
        s = context.scene.fpv
        try:
            self.b = inputs.open_backend(s.backend, s.device)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if isinstance(self.b, inputs.KeyboardBackend):
            self.report({'INFO'}, T("Keyboard mapping is fixed"))
            return {'CANCELLED'}
        self.t0 = time.perf_counter()
        self.base = None
        self.samples = []
        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / 60.0, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(T("FPV Learn %s: keep sticks still, then %s  (ESC cancel)") % (
            T(self.channel.capitalize()).upper(), T(self.HINT[self.channel])))
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._end(context, None)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        axes, _ = self.b.read()
        if axes is None:
            return self._end(context, T("Controller disconnected"))
        t = time.perf_counter() - self.t0
        if t < 0.5:
            self.samples.append(axes)
            return {'RUNNING_MODAL'}
        if self.base is None:
            n = len(self.samples) or 1
            self.base = [sum(a[i] for a in self.samples) / n for i in range(8)]
        deltas = [axes[i] - self.base[i] for i in range(8)]
        i = max(range(8), key=lambda k: abs(deltas[k]))
        if abs(deltas[i]) > 0.6:
            s = context.scene.fpv
            setattr(s, "axis_" + self.channel, i)
            setattr(s, "inv_" + self.channel, deltas[i] < 0)
            return self._end(context, None, T("%s = axis %d%s") % (T(self.channel.capitalize()), i,
                                                                  T(" (inverted)") if deltas[i] < 0 else ""))
        if t > 10.0:
            return self._end(context, T("No stick movement detected"))
        return {'RUNNING_MODAL'}

    def _end(self, context, err, ok=None):
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        self.b.close()
        if err:
            self.report({'WARNING'}, err)
        if ok:
            self.report({'INFO'}, ok)
        _redraw_ui(context)
        return {'FINISHED'} if ok else {'CANCELLED'}


# ===================================================================================== phone link
def _phone_idle_telemetry():
    if phone.SERVER is None:
        return
    sc = bpy.context.scene
    s = sc.fpv
    phone.SERVER.state.telemetry = {"flying": False, "state": "IDLE", "mode": s.flight_mode, "rec": s.record,
                                     "frame": sc.frame_current, "speed": 0, "alt": 0, "crash": False,
                                     "boost": round(s.speed_boost, 2)}


def _start_flight_from_phone():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                with bpy.context.temp_override(window=win, area=area, region=region):
                    s = bpy.context.scene.fpv
                    if s.backend not in ('PHONE', 'AUTO'):
                        s.backend = 'PHONE'
                    if s.drone is None:
                        rig.create_rig(bpy.context, s)
                    bpy.ops.fpv.fly('INVOKE_DEFAULT')
                return


def phone_pump():
    """Main-thread timer: executes the phone buttons (Blender data must not be touched from the server thread)."""
    if phone.SERVER is None:
        return None
    wm = bpy.context.window_manager
    st = wm.fpv_state
    q = phone.SERVER.state.commands
    while q:
        cmd = q.popleft()
        s = bpy.context.scene.fpv
        if cmd == 'fly' and not st.flying:
            try:
                _start_flight_from_phone()
            except Exception as e:
                print("FPV phone: cannot start flight:", e)
        elif cmd == 'stop' and st.flying:
            st.stop_request = True
        elif cmd == 'reset' and st.flying:
            st.reset_request = True
        elif cmd == 'mode':
            s.flight_mode = 'ANGLE' if s.flight_mode == 'ACRO' else 'ACRO'
        elif cmd == 'motion':
            s.flight_mode = 'MOTION'
            if s.backend not in ('PHONE', 'AUTO'):
                s.backend = 'PHONE'
        elif cmd == 'sticks':
            if s.flight_mode == 'MOTION':
                s.flight_mode = 'ANGLE'
        elif cmd in ('recenter', 'fire'):
            sess = hud.STATE.get("session")
            if sess is not None:
                sess.event(cmd)
        elif cmd == 'rec' and not st.flying:
            s.record = not s.record
        elif cmd in ('speed+', 'speed-', 'speed1'):
            sess = hud.STATE.get("session")
            if cmd == 'speed1':
                s.speed_boost = 1.0
            elif sess is not None:
                sess.change_boost(1 if cmd == 'speed+' else -1)
            else:
                from . import flight
                s.speed_boost = flight.boost_step(s.speed_boost, 1 if cmd == 'speed+' else -1)
    if not st.flying:
        _phone_idle_telemetry()
        # redraw only when the QR / connection badge changes (no constant redraw of a heavy viewport)
        key = (phone.SERVER.connected, int(phone.SERVER.state.latency_ms / 10))
        if key != getattr(phone_pump, "_last", None):
            phone_pump._last = key
            for w in wm.windows:
                for a in w.screen.areas:
                    if a.type == 'VIEW_3D':
                        a.tag_redraw()
            _redraw_ui(bpy.context)
    return 0.1


class FPV_OT_phone_link(_Op, bpy.types.Operator):
    """Start / stop the phone link: open the link (or scan the QR code) on a phone connected to the same Wi-Fi"""
    bl_idname = "fpv.phone_link"
    bl_label = "Phone Link"

    def execute(self, context):
        if phone.SERVER is not None:
            phone.stop()
            hud.phone_overlay(False)
            self.report({'INFO'}, T("Phone link stopped"))
            return {'FINISHED'}
        s = context.scene.fpv
        try:
            try:
                cert_dir = bpy.utils.extension_path_user(__package__, path="tls", create=True)
            except Exception:
                import tempfile, os
                cert_dir = os.path.join(tempfile.gettempdir(), "fpv_drone_tls")
            srv = phone.start(s.phone_port, cert_dir)
        except OSError as e:
            self.report({'ERROR'}, T("Cannot open port %d (%s). Try another port.") % (s.phone_port, e))
            return {'CANCELLED'}
        if not bpy.app.timers.is_registered(phone_pump):
            bpy.app.timers.register(phone_pump, first_interval=0.1, persistent=True)
        hud.phone_overlay(True)
        if srv.tls is None:
            self.report({'WARNING'}, T("HTTPS unavailable (%s): sticks only, no tilt mode") % srv.tls_error)
        self.report({'INFO'}, T("Phone link: ") + srv.urls()[0])
        return {'FINISHED'}


class FPV_OT_phone_copy(_Op, bpy.types.Operator):
    """Copy the phone link to the clipboard (send it to your phone)"""
    bl_idname = "fpv.phone_copy"
    bl_label = "Copy Link"
    index: bpy.props.IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return phone.SERVER is not None

    def execute(self, context):
        urls = phone.SERVER.urls()
        context.window_manager.clipboard = urls[min(self.index, len(urls) - 1)]
        self.report({'INFO'}, T("Link copied"))
        return {'FINISHED'}


class FPV_OT_ai_link(_Op, bpy.types.Operator):
    """Start / stop the AI Link: lets Claude fly the drone through the FPV Drone MCP server (this PC only)"""
    bl_idname = "fpv.ai_link"
    bl_label = "AI Link"

    def execute(self, context):
        if ai.SERVER is not None:
            ai.stop()
            self.report({'INFO'}, T("AI Link stopped"))
        else:
            p = context.preferences.addons[__package__].preferences
            try:
                ai.start(getattr(p, "ai_port", ai.DEFAULT_PORT))
            except OSError as e:
                self.report({'ERROR'}, T("Cannot open port %d (%s). Try another port.") % (p.ai_port, e))
                return {'CANCELLED'}
            self.report({'INFO'}, T("AI Link ready: Claude can fly the drone"))
        _redraw_ui(context)
        return {'FINISHED'}


class FPV_OT_ai_copy(_Op, bpy.types.Operator):
    """Copy the setup for Claude to the clipboard"""
    bl_idname = "fpv.ai_copy"
    bl_label = "Copy"
    what: bpy.props.EnumProperty(items=[('CODE', "Claude Code", "Command that adds the MCP server to Claude Code"),
                                        ('DESKTOP', "Claude Desktop", "Block for claude_desktop_config.json")])

    def execute(self, context):
        context.window_manager.clipboard = ai.claude_command() if self.what == 'CODE' else ai.desktop_config()
        self.report({'INFO'}, T("Copied: paste it in a terminal") if self.what == 'CODE'
                    else T("Copied: paste it in claude_desktop_config.json"))
        return {'FINISHED'}


class FPV_OT_optimize(_Op, bpy.types.Operator):
    """Split very heavy meshes into tiles so Blender only draws what the drone camera sees (reversible)"""
    bl_idname = "fpv.optimize_scene"
    bl_label = "Optimize Scene for Flight"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.fpv
        t = time.perf_counter()
        rep = optimize.optimize(context, s.opt_min_faces, s.opt_tile_size, s.meters_per_unit)
        collide.clear_cache()
        if not rep:
            self.report({'INFO'}, T("Nothing to optimize (no visible mesh above %d faces, or already done)") % s.opt_min_faces)
            return {'FINISHED'}
        msg = ", ".join(T("%s: %dk faces -> %d tiles%s") % (m, f // 1000, n, (T(" x%d objects") % k) if k > 1 else "")
                        for m, f, n, k in rep)
        self.report({'INFO'}, T("Optimized in %.1fs: %s") % (time.perf_counter() - t, msg))
        return {'FINISHED'}


class FPV_OT_restore(_Op, bpy.types.Operator):
    """Remove the flight tiles and bring the original meshes back"""
    bl_idname = "fpv.restore_scene"
    bl_label = "Restore Original Meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n, k = optimize.restore(context)
        collide.clear_cache()
        self.report({'INFO'}, T("Removed %d tiles, restored %d objects") % (n, k))
        return {'FINISHED'}


# ===================================================================================== physics cam
class FPV_OT_physcam_bake(_Op, bpy.types.Operator):
    """Simulate the drone chasing the target (Polyfjord-style physics FPV) and bake one key per frame"""
    bl_idname = "fpv.physcam_bake"
    bl_label = "Bake Physics Cam"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = context.scene.fpv
        return s.drone is not None and s.pc_target is not None and not context.window_manager.fpv_state.flying

    def execute(self, context):
        import time as _t
        t = _t.perf_counter()
        try:
            r = physcam.bake(context)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        self.report({'INFO'}, T("Physics Cam baked: %d frames in %.1fs, top %.0f km/h, max lag %.1f m, %d impacts") % (
            r["frames"], _t.perf_counter() - t, r["top_speed"] * 3.6, r["max_lag"], r["impacts"]))
        return {'FINISHED'}


class FPV_OT_target_from_curve(_Op, bpy.types.Operator):
    """Create an animated target that travels along the selected curve over the scene frame range"""
    bl_idname = "fpv.target_from_curve"
    bl_label = "Target from Curve"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'CURVE'

    def execute(self, context):
        try:
            ob, speed = physcam.target_from_curve(context, context.active_object)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        self.report({'INFO'}, T("Target %s created: average %.0f km/h") % (ob.name, speed * 3.6))
        return {'FINISHED'}


class FPV_OT_target_from_take(_Op, bpy.types.Operator):
    """Turn the flight you recorded into a target: the bake re-flies it with clean physics"""
    bl_idname = "fpv.target_from_take"
    bl_label = "Target from My Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None

    def execute(self, context):
        try:
            ob = physcam.target_from_take(context)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        self.report({'INFO'}, T("Target %s created from the take") % ob.name)
        return {'FINISHED'}


class FPV_OT_target_new(_Op, bpy.types.Operator):
    """Create an empty target at the drone, with a first key: move it and key it along the shot"""
    bl_idname = "fpv.target_new"
    bl_label = "New Target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.fpv
        ob = physcam._new_target(context, "FPV_Target")
        ob.location = s.drone.location if s.drone else context.scene.cursor.location
        ob.keyframe_insert("location", frame=context.scene.frame_start)
        s.pc_target = ob
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        self.report({'INFO'}, T("Target created: move it and press I to key it along the shot"))
        return {'FINISHED'}


class FPV_OT_look_apply(_Op, bpy.types.Operator):
    """FPV action-cam finish: motion blur, wide-angle distortion, chromatic aberration, bloom, vignette (compositor)"""
    bl_idname = "fpv.look_apply"
    bl_label = "Apply FPV Look"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            look.apply(context)
        except Exception as e:
            self.report({'ERROR'}, "FPV Look: %s" % e)
            return {'CANCELLED'}
        self.report({'INFO'}, T("FPV Look on (compositor group '%s'). Visible in the render / camera view with the compositor")
                    % look.GROUP)
        return {'FINISHED'}


class FPV_OT_look_remove(_Op, bpy.types.Operator):
    """Remove the FPV Look and restore the previous compositor and motion blur settings"""
    bl_idname = "fpv.look_remove"
    bl_label = "Remove FPV Look"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        look.remove(context)
        return {'FINISHED'}


class FPV_OT_tune_blender(_Op, bpy.types.Operator):
    """Set Blender's preferences and this file's viewport for maximum performance (reversible)"""
    bl_idname = "fpv.tune_blender"
    bl_label = "Optimize Blender"
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            changed, restart = tune.apply(context)
        except Exception as e:
            self.report({'ERROR'}, "Optimize Blender: %s" % e)
            return {'CANCELLED'}
        if not changed:
            self.report({'INFO'}, T("Blender is already optimized"))
        else:
            self.report({'WARNING'} if restart else {'INFO'}, T("Optimized (%d settings)%s: %s") % (
                len(changed), T(" - RESTART Blender for the OpenGL backend") if restart else "",
                "; ".join(T(c) for c in changed)))
        return {'FINISHED'}


class FPV_OT_untune_blender(_Op, bpy.types.Operator):
    """Put back the preferences and viewport settings changed by 'Optimize Blender'"""
    bl_idname = "fpv.untune_blender"
    bl_label = "Restore Blender Settings"
    bl_options = {'REGISTER'}

    def execute(self, context):
        n = tune.restore(context)
        self.report({'INFO'}, T("%d settings restored") % n)
        return {'FINISHED'}


def simplify_take(context):
    """Re-write the drone's take (and its camera's) as editable Bezier keys. Returns the key counts."""
    from . import keyreduce
    s = context.scene.fpv
    obj = s.drone
    rng = smooth.take_range(obj, s.record_start)
    if rng is None:
        raise RuntimeError("No recorded take to smooth (record a flight first)")
    frames = list(range(rng[0], rng[1] + 1))
    tol_p = s.key_tol_pos / max(1e-6, s.meters_per_unit)
    locs, quats = keyreduce.sample_take(obj, frames)
    n_loc, n_rot = keyreduce.write_take(obj, frames, locs, quats, tol_p, s.key_tol_rot, s.key_max_gap)
    n_cam = 0
    cam = rig.find_rig_camera(obj)
    if cam is not None:
        _l, cq = keyreduce.sample_take(cam, frames)
        if cq is not None:
            _l, n_cam = keyreduce.write_take(cam, frames, None, cq, tol_p, s.key_tol_rot, s.key_max_gap)
    return len(frames), n_loc, n_rot, n_cam


class FPV_OT_path_edit(_Op, bpy.types.Operator):
    """Edit the path in the 3D viewport: every key becomes a sphere you move like an object (G), the animation follows live. Click again to finish"""
    bl_idname = "fpv.path_edit"
    bl_label = "Edit Path"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None and not context.window_manager.fpv_state.flying

    def execute(self, context):
        if pathviz.editing(context):
            pathviz.stop_edit(context)
            pathviz.tag_redraw()
            self.report({'INFO'}, T("Path edit finished"))
            return {'FINISHED'}
        try:
            n, _rng = pathviz.start_edit(context)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        context.scene.fpv.show_path = True
        for o in context.selected_objects:
            o.select_set(False)
        pathviz.tag_redraw()
        self.report({'INFO'}, T("%d key points: select a sphere and move it (G), the animation follows") % n)
        return {'FINISHED'}


class FPV_OT_simplify_take(_Op, bpy.types.Operator):
    """Turn the recorded take into an editable curve: a few Bezier keys where the motion changes (within the precision settings), rotation as X / Y / Z angles. Undoable"""
    bl_idname = "fpv.simplify_take"
    bl_label = "Simplify Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None and not context.window_manager.fpv_state.flying

    def execute(self, context):
        try:
            n, kl, kr, kc = simplify_take(context)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, T("Editable curve: %d frames -> %d position keys, %d rotation keys, %d camera keys")
                    % (n, kl, kr, kc))
        return {'FINISHED'}


class FPV_OT_smooth_take(_Op, bpy.types.Operator):
    """Smooth the recorded take for a cinematic move: calmer path and orientation, camera shake removed. Same timing, undoable"""
    bl_idname = "fpv.smooth_take"
    bl_label = "Smooth Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fpv.drone is not None and not context.window_manager.fpv_state.flying

    def execute(self, context):
        s = context.scene.fpv
        try:
            n = smooth.smooth_take(context, s.drone, rig.find_rig_camera(s.drone), s.record_start, s.smooth_pos,
                                   s.smooth_rot, s.smooth_cam)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        if s.record_keys == 'CURVE':                  # keep the take editable after smoothing
            try:
                simplify_take(context)
            except Exception as e:
                self.report({'WARNING'}, T(str(e)))
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, T("Take smoothed: %d frames") % n)
        return {'FINISHED'}


class FPV_OT_refly(_Op, bpy.types.Operator):
    """Re-fly the take from the recorded stick inputs with the CURRENT settings (drone type, rates, wind, speed, collisions...). Edit the inputs in the Graph Editor (FPV Inputs) to change the flight. Undoable"""
    bl_idname = "fpv.refly"
    bl_label = "Re-fly Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return refly.has_take(context.scene.fpv.drone) and not context.window_manager.fpv_state.flying

    def execute(self, context):
        try:
            r = refly.refly(context)
        except Exception as e:
            self.report({'ERROR'}, T(str(e)))
            return {'CANCELLED'}
        self.report({'INFO'}, T("Take re-flown: %d frames in %.1fs, top speed %.0f km/h") % (
            r["frames"], r["seconds"], r["top_speed"] * 3.6))
        return {'FINISHED'}


class FPV_OT_add_model(_Op, bpy.types.Operator):
    """Add (or rebuild) the detailed 3D quad on the drone rig, sized from Frame Size"""
    bl_idname = "fpv.add_model"
    bl_label = "Add Drone Model"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        d = context.scene.fpv.drone
        return d is not None and d.type == 'EMPTY' and not context.window_manager.fpv_state.flying

    def execute(self, context):
        s = context.scene.fpv
        objs = model.add_model(context, s.drone, s)
        for o in s.drone.children:
            if o.name.startswith("FPV_Drone_Proxy"):
                o.hide_set(True)
        self.report({'INFO'}, T("Drone model added (%d objects)") % len(objs))
        return {'FINISHED'}


class FPV_OT_add_course(_Op, bpy.types.Operator):
    """Add a practice course at the 3D cursor: a loop of gates, slalom pillars and a dive gap"""
    bl_idname = "fpv.add_course"
    bl_label = "Add Practice Course"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objs = model.add_course(context, context.scene.fpv)
        self.report({'INFO'}, T("Practice course added (%d objects, collection FPV_Course)") % len(objs))
        return {'FINISHED'}


class FPV_OT_language(_Op, bpy.types.Operator):
    """Switch the add-on language: Français / English"""
    bl_idname = "fpv.language"
    bl_label = "Language"
    lang: EnumProperty(items=[('FR', "Français", ""), ('EN', "English", ""), ('AUTO', "Auto", "")])

    def execute(self, context):
        if context.window_manager.fpv_state.flying:
            return {'CANCELLED'}
        i18n.set_language(self.lang)
        return {'FINISHED'}


class FPV_OT_update_check(_Op, bpy.types.Operator):
    """Look for a new version of FPV Drone on GitHub (releases)"""
    bl_idname = "fpv.update_check"
    bl_label = "Check for Updates"

    def execute(self, context):
        msg = updater.check(manual=True)
        if msg:
            self.report({'WARNING'}, T(msg))
        return {'FINISHED'}


class FPV_OT_update_install(_Op, bpy.types.Operator):
    """Download the new version from GitHub and install it (Blender reloads the add-on)"""
    bl_idname = "fpv.update_install"
    bl_label = "Install Update"

    @classmethod
    def poll(cls, context):
        return updater.state["status"] in ("available", "error") and updater.state.get("asset") is not None

    def execute(self, context):
        if context.window_manager.fpv_state.flying:
            self.report({'WARNING'}, T("Stop flying before updating"))
            return {'CANCELLED'}
        msg = updater.install()
        if msg:
            self.report({'WARNING'}, T(msg))
        return {'FINISHED'}


class FPV_OT_update_page(_Op, bpy.types.Operator):
    """Open the release page on GitHub (changes of the new version)"""
    bl_idname = "fpv.update_page"
    bl_label = "Release Notes"

    def execute(self, context):
        import webbrowser
        webbrowser.open(updater.state.get("page") or updater.repo_url())
        return {'FINISHED'}


classes = (FPV_OT_path_edit, FPV_OT_simplify_take, FPV_OT_smooth_take, FPV_OT_refly, FPV_OT_add_model, FPV_OT_add_course, FPV_OT_language, FPV_OT_update_check, FPV_OT_update_install, FPV_OT_update_page, FPV_OT_tune_blender, FPV_OT_untune_blender, FPV_OT_look_apply, FPV_OT_look_remove, FPV_OT_physcam_bake, FPV_OT_target_from_curve, FPV_OT_target_from_take, FPV_OT_target_new, FPV_OT_optimize, FPV_OT_restore, FPV_OT_phone_link, FPV_OT_phone_copy, FPV_OT_ai_link, FPV_OT_ai_copy, FPV_OT_fly, FPV_OT_create_rig, FPV_OT_reset, FPV_OT_clear_take, FPV_OT_start_here, FPV_OT_preset,
           FPV_OT_monitor, FPV_OT_learn_axis)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    phone.stop()
    if bpy.app.timers.is_registered(phone_pump):
        bpy.app.timers.unregister(phone_pump)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
