# This file is part of gkraken.
#
# Copyright (c) 2018 Roberto Leinardi
#
# gkraken is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# gkraken is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with gkraken.  If not, see <http://www.gnu.org/licenses/>.


import logging
import multiprocessing
import re
from typing import Optional, Any, List, Tuple, Dict, Callable

from gi.repository import Gtk
from injector import inject, singleton
from rx import Observable
from rx.concurrency import GtkScheduler, ThreadPoolScheduler
from rx.concurrency.schedulerbase import SchedulerBase
from rx.disposables import CompositeDisposable

from gkraken.conf import SETTINGS_DEFAULTS, APP_NAME, APP_SOURCE_URL
from gkraken.interactor import GetStatusInteractor, SetSpeedProfileInteractor, SettingsInteractor, \
    CheckNewVersionInteractor
from gkraken.model import Status, SpeedProfile, ChannelType, CurrentSpeedProfile, SpeedStep

LOG = logging.getLogger(__name__)
_ADD_NEW_PROFILE_INDEX = -10


class ViewInterface:
    def toggle_window_visibility(self) -> None:
        raise NotImplementedError()

    def refresh_status(self, status: Optional[Status]) -> None:
        raise NotImplementedError()

    def refresh_profile_combobox(self, channel: ChannelType, data: List[Tuple[int, str]],
                                 active: Optional[int]) -> None:
        raise NotImplementedError()

    def refresh_chart(self, profile: SpeedProfile) -> None:
        raise NotImplementedError()

    def set_apply_button_enabled(self, channel: ChannelType, enabled: bool) -> None:
        raise NotImplementedError()

    def set_edit_button_enabled(self, channel: ChannelType, enabled: bool) -> None:
        raise NotImplementedError()

    def set_statusbar_text(self, text: str) -> None:
        raise NotImplementedError()

    def refresh_settings(self, settings: Dict[str, Any]) -> None:
        raise NotImplementedError()

    def show_main_infobar_message(self, message: str, markup: bool = False) -> None:
        raise NotImplementedError()

    def show_add_speed_profile_dialog(self, channel: ChannelType) -> None:
        raise NotImplementedError()

    def show_fixed_speed_profile_popover(self, profile: SpeedProfile) -> None:
        raise NotImplementedError()

    def dismiss_and_get_value_fixed_speed_popover(self) -> Tuple[int, str]:
        raise NotImplementedError()

    def show_about_dialog(self) -> None:
        raise NotImplementedError()

    def show_settings_dialog(self) -> None:
        raise NotImplementedError()


@singleton
class Presenter:
    @inject
    def __init__(self,
                 get_status_interactor: GetStatusInteractor,
                 set_speed_profile_interactor: SetSpeedProfileInteractor,
                 settings_interactor: SettingsInteractor,
                 check_new_version_interactor: CheckNewVersionInteractor,
                 composite_disposable: CompositeDisposable,
                 ) -> None:
        LOG.debug("init Presenter ")
        self.view: ViewInterface = ViewInterface()
        self._scheduler: SchedulerBase = ThreadPoolScheduler(multiprocessing.cpu_count())
        self._get_status_interactor: GetStatusInteractor = get_status_interactor
        self._set_speed_profile_interactor: SetSpeedProfileInteractor = set_speed_profile_interactor
        self._settings_interactor = settings_interactor
        self._check_new_version_interactor = check_new_version_interactor
        self._composite_disposable: CompositeDisposable = composite_disposable
        self._profile_selected: Dict[str, SpeedProfile] = {}
        self._should_update_fan_speed: bool = False
        self.application_quit: Callable = lambda *args: None  # will be set by the Application

    def on_start(self) -> None:
        self._init_speed_profiles()
        self._init_settings()
        self._start_refresh()
        self._check_new_version()

    def _start_refresh(self) -> None:
        LOG.debug("start refresh")
        refresh_interval_ms = self._settings_interactor.get_int('settings_refresh_interval') * 1000
        self._composite_disposable \
            .add(Observable
                 .interval(refresh_interval_ms, scheduler=self._scheduler)
                 .start_with(0)
                 .subscribe_on(self._scheduler)
                 .flat_map(lambda _: self._get_status())
                 .observe_on(GtkScheduler())
                 .subscribe(on_next=self._update_status,
                            on_error=lambda e: LOG.exception("Refresh error: %s", str(e)))
                 )

    def _update_status(self, status: Optional[Status]) -> None:
        if status is not None:
            if self._should_update_fan_speed:
                last_applied: CurrentSpeedProfile = CurrentSpeedProfile.get_or_none(channel=ChannelType.FAN.value)
                if last_applied is not None:
                    duties = [i.duty for i in last_applied.profile.steps if status.liquid_temperature >= i.temperature]
                    if duties:
                        status.fan_speed = duties[-1]

            self.view.refresh_status(status)

    # def _load_last_profile(self) -> None:
    #     for current in CurrentSpeedProfile.select():

    @staticmethod
    def _get_profile_list(channel: ChannelType) -> List[Tuple[int, str]]:
        return [(p.id, p.name) for p in SpeedProfile.select().where(SpeedProfile.channel == channel.value)]

    def _init_speed_profiles(self) -> None:
        for channel in ChannelType:
            data = self._get_profile_list(channel)

            active = None
            if self._settings_interactor.get_bool('settings_load_last_profile'):
                self._should_update_fan_speed = True
                current: CurrentSpeedProfile = CurrentSpeedProfile.get_or_none(channel=channel.value)
                if current is not None:
                    active = next(i for i, item in enumerate(data) if item[0] == current.profile.id)
                    self._set_speed_profile(current.profile)

            data.append((_ADD_NEW_PROFILE_INDEX, "<span style='italic' alpha='50%'>Add new profile...</span>"))

            self.view.refresh_profile_combobox(channel, data, active)

    def _init_settings(self) -> None:
        settings: Dict[str, Any] = {}
        for key, default_value in SETTINGS_DEFAULTS.items():
            if isinstance(default_value, bool):
                settings[key] = self._settings_interactor.get_bool(key)
            elif isinstance(default_value, int):
                settings[key] = self._settings_interactor.get_int(key)
        self.view.refresh_settings(settings)

    def on_menu_settings_clicked(self, *_: Any) -> None:
        self.view.show_settings_dialog()

    def on_menu_about_clicked(self, *_: Any) -> None:
        self.view.show_about_dialog()

    def on_setting_changed(self, widget: Any, *args: Any) -> None:
        key = value = None
        if isinstance(widget, Gtk.Switch):
            value = args[0]
            key = re.sub('_switch$', '', widget.get_name())
        elif isinstance(widget, Gtk.SpinButton):
            key = re.sub('_spinbutton$', '', widget.get_name())
            value = widget.get_value_as_int()
        if key is not None and value is not None:
            self._settings_interactor.set_bool(key, value)

    def on_stack_visible_child_changed(self, *_: Any) -> None:
        pass

    def on_fan_profile_selected(self, widget: Any, *_: Any) -> None:
        profile_id = widget.get_model()[widget.get_active()][0]
        self._select_speed_profile(profile_id, ChannelType.FAN)

    def on_pump_profile_selected(self, widget: Any, *_: Any) -> None:
        profile_id = widget.get_model()[widget.get_active()][0]
        self._select_speed_profile(profile_id, ChannelType.PUMP)

    def on_quit_clicked(self, *_: Any) -> None:
        self.application_quit()

    def on_toggle_app_window_clicked(self, *_: Any) -> None:
        self.view.toggle_window_visibility()

    def _select_speed_profile(self, profile_id: int, channel: ChannelType) -> None:
        if profile_id == _ADD_NEW_PROFILE_INDEX:
            self.view.set_apply_button_enabled(channel, False)
            self.view.set_edit_button_enabled(channel, False)
            self.view.show_add_speed_profile_dialog(channel)
        else:
            profile: SpeedProfile = SpeedProfile.get(id=profile_id)
            self._profile_selected[profile.channel] = profile
            self.view.set_apply_button_enabled(channel, True)
            self.view.set_edit_button_enabled(channel, True)
            self.view.refresh_chart(profile)

    @staticmethod
    def _get_profile_data(profile: SpeedProfile) -> List[Tuple[int, int]]:
        return [(p.temperature, p.duty) for p in profile.steps]

    def on_fan_edit_button_clicked(self, *_: Any) -> None:
        self._on_edit_button_clicked(ChannelType.FAN)

    def on_pump_edit_button_clicked(self, *_: Any) -> None:
        self._on_edit_button_clicked(ChannelType.PUMP)

    def _on_edit_button_clicked(self, channel: ChannelType) -> None:
        profile = self._profile_selected[channel.value]
        if profile.single_step:
            self.view.show_fixed_speed_profile_popover(profile)

    def on_fixed_speed_apply_button_clicked(self, *_: Any) -> None:
        value, channel = self.view.dismiss_and_get_value_fixed_speed_popover()
        profile = self._profile_selected[channel]
        speed_step: SpeedStep = profile.steps[0]
        speed_step.duty = value
        speed_step.save()
        if channel == ChannelType.FAN.value:
            self._should_update_fan_speed = False
        self.view.refresh_chart(profile)

    def on_fan_apply_button_clicked(self, *_: Any) -> None:
        self._set_speed_profile(self._profile_selected[ChannelType.FAN.value])
        self._should_update_fan_speed = True

    def on_pump_apply_button_clicked(self, *_: Any) -> None:
        self._set_speed_profile(self._profile_selected[ChannelType.PUMP.value])

    def _set_speed_profile(self, profile: SpeedProfile) -> None:
        observable = self._set_speed_profile_interactor \
            .execute(profile.channel, self._get_profile_data(profile))
        self._composite_disposable \
            .add(observable
                 .subscribe_on(self._scheduler)
                 .observe_on(GtkScheduler())
                 .subscribe(on_next=lambda _: self._update_current_speed_profile(profile),
                            on_error=lambda e: (LOG.exception("Set cooling error: %s", str(e)),
                                                self.view.set_statusbar_text('Error applying %s speed profile!'
                                                                             % profile.channel))))

    def _update_current_speed_profile(self, profile: SpeedProfile) -> None:
        current: CurrentSpeedProfile = CurrentSpeedProfile.get_or_none(channel=profile.channel)
        if current is None:
            CurrentSpeedProfile.create(channel=profile.channel, profile=profile)
        else:
            current.profile = profile
            current.save()
        self.view.set_statusbar_text('%s cooling profile applied' % profile.channel.capitalize())

    def _log_exception_return_empty_observable(self, ex: Exception) -> Observable:
        LOG.exception("Err = %s", ex)
        self.view.set_statusbar_text(str(ex))
        return Observable.just(None)

    def _get_status(self) -> Observable:
        return self._get_status_interactor.execute() \
            .catch_exception(self._log_exception_return_empty_observable)

    def _check_new_version(self) -> None:
        self._composite_disposable \
            .add(self._check_new_version_interactor.execute()
                 .subscribe_on(self._scheduler)
                 .observe_on(GtkScheduler())
                 .subscribe(on_next=self._handle_new_version_response,
                            on_error=lambda e: LOG.exception("Check new version error: %s", str(e)))
                 )

    def _handle_new_version_response(self, version: Optional[str]) -> None:
        if version is not None:
            message = "%s version <b>%s</b> is available! Click <a href=\"%s/blob/%s/CHANGELOG.md\"><b>here</b></a> " \
                      "to see what's new." % (APP_NAME, version, APP_SOURCE_URL, version)
            self.view.show_main_infobar_message(message, True)
