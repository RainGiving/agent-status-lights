// SPDX-License-Identifier: MIT
import ClaudeHalo65Core
import SwiftUI

/// A 50-dot ring that runs the same animation the firmware does, so a tail
/// length or speed can be judged without watching the keyboard.
///
/// The timing constants mirror halo_host_show() in side.c. PULSE is the one
/// approximation: the firmware walks a 128-entry breathe table, this uses a
/// triangle of the same period, which is close enough to judge the rhythm.
struct RingPreview: View {
    let style: HaloSpec
    var dotSize: CGFloat = 7
    var radius: CGFloat = 62

    private static let ledCount = 50

    var body: some View {
        TimelineView(.animation) { context in
            let phase = context.date.timeIntervalSince1970 * 1000
            Canvas { ctx, size in
                let center = CGPoint(x: size.width / 2, y: size.height / 2)
                let base = ColorHex.parse(style.color) ?? (r: 128, g: 128, b: 128)
                for index in 0..<Self.ledCount {
                    let level = self.level(for: index, phaseMilliseconds: phase)
                    // The ring starts at the top and runs clockwise, matching
                    // the order of side_led_index_tab[].
                    let angle = (Double(index) / Double(Self.ledCount)) * 2 * .pi - .pi / 2
                    let point = CGPoint(x: center.x + cos(angle) * radius,
                                        y: center.y + sin(angle) * radius)
                    let scale = Double(level) / 255 * Double(style.brightness) / 100
                    let color = Color(.sRGB,
                                      red: Double(base.r) / 255 * scale,
                                      green: Double(base.g) / 255 * scale,
                                      blue: Double(base.b) / 255 * scale)
                    let rect = CGRect(x: point.x - dotSize / 2, y: point.y - dotSize / 2,
                                      width: dotSize, height: dotSize)
                    ctx.fill(Path(ellipseIn: rect), with: .color(color.opacity(0.15 + scale * 0.85)))
                }
            }
        }
        .frame(width: radius * 2 + dotSize * 4, height: radius * 2 + dotSize * 4)
    }

    private func level(for index: Int, phaseMilliseconds: Double) -> Int {
        let speed = max(1, style.speed)
        let stepMs = Double(4 + (255 - speed) / 3)
        let cycleMs = Double(240 + (255 - speed) * 14)
        let phase = phaseMilliseconds

        switch style.haloMode {
        case .release:
            // Callers show a "handed back" placeholder instead of this view,
            // but a dark ring is the honest fallback if one forgets.
            return 0

        case .solid:
            return 255

        case .pulse:
            let t = phase.truncatingRemainder(dividingBy: cycleMs) / cycleMs
            return Int((t < 0.5 ? t * 2 : (1 - t) * 2) * 255)

        case .comet:
            let tail = max(1, min(50, style.param == 0 ? 12 : style.param))
            let head = Int(phase / stepMs) % Self.ledCount
            let behind = (head + Self.ledCount - index) % Self.ledCount
            return behind < tail ? 255 - behind * 255 / tail : 0

        case .strobe:
            let duty = Double(style.param == 0 ? 50 : style.param)
            let period = max(1, cycleMs / 3)
            return phase.truncatingRemainder(dividingBy: period) < period * duty / 100 ? 255 : 0

        case .fill:
            // Sweeps once then holds, and restarts every few seconds so the
            // preview keeps showing the sweep rather than a static ring.
            let loop = stepMs * Double(Self.ledCount) + 1600
            let filled = Int(phase.truncatingRemainder(dividingBy: loop) / stepMs)
            return index < min(filled, Self.ledCount) ? 255 : 0
        }
    }
}
