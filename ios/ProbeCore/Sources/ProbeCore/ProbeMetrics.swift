import Foundation

struct FrameObservation: Codable, Sendable {
    let time: Double
    let comparisonTime: Double?
    let changed: Bool
    let flat: Bool
}

struct BackgroundInterval: Codable, Sendable {
    let start: Double
    var end: Double?
}

struct IntervalSummary: Codable, Sendable {
    let start: Double
    let end: Double
    let evaluatedSeconds: Double
    let samples: Int
    let changes: Int
    let flatSamples: Int
    let maxSampleGap: Double
    let maxChangeGap: Double
    let continuousChangesObserved: Bool
}

struct ProbeMetrics: Codable, Sendable {
    private(set) var callbacks = 0
    private(set) var invalidImages = 0
    private(set) var observations: [FrameObservation] = []
    private(set) var backgroundIntervals: [BackgroundInterval] = []

    mutating func recordCallback(valid: Bool) {
        callbacks += 1
        if !valid { invalidImages += 1 }
    }

    mutating func recordSample(at time: Double, comparisonTime: Double?, changed: Bool, flat: Bool) {
        guard time.isFinite, time >= 0, observations.count < 2400,
              time >= (observations.last?.time ?? 0) else { return }
        observations.append(FrameObservation(time: time, comparisonTime: comparisonTime, changed: changed, flat: flat))
    }

    mutating func setBackground(_ isBackground: Bool, at time: Double) {
        let open = backgroundIntervals.last?.end == nil && !backgroundIntervals.isEmpty
        if isBackground && !open {
            backgroundIntervals.append(BackgroundInterval(start: time, end: nil))
        } else if !isBackground && open {
            backgroundIntervals[backgroundIntervals.count - 1].end = max(time, backgroundIntervals.last!.start)
        }
    }

    func summaries(until time: Double) -> [IntervalSummary] {
        backgroundIntervals.map { interval in
            let end = max(interval.start, min(interval.end ?? time, time))
            // アプリ切替の映像を成功の根拠にしないため、両端の2秒を除く。
            let low = interval.start + 2
            let high = max(low, end - 2)
            let duration = high - low
            let samples = observations.filter { $0.time >= low && $0.time <= high && duration > 0 }
            let changes = samples.filter {
                $0.changed && ($0.comparisonTime.map { $0 >= low } ?? false)
            }
            let sampleGap = Self.maxGap(from: low, to: high, events: samples.map(\.time))
            let changeGap = Self.maxGap(from: low, to: high, events: changes.map(\.time))
            return IntervalSummary(
                start: interval.start, end: end, evaluatedSeconds: duration,
                samples: samples.count, changes: changes.count,
                flatSamples: samples.filter(\.flat).count,
                maxSampleGap: sampleGap, maxChangeGap: changeGap,
                continuousChangesObserved: duration >= 10 && samples.count >= 20 &&
                    changes.count >= 3 && sampleGap <= 2 && changeGap <= 5
            )
        }
    }

    private static func maxGap(from start: Double, to end: Double, events: [Double]) -> Double {
        let points = [start] + events + [end]
        return zip(points, points.dropFirst()).map { max(0, $1 - $0) }.max() ?? 0
    }
}

struct ImageComparison {
    // 画素は比較中だけメモリーに持ち、診断JSONには含めない。
    static func changed(_ current: [UInt8], from previous: [UInt8]?) -> Bool {
        guard let previous, current.count == previous.count, !current.isEmpty else { return false }
        let difference = zip(current, previous).reduce(0) { $0 + abs(Int($1.0) - Int($1.1)) }
        return Double(difference) / Double(current.count) >= 1.5
    }

    static func isFlat(_ pixels: [UInt8]) -> Bool {
        guard let low = pixels.min(), let high = pixels.max() else { return true }
        return Int(high) - Int(low) < 4
    }
}
