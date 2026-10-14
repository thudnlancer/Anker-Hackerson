"""In-memory HA/simulator authentication and allowlisted live observations."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit
import requests

from energy_lab.config import exchange_ha_token, is_placeholder, require_runtime_fields
from energy_lab.ha_client import HAClient


class SourceUnavailable(RuntimeError):
    def __init__(self, category, status=None):
        super().__init__(category)
        self.category, self.http_status = category, status


class LiveSource:
    def __init__(self, cfg, *, timeout=10, max_auth_refreshes=1):
        require_runtime_fields(cfg, write_enabled=False)
        self.cfg = deepcopy(cfg)
        self.timeout, self.max_auth_refreshes = timeout, max_auth_refreshes
        self.refreshes = {'ha': 0, 'sim': 0}
        self.tokens = {}
        self.session = requests.Session()
        self.session.trust_env = False
        for key in ('ha_base', 'sim_base'):
            url = urlsplit(self.cfg[key])
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('base URL must not contain credentials or query parameters')

    def close(self):
        self.session.close()
        self.tokens.clear()

    def _login(self, source):
        try:
            if source == 'ha':
                cfg = dict(self.cfg)
                if 'ha' in self.tokens:
                    cfg['ha_token'] = None
                token = exchange_ha_token(cfg)
            else:
                response = self.session.post(self.cfg['sim_base'].rstrip('/') + '/api/auth/login',
                    json={'username': self.cfg['sim_username'], 'password': self.cfg['sim_password']}, timeout=self.timeout,
                    allow_redirects=False)
                response.raise_for_status()
                token = response.json()['token']
            if is_placeholder(token):
                raise ValueError('missing token')
            self.tokens[source] = str(token)
        except Exception as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            raise SourceUnavailable(source + '_authentication_failed', status) from None

    def _get(self, source, path, **params):
        allowed = {'ha': {'/api/states'}, 'sim': {'/api/dashboard', '/api/price', '/api/weather/today', '/api/weather/tomorrow'}}
        if path not in allowed[source]:
            raise ValueError('read endpoint is not allowed')
        if source not in self.tokens:
            self._login(source)
        for attempt in range(2):
            try:
                response = self.session.get(self.cfg[source + '_base'].rstrip('/') + path,
                    headers={'Authorization': 'Bearer ' + self.tokens[source]}, params=params or None,
                    timeout=self.timeout, allow_redirects=False)
                if response.status_code == 401 and attempt == 0 and self.refreshes[source] < self.max_auth_refreshes:
                    self.refreshes[source] += 1
                    self._login(source)
                    continue
                if response.status_code in (401, 403):
                    raise SourceUnavailable(source + '_authentication_failed', response.status_code)
                if not 200 <= response.status_code < 300:
                    raise SourceUnavailable(source + '_read_failed', response.status_code)
                return response.json()
            except SourceUnavailable:
                raise
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                raise SourceUnavailable(source + '_read_failed', status) from None
        raise SourceUnavailable(source + '_authentication_failed', 401)

    def dashboard(self):
        return self._get('sim', '/api/dashboard')

    def states(self):
        rows = self._get('ha', '/api/states')
        if not isinstance(rows, list) or any(not isinstance(x, dict) or 'entity_id' not in x for x in rows):
            raise SourceUnavailable('invalid_ha_entity_list')
        return rows, datetime.now(timezone.utc)

    def ha_client(self):
        if 'ha' not in self.tokens:
            self._login('ha')
        return HAClient(self.cfg['ha_base'], self.tokens['ha'], timeout=self.timeout)

    def capture(self):
        prices = self._get('sim', '/api/price')
        # Forecast requests use separate sessions and immutable token copies.
        token = self.tokens['sim']
        def weather(site, day):
            try:
                with requests.Session() as worker:
                    worker.trust_env = False
                    response = worker.get(self.cfg['sim_base'].rstrip('/') + '/api/weather/' + day,
                        headers={'Authorization': 'Bearer ' + token}, params={'site': int(site)},
                        timeout=self.timeout, allow_redirects=False)
                    if response.status_code in (401, 403):
                        raise SourceUnavailable('sim_authentication_failed', response.status_code)
                    response.raise_for_status()
                    return response.json()
            except SourceUnavailable:
                raise
            except Exception as exc:
                return {'unavailable': type(exc).__name__}
        futures = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            for site in self.cfg['stations']:
                for day in ('today', 'tomorrow'):
                    futures[site, day] = pool.submit(weather, site, day)
            states, captured = self.states()
            forecasts = {site: {day: futures[site, day].result() for day in ('today', 'tomorrow')}
                         for site in self.cfg['stations']}
        entities = {x['entity_id']: x for x in states}
        stations = {}
        for site, spec in self.cfg['stations'].items():
            suffix = spec['suffix']
            names = {'soc': 'soc', 'solar_power': 'solar_power', 'home_load': 'home_load', 'device_time': 'device_time'}
            state = {}
            for key, name in names.items():
                entity = entities.get(f'sensor.{suffix}_{name}', {})
                state[key] = entity.get('state')
                state[key + '_last_changed'] = entity.get('last_changed')
            stations[site] = {'state': state, 'received_at': captured.isoformat(), 'weather': forecasts[site]}
        return {'prices': prices, 'stations': stations, 'received_at': captured.isoformat()}, states
