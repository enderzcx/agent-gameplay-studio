// GameAVRec — 最小 app 级「游戏原声 + 画面」录制器（macOS ScreenCaptureKit）
//
// 目的：只抓目标游戏的音频（app 过滤），不碰麦克风、不收其他应用音频；
//       同时抓该 app 的画面，输出带音轨的 mp4 + 分段音频指标 sidecar。
//
// 关键 API：
//   SCContentFilter(display:includingApplications:exceptingWindows:)  → 按 app 过滤
//   SCStreamConfiguration.capturesAudio = true / captureMicrophone = false
//   AVAssetWriter（video h264 + audio AAC）→ 自己控制，便于统计真实信号
//
// 用法：
//   GameAVRec --probe
//   GameAVRec --out x.mp4 --duration 20 [--json x.metrics.json] [--no-video]

import Foundation
import AVFoundation
import CoreMedia
import CoreGraphics
import CoreAudio
import AppKit
import ScreenCaptureKit

// MARK: - 小工具

func logErr(_ s: String) {
    FileHandle.standardError.write(("[gameavrec] " + s + "\n").data(using: .utf8)!)
}

let ISO8601: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()

func jsonString(_ obj: [String: Any]) -> String {
    guard let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .prettyPrinted]),
          let s = String(data: d, encoding: .utf8) else { return "{}" }
    return s
}

func writeFile(_ path: String, _ text: String) {
    do {
        let url = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    } catch {
        logErr("写入 \(path) 失败: \(error)")
    }
}

// MARK: - 参数

struct Options {
    var out = ""
    var json = ""
    var statusFile = ""
    var logFile = ""
    // 刻意留空：本工具**不内置任何默认目标**。抓错 app 会静默录到别的东西，
    // 所以调用方必须显式给出 --bundle-id / --app-name / --pid 之一。
    var bundleId = ""
    var bundleIdSet = false
    var appName = ""
    var pid: Int = 0
    var fps = 30
    var duration = 0.0
    var maxWidth = 1920
    var maxSeconds = 3600.0
    var cursor = false
    var noVideo = false
    var probe = false
    var videoBitrate = 12_000_000
    var quiet = false
    var overwrite = false
    var focusLog = ""
    var focusInterval = 0.5

    static func parse(_ argv: [String]) throws -> Options {
        var o = Options()
        var i = 1
        func value(_ name: String) throws -> String {
            i += 1
            guard i < argv.count else { throw CliError.bad("\(name) 缺参数值") }
            return argv[i]
        }
        while i < argv.count {
            switch argv[i] {
            case "--out": o.out = try value("--out")
            case "--json": o.json = try value("--json")
            case "--status": o.statusFile = try value("--status")
            case "--log": o.logFile = try value("--log")
            case "--bundle-id": o.bundleId = try value("--bundle-id"); o.bundleIdSet = true
            case "--app-name": o.appName = try value("--app-name")
            case "--pid": o.pid = Int(try value("--pid")) ?? 0
            case "--fps": o.fps = Int(try value("--fps")) ?? 30
            case "--duration": o.duration = Double(try value("--duration")) ?? 0
            case "--max-width": o.maxWidth = Int(try value("--max-width")) ?? 1920
            case "--max-seconds": o.maxSeconds = Double(try value("--max-seconds")) ?? 3600
            case "--video-bitrate": o.videoBitrate = Int(try value("--video-bitrate")) ?? 12_000_000
            case "--cursor": o.cursor = true
            case "--no-video": o.noVideo = true
            case "--probe": o.probe = true
            case "--quiet": o.quiet = true
            case "--overwrite": o.overwrite = true
            case "--focus-log": o.focusLog = try value("--focus-log")
            case "--focus-interval": o.focusInterval = Double(try value("--focus-interval")) ?? 0.5
            case "--help", "-h":
                print("""
                GameAVRec — app 级游戏原声音视频录制（ScreenCaptureKit）

                  --probe                     只探测：权限/显示器/目标 app/窗口，输出 JSON 后退出
                  --out <path.mp4>            输出视频（**默认拒绝覆盖已存在文件**）
                  --json <path.json>          分段音频/画面指标 sidecar（同样默认拒绝覆盖）
                  --status <path.json>        结束时写状态文件（供 open -g 启动的调用方等待）
                  --log <path.log>            把日志同时写入文件
                  --overwrite                 允许覆盖上面这些已存在的文件（默认不允许）
                  --bundle-id <id>            目标 app bundle id
                  --app-name <name>           目标 app 名称
                  --pid <pid>                 目标进程 pid
                                              三个选择器是"与"关系；给了却没命中就报错退出，不回退去抓别的 app。
                                              **至少要给一个**：本工具不内置默认目标，
                  --fps <n>                   帧率，默认 30
                  --duration <sec>            录制时长，0 = 等到 SIGINT/SIGTERM
                  --max-width <px>            输出视频最大宽度，默认 1920
                  --cursor                    把系统光标画进画面（默认不画）
                  --no-video                  只录音频（该模式的产物请用 verify --expect audio 校验）
                  --focus-log <path.jsonl>    录制期间每 0.5s 记一行：前台 app / 游戏窗口是否在屏 + 位置
                                              注意：记的是"前台 app"，不等于游戏窗口 key 状态
                  --focus-interval <sec>      前台采样间隔，默认 0.5
                  --quiet                     不打印逐条进度
                """)
                exit(0)
            default:
                throw CliError.bad("未知参数: \(argv[i])")
            }
            i += 1
        }
        return o
    }
}

enum CliError: Error, CustomStringConvertible {
    case bad(String)
    case usage(String)
    case permission(String)
    case runtime(String)
    var description: String {
        switch self {
        case .bad(let s): return "参数错误: " + s
        case .usage(let s): return "用法错误: " + s
        case .permission(let s): return "权限问题: " + s
        case .runtime(let s): return "运行时错误: " + s
        }
    }
}

// MARK: - 录制器

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let opts: Options
    private let queue = DispatchQueue(label: "gameavrec.capture")
    private var stream: SCStream?
    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var audioInput: AVAssetWriterInput?
    private var sessionStarted = false
    private var sessionStartPTS = 0.0
    private var stopping = false
    private var closed = false
    /// 采集是否真正跑起来（startCapture 返回后为 true）
    private var captureLive = false
    /// 是否收到过停止请求（可能早于采集启动）
    private var stopRequested = false
    /// 是否在拿到任何采样前就被停止（此时 finishWriting 可能永不回调）
    private var abortedBeforeSamples = false
    private var forcedCloseReason: String? = nil

    let finished = DispatchSemaphore(value: 0)
    private(set) var targetDescription: [String: Any] = [:]

    // video metrics
    private var videoFrames = 0
    private var firstVideoPTS = -1.0
    private var lastVideoPTS = -1.0
    private var maxVideoGap = 0.0
    private var stallsOver500ms = 0
    private var droppedVideoAppends = 0
    private var videoAppendsOK = 0

    // audio metrics
    private var audioBuffers = 0
    private var audioSamples = 0
    private var firstAudioPTS = -1.0
    private var lastAudioPTS = -1.0
    private var peak: Double = 0
    private var sumSq = 0.0
    private var sumN = 0.0
    private var buckets: [Int: (peak: Double, sumSq: Double, n: Double)] = [:]
    private let bucketSize = 0.5
    private var audioFormatDesc = ""
    private var droppedAudioAppends = 0
    private var audioAppendsOK = 0
    /// close() 决定的真实退出码（0 成功 / 1 失败 / 2 无法核实）
    private(set) var finalExitCode: Int32 = 1

    private var startedAt = Date()
    private var endedAt: Date?
    private var startError: String?

    // 焦点/窗口时间线
    private let focusQueue = DispatchQueue(label: "gameavrec.focus")
    private var focusTimer: DispatchSourceTimer?
    private var focusHandle: FileHandle?
    private var focusSamples = 0
    private var gameFrontmostSamples = 0

    init(opts: Options) { self.opts = opts }

    // MARK: 焦点/窗口时间线

    /// 记录「前台是哪个 app」「游戏窗口是否还在屏上、在哪」，用来给 A/B/A 对照分段。
    /// 只用 NSWorkspace + CGWindowList，不需要无障碍权限。
    private func startFocusLog(gamePid: Int, gameBundle: String) {
        guard !opts.focusLog.isEmpty else { return }
        let url = URL(fileURLWithPath: opts.focusLog)
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        // 入口已做过"拒绝覆盖"校验；这里只在文件不存在时创建，避免把已有证据清零
        if !FileManager.default.fileExists(atPath: opts.focusLog) {
            FileManager.default.createFile(atPath: opts.focusLog, contents: nil)
        }
        focusHandle = FileHandle(forWritingAtPath: opts.focusLog)
        let t = DispatchSource.makeTimerSource(queue: focusQueue)
        t.schedule(deadline: .now(), repeating: opts.focusInterval)
        t.setEventHandler { [weak self] in
            guard let self = self else { return }
            let now = Date()
            let front = NSWorkspace.shared.frontmostApplication
            let frontBundle = front?.bundleIdentifier ?? ""
            let isGameFront = (frontBundle == gameBundle) || (front?.processIdentifier == pid_t(gamePid))
            var gameWindows: [[String: Any]] = []
            var gameOnScreen = false
            if let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
                for w in list {
                    guard let owner = w[kCGWindowOwnerPID as String] as? Int, owner == gamePid else { continue }
                    let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
                    gameOnScreen = true
                    gameWindows.append([
                        "window_number": w[kCGWindowNumber as String] as? Int ?? -1,
                        "layer": w[kCGWindowLayer as String] as? Int ?? -1,
                        "x": b["X"] as? Double ?? 0, "y": b["Y"] as? Double ?? 0,
                        "w": b["Width"] as? Double ?? 0, "h": b["Height"] as? Double ?? 0,
                    ])
                }
            }
            self.focusSamples += 1
            if isGameFront { self.gameFrontmostSamples += 1 }
            let line: [String: Any] = [
                "wall": ISO8601.string(from: now),
                "t_rel": now.timeIntervalSince(self.startedAt),
                "frontmost_name": front?.localizedName ?? "",
                "frontmost_bundle": frontBundle,
                "game_is_frontmost": isGameFront,
                "game_window_on_screen": gameOnScreen,
                "game_windows": gameWindows,
            ]
            if let d = try? JSONSerialization.data(withJSONObject: line, options: [.sortedKeys]),
               let s = String(data: d, encoding: .utf8) {
                self.focusHandle?.write((s + "\n").data(using: .utf8)!)
            }
        }
        t.resume()
        focusTimer = t
    }

    private func stopFocusLog() {
        focusTimer?.cancel()
        focusTimer = nil
        try? focusHandle?.close()
        focusHandle = nil
    }

    // MARK: 发现目标

    private static func pickDisplay(_ content: SCShareableContent, app: SCRunningApplication) -> SCDisplay? {
        let wins = content.windows.filter { $0.owningApplication?.bundleIdentifier == app.bundleIdentifier && $0.isOnScreen }
        for w in wins {
            if let d = content.displays.first(where: { $0.frame.intersects(w.frame) }) { return d }
        }
        return content.displays.first
    }

    static func probe(_ opts: Options) async throws -> [String: Any] {
        var out: [String: Any] = [:]
        out["cg_preflight_screen_capture"] = CGPreflightScreenCaptureAccess()
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        } catch {
            out["shareable_content_error"] = "\(error)"
            out["hint"] = "未取得「屏幕与系统音频录制」权限：系统设置 → 隐私与安全性 → 屏幕与系统音频录制，勾选运行本程序的 app"
            return out
        }
        out["displays"] = content.displays.map { ["displayID": $0.displayID, "width": $0.width, "height": $0.height,
                                                  "x": $0.frame.origin.x, "y": $0.frame.origin.y,
                                                  "w": $0.frame.width, "h": $0.frame.height] }
        var apps: [[String: Any]] = []
        for a in content.applications {
            let n = content.windows.filter { $0.owningApplication?.bundleIdentifier == a.bundleIdentifier }.count
            apps.append(["name": a.applicationName, "bundle_id": a.bundleIdentifier, "pid": a.processID, "windows": n])
        }
        out["applications"] = apps.sorted { ($0["name"] as? String ?? "") < ($1["name"] as? String ?? "") }
        out["selector"] = selectorDescription(opts)
        if let target = matchApp(content, opts) {
            out["match"] = ["name": target.applicationName, "bundle_id": target.bundleIdentifier, "pid": target.processID]
            out["match_windows"] = content.windows
                .filter { $0.owningApplication?.bundleIdentifier == target.bundleIdentifier }
                .map { ["title": $0.title ?? "", "windowID": $0.windowID, "on_screen": $0.isOnScreen,
                        "x": $0.frame.origin.x, "y": $0.frame.origin.y, "w": $0.frame.width, "h": $0.frame.height] }
        } else {
            out["match"] = NSNull()
            out["hint"] = "显式目标没命中：selector = \(selectorDescription(opts)) —— 不会回退去抓别的 app；确认目标是否在运行"
        }
        return out
    }

    /// 目标选择器是**合取**语义：给了哪个就按哪个筛，多个都给就都要满足。
    /// **本工具没有默认目标**：三个都不给 = 调用方用法错误，直接失败。
    /// 指定的目标没命中 = 返回 nil（调用方必须失败），**绝不**回退去抓别的 app。
    static func selectorDescription(_ opts: Options) -> String {
        var parts: [String] = []
        if opts.pid > 0 { parts.append("--pid \(opts.pid)") }
        if opts.bundleIdSet { parts.append("--bundle-id \(opts.bundleId)") }
        if !opts.appName.isEmpty { parts.append("--app-name \(opts.appName)") }
        if parts.isEmpty { return "未指定目标" }
        return parts.joined(separator: " AND ")
    }

    /// 必须显式给出选择器：本工具**没有**内置默认目标，也**不**回退去抓别的 app。
    private static func matchApp(_ content: SCShareableContent, _ opts: Options) -> SCRunningApplication? {
        let explicit = opts.pid > 0 || opts.bundleIdSet || !opts.appName.isEmpty
        guard explicit else { return nil }
        var candidates = content.applications
        if opts.pid > 0 { candidates = candidates.filter { $0.processID == opts.pid } }
        if opts.bundleIdSet { candidates = candidates.filter { $0.bundleIdentifier == opts.bundleId } }
        if !opts.appName.isEmpty { candidates = candidates.filter { $0.applicationName == opts.appName } }
        return candidates.first
    }

    // MARK: 启动

    func start() async throws {
        startedAt = Date()
        // 用法错误在要权限之前就报掉：没目标就没必要去申请屏幕录制权限。
        guard opts.pid > 0 || opts.bundleIdSet || !opts.appName.isEmpty else {
            throw CliError.usage(
                "必须显式指定录制目标：--bundle-id <id> / --app-name <name> / --pid <pid> 至少给一个。\n" +
                "本工具不内置任何默认目标，也不会回退去抓别的 app。")
        }
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        } catch {
            throw CliError.permission(
                "SCShareableContent 失败: \(error)\n" +
                "→ 需要「屏幕与系统音频录制」权限。请到 系统设置 → 隐私与安全性 → 屏幕与系统音频录制，\n" +
                "  勾选（必要时点 + 添加）本程序所在 app bundle，然后重跑。")
        }
        guard let app = Recorder.matchApp(content, opts) else {
            throw CliError.runtime("目标未命中，拒绝改抓别的应用：selector = \(Recorder.selectorDescription(opts))")
        }
        guard let display = Recorder.pickDisplay(content, app: app) else {
            throw CliError.runtime("没有可用显示器")
        }

        let filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
        let scale = Double(filter.pointPixelScale)

        // 游戏常有多个不可见的辅助窗口，filter.contentRect 会退化成整屏（四周大片黑边）。
        // 这里用窗口服务器里「最大的一块在屏 app 窗口」当裁剪区，录出来紧贴游戏窗口。
        var sourceRect: CGRect? = nil
        if let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
            var best: CGRect = .zero
            for win in list {
                guard let owner = win[kCGWindowOwnerPID as String] as? Int, owner == Int(app.processID) else { continue }
                guard (win[kCGWindowLayer as String] as? Int ?? 0) == 0 else { continue }
                guard let b = win[kCGWindowBounds as String] as? [String: Any] else { continue }
                let r = CGRect(x: b["X"] as? Double ?? 0, y: b["Y"] as? Double ?? 0,
                               width: b["Width"] as? Double ?? 0, height: b["Height"] as? Double ?? 0)
                if r.width * r.height > best.width * best.height { best = r }
            }
            // 只在这个窗口确实落在显示器内、且比整块内容区更小时才裁剪
            if best.width >= 64, best.height >= 64,
               display.frame.contains(best),
               best.width * best.height < filter.contentRect.width * filter.contentRect.height * 0.95 {
                sourceRect = best
            }
        }

        let baseRect = sourceRect ?? filter.contentRect
        var w = Int((Double(baseRect.width) * scale).rounded())
        var h = Int((Double(baseRect.height) * scale).rounded())
        if w < 2 || h < 2 {
            w = Int(display.width); h = Int(display.height)
        }
        if w > opts.maxWidth {
            let r = Double(opts.maxWidth) / Double(w)
            w = opts.maxWidth
            h = Int((Double(h) * r).rounded())
        }
        w -= (w % 2); h -= (h % 2)
        w = max(w, 2); h = max(h, 2)

        let cfg = SCStreamConfiguration()
        cfg.width = w
        cfg.height = h
        if let sr = sourceRect {
            cfg.sourceRect = sr
            // sourceRect 是相对 filter 内容原点的坐标，单显示器下内容原点即 (0,0)
            cfg.sourceRect.origin.x -= filter.contentRect.origin.x
            cfg.sourceRect.origin.y -= filter.contentRect.origin.y
        }
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(opts.fps))
        cfg.queueDepth = 6
        cfg.pixelFormat = kCVPixelFormatType_32BGRA
        cfg.scalesToFit = false
        cfg.showsCursor = opts.cursor
        cfg.colorSpaceName = CGColorSpace.sRGB
        // —— 音频：只要系统里这个 app 的音频，不要麦克风 ——
        cfg.capturesAudio = true
        cfg.sampleRate = 48_000
        cfg.channelCount = 2
        cfg.excludesCurrentProcessAudio = true
        cfg.captureMicrophone = false

        targetDescription = ["name": app.applicationName, "bundle_id": app.bundleIdentifier,
                             "pid": app.processID, "display_id": display.displayID,
                             "capture_width": w, "capture_height": h, "fps": opts.fps,
                             "source_rect": sourceRect.map { ["x": $0.origin.x, "y": $0.origin.y, "w": $0.width, "h": $0.height] } ?? NSNull(),
                             "filter": "display + includingApplications([\(app.applicationName)])",
                             "selector": Recorder.selectorDescription(opts),
                             "captures_audio": true, "capture_microphone": false,
                             "sample_rate": 48_000, "channels": 2,
                             "excludes_current_process_audio": true]

        let url = URL(fileURLWithPath: opts.out)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        // 不静默删除已有素材；存在即报错（除非显式 --overwrite）
        if FileManager.default.fileExists(atPath: url.path) {
            if opts.overwrite {
                try FileManager.default.removeItem(at: url)
            } else {
                throw CliError.usage("输出已存在，拒绝覆盖：\(url.path)（要覆盖请显式加 --overwrite，或换新路径）")
            }
        }
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        self.writer = writer

        if !opts.noVideo {
            let vSettings: [String: Any] = [
                AVVideoCodecKey: AVVideoCodecType.h264,
                AVVideoWidthKey: w,
                AVVideoHeightKey: h,
                AVVideoCompressionPropertiesKey: [
                    AVVideoAverageBitRateKey: opts.videoBitrate,
                    AVVideoExpectedSourceFrameRateKey: opts.fps,
                    AVVideoMaxKeyFrameIntervalKey: opts.fps * 2,
                    AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                ],
            ]
            let vi = AVAssetWriterInput(mediaType: .video, outputSettings: vSettings)
            vi.expectsMediaDataInRealTime = true
            if writer.canAdd(vi) { writer.add(vi); videoInput = vi }
            else { throw CliError.runtime("AVAssetWriter 无法添加视频轨") }
        }

        let aSettings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48_000,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: 192_000,
        ]
        let ai = AVAssetWriterInput(mediaType: .audio, outputSettings: aSettings)
        ai.expectsMediaDataInRealTime = true
        if writer.canAdd(ai) { writer.add(ai); audioInput = ai }
        else { throw CliError.runtime("AVAssetWriter 无法添加音频轨") }

        guard writer.startWriting() else {
            throw CliError.runtime("AVAssetWriter.startWriting 失败: \(writer.error?.localizedDescription ?? "unknown")")
        }

        let stream = SCStream(filter: filter, configuration: cfg, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        self.stream = stream
        try await stream.startCapture()
        queue.sync { self.captureLive = true }
        startFocusLog(gamePid: Int(app.processID), gameBundle: app.bundleIdentifier)
        // 停止请求可能早于采集启动：采集一起来就在这里串行收尾（同一线程，不与 writer 竞争）
        if queue.sync(execute: { self.stopRequested && !self.closed }) {
            if let s = self.stream { try? await s.stopCapture() }
            queue.sync { self.abortBeforeSamples(reason: "stop-requested-during-start") }
        }

        if !opts.quiet {
            logErr("开始录制 → \(opts.out) [\(w)x\(h) @\(opts.fps)] app=\(app.applicationName)/\(app.bundleIdentifier) pid=\(app.processID) audio=on mic=off")
        }

        if opts.duration > 0 {
            queue.asyncAfter(deadline: .now() + opts.duration) { self.requestStop(reason: "duration") }
        }
        if opts.maxSeconds > 0 {
            queue.asyncAfter(deadline: .now() + opts.maxSeconds) { self.requestStop(reason: "max_seconds") }
        }
    }

    // MARK: 采样

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        let pts = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        guard pts.isFinite else { return }

        if !sessionStarted {
            sessionStarted = true
            sessionStartPTS = pts
            writer?.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        }

        switch type {
        case .screen:
            guard !opts.noVideo, let vi = videoInput else { return }
            // 统一时间基准：首/末 PTS 都相对 sessionStartPTS（绝对 host PTS 有 75 万秒量级，不能混用）
            let vRel = pts - sessionStartPTS
            if firstVideoPTS < 0 { firstVideoPTS = vRel }
            if lastVideoPTS >= 0 {
                let gap = vRel - lastVideoPTS
                if gap > maxVideoGap { maxVideoGap = gap }
                if gap > 0.5 { stallsOver500ms += 1 }
            }
            lastVideoPTS = vRel
            videoFrames += 1
            if vi.isReadyForMoreMediaData {
                if vi.append(sampleBuffer) { videoAppendsOK += 1 } else { droppedVideoAppends += 1 }
            } else {
                droppedVideoAppends += 1
            }

        case .audio:
            guard let ai = audioInput else { return }
            if firstAudioPTS < 0 { firstAudioPTS = pts - sessionStartPTS }
            lastAudioPTS = pts - sessionStartPTS
            audioBuffers += 1
            measure(sampleBuffer, pts: pts)
            if ai.isReadyForMoreMediaData {
                if ai.append(sampleBuffer) { audioAppendsOK += 1 } else { droppedAudioAppends += 1 }
            } else {
                droppedAudioAppends += 1
            }

        default:
            break
        }
    }

    /// 计算真实信号：峰值 / RMS，并按 0.5s 分桶，用来判断「有音轨但全是静音」
    private func measure(_ sb: CMSampleBuffer, pts: Double) {
        guard let fd = CMSampleBufferGetFormatDescription(sb),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fd) else { return }
        let asbd = asbdPtr.pointee
        if audioFormatDesc.isEmpty {
            let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
            let layout = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0 ? "non-interleaved" : "interleaved"
            audioFormatDesc = "\(asbd.mSampleRate)Hz ch=\(asbd.mChannelsPerFrame) bits=\(asbd.mBitsPerChannel) \(isFloat ? "float" : "int") \(layout)"
        }
        let chCount = max(1, Int(asbd.mChannelsPerFrame))
        let ablPtr = AudioBufferList.allocate(maximumBuffers: chCount)
        defer { free(ablPtr.unsafeMutablePointer) }
        var blockBuffer: CMBlockBuffer?
        let st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil,
            bufferListOut: ablPtr.unsafeMutablePointer,
            bufferListSize: AudioBufferList.sizeInBytes(maximumBuffers: chCount),
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0, blockBufferOut: &blockBuffer)
        guard st == noErr else { return }

        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let bits = Int(asbd.mBitsPerChannel)
        let abl = UnsafeMutableAudioBufferListPointer(ablPtr.unsafeMutablePointer)

        var localPeak = 0.0
        var localSq = 0.0
        var localN = 0.0

        for buf in abl {
            guard let mData = buf.mData else { continue }
            let byteCount = Int(buf.mDataByteSize)
            if isFloat && bits == 32 {
                let n = byteCount / 4
                let p = mData.assumingMemoryBound(to: Float.self)
                for i in 0..<n {
                    let v = Double(p[i])
                    let a = abs(v)
                    if a > localPeak { localPeak = a }
                    localSq += v * v
                }
                localN += Double(n)
            } else if !isFloat && bits == 16 {
                let n = byteCount / 2
                let p = mData.assumingMemoryBound(to: Int16.self)
                for i in 0..<n {
                    let v = Double(p[i]) / 32768.0
                    let a = abs(v)
                    if a > localPeak { localPeak = a }
                    localSq += v * v
                }
                localN += Double(n)
            } else if !isFloat && bits == 32 {
                let n = byteCount / 4
                let p = mData.assumingMemoryBound(to: Int32.self)
                for i in 0..<n {
                    let v = Double(p[i]) / 2147483648.0
                    let a = abs(v)
                    if a > localPeak { localPeak = a }
                    localSq += v * v
                }
                localN += Double(n)
            }
        }

        audioSamples += Int(localN) / chCount
        if localPeak > peak { peak = localPeak }
        sumSq += localSq
        sumN += localN

        let rel = pts - sessionStartPTS
        let key = Int(floor(rel / bucketSize))
        var b = buckets[key] ?? (0, 0, 0)
        if localPeak > b.peak { b.peak = localPeak }
        b.sumSq += localSq
        b.n += localN
        buckets[key] = b
    }

    // MARK: 停止

    func requestStop(reason: String) {
        stopRequested = true
        queue.async {
            guard !self.stopping, !self.closed else { return }

            // 采集还没起来（startCapture 未返回）：**不要在这里碰 writer** ——
            // start() 可能正在同一时刻 startWriting()，并发 cancelWriting 会让
            // AVFoundation 抛 ObjC 异常直接 abort。这里只记录请求：
            //   · start() 起来后会自己走 abort 路径给终态；
            //   · 启动卡死则由 main 的 30s 启动上限给 failed 终态。
            if !self.captureLive {
                if !self.opts.quiet { logErr("收到停止请求（\(reason)），采集尚未启动：交给启动流程收尾") }
                return
            }

            self.stopping = true
            if !self.opts.quiet { logErr("停止录制（\(reason)）") }

            // 采集在跑但还没拿到任何采样：writer 没 startSession，
            // finishWriting 回调可能永远不来 → 取消写入并立刻给终态。
            if !self.sessionStarted {
                self.abortBeforeSamples(reason: reason)
                self.stream?.stopCapture { _ in }
                return
            }

            if let s = self.stream {
                s.stopCapture { err in
                    if let err = err { logErr("stopCapture 报错: \(err)") }
                    self.queue.async { self.finishUp() }
                }
            } else {
                self.finishUp()
            }
        }
    }

    /// 在拿到任何采样前停止：取消写入并立刻给失败终态（finishWriting 此时可能永不回调）
    private func abortBeforeSamples(reason: String) {
        guard !closed, !stopping else { return }
        stopping = true
        abortedBeforeSamples = true
        if !opts.quiet { logErr("在采集到任何采样之前停止（\(reason)）：取消写入并给失败终态") }
        videoInput?.markAsFinished()
        audioInput?.markAsFinished()
        writer?.cancelWriting()
        close(sessionStartPTS: 0)
    }

    /// 正常收尾：标记输入结束 + finishWriting，并挂一个兜底，保证一定有终态
    private func finishUp() {
        guard !closed else { return }
        videoInput?.markAsFinished()
        audioInput?.markAsFinished()
        writer?.finishWriting { self.close(sessionStartPTS: self.sessionStartPTS) }
        queue.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self = self, !self.closed else { return }
            logErr("finishWriting 5s 未回调：强制收尾（文件可能不完整）")
            self.forcedCloseReason = "finish_writing_timeout"
            self.close(sessionStartPTS: self.sessionStartPTS)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        logErr("SCStream 异常停止: \(error)")
        startError = "\(error)"
        queue.async {
            guard !self.stopping, !self.closed else { return }
            self.stopping = true
            if self.sessionStarted { self.finishUp() } else {
                self.abortedBeforeSamples = true
                self.writer?.cancelWriting()
                self.close(sessionStartPTS: 0)
            }
        }
    }

    private func close(sessionStartPTS: Double) {
        guard !closed else { return }
        closed = true
        endedAt = Date()
        stopFocusLog()

        // ---- 真实成功/失败判定：writer 状态 + stream 错误 + 文件里是否真有轨道 ----
        let wStatus = writer?.status ?? .unknown
        let writerErrorText: String? = writer?.error.map { "\($0)" }
        var fileAudioTracks: Int? = nil
        var fileVideoTracks: Int? = nil
        var trackProbeError: String? = nil
        if wStatus == .completed, let url = writer?.outputURL {
            let asset = AVURLAsset(url: url)
            let sem = DispatchSemaphore(value: 0)
            var na = 0, nv = 0, perr: String? = nil
            Task {
                do {
                    na = try await asset.loadTracks(withMediaType: .audio).count
                    nv = try await asset.loadTracks(withMediaType: .video).count
                } catch { perr = "\(error)" }
                sem.signal()
            }
            sem.wait()
            if perr == nil { fileAudioTracks = na; fileVideoTracks = nv } else { trackProbeError = perr }
        }
        let tracksVerified = (fileAudioTracks != nil)
        let audioInFile = tracksVerified ? (fileAudioTracks! > 0) : false
        let videoInFile = tracksVerified ? (fileVideoTracks! > 0) : false
        let needVideo = !opts.noVideo
        var problems: [String] = []
        if wStatus != .completed { problems.append("writer.status=\(wStatus.rawValue)（非 completed）") }
        if let we = writerErrorText { problems.append("writer.error=\(we)") }
        if let se = startError { problems.append("stream_error=\(se)") }
        if abortedBeforeSamples { problems.append("在采集到任何采样之前就被停止（没有可交付内容）") }
        if let fr = forcedCloseReason { problems.append("收尾异常：\(fr)") }
        if tracksVerified {
            if !audioInFile { problems.append("产出文件里没有音频轨") }
            if needVideo && !videoInFile { problems.append("产出文件里没有视频轨") }
        }

        var bucketArr: [[String: Any]] = []
        for k in buckets.keys.sorted() {
            let b = buckets[k]!
            let rms = b.n > 0 ? sqrt(b.sumSq / b.n) : 0
            bucketArr.append([
                "t_start": Double(k) * bucketSize,
                "t_end": Double(k + 1) * bucketSize,
                "peak_dbfs": dbfs(b.peak),
                "rms_dbfs": dbfs(rms),
            ])
        }
        let overallRMS = sumN > 0 ? sqrt(sumSq / sumN) : 0
        let silentBuckets = bucketArr.filter { ($0["peak_dbfs"] as? Double ?? -160) < -60 }.count

        var out: [String: Any] = [
            "tool": "GameAVRec",
            "out": opts.out,
            "started_at": ISO8601.string(from: startedAt),
            "ended_at": ISO8601.string(from: endedAt ?? Date()),
            "wall_seconds": (endedAt ?? Date()).timeIntervalSince(startedAt),
            "time_basis": "所有 *_pts_rel 均相对本次 session 起点（第一个采样的 PTS）；绝对 host PTS 不外泄",
            "writer_status": Recorder.statusName(wStatus),
            "writer_status_raw": wStatus.rawValue,
            "writer_error": writerErrorText ?? NSNull(),
            "file_tracks": [
                "verified": tracksVerified,
                "audio": fileAudioTracks.map { $0 as Any } ?? NSNull(),
                "video": fileVideoTracks.map { $0 as Any } ?? NSNull(),
                "probe_error": trackProbeError ?? NSNull(),
            ],
            "appends_ok": ["video": videoAppendsOK, "audio": audioAppendsOK],
            "target": targetDescription,
            "video": [
                "enabled": !opts.noVideo,
                "frames": videoFrames,
                "first_pts_rel": firstVideoPTS,
                "last_pts_rel": lastVideoPTS,
                "max_gap_s": maxVideoGap,
                "stalls_over_500ms": stallsOver500ms,
                "dropped_appends": droppedVideoAppends,
                "appends_ok": videoAppendsOK,
            ],
            "audio": [
                "buffers": audioBuffers,
                "samples_per_channel": audioSamples,
                "source_format": audioFormatDesc,
                "first_pts_rel": firstAudioPTS,
                "last_pts_rel": lastAudioPTS,
                "peak_dbfs": dbfs(peak),
                "rms_dbfs": dbfs(overallRMS),
                "silent_buckets": silentBuckets,
                "buckets_total": bucketArr.count,
                "dropped_appends": droppedAudioAppends,
                "appends_ok": audioAppendsOK,
                "buckets": bucketArr,
            ],
            "av_start_delta_s": (firstVideoPTS >= 0 && firstAudioPTS >= 0) ? (firstVideoPTS - firstAudioPTS) : NSNull(),
            "focus": [
                "log": opts.focusLog,
                "samples": focusSamples,
                "frontmost_game_samples": gameFrontmostSamples,
                "frontmost_game_ratio": focusSamples > 0 ? Double(gameFrontmostSamples) / Double(focusSamples) : NSNull(),
            ],
            "stream_error": startError ?? NSNull(),
            "problems": problems,
        ]

        // 结论性判定：轨道是否真的写进文件（查不到就标 unverified，不用 buffers>0 冒充）
        var verdict: [String: Any] = [:]
        verdict["audio_track_present"] = tracksVerified ? audioInFile : NSNull()
        verdict["audio_track_present_basis"] = tracksVerified ? "file_tracks" : "unverified"
        verdict["audio_buffers_seen"] = audioBuffers
        verdict["audio_appends_ok"] = audioAppendsOK
        verdict["audio_has_signal"] = peak > 0.0005   // 约 -66 dBFS
        verdict["audio_peak_dbfs"] = dbfs(peak)
        verdict["video_track_present"] = tracksVerified ? videoInFile : NSNull()
        verdict["video_frames_present"] = videoFrames > 0
        verdict["video_frames_arriving"] = (maxVideoGap < 2.0)
        verdict["tracks_verified"] = tracksVerified
        out["verdict"] = verdict

        let text = jsonString(out)
        let jsonPath = opts.json.isEmpty ? (opts.out + ".metrics.json") : opts.json
        writeFile(jsonPath, text)

        // 只有真的成功才写 done/exit=0；否则如实传播失败
        let state: String
        if !problems.isEmpty {
            state = "failed"; finalExitCode = 1
        } else if !tracksVerified {
            state = "unverified"; finalExitCode = 2
            problems.append("完成写入但无法核实产出轨道（\(trackProbeError ?? "unknown")）")
        } else {
            state = "done"; finalExitCode = 0
        }
        if !opts.statusFile.isEmpty {
            writeFile(opts.statusFile, jsonString(["state": state, "exit": Int(finalExitCode),
                                                   "metrics": jsonPath, "out": opts.out,
                                                   "writer_status": Recorder.statusName(wStatus),
                                                   "problems": problems, "verdict": verdict]))
        }
        if !opts.quiet {
            print(jsonString(["ok": finalExitCode == 0, "state": state, "exit": Int(finalExitCode),
                              "out": opts.out, "metrics": jsonPath, "problems": problems,
                              "audio_buffers": audioBuffers, "audio_appends_ok": audioAppendsOK,
                              "video_frames": videoFrames, "video_appends_ok": videoAppendsOK,
                              "file_audio_tracks": fileAudioTracks.map { $0 as Any } ?? NSNull(),
                              "file_video_tracks": fileVideoTracks.map { $0 as Any } ?? NSNull(),
                              "peak_dbfs": dbfs(peak), "verdict": verdict]))
        }
        finished.signal()
    }

    /// AVAssetWriter.Status 的可读名字（rawValue 数字不利于人读）
    static func statusName(_ s: AVAssetWriter.Status) -> String {
        switch s {
        case .unknown: return "unknown"
        case .writing: return "writing"
        case .completed: return "completed"
        case .failed: return "failed"
        case .cancelled: return "cancelled"
        @unknown default: return "unknown(\(s.rawValue))"
        }
    }

    private func dbfs(_ linear: Double) -> Double {
        if linear <= 0 { return -160.0 }
        return max(-160.0, 20.0 * log10(linear))
    }
}

// MARK: - main

/// 必须活到进程结束：Pipe / FileHandle 一旦被 ARC 释放，stderr 写端就成了孤儿，
/// 之后任何一次日志写入都会 SIGPIPE 把录制器悄悄干掉（连 sidecar 都不会写）。
var gLogPipe: Pipe?
var gLogFileHandle: FileHandle?
/// 录制器还没构造出来时，信号先记在钩子上，避免早期 SIGTERM 被吞掉后进程不再响应停止
var gStopHook: ((String) -> Void)?

do {
    let opts = try Options.parse(CommandLine.arguments)

    // 信号源第一件事就装好（后面才创建 Recorder）
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    sigint.setEventHandler { gStopHook?("SIGINT") }
    sigterm.setEventHandler { gStopHook?("SIGTERM") }
    sigint.resume()
    sigterm.resume()

    // 会写的文件一律默认不覆盖（--log 也追加入已有文件而不是清零）
    if !opts.logFile.isEmpty, FileManager.default.fileExists(atPath: opts.logFile), !opts.overwrite {
        logErr("拒绝覆盖已存在的日志文件（退出码 3）：\(opts.logFile)（加 --overwrite 或换路径）")
        exit(3)
    }

    if !opts.logFile.isEmpty {
        // 简单 tee：把 stderr 也写文件（供 open -g 启动时排查）
        let path = opts.logFile
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil)
        }
        if let fh = FileHandle(forWritingAtPath: path) {
            fh.seekToEndOfFile()
            gLogFileHandle = fh        // 保活
            // 用管道把 stderr 复制一份；**先把原 stderr 存成 dup fd**，
            // 否则 handler 里再写标准错误 = 写回同一条管道 → 自我反馈、日志无限增长。
            let savedStderr = dup(STDERR_FILENO)
            let pipe = Pipe()
            gLogPipe = pipe            // 保活（否则 handler 随 Pipe 一起被释放 → SIGPIPE）
            dup2(pipe.fileHandleForWriting.fileDescriptor, STDERR_FILENO)
            pipe.fileHandleForReading.readabilityHandler = { h in
                let d = h.availableData
                if d.isEmpty { return }
                fh.write(d)                      // 落盘
                if savedStderr >= 0 {            // 回显到**原始**终端，不回流管道
                    d.withUnsafeBytes { raw in
                        if let base = raw.baseAddress { _ = write(savedStderr, base, d.count) }
                    }
                }
            }
        }
    }

    if opts.probe {
        let sem = DispatchSemaphore(value: 0)
        var payload: [String: Any] = [:]
        Task {
            do { payload = try await Recorder.probe(opts) }
            catch { payload = ["error": "\(error)"] }
            sem.signal()
        }
        sem.wait()
        print(jsonString(payload))
        exit(0)
    }

    guard !opts.out.isEmpty else {
        logErr("缺少 --out")
        exit(64)
    }

    // 入口处一次性校验所有会写的文件：默认不改写任何已存在的素材/证据（退出码 3）
    let metricsPath = opts.json.isEmpty ? (opts.out + ".metrics.json") : opts.json
    var conflicts: [String] = []
    for p in [opts.out, metricsPath, opts.focusLog, opts.statusFile] where !p.isEmpty {
        if FileManager.default.fileExists(atPath: p) { conflicts.append(p) }
    }
    if !conflicts.isEmpty && !opts.overwrite {
        logErr("拒绝覆盖已存在的文件（退出码 3）：\n  " + conflicts.joined(separator: "\n  ") +
               "\n→ 换一个新路径，或显式加 --overwrite")
        exit(3)
    }

    let rec = Recorder(opts: opts)

    gStopHook = { reason in rec.requestStop(reason: reason) }

    let startup = DispatchSemaphore(value: 0)
    var failure: String?
    Task {
        do { try await rec.start() }
        catch { failure = "\(error)" }
        startup.signal()
    }
    // 启动有界：SCShareableContent / startCapture 卡住时也必须给终态
    if startup.wait(timeout: .now() + 30) == .timedOut {
        let msg = "启动超时（30s）：startCapture 未返回，按失败收尾"
        logErr(msg)
        if !opts.statusFile.isEmpty {
            writeFile(opts.statusFile, jsonString(["state": "failed", "exit": 4, "error": "startup_timeout"]))
        }
        exit(4)
    }

    if let failure = failure {
        logErr(failure)
        // 退出码分工：64 = 用法错误（与参数解析失败一致，也和 record-game.sh 的约定对齐），
        // 2 = 其它启动失败，3 = 预留「拒绝覆盖」。用法错误**不能**复用 3，否则会被误读成"拒绝覆盖"。
        let code = failure.hasPrefix("用法错误") ? 64 : 2
        if !opts.statusFile.isEmpty {
            writeFile(opts.statusFile, jsonString(["state": "failed", "exit": code, "error": failure]))
        }
        exit(Int32(code))
    }

    // 运行 + 收尾有界：到期还没终态就如实报失败，绝不无限等
    let budget: Double = opts.duration > 0 ? opts.duration : max(opts.maxSeconds, 1)
    let hardDeadline = Date().addingTimeInterval(budget + 30)
    while rec.finished.wait(timeout: .now() + 0.5) == .timedOut {
        if Date() > hardDeadline {
            let msg = "收尾超时（\(Int(budget + 30))s 内没有终态）：按失败退出"
            logErr(msg)
            if !opts.statusFile.isEmpty {
                writeFile(opts.statusFile, jsonString(["state": "failed", "exit": 4,
                                                       "error": "no_terminal_state_timeout"]))
            }
            exit(4)
        }
    }
    exit(rec.finalExitCode)
} catch {
    logErr("\(error)")
    exit(64)
}
