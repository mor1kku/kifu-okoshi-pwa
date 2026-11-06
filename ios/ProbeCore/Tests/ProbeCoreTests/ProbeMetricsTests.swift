import XCTest
@testable import ProbeCore

final class ProbeMetricsTests: XCTestCase {
    func testVisibleFramesAndSwitchAnimationsDoNotProveBackgroundCapture() {
        var metrics = ProbeMetrics()
        for index in 0..<100 {
            metrics.recordSample(at: Double(index) / 10, comparisonTime: Double(index - 1) / 10, changed: true, flat: false)
        }
        metrics.setBackground(true, at: 10)
        metrics.recordSample(at: 10.5, comparisonTime: 9.9, changed: true, flat: false)
        metrics.recordSample(at: 39.5, comparisonTime: 10.5, changed: true, flat: false)
        metrics.setBackground(false, at: 40)
        let result = metrics.summaries(until: 40)[0]
        XCTAssertEqual(result.samples, 0)
        XCTAssertEqual(result.changes, 0)
        XCTAssertEqual(result.maxSampleGap, 26)
        XCTAssertFalse(result.continuousChangesObserved)
    }

    func testBackgroundFramesWithContinuousChanges() {
        var metrics = ProbeMetrics()
        metrics.setBackground(true, at: 0)
        for index in 0...300 {
            let time = Double(index) / 10
            metrics.recordSample(at: time, comparisonTime: time - 0.1, changed: index % 5 == 0, flat: false)
        }
        metrics.setBackground(false, at: 30)
        let result = metrics.summaries(until: 30)[0]
        XCTAssertTrue(result.continuousChangesObserved)
        XCTAssertGreaterThan(result.changes, 40)
        XCTAssertLessThan(result.maxSampleGap, 0.11)
    }

    func testRepeatedFrozenFramesAreNotContinuousChanges() {
        var metrics = ProbeMetrics()
        metrics.setBackground(true, at: 0)
        for index in 0...300 {
            let time = Double(index) / 10
            metrics.recordSample(at: time, comparisonTime: time - 0.1, changed: false, flat: false)
        }
        let result = metrics.summaries(until: 30)[0]
        XCTAssertGreaterThan(result.samples, 200)
        XCTAssertEqual(result.changes, 0)
        XCTAssertFalse(result.continuousChangesObserved)
    }

    func testBurstFollowedByMissingCallbacksIsNotContinuous() {
        var metrics = ProbeMetrics()
        metrics.setBackground(true, at: 0)
        for index in 20...80 {
            let time = Double(index) / 10
            metrics.recordSample(at: time, comparisonTime: time - 0.1, changed: true, flat: false)
        }
        XCTAssertFalse(metrics.summaries(until: 30)[0].continuousChangesObserved)
        XCTAssertEqual(metrics.summaries(until: 30)[0].maxSampleGap, 20)
    }

    func testMultipleIntervalsAndDuplicateLifecycleNotifications() {
        var metrics = ProbeMetrics()
        metrics.setBackground(true, at: 1)
        metrics.setBackground(true, at: 2)
        metrics.setBackground(false, at: 4)
        metrics.setBackground(false, at: 5)
        metrics.setBackground(true, at: 10)
        let result = metrics.summaries(until: 30)
        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0].evaluatedSeconds, 0)
        XCTAssertEqual(result[1].evaluatedSeconds, 16)
        XCTAssertFalse(result[0].continuousChangesObserved)
    }

    func testImageComparisonRequiresComparableImagesAndIgnoresTinyNoise() {
        XCTAssertFalse(ImageComparison.changed([50, 100], from: nil))
        XCTAssertFalse(ImageComparison.changed([50, 100], from: [50]))
        XCTAssertFalse(ImageComparison.changed([51, 101], from: [50, 100]))
        XCTAssertTrue(ImageComparison.changed([60, 110], from: [50, 100]))
        XCTAssertTrue(ImageComparison.isFlat([0, 0, 1]))
        XCTAssertFalse(ImageComparison.isFlat([0, 100, 255]))
    }

    func testReportsRoundTripAndDoNotContainPixels() throws {
        var metrics = ProbeMetrics()
        metrics.recordCallback(valid: false)
        metrics.recordSample(at: 1, comparisonTime: nil, changed: false, flat: true)
        let data = try JSONEncoder().encode(metrics)
        let restored = try JSONDecoder().decode(ProbeMetrics.self, from: data)
        XCTAssertEqual(restored.invalidImages, 1)
        XCTAssertEqual(restored.observations.count, 1)
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("pixels"))
    }
}
