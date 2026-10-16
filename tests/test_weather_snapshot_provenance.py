import json
from pathlib import Path
from unittest.mock import patch

from scripts.online.capture_api_weather_snapshot import capture


def test_each_response_is_bracketed_and_raw_payload_preserved(tmp_path):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    calls = []
    times = iter(['2032-04-02 00:00:00', '2032-04-02 00:15:00',
                  '2032-04-02 00:30:00', '2032-04-02 00:45:00', '2032-04-02 01:00:00'])
    payload = {'points': [{'timestamp': '2032-04-03T12:00:00Z', 'solar_irradiance_wm2': 400}]}

    class Session:
        def post(self, *args, **kwargs):
            return Response({'token': 'test-only'})

        def get(self, url, **kwargs):
            calls.append(url.rsplit('/', 1)[-1])
            return Response({'time': next(times)} if url.endswith('/health') else payload)

    with patch('scripts.online.capture_api_weather_snapshot.load_runtime_config', return_value={
        'sim_base': 'http://offline.invalid', 'sim_username': 'test',
        'sim_password': 'test', 'stations': {'1': {}}}), \
            patch('scripts.online.capture_api_weather_snapshot.requests.Session', return_value=Session()):
        result = capture(Path('unused.json'), tmp_path / 'snapshot.json', epoch_id='new-window')
    assert calls == ['health', 'health', 'today', 'health', 'health', 'tomorrow', 'health']
    today = result['sites']['1']['today']
    assert today['source_device_time_before_response'] == '2032-04-02 00:15:00'
    assert today['source_device_time_after_response'] == '2032-04-02 00:30:00'
    assert today['raw_response'] == payload
    assert today['api_published_at'] is None
    assert result['epoch_id'] == 'new-window'
    assert len(today['response_sha256']) == 64
    assert json.loads((tmp_path / 'snapshot.json').read_text(encoding='utf-8'))['device_writes'] == 0
