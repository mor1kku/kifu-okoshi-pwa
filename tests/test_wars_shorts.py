import json
from pathlib import Path
import numpy as np
import pytest
from backend.recognition import FrameRecognizer, MoveTracker, WarsAdapter


fixturePath = Path(__file__).parent / 'fixtures' / 'wars-shorts.npz'


@pytest.mark.skipif(not fixturePath.exists(), reason='実動画由来の非公開fixtureが必要です')
def testShortRecordingAndLateStartValidation():
    with np.load(fixturePath) as fixture:
        core = FrameRecognizer.restore(json.loads(str(fixture['checkpoint'])))
        dummyFrame = np.zeros((438, 396, 3), dtype=np.uint8)
        for time, glyph in zip(fixture['times'], fixture['glyphs']):
            observation = core.adapter.observation(glyph)
            core.adapter.features = lambda cells, value=observation: value
            core.feed(dummyFrame, float(time))
        assert [row['usi'] for row in core.result()['moves']] == fixture['expected'].tolist()
        # この観測fixtureは打ちの二枚目まで。途中の入力では未確定手を棋譜へ加えない。
        assert core.result()['recognitionIncomplete']
        assert core.tracker.pendingCount == 2
        lateGlyph = fixture['glyphs'][np.searchsorted(fixture['times'], 3.45)]
        adapter = WarsAdapter()
        with pytest.raises(ValueError, match='1手目が動く前'):
            MoveTracker(adapter, adapter.observation(lateGlyph))
        # 問い合わせの広すぎる枠では、誤った2手を出す前に範囲の修正を促す。
        adapter = WarsAdapter()
        with pytest.raises(ValueError, match='盤の9×9マス'):
            MoveTracker(adapter, adapter.observation(fixture['misalignedGlyph']))
