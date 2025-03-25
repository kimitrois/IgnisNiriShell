import urllib.parse
from typing import Any, Callable
from gi.repository import Adw, Gio, GLib, Gtk
from ignis.app import IgnisApp
from ignis.widgets import Widget
from ignis.services.audio import AudioService, Stream
from ignis.services.notifications import Notification, NotificationAction, NotificationService
from ignis.services.recorder import RecorderService
from ignis.utils.thread import run_in_thread
from ignis.utils.timeout import Timeout
from .backdrop import overlay_window
from .constants import AudioStreamType, WindowName
from .template import gtk_template, gtk_template_callback, gtk_template_child
from .utils import connect_window, set_on_click


app = IgnisApp.get_default()


class RevealerBox(Widget.Box):
    __gtype_name__ = "IgnisRevealerBox"

    def __init__(
        self,
        caption: Gtk.Widget,
        content: Gtk.Widget,
        arrow: Widget.Arrow,
        on_toggle: Callable[[bool], Any] | None = None,
        **kwargs,
    ):
        self.__revealer = Widget.Revealer(child=content)
        self.__arrow = arrow
        self.__on_toggle = on_toggle
        super().__init__(child=[caption, self.__revealer], **kwargs)
        set_on_click(caption, left=self.__on_caption_click)

    def __on_caption_click(self, _):
        self.__arrow.toggle()
        reveal: bool = self.__arrow.get_rotated()
        self.__revealer.set_reveal_child(reveal)
        if self.__on_toggle:
            self.__on_toggle(reveal)


@gtk_template("controlcenter/audio-group")
class AudioControlGroup(Gtk.Box):
    __gtype_name__ = "AudioControlGroup"

    caption: Gtk.Box = gtk_template_child()
    icon: Gtk.Image = gtk_template_child()
    scale: Gtk.Scale = gtk_template_child()
    label: Gtk.Label = gtk_template_child()
    arrow: Gtk.Image = gtk_template_child()
    revealer: Gtk.Revealer = gtk_template_child()
    list_box: Gtk.ListBox = gtk_template_child()

    @gtk_template("controlcenter/audio-stream")
    class AudioControlStream(Gtk.ListBoxRow):
        __gtype_name__ = "AudioControlStream"

        icon: Gtk.Image = gtk_template_child()
        inscription: Gtk.Inscription = gtk_template_child()

        def __init__(self, stream: Stream, stream_type: AudioStreamType):
            self.__service = AudioService.get_default()
            self._stream = stream
            self._default = stream
            self._stream_type = stream_type
            super().__init__()

            match stream_type:
                case AudioStreamType.speaker:
                    self._default = self.__service.get_speaker()
                case AudioStreamType.microphone:
                    self._default = self.__service.get_microphone()

            stream.connect("notify::name", self.__on_stream_changed)
            stream.connect("notify::icon-name", self.__on_stream_changed)
            stream.connect("notify::is_default", self.__on_default_changed)
            self._default.connect("notify::id", self.__on_default_changed)
            set_on_click(self.icon, self.__on_mute_clicked)
            set_on_click(self, self.__on_clicked)

            self.__on_stream_changed()
            self.__on_default_changed()

        def __on_stream_changed(self, *_):
            icon: str = self._stream.get_icon_name()
            description: str = self._stream.get_description()
            self.icon.set_from_icon_name(icon)
            self.inscription.set_text(description)
            self.inscription.set_tooltip_text(description)

        def __on_default_changed(self, *_):
            if self._stream.get_id() == self._default.get_id():
                self.icon.add_css_class("accent")
            else:
                self.icon.remove_css_class("accent")

        def __on_mute_clicked(self, *_):
            self._stream.set_is_muted(not self._stream.get_is_muted())

        def __on_clicked(self, *_):
            match self._stream_type:
                case AudioStreamType.speaker:
                    self.__service.set_speaker(self._stream)
                case AudioStreamType.microphone:
                    self.__service.set_microphone(self._stream)

    def __init__(self, stream_type: AudioStreamType):
        self.__service = AudioService.get_default()
        self._stream_type = stream_type
        self._default: Stream | None = None
        self._streams = Gio.ListStore()

        super().__init__()
        self.list_box.bind_model(
            model=self._streams, create_widget_func=lambda item: self.AudioControlStream(item, self._stream_type)
        )

        set_on_click(self.icon, left=self.__on_mute_clicked)
        set_on_click(self.caption, left=self.__on_caption_clicked)
        connect_window(self, "notify::visible", self.__on_window_visible_change)

        match stream_type:
            case AudioStreamType.speaker:
                self._default = self.__service.get_speaker()
                self.__service.connect("speaker_added", self.__on_stream_added)
            case AudioStreamType.microphone:
                self._default = self.__service.get_microphone()
                self.__service.connect("microphone_added", self.__on_stream_added)

        if self._default is not None:
            self._default.connect("notify::description", self.__on_volume_changed)
            self._default.connect("notify::icon-name", self.__on_volume_changed)
            self._default.connect("notify::volume", self.__on_volume_changed)
            self.__on_volume_changed()

    def __on_window_visible_change(self, window: Widget.Window, _):
        if not window.get_visible():
            self.revealer.set_reveal_child(False)

    def __on_volume_changed(self, *_):
        if self._default is None:
            return

        description: str = self._default.get_description()
        if description != self.caption.get_tooltip_text():
            self.caption.set_tooltip_text(description)

        icon_name: str = self._default.get_icon_name()
        if icon_name != self.icon.get_icon_name():
            self.icon.set_from_icon_name(self._default.get_icon_name())

        volume: int = round(self._default.get_volume())
        if volume != round(self.scale.get_value()):
            self.scale.set_value(volume)

    def __on_stream_added(self, _, stream: Stream):
        self._streams.append(stream)

        def on_removed(stream: Stream):
            found, pos = self._streams.find(stream)
            if found:
                self._streams.remove(pos)

        stream.connect("removed", on_removed)

    def __on_mute_clicked(self, *_):
        if self._default is None:
            return

        self._default.set_is_muted(not self._default.get_is_muted())

    def __on_caption_clicked(self, *_):
        revealed = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(revealed)
        if revealed:
            self.arrow.add_css_class("rotate-icon-90")
        else:
            self.arrow.remove_css_class("rotate-icon-90")

    @gtk_template_callback
    def on_scale_value_changed(self, *_):
        if self._default is None:
            return

        volume = round(self.scale.get_value())
        self.label.set_label(f"{volume}")
        if volume != round(self._default.get_volume()):
            self._default.set_volume(volume)


class AudioControlGroupSpeaker(Gtk.Box):
    __gtype_name__ = "AudioControlGroupSpeaker"

    def __init__(self):
        super().__init__()
        self.append(AudioControlGroup(AudioStreamType.speaker))


class AudioControlGroupMicrophone(Gtk.Box):
    __gtype_name__ = "AudioControlGroupMicrophone"

    def __init__(self):
        super().__init__()
        self.append(AudioControlGroup(AudioStreamType.microphone))


@gtk_template("controlcenter/switchpill")
class ControlSwitchPill(Gtk.Box):
    __gtype_name__ = "ControlSwitchPill"

    pill: Gtk.Box = gtk_template_child()
    icon: Gtk.Image = gtk_template_child()
    title: Gtk.Label = gtk_template_child()
    subtitle: Gtk.Label = gtk_template_child()

    def __init__(self):
        super().__init__()


class ColorSchemeSwitcher(Gtk.Box):
    __gtype_name__ = "ColorSchemeSwitcher"

    def __init__(self):
        super().__init__()

        self.__pill = ControlSwitchPill()
        self.append(self.__pill)
        self.__pill.icon.set_from_icon_name("dark-mode-symbolic")
        self.__pill.title.set_label("Color Scheme")

        self.__color_scheme = "default"
        self.__color_scheme_range: list[str] = []
        self.__desktop_settings: Gio.Settings | None = None

        try:
            gs = Gio.Settings(schema_id="org.gnome.desktop.interface")
            self.__desktop_settings = gs

            schema: Gio.SettingsSchema = gs.get_property("settings-schema")
            rng: GLib.Variant = schema.get_key("color-scheme").get_range()
            assert rng.get_child_value(0).get_string() == "enum", "failed to get 'color-scheme' range"
            rng = rng.get_child_value(1).get_variant()
            self.__color_scheme_range = rng.get_strv()
            self.set_tooltip_text("/".join(self.__color_scheme_range))

            gs.connect("changed::color-scheme", self.__on_color_scheme_changed)
            self.connect("realize", self.__on_color_scheme_changed)
            set_on_click(
                self, left=lambda *_: self.__switch_color_scheme(1), right=lambda *_: self.__switch_color_scheme(-1)
            )

        except Exception as e:
            from loguru import logger

            logger.warning(f"failed to connect gsettings monitor: {e}")

    def __on_color_scheme_changed(self, *_):
        if self.__desktop_settings is None:
            return

        self.__color_scheme = self.__desktop_settings.get_string("color-scheme")
        self.__pill.subtitle.set_label(self.__color_scheme)

        if self.__color_scheme != "default":
            self.__pill.pill.add_css_class("accent")
        else:
            self.__pill.pill.remove_css_class("accent")

    def __switch_color_scheme(self, delta: int):
        if self.__desktop_settings is None:
            return

        current_index = self.__color_scheme_range.index(self.__color_scheme)
        value = self.__color_scheme_range[(current_index + delta) % len(self.__color_scheme_range)]
        niri_action("DoScreenTransition")
        self.__desktop_settings.set_string("color-scheme", value)


class IgnisRecorder(Gtk.Box):
    __gtype_name__ = "IgnisRecorder"

    def __init__(self):
        self.__service = RecorderService.get_default()
        super().__init__()

        self.__pill = ControlSwitchPill()
        self.append(self.__pill)
        self.__pill.title.set_label("Recorder")
        self.__pill.subtitle.set_label("screen recorder")

        self.__service.connect("notify::active", self.__on_status_changed)
        self.__service.connect("notify::is-paused", self.__on_status_changed)
        set_on_click(self, left=self.__on_clicked, right=self.__on_right_clicked)
        self.__on_status_changed()

    def __on_status_changed(self, *_):
        if self.__service.get_active():
            self.__pill.pill.add_css_class("accent")
            if self.__service.get_is_paused():
                self.__pill.pill.add_css_class("warning")
                self.__pill.icon.set_from_icon_name("media-playback-pause-symbolic")
            else:
                self.__pill.pill.remove_css_class("warning")
                self.__pill.icon.set_from_icon_name("media-playback-stop-symbolic")
        else:
            self.__pill.pill.remove_css_class("accent")
            self.__pill.pill.remove_css_class("warning")
            self.__pill.icon.set_from_icon_name("media-record-symbolic")

    @run_in_thread
    def __on_clicked(self, *_):
        if self.__service.get_active():
            if self.__service.get_is_paused():
                self.__service.continue_recording()
            else:
                self.__service.stop_recording()
        else:
            app.close_window(WindowName.control_center.value)
            self.__service.start_recording()

    def __on_right_clicked(self, *_):
        if self.__service.get_active():
            if self.__service.get_is_paused():
                self.__service.continue_recording()
            else:
                self.__service.pause_recording()


@gtk_template("controlcenter/notification-item")
class NotificationItem(Gtk.ListBoxRow):
    __gtype_name__ = "NotificationItem"

    revealer: Gtk.Revealer = gtk_template_child()
    action_row: Adw.ActionRow = gtk_template_child()
    icon: Widget.Icon = gtk_template_child()
    actions: Gtk.Box = gtk_template_child()

    def __init__(self, notify: Notification, is_popup: bool):
        self.notify_id = notify.id
        self._notification = notify
        self._is_popup = is_popup
        super().__init__()

        self.action_row.set_title(notify.get_summary())
        self.action_row.set_subtitle(notify.get_body())

        if notify.get_icon():
            icon = notify.get_icon()
            if icon.startswith("file://"):
                icon = urllib.parse.unquote(icon).removeprefix("file://")
            self.icon.set_image(icon)

        if len(notify.get_actions()) == 0:
            self.actions.set_visible(False)

        for action in notify.get_actions():
            action: NotificationAction
            button = Gtk.Button(label=action.get_label())
            button.connect("clicked", self.__on_action(action))
            self.actions.append(button)

        self.revealer.connect("notify::child-revealed", self.__on_child_revealed)
        self.connect("realize", self.__on_realized)
        set_on_click(self.action_row, left=self.__on_clicked, right=self.__on_right_clicked)

    def __on_realized(self, *_):
        Timeout(ms=0, target=lambda: self.revealer.set_reveal_child(True))

    def __on_child_revealed(self, *_):
        if self.revealer.get_reveal_child():
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        else:
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

    def __on_clicked(self, *_):
        if not self.revealer.get_reveal_child():
            return

        if self._is_popup:
            app.open_window(WindowName.control_center.value)

    def __on_right_clicked(self, *_):
        if not self.revealer.get_reveal_child():
            return

        if self._is_popup:
            self._notification.dismiss()
        else:
            self._notification.close()

    def __on_action(self, action: NotificationAction):
        def callback(_):
            action.invoke()
            app.close_window(WindowName.control_center.value)

        return callback


@gtk_template("controlcenter/notificationcenter")
class NotificationCenter(Gtk.Box):
    __gtype_name__ = "NotificationCenter"

    clear_all: Gtk.Button = gtk_template_child()
    stack: Gtk.Stack = gtk_template_child()
    list_box: Gtk.ListBox = gtk_template_child()

    def __init__(self):
        self.__service = NotificationService.get_default()
        super().__init__()
        self._notifications = Gio.ListStore()
        self.list_box.bind_model(model=self._notifications, create_widget_func=lambda i: i)

        self._notifications.connect("notify::n-items", self.__on_store_changed)
        self.__service.connect("notified", self.__on_notified)

        for notify in self.__service.get_notifications():
            self.__on_notified(self.__service, notify)
        self.__on_store_changed()

    def __on_store_changed(self, *_):
        if self._notifications.get_n_items() != 0:
            self.clear_all.set_sensitive(True)
            self.stack.set_visible_child_name("notifications")
        else:
            self.clear_all.set_sensitive(False)
            self.stack.set_visible_child_name("no-notifications")

    def __find_notify(self, notify: Notification):
        return self._notifications.find_with_equal_func(notify, lambda i, n: i.notify_id == n.id)

    def __on_notified(self, _, notify: Notification):
        item = NotificationItem(notify, False)
        self._notifications.insert(0, item)

        def on_closed(notify: Notification):
            found, pos = self.__find_notify(notify)
            if found:
                item: NotificationItem = self._notifications.get_item(pos)  # type: ignore

                def on_child_folded(*_):
                    found, pos = self.__find_notify(notify)
                    if found:
                        self._notifications.remove(pos)

                item.revealer.set_reveal_child(False)
                item.revealer.connect("notify::child-revealed", on_child_folded)

        notify.connect("closed", on_closed)

    @gtk_template_callback
    def on_clear_all_clicked(self, *_):
        self.__service.clear_all()


class NotificationPopups(Widget.RevealerWindow):
    __gtype_name__ = "IgnisNotificationPopups"

    @gtk_template("notificationpopups")
    class View(Gtk.Box):
        __gtype_name__ = "NotificationPopupsView"

        revealer: Widget.Revealer = gtk_template_child()
        list_box: Gtk.ListBox = gtk_template_child()

    def __init__(self):
        self.__service = NotificationService.get_default()
        super().__init__(
            namespace=WindowName.notification_popups.value,
            anchor=["top", "right"],
            visible=False,
            margin_top=48,
            margin_right=8,
            css_classes=["notification-popups"],
            revealer=Widget.Revealer(),
        )

        self.__view = self.View()
        self.set_child(self.__view)
        self.set_revealer(self.__view.revealer)

        self._popups = Gio.ListStore()
        self.__view.list_box.bind_model(model=self._popups, create_widget_func=lambda i: i)

        self._popups.connect("notify::n-items", self.__on_store_changed)
        self.__service.connect("new_popup", self.__on_new_popup)

    def __on_store_changed(self, *_):
        if self._popups.get_n_items() != 0:
            self.set_visible(True)
        else:
            self.set_visible(False)

    def __find_popup(self, popup: Notification):
        return self._popups.find_with_equal_func(popup, lambda i, p: i.notify_id == p.id)

    def __on_new_popup(self, _, popup: Notification):
        item = NotificationItem(popup, True)
        self._popups.insert(0, item)

        def on_dismissed(popup: Notification):
            found, pos = self.__find_popup(popup)
            if found:
                item: NotificationItem = self._popups.get_item(pos)  # type: ignore

                def on_child_folded(*_):
                    found, pos = self.__find_popup(popup)
                    if found:
                        self._popups.remove(pos)

                item.revealer.set_reveal_child(False)
                item.revealer.connect("notify::child-revealed", on_child_folded)

        popup.connect("dismissed", on_dismissed)


class ControlCenter(Widget.RevealerWindow):
    __gtype_name__ = "ControlCenter"

    @gtk_template("controlcenter")
    class View(Gtk.Box):
        __gtype_name__ = "ControlCenterView"

        revealer: Widget.Revealer = gtk_template_child()
        preferences_button: Gtk.Button = gtk_template_child()

        @gtk_template_callback
        def on_preferences_button_clicked(self, *_):
            app.close_window(WindowName.control_center.value)
            app.open_window(WindowName.preferences.value)

    def __init__(self):
        super().__init__(
            namespace=WindowName.control_center.value,
            kb_mode="exclusive",
            margin_top=48,
            margin_bottom=48,
            margin_end=8,
            anchor=["top", "right", "bottom"],
            layer="overlay",
            popup=True,
            visible=False,
            css_classes=[],
            revealer=Widget.Revealer(),
        )

        self.__view = self.View()
        self.set_child(self.__view)
        self.set_revealer(self.__view.revealer)
        self.connect("notify::visible", self.__on_visible_changed)

    def __on_visible_changed(self, *_):
        if self.get_visible():
            overlay_window.set_window(self.get_namespace())
        else:
            overlay_window.unset_window(self.get_namespace())
