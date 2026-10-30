from pathlib import Path
import pytest
import shogi
from backend.records import compareMoves, decodeRecord, exportRecord, parseRecord, replayMoves


def testProvidedRecordRoundTrip():
    samplePath = Path(__file__).resolve().parents[1] / '棋譜.txt'
    if not samplePath.exists():
        pytest.skip('実動画に付属する棋譜がない環境')
    record = parseRecord(decodeRecord(samplePath.read_bytes()))
    assert len(record['moves']) == 136
    assert record['termination'] == '投了'
    exported = exportRecord(record['moves'], record['names'], record['termination'])
    assert parseRecord(exported)['moves'] == record['moves']


def testIncompleteRecordDoesNotInventResignation():
    text = exportRecord(['7g7f', '3c3d'])
    assert '投了' not in text
    assert parseRecord(text)['termination'] is None


@pytest.mark.parametrize('text', ['1 INVALID', '2 ７六歩(77)', '1 ７六歩(77)\n3 ３四歩(33)', '1 ７六歩(77)\n2 投了\n3 ２六歩(27)', '1 ７六飛(77)', '1 ７六歩(77)\n2 ３四角(33)', '1 ７六歩(77)\n2 投了INVALID', '1 ７六歩(77)INVALID'])
def testMalformedMovesAreNotSkipped(text):
    with pytest.raises(ValueError):
        parseRecord(text)


def testIllegalMovesAreRejected():
    for moves in [['7g7e'], ['7g7f', '7f7e'], ['P*5e'], ['7g7f', '3c3d', '7f7e+']]:
        with pytest.raises(ValueError):
            replayMoves(moves)


def testEncodingAndNames():
    record = parseRecord(decodeRecord('先手：対局者\n手合割：平手\n1 ７六歩(77)\n2 ３四歩(33)'.encode('cp932')))
    assert record['names'][0] == '対局者'
    assert parseRecord(exportRecord(record['moves']))['moves'] == record['moves']


def testUnsupportedStartingPosition():
    with pytest.raises(ValueError):
        parseRecord('手合割：二枚落ち\n1 ７六歩(77)')


def testComparisonDoesNotHideMissingMoves():
    comparison = compareMoves(['7g7f'], ['7g7f', '3c3d'])
    assert not comparison['exactMatch']
    assert comparison['prefixMatch'] == 1
    assert comparison['firstMismatch'] == 2


def testWarsReferenceRoundTrip():
    path = Path(__file__).resolve().parents[1] / '棋譜_20261029.kif'
    if not path.exists():
        pytest.skip('ユーザー提供の比較棋譜がない環境')
    record = parseRecord(decodeRecord(path.read_bytes()))
    assert len(record['moves']) == 42
    assert record['termination'] == '投了'
    assert parseRecord(exportRecord(record['moves']))['moves'] == record['moves']
