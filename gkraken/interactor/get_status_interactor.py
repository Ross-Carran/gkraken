# This file is part of gkraken.
#
# Copyright (c) 2018 Roberto Leinardi
#
# gsi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# gsi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with gsi.  If not, see <http://www.gnu.org/licenses/>.
import logging

import rx
from injector import singleton, inject
from rx import Observable

from gkraken.repository.kraken_repository import KrakenRepository

_LOG = logging.getLogger(__name__)


@singleton
class GetStatusInteractor:
    @inject
    def __init__(self,
                 kraken_repository: KrakenRepository,
                 ) -> None:
        self._kraken_repository = kraken_repository

    def execute(self) -> Observable:
        _LOG.debug("GetStatusInteractor.execute()")
        return rx.defer(lambda _: rx.just(self._kraken_repository.get_status()))
