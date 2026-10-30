import importlib
import json
from pathlib import Path
import uuid
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def apiClient(tmp_path, monkeypatch):
    from backend import app as appModule
    monkeypatch.setattr(appModule, 'dataDir', tmp_path)
    monkeypatch.delenv('KIFU_SAMPLE_DIR', raising=False)
    monkeypatch.delenv('KIFU_ACCESS_TOKEN', raising=False)
    return TestClient(appModule.app), appModule


def testHealthAndNoArbitraryPath(apiClient):
    client, _ = apiClient
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert client.get('/api/sample').json()['available'] is False
    assert client.get('/api/videos/not-a-uuid').status_code == 404
    assert client.post('/api/jobs', json={'videoId': '../../secret'}).status_code == 422


def testInvalidMediaIsRejected(apiClient):
    client, module = apiClient
    response = client.post('/api/videos', files={'file': ('invalid.mp4', b'not a video', 'video/mp4')})
    assert response.status_code == 422
    assert list(module.dataDir.iterdir()) == []


def testCrossOriginMutationIsRejected(apiClient):
    client, _ = apiClient
    assert client.post('/api/sample', headers={'Origin': 'https://other.invalid'}).status_code == 403


def testOptionalAuthentication(apiClient, monkeypatch):
    client, _ = apiClient
    monkeypatch.setenv('KIFU_ACCESS_TOKEN', 'test-only-password')
    assert client.get('/api/health').status_code == 401
    assert client.get('/api/health', auth=('user', 'test-only-password')).status_code == 200


def testUnicodePassword(apiClient, monkeypatch):
    import base64
    client, _ = apiClient
    monkeypatch.setenv('KIFU_ACCESS_TOKEN', '日本語のテスト')
    for password, status in [('不一致', 401), ('日本語のテスト', 200)]:
        credentials = base64.b64encode(f'user:{password}'.encode()).decode()
        assert client.get('/api/health', headers={'Authorization': f'Basic {credentials}'}).status_code == status


def testEditExportAndComparison(apiClient):
    client, module = apiClient
    jobId = str(uuid.uuid4())
    from backend.records import replayMoves
    result = {'id': jobId, 'moves': replayMoves(['7g7f', '3c3d']), 'termination': None}
    (module.dataDir / f'{jobId}.result.json').write_text(json.dumps(result), encoding='utf-8')
    invalid = client.put(f'/api/jobs/{jobId}/record', json={'moves': ['7g7e']})
    assert invalid.status_code == 422
    response = client.put(f'/api/jobs/{jobId}/record', json={'moves': ['7g7f', '8c8d'], 'termination': '中断'})
    assert response.status_code == 200
    assert response.json()['moves'][1]['edited']
    kif = client.get(f'/api/jobs/{jobId}/kif')
    assert '中断' in kif.content.decode('utf-8-sig')
    comparison = client.post(f'/api/jobs/{jobId}/compare', files={'file': ('reference.kif', '1 ７六歩(77)\n2 ３四歩(33)'.encode('utf-8'))})
    assert comparison.json()['prefixMatch'] == 1
    assert not comparison.json()['exactMatch']


def testSavedResultBelongsToSelectedVideo(apiClient):
    client, module = apiClient
    videoId, otherId, jobId = [str(uuid.uuid4()) for _ in range(3)]
    folder = module.dataDir / videoId
    folder.mkdir()
    (folder / 'video.json').write_text(json.dumps({'id': videoId}), encoding='utf-8')
    resultPath = module.dataDir / f'{jobId}.result.json'
    resultPath.write_text(json.dumps({'id': jobId, 'videoId': otherId, 'moves': []}), encoding='utf-8')
    assert client.get(f'/api/videos/{videoId}/latest-result').json() == {'id': None}
    resultPath.write_text(json.dumps({'id': jobId, 'videoId': videoId, 'moves': [], 'cancelled': True}), encoding='utf-8')
    assert client.get(f'/api/videos/{videoId}/latest-result').json()['id'] == jobId


def testSampleSelectionAndReplacement(apiClient, monkeypatch, tmp_path):
    from tests.test_recognition import makeVideo
    client, module = apiClient
    samples = tmp_path / 'samples'
    samples.mkdir()
    for name in ['a.mp4', 'b.mp4']:
        makeVideo(samples / name, 4)
    monkeypatch.setenv('KIFU_SAMPLE_DIR', str(samples))
    listing = client.get('/api/sample').json()['videos']
    assert [item['name'] for item in listing] == ['a.mp4', 'b.mp4']
    selected = client.post('/api/sample', json={'id': listing[1]['id']})
    assert selected.status_code == 200
    assert selected.json()['name'] == 'b.mp4'
    assert client.post('/api/sample', json={'id': str(uuid.uuid4())}).status_code == 404
    makeVideo(samples / 'b.mp4', 8)
    replacement = client.get('/api/sample').json()['videos'][1]
    assert replacement['id'] != listing[1]['id']


def testCancelledAndUppercaseIdRestore(apiClient):
    client, module = apiClient
    jobId = str(uuid.uuid4())
    module.saveJson(module.dataDir / f'{jobId}.result.json', {'moves': [], 'cancelled': True})
    assert client.get(f'/api/jobs/{jobId.upper()}').json()['status'] == 'cancelled'


def testSaveFailurePreservesMemory(apiClient, monkeypatch):
    from threading import Event
    from backend.records import replayMoves
    client, module = apiClient
    jobId = str(uuid.uuid4())
    original = {'moves': replayMoves(['7g7f']), 'termination': None}
    monkeypatch.setattr(module, 'jobs', {jobId: {'id': jobId, 'status': 'complete', 'cancel': Event(), 'result': original}})
    def failedSave(*args):
        raise OSError('disk full')
    monkeypatch.setattr(module, 'saveJson', failedSave)
    with pytest.raises(OSError):
        module.updateRecord(jobId, module.RecordUpdate(moves=['2g2f']))
    assert module.jobs[jobId]['result']['moves'][0]['usi'] == '7g7f'


def testConcurrentJsonWrites(apiClient):
    from concurrent.futures import ThreadPoolExecutor
    _, module = apiClient
    path = module.dataDir / 'concurrent.json'
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda value: module.saveJson(path, {'value': value}), range(20)))
    assert json.loads(path.read_text())['value'] in range(20)
    assert list(module.dataDir.glob('*.tmp')) == []


def testBoardCannotExtendOutsideImage(apiClient):
    client, _ = apiClient
    response = client.post('/api/jobs', json={'videoId': str(uuid.uuid4()),
        'board': {'x': .1, 'y': .1, 'width': .9005, 'height': .9}})
    assert response.status_code == 422
